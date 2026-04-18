"""Integration tests for strict 3-call pipeline and publish gate behavior."""

from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.config import Settings
from app.llm_client import BaseLLMClient
from app.pipeline.orchestrator import PipelineDependencies, run_pipeline
from app.schemas import AntiHindsightStatus

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _collect_news_stub(settings: Settings, manual_path: str | None = None) -> tuple[list[dict[str, Any]], str]:
    del settings, manual_path
    return (
        [
            {
                "source": "stub-news",
                "headline": "US macro data shows mixed but cooling inflation signals",
                "summary": "Policy path still data-dependent.",
                "url": "https://example.com/news/1",
                "published_at": "2026-04-17T08:00:00Z",
            }
        ],
        "stub",
    )


def _collect_market_stub(settings: Settings, manual_path: str | None = None) -> tuple[list[dict[str, Any]], str]:
    del settings, manual_path
    as_of = "2026-04-17T08:00:00Z"
    return (
        [
            {
                "symbol": "SPY",
                "name": "SPDR S&P 500 ETF Trust",
                "value": 514.2,
                "previous_value": 512.1,
                "change_pct": 0.41,
                "unit": "usd",
                "as_of": as_of,
            },
            {
                "symbol": "QQQ",
                "name": "Invesco QQQ Trust",
                "value": 436.7,
                "previous_value": 434.2,
                "change_pct": 0.58,
                "unit": "usd",
                "as_of": as_of,
            },
            {
                "symbol": "IWM",
                "name": "iShares Russell 2000 ETF",
                "value": 206.4,
                "previous_value": 205.9,
                "change_pct": 0.24,
                "unit": "usd",
                "as_of": as_of,
            },
            {
                "symbol": "VIX",
                "name": "CBOE Volatility Index",
                "value": 17.9,
                "previous_value": 18.2,
                "change_pct": -1.65,
                "unit": "index",
                "as_of": as_of,
            },
            {
                "symbol": "US10Y",
                "name": "US 10Y Treasury Yield Proxy",
                "value": 4.23,
                "previous_value": 4.20,
                "change_pct": 0.71,
                "unit": "proxy",
                "as_of": as_of,
            },
            {
                "symbol": "DXY",
                "name": "US Dollar Index Proxy",
                "value": 103.1,
                "previous_value": 102.9,
                "change_pct": 0.19,
                "unit": "proxy",
                "as_of": as_of,
            },
            {
                "symbol": "OIL",
                "name": "WTI Crude Oil Proxy",
                "value": 79.6,
                "previous_value": 79.0,
                "change_pct": 0.76,
                "unit": "proxy",
                "as_of": as_of,
            },
        ],
        "stub",
    )


