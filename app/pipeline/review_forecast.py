"""Anti-hindsight review stage: review draft and return reviewed forecast artifact."""

from __future__ import annotations

import re

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
        "output_language": output_language,
    }

    max_attempts = 3
    last_exc: Exception | None = None
    for _ in range(max_attempts):
        try:
            response = llm_client.generate_json("anti_hindsight_review", prompt_template, payload)
            normalized_response = _normalize_review_response(response)
            review_result = AntiHindsightReviewResult.model_validate(normalized_response)

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
