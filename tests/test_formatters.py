"""Unit tests for CLI output formatters."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.pipeline.orchestrator import PipelineResult
from app.presentation.formatters import format_cli_output, render_cli_output
from app.schemas import (
    AntiHindsightStatus,
    DirectionalBias,
    FinalForecast,
    GovernanceIssue,
    IssueSeverity,
    ReferenceLevels,
)


class FormatterTests(unittest.TestCase):
    def _sample_forecast(self) -> FinalForecast:
        return FinalForecast(
            generated_at=datetime.now(timezone.utc),
            forecast_horizon="5 trading days",
            market_universe=["SPY", "QQQ", "IWM", "VIX", "US10Y", "DXY", "OIL", "BTC", "USDJPY"],
            directional_bias=DirectionalBias.BULLISH,
            confidence=0.61,
            dominant_drivers=["driver1", "driver2", "driver3", "driver4"],
            supportive_evidence=["support1"],
            opposing_evidence=["oppose1"],
            upside_triggers=["up1", "up2", "up3", "up4"],
            downside_triggers=["down1", "down2", "down3"],
            invalidation_conditions=["inv1", "inv2", "inv3", "inv4"],
            monitoring_list=["mon1", "mon2", "mon3", "mon4", "mon5", "mon6"],
            reference_levels=ReferenceLevels(
                support_levels=["SPY 500-503"],
                resistance_levels=["SPY 518-520"],
            ),
            final_thesis="thesis text based on current observable drivers only.",
            anti_hindsight_status=AntiHindsightStatus.PASS,
        )

    def _sample_result(self, *, is_publishable: bool = True) -> PipelineResult:
        forecast = self._sample_forecast()
        if not is_publishable:
            payload = forecast.model_dump(mode="json")
            payload["review_status"] = AntiHindsightStatus.FAIL.value
            payload["anti_hindsight_status"] = AntiHindsightStatus.FAIL.value
            forecast = FinalForecast.model_validate(payload)

        return PipelineResult(
            run_id="run_123",
            final_forecast=forecast,
            publish_status="approved" if is_publishable else "rejected",
            run_status="approved" if is_publishable else "review_fail",
            is_publishable=is_publishable,
            review_status=("PASS" if is_publishable else "FAIL"),
            decision_summary="governance decision summary",
            rejection_reasons=([] if is_publishable else ["REVIEW_STATUS_FAIL: reviewer marked FAIL"]),
            review_summary="review summary",
            review_findings={
                "hard_fail_issues": (
                    []
                    if is_publishable
                    else [
                        GovernanceIssue(
                            code="REVIEW_STATUS_FAIL",
                            field="review_decision.review_status",
                            message="reviewer marked FAIL",
                            severity=IssueSeverity.HARD_FAIL,
                        ).model_dump(mode="json")
                    ]
                ),
                "soft_warnings": [],
                "info_notes": [],
            },
            reference_levels=forecast.reference_levels.model_dump(mode="json"),
            collected_at="2026-04-17T10:00:00+00:00",
            reviewed_at="2026-04-17T10:06:00+00:00",
            latest_news_at="2026-04-17T09:40:00+00:00",
            latest_market_at="2026-04-17T09:55:00+00:00",
            run_started_at="2026-04-17T09:59:00+00:00",
            run_completed_at="2026-04-17T10:07:00+00:00",
            market_snapshot={
                "SPY": {
                    "name": "SPDR S&P 500 ETF Trust",
                    "value": 510.1,
                    "change_pct": 0.4,
                    "as_of": "2026-04-17T10:00:00+00:00",
                }
            },
            news_snapshot=[
                {
                    "source": "stub-news",
                    "headline": "Macro signal mixed",
                    "summary": "Liquidity and rates signals diverged overnight.",
                    "url": "https://example.com/news/1",
                    "published_at": "2026-04-17T09:40:00+00:00",
                }
            ],
            reasoning_summary=["状态映射: stable regime", "主情景: Base(60%), 方向=neutral"],
            state_snapshot={
                "regime_label": "stable regime",
                "growth_state": "moderate",
                "inflation_state": "sticky",
                "liquidity_state": "neutral",
                "volatility_state": "normal",
                "cross_asset_signals": ["vix benign", "rates elevated"],
                "scenarios": [
                    {
                        "name": "Base",
                        "probability": 0.6,
                        "directional_implication": "bullish",
                        "key_conditions": ["vix stays low"],
                    }
                ],
            },
            confidence_snapshot={
                "components": {
                    "scenario_alignment": 0.7,
                    "event_consensus": 0.6,
                    "cross_asset_confirmation": 0.65,
                    "evidence_balance": 0.8,
                },
                "penalties": {"freshness_penalty": 0.0, "risk_penalty": 0.03},
            },
            artifact_paths={
                "final_forecast": "/tmp/final.json",
                "market_raw": "/tmp/market.json",
                "anti_hindsight_review": "/tmp/review.json",
                "review_rejected": "/tmp/rejected.json",
            },
            analysis_flow=[{"stage": "collect_inputs", "status": "ok", "elapsed_seconds": 0.1}],
            publish_gate_report={"approved": is_publishable},
            market_snapshot_summary=["SPY/QQQ稳健，US10Y偏强"],
            top_news_signals=[
                {
                    "signal": "Macro headline mixed",
                    "direction": "mixed",
                    "confidence": 0.6,
                    "evidence_refs": ["news:1"],
                    "rationale": "policy uncertainty",
                }
            ],
            top_market_signals=[
                {
                    "signal": "US10Y up",
                    "direction": "down",
                    "confidence": 0.61,
                    "evidence_refs": ["US10Y"],
                    "rationale": "valuation pressure",
                }
            ],
            signal_conflicts=["rates up vs risk-assets resilient"],
            forecast_support_map=["support path 1"],
            forecast_opposition_map=["opposition path 1"],
            monitoring_priorities=["VIX", "US10Y"],
            next_run_questions=["Will rates stabilize next run?"],
            pre_forecast_feedback={"generated_at": "2026-04-17T10:01:00+00:00"},
            post_forecast_feedback={"generated_at": "2026-04-17T10:02:00+00:00"},
            factor_snapshot={
                "earnings_revision": {"direction": "up", "score": 0.45, "strength": "medium", "as_of": "2026-04-17T10:00:00+00:00"},
                "volatility": {"direction": "up", "score": 0.3, "strength": "medium", "as_of": "2026-04-17T10:00:00+00:00"},
                "rates": {"direction": "down", "score": -0.25, "strength": "medium", "as_of": "2026-04-17T10:00:00+00:00"},
                "dollar": {"direction": "neutral", "score": 0.0, "strength": "low", "as_of": "2026-04-17T10:00:00+00:00"},
                "energy_geopolitics": {"direction": "neutral", "score": -0.05, "strength": "low", "as_of": "2026-04-17T10:00:00+00:00"},
            },
            dominant_factor={
                "dominant_factor": "earnings_revision",
                "dominant_factors": ["earnings_revision"],
                "tie_detected": False,
                "scoreboard": {"earnings_revision": 0.144},
            },
            dominant_factor_explainer="主导判定按 abs(weight*score) 排序。",
            earnings_revision_proxy_summary={
                "signal": "up",
                "score": 0.45,
                "summary": "Earnings revisions are net positive.",
                "limitations": [],
            },
            earnings_proxy_source="live_fmp_partial",
        )

    def test_simple_zh_output(self) -> None:
        payload = format_cli_output(self._sample_result(), language="zh", style="simple")
        self.assertEqual(payload["方向判断"], "看多")
        self.assertEqual(payload["反后验审查"], "通过")
        self.assertEqual(payload["置信度"], "61.0%")
        self.assertEqual(len(payload["核心驱动"]), 3)
        self.assertIn("市场信息", payload)
        self.assertIn("思维总结", payload)
        self.assertIn("最新新闻", payload)
        self.assertEqual(payload["发布状态"], "已通过")
        self.assertIn("数据反馈", payload)
        self.assertIn("五因子与主导", payload)

    def test_simple_zh_output_for_review_fail_still_has_analysis(self) -> None:
        payload = format_cli_output(
            self._sample_result(is_publishable=False),
            language="zh",
            style="simple",
        )
        self.assertEqual(payload["发布状态"], "已拒绝")
        self.assertIn("方向判断", payload)
        self.assertIn("结论摘要", payload)

    def test_full_output_contains_full_forecast(self) -> None:
        payload = format_cli_output(self._sample_result(), language="zh", style="full")
        self.assertIn("final_forecast", payload)
        self.assertIn("artifact_paths", payload)
        self.assertEqual(payload["publish_status"], "approved")
        self.assertIn("run_status", payload)
        self.assertIn("is_publishable", payload)
        self.assertIn("review_findings", payload)
        self.assertIn("market_snapshot_summary", payload)
        self.assertIn("post_forecast_feedback", payload)

    def test_telegram_zh_output_contains_expected_blocks_and_order(self) -> None:
        payload = format_cli_output(self._sample_result(), language="zh", style="telegram")
        keys = list(payload.keys())
        self.assertEqual(keys[0], "结论")
        self.assertEqual(keys[1], "审查风险摘要")
        self.assertEqual(keys[2], "监控与参考位")
        self.assertIn("五因子与主导", payload)
        self.assertIn("数据反馈层", payload)
        self.assertIn("条件结构化观察", payload)
        self.assertIn("市场信息", payload)
        self.assertIn("最新新闻", payload)
        self.assertIn("思维总结", payload)
        self.assertIn("文件路径", payload)

    def test_telegram_zh_output_for_rejected_contains_rejection_details(self) -> None:
        payload = format_cli_output(
            self._sample_result(is_publishable=False),
            language="zh",
            style="telegram",
        )
        keys = list(payload.keys())
        self.assertEqual(keys[0], "结论")
        self.assertEqual(keys[1], "审查风险摘要")
        self.assertEqual(keys[2], "拒绝详情")
        self.assertEqual(keys[3], "监控与参考位")
        self.assertIn("rejection_reasons", payload["拒绝详情"])

    def test_market_snapshot_zh_enforces_full_universe_with_placeholders(self) -> None:
        payload = format_cli_output(self._sample_result(), language="zh", style="telegram")
        market_rows = payload["市场信息"]
        self.assertEqual(len(market_rows), 9)
        symbols = [row["标的"] for row in market_rows]
        self.assertEqual(symbols, ["SPY", "QQQ", "IWM", "VIX", "US10Y", "DXY", "OIL", "BTC", "USDJPY"])
        qqq_row = next(row for row in market_rows if row["标的"] == "QQQ")
        self.assertEqual(qqq_row["名称"], "数据缺失")

    def test_factor_card_includes_equity_impact_and_rates_logic_note(self) -> None:
        payload = format_cli_output(self._sample_result(), language="zh", style="telegram")
        factor_rows = payload["五因子与主导"]["因子明细"]
        rates_row = next(row for row in factor_rows if row["因子"] == "rates")
        self.assertIn("对权益", rates_row["对权益含义"])
        self.assertIn("对权益影响", rates_row["逻辑说明"])

    def test_render_cli_output_telegram_text_is_panel_like(self) -> None:
        rendered = render_cli_output(self._sample_result(), language="zh", style="telegram")
        self.assertIn("[结论]", rendered)
        self.assertIn("[五因子真实表现]", rendered)
        self.assertIn("[市场快照（9标的）]", rendered)
        self.assertIn("[条件结构（可执行观察）]", rendered)
        self.assertNotIn('"{', rendered)


if __name__ == "__main__":
    unittest.main()
