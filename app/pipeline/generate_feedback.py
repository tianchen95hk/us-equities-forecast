"""Prompt-driven data feedback layers for input visibility and forecast traceability."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.exceptions import PipelineStepError
from app.llm_client import BaseLLMClient, LLMResponseError
from app.schemas import (
    EventExtractionResult,
    FinalForecast,
    NormalizedInputs,
    PostForecastFeedback,
    PreForecastFeedback,
    StateMappingResult,
)


def _to_signal_dicts(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            normalized.append(item)
            continue
        if isinstance(item, str) and item.strip():
            normalized.append(
                {
                    "signal": item.strip(),
                    "direction": "neutral",
                    "confidence": 0.5,
                    "evidence_refs": [],
                    "rationale": "",
                }
            )
    return normalized


def _to_non_empty_list(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [str(item).strip() for item in items if str(item).strip()]


def _format_market_summary_line(
    *,
    symbol: str,
    point: dict[str, Any] | None,
    output_language: str,
) -> str:
    if point is None:
        if output_language.lower() == "zh":
            return f"{symbol}: 数据缺失（本轮未采集到该标的行情）"
        return f"{symbol}: data missing (not collected in this run)"

    value = point.get("value")
    change_pct = point.get("change_pct")
    as_of = point.get("as_of")
    if output_language.lower() == "zh":
        return f"{symbol}: 最新值={value}, 日变动={change_pct}%, 时间={as_of}"
    return f"{symbol}: value={value}, daily_change={change_pct}%, as_of={as_of}"


def _ensure_market_snapshot_summary_coverage(
    *,
    summary: list[str],
    market_universe: list[str],
    market_snapshot: dict[str, dict[str, Any]],
    output_language: str,
) -> list[str]:
    """Ensure market snapshot summary covers all symbols in market_universe."""
    normalized = list(summary)
    lowered = [item.lower() for item in normalized]
    for symbol in market_universe:
        if any(symbol.lower() in entry for entry in lowered):
            continue
        normalized.append(
            _format_market_summary_line(
                symbol=symbol,
                point=market_snapshot.get(symbol),
                output_language=output_language,
            )
        )
    return normalized


def _normalize_pre_feedback_response(
    response: dict[str, Any],
    now: str,
    *,
    market_universe: list[str],
    market_snapshot: dict[str, dict[str, Any]],
    output_language: str,
) -> dict[str, Any]:
    if not isinstance(response, dict):
        return response

    normalized = dict(response)
    normalized.setdefault("generated_at", now)
    summary = _to_non_empty_list(normalized.get("market_snapshot_summary"))
    normalized["market_snapshot_summary"] = _ensure_market_snapshot_summary_coverage(
        summary=summary,
        market_universe=market_universe,
        market_snapshot=market_snapshot,
        output_language=output_language,
    )
    normalized["top_news_signals"] = _to_signal_dicts(normalized.get("top_news_signals"))
    normalized["top_market_signals"] = _to_signal_dicts(normalized.get("top_market_signals"))
    normalized["signal_conflicts"] = _to_non_empty_list(normalized.get("signal_conflicts"))
    return normalized


def _normalize_post_feedback_response(response: dict[str, Any], now: str) -> dict[str, Any]:
    if not isinstance(response, dict):
        return response

    normalized = dict(response)
    normalized.setdefault("generated_at", now)
    normalized["forecast_support_map"] = _to_non_empty_list(normalized.get("forecast_support_map"))
    normalized["forecast_opposition_map"] = _to_non_empty_list(
        normalized.get("forecast_opposition_map")
    )
    normalized["monitoring_priorities"] = _to_non_empty_list(
        normalized.get("monitoring_priorities")
    )
    normalized["next_run_questions"] = _to_non_empty_list(normalized.get("next_run_questions"))
    return normalized


def run_pre_forecast_feedback(
    llm_client: BaseLLMClient,
    prompt_template: str,
    normalized_inputs: NormalizedInputs,
    structured_events: EventExtractionResult,
    state_mapping: StateMappingResult,
    factor_snapshot: dict[str, Any] | None,
    dominant_factor_result: dict[str, Any] | None,
    market_snapshot: dict[str, dict[str, Any]],
    news_snapshot: list[dict[str, Any]],
    output_language: str = "zh",
    normalized_inputs_payload: dict[str, object] | None = None,
) -> PreForecastFeedback:
    """Generate a structured pre-forecast feedback layer from current observations."""
    payload = {
        "normalized_inputs": (
            normalized_inputs_payload
            if normalized_inputs_payload is not None
            else normalized_inputs.model_dump(mode="json")
        ),
        "structured_events": structured_events.model_dump(mode="json"),
        "state_mapping": state_mapping.model_dump(mode="json"),
        "factor_snapshot": factor_snapshot or {},
        "dominant_factor_result": dominant_factor_result or {},
        "market_snapshot": market_snapshot,
        "news_snapshot": news_snapshot,
        "output_language": output_language,
    }

    max_attempts = 3
    last_exc: Exception | None = None
    for _ in range(max_attempts):
        try:
            response = llm_client.generate_json("pre_forecast_feedback", prompt_template, payload)
            now = datetime.now(timezone.utc).isoformat()
            normalized = _normalize_pre_feedback_response(
                response,
                now,
                market_universe=normalized_inputs.market_universe,
                market_snapshot=market_snapshot,
                output_language=output_language,
            )
            return PreForecastFeedback.model_validate(normalized)
        except (LLMResponseError, ValidationError, ValueError) as exc:
            last_exc = exc

    raise PipelineStepError(
        f"Pre-forecast feedback stage failed after {max_attempts} attempts: {last_exc}"
    ) from last_exc


def run_post_forecast_feedback(
    llm_client: BaseLLMClient,
    prompt_template: str,
    normalized_inputs: NormalizedInputs,
    state_mapping: StateMappingResult,
    final_forecast: FinalForecast,
    pre_feedback: PreForecastFeedback,
    factor_snapshot: dict[str, Any] | None,
    dominant_factor_result: dict[str, Any] | None,
    market_snapshot: dict[str, dict[str, Any]],
    news_snapshot: list[dict[str, Any]],
    output_language: str = "zh",
    normalized_inputs_payload: dict[str, object] | None = None,
) -> PostForecastFeedback:
    """Generate a post-forecast feedback layer mapping observations to final stance."""
    payload = {
        "normalized_inputs": (
            normalized_inputs_payload
            if normalized_inputs_payload is not None
            else normalized_inputs.model_dump(mode="json")
        ),
        "state_mapping": state_mapping.model_dump(mode="json"),
        "final_forecast": final_forecast.model_dump(mode="json"),
        "pre_forecast_feedback": pre_feedback.model_dump(mode="json"),
        "factor_snapshot": factor_snapshot or {},
        "dominant_factor_result": dominant_factor_result or {},
        "market_snapshot": market_snapshot,
        "news_snapshot": news_snapshot,
        "output_language": output_language,
    }

    max_attempts = 3
    last_exc: Exception | None = None
    for _ in range(max_attempts):
        try:
            response = llm_client.generate_json("post_forecast_feedback", prompt_template, payload)
            now = datetime.now(timezone.utc).isoformat()
            normalized = _normalize_post_feedback_response(response, now)
            return PostForecastFeedback.model_validate(normalized)
        except (LLMResponseError, ValidationError, ValueError) as exc:
            last_exc = exc

    raise PipelineStepError(
        f"Post-forecast feedback stage failed after {max_attempts} attempts: {last_exc}"
    ) from last_exc


def build_pre_feedback_fallback(
    normalized_inputs: NormalizedInputs,
    structured_events: EventExtractionResult,
    market_snapshot: dict[str, dict[str, Any]],
) -> PreForecastFeedback:
    """Deterministic fallback when prompt-driven pre-feedback generation fails."""
    summary: list[str] = []
    for symbol in normalized_inputs.market_universe:
        point = market_snapshot.get(symbol)
        summary.append(
            _format_market_summary_line(
                symbol=symbol,
                point=point,
                output_language="en",
            )
        )

    news_signals: list[dict[str, Any]] = []
    up_count = 0
    down_count = 0
    for event in structured_events.events[:5]:
        direction = "neutral"
        if event.impact_bias == "up":
            direction = "up"
            up_count += 1
        elif event.impact_bias == "down":
            direction = "down"
            down_count += 1
        news_signals.append(
            {
                "signal": event.description,
                "direction": direction,
                "confidence": event.confidence,
                "evidence_refs": event.evidence_refs,
                "rationale": event.impact_pathway,
            }
        )

    market_signals: list[dict[str, Any]] = []
    for symbol in normalized_inputs.market_universe:
        point = market_snapshot.get(symbol)
        if not point:
            continue
        change_pct = point.get("change_pct")
        direction = "neutral"
        if isinstance(change_pct, (float, int)):
            if change_pct > 0.25:
                direction = "up"
            elif change_pct < -0.25:
                direction = "down"
        market_signals.append(
            {
                "signal": f"{symbol} change_pct={change_pct}",
                "direction": direction,
                "confidence": 0.55,
                "evidence_refs": [symbol],
                "rationale": "Fallback signal derived from latest market snapshot.",
            }
        )

    conflicts: list[str] = []
    if up_count > 0 and down_count > 0:
        conflicts.append("News signals are mixed across positive and negative event impulses.")

    return PreForecastFeedback(
        generated_at=datetime.now(timezone.utc),
        market_snapshot_summary=summary,
        top_news_signals=news_signals,
        top_market_signals=market_signals,
        signal_conflicts=conflicts,
    )


def build_post_feedback_fallback(
    final_forecast: FinalForecast,
    pre_feedback: PreForecastFeedback,
) -> PostForecastFeedback:
    """Deterministic fallback when prompt-driven post-feedback generation fails."""
    support_map = [f"{idx}. {item}" for idx, item in enumerate(final_forecast.supportive_evidence, start=1)]
    opposition_map = [
        f"{idx}. {item}" for idx, item in enumerate(final_forecast.opposing_evidence, start=1)
    ]
    monitoring = list(dict.fromkeys([*final_forecast.monitoring_list, *final_forecast.invalidation_conditions]))

    questions: list[str] = []
    for conflict in pre_feedback.signal_conflicts[:3]:
        questions.append(f"How does this conflict resolve next run: {conflict}")
    if not questions:
        questions.append("Which new input could invalidate the current directional bias next run?")

    return PostForecastFeedback(
        generated_at=datetime.now(timezone.utc),
        forecast_support_map=support_map,
        forecast_opposition_map=opposition_map,
        monitoring_priorities=monitoring[:8],
        next_run_questions=questions[:6],
    )
