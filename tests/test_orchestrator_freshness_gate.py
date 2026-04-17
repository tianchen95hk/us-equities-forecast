"""Integration tests for stale-input rejection before LLM stages."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.config import Settings
from app.llm_client import BaseLLMClient
from app.pipeline.orchestrator import PipelineDependencies, run_pipeline
from app.schemas import AntiHindsightStatus


class CountingNoopLLMClient(BaseLLMClient):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate_json(self, task_name: str, system_prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        del system_prompt, payload
        self.calls.append(task_name)
        raise AssertionError("LLM should not be called when freshness gate rejects inputs")


class MinimalPassLLMClient(BaseLLMClient):
    """LLM stub used to verify latest-available fallback continues pipeline."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate_json(self, task_name: str, system_prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        del system_prompt
        self.calls.append(task_name)
        if task_name == "event_extraction":
            return {
                "generated_at": "2026-04-17T10:00:00Z",
                "summary": "stale inputs still produce observable events",
                "events": [
                    {
                        "event_id": "E1",
                        "category": "market",
                        "description": "Cross-asset signal remains mixed",
                        "impact_bias": "neutral",
                        "impact_pathway": "No directional breakout in observable data",
                        "confidence": 0.6,
                        "evidence_refs": ["stub"],
                    }
                ],
            }

        if task_name == "state_and_forecast":
            normalized_inputs = payload["normalized_inputs"]
            return {
                "state_mapping": {
                    "generated_at": "2026-04-17T10:01:00Z",
                    "regime_label": "stable",
                    "growth_state": "stable",
                    "inflation_state": "sticky",
                    "liquidity_state": "neutral",
                    "volatility_state": "contained",
                    "cross_asset_signals": ["Rates stable", "Volatility contained"],
                    "scenarios": [
                        {
                            "name": "Base",
                            "probability": 0.6,
                            "directional_implication": "neutral",
                            "key_conditions": ["No macro shock"],
                        }
                    ],
                    "narrative": "State derived from latest available observations.",
                },
                "forecast_draft": {
                    "generated_at": "2026-04-17T10:01:00Z",
                    "forecast_horizon": normalized_inputs["forecast_horizon"],
                    "market_universe": normalized_inputs["market_universe"],
                    "directional_bias": "neutral",
                    "confidence": 0.6,
                    "dominant_drivers": ["Cross-asset stability"],
                    "supportive_evidence": ["VIX contained", "Risk assets stable"],
                    "opposing_evidence": ["Oil remains elevated"],
                    "upside_triggers": ["Volatility cools further"],
                    "downside_triggers": ["Rates re-accelerate upward"],
                    "invalidation_conditions": ["Cross-asset signals flip"],
                    "monitoring_list": ["VIX", "US10Y", "DXY"],
                    "final_thesis": "在最新可得输入下维持中性判断，若跨资产信号出现系统性反转则该判断失效。",
                },
            }

        if task_name == "anti_hindsight_review":
            reviewed = dict(payload["forecast_draft"])
            reviewed["anti_hindsight_status"] = AntiHindsightStatus.PASS.value
            return {
                "reviewed_at": "2026-04-17T10:02:00Z",
                "anti_hindsight_status": AntiHindsightStatus.PASS.value,
                "issues": [],
                "review_summary": "No hindsight issue detected.",
                "reviewed_forecast": reviewed,
            }

        raise AssertionError(f"Unexpected task: {task_name}")


def _stale_news_collector(settings: Settings, manual_path: str | None = None) -> tuple[list[dict[str, Any]], str]:
    del settings, manual_path
    return (
        [
            {
                "source": "stub-news",
                "headline": "Old macro headline",
                "summary": "",
                "url": "https://example.com/news/old",
                "published_at": "2026-04-10T00:00:00Z",
            }
        ],
        "stub",
    )


