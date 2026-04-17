"""Unit tests for rule validation behavior."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.exceptions import RuleViolationError
from app.rules.schema_check import build_rule_report, validate_forecast_rules
from app.schemas import AntiHindsightStatus, DirectionalBias, FinalForecast


class ForecastRuleTests(unittest.TestCase):
    def _valid_forecast(self) -> FinalForecast:
        return FinalForecast(
            generated_at=datetime.now(timezone.utc),
            forecast_horizon="5 trading days",
            market_universe=["SPY", "QQQ", "IWM", "VIX", "US10Y", "DXY", "OIL"],
            directional_bias=DirectionalBias.NEUTRAL,
            confidence=0.6,
            dominant_drivers=["Cross-asset regime"],
            supportive_evidence=["Rates and dollar stabilized"],
            opposing_evidence=["Event flow remains mixed"],
            upside_triggers=["Volatility cools"],
            downside_triggers=["Rates re-accelerate"],
            invalidation_conditions=["Signal regime flips"],
            monitoring_list=["VIX", "US10Y", "DXY"],
            final_thesis="Bias reflects current observable conditions and is invalidated by cross-asset regime reversal.",
            anti_hindsight_status=AntiHindsightStatus.PASS,
        )

    def test_valid_forecast_passes_rules(self) -> None:
        forecast = self._valid_forecast()
        validate_forecast_rules(forecast)

    def test_price_target_phrase_is_rejected(self) -> None:
        forecast = self._valid_forecast()
        payload = forecast.model_dump(mode="json")
        payload["final_thesis"] = "The target price will reach 600 within days."

        with self.assertRaises(RuleViolationError):
            validate_forecast_rules(payload)

    def test_chinese_price_target_phrase_is_rejected(self) -> None:
        forecast = self._valid_forecast()
        payload = forecast.model_dump(mode="json")
        payload["final_thesis"] = "指数将到5200点，短期内突破关键位。"

        with self.assertRaises(RuleViolationError):
            validate_forecast_rules(payload)

    def test_unparseable_horizon_is_rejected(self) -> None:
        forecast = self._valid_forecast()
        payload = forecast.model_dump(mode="json")
        payload["forecast_horizon"] = "soon"

        report = build_rule_report(payload)
        issue_codes = {item.code for item in report.issues}
        self.assertIn("FORECAST_HORIZON_UNPARSEABLE", issue_codes)

    def test_evidence_symmetry_is_blocking_when_opposing_missing(self) -> None:
        forecast = self._valid_forecast()
        payload = forecast.model_dump(mode="json")
        payload["opposing_evidence"] = []

        report = build_rule_report(payload)
        issue_codes = {item.code for item in report.issues}
        self.assertIn("OPPOSING_EVIDENCE_MISSING", issue_codes)


if __name__ == "__main__":
    unittest.main()
