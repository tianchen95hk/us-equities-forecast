"""Unit tests for rule validation behavior."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.exceptions import RuleViolationError
from app.rules.anti_hindsight import (
    find_banned_phrase_issues,
    is_valid_review_status,
    validate_review_status_pair,
)
from app.rules.schema_check import build_rule_report, validate_forecast_rules
from app.schemas import AntiHindsightStatus, DirectionalBias, FinalForecast, ReferenceLevels


class ForecastRuleTests(unittest.TestCase):
    def _valid_forecast(self) -> FinalForecast:
        return FinalForecast(
            generated_at=datetime.now(timezone.utc),
            forecast_horizon="5 trading days",
            market_universe=["SPY", "QQQ", "IWM", "VIX", "US10Y", "DXY", "OIL", "BTC", "USDJPY"],
            directional_bias=DirectionalBias.NEUTRAL,
            confidence=0.6,
            dominant_drivers=["Cross-asset regime"],
            supportive_evidence=["Rates and dollar stabilized"],
            opposing_evidence=["Event flow remains mixed"],
            upside_triggers=["Volatility cools"],
            downside_triggers=["Rates re-accelerate"],
            invalidation_conditions=["Signal regime flips"],
            monitoring_list=["VIX", "US10Y", "DXY"],
            reference_levels=ReferenceLevels(support_levels=["SPY 500-503"]),
            final_thesis=(
                "Bias reflects current observable conditions and is invalidated by "
                "cross-asset regime reversal."
            ),
            anti_hindsight_status=AntiHindsightStatus.PASS,
        )

    def test_valid_forecast_passes_rules(self) -> None:
        forecast = self._valid_forecast()
        validate_forecast_rules(forecast, require_review_status=True)

    def test_price_target_phrase_in_thesis_is_rejected(self) -> None:
        forecast = self._valid_forecast()
        payload = forecast.model_dump(mode="json")
        payload["final_thesis"] = "The target price will reach 600 within days."

        with self.assertRaises(RuleViolationError):
            validate_forecast_rules(payload, require_review_status=True)

    def test_chinese_price_target_phrase_in_thesis_is_rejected(self) -> None:
        forecast = self._valid_forecast()
        payload = forecast.model_dump(mode="json")
        payload["final_thesis"] = "指数将到5200点，短期内突破关键位。"

        with self.assertRaises(RuleViolationError):
            validate_forecast_rules(payload, require_review_status=True)

    def test_unparseable_horizon_is_rejected(self) -> None:
        forecast = self._valid_forecast()
        payload = forecast.model_dump(mode="json")
        payload["forecast_horizon"] = "soon"

        report = build_rule_report(payload, require_review_status=True)
        issue_codes = {item.code for item in report.hard_fail_issues}
        self.assertIn("FORECAST_HORIZON_UNPARSEABLE", issue_codes)

    def test_missing_opposing_evidence_is_blocking(self) -> None:
        forecast = self._valid_forecast()
        payload = forecast.model_dump(mode="json")
        payload["opposing_evidence"] = []

        report = build_rule_report(payload, require_review_status=True)
        issue_codes = {item.code for item in report.hard_fail_issues}
        self.assertIn("OPPOSING_EVIDENCE_MISSING", issue_codes)

    def test_missing_invalidation_conditions_is_blocking(self) -> None:
        forecast = self._valid_forecast()
        payload = forecast.model_dump(mode="json")
        payload["invalidation_conditions"] = []

        report = build_rule_report(payload, require_review_status=True)
        issue_codes = {item.code for item in report.hard_fail_issues}
        self.assertIn("MISSING_INVALIDATION_CONDITIONS", issue_codes)

    def test_review_status_must_be_pass_or_fail(self) -> None:
        forecast = self._valid_forecast()
        payload = forecast.model_dump(mode="json")
        payload["review_status"] = "UNKNOWN"
        payload["anti_hindsight_status"] = "UNKNOWN"

        report = build_rule_report(payload, require_review_status=True)
        issue_codes = {item.code for item in report.hard_fail_issues}
        self.assertIn("REVIEW_STATUS_INVALID", issue_codes)

    def test_banned_phrase_scan_ignores_artifact_paths(self) -> None:
        payload = self._valid_forecast().model_dump(mode="json")
        payload["artifact_paths"] = {
            "attachment": "/tmp/target_price_reference.txt"
        }

        issues = find_banned_phrase_issues(payload)
        self.assertEqual(issues["price_target_issues"], [])
        self.assertEqual(issues["hindsight_issues"], [])

    def test_review_summary_scan_is_warning_only(self) -> None:
        payload = self._valid_forecast().model_dump(mode="json")
        report = build_rule_report(
            payload,
            require_review_status=True,
            review_summary="审查说明：命中 target price 词但已移动到参考位。",
        )
        self.assertFalse(report.has_hard_fail)
        self.assertTrue(report.has_soft_warn)

    def test_review_status_pair_validator_detects_mismatch(self) -> None:
        issues = validate_review_status_pair("PASS", "FAIL")
        self.assertIn(
            "REVIEW_STATUS_MISMATCH: top-level review status must match reviewed_forecast.anti_hindsight_status",
            issues,
        )

    def test_review_status_helper_accepts_only_pass_or_fail(self) -> None:
        self.assertTrue(is_valid_review_status("PASS"))
        self.assertTrue(is_valid_review_status("FAIL"))
        self.assertFalse(is_valid_review_status("UNKNOWN"))
        self.assertFalse(is_valid_review_status(None))


if __name__ == "__main__":
    unittest.main()
