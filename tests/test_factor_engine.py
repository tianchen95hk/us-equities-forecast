"""Unit tests for deterministic five-factor engine."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.config import Settings
from app.pipeline.factors import build_factor_snapshot
from app.schemas import (
    EarningsRevisionMetrics,
    EarningsRevisionProxy,
    FactorDirection,
    MarketIndicator,
    NewsItem,
    NormalizedInputs,
)


class FactorEngineTests(unittest.TestCase):
    def _normalized_inputs(self) -> NormalizedInputs:
        now = datetime.now(timezone.utc)
        return NormalizedInputs(
            run_id="run_test",
            collected_at=now,
            forecast_horizon="5 trading days",
            market_universe=["SPY", "QQQ", "IWM", "VIX", "US10Y", "DXY", "OIL", "BTC", "USDJPY"],
            news=[
                NewsItem(
                    source="Reuters",
                    source_type="newsapi",
                    source_reliability="high",
                    headline="Energy market remains stable",
                    summary="No major geopolitical shock.",
                    published_at=now,
                )
            ],
            indicators=[
                MarketIndicator(symbol="SPY", name="SPY", value=520.0, change_pct=0.2, as_of=now),
                MarketIndicator(symbol="QQQ", name="QQQ", value=450.0, change_pct=0.1, as_of=now),
                MarketIndicator(symbol="IWM", name="IWM", value=210.0, change_pct=0.05, as_of=now),
                MarketIndicator(symbol="VIX", name="VIX", value=18.2, change_pct=-0.8, as_of=now),
                MarketIndicator(symbol="US10Y", name="US10Y", value=4.1, change_pct=0.02, as_of=now),
                MarketIndicator(symbol="DXY", name="DXY", value=102.1, change_pct=0.03, as_of=now),
                MarketIndicator(symbol="OIL", name="OIL", value=74.0, change_pct=0.1, as_of=now),
                MarketIndicator(symbol="BTC", name="BTC", value=83000.0, change_pct=0.5, as_of=now),
                MarketIndicator(symbol="USDJPY", name="USDJPY", value=151.0, change_pct=0.02, as_of=now),
            ],
            state_variables={},
        )

    def _earnings_proxy(self, score: float) -> EarningsRevisionProxy:
        now = datetime.now(timezone.utc)
        return EarningsRevisionProxy(
            generated_at=now,
            as_of=now,
            coverage_status="full",
            sample_size=30,
            available_series=25,
            metrics=EarningsRevisionMetrics(
                eps_avg_7d_delta=2.0,
                eps_avg_30d_delta=3.5,
                rating_upgrade_ratio=0.62,
                coverage_change=1.0,
            ),
            signal=FactorDirection.UP if score > 0 else FactorDirection.DOWN,
            score=score,
            summary="proxy summary",
            limitations=[],
            evidence_refs=["AAPL analyst-estimates"],
        )

    def test_factor_snapshot_outputs_five_factors_and_dominant(self) -> None:
        settings = Settings(use_live_data=False)
        snapshot, dominant = build_factor_snapshot(
            settings=settings,
            normalized_inputs=self._normalized_inputs(),
            earnings_proxy=self._earnings_proxy(0.9),
            output_language="zh",
        )

        self.assertAlmostEqual(snapshot.earnings_revision.score, 0.9, places=5)
        self.assertIn("earnings_revision", snapshot.weighted_scores)
        self.assertEqual(dominant.dominant_factor, "earnings_revision")
        self.assertFalse(dominant.tie_detected)
        self.assertIn("主导", dominant.explainer)

    def test_dominant_factor_tie_when_threshold_is_large(self) -> None:
        settings = Settings(
            use_live_data=False,
            factor_dominant_tie_threshold=1.0,
        )
        snapshot, dominant = build_factor_snapshot(
            settings=settings,
            normalized_inputs=self._normalized_inputs(),
            earnings_proxy=self._earnings_proxy(0.35),
            output_language="en",
        )

        self.assertIn("earnings_revision", snapshot.weighted_scores)
        self.assertTrue(dominant.tie_detected)
        self.assertGreaterEqual(len(dominant.dominant_factors), 2)


if __name__ == "__main__":
    unittest.main()
