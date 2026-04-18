"""Tests for feedback-layer normalization and deterministic fallback behavior."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.pipeline.generate_feedback import (
    _normalize_pre_feedback_response,
    build_pre_feedback_fallback,
)
from app.schemas import EventExtractionResult, NormalizedInputs


class FeedbackLayerTests(unittest.TestCase):
    def _normalized_inputs(self) -> NormalizedInputs:
        now = datetime.now(timezone.utc)
        return NormalizedInputs(
            run_id="run_feedback",
            collected_at=now,
            forecast_horizon="5 trading days",
            market_universe=["SPY", "QQQ", "IWM", "VIX", "US10Y", "DXY", "OIL", "BTC", "USDJPY"],
            news=[],
            indicators=[],
            state_variables={},
        )

    def test_normalize_pre_feedback_response_backfills_missing_universe_symbols(self) -> None:
        normalized_inputs = self._normalized_inputs()
        response = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "market_snapshot_summary": ["SPY: 最新值=510.0, 日变动=0.4%, 时间=2026-04-17T10:00:00+00:00"],
            "top_news_signals": [],
            "top_market_signals": [],
            "signal_conflicts": [],
        }
        market_snapshot = {
            "SPY": {"value": 510.0, "change_pct": 0.4, "as_of": "2026-04-17T10:00:00+00:00"},
            "QQQ": {"value": 450.0, "change_pct": 0.2, "as_of": "2026-04-17T10:00:00+00:00"},
        }

        normalized = _normalize_pre_feedback_response(
            response,
            datetime.now(timezone.utc).isoformat(),
            market_universe=normalized_inputs.market_universe,
            market_snapshot=market_snapshot,
            output_language="zh",
        )

        summary = normalized["market_snapshot_summary"]
        self.assertEqual(len(summary), len(normalized_inputs.market_universe))
        for symbol in normalized_inputs.market_universe:
            self.assertTrue(any(line.startswith(f"{symbol}:") for line in summary))

    def test_pre_feedback_fallback_covers_full_universe_without_truncation(self) -> None:
        normalized_inputs = self._normalized_inputs()
        now_iso = datetime.now(timezone.utc).isoformat()
        market_snapshot = {
            symbol: {
                "value": float(index + 1),
                "change_pct": 0.1 * (index + 1),
                "as_of": now_iso,
            }
            for index, symbol in enumerate(normalized_inputs.market_universe)
        }
        structured_events = EventExtractionResult(
            generated_at=datetime.now(timezone.utc),
            summary="none",
            events=[],
        )

        fallback = build_pre_feedback_fallback(
            normalized_inputs=normalized_inputs,
            structured_events=structured_events,
            market_snapshot=market_snapshot,
        )

        self.assertEqual(len(fallback.market_snapshot_summary), len(normalized_inputs.market_universe))
        self.assertEqual(len(fallback.top_market_signals), len(normalized_inputs.market_universe))


if __name__ == "__main__":
    unittest.main()
