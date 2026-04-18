"""Event extraction stage: convert normalized inputs into structured events."""

from __future__ import annotations

from pydantic import ValidationError

from app.exceptions import PipelineStepError
from app.llm_client import BaseLLMClient, LLMResponseError
from app.schemas import EventExtractionResult, NormalizedInputs


def run_event_extraction(
    llm_client: BaseLLMClient,
    prompt_template: str,
    normalized_inputs: NormalizedInputs,
    normalized_inputs_payload: dict[str, object] | None = None,
) -> EventExtractionResult:
    """Execute the event extraction stage."""
    payload = {
        "normalized_inputs": (
            normalized_inputs_payload
            if normalized_inputs_payload is not None
            else normalized_inputs.model_dump(mode="json")
        )
    }
    max_attempts = 3

    last_exc: Exception | None = None
    for _ in range(max_attempts):
        try:
            response = llm_client.generate_json("event_extraction", prompt_template, payload)
            return EventExtractionResult.model_validate(response)
        except (LLMResponseError, ValidationError, ValueError) as exc:
            last_exc = exc

    raise PipelineStepError(f"Event extraction stage failed after {max_attempts} attempts: {last_exc}") from last_exc
