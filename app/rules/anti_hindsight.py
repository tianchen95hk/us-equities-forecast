"""Pure anti-hindsight validators used by rules and review gating."""

from __future__ import annotations

import re
from typing import Any

BANNED_PRICE_TARGET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\btarget\s+price\b", re.IGNORECASE),
    re.compile(r"\bwill\s+reach\b", re.IGNORECASE),
    re.compile(r"\bbreak\s+above\b", re.IGNORECASE),
    re.compile(r"\bfall\s+to\b", re.IGNORECASE),
    re.compile(r"\bhit\s+\$?\d{3,6}(?:\.\d+)?\b", re.IGNORECASE),
    re.compile(r"\bprice\s+target\b", re.IGNORECASE),
    re.compile(r"\bupside\s+target\b", re.IGNORECASE),
    re.compile(r"\bdownside\s+target\b", re.IGNORECASE),
    re.compile(r"\bprice\s+objective\b", re.IGNORECASE),
    re.compile(r"目标价"),
    re.compile(r"(?:将到|会到|将会到|到达)\s*\d+(?:\.\d+)?(?:\s*(?:点|美元|元))?"),
    re.compile(r"突破\s*\d+(?:\.\d+)?"),
    re.compile(r"跌破\s*\d+(?:\.\d+)?"),
    re.compile(r"触及\s*\d+(?:\.\d+)?\s*(?:点|美元|元)?"),
    re.compile(r"(?:突破|跌破)\s*(?:关键位|关键点位|前高|前低)"),
)

BANNED_HINDSIGHT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\balready\s+rall(?:ied|y)\b", re.IGNORECASE),
    re.compile(r"\balready\s+fell\b", re.IGNORECASE),
    re.compile(r"\bas\s+we\s+saw\b", re.IGNORECASE),
    re.compile(r"\blooking\s+back\b", re.IGNORECASE),
    re.compile(r"\bin\s+hindsight\b", re.IGNORECASE),
    re.compile(r"\bafter\s+it\s+already\b", re.IGNORECASE),
    re.compile(r"\bbecause\s+it\s+already\s+(?:rose|fell|rallied|dropped)\b", re.IGNORECASE),
    re.compile(r"回看|回头看|事后看|事后来看"),
    re.compile(r"已经(?:上涨|下跌|反弹|回落)"),
)

# Scan only publishable forecast fields to avoid false positives from
# artifact paths, raw attachments, or intermediate logs.
SCANNED_FORECAST_FIELDS: tuple[str, ...] = (
    "forecast_horizon",
    "dominant_drivers",
    "supportive_evidence",
    "opposing_evidence",
    "upside_triggers",
    "downside_triggers",
    "invalidation_conditions",
    "monitoring_list",
    "final_thesis",
)

VALID_REVIEW_STATUSES = {"PASS", "FAIL"}


def flatten_text(value: Any) -> list[str]:
    """Recursively extract non-empty text values from nested structures."""
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(flatten_text(item))
        return out
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(flatten_text(item))
        return out
    return []


def extract_scannable_forecast_text(
    payload: dict[str, Any],
    fields: tuple[str, ...] = SCANNED_FORECAST_FIELDS,
) -> list[str]:
    """Extract text only from publishable forecast fields."""
    texts: list[str] = []
    for field_name in fields:
        texts.extend(flatten_text(payload.get(field_name)))
    return texts


def collect_pattern_matches(
    texts: list[str],
    patterns: tuple[re.Pattern[str], ...],
) -> list[str]:
    """Return deterministic pattern-match diagnostics."""
    matches: list[str] = []
    for text in texts:
        for pattern in patterns:
            if pattern.search(text):
                matches.append(f"Pattern `{pattern.pattern}` matched text: {text}")
    return matches


def find_banned_phrase_issues(payload: dict[str, Any]) -> dict[str, list[str]]:
    """Return banned price-target / hindsight issues from forecast fields only."""
    texts = extract_scannable_forecast_text(payload)
    return {
        "price_target_issues": collect_pattern_matches(texts, BANNED_PRICE_TARGET_PATTERNS),
        "hindsight_issues": collect_pattern_matches(texts, BANNED_HINDSIGHT_PATTERNS),
    }


def is_valid_review_status(value: Any) -> bool:
    """Return True when status is PASS or FAIL."""
    return isinstance(value, str) and value.upper() in VALID_REVIEW_STATUSES


def validate_review_status_pair(review_status: Any, forecast_status: Any) -> list[str]:
    """Validate top-level review status vs reviewed_forecast status."""
    issues: list[str] = []
    normalized_review = str(review_status).upper() if isinstance(review_status, str) else review_status
    normalized_forecast = str(forecast_status).upper() if isinstance(forecast_status, str) else forecast_status

    if not is_valid_review_status(normalized_review):
        issues.append("REVIEW_STATUS_INVALID: top-level anti_hindsight_status must be PASS or FAIL")
    if not is_valid_review_status(normalized_forecast):
        issues.append(
            "FORECAST_REVIEW_STATUS_INVALID: reviewed_forecast.anti_hindsight_status must be PASS or FAIL"
        )
    if not issues and normalized_review != normalized_forecast:
        issues.append(
            "REVIEW_STATUS_MISMATCH: top-level review status must match reviewed_forecast.anti_hindsight_status"
        )
    return issues


def scan_text_for_violations(payload: dict[str, Any]) -> dict[str, list[str]]:
    """Backward-compatible alias for older call sites."""
    return find_banned_phrase_issues(payload)
