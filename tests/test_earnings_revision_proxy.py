"""Unit tests for earnings revision proxy collector behavior."""

from __future__ import annotations

import unittest

from app.collectors.earnings_revision import collect_earnings_revision_proxy
from app.config import Settings
from app.exceptions import CollectorError


class EarningsRevisionProxyTests(unittest.TestCase):
    def test_non_live_mode_returns_disabled_proxy(self) -> None:
        settings = Settings(use_live_data=False, fmp_api_key=None)
        proxy, source = collect_earnings_revision_proxy(settings)

        self.assertEqual(source, "disabled")
        self.assertEqual(proxy.coverage_status, "none")
        self.assertEqual(proxy.signal.value, "neutral")
        self.assertGreaterEqual(len(proxy.limitations), 1)

    def test_live_without_key_in_non_strict_mode_returns_fallback(self) -> None:
        settings = Settings(use_live_data=True, strict_live_mode=False, fmp_api_key=None)
        proxy, source = collect_earnings_revision_proxy(settings)

        self.assertEqual(source, "no_api_key")
        self.assertEqual(proxy.coverage_status, "none")
        self.assertEqual(proxy.score, 0.0)

    def test_live_without_key_in_strict_mode_raises(self) -> None:
        settings = Settings(use_live_data=True, strict_live_mode=True, fmp_api_key=None)
        with self.assertRaises(CollectorError):
            collect_earnings_revision_proxy(settings)


if __name__ == "__main__":
    unittest.main()
