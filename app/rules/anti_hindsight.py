"""Rule helpers to detect hindsight framing and price-target style language."""

from __future__ import annotations

import re
from typing import Any

PRICE_TARGET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # English patterns.
    re.compile(r"\btarget\s+price\b", re.IGNORECASE),
    re.compile(r"\bwill\s+reach\b", re.IGNORECASE),
    re.compile(r"\bbreak\s+above\b", re.IGNORECASE),
    re.compile(r"\bfall\s+to\b", re.IGNORECASE),
    re.compile(r"\bhit\s+\$?\d{3,6}(?:\.\d+)?\b", re.IGNORECASE),
    re.compile(r"\bprice\s+target\b", re.IGNORECASE),
    re.compile(r"\bupside\s+target\b", re.IGNORECASE),
    re.compile(r"\bdownside\s+target\b", re.IGNORECASE),
    re.compile(r"\bprice\s+objective\b", re.IGNORECASE),
    # Chinese patterns.
    re.compile(r"目标价"),
    re.compile(r"(?:将到|会到|将会到|到达)\s*\d+(?:\.\d+)?(?:\s*(?:点|美元|元))?"),
    re.compile(r"突破\s*\d+(?:\.\d+)?"),
    re.compile(r"跌破\s*\d+(?:\.\d+)?"),
    re.compile(r"触及\s*\d+(?:\.\d+)?\s*(?:点|美元|元)?"),
    re.compile(r"(?:突破|跌破)\s*(?:关键位|关键点位|前高|前低)"),
)

HINDSIGHT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # English patterns.
    re.compile(r"\balready\s+rall(?:ied|y)\b", re.IGNORECASE),
    re.compile(r"\balready\s+fell\b", re.IGNORECASE),
    re.compile(r"\bas\s+we\s+saw\b", re.IGNORECASE),
    re.compile(r"\blooking\s+back\b", re.IGNORECASE),
    re.compile(r"\bin\s+hindsight\b", re.IGNORECASE),
    re.compile(r"\bafter\s+it\s+already\b", re.IGNORECASE),
    re.compile(r"\bbecause\s+it\s+already\s+(?:rose|fell|rallied|dropped)\b", re.IGNORECASE),
    # Chinese patterns.
    re.compile(r"回看|回头看|事后看|事后来看"),
    re.compile(r"已经(?:上涨|下跌|反弹|回落)"),
)


def _flatten_text(payload: Any) -> list[str]:
    if payload is None:
        return []
    if isinstance(payload, str):
        return [payload]
    if isinstance(payload, list):
        out: list[str] = []
        for item in payload:
            out.extend(_flatten_text(item))
        return out
    if isinstance(payload, dict):
        out: list[str] = []
        for item in payload.values():
            out.extend(_flatten_text(item))
        return out
    return []


def _collect_pattern_matches(texts: list[str], patterns: tuple[re.Pattern[str], ...]) -> list[str]:
    matches: list[str] = []
    for text in texts:
        for pattern in patterns:
            if pattern.search(text):
                matches.append(f"Pattern `{pattern.pattern}` matched text: {text}")
    return matches


def scan_text_for_violations(payload: dict[str, Any]) -> dict[str, list[str]]:
    """Return detected issues for banned price-target and hindsight language."""
    flattened = _flatten_text(payload)
    return {
        "price_target_issues": _collect_pattern_matches(flattened, PRICE_TARGET_PATTERNS),
        "hindsight_issues": _collect_pattern_matches(flattened, HINDSIGHT_PATTERNS),
    }
