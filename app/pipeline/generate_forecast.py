"""Forecast draft stage: convert mapped state into directional forecast draft."""

from __future__ import annotations

from pydantic import ValidationError

from app.exceptions import PipelineStepError
from app.llm_client import BaseLLMClient, LLMResponseError
from app.schemas import EventExtractionResult, ForecastDraft, NormalizedInputs, StateMappingResult


def run_forecast_generation(
    llm_client: BaseLLMClient,
    prompt_template: str,
    normalized_inputs: NormalizedInputs,
    structured_events: EventExtractionResult,
    state_mapping: StateMappingResult,
    output_language: str = "zh",
) -> ForecastDraft:
    """Execute forecast draft generation stage."""
    payload = {
        "normalized_inputs": normalized_inputs.model_dump(mode="json"),
        "structured_events": structured_events.model_dump(mode="json"),
        "state_mapping": state_mapping.model_dump(mode="json"),
        "output_language": output_language,
    }

    max_attempts = 3
    last_exc: Exception | None = None
    for _ in range(max_attempts):
        try:
            response = llm_client.generate_json("forecast_generation", prompt_template, payload)
            return ForecastDraft.model_validate(response)
        except (LLMResponseError, ValidationError, ValueError) as exc:
            last_exc = exc

    raise PipelineStepError(
        f"Forecast generation stage failed after {max_attempts} attempts: {last_exc}"
    ) from last_exc
