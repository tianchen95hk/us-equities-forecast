"""Unit tests for storage-layer anti-hindsight persistence guard."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.exceptions import RuleViolationError
from app.schemas import AntiHindsightStatus, DirectionalBias, FinalForecast
from app.storage.db import Storage


def _build_forecast(status: AntiHindsightStatus) -> FinalForecast:
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

    def test_save_forecast_rejects_non_pass_status(self) -> None:
        run_id = self.storage.create_run("5 trading days", ["SPY"])
        with self.assertRaises(RuleViolationError):
            self.storage.save_forecast(run_id, _build_forecast(AntiHindsightStatus.FAIL))

    def test_save_forecast_allows_pass_status(self) -> None:
        run_id = self.storage.create_run("5 trading days", ["SPY"])
        self.storage.save_forecast(run_id, _build_forecast(AntiHindsightStatus.PASS))
        latest = self.storage.get_latest_forecast()
        self.assertIsNotNone(latest)
        if latest is None:
            self.fail("Expected persisted forecast")
        self.assertEqual(latest.anti_hindsight_status, AntiHindsightStatus.PASS)


if __name__ == "__main__":
    unittest.main()
