"""Anti-hindsight review stage: review draft and return reviewed forecast artifact."""

from __future__ import annotations

import re

from pydantic import ValidationError

from app.exceptions import PipelineStepError
from app.llm_client import BaseLLMClient, LLMResponseError
from app.rules.anti_hindsight import validate_review_status_pair
from app.schemas import (
    AntiHindsightReviewResult,
    ForecastDraft,
    NormalizedInputs,
    RuleCheckReport,
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
            review_result = AntiHindsightReviewResult.model_validate(response)

            status_issues = validate_review_status_pair(
                review_result.anti_hindsight_status.value,
                review_result.reviewed_forecast.anti_hindsight_status.value,
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
