"""Combined state-mapping + forecast-draft stage (single LLM call)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.exceptions import PipelineStepError
from app.llm_client import BaseLLMClient, LLMResponseError
from app.schemas import DirectionalBias, EventExtractionResult, NormalizedInputs, StateAndForecastResult

_BIAS_MAP: dict[str, str] = {
    "bullish": DirectionalBias.BULLISH.value,
    "bearish": DirectionalBias.BEARISH.value,
    "neutral": DirectionalBias.NEUTRAL.value,
    "看涨": DirectionalBias.BULLISH.value,
    "看多": DirectionalBias.BULLISH.value,
    "偏多": DirectionalBias.BULLISH.value,
    "上行": DirectionalBias.BULLISH.value,
    "看跌": DirectionalBias.BEARISH.value,
    "看空": DirectionalBias.BEARISH.value,
    "偏空": DirectionalBias.BEARISH.value,
    "下行": DirectionalBias.BEARISH.value,
    "中性": DirectionalBias.NEUTRAL.value,
    "震荡": DirectionalBias.NEUTRAL.value,
    "区间": DirectionalBias.NEUTRAL.value,
}


def _normalize_bias(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        lowered = text.lower()
        if lowered in _BIAS_MAP:
            return _BIAS_MAP[lowered]
        if text in _BIAS_MAP:
            return _BIAS_MAP[text]
    return DirectionalBias.NEUTRAL.value


def _event_bias(events_payload: dict[str, Any]) -> str:
    score = 0
    for event in events_payload.get("events", []):
        if not isinstance(event, dict):
            continue
        impact_bias = str(event.get("impact_bias", "neutral")).lower()
        if impact_bias == "up":
            score += 1
        elif impact_bias == "down":
            score -= 1
    if score > 0:
        return DirectionalBias.BULLISH.value
    if score < 0:
        return DirectionalBias.BEARISH.value
    return DirectionalBias.NEUTRAL.value


def _coerce_str_list(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, list):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if cleaned:
            return cleaned
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return default


def _build_fallback_forecast_draft(
    state_mapping: dict[str, Any],
    normalized_inputs: NormalizedInputs,
    structured_events: EventExtractionResult,
    output_language: str,
) -> dict[str, Any]:
    scenarios = state_mapping.get("scenarios", [])
    top_scenario: dict[str, Any] | None = None
    if isinstance(scenarios, list):
        ranked = [item for item in scenarios if isinstance(item, dict)]
        if ranked:
            top_scenario = max(ranked, key=lambda item: float(item.get("probability", 0.0)))

    directional_bias = (
        _normalize_bias(top_scenario.get("directional_implication"))
        if top_scenario
        else _event_bias(structured_events.model_dump(mode="json"))
    )
    generated_at = state_mapping.get("generated_at") or datetime.now(timezone.utc).isoformat()
    signals = _coerce_str_list(
        state_mapping.get("cross_asset_signals"),
        ["Cross-asset signals are mixed and require close monitoring."],
    )

    event_desc = [
        event.description for event in structured_events.events if event.description.strip()
    ]
    up_events = [
        event.description
        for event in structured_events.events
        if event.impact_bias == "up" and event.description.strip()
    ]
    down_events = [
        event.description
        for event in structured_events.events
        if event.impact_bias == "down" and event.description.strip()
    ]

    if output_language.lower() == "zh":
        dominant_drivers = _coerce_str_list(
            state_mapping.get("cross_asset_signals"),
            ["跨资产信号显示当前处于过渡状态，需要结合触发条件动态调整。"],
        )[:3]
        supportive_evidence = up_events[:2] or event_desc[:2] or ["当前可观测信号未出现系统性恶化。"]
        opposing_evidence = down_events[:2] or ["部分事件信号与主方向存在冲突。"]
        upside_triggers = [
            "波动率继续回落并维持低位",
            "利率与美元压力边际缓和",
        ]
        downside_triggers = [
            "波动率快速上行并持续",
            "利率走高叠加风险资产广度转弱",
        ]
        invalidation_conditions = [
            "跨资产信号与当前方向判断出现系统性反转",
            "新增高置信事件流与当前主假设明显相反",
        ]
        monitoring_list = normalized_inputs.market_universe + ["重点宏观新闻流"]
        final_thesis = (
            f"基于当前可观测状态映射（{state_mapping.get('regime_label', 'unknown regime')}）"
            "与事件方向统计形成该方向判断；若失效条件触发，应及时降级置信度并调整仓位。"
        )
    else:
        dominant_drivers = signals[:3]
        supportive_evidence = up_events[:2] or event_desc[:2] or ["Current observable signals are not deteriorating systemically."]
        opposing_evidence = down_events[:2] or ["Some incoming events conflict with the base direction."]
        upside_triggers = [
            "Volatility continues to cool and stays contained",
            "Rates and dollar pressure ease at the margin",
        ]
        downside_triggers = [
            "Volatility rises and remains elevated",
            "Rates increase while risk-asset breadth weakens",
        ]
        invalidation_conditions = [
            "Cross-asset signals flip systemically against current directional bias",
            "New high-confidence events oppose the core hypothesis",
        ]
        monitoring_list = normalized_inputs.market_universe + ["macro news flow"]
        final_thesis = (
            f"Directional bias follows currently observable regime "
            f"({state_mapping.get('regime_label', 'unknown regime')}) and event flow. "
            "Reduce confidence and adjust exposure if invalidation conditions trigger."
        )

    return {
        "generated_at": generated_at,
        "forecast_horizon": normalized_inputs.forecast_horizon,
        "market_universe": normalized_inputs.market_universe,
        "directional_bias": directional_bias,
        "confidence": 0.58,
        "dominant_drivers": dominant_drivers,
        "supportive_evidence": supportive_evidence,
        "opposing_evidence": opposing_evidence,
        "upside_triggers": upside_triggers,
        "downside_triggers": downside_triggers,
        "invalidation_conditions": invalidation_conditions,
        "monitoring_list": monitoring_list,
        "final_thesis": final_thesis,
    }


def _normalize_state_and_forecast_response(
    response: dict[str, Any],
    normalized_inputs: NormalizedInputs,
    structured_events: EventExtractionResult,
    output_language: str,
) -> dict[str, Any]:
    if not isinstance(response, dict):
        return response

    state_mapping = response.get("state_mapping")
    if isinstance(state_mapping, dict):
        scenarios = state_mapping.get("scenarios")
        if isinstance(scenarios, list):
            for scenario in scenarios:
                if not isinstance(scenario, dict):
                    continue
                if "directional_implication" in scenario:
                    scenario["directional_implication"] = _normalize_bias(
                        scenario.get("directional_implication")
                    )

    forecast_draft = response.get("forecast_draft")
    if isinstance(forecast_draft, dict):
        forecast_draft["directional_bias"] = _normalize_bias(forecast_draft.get("directional_bias"))
    else:
        response["forecast_draft"] = _build_fallback_forecast_draft(
            state_mapping=state_mapping if isinstance(state_mapping, dict) else {},
            normalized_inputs=normalized_inputs,
            structured_events=structured_events,
            output_language=output_language,
        )

    return response


def run_state_and_forecast(
    llm_client: BaseLLMClient,
    prompt_template: str,
    normalized_inputs: NormalizedInputs,
    structured_events: EventExtractionResult,
    output_language: str = "zh",
    normalized_inputs_payload: dict[str, object] | None = None,
) -> StateAndForecastResult:
    """Execute combined stage mapping state and producing forecast draft."""
    payload = {
        "normalized_inputs": (
            normalized_inputs_payload
            if normalized_inputs_payload is not None
            else normalized_inputs.model_dump(mode="json")
        ),
        "structured_events": structured_events.model_dump(mode="json"),
        "output_language": output_language,
    }

    max_attempts = 3
    last_exc: Exception | None = None
    for _ in range(max_attempts):
        try:
            response = llm_client.generate_json("state_and_forecast", prompt_template, payload)
            normalized_response = _normalize_state_and_forecast_response(
                response=response,
                normalized_inputs=normalized_inputs,
                structured_events=structured_events,
                output_language=output_language,
            )
            return StateAndForecastResult.model_validate(normalized_response)
        except (LLMResponseError, ValidationError, ValueError) as exc:
            last_exc = exc

    raise PipelineStepError(
        f"State+forecast stage failed after {max_attempts} attempts: {last_exc}"
    ) from last_exc