def _stale_market_collector(settings: Settings, manual_path: str | None = None) -> tuple[list[dict[str, Any]], str]:
    del settings, manual_path
    old_as_of = "2026-04-17T00:00:00Z"
    return (
        [
            {
                "symbol": "SPY",
                "name": "SPDR S&P 500 ETF Trust",
                "value": 514.2,
                "previous_value": 512.1,
                "change_pct": 0.41,
                "unit": "usd",
                "as_of": old_as_of,
            },
            {
                "symbol": "QQQ",
                "name": "Invesco QQQ Trust",
                "value": 436.7,
                "previous_value": 434.2,
                "change_pct": 0.58,
                "unit": "usd",
                "as_of": old_as_of,
            },
            {
                "symbol": "IWM",
                "name": "iShares Russell 2000 ETF",
                "value": 206.4,
                "previous_value": 205.9,
                "change_pct": 0.24,
                "unit": "usd",
                "as_of": old_as_of,
            },
            {
                "symbol": "VIX",
                "name": "CBOE Volatility Index",
                "value": 17.9,
                "previous_value": 18.2,
                "change_pct": -1.65,
                "unit": "index",
                "as_of": old_as_of,
            },
            {
                "symbol": "US10Y",
                "name": "US 10Y Treasury Yield Proxy",
                "value": 4.23,
                "previous_value": 4.20,
                "change_pct": 0.71,
                "unit": "proxy",
                "as_of": old_as_of,
            },
            {
                "symbol": "DXY",
                "name": "US Dollar Index Proxy",
                "value": 103.1,
                "previous_value": 102.9,
                "change_pct": 0.19,
                "unit": "proxy",
                "as_of": old_as_of,
            },
            {
                "symbol": "OIL",
                "name": "WTI Crude Oil Proxy",
                "value": 79.6,
                "previous_value": 79.0,
                "change_pct": 0.76,
                "unit": "proxy",
                "as_of": old_as_of,
            },
        ],
        "stub",
    )


class OrchestratorFreshnessGateTests(unittest.TestCase):
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
            enforce_input_freshness=True,
            max_news_age_hours=72,
            max_market_age_minutes=60,
            allow_latest_available_fallback=False,
        )

    def test_stale_inputs_reject_before_llm(self) -> None:
        llm_client = CountingNoopLLMClient()
        deps = PipelineDependencies(
            news_collector=_stale_news_collector,
            market_data_collector=_stale_market_collector,
            llm_client=llm_client,
        )

        result = run_pipeline(settings=self.settings, dependencies=deps)

        self.assertEqual(result.publish_status, "rejected")
        self.assertIsNone(result.final_forecast)
        self.assertIn("input_rejected", result.artifact_paths)
        self.assertIn("input_freshness_report", result.artifact_paths)
        self.assertEqual(llm_client.calls, [])

        with sqlite3.connect(self.db_path) as conn:
            forecast_count = conn.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0]
            run_status = conn.execute(
                "SELECT status FROM runs WHERE id = ?",
                (result.run_id,),
            ).fetchone()[0]

        self.assertEqual(forecast_count, 0)
        self.assertEqual(run_status, "INPUT_STALE_REJECTED")

    def test_stale_inputs_continue_when_latest_available_fallback_enabled(self) -> None:
        fallback_settings = self.settings.model_copy(
            update={
                "allow_latest_available_fallback": True,
                "latest_available_max_news_age_hours": 99999,
                "latest_available_max_market_age_minutes": 99999,
            }
        )
        llm_client = MinimalPassLLMClient()
        deps = PipelineDependencies(
            news_collector=_stale_news_collector,
            market_data_collector=_stale_market_collector,
            llm_client=llm_client,
        )

        result = run_pipeline(settings=fallback_settings, dependencies=deps)

        self.assertEqual(result.publish_status, "approved")
        self.assertIn("input_latest_available_fallback", result.artifact_paths)
        self.assertNotIn("input_rejected", result.artifact_paths)
        self.assertEqual(
            llm_client.calls,
            ["event_extraction", "state_and_forecast", "anti_hindsight_review"],
        )


if __name__ == "__main__":
    unittest.main()
