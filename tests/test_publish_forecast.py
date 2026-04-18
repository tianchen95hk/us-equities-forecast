"""Unit tests for reviewed-only publish selection."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.pipeline.publish_forecast import select_publishable_forecast
from app.schemas import (
    AntiHindsightReviewResult,
    AntiHindsightStatus,
    DirectionalBias,
    FinalForecast,
    ReviewDecision,
    ReviewFindings,
)


class PublishForecastTests(unittest.TestCase):
    def test_publish_uses_reviewed_forecast(self) -> None:
        reviewed = FinalForecast(
            generated_at=datetime.now(timezone.utc),
            forecast_horizon="5 trading days",
            market_universe=["SPY", "QQQ", "IWM", "VIX", "US10Y", "DXY", "OIL", "BTC", "USDJPY"],
            directional_bias=DirectionalBias.BEARISH,
            confidence=0.63,
            dominant_drivers=["Liquidity pressure"],
            supportive_evidence=["Rising yields"],
            opposing_evidence=["Contained volatility"],
            upside_triggers=["Yield stabilization"],
            downside_triggers=["Persistent dollar strength"],
            invalidation_conditions=["Cross-asset reversal"],
            monitoring_list=["US10Y", "DXY", "VIX"],
            final_thesis="Bias remains bearish while rates and dollar pressures persist.",
            anti_hindsight_status=AntiHindsightStatus.PASS,
        )
        review_result = AntiHindsightReviewResult(
            reviewed_at=datetime.now(timezone.utc),
            review_decision=ReviewDecision(
                review_status=AntiHindsightStatus.PASS,
                is_publishable=True,
                decision_summary="No issues",
                hard_fail_count=0,
                soft_warn_count=0,
            ),
            review_findings=ReviewFindings(),
            review_summary="No issues",
            reviewed_forecast=reviewed,
        )

        publishable = select_publishable_forecast(review_result)
        self.assertEqual(publishable, reviewed)


if __name__ == "__main__":
    unittest.main()
