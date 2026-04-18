"""Pure anti-hindsight validators used by rules and review gating."""

from __future__ import annotations

import re
from typing import Any

from app.schemas import GovernanceIssue, IssueSeverity

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

# Only publishable forecast fields are hard/soft-scanned by default.
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
) -> dict[str, list[str]]:
    """Extract text per forecast field for contextual severity handling."""
    field_texts: dict[str, list[str]] = {}
    for field_name in fields:
        field_texts[field_name] = flatten_text(payload.get(field_name))
    return field_texts


def _field_severity(field_name: str) -> IssueSeverity:
    if field_name in {"final_thesis", "dominant_drivers"}:
        return IssueSeverity.HARD_FAIL
    if field_name in {"monitoring_list", "invalidation_conditions"}:
        return IssueSeverity.SOFT_WARN
    return IssueSeverity.SOFT_WARN


def _normalize_field_code(field_name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", field_name.upper())


def _collect_for_patterns(
    *,
    texts_by_field: dict[str, list[str]],
    patterns: tuple[re.Pattern[str], ...],
    code_prefix: str,
) -> list[GovernanceIssue]:
    findings: list[GovernanceIssue] = []
    for field_name, texts in texts_by_field.items():
        severity = _field_severity(field_name)
        for text in texts:
            for pattern in patterns:
                if pattern.search(text):
                    findings.append(
                        GovernanceIssue(
                            code=f"{code_prefix}_{_normalize_field_code(field_name)}",
                            field=field_name,
                            message=f"Pattern `{pattern.pattern}` matched text: {text}",
                            severity=severity,
                        )
                    )
    return findings


def _review_summary_findings(review_summary: str | None) -> list[GovernanceIssue]:
    if not isinstance(review_summary, str) or not review_summary.strip():
        return []
    summary = review_summary.strip()
    findings: list[GovernanceIssue] = []
    for pattern in BANNED_PRICE_TARGET_PATTERNS:
        if pattern.search(summary):
            findings.append(
                GovernanceIssue(
                    code="PRICE_TARGET_IN_REVIEW_SUMMARY",
                    field="review_summary",
                    message=f"Pattern `{pattern.pattern}` matched review_summary",
                    severity=IssueSeverity.SOFT_WARN,
                )
            )
    for pattern in BANNED_HINDSIGHT_PATTERNS:
        if pattern.search(summary):
            findings.append(
                GovernanceIssue(
                    code="HINDSIGHT_IN_REVIEW_SUMMARY",
                    field="review_summary",
                    message=f"Pattern `{pattern.pattern}` matched review_summary",
                    severity=IssueSeverity.SOFT_WARN,
                )
            )
    return findings


def _reference_level_findings(reference_levels: Any) -> list[GovernanceIssue]:
    texts = flatten_text(reference_levels)
    if not texts:
        return []

    findings: list[GovernanceIssue] = []
    for text in texts:
        for pattern in BANNED_PRICE_TARGET_PATTERNS:
            if pattern.search(text):
                findings.append(
                    GovernanceIssue(
                        code="REFERENCE_LEVEL_PRICE_LANGUAGE",
                        field="reference_levels",
                        message=f"Reference level includes threshold expression: {text}",
                        severity=IssueSeverity.INFO,
                    )
                )
                break
    return findings


def find_banned_phrase_issues(
    payload: dict[str, Any],
    *,
    review_summary: str | None = None,
    reference_levels: Any = None,
) -> dict[str, Any]:
    """Return severity-aware banned-phrase findings from scoped text fields."""
    scoped_texts = extract_scannable_forecast_text(payload)
    price_findings = _collect_for_patterns(
        texts_by_field=scoped_texts,
        patterns=BANNED_PRICE_TARGET_PATTERNS,
        code_prefix="PRICE_TARGET_IN",
    )
    hindsight_findings = _collect_for_patterns(
        texts_by_field=scoped_texts,
        patterns=BANNED_HINDSIGHT_PATTERNS,
        code_prefix="HINDSIGHT_IN",
    )
    summary_findings = _review_summary_findings(review_summary)
    reference_findings = _reference_level_findings(reference_levels)

    hard_fail_issues: list[GovernanceIssue] = []
    soft_warnings: list[GovernanceIssue] = []
    info_notes: list[GovernanceIssue] = []

    for item in [*price_findings, *hindsight_findings, *summary_findings, *reference_findings]:
        if item.severity == IssueSeverity.HARD_FAIL:
            hard_fail_issues.append(item)
        elif item.severity == IssueSeverity.SOFT_WARN:
            soft_warnings.append(item)
        else:
            info_notes.append(item)

    result = {
        "hard_fail_issues": hard_fail_issues,
        "soft_warnings": soft_warnings,
        "info_notes": info_notes,
    }
    # Backward-compatible aliases for older call sites/tests that read raw strings.
    result["price_target_issues"] = [
        item.message
        for item in [*hard_fail_issues, *soft_warnings, *info_notes]
        if "PRICE_TARGET" in item.code or "REFERENCE_LEVEL_PRICE" in item.code
    ]
    result["hindsight_issues"] = [
        item.message
        for item in [*hard_fail_issues, *soft_warnings, *info_notes]
        if "HINDSIGHT" in item.code
    ]
    return result


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
    findings = find_banned_phrase_issues(payload)
    return {
        "price_target_issues": [
            item.message
            for item in [*findings["hard_fail_issues"], *findings["soft_warnings"], *findings["info_notes"]]
            if item.code.startswith("PRICE_TARGET")
        ],
        "hindsight_issues": [
            item.message
            for item in [*findings["hard_fail_issues"], *findings["soft_warnings"], *findings["info_notes"]]
            if item.code.startswith("HINDSIGHT")
        ],
    }
