"""Unit tests for input freshness gate behavior."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.pipeline.check_freshness import build_input_freshness_report
from app.schemas import MarketIndicator, NewsItem, NormalizedInputs


class InputFreshnessTests(unittest.TestCase):
    def _build_inputs(self, now: datetime, news_age_hours: float, market_age_minutes: float) -> NormalizedInputs:
        return NormalizedInputs(
            run_id="run_test",
            collected_at=now,
            forecast_horizon="5 trading days",
            market_universe=["SPY", "QQQ", "IWM", "VIX", "US10Y", "DXY", "OIL"],
            news=[
                NewsItem(
                    source="test",
                    headline="Test headline",
                    summary="",
                    url=None,
                    published_at=now - timedelta(hours=news_age_hours),
                )
            ],
            indicators=[
                MarketIndicator(
                    symbol="SPY",
                    name="SPDR S&P 500 ETF Trust",
                    value=510.0,
                    previous_value=508.0,
                    change_pct=0.39,
                    unit="usd",
                    as_of=now - timedelta(minutes=market_age_minutes),
                )
            ],
            state_variables={},
        )

    def test_fresh_inputs_pass(self) -> None:
        now = datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc)
        inputs = self._build_inputs(now, news_age_hours=24, market_age_minutes=30)

        report = build_input_freshness_report(
            normalized_inputs=inputs,
            max_news_age_hours=72,
            max_market_age_minutes=60,
            checked_at=now,
        )

        self.assertFalse(report.has_blocking_issues)
        self.assertEqual(len(report.stale_news), 0)
        self.assertEqual(len(report.stale_market), 0)

    def test_stale_news_is_blocking(self) -> None:
        now = datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc)
        inputs = self._build_inputs(now, news_age_hours=96, market_age_minutes=30)

        report = build_input_freshness_report(
            normalized_inputs=inputs,
            max_news_age_hours=72,
            max_market_age_minutes=60,
            checked_at=now,
        )

        self.assertTrue(report.has_blocking_issues)
        self.assertEqual(len(report.stale_news), 1)
        self.assertEqual(len(report.stale_market), 0)

    def test_stale_market_is_blocking(self) -> None:
        now = datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc)
        inputs = self._build_inputs(now, news_age_hours=24, market_age_minutes=180)

        report = build_input_freshness_report(
            normalized_inputs=inputs,
            max_news_age_hours=72,
            max_market_age_minutes=60,
            checked_at=now,
        )

        self.assertTrue(report.has_blocking_issues)
        self.assertEqual(len(report.stale_news), 0)
        self.assertEqual(len(report.stale_market), 1)


if __name__ == "__main__":
    unittest.main()
