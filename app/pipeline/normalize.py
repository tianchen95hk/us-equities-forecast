"""Normalize raw collector payloads into typed and timestamped inputs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.schemas import MarketIndicator, NewsItem, NormalizedInputs

_NEWS_SOURCE_TYPES = {"newsapi", "sec", "fmp_news", "manual", "mock", "other"}
_NEWS_SOURCE_RELIABILITY = {"very_high", "high", "medium", "unknown"}


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if not value:
        return datetime.now(timezone.utc)

    datetime_text = str(value).strip()
    if datetime_text.endswith("Z"):
        datetime_text = datetime_text.replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(datetime_text)
    except ValueError:
        return datetime.now(timezone.utc)

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _safe_change_pct(indicator_map: dict[str, MarketIndicator], symbol: str) -> float:
    indicator = indicator_map.get(symbol)
    if not indicator or indicator.change_pct is None:
        return 0.0
    return float(indicator.change_pct)


def _normalize_news_source_type(value: Any) -> str:
    text = str(value or "other").strip().lower()
    return text if text in _NEWS_SOURCE_TYPES else "other"


def _normalize_news_reliability(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    return text if text in _NEWS_SOURCE_RELIABILITY else "unknown"


def _derive_state_variables(indicators: list[MarketIndicator]) -> dict[str, Any]:
    indicator_map = {indicator.symbol: indicator for indicator in indicators}

    risk_average_change = (
        _safe_change_pct(indicator_map, "SPY")
        + _safe_change_pct(indicator_map, "QQQ")
        + _safe_change_pct(indicator_map, "IWM")
    ) / 3.0

    vix = indicator_map.get("VIX")
    us10y = indicator_map.get("US10Y")
    dxy = indicator_map.get("DXY")
    oil = indicator_map.get("OIL")
    btc = indicator_map.get("BTC")
    usdjpy = indicator_map.get("USDJPY")

    return {
        "risk_assets_avg_change_pct": round(risk_average_change, 4),
        "volatility_regime": "high" if vix and vix.value >= 22 else "normal",
        "rates_regime": "rising" if us10y and (us10y.change_pct or 0.0) > 0.2 else "stable",
        "dollar_regime": "stronger" if dxy and (dxy.change_pct or 0.0) > 0.2 else "stable",
        "oil_regime": "up-shock" if oil and (oil.change_pct or 0.0) > 1.0 else "stable",
        "crypto_risk_regime": "risk-on" if btc and (btc.change_pct or 0.0) > 1.0 else "neutral",
        "yen_carry_regime": "carry-on" if usdjpy and (usdjpy.change_pct or 0.0) > 0.2 else "stable",
    }


def normalize_inputs(
    run_id: str,
    forecast_horizon: str,
    market_universe: list[str],
    raw_news_items: list[dict[str, Any]],
    raw_market_indicators: list[dict[str, Any]],
) -> NormalizedInputs:
    """Build canonical pipeline input payload from raw collector outputs."""

    normalized_news = [
        NewsItem(
            source=str(news_item.get("source", "unknown")),
            source_type=_normalize_news_source_type(news_item.get("source_type")),
            source_reliability=_normalize_news_reliability(news_item.get("source_reliability")),
            headline=str(news_item.get("headline", "")).strip(),
            summary=str(news_item.get("summary", "")).strip(),
            url=news_item.get("url"),
            published_at=_parse_datetime(news_item.get("published_at")),
        )
        for news_item in raw_news_items
        if str(news_item.get("headline", "")).strip()
    ]

    normalized_indicators = [
        MarketIndicator(
            symbol=str(indicator.get("symbol", "")).strip(),
            name=str(indicator.get("name", "")).strip(),
            value=float(indicator.get("value", 0.0)),
            previous_value=(
                None
                if indicator.get("previous_value") is None
                else float(indicator.get("previous_value"))
            ),
            change_pct=(
                None if indicator.get("change_pct") is None else float(indicator.get("change_pct"))
            ),
            unit=str(indicator.get("unit", "index")),
            as_of=_parse_datetime(indicator.get("as_of")),
        )
        for indicator in raw_market_indicators
        if str(indicator.get("symbol", "")).strip()
    ]

    return NormalizedInputs(
        run_id=run_id,
        collected_at=datetime.now(timezone.utc),
        forecast_horizon=forecast_horizon,
        market_universe=market_universe,
        news=normalized_news,
        indicators=normalized_indicators,
        state_variables=_derive_state_variables(normalized_indicators),
    )
