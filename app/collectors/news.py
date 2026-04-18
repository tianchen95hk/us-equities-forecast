"""News collector: manual source, multi-live aggregation, and mock fallback."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

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


def _save_news_file(path: str, payload: list[dict[str, Any]]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _fetch_live_newsapi(settings: Settings) -> list[dict[str, Any]]:
    if not settings.news_api_key:
        raise CollectorError("NEWS_API_KEY is required for live news fetching")

    params = {
        "q": settings.live_news_query,
        "language": "en",
        "sortBy": "publishedAt",
        "searchIn": "title,description",
        "domains": ",".join(settings.live_news_domains),
        "pageSize": max(1, int(settings.live_news_page_size)),
        "from": settings.news_from_iso(),
        "apiKey": settings.news_api_key,
    }
    headers = {"User-Agent": settings.user_agent}

    timeout = httpx.Timeout(
        connect=min(8.0, settings.request_timeout_seconds),
        read=settings.request_timeout_seconds,
        write=min(8.0, settings.request_timeout_seconds),
        pool=min(8.0, settings.request_timeout_seconds),
    )

    with httpx.Client(timeout=timeout) as client:
        response = client.get(settings.news_api_url, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()

    articles = payload.get("articles", [])
    out: list[dict[str, Any]] = []
    for article in articles:
        out.append(
            {
                "source": (article.get("source") or {}).get("name") or "newsapi",
                "source_type": "newsapi",
                "source_reliability": "high",
                "headline": article.get("title") or "",
                "summary": article.get("description") or "",
                "url": article.get("url"),
                "published_at": article.get("publishedAt"),
            }
        )
    return out


def _fetch_live_sec_rss(settings: Settings) -> list[dict[str, Any]]:
    timeout = httpx.Timeout(
        connect=min(8.0, settings.request_timeout_seconds),
        read=settings.request_timeout_seconds,
        write=min(8.0, settings.request_timeout_seconds),
        pool=min(8.0, settings.request_timeout_seconds),
    )
    headers = {"User-Agent": settings.user_agent}

    items: list[dict[str, Any]] = []
    with httpx.Client(timeout=timeout) as client:
        for url in settings.sec_rss_urls:
            try:
                response = client.get(url, headers=headers)
                response.raise_for_status()
                root = ElementTree.fromstring(response.text)
            except Exception:  # noqa: BLE001
                continue

            for node in root.findall(".//item")[:30]:
                title = (node.findtext("title") or "").strip()
                if not title:
                    continue
                link = (node.findtext("link") or "").strip() or None
                summary = (node.findtext("description") or "").strip()
                published_at = _parse_rss_dt(node.findtext("pubDate"))

                items.append(
                    {
                        "source": "SEC",
                        "source_type": "sec",
                        "source_reliability": "very_high",
                        "headline": title,
                        "summary": summary,
                        "url": link,
                        "published_at": published_at,
                    }
                )
    return items


def _fetch_live_fmp_news(settings: Settings) -> list[dict[str, Any]]:
    if not settings.fmp_api_key:
        return []

    tickers = ",".join(settings.earnings_proxy_top_tickers[:12])
    url = f"{settings.fmp_base_url.rstrip('/')}/stock_news"
    params = {
        "tickers": tickers,
        "limit": max(1, int(settings.fmp_news_limit)),
        "apikey": settings.fmp_api_key,
    }
    headers = {"User-Agent": settings.user_agent}
    timeout = httpx.Timeout(
        connect=min(8.0, settings.request_timeout_seconds),
        read=settings.request_timeout_seconds,
        write=min(8.0, settings.request_timeout_seconds),
        pool=min(8.0, settings.request_timeout_seconds),
    )

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except Exception:  # noqa: BLE001
        return []

    if not isinstance(payload, list):
        return []

    out: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "source": item.get("site") or "FMP",
                "source_type": "fmp_news",
                "source_reliability": "medium",
                "headline": item.get("title") or "",
                "summary": item.get("text") or item.get("summary") or "",
                "url": item.get("url"),
                "published_at": item.get("publishedDate") or item.get("published_at"),
            }
        )
    return out


def _parse_rss_dt(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).isoformat()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _normalize_iso(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            dt = datetime.now(timezone.utc)
        else:
            if text.endswith("Z"):
                text = text.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _normalize_headline(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    return text


def _hash_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _dedupe_news(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        headline = _normalize_headline(item.get("headline"))
        if not headline:
            continue
        url = str(item.get("url") or "").strip() or None
        normalized.append(
            {
                "source": str(item.get("source") or "unknown").strip() or "unknown",
                "source_type": str(item.get("source_type") or "other").strip() or "other",
                "source_reliability": str(item.get("source_reliability") or "unknown").strip()
                or "unknown",
                "headline": headline,
                "summary": " ".join(str(item.get("summary") or "").strip().split()),
                "url": url,
                "published_at": _normalize_iso(item.get("published_at")),
            }
        )

    normalized.sort(key=lambda row: row["published_at"], reverse=True)

    out: list[dict[str, Any]] = []
    seen_url: set[str] = set()
    seen_title: set[str] = set()
    for row in normalized:
        url = row.get("url")
        title_key = _hash_text(row["headline"].lower())
        url_key = _hash_text(url.lower()) if isinstance(url, str) and url else None

        if url_key and url_key in seen_url:
            continue
        if title_key in seen_title:
            continue

        if url_key:
            seen_url.add(url_key)
        seen_title.add(title_key)
        out.append(row)

    return out


def _filter_recent_news(items: list[dict[str, Any]], max_age_hours: int) -> list[dict[str, Any]]:
    if max_age_hours <= 0:
        return items

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    filtered: list[dict[str, Any]] = []
    for item in items:
        published = item.get("published_at")
        dt = _to_datetime(published)
        if dt is None:
            continue
        if dt >= cutoff:
            filtered.append(item)
    return filtered


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fetch_live_news(settings: Settings) -> tuple[list[dict[str, Any]], str]:
    errors: list[str] = []
    combined: list[dict[str, Any]] = []
    source_labels: list[str] = []

    try:
        newsapi_items = _fetch_live_newsapi(settings)
        if newsapi_items:
            combined.extend(newsapi_items)
            source_labels.append("newsapi")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"newsapi: {exc}")

    try:
        sec_items = _fetch_live_sec_rss(settings)
        if sec_items:
            combined.extend(sec_items)
            source_labels.append("sec")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"sec: {exc}")

    try:
        fmp_items = _fetch_live_fmp_news(settings)
        if fmp_items:
            combined.extend(fmp_items)
            source_labels.append("fmp_news")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"fmp_news: {exc}")

    merged = _dedupe_news(combined)
    if merged:
        source = "live_" + "+".join(sorted(set(source_labels)))
        return merged, source

    detail = "; ".join(errors) if errors else "no live sources returned items"
    raise CollectorError(f"Live news fetch failed: {detail}")


def collect_news(settings: Settings, manual_path: str | None = None) -> tuple[list[dict[str, Any]], str]:
    """Collect current news inputs and return (items, source_label)."""
    if manual_path:
        manual_items = _load_news_file(manual_path)
        return _dedupe_news(manual_items), "manual"

    if settings.use_live_data:
        try:
            live_news, source = _fetch_live_news(settings)
            live_news = _filter_recent_news(live_news, settings.max_news_age_hours)
            if live_news:
                try:
                    _save_news_file(settings.latest_news_cache_file, live_news)
                except OSError:
                    pass
                return live_news, source
        except CollectorError:
            pass

        try:
            latest_cached_news = _load_news_file(settings.latest_news_cache_file)
            latest_cached_news = _dedupe_news(latest_cached_news)
            latest_cached_news = _filter_recent_news(
                latest_cached_news,
                settings.latest_available_max_news_age_hours,
            )
            if latest_cached_news:
                return latest_cached_news, "latest_available_cache"
        except CollectorError:
            pass

        if settings.strict_live_mode:
            raise CollectorError(
                "Strict live mode enabled: failed to fetch live news and no latest cache available"
            )

    return _dedupe_news(_load_news_file(settings.mock_news_file)), "mock"
