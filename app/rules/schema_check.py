"""Schema and governance checks independent from LLM generation."""

from __future__ import annotations

import re
from typing import Any

from app.exceptions import RuleViolationError
from app.rules.anti_hindsight import find_banned_phrase_issues, is_valid_review_status
from app.schemas import (
    ForecastDraft,
    FinalForecast,
    GovernanceIssue,
    IssueSeverity,
    ReferenceLevels,
    RuleCheckReport,
)

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


def _hard_issue(code: str, field: str, message: str) -> GovernanceIssue:
    return GovernanceIssue(code=code, field=field, message=message, severity=IssueSeverity.HARD_FAIL)


def _soft_issue(code: str, field: str, message: str) -> GovernanceIssue:
    return GovernanceIssue(code=code, field=field, message=message, severity=IssueSeverity.SOFT_WARN)


def _info_issue(code: str, field: str, message: str) -> GovernanceIssue:
    return GovernanceIssue(code=code, field=field, message=message, severity=IssueSeverity.INFO)


def build_rule_report(
    forecast: ForecastDraft | FinalForecast | dict[str, Any],
    *,
    require_review_status: bool = False,
    review_summary: str | None = None,
    reference_levels: ReferenceLevels | dict[str, Any] | None = None,
) -> RuleCheckReport:
    """Build a structured governance report without raising exceptions."""
    payload = _payload_from_forecast(forecast)
    hard_fail_issues: list[GovernanceIssue] = []
    soft_warnings: list[GovernanceIssue] = []
    info_notes: list[GovernanceIssue] = []
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
        "review_summary_scanned": isinstance(review_summary, str),
        "reference_levels_scanned": reference_levels is not None,
    }

    for field_name in _REQUIRED_TEXT_LIST_FIELDS:
        cleaned = _text_list(payload.get(field_name))
        if not cleaned:
            hard_fail_issues.append(
                _hard_issue(
                    code=f"MISSING_{field_name.upper()}",
                    field=field_name,
                    message=f"`{field_name}` must contain at least one non-empty item",
                )
            )

    forecast_horizon = payload.get("forecast_horizon")
    if not isinstance(forecast_horizon, str) or not forecast_horizon.strip():
        hard_fail_issues.append(
            _hard_issue(
                code="FORECAST_HORIZON_MISSING",
                field="forecast_horizon",
                message="`forecast_horizon` must be a non-empty string",
            )
        )
    elif not _has_parseable_horizon(forecast_horizon):
        hard_fail_issues.append(
            _hard_issue(
                code="FORECAST_HORIZON_UNPARSEABLE",
                field="forecast_horizon",
                message="`forecast_horizon` must contain parseable duration units",
            )
        )

    supportive = _text_list(payload.get("supportive_evidence"))
    opposing = _text_list(payload.get("opposing_evidence"))
    if not supportive:
        hard_fail_issues.append(
            _hard_issue(
                code="SUPPORTIVE_EVIDENCE_MISSING",
                field="supportive_evidence",
                message="supportive_evidence is required",
            )
        )
    if not opposing:
        hard_fail_issues.append(
            _hard_issue(
                code="OPPOSING_EVIDENCE_MISSING",
                field="opposing_evidence",
                message="opposing_evidence is required",
            )
        )

    final_thesis = payload.get("final_thesis")
    if not isinstance(final_thesis, str) or len(final_thesis.strip()) < 20:
        hard_fail_issues.append(
            _hard_issue(
                code="FINAL_THESIS_TOO_SHORT",
                field="final_thesis",
                message="`final_thesis` must be at least 20 non-whitespace characters",
            )
        )

    if require_review_status:
        review_status = payload.get("review_status")
        legacy_status = payload.get("anti_hindsight_status")
        effective_status = review_status if review_status is not None else legacy_status
        if not is_valid_review_status(effective_status):
            hard_fail_issues.append(
                _hard_issue(
                    code="REVIEW_STATUS_INVALID",
                    field="review_status",
                    message="`review_status` must be explicitly set to PASS or FAIL",
                )
            )
        if legacy_status is not None and not is_valid_review_status(legacy_status):
            hard_fail_issues.append(
                _hard_issue(
                    code="REVIEW_STATUS_INVALID",
                    field="anti_hindsight_status",
                    message="`anti_hindsight_status` must be PASS or FAIL when provided",
                )
            )
        if review_status is not None and legacy_status is not None:
            normalized_review = str(review_status).upper()
            normalized_legacy = str(legacy_status).upper()
            if (
                is_valid_review_status(normalized_review)
                and is_valid_review_status(normalized_legacy)
                and normalized_review != normalized_legacy
            ):
                hard_fail_issues.append(
                    _hard_issue(
                        code="REVIEW_STATUS_MISMATCH",
                        field="review_status/anti_hindsight_status",
                        message="`review_status` and `anti_hindsight_status` must match when both exist",
                    )
                )

    if len(supportive) and len(opposing):
        ratio = max(len(supportive), len(opposing)) / min(len(supportive), len(opposing))
        if ratio >= 4:
            soft_warnings.append(
                _soft_issue(
                    code="EVIDENCE_IMBALANCED",
                    field="supportive_evidence/opposing_evidence",
                    message="Supportive vs opposing evidence appears heavily imbalanced",
                )
            )

    if reference_levels is not None:
        info_notes.append(
            _info_issue(
                code="REFERENCE_LEVELS_PRESENT",
                field="reference_levels",
                message="Structured reference levels are present as appendix context.",
            )
        )

    findings = find_banned_phrase_issues(
        payload,
        review_summary=review_summary,
        reference_levels=(
            reference_levels.model_dump(mode="json")
            if isinstance(reference_levels, ReferenceLevels)
            else reference_levels
        ),
    )
    hard_fail_issues.extend(findings["hard_fail_issues"])
    soft_warnings.extend(findings["soft_warnings"])
    info_notes.extend(findings["info_notes"])

    return RuleCheckReport(
        has_hard_fail=bool(hard_fail_issues),
        has_soft_warn=bool(soft_warnings),
        hard_fail_issues=hard_fail_issues,
        soft_warnings=soft_warnings,
        info_notes=info_notes,
        coverage=coverage,
    )


def validate_forecast_rules(
    forecast: ForecastDraft | FinalForecast | dict[str, Any],
    *,
    require_review_status: bool = False,
    review_summary: str | None = None,
    reference_levels: ReferenceLevels | dict[str, Any] | None = None,
) -> None:
    """Validate a forecast payload and raise only on hard-fail issues."""
    report = build_rule_report(
        forecast,
        require_review_status=require_review_status,
        review_summary=review_summary,
        reference_levels=reference_levels,
    )
    if not report.has_hard_fail:
        return

    details = " | ".join(f"[{item.code}] {item.message}" for item in report.hard_fail_issues)
    raise RuleViolationError(f"Forecast failed hard governance checks: {details}")
