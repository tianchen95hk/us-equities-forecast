"""Deterministic confidence calibration for forecast payloads."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.schemas import (
    ConfidenceBreakdown,
    ConfidenceComponentScores,
    ConfidencePenaltyScores,
    InputFreshnessReport,
    StateMappingResult,
)


@dataclass
class ConfidenceComputationResult:
    """Structured confidence computation output."""

    confidence: float
    breakdown: ConfidenceBreakdown


def compute_confidence(
    *,
    state_mapping: StateMappingResult,
    structured_events_payload: dict[str, Any],
    forecast_payload: dict[str, Any],
    freshness_report: InputFreshnessReport,
    latest_available_fallback_applied: bool,
) -> ConfidenceComputationResult:
    """Compute a deterministic confidence score in range [0.35, 0.9].

    The score is derived from current observable consistency instead of opaque LLM output.
    """
    directional_bias = str(forecast_payload.get("directional_bias", "neutral")).lower().strip()

    scenario_alignment = _scenario_alignment_score(state_mapping, directional_bias)
    event_consensus = _event_consensus_score(structured_events_payload, directional_bias)
    cross_asset_confirmation = _cross_asset_confirmation_score(state_mapping, directional_bias)
    evidence_balance = _evidence_balance_score(forecast_payload)

    freshness_penalty = 0.0
    if latest_available_fallback_applied:
        freshness_penalty += 0.07
    if freshness_report.stale_news or freshness_report.stale_market:
        freshness_penalty += 0.03

    risk_penalty = _risk_penalty_from_state(state_mapping, directional_bias)

    raw_confidence = (
        0.40
        + 0.22 * scenario_alignment
        + 0.20 * event_consensus
        + 0.23 * cross_asset_confirmation
        + 0.12 * evidence_balance
        - freshness_penalty
        - risk_penalty
    )
    confidence = round(_clamp(raw_confidence, 0.35, 0.90), 2)

    breakdown = ConfidenceBreakdown(
        formula=(
            "0.40 + 0.22*scenario_alignment + 0.20*event_consensus "
            "+ 0.23*cross_asset_confirmation + 0.12*evidence_balance "
            "- freshness_penalty - risk_penalty"
        ),
        directional_bias=directional_bias,
        components=ConfidenceComponentScores(
            scenario_alignment=round(scenario_alignment, 4),
            event_consensus=round(event_consensus, 4),
            cross_asset_confirmation=round(cross_asset_confirmation, 4),
            evidence_balance=round(evidence_balance, 4),
        ),
        penalties=ConfidencePenaltyScores(
            freshness_penalty=round(freshness_penalty, 4),
            risk_penalty=round(risk_penalty, 4),
        ),
        raw_confidence=round(raw_confidence, 4),
        final_confidence=confidence,
        notes=[
            "confidence is deterministic and recomputed from observable state consistency",
            "LLM-proposed confidence is overwritten to improve auditability",
        ],
    )

    return ConfidenceComputationResult(confidence=confidence, breakdown=breakdown)


def _scenario_alignment_score(state_mapping: StateMappingResult, directional_bias: str) -> float:
    if not state_mapping.scenarios:
        return 0.5

    top_scenario = max(state_mapping.scenarios, key=lambda item: float(item.probability))
    top_implication = str(top_scenario.directional_implication.value).lower()
    top_probability = float(top_scenario.probability)

    if directional_bias == "neutral":
        if top_implication == "neutral":
            return 0.7 + 0.3 * top_probability
        return 0.45 + 0.25 * (1.0 - top_probability)

    if top_implication == directional_bias:
        return 0.65 + 0.35 * top_probability

    if top_implication == "neutral":
        return 0.45

    return 0.25


def _event_consensus_score(structured_events_payload: dict[str, Any], directional_bias: str) -> float:
    events = structured_events_payload.get("events", [])
    if not isinstance(events, list) or not events:
        return 0.5

    mapped: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        impact_bias = str(event.get("impact_bias", "neutral")).lower()
        if impact_bias in {"up", "down", "neutral"}:
            mapped.append(impact_bias)

    if not mapped:
        return 0.5

    counts = Counter(mapped)
    total = len(mapped)

    if directional_bias == "bullish":
        aligned = counts.get("up", 0)
        opposed = counts.get("down", 0)
    elif directional_bias == "bearish":
        aligned = counts.get("down", 0)
        opposed = counts.get("up", 0)
    else:
        aligned = counts.get("neutral", 0)
        opposed = abs(counts.get("up", 0) - counts.get("down", 0))

    score = 0.5 + (aligned - opposed) / max(total, 1)
    return _clamp(score, 0.0, 1.0)


def _cross_asset_confirmation_score(state_mapping: StateMappingResult, directional_bias: str) -> float:
    text = " ".join(state_mapping.cross_asset_signals).lower()
    text += " " + " ".join(
        [state_mapping.growth_state, state_mapping.inflation_state, state_mapping.liquidity_state, state_mapping.volatility_state]
    ).lower()

    bullish_terms = ["contained", "stable", "moderating", "improves", "cools"]
    bearish_terms = ["elevated", "tightening", "rising", "shock", "stress", "sticky"]

    bullish_hits = sum(1 for term in bullish_terms if term in text)
    bearish_hits = sum(1 for term in bearish_terms if term in text)

    if directional_bias == "bullish":
        score = 0.5 + 0.08 * (bullish_hits - bearish_hits)
    elif directional_bias == "bearish":
        score = 0.5 + 0.08 * (bearish_hits - bullish_hits)
    else:
        score = 0.55 - 0.04 * abs(bullish_hits - bearish_hits)

    return _clamp(score, 0.0, 1.0)


def _evidence_balance_score(forecast_payload: dict[str, Any]) -> float:
    supportive = _text_items(forecast_payload.get("supportive_evidence"))
    opposing = _text_items(forecast_payload.get("opposing_evidence"))
    if not supportive or not opposing:
        return 0.35

    ratio = max(len(supportive), len(opposing)) / max(1, min(len(supportive), len(opposing)))
    if ratio <= 1.5:
        return 0.9
    if ratio <= 2.5:
        return 0.75
    if ratio <= 4.0:
        return 0.6
    return 0.45


def _risk_penalty_from_state(state_mapping: StateMappingResult, directional_bias: str) -> float:
    penalty = 0.0
    vol = state_mapping.volatility_state.lower()
    liquidity = state_mapping.liquidity_state.lower()

    if "elevated" in vol:
        penalty += 0.06
    if "tight" in liquidity:
        penalty += 0.04

    if directional_bias == "bullish" and penalty > 0:
        penalty += 0.02
    return penalty


def _text_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
