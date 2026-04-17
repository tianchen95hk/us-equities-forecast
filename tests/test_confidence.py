"""Unit tests for deterministic confidence computation."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.pipeline.confidence import compute_confidence
from app.schemas import (
    EventExtractionResult,
    InputFreshnessReport,
    StateMappingResult,
    StateScenario,
)


class ConfidenceTests(unittest.TestCase):
    def _state_mapping(self) -> StateMappingResult:
        return StateMappingResult(
            generated_at=datetime.now(timezone.utc),
            regime_label="stable-growth/moderating-inflation/neutral-liquidity",
            growth_state="stable",
            inflation_state="moderating",
            liquidity_state="neutral",
            volatility_state="contained",
            cross_asset_signals=["VIX contained", "Rates stable"],
            scenarios=[
                StateScenario(
                    name="Base",
                    probability=0.62,
                    directional_implication="neutral",
                    key_conditions=["No macro shock"],
                )
            ],
            narrative="State remains mixed but stable.",
        )

    def _freshness_report(self) -> InputFreshnessReport:
        return InputFreshnessReport(
            checked_at=datetime.now(timezone.utc),
            max_news_age_hours=72,
            max_market_age_minutes=60,
            news_items_checked=5,
            market_items_checked=7,
            stale_news=[],
            stale_market=[],
            has_blocking_issues=False,
            summary="ok",
        )

    def test_confidence_is_deterministic_and_bounded(self) -> None:
        state_mapping = self._state_mapping()
        freshness = self._freshness_report()

        structured_events = EventExtractionResult(
            generated_at=datetime.now(timezone.utc),
            summary="Mixed events",
            events=[],
        ).model_dump(mode="json")

        forecast_payload = {
            "directional_bias": "neutral",
            "supportive_evidence": ["support-1", "support-2"],
            "opposing_evidence": ["oppose-1", "oppose-2"],
        }

        result_a = compute_confidence(
            state_mapping=state_mapping,
            structured_events_payload=structured_events,
            forecast_payload=forecast_payload,
            freshness_report=freshness,
            latest_available_fallback_applied=False,
        )
        result_b = compute_confidence(
            state_mapping=state_mapping,
            structured_events_payload=structured_events,
            forecast_payload=forecast_payload,
            freshness_report=freshness,
            latest_available_fallback_applied=False,
        )

        self.assertEqual(result_a.confidence, result_b.confidence)
        self.assertGreaterEqual(result_a.confidence, 0.35)
        self.assertLessEqual(result_a.confidence, 0.90)

    def test_latest_available_fallback_adds_penalty(self) -> None:
        state_mapping = self._state_mapping()
        freshness = self._freshness_report()
        structured_events = {"events": []}
        forecast_payload = {
            "directional_bias": "neutral",
            "supportive_evidence": ["support-1", "support-2"],
            "opposing_evidence": ["oppose-1", "oppose-2"],
        }

        base = compute_confidence(
            state_mapping=state_mapping,
            structured_events_payload=structured_events,
            forecast_payload=forecast_payload,
            freshness_report=freshness,
            latest_available_fallback_applied=False,
        )
        fallback = compute_confidence(
            state_mapping=state_mapping,
            structured_events_payload=structured_events,
            forecast_payload=forecast_payload,
            freshness_report=freshness,
            latest_available_fallback_applied=True,
        )

        self.assertLessEqual(fallback.confidence, base.confidence)


if __name__ == "__main__":
    unittest.main()
