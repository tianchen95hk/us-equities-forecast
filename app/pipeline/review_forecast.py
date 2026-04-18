"""Anti-hindsight review stage: review draft and return reviewed forecast artifact."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from pydantic import ValidationError

from app.exceptions import PipelineStepError
from app.llm_client import BaseLLMClient, LLMResponseError
from app.rules.anti_hindsight import validate_review_status_pair
from app.schemas import (
    AntiHindsightStatus,
    AntiHindsightReviewResult,
    GovernanceIssue,
    IssueSeverity,
    ForecastDraft,
    NormalizedInputs,
    RuleCheckReport,
    ReviewDecision,
    ReviewFindings,
    ReferenceLevels,
    StateMappingResult,
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_COVERAGE_FALSE_POSITIVE_PATTERNS: tuple[str, ...] = (
    "review_status_checked=false",
    "reference_levels_scanned=false",
    "review_status_checked",
    "reference_levels_scanned",
    "missing reference_levels",
    "reference_levels",
    "review_status not marked",
    "缺少reference_levels",
    "缺少 reference_levels",
    "review_status未标记",
    "review_status 未标记",
)
_CONDITIONAL_MARKERS: tuple[str, ...] = (
    "if",
    "when",
    "unless",
    "若",
    "如果",
    "一旦",
    "当",
    "除非",
    "可能",
    "触发",
)
_LEVEL_MARKERS: tuple[str, ...] = (
    "break above",
    "fall to",
    "突破",
    "跌破",
    "触及",
)
_PRICE_LEVEL_FAIL_HINTS: tuple[str, ...] = (
    "price_target",
    "price target",
    "price level",
    "突破",
    "跌破",
    "触及",
)


def run_anti_hindsight_review(
    llm_client: BaseLLMClient,
    prompt_template: str,
    normalized_inputs: NormalizedInputs,
    state_mapping: StateMappingResult,
    forecast_draft: ForecastDraft,
    draft_rule_report: RuleCheckReport,
    output_language: str = "zh",
    normalized_inputs_payload: dict[str, object] | None = None,
) -> AntiHindsightReviewResult:
    """Execute anti-hindsight review and return review artifact only."""
    payload = {
        "normalized_inputs": (
            normalized_inputs_payload
            if normalized_inputs_payload is not None
            else normalized_inputs.model_dump(mode="json")
        ),
        "state_mapping": state_mapping.model_dump(mode="json"),
        "forecast_draft": forecast_draft.model_dump(mode="json"),
        "rule_report": draft_rule_report.model_dump(mode="json"),
        "rule_report_context": {
            "stage": "draft_pre_review",
            "guidance": (
                "coverage.review_status_checked/reference_levels_scanned can be false at draft stage. "
                "Do not mark FAIL solely because these draft-stage coverage flags are false."
            ),
            "coverage": draft_rule_report.coverage,
        },
        "output_language": output_language,
    }

    max_attempts = 3
    last_exc: Exception | None = None
    for _ in range(max_attempts):
        try:
            response = llm_client.generate_json("anti_hindsight_review", prompt_template, payload)
            normalized_response = _normalize_review_response(response)
            review_result = AntiHindsightReviewResult.model_validate(normalized_response)
            review_result = _normalize_coverage_false_positive_fail(
                review_result=review_result,
                draft_rule_report=draft_rule_report,
                output_language=output_language,
            )
            review_result = _normalize_conditional_price_level_fail(
                review_result=review_result,
                output_language=output_language,
            )

            status_issues = validate_review_status_pair(
                review_result.review_decision.review_status.value,
                review_result.reviewed_forecast.review_status.value,
            )
            if status_issues:
                raise ValueError(" | ".join(status_issues))

            if output_language.lower() == "zh":
                if not _contains_cjk(review_result.reviewed_forecast.final_thesis):
                    raise ValueError("reviewed_forecast.final_thesis is not in Chinese")
            return review_result
        except (LLMResponseError, ValidationError, ValueError) as exc:
            last_exc = exc

    raise PipelineStepError(
        f"Anti-hindsight review stage failed after {max_attempts} attempts: {last_exc}"
    ) from last_exc


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def _normalize_review_response(response: dict[str, object]) -> dict[str, object]:
    if not isinstance(response, dict):
        return response

    normalized = dict(response)
    reviewed_forecast = normalized.get("reviewed_forecast")
    if isinstance(reviewed_forecast, dict):
        reviewed = dict(reviewed_forecast)
    else:
        reviewed = {}

    reference_levels_raw = normalized.get("reference_levels")
    if isinstance(reference_levels_raw, dict):
        reference_levels = reference_levels_raw
    elif isinstance(reviewed.get("reference_levels"), dict):
        reference_levels = reviewed.get("reference_levels", {})
    else:
        reference_levels = ReferenceLevels().model_dump(mode="json")

    reviewed.setdefault("reference_levels", reference_levels)

    if "review_status" not in reviewed and "anti_hindsight_status" in reviewed:
        reviewed["review_status"] = reviewed.get("anti_hindsight_status")
    if "anti_hindsight_status" not in reviewed and "review_status" in reviewed:
        reviewed["anti_hindsight_status"] = reviewed.get("review_status")

    if "review_decision" not in normalized:
        status_value = normalized.get("anti_hindsight_status", reviewed.get("review_status", "FAIL"))
        status_text = str(status_value).upper() if isinstance(status_value, str) else "FAIL"
        status = AntiHindsightStatus.PASS if status_text == "PASS" else AntiHindsightStatus.FAIL
        issue_strings = [item for item in normalized.get("issues", []) if isinstance(item, str)]
        hard_fail_issues = [
            GovernanceIssue(
                code=f"LEGACY_REVIEW_ISSUE_{index}",
                field="review_summary",
                message=item,
                severity=IssueSeverity.HARD_FAIL if status == AntiHindsightStatus.FAIL else IssueSeverity.SOFT_WARN,
            ).model_dump(mode="json")
            for index, item in enumerate(issue_strings, start=1)
        ]
        review_summary = normalized.get("review_summary", "")
        review_decision = ReviewDecision(
            review_status=status,
            is_publishable=status == AntiHindsightStatus.PASS,
            decision_summary=(
                str(review_summary).strip()
                if isinstance(review_summary, str) and review_summary.strip()
                else "Review decision synthesized from legacy fields."
            ),
            hard_fail_count=(len(hard_fail_issues) if status == AntiHindsightStatus.FAIL else 0),
            soft_warn_count=(0 if status == AntiHindsightStatus.FAIL else len(hard_fail_issues)),
        ).model_dump(mode="json")
        review_findings = ReviewFindings(
            hard_fail_issues=(
                [GovernanceIssue.model_validate(item) for item in hard_fail_issues]
                if status == AntiHindsightStatus.FAIL
                else []
            ),
            soft_warnings=(
                [GovernanceIssue.model_validate(item) for item in hard_fail_issues]
                if status == AntiHindsightStatus.PASS
                else []
            ),
            info_notes=[],
        ).model_dump(mode="json")
        normalized["review_decision"] = review_decision
        normalized["review_findings"] = review_findings

    normalized["reviewed_forecast"] = reviewed
    normalized["reference_levels"] = reference_levels
    return normalized


def _contains_coverage_false_positive_text(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in _COVERAGE_FALSE_POSITIVE_PATTERNS)


def _is_conditional_level_risk_text(text: str) -> bool:
    lowered = text.lower()
    has_level = any(marker in lowered for marker in _LEVEL_MARKERS)
    has_conditional = any(marker in lowered for marker in _CONDITIONAL_MARKERS)
    return has_level and has_conditional


def _looks_like_price_level_fail_text(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _PRICE_LEVEL_FAIL_HINTS)


def _is_price_level_issue(issue: GovernanceIssue) -> bool:
    blob = f"{issue.code} {issue.field} {issue.message}"
    return _looks_like_price_level_fail_text(blob)


def _normalize_conditional_price_level_fail(
    *,
    review_result: AntiHindsightReviewResult,
    output_language: str,
) -> AntiHindsightReviewResult:
    """
    Downgrade FAIL caused only by conditional level language in thesis.

    Example: "若10Y突破100可能触发估值调整" should be warning-level risk framing,
    not a hard publish block.
    """
    if review_result.review_decision.review_status != AntiHindsightStatus.FAIL:
        return review_result

    thesis = review_result.reviewed_forecast.final_thesis
    if not _is_conditional_level_risk_text(thesis):
        return review_result

    hard_fail_issues = review_result.review_findings.hard_fail_issues
    decision_blob = f"{review_result.review_decision.decision_summary} {review_result.review_summary}"
    if hard_fail_issues:
        if not all(_is_price_level_issue(item) for item in hard_fail_issues):
            return review_result
    elif not _looks_like_price_level_fail_text(decision_blob):
        return review_result

    payload = review_result.model_dump(mode="json")
    reviewed_forecast = dict(payload.get("reviewed_forecast", {}))
    reviewed_forecast["review_status"] = AntiHindsightStatus.PASS.value
    reviewed_forecast["anti_hindsight_status"] = AntiHindsightStatus.PASS.value

    existing_soft = payload.get("review_findings", {}).get("soft_warnings", [])
    converted_soft = [
        GovernanceIssue(
            code=item.code,
            field=item.field,
            message=f"[downgraded_from_hard_fail] {item.message}",
            severity=IssueSeverity.SOFT_WARN,
        ).model_dump(mode="json")
        for item in hard_fail_issues
    ]
    converted_soft.append(
        GovernanceIssue(
            code="CONDITIONAL_LEVEL_LANGUAGE_WARN",
            field="final_thesis",
            message="Conditional level language in final_thesis treated as warning, not hard fail.",
            severity=IssueSeverity.SOFT_WARN,
        ).model_dump(mode="json")
    )

    info_notes = payload.get("review_findings", {}).get("info_notes", [])
    info_notes.append(
        GovernanceIssue(
            code="CONDITIONAL_LEVEL_CONTEXT_NOTE",
            field="final_thesis",
            message=(
                "Numeric level appears in conditional risk framing; allowed as warning if not core "
                "price-target thesis."
            ),
            severity=IssueSeverity.INFO,
        ).model_dump(mode="json")
    )

    if output_language.lower() == "zh":
        decision_summary = "已修正条件性价格位误判：该表述属于风险触发提示，降级为警告。"
        review_summary = (
            f"{review_result.review_summary} "
            "（系统纠偏：`final_thesis`中的条件性关口描述视为风险提示，不作为硬失败。）"
        ).strip()
    else:
        decision_summary = (
            "Corrected conditional price-level false-positive: risk-trigger phrasing was downgraded "
            "to warning."
        )
        review_summary = (
            f"{review_result.review_summary} "
            "(System correction: conditional threshold language in final_thesis is warning-level.)"
        ).strip()

    payload["review_decision"] = ReviewDecision(
        review_status=AntiHindsightStatus.PASS,
        is_publishable=True,
        decision_summary=decision_summary,
        hard_fail_count=0,
        soft_warn_count=len(existing_soft) + len(converted_soft),
    ).model_dump(mode="json")
    payload["review_findings"] = ReviewFindings(
        hard_fail_issues=[],
        soft_warnings=[
            GovernanceIssue.model_validate(item)
            for item in [*existing_soft, *converted_soft]
            if isinstance(item, dict)
        ],
        info_notes=[
            GovernanceIssue.model_validate(item)
            for item in info_notes
            if isinstance(item, dict)
        ],
    ).model_dump(mode="json")
    payload["review_summary"] = review_summary
    payload["reviewed_at"] = payload.get("reviewed_at") or datetime.now(timezone.utc).isoformat()
    payload["reviewed_forecast"] = reviewed_forecast
    return AntiHindsightReviewResult.model_validate(payload)


def _normalize_coverage_false_positive_fail(
    *,
    review_result: AntiHindsightReviewResult,
    draft_rule_report: RuleCheckReport,
    output_language: str,
) -> AntiHindsightReviewResult:
    """Downgrade known draft-coverage false positives from hard-fail to soft warnings."""
    if review_result.review_decision.review_status != AntiHindsightStatus.FAIL:
        return review_result

    coverage = draft_rule_report.coverage or {}
    draft_has_expected_false = (
        coverage.get("review_status_checked") is False
        and coverage.get("reference_levels_scanned") is False
    )
    if not draft_has_expected_false:
        return review_result

    decision_text = f"{review_result.review_decision.decision_summary} {review_result.review_summary}"
    if not _contains_coverage_false_positive_text(decision_text):
        return review_result

    hard_fail_issues = review_result.review_findings.hard_fail_issues
    if hard_fail_issues and not all(
        _contains_coverage_false_positive_text(f"{item.code} {item.field} {item.message}")
        for item in hard_fail_issues
    ):
        return review_result

    payload = review_result.model_dump(mode="json")
    reviewed_forecast = dict(payload.get("reviewed_forecast", {}))
    reviewed_forecast["review_status"] = AntiHindsightStatus.PASS.value
    reviewed_forecast["anti_hindsight_status"] = AntiHindsightStatus.PASS.value

    existing_soft = payload.get("review_findings", {}).get("soft_warnings", [])
    converted_soft = [
        GovernanceIssue(
            code=item.code,
            field=item.field,
            message=f"[downgraded_from_hard_fail] {item.message}",
            severity=IssueSeverity.SOFT_WARN,
        ).model_dump(mode="json")
        for item in hard_fail_issues
    ]
    converted_soft.append(
        GovernanceIssue(
            code="DRAFT_COVERAGE_FALSE_POSITIVE_FILTERED",
            field="rule_report.coverage",
            message=(
                "Draft-stage coverage flags were interpreted as publish-stage hard fail; "
                "downgraded to warning."
            ),
            severity=IssueSeverity.SOFT_WARN,
        ).model_dump(mode="json")
    )

    info_notes = payload.get("review_findings", {}).get("info_notes", [])
    info_notes.append(
        GovernanceIssue(
            code="DRAFT_STAGE_COVERAGE_NOTE",
            field="rule_report.coverage",
            message=(
                "review_status_checked/reference_levels_scanned false is expected before review stage."
            ),
            severity=IssueSeverity.INFO,
        ).model_dump(mode="json")
    )

    if output_language.lower() == "zh":
        decision_summary = "已修正 draft 覆盖标记误判：仅因 draft coverage=false 的 FAIL 被降级为警告。"
        review_summary = (
            f"{review_result.review_summary} "
            "（系统纠偏：draft阶段 coverage 标记为 false 属正常，不应作为硬失败发布门禁。）"
        ).strip()
    else:
        decision_summary = (
            "Corrected draft-coverage false-positive: FAIL based solely on draft coverage=false "
            "is downgraded to warning."
        )
        review_summary = (
            f"{review_result.review_summary} "
            "(System correction: draft-stage coverage=false is expected and not a hard publish gate fail.)"
        ).strip()

    payload["review_decision"] = ReviewDecision(
        review_status=AntiHindsightStatus.PASS,
        is_publishable=True,
        decision_summary=decision_summary,
        hard_fail_count=0,
        soft_warn_count=len(existing_soft) + len(converted_soft),
    ).model_dump(mode="json")
    payload["review_findings"] = ReviewFindings(
        hard_fail_issues=[],
        soft_warnings=[
            GovernanceIssue.model_validate(item)
            for item in [*existing_soft, *converted_soft]
            if isinstance(item, dict)
        ],
        info_notes=[
            GovernanceIssue.model_validate(item)
            for item in info_notes
            if isinstance(item, dict)
        ],
    ).model_dump(mode="json")
    payload["review_summary"] = review_summary
    payload["reviewed_at"] = payload.get("reviewed_at") or datetime.now(timezone.utc).isoformat()
    payload["reviewed_forecast"] = reviewed_forecast
    return AntiHindsightReviewResult.model_validate(payload)
