"""News collector: manual source, optional live source, mock fallback."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.exceptions import CollectorError


def _load_news_file(path: str) -> list[dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        raise CollectorError(f"News input file not found: {file_path}")

    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectorError(f"Failed to parse news input file: {file_path}") from exc

    if not isinstance(payload, list):
        raise CollectorError(f"News input file must be a JSON list: {file_path}")
    return payload


def _fetch_live_news(settings: Settings) -> list[dict[str, Any]]:
    if not settings.news_api_key:
        raise CollectorError("NEWS_API_KEY is required for live news fetching")

    params = {
        "q": "(SPY OR QQQ OR IWM OR Federal Reserve OR Treasury yield OR DXY OR oil)",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 20,
        "apiKey": settings.news_api_key,
    }
    headers = {"User-Agent": settings.user_agent}

    try:
        with httpx.Client(timeout=settings.request_timeout_seconds) as client:
            response = client.get(settings.news_api_url, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CollectorError(f"Live news fetch failed: {exc}") from exc

    articles = payload.get("articles", [])
    normalized_articles: list[dict[str, Any]] = []
    for article in articles:
        normalized_articles.append(
            {
                "source": (article.get("source") or {}).get("name") or "unknown",
                "headline": article.get("title") or "",
                "summary": article.get("description") or "",
                "url": article.get("url"),
                "published_at": article.get("publishedAt"),
            }
        )
    return normalized_articles


def collect_news(settings: Settings, manual_path: str | None = None) -> tuple[list[dict[str, Any]], str]:
    """Collect current news inputs and return (items, source_label)."""
    if manual_path:
        return _load_news_file(manual_path), "manual"

    if settings.use_live_data:
        try:
            live_news = _fetch_live_news(settings)
            if live_news:
                return live_news, "live"
        except CollectorError:
            # Intentional fallback to maintain local run reliability.
            pass

    return _load_news_file(settings.mock_news_file), "mock"
