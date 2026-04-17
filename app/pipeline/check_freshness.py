"""Input freshness checks for news and market timestamps."""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas import InputFreshnessReport, InputStalenessItem, NormalizedInputs


def build_input_freshness_report(
    normalized_inputs: NormalizedInputs,
    max_news_age_hours: int,
    max_market_age_minutes: int,
    checked_at: datetime | None = None,
) -> InputFreshnessReport:
    """Build freshness report from normalized inputs without side effects."""
    now = checked_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    news_threshold_minutes = float(max_news_age_hours) * 60.0
    market_threshold_minutes = float(max_market_age_minutes)

    stale_news: list[InputStalenessItem] = []
    stale_market: list[InputStalenessItem] = []

    for item in normalized_inputs.news:
        age_minutes = _age_minutes(now, item.published_at)
        if max_news_age_hours > 0 and age_minutes > news_threshold_minutes:
            stale_news.append(
                InputStalenessItem(
                    source_type="news",
                    key=item.headline[:120] or "(empty headline)",
                    observed_at=item.published_at,
                    age_minutes=round(age_minutes, 2),
                    threshold_minutes=news_threshold_minutes,
                )
            )

    for item in normalized_inputs.indicators:
        age_minutes = _age_minutes(now, item.as_of)
        if max_market_age_minutes > 0 and age_minutes > market_threshold_minutes:
            stale_market.append(
                InputStalenessItem(
                    source_type="market",
                    key=item.symbol,
                    observed_at=item.as_of,
                    age_minutes=round(age_minutes, 2),
                    threshold_minutes=market_threshold_minutes,
                )
            )

    has_blocking_issues = bool(stale_news or stale_market)
    summary = (
        "Input freshness check passed"
        if not has_blocking_issues
        else (
            f"Input freshness check failed: stale_news={len(stale_news)}, "
            f"stale_market={len(stale_market)}"
        )
    )

    return InputFreshnessReport(
        checked_at=now,
        max_news_age_hours=max_news_age_hours,
        max_market_age_minutes=max_market_age_minutes,
        news_items_checked=len(normalized_inputs.news),
        market_items_checked=len(normalized_inputs.indicators),
        stale_news=stale_news,
        stale_market=stale_market,
        has_blocking_issues=has_blocking_issues,
        summary=summary,
    )


def _age_minutes(now: datetime, observed_at: datetime) -> float:
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    age_seconds = max((now - observed_at).total_seconds(), 0.0)
    return age_seconds / 60.0
