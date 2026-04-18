"""Unit tests for news normalization/dedup source metadata."""

from __future__ import annotations

import unittest

from app.collectors.news import _dedupe_news


class NewsSourceTests(unittest.TestCase):
    def test_dedupe_keeps_source_metadata(self) -> None:
        payload = [
            {
                "source": "Reuters",
                "source_type": "newsapi",
                "source_reliability": "high",
                "headline": "Fed keeps rates unchanged",
                "summary": "Policy statement unchanged.",
                "url": "https://example.com/a",
                "published_at": "2026-04-18T10:00:00Z",
            },
            {
                "source": "Reuters",
                "source_type": "newsapi",
                "source_reliability": "high",
                "headline": "Fed keeps rates unchanged",
                "summary": "Duplicate headline.",
                "url": "https://example.com/a",
                "published_at": "2026-04-18T10:01:00Z",
            },
            {
                "source": "SEC",
                "source_type": "sec",
                "source_reliability": "very_high",
                "headline": "SEC announces enforcement action",
                "summary": "Regulatory event",
                "url": "https://sec.gov/example",
                "published_at": "2026-04-18T09:30:00Z",
            },
        ]

        deduped = _dedupe_news(payload)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["source_type"], "newsapi")
        self.assertEqual(deduped[1]["source_type"], "sec")
        self.assertEqual(deduped[1]["source_reliability"], "very_high")


if __name__ == "__main__":
    unittest.main()
