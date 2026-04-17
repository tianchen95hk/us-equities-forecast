"""Unit tests for CLI output formatters."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.pipeline.orchestrator import PipelineResult
from app.presentation.formatters import format_cli_output
from app.schemas import AntiHindsightStatus, DirectionalBias, FinalForecast


class FormatterTests(unittest.TestCase):
    def _sample_result(self) -> PipelineResult:
        forecast = FinalForecast(
            generated_at=datetime.now(timezone.utc),
            forecast_horizon="5 trading days",
            market_universe=["SPY", "QQQ", "IWM", "VIX", "US10Y", "DXY", "OIL"],
            directional_bias=DirectionalBias.BULLISH,
            confidence=0.61,
            dominant_drivers=["driver1", "driver2", "driver3", "driver4"],
            supportive_evidence=["support1"],
            opposing_evidence=["oppose1"],
            upside_triggers=["up1", "up2", "up3", "up4"],
            downside_triggers=["down1", "down2", "down3"],
            invalidation_conditions=["inv1", "inv2", "inv3", "inv4"],
            monitoring_list=["mon1", "mon2", "mon3", "mon4", "mon5", "mon6"],
            final_thesis="thesis text",
            anti_hindsight_status=AntiHindsightStatus.PASS,
        )
        return PipelineResult(
            run_id="run_123",
            final_forecast=forecast,
            publish_status="approved",
            rejection_reasons=[],
            collected_at="2026-04-17T10:00:00+00:00",
            reviewed_at="2026-04-17T10:06:00+00:00",
            latest_news_at="2026-04-17T09:40:00+00:00",
            latest_market_at="2026-04-17T09:55:00+00:00",
            run_started_at="2026-04-17T09:59:00+00:00",
            run_completed_at="2026-04-17T10:07:00+00:00",
            artifact_paths={
                "final_forecast": "/tmp/final.json",
                "market_raw": "/tmp/market.json",
                "anti_hindsight_review": "/tmp/review.json",
            },
        )

    def test_simple_zh_output(self) -> None:
        payload = format_cli_output(self._sample_result(), language="zh", style="simple")
        self.assertEqual(payload["方向判断"], "看多")
        self.assertEqual(payload["反后验审查"], "通过")
        self.assertEqual(payload["置信度"], "61.0%")
        self.assertEqual(len(payload["核心驱动"]), 3)

    def test_full_output_contains_full_forecast(self) -> None:
        payload = format_cli_output(self._sample_result(), language="zh", style="full")
        self.assertIn("final_forecast", payload)
        self.assertIn("artifact_paths", payload)
        self.assertEqual(payload["publish_status"], "approved")
        self.assertIn("collected_at", payload)

    def test_telegram_zh_output_contains_expected_blocks(self) -> None:
        payload = format_cli_output(self._sample_result(), language="zh", style="telegram")
        self.assertIn("运行信息", payload)
        self.assertIn("结论", payload)
        self.assertIn("核心依据", payload)
        self.assertIn("条件结构", payload)
        self.assertIn("文件路径", payload)

    def test_simple_zh_output_for_rejected_publish(self) -> None:
        rejected_result = PipelineResult(
            run_id="run_rejected",
            final_forecast=None,
            publish_status="rejected",
            rejection_reasons=["ANTI_HINDSIGHT_FAIL: review status is FAIL"],
            run_started_at="2026-04-17T09:59:00+00:00",
            run_completed_at="2026-04-17T10:07:00+00:00",
            artifact_paths={
                "review_rejected": "/tmp/review_rejected.json",
                "anti_hindsight_review": "/tmp/review.json",
                "draft_rule_report": "/tmp/draft_rule_report.json",
                "post_repair_rule_report": "/tmp/post_repair_rule_report.json",
            },
        )

        payload = format_cli_output(rejected_result, language="zh", style="simple")
        self.assertEqual(payload["发布状态"], "已拒绝")
        self.assertIn("拒绝原因", payload)
        self.assertIn("文件路径", payload)


if __name__ == "__main__":
    unittest.main()