class CountingLLMClient(BaseLLMClient):
    """Deterministic fake LLM to test orchestration behavior."""

    def __init__(self, review_status: AntiHindsightStatus = AntiHindsightStatus.PASS):
        self.calls: list[str] = []
        self.review_status = review_status

    def generate_json(self, task_name: str, system_prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        del system_prompt
        self.calls.append(task_name)

        if task_name == "event_extraction":
            return {
                "generated_at": "2026-04-17T08:05:00Z",
                "summary": "仅基于当前可观测新闻事件提取。",
                "events": [
                    {
                        "event_id": "E1",
                        "category": "macro",
                        "description": "通胀边际降温但政策仍谨慎",
                        "impact_bias": "neutral",
                        "impact_pathway": "宏观不确定性缓和但风险偏好仍需确认。",
                        "confidence": 0.62,
                        "evidence_refs": ["news:1"],
                    }
                ],
            }

        if task_name == "state_and_forecast":
            normalized_inputs = payload["normalized_inputs"]
            output_language = str(payload.get("output_language", "zh")).lower()
            use_zh = output_language == "zh"

            return {
                "state_mapping": {
                    "generated_at": "2026-04-17T08:06:00Z",
                    "regime_label": "stable-growth/moderating-inflation/neutral-liquidity",
                    "growth_state": "stable",
                    "inflation_state": "moderating",
                    "liquidity_state": "neutral",
                    "volatility_state": "contained",
                    "cross_asset_signals": [
                        "VIX维持可控区间",
                        "10Y利率边际抬升但未形成冲击",
                    ],
                    "scenarios": [
                        {
                            "name": "基准情景",
                            "probability": 0.6,
                            "directional_implication": "neutral",
                            "key_conditions": ["波动率维持平稳", "美元无趋势性走强"],
                        }
                    ],
                    "narrative": "状态映射仅使用当前可观测输入。",
                },
                "forecast_draft": {
                    "generated_at": "2026-04-17T08:06:00Z",
                    "forecast_horizon": normalized_inputs["forecast_horizon"],
                    "market_universe": normalized_inputs["market_universe"],
                    "directional_bias": "neutral",
                    "confidence": 0.61,
                    "dominant_drivers": [
                        "跨资产条件处于中性偏稳区间",
                        "宏观事件信号偏中性",
                    ],
                    "supportive_evidence": [
                        "波动率没有进入持续抬升状态",
                        "风险资产与美元未出现极端背离",
                    ],
                    "opposing_evidence": [
                        "利率若继续抬升可能压制估值",
                    ],
                    "upside_triggers": ["波动率进一步回落", "广度改善"],
                    "downside_triggers": ["利率快速上行", "美元走强"],
                    "invalidation_conditions": ["跨资产信号一致反转"],
                    "monitoring_list": ["VIX", "US10Y", "DXY", "SPY/QQQ/IWM广度"],
                    "final_thesis": (
                        "方向判断基于当前可观测状态与跨资产信号，一旦信号反转将撤销观点。"
                        if use_zh
                        else "Directional bias is based on current observable cross-asset signals and is invalidated when those signals flip."
                    ),
                },
            }

        if task_name == "anti_hindsight_review":
            draft = payload["forecast_draft"]
            output_language = str(payload.get("output_language", "zh")).lower()
            use_zh = output_language == "zh"

            reviewed = dict(draft)
            reviewed["anti_hindsight_status"] = self.review_status.value
            if use_zh and not _CJK_RE.search(str(reviewed.get("final_thesis", ""))):
                reviewed["final_thesis"] = "结论基于当前可观测条件，若触发失效条件将调整方向判断。"

            issues = []
            review_summary = "未发现后验叙述问题。" if use_zh else "No hindsight issue detected."
            if self.review_status == AntiHindsightStatus.FAIL:
                issues = ["Review found unresolved hindsight phrasing"]
                review_summary = "审查未通过：存在未消除的后验描述。" if use_zh else "Review failed due to unresolved hindsight phrasing."

            return {
                "reviewed_at": "2026-04-17T08:07:00Z",
                "anti_hindsight_status": self.review_status.value,
                "issues": issues,
                "review_summary": review_summary,
                "reviewed_forecast": reviewed,
            }

        raise AssertionError(f"Unexpected task: {task_name}")


class OrchestratorReviewGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

        root = Path(self._tmp.name)
        self.db_path = root / "forecast.db"
        self.artifacts_dir = root / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        repo_root = Path(__file__).resolve().parents[1]
        self.settings = Settings(
            database_url=f"sqlite:///{self.db_path}",
            artifacts_dir=str(self.artifacts_dir),
            prompts_dir=str(repo_root / "prompts"),
            mock_news_file=str(repo_root / "data/mock/news_latest.json"),
            mock_market_file=str(repo_root / "data/mock/market_latest.json"),
            use_live_data=False,
            output_language="zh",
            output_style="full",
        )

    def _run(self, llm_client: BaseLLMClient):
        deps = PipelineDependencies(
            news_collector=_collect_news_stub,
            market_data_collector=_collect_market_stub,
            llm_client=llm_client,
        )
        return run_pipeline(settings=self.settings, dependencies=deps)

    def test_pipeline_uses_exactly_three_llm_calls(self) -> None:
        llm_client = CountingLLMClient(review_status=AntiHindsightStatus.PASS)
        result = self._run(llm_client)

        self.assertEqual(result.publish_status, "approved")
        self.assertEqual(
            llm_client.calls,
            ["event_extraction", "state_and_forecast", "anti_hindsight_review"],
        )
        self.assertNotIn("state_mapping", llm_client.calls)
        self.assertNotIn("forecast_generation", llm_client.calls)

    def test_publish_gate_rejects_fail_review_and_skips_forecast_table_write(self) -> None:
        llm_client = CountingLLMClient(review_status=AntiHindsightStatus.FAIL)
        result = self._run(llm_client)

        self.assertEqual(result.publish_status, "rejected")
        self.assertIsNone(result.final_forecast)
        self.assertIn("review_rejected", result.artifact_paths)
        self.assertIn("analysis_trace", result.artifact_paths)
        self.assertNotIn("final_forecast", result.artifact_paths)

        review_rejected_path = Path(result.artifact_paths["review_rejected"])
        self.assertTrue(review_rejected_path.exists())
        payload = json.loads(review_rejected_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["publish_status"], "rejected")
        self.assertIn("analysis_variants", payload)
        self.assertIn("publish_gate_report", payload)

        analysis_trace_path = Path(result.artifact_paths["analysis_trace"])
        self.assertTrue(analysis_trace_path.exists())
        analysis_trace = json.loads(analysis_trace_path.read_text(encoding="utf-8"))
        self.assertEqual(analysis_trace["publish_status"], "rejected")
        self.assertIn("analysis_flow", analysis_trace)
        self.assertIn("runtime_assertions", analysis_trace)

        with sqlite3.connect(self.db_path) as conn:
            forecast_count = conn.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0]
            run_status = conn.execute(
                "SELECT status FROM runs WHERE id = ?",
                (result.run_id,),
            ).fetchone()[0]

        self.assertEqual(forecast_count, 0)
        self.assertEqual(run_status, "REVIEW_REJECTED")

    def test_approved_publish_writes_forecast_and_run_status(self) -> None:
        llm_client = CountingLLMClient(review_status=AntiHindsightStatus.PASS)
        result = self._run(llm_client)

        self.assertEqual(result.publish_status, "approved")
        self.assertIsNotNone(result.final_forecast)
        self.assertIn("final_forecast", result.artifact_paths)
        self.assertIn("analysis_trace", result.artifact_paths)

        with sqlite3.connect(self.db_path) as conn:
            forecast_count = conn.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0]
            run_status = conn.execute(
                "SELECT status FROM runs WHERE id = ?",
                (result.run_id,),
            ).fetchone()[0]

        self.assertEqual(forecast_count, 1)
        self.assertEqual(run_status, "SUCCEEDED")

        analysis_trace_path = Path(result.artifact_paths["analysis_trace"])
        self.assertTrue(analysis_trace_path.exists())
        analysis_trace = json.loads(analysis_trace_path.read_text(encoding="utf-8"))
        self.assertEqual(analysis_trace["publish_status"], "approved")
        self.assertIn("publish_gate_report", analysis_trace)
        self.assertTrue(analysis_trace["publish_gate_report"]["approved"])

    def test_chinese_mode_keeps_review_summary_and_final_thesis_in_chinese(self) -> None:
        llm_client = CountingLLMClient(review_status=AntiHindsightStatus.PASS)
        result = self._run(llm_client)

        self.assertEqual(result.publish_status, "approved")
        self.assertIsNotNone(result.final_forecast)
        final_forecast = result.final_forecast
        if final_forecast is None:
            self.fail("final_forecast should be present for approved publish")
        self.assertRegex(final_forecast.final_thesis, _CJK_RE)

        review_path = Path(result.artifact_paths["anti_hindsight_review"])
        review_payload = json.loads(review_path.read_text(encoding="utf-8"))
        self.assertRegex(str(review_payload["review_summary"]), _CJK_RE)


if __name__ == "__main__":
    unittest.main()
