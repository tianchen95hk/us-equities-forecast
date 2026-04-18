"""Unit tests for storage-layer persistence semantics under governance statuses."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.schemas import AntiHindsightStatus, DirectionalBias, FinalForecast, ReferenceLevels
from app.storage.db import Storage


def _build_forecast(status: AntiHindsightStatus) -> FinalForecast:
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
        anti_hindsight_status=status,
    )


class StorageGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.db_path = root / "forecast.db"
        self.artifacts_dir = root / "artifacts"
        self.settings = Settings(
            database_url=f"sqlite:///{self.db_path}",
            artifacts_dir=str(self.artifacts_dir),
            prompts_dir="./prompts",
        )
        self.storage = Storage(self.settings)
        self.storage.init_db()
        self.addCleanup(self.storage.close)

    def test_save_forecast_allows_fail_status_when_not_publishable(self) -> None:
        run_id = self.storage.create_run("5 trading days", ["SPY"])
        self.storage.save_forecast(
            run_id,
            _build_forecast(AntiHindsightStatus.FAIL),
            run_status="review_fail",
            is_publishable=False,
            decision_summary="review failed",
            hard_fail_count=1,
            soft_warn_count=0,
            reference_levels={"support_levels": ["SPY 500-503"]},
            review_findings={"hard_fail_issues": [{"code": "X"}]},
            review_summary="review failed",
        )
        latest = self.storage.get_latest_forecast()
        self.assertIsNotNone(latest)
        if latest is None:
            self.fail("Expected persisted forecast")
        self.assertEqual(latest.review_status, AntiHindsightStatus.FAIL)

    def test_save_forecast_allows_pass_status(self) -> None:
        run_id = self.storage.create_run("5 trading days", ["SPY"])
        self.storage.save_forecast(
            run_id,
            _build_forecast(AntiHindsightStatus.PASS),
            run_status="approved",
            is_publishable=True,
            decision_summary="approved",
            hard_fail_count=0,
            soft_warn_count=0,
            reference_levels={"support_levels": ["SPY 500-503"]},
            review_findings={"hard_fail_issues": []},
            review_summary="approved",
        )
        latest = self.storage.get_latest_forecast()
        self.assertIsNotNone(latest)
        if latest is None:
            self.fail("Expected persisted forecast")
        self.assertEqual(latest.review_status, AntiHindsightStatus.PASS)


if __name__ == "__main__":
    unittest.main()
