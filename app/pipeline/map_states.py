"""Combined state-mapping + forecast-draft stage (single LLM call)."""

from __future__ import annotations

from pydantic import ValidationError

from app.exceptions import PipelineStepError
from app.llm_client import BaseLLMClient, LLMResponseError
from app.schemas import EventExtractionResult, NormalizedInputs, StateAndForecastResult


def run_state_and_forecast(
    llm_client: BaseLLMClient,
    prompt_template: str,
    normalized_inputs: NormalizedInputs,
    structured_events: EventExtractionResult,
    output_language: str = "zh",
) -> StateAndForecastResult:
    """Execute combined stage mapping state and producing forecast draft."""
    payload = {
        "normalized_inputs": normalized_inputs.model_dump(mode="json"),
        "structured_events": structured_events.model_dump(mode="json"),
        "output_language": output_language,
    }

    max_attempts = 3
    last_exc: Exception | None = None
    for _ in range(max_attempts):
        try:
            response = llm_client.generate_json("state_and_forecast", prompt_template, payload)
            return StateAndForecastResult.model_validate(response)
        except (LLMResponseError, ValidationError, ValueError) as exc:
            last_exc = exc

    raise PipelineStepError(
        f"State+forecast stage failed after {max_attempts} attempts: {last_exc}"
    ) from last_exc
