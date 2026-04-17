"""Market data collector: manual source, FMP-first live source, and mock fallback."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.exceptions import CollectorError

YAHOO_SYMBOLS: dict[str, str] = {
    "SPY": "SPY",
    "QQQ": "QQQ",
    "IWM": "IWM",
    "VIX": "^VIX",
    "US10Y": "^TNX",
    "DXY": "DX-Y.NYB",
    "OIL": "CL=F",
}

# Candidate FMP symbols per canonical instrument.
# Some instruments are proxies where direct index/commodity symbols are unavailable.
FMP_SYMBOL_CANDIDATES: dict[str, list[str]] = {
    "SPY": ["SPY"],
    "QQQ": ["QQQ"],
    "IWM": ["IWM"],
    "VIX": ["^VIX", "VIX", "VIXY"],
    "US10Y": ["^TNX", "TNX", "IEF"],
    "DXY": ["DXY", "DX-Y.NYB", "UUP"],
    "OIL": ["CLUSD", "USO", "CL=F"],
}

INDICATOR_NAMES: dict[str, str] = {
    "SPY": "SPDR S&P 500 ETF Trust",
    "QQQ": "Invesco QQQ Trust",
    "IWM": "iShares Russell 2000 ETF",
    "VIX": "CBOE Volatility Index",
    "US10Y": "US 10Y Treasury Yield Proxy",
    "DXY": "US Dollar Index Proxy",
    "OIL": "WTI Crude Oil Proxy",
}

INDICATOR_UNITS: dict[str, str] = {
    "SPY": "usd",
    "QQQ": "usd",
    "IWM": "usd",
    "VIX": "index",
    "US10Y": "proxy",
    "DXY": "proxy",
    "OIL": "proxy",
}


def _load_market_data_file(path: str) -> list[dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        raise CollectorError(f"Market input file not found: {file_path}")

    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectorError(f"Failed to parse market input file: {file_path}") from exc

    if not isinstance(payload, list):
        raise CollectorError(f"Market input file must contain a JSON list: {file_path}")
    return payload


def _last_two_non_null(values: list[Any]) -> tuple[float, float] | None:
    filtered = [float(value) for value in values if value is not None]
    if len(filtered) < 2:
        return None
    return filtered[-1], filtered[-2]


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)

    if not value:
        return datetime.now(timezone.utc)

    text = str(value).strip()
    if text.endswith("Z"):
        text = text.replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def _parse_change_pct(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip().replace("%", "")
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fetch_yahoo_indicator(settings: Settings, symbol: str) -> dict[str, Any]:
    yahoo_symbol = YAHOO_SYMBOLS[symbol]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
    params = {"interval": "1d", "range": "7d"}
    headers = {"User-Agent": settings.user_agent}

    try:
        with httpx.Client(timeout=settings.request_timeout_seconds) as client:
            response = client.get(url, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CollectorError(f"Failed Yahoo fetch for {symbol}: {exc}") from exc

    try:
        result = payload["chart"]["result"][0]
        close_values = result["indicators"]["quote"][0]["close"]
        timestamps = result["timestamp"]
    except (KeyError, IndexError, TypeError) as exc:
        raise CollectorError(f"Unexpected Yahoo payload structure for {symbol}") from exc

    latest_pair = _last_two_non_null(close_values)
    if not latest_pair:
        raise CollectorError(f"Insufficient close values from Yahoo for {symbol}")

    latest, previous = latest_pair
    as_of_timestamp = datetime.fromtimestamp(timestamps[-1], tz=timezone.utc)
    change_pct = ((latest - previous) / previous) * 100 if previous else 0.0

    return {
        "symbol": symbol,
        "vendor_symbol": yahoo_symbol,
        "name": INDICATOR_NAMES[symbol],
        "value": latest,
        "previous_value": previous,
        "change_pct": change_pct,
        "unit": INDICATOR_UNITS[symbol],
        "as_of": as_of_timestamp.isoformat(),
    }


def _fetch_fmp_quote_row(client: httpx.Client, settings: Settings, vendor_symbol: str) -> dict[str, Any]:
    if not settings.fmp_api_key:
        raise CollectorError("FMP_API_KEY is not configured")

    base_url = settings.fmp_base_url.rstrip("/")
    url = f"{base_url}/quote"
    params = {"symbol": vendor_symbol, "apikey": settings.fmp_api_key}

    response = client.get(url, params=params, headers={"User-Agent": settings.user_agent})
    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, list) or not payload:
        raise CollectorError(f"FMP quote returned empty payload for symbol {vendor_symbol}")

    row = payload[0]
    if not isinstance(row, dict):
        raise CollectorError(f"FMP quote returned malformed row for symbol {vendor_symbol}")
    return row


def _fetch_fmp_eod_rows(client: httpx.Client, settings: Settings, vendor_symbol: str) -> list[dict[str, Any]]:
    if not settings.fmp_api_key:
        raise CollectorError("FMP_API_KEY is not configured")

    base_url = settings.fmp_base_url.rstrip("/")
    url = f"{base_url}/historical-price-eod/light"
    params = {"symbol": vendor_symbol, "apikey": settings.fmp_api_key}

    response = client.get(url, params=params, headers={"User-Agent": settings.user_agent})
    response.raise_for_status()
    payload = response.json()

    if isinstance(payload, dict):
        rows = payload.get("historical")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    elif isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]

    raise CollectorError(f"FMP EOD returned malformed payload for symbol {vendor_symbol}")


def _normalize_fmp_quote(canonical_symbol: str, vendor_symbol: str, row: dict[str, Any]) -> dict[str, Any]:
    value_raw = row.get("price", row.get("close"))
    previous_raw = row.get("previousClose")

    if value_raw is None:
        raise CollectorError(f"FMP quote missing price for {canonical_symbol}")

    value = float(value_raw)
    previous_value = float(previous_raw) if previous_raw is not None else None
    change_pct = _parse_change_pct(row.get("changesPercentage"))

    if change_pct is None and previous_value not in {None, 0.0}:
        change_pct = ((value - previous_value) / previous_value) * 100

    as_of = _parse_datetime(row.get("timestamp") or row.get("lastUpdatedAt") or row.get("date"))

    return {
        "symbol": canonical_symbol,
        "vendor_symbol": vendor_symbol,
        "name": INDICATOR_NAMES[canonical_symbol],
        "value": value,
        "previous_value": previous_value,
        "change_pct": change_pct,
        "unit": INDICATOR_UNITS[canonical_symbol],
        "as_of": as_of.isoformat(),
    }


def _normalize_fmp_eod(canonical_symbol: str, vendor_symbol: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_rows: list[tuple[datetime, float]] = []
    for row in rows:
        value_raw = row.get("price", row.get("close"))
        if value_raw is None:
            continue
        try:
            as_of = _parse_datetime(row.get("date"))
            value = float(value_raw)
            normalized_rows.append((as_of, value))
        except (TypeError, ValueError):
            continue

    if len(normalized_rows) < 2:
        raise CollectorError(f"FMP EOD has insufficient rows for {canonical_symbol}")

    normalized_rows.sort(key=lambda item: item[0], reverse=True)
    latest_as_of, latest_value = normalized_rows[0]
    _, previous_value = normalized_rows[1]
    change_pct = ((latest_value - previous_value) / previous_value) * 100 if previous_value else 0.0

    return {
        "symbol": canonical_symbol,
        "vendor_symbol": vendor_symbol,
        "name": INDICATOR_NAMES[canonical_symbol],
        "value": latest_value,
        "previous_value": previous_value,
        "change_pct": change_pct,
        "unit": INDICATOR_UNITS[canonical_symbol],
        "as_of": latest_as_of.isoformat(),
    }


def _fetch_fmp_indicator(client: httpx.Client, settings: Settings, canonical_symbol: str) -> dict[str, Any]:
    candidates = FMP_SYMBOL_CANDIDATES[canonical_symbol]

    # Prefer quote endpoint for latest value.
    for vendor_symbol in candidates:
        try:
            quote_row = _fetch_fmp_quote_row(client, settings, vendor_symbol)
            return _normalize_fmp_quote(canonical_symbol, vendor_symbol, quote_row)
        except (CollectorError, httpx.HTTPError, ValueError):
            continue

    # Fallback to EOD light endpoint.
    for vendor_symbol in candidates:
        try:
            eod_rows = _fetch_fmp_eod_rows(client, settings, vendor_symbol)
            return _normalize_fmp_eod(canonical_symbol, vendor_symbol, eod_rows)
        except (CollectorError, httpx.HTTPError, ValueError):
            continue

    raise CollectorError(f"FMP could not resolve indicator {canonical_symbol} from candidates {candidates}")


def _fetch_fmp_market_data(settings: Settings) -> dict[str, dict[str, Any]]:
    if not settings.fmp_api_key:
        return {}

    results: dict[str, dict[str, Any]] = {}
    with httpx.Client(timeout=settings.request_timeout_seconds) as client:
        for canonical_symbol in INDICATOR_NAMES:
            try:
                results[canonical_symbol] = _fetch_fmp_indicator(client, settings, canonical_symbol)
            except CollectorError:
                continue
            except httpx.HTTPError:
                continue
    return results


def _fetch_live_market_data(settings: Settings) -> tuple[list[dict[str, Any]], str]:
    fmp_results = _fetch_fmp_market_data(settings)

    merged: dict[str, dict[str, Any]] = dict(fmp_results)
    source_label = "live_fmp" if fmp_results else "live"

    for canonical_symbol in INDICATOR_NAMES:
        if canonical_symbol in merged:
            continue
        try:
            merged[canonical_symbol] = _fetch_yahoo_indicator(settings, canonical_symbol)
            source_label = "live_fmp+yahoo" if fmp_results else "live_yahoo"
        except CollectorError:
            continue

    missing = [symbol for symbol in INDICATOR_NAMES if symbol not in merged]
    if missing:
        raise CollectorError(f"Live market data incomplete, missing: {', '.join(missing)}")

    ordered = [merged[symbol] for symbol in INDICATOR_NAMES]
    return ordered, source_label


def collect_market_data(settings: Settings, manual_path: str | None = None) -> tuple[list[dict[str, Any]], str]:
    """Collect current market indicators and return (items, source_label)."""
    if manual_path:
        return _load_market_data_file(manual_path), "manual"

    if settings.use_live_data:
        try:
            live_market_data, source = _fetch_live_market_data(settings)
            if live_market_data:
                return live_market_data, source
        except CollectorError:
            # Intentional fallback to maintain local run reliability.
            pass

    return _load_market_data_file(settings.mock_market_file), "mock"
