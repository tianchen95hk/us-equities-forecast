"""Earnings-revision proxy collector based on FMP analyst and ratings datasets."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import Settings
from app.exceptions import CollectorError
from app.schemas import (
    EarningsRevisionMetrics,
    EarningsRevisionProxy,
    FactorDirection,
)


@dataclass
class _TickerProxyStat:
    ticker: str
    eps_delta_7d: float | None
    eps_delta_30d: float | None
    rating_upgrade_ratio: float | None
    coverage_change: float | None
    evidence: list[str]
    limitations: list[str]


def collect_earnings_revision_proxy(
    settings: Settings,
    *,
    as_of: datetime | None = None,
    tickers: list[str] | None = None,
) -> tuple[EarningsRevisionProxy, str]:
    """Collect an earnings-revision proxy from FMP endpoints.

    Returns a typed proxy and a source label.
    """
    now = as_of or datetime.now(timezone.utc)
    sample = list(dict.fromkeys((tickers or settings.earnings_proxy_top_tickers)))
    live_max = max(1, int(settings.earnings_proxy_live_max_tickers))
    fetch_sample = sample[:live_max] if settings.use_live_data else sample

    if not settings.use_live_data:
        return _fallback_proxy(now, sample, "live mode disabled"), "disabled"

    if not settings.fmp_api_key:
        if settings.strict_live_mode:
            raise CollectorError("Strict live mode requires FMP_API_KEY for earnings revision proxy")
        return _fallback_proxy(now, sample, "FMP_API_KEY missing"), "no_api_key"

    try:
        stats, errors = _collect_live_stats(settings, fetch_sample, now)
        if len(fetch_sample) < len(sample):
            errors.append(
                f"live subset executed: {len(fetch_sample)}/{len(sample)} tickers for latency control"
            )
        proxy = _build_proxy_from_stats(now, sample, stats, errors)
        source = "live_fmp" if proxy.coverage_status == "full" else "live_fmp_partial"
        return proxy, source
    except Exception as exc:  # noqa: BLE001
        if settings.strict_live_mode:
            raise CollectorError(f"Failed to collect live earnings revision proxy: {exc}") from exc
        return _fallback_proxy(now, sample, f"live fetch failed: {exc}"), "fallback"


def _fallback_proxy(now: datetime, sample: list[str], reason: str) -> EarningsRevisionProxy:
    return EarningsRevisionProxy(
        generated_at=now,
        as_of=now,
        coverage_status="none",
        sample_size=len(sample),
        available_series=0,
        metrics=EarningsRevisionMetrics(),
        signal=FactorDirection.NEUTRAL,
        score=0.0,
        summary="Earnings revision proxy unavailable; use neutral placeholder.",
        limitations=[reason],
        evidence_refs=[],
    )


def _collect_live_stats(
    settings: Settings,
    sample: list[str],
    now: datetime,
) -> tuple[list[_TickerProxyStat], list[str]]:
    timeout = httpx.Timeout(
        connect=min(5.0, settings.request_timeout_seconds),
        read=min(8.0, settings.request_timeout_seconds),
        write=min(5.0, settings.request_timeout_seconds),
        pool=min(5.0, settings.request_timeout_seconds),
    )

    stats: list[_TickerProxyStat] = []
    errors: list[str] = []
    max_workers = min(10, max(1, len(sample)))

    def _collect_one(ticker: str) -> _TickerProxyStat:
        with httpx.Client(timeout=timeout, headers={"User-Agent": settings.user_agent}) as client:
            return _collect_ticker_stat(client, settings, ticker, now)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_collect_one, ticker): ticker for ticker in sample}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                stats.append(future.result())
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{ticker}: {_compact_error(exc)}")

    return stats, errors


def _collect_ticker_stat(
    client: httpx.Client,
    settings: Settings,
    ticker: str,
    now: datetime,
) -> _TickerProxyStat:
    evidence: list[str] = []
    limitations: list[str] = []
    estimates: list[dict[str, Any]] = []
    ratings_hist: list[dict[str, Any]] = []
    consensus_rows: list[dict[str, Any]] = []

    try:
        estimates = _fetch_fmp_dataset(client, settings, "analyst-estimates", ticker)
    except Exception as exc:  # noqa: BLE001
        limitations.append(f"analyst-estimates unavailable: {_compact_error(exc)}")

    try:
        ratings_hist = _fetch_fmp_dataset(client, settings, "ratings-historical", ticker)
    except Exception as exc:  # noqa: BLE001
        limitations.append(f"ratings-historical unavailable: {_compact_error(exc)}")

    eps_delta_7d, eps_delta_30d, coverage_change = _compute_estimate_deltas(estimates, now)
    if eps_delta_7d is None or eps_delta_30d is None:
        limitations.append("insufficient analyst-estimates history")
    else:
        evidence.append(f"{ticker} analyst-estimates")

    rating_upgrade_ratio = _compute_rating_upgrade_ratio(ratings_hist, now)
    if rating_upgrade_ratio is None:
        if not consensus_rows:
            try:
                consensus_rows = _fetch_fmp_dataset(client, settings, "grades-consensus", ticker)
            except Exception as exc:  # noqa: BLE001
                limitations.append(f"grades-consensus unavailable: {_compact_error(exc)}")
        rating_upgrade_ratio = _compute_rating_from_consensus(consensus_rows)
        if rating_upgrade_ratio is None:
            limitations.append("insufficient ratings-historical/grades-consensus")
        else:
            evidence.append(f"{ticker} grades-consensus")
    else:
        evidence.append(f"{ticker} ratings-historical")

    if coverage_change is None:
        if not consensus_rows:
            try:
                consensus_rows = _fetch_fmp_dataset(client, settings, "grades-consensus", ticker)
            except Exception as exc:  # noqa: BLE001
                limitations.append(f"grades-consensus unavailable: {_compact_error(exc)}")
        coverage_change = _extract_coverage_delta_from_consensus(consensus_rows)
        if coverage_change is None:
            limitations.append("missing coverage history")

    return _TickerProxyStat(
        ticker=ticker,
        eps_delta_7d=eps_delta_7d,
        eps_delta_30d=eps_delta_30d,
        rating_upgrade_ratio=rating_upgrade_ratio,
        coverage_change=coverage_change,
        evidence=evidence,
        limitations=limitations,
    )


def _fetch_fmp_dataset(
    client: httpx.Client,
    settings: Settings,
    endpoint: str,
    ticker: str,
) -> list[dict[str, Any]]:
    errors: list[str] = []
    for url, params in _candidate_requests(settings=settings, endpoint=endpoint, ticker=ticker):
        try:
            response = client.get(url, params=params)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url} params={params}: {exc}")
            continue

        try:
            payload = response.json()
        except ValueError as exc:
            errors.append(f"{url} params={params}: invalid json ({exc})")
            continue

        rows = _extract_rows(payload)
        if rows:
            return rows

    raise CollectorError(
        f"{endpoint} unavailable for {ticker}: " + (errors[0] if errors else "no successful response")
    )


def _candidate_requests(
    *,
    settings: Settings,
    endpoint: str,
    ticker: str,
) -> list[tuple[str, dict[str, Any]]]:
    base_url = settings.fmp_base_url.rstrip("/")
    api_key = settings.fmp_api_key
    vendor_ticker = _to_fmp_symbol(ticker)

    candidates: list[tuple[str, dict[str, Any]]] = []
    if endpoint == "analyst-estimates":
        candidates.extend(
            [
                (
                    f"{base_url}/analyst-estimates",
                    {"symbol": vendor_ticker, "period": "annual", "page": 0, "limit": 10, "apikey": api_key},
                ),
                (f"https://financialmodelingprep.com/api/v3/analyst-estimates/{vendor_ticker}", {"apikey": api_key}),
            ]
        )
    elif endpoint == "ratings-historical":
        candidates.extend(
            [
                (f"{base_url}/ratings-historical", {"symbol": vendor_ticker, "apikey": api_key}),
            ]
        )
    elif endpoint == "grades-consensus":
        candidates.extend(
            [
                (f"{base_url}/grades-consensus", {"symbol": vendor_ticker, "apikey": api_key}),
            ]
        )
    else:
        candidates.append((f"{base_url}/{endpoint}", {"symbol": vendor_ticker, "apikey": api_key}))

    return candidates


def _to_fmp_symbol(ticker: str) -> str:
    """Normalize symbols to FMP naming, e.g. BRK.B -> BRK-B."""
    return ticker.replace(".", "-").strip().upper()


def _extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("historical", "data", "estimates", "ratings", "results"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [item for item in rows if isinstance(item, dict)]
        if payload:
            return [payload]
    return []


def _compute_estimate_deltas(
    rows: list[dict[str, Any]],
    now: datetime,
) -> tuple[float | None, float | None, float | None]:
    if not rows:
        return None, None, None

    parsed: list[tuple[datetime, float, float | None]] = []
    for row in rows:
        dt = _parse_dt(row.get("date") or row.get("publishedDate") or row.get("updatedAt"))
        eps = _to_float(row.get("epsAvg") or row.get("epsavg") or row.get("eps_average"))
        if dt is None or eps is None:
            continue
        analysts = _to_float(
            row.get("numberAnalysts")
            or row.get("numberOfAnalysts")
            or row.get("analystsCount")
            or row.get("analystCount")
        )
        parsed.append((dt, eps, analysts))

    if len(parsed) < 2:
        return None, None, None

    parsed.sort(key=lambda item: item[0], reverse=True)
    latest_dt, latest_eps, latest_coverage = parsed[0]

    eps_7 = _relative_delta(latest_eps, _value_on_or_before(parsed, latest_dt - timedelta(days=7)))
    eps_30 = _relative_delta(latest_eps, _value_on_or_before(parsed, latest_dt - timedelta(days=30)))

    coverage_latest = latest_coverage
    coverage_30 = _coverage_on_or_before(parsed, latest_dt - timedelta(days=30))
    coverage_change = None
    if coverage_latest is not None and coverage_30 is not None:
        coverage_change = coverage_latest - coverage_30

    if latest_dt + timedelta(days=10) < now:
        # Data can be stale by design; mark through limitations upstream via None handling only
        pass

    return eps_7, eps_30, coverage_change


def _compute_rating_upgrade_ratio(rows: list[dict[str, Any]], now: datetime) -> float | None:
    if not rows:
        return None

    cutoff = now - timedelta(days=30)
    upgrades = 0
    downgrades = 0
    for row in rows:
        dt = _parse_dt(row.get("date") or row.get("publishedDate") or row.get("updatedAt"))
        if dt is None or dt < cutoff:
            continue

        action_text = " ".join(
            str(row.get(key, ""))
            for key in ("action", "gradingCompany", "newGrade", "oldGrade")
        ).lower()

        if "upgrad" in action_text or "outperform" in action_text or "buy" in action_text:
            upgrades += 1
        elif "downgrad" in action_text or "underperform" in action_text or "sell" in action_text:
            downgrades += 1

    total = upgrades + downgrades
    if total == 0:
        return None
    return upgrades / total


def _compute_rating_from_consensus(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    row = rows[0]
    buy = _to_float(row.get("strongBuy") or row.get("buy"))
    sell = _to_float(row.get("strongSell") or row.get("sell"))
    hold = _to_float(row.get("hold"))
    if buy is None and sell is None and hold is None:
        score = _to_float(row.get("ratingScore") or row.get("score"))
        if score is None:
            return None
        # 1(best)-5(worst) -> 0-1 upgrade ratio proxy
        return max(0.0, min(1.0, (5.0 - score) / 4.0))

    buy_count = (buy or 0.0)
    sell_count = (sell or 0.0)
    hold_count = (hold or 0.0)
    total = buy_count + sell_count + hold_count
    if total <= 0:
        return None
    return (buy_count + 0.5 * hold_count) / total


def _extract_coverage_delta_from_consensus(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    row = rows[0]
    current = _to_float(row.get("analystCount") or row.get("analystsCount") or row.get("numberAnalysts"))
    previous = _to_float(row.get("analystCountPrevious") or row.get("analystsCountPrevious"))
    if current is None or previous is None:
        return None
    return current - previous


def _build_proxy_from_stats(
    now: datetime,
    sample: list[str],
    stats: list[_TickerProxyStat],
    errors: list[str],
) -> EarningsRevisionProxy:
    eps_7_vals = [item.eps_delta_7d for item in stats if item.eps_delta_7d is not None]
    eps_30_vals = [item.eps_delta_30d for item in stats if item.eps_delta_30d is not None]
    rating_vals = [item.rating_upgrade_ratio for item in stats if item.rating_upgrade_ratio is not None]
    coverage_vals = [item.coverage_change for item in stats if item.coverage_change is not None]

    metrics = EarningsRevisionMetrics(
        eps_avg_7d_delta=_avg(eps_7_vals),
        eps_avg_30d_delta=_avg(eps_30_vals),
        rating_upgrade_ratio=_avg(rating_vals),
        coverage_change=_avg(coverage_vals),
    )

    component_scores: list[float] = []
    if metrics.eps_avg_7d_delta is not None:
        component_scores.append(_clamp(metrics.eps_avg_7d_delta / 6.0, -1.0, 1.0) * 0.35)
    if metrics.eps_avg_30d_delta is not None:
        component_scores.append(_clamp(metrics.eps_avg_30d_delta / 10.0, -1.0, 1.0) * 0.35)
    if metrics.rating_upgrade_ratio is not None:
        component_scores.append(_clamp((metrics.rating_upgrade_ratio - 0.5) * 2.0, -1.0, 1.0) * 0.2)
    if metrics.coverage_change is not None:
        component_scores.append(_clamp(metrics.coverage_change / 6.0, -1.0, 1.0) * 0.1)

    raw_score = _clamp(sum(component_scores), -1.0, 1.0) if component_scores else 0.0
    direction = _direction_from_score(raw_score)

    all_limitations = list(dict.fromkeys([*errors, *[msg for item in stats for msg in item.limitations]]))
    coverage_ratio = len(stats) / max(1, len(sample))
    if coverage_ratio >= 0.7 and component_scores:
        coverage_status = "full"
    elif coverage_ratio > 0.0:
        coverage_status = "partial"
    else:
        coverage_status = "none"

    summary = (
        "Earnings revision proxy indicates net upgrades in forward expectations."
        if raw_score > 0.15
        else "Earnings revision proxy indicates net downgrades in forward expectations."
        if raw_score < -0.15
        else "Earnings revision proxy remains mixed/neutral with limited directional edge."
    )
    if all_limitations:
        summary = f"{summary} Data limitations observed: {len(all_limitations)}."

    evidence = list(dict.fromkeys([ref for item in stats for ref in item.evidence]))
    as_of_candidates = [now]

    return EarningsRevisionProxy(
        generated_at=now,
        as_of=max(as_of_candidates),
        coverage_status=coverage_status,
        sample_size=len(sample),
        available_series=len(stats),
        metrics=metrics,
        signal=direction,
        score=round(raw_score, 4),
        summary=summary,
        limitations=all_limitations[:12],
        evidence_refs=evidence[:50],
    )


def _value_on_or_before(rows: list[tuple[datetime, float, float | None]], target: datetime) -> float | None:
    for dt, eps, _ in rows:
        if dt <= target:
            return eps
    return rows[-1][1] if rows else None


def _coverage_on_or_before(rows: list[tuple[datetime, float, float | None]], target: datetime) -> float | None:
    for dt, _, coverage in rows:
        if dt <= target:
            return coverage
    return None


def _relative_delta(current: float, previous: float | None) -> float | None:
    if previous in {None, 0.0}:
        return None
    return ((current - previous) / abs(previous)) * 100.0


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _direction_from_score(score: float) -> FactorDirection:
    if score >= 0.15:
        return FactorDirection.UP
    if score <= -0.15:
        return FactorDirection.DOWN
    if abs(score) <= 0.05:
        return FactorDirection.NEUTRAL
    return FactorDirection.MIXED


def _avg(values: list[float | None]) -> float | None:
    clean = [float(item) for item in values if item is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 6)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _compact_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    if " for url " in text:
        text = text.split(" for url ", 1)[0]
    return text[:220]
