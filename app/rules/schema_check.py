"""Schema and governance checks independent from LLM generation."""

from __future__ import annotations

import re
from typing import Any

from app.exceptions import RuleViolationError
from app.rules.anti_hindsight import find_banned_phrase_issues, is_valid_review_status
from app.schemas import ForecastDraft, FinalForecast, RuleCheckItem, RuleCheckReport, RuleSeverity

_REQUIRED_TEXT_LIST_FIELDS: tuple[str, ...] = (
    "dominant_drivers",
    "upside_triggers",
    "downside_triggers",
    "invalidation_conditions",
    "monitoring_list",
)

_HORIZON_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b\d+\s*(?:trading\s+days?|business\s+days?|days?|weeks?|months?|quarters?)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d+\s*(?:d|w|wk|wks|mo|mth|q)\b", re.IGNORECASE),
    re.compile(r"\d+\s*(?:天|周|月|个?交易日)"),
)


def _payload_from_forecast(forecast: ForecastDraft | FinalForecast | dict[str, Any]) -> dict[str, Any]:
    return forecast if isinstance(forecast, dict) else forecast.model_dump(mode="json")


def _text_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, str) and item.strip()]


def _has_parseable_horizon(horizon: str) -> bool:
    return any(pattern.search(horizon) for pattern in _HORIZON_PATTERNS)


def build_rule_report(
    forecast: ForecastDraft | FinalForecast | dict[str, Any],
    *,
    require_review_status: bool = False,
) -> RuleCheckReport:
    """Build a structured rule report without raising exceptions."""
    payload = _payload_from_forecast(forecast)
    issues: list[RuleCheckItem] = []
    warnings: list[RuleCheckItem] = []
    coverage: dict[str, bool] = {
        "required_lists_checked": True,
        "invalidation_present_checked": True,
        "horizon_declared_checked": True,
        "horizon_parseable_checked": True,
        "evidence_symmetry_checked": True,
        "price_target_scan_checked": True,
        "hindsight_scan_checked": True,
        "final_thesis_length_checked": True,
        "review_status_checked": require_review_status,
    }

    for field_name in _REQUIRED_TEXT_LIST_FIELDS:
        cleaned = _text_list(payload.get(field_name))
        if not cleaned:
            issues.append(
                RuleCheckItem(
                    code=f"MISSING_{field_name.upper()}",
                    message=f"`{field_name}` must contain at least one non-empty item",
                    severity=RuleSeverity.BLOCKING,
                )
            )

    forecast_horizon = payload.get("forecast_horizon")
    if not isinstance(forecast_horizon, str) or not forecast_horizon.strip():
        issues.append(
            RuleCheckItem(
                code="FORECAST_HORIZON_MISSING",
                message="`forecast_horizon` must be a non-empty string",
                severity=RuleSeverity.BLOCKING,
            )
        )
    elif not _has_parseable_horizon(forecast_horizon):
        issues.append(
            RuleCheckItem(
                code="FORECAST_HORIZON_UNPARSEABLE",
                message="`forecast_horizon` must contain parseable duration units",
                severity=RuleSeverity.BLOCKING,
            )
        )

    supportive = _text_list(payload.get("supportive_evidence"))
    opposing = _text_list(payload.get("opposing_evidence"))
    if not supportive:
        issues.append(
            RuleCheckItem(
                code="SUPPORTIVE_EVIDENCE_MISSING",
                message="supportive_evidence is required",
                severity=RuleSeverity.BLOCKING,
            )
        )
    if not opposing:
        issues.append(
            RuleCheckItem(
                code="OPPOSING_EVIDENCE_MISSING",
                message="opposing_evidence is required",
                severity=RuleSeverity.BLOCKING,
            )
        )

    final_thesis = payload.get("final_thesis")
    if not isinstance(final_thesis, str) or len(final_thesis.strip()) < 20:
        issues.append(
            RuleCheckItem(
                code="FINAL_THESIS_TOO_SHORT",
                message="`final_thesis` must be at least 20 non-whitespace characters",
                severity=RuleSeverity.BLOCKING,
            )
        )

    if require_review_status:
        review_status = payload.get("anti_hindsight_status")
        if not is_valid_review_status(review_status):
            issues.append(
                RuleCheckItem(
                    code="REVIEW_STATUS_INVALID",
                    message="`anti_hindsight_status` must be explicitly set to PASS or FAIL",
                    severity=RuleSeverity.BLOCKING,
                )
            )

    violations = find_banned_phrase_issues(payload)
    for idx, detail in enumerate(violations["price_target_issues"], start=1):
        issues.append(
            RuleCheckItem(
                code=f"PRICE_TARGET_LANGUAGE_{idx}",
                message=detail,
                severity=RuleSeverity.BLOCKING,
            )
        )

    for idx, detail in enumerate(violations["hindsight_issues"], start=1):
        issues.append(
            RuleCheckItem(
                code=f"HINDSIGHT_LANGUAGE_{idx}",
                message=detail,
                severity=RuleSeverity.BLOCKING,
            )
        )

    if len(supportive) and len(opposing):
        ratio = max(len(supportive), len(opposing)) / min(len(supportive), len(opposing))
        if ratio >= 4:
            warnings.append(
                RuleCheckItem(
                    code="EVIDENCE_IMBALANCED",
                    message="Supportive vs opposing evidence appears heavily imbalanced",
                    severity=RuleSeverity.WARNING,
                )
            )

    return RuleCheckReport(
        has_blocking_issues=bool(issues),
        issues=issues,
        warnings=warnings,
        coverage=coverage,
    )


def validate_forecast_rules(
    forecast: ForecastDraft | FinalForecast | dict[str, Any],
    *,
    require_review_status: bool = False,
) -> None:
    """Validate a forecast payload and raise on blocking issues."""
    report = build_rule_report(forecast, require_review_status=require_review_status)
    if not report.has_blocking_issues:
        return

    details = " | ".join(f"[{item.code}] {item.message}" for item in report.issues)
    raise RuleViolationError(f"Forecast failed rule checks: {details}")
