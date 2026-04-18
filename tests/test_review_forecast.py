"""Unit tests for anti-hindsight review normalization safeguards."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import Any

from app.llm_client import BaseLLMClient
from app.pipeline.review_forecast import run_anti_hindsight_review
from app.rules.schema_check import build_rule_report
from app.schemas import (
    AntiHindsightStatus,
    DirectionalBias,
    ForecastDraft,
    MarketIndicator,
    NewsItem,
    NormalizedInputs,
    StateMappingResult,
    StateScenario,
)


class _StubLLMClient(BaseLLMClient):
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def generate_json(self, task_name: str, system_prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._response


class ReviewForecastTests(unittest.TestCase):
    def _normalized_inputs(self) -> NormalizedInputs:
        now = datetime.now(timezone.utc)
        return NormalizedInputs(
            run_id="run_review_test",
            collected_at=now,
            forecast_horizon="5 trading days",
            market_universe=["SPY", "QQQ", "IWM", "VIX", "US10Y", "DXY", "OIL", "BTC", "USDJPY"],
            news=[
                NewsItem(
                    source="test",
                    source_type="manual",
                    source_reliability="high",
                    headline="Test headline",
                    summary="Test summary",
                    published_at=now,
                )
            ],
            indicators=[
                MarketIndicator(symbol="SPY", name="SPY", value=500.0, change_pct=0.5, as_of=now),
            ],
            state_variables={},
        )

    def _state_mapping(self) -> StateMappingResult:
        now = datetime.now(timezone.utc)
        return StateMappingResult(
            generated_at=now,
            regime_label="test_regime",
            growth_state="stable",
            inflation_state="stable",
            liquidity_state="neutral",
            volatility_state="normal",
            cross_asset_signals=["signal1"],
            scenarios=[
                StateScenario(
                    name="Base",
                    probability=0.6,
                    directional_implication=DirectionalBias.BULLISH,
                    key_conditions=["cond1"],
                )
            ],
            narrative="test narrative",
        )

    def _forecast_draft(self) -> ForecastDraft:
        now = datetime.now(timezone.utc)
        return ForecastDraft(
            generated_at=now,
            forecast_horizon="5 trading days",
            market_universe=["SPY", "QQQ", "IWM", "VIX", "US10Y", "DXY", "OIL", "BTC", "USDJPY"],
            directional_bias=DirectionalBias.BULLISH,
            confidence=0.62,
            dominant_drivers=["driver1"],
            supportive_evidence=["support1"],
            opposing_evidence=["oppose1"],
            upside_triggers=["up1"],
            downside_triggers=["down1"],
            invalidation_conditions=["inv1"],
            monitoring_list=["mon1"],
            final_thesis="Current observable drivers support a short-horizon bullish bias.",
        )

    def test_draft_coverage_false_positive_fail_is_downgraded(self) -> None:
        draft = self._forecast_draft()
        rule_report = build_rule_report(draft)
        self.assertFalse(rule_report.coverage.get("review_status_checked"))
        self.assertFalse(rule_report.coverage.get("reference_levels_scanned"))

        response = {
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "review_decision": {
                "review_status": "FAIL",
                "is_publishable": False,
                "decision_summary": "rule_report shows review_status_checked=false and reference_levels_scanned=false",
                "hard_fail_count": 1,
                "soft_warn_count": 0,
            },
            "review_findings": {
                "hard_fail_issues": [
                    {
                        "code": "DRAFT_COVERAGE_FAIL",
                        "field": "rule_report.coverage",
                        "message": "review_status_checked=false",
                        "severity": "hard_fail",
                    }
                ],
                "soft_warnings": [],
                "info_notes": [],
            },
            "review_summary": "reference_levels_scanned=false and review_status_checked=false",
            "reviewed_forecast": {
                **draft.model_dump(mode="json"),
                "review_status": "FAIL",
                "anti_hindsight_status": "FAIL",
                "reference_levels": {
                    "support_levels": [],
                    "resistance_levels": [],
                    "risk_triggers": [],
                    "confirmation_levels": [],
                },
            },
            "reference_levels": {
                "support_levels": [],
                "resistance_levels": [],
                "risk_triggers": [],
                "confirmation_levels": [],
            },
        }

        result = run_anti_hindsight_review(
            llm_client=_StubLLMClient(response),
            prompt_template="test",
            normalized_inputs=self._normalized_inputs(),
            state_mapping=self._state_mapping(),
            forecast_draft=draft,
            draft_rule_report=rule_report,
            output_language="en",
        )

        self.assertEqual(result.review_decision.review_status, AntiHindsightStatus.PASS)
        self.assertTrue(result.review_decision.is_publishable)
        self.assertEqual(result.reviewed_forecast.review_status, AntiHindsightStatus.PASS)
        self.assertEqual(len(result.review_findings.hard_fail_issues), 0)
        self.assertGreater(len(result.review_findings.soft_warnings), 0)

    def test_non_coverage_fail_remains_fail(self) -> None:
        draft = self._forecast_draft()
        rule_report = build_rule_report(draft)

        response = {
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "review_decision": {
                "review_status": "FAIL",
                "is_publishable": False,
                "decision_summary": "Core thesis includes hindsight issue",
                "hard_fail_count": 1,
                "soft_warn_count": 0,
            },
            "review_findings": {
                "hard_fail_issues": [
                    {
                        "code": "HINDSIGHT_IN_THESIS",
                        "field": "final_thesis",
                        "message": "Contains hindsight justification",
                        "severity": "hard_fail",
                    }
                ],
                "soft_warnings": [],
                "info_notes": [],
            },
            "review_summary": "Found hindsight in thesis.",
            "reviewed_forecast": {
                **draft.model_dump(mode="json"),
                "review_status": "FAIL",
                "anti_hindsight_status": "FAIL",
                "reference_levels": {
                    "support_levels": [],
                    "resistance_levels": [],
                    "risk_triggers": [],
                    "confirmation_levels": [],
                },
            },
            "reference_levels": {
                "support_levels": [],
                "resistance_levels": [],
                "risk_triggers": [],
                "confirmation_levels": [],
            },
        }

        result = run_anti_hindsight_review(
            llm_client=_StubLLMClient(response),
            prompt_template="test",
            normalized_inputs=self._normalized_inputs(),
            state_mapping=self._state_mapping(),
            forecast_draft=draft,
            draft_rule_report=rule_report,
            output_language="en",
        )

        self.assertEqual(result.review_decision.review_status, AntiHindsightStatus.FAIL)
        self.assertFalse(result.review_decision.is_publishable)

    def test_conditional_price_level_fail_is_downgraded(self) -> None:
        draft = self._forecast_draft()
        draft_payload = draft.model_dump(mode="json")
        draft_payload["final_thesis"] = "若10年期收益率突破100，可能触发科技股估值调整。"
        draft = ForecastDraft.model_validate(draft_payload)
        rule_report = build_rule_report(draft)

        response = {
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "review_decision": {
                "review_status": "FAIL",
                "is_publishable": False,
                "decision_summary": "final_thesis has price level wording",
                "hard_fail_count": 1,
                "soft_warn_count": 0,
            },
            "review_findings": {
                "hard_fail_issues": [
                    {
                        "code": "PRICE_TARGET_IN_FINAL_THESIS",
                        "field": "final_thesis",
                        "message": "Pattern `突破\\s*\\d+(?:\\.\\d+)?` matched text",
                        "severity": "hard_fail",
                    }
                ],
                "soft_warnings": [],
                "info_notes": [],
            },
            "review_summary": "Detected price target in thesis.",
            "reviewed_forecast": {
                **draft.model_dump(mode="json"),
                "review_status": "FAIL",
                "anti_hindsight_status": "FAIL",
                "reference_levels": {
                    "support_levels": [],
                    "resistance_levels": [],
                    "risk_triggers": [],
                    "confirmation_levels": [],
                },
            },
            "reference_levels": {
                "support_levels": [],
                "resistance_levels": [],
                "risk_triggers": [],
                "confirmation_levels": [],
            },
        }

        result = run_anti_hindsight_review(
            llm_client=_StubLLMClient(response),
            prompt_template="test",
            normalized_inputs=self._normalized_inputs(),
            state_mapping=self._state_mapping(),
            forecast_draft=draft,
            draft_rule_report=rule_report,
            output_language="zh",
        )

        self.assertEqual(result.review_decision.review_status, AntiHindsightStatus.PASS)
        self.assertTrue(result.review_decision.is_publishable)
        self.assertEqual(result.reviewed_forecast.review_status, AntiHindsightStatus.PASS)
        self.assertEqual(len(result.review_findings.hard_fail_issues), 0)
        self.assertGreater(len(result.review_findings.soft_warnings), 0)


if __name__ == "__main__":
    unittest.main()
