"""Deterministic five-factor engine for forward-looking directional attribution."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.schemas import (
    DominantFactorResult,
    EarningsRevisionProxy,
    FactorDirection,
    FactorSignal,
    FactorSnapshot,
    FactorStrength,
    NormalizedInputs,
)

FACTOR_NAMES: tuple[str, ...] = (
    "earnings_revision",
    "volatility",
    "rates",
    "dollar",
    "energy_geopolitics",
)


def build_factor_snapshot(
    *,
    settings: Settings,
    normalized_inputs: NormalizedInputs,
    earnings_proxy: EarningsRevisionProxy,
    output_language: str = "zh",
) -> tuple[FactorSnapshot, DominantFactorResult]:
    """Build deterministic five-factor snapshot and dominant-factor decision."""
    indicator_map = {item.symbol: item for item in normalized_inputs.indicators}
    generated_at = datetime.now(timezone.utc)

    earnings_signal = _earnings_factor_signal(earnings_proxy)
    volatility_signal = _volatility_factor_signal(indicator_map)
    rates_signal = _rates_factor_signal(indicator_map)
    dollar_signal = _dollar_factor_signal(indicator_map)
    energy_signal = _energy_geopolitics_factor_signal(indicator_map, normalized_inputs)

    weights = {
        "earnings_revision": float(settings.factor_weight_earnings_revision),
        "volatility": float(settings.factor_weight_volatility),
        "rates": float(settings.factor_weight_rates),
        "dollar": float(settings.factor_weight_dollar),
        "energy_geopolitics": float(settings.factor_weight_energy_geopolitics),
    }

    weighted_scores = {
        "earnings_revision": round(earnings_signal.score * weights["earnings_revision"], 6),
        "volatility": round(volatility_signal.score * weights["volatility"], 6),
        "rates": round(rates_signal.score * weights["rates"], 6),
        "dollar": round(dollar_signal.score * weights["dollar"], 6),
        "energy_geopolitics": round(energy_signal.score * weights["energy_geopolitics"], 6),
    }

    snapshot = FactorSnapshot(
        generated_at=generated_at,
        earnings_revision=earnings_signal,
        volatility=volatility_signal,
        rates=rates_signal,
        dollar=dollar_signal,
        energy_geopolitics=energy_signal,
        weights=weights,
        weighted_scores=weighted_scores,
    )

    dominant = _build_dominant_factor_result(
        weighted_scores=weighted_scores,
        tie_threshold=float(settings.factor_dominant_tie_threshold),
        output_language=output_language,
    )
    return snapshot, dominant


def _earnings_factor_signal(proxy: EarningsRevisionProxy) -> FactorSignal:
    return FactorSignal(
        direction=proxy.signal,
        score=float(proxy.score),
        strength=_strength_from_score(float(proxy.score)),
        evidence_refs=proxy.evidence_refs,
        limitations=proxy.limitations,
        as_of=proxy.as_of,
    )


def _volatility_factor_signal(indicator_map: dict[str, Any]) -> FactorSignal:
    now = datetime.now(timezone.utc)
    vix = indicator_map.get("VIX")
    if vix is None:
        return _missing_signal("VIX missing", now)

    value = float(vix.value)
    change = float(vix.change_pct or 0.0)

    if value <= 16:
        score = 0.65
    elif value <= 20:
        score = 0.35
    elif value <= 24:
        score = 0.0
    elif value <= 30:
        score = -0.45
    else:
        score = -0.75

    if change >= 4.0:
        score -= 0.2
    elif change <= -4.0:
        score += 0.15

    score = _clamp(score, -1.0, 1.0)
    return FactorSignal(
        direction=_direction_from_score(score),
        score=score,
        strength=_strength_from_score(score),
        evidence_refs=[f"VIX={value:.2f}", f"VIX_change_pct={change:.2f}"],
        limitations=[],
        as_of=vix.as_of,
    )


def _rates_factor_signal(indicator_map: dict[str, Any]) -> FactorSignal:
    now = datetime.now(timezone.utc)
    us10y = indicator_map.get("US10Y")
    if us10y is None:
        return _missing_signal("US10Y missing", now)

    level = float(us10y.value)
    change = float(us10y.change_pct or 0.0)

    level_component = 0.0
    if level >= 4.8:
        level_component = -0.45
    elif level >= 4.5:
        level_component = -0.3
    elif level >= 4.2:
        level_component = -0.12
    elif level <= 3.6:
        level_component = 0.2

    change_component = _clamp(-change / 0.9, -0.7, 0.7)
    score = _clamp(level_component + change_component, -1.0, 1.0)

    return FactorSignal(
        direction=_direction_from_score(score),
        score=score,
        strength=_strength_from_score(score),
        evidence_refs=[f"US10Y={level:.3f}", f"US10Y_change_pct={change:.2f}"],
        limitations=[],
        as_of=us10y.as_of,
    )


def _dollar_factor_signal(indicator_map: dict[str, Any]) -> FactorSignal:
    now = datetime.now(timezone.utc)
    dxy = indicator_map.get("DXY")
    usdjpy = indicator_map.get("USDJPY")
    if dxy is None and usdjpy is None:
        return _missing_signal("DXY and USDJPY missing", now)

    dxy_change = float(dxy.change_pct or 0.0) if dxy is not None else 0.0
    dxy_level = float(dxy.value) if dxy is not None else 0.0
    usdjpy_change = float(usdjpy.change_pct or 0.0) if usdjpy is not None else 0.0

    level_component = 0.0
    if dxy is not None:
        if dxy_level >= 106:
            level_component = -0.35
        elif dxy_level >= 104:
            level_component = -0.2
        elif dxy_level <= 100:
            level_component = 0.15

    move_component = _clamp(-(0.75 * dxy_change + 0.25 * usdjpy_change) / 0.8, -0.7, 0.7)
    score = _clamp(level_component + move_component, -1.0, 1.0)

    evidence_refs = []
    if dxy is not None:
        evidence_refs.extend([f"DXY={dxy_level:.2f}", f"DXY_change_pct={dxy_change:.2f}"])
    if usdjpy is not None:
        evidence_refs.append(f"USDJPY_change_pct={usdjpy_change:.2f}")

    as_of = max(
        [item.as_of for item in (dxy, usdjpy) if item is not None],
        default=now,
    )

    limitations: list[str] = []
    if dxy is None:
        limitations.append("DXY missing; USDJPY used as partial proxy")
    if usdjpy is None:
        limitations.append("USDJPY missing; DXY only")

    return FactorSignal(
        direction=_direction_from_score(score),
        score=score,
        strength=_strength_from_score(score),
        evidence_refs=evidence_refs,
        limitations=limitations,
        as_of=as_of,
    )


def _energy_geopolitics_factor_signal(
    indicator_map: dict[str, Any],
    normalized_inputs: NormalizedInputs,
) -> FactorSignal:
    now = datetime.now(timezone.utc)
    oil = indicator_map.get("OIL")
    oil_component = 0.0
    evidence = []
    limitations: list[str] = []

    if oil is None:
        limitations.append("OIL missing")
    else:
        oil_change = float(oil.change_pct or 0.0)
        oil_level = float(oil.value)
        oil_component = _clamp(-oil_change / 2.8, -0.8, 0.8)
        if oil_level >= 90:
            oil_component -= 0.12
        elif oil_level <= 68:
            oil_component += 0.12
        evidence.extend([f"OIL={oil_level:.2f}", f"OIL_change_pct={oil_change:.2f}"])

    geo_down_keywords = (
        "war",
        "missile",
        "attack",
        "sanction",
        "opec cut",
        "supply disruption",
        "strait",
        "conflict",
        "地缘",
        "冲突",
        "制裁",
        "中东",
    )
    geo_up_keywords = (
        "ceasefire",
        "truce",
        "production increase",
        "supply easing",
        "diplomacy",
        "停火",
        "增产",
        "缓和",
    )

    down_hits = 0
    up_hits = 0
    for item in normalized_inputs.news[:20]:
        text = f"{item.headline} {item.summary}".lower()
        if any(token in text for token in geo_down_keywords):
            down_hits += 1
        if any(token in text for token in geo_up_keywords):
            up_hits += 1

    geo_component = 0.0
    total_hits = up_hits + down_hits
    if total_hits > 0:
        geo_component = _clamp((up_hits - down_hits) / total_hits, -1.0, 1.0) * 0.45
        evidence.append(f"geo_headline_balance=up:{up_hits}/down:{down_hits}")

    score = _clamp(0.7 * oil_component + 0.3 * geo_component, -1.0, 1.0)

    as_of = oil.as_of if oil is not None else now
    return FactorSignal(
        direction=_direction_from_score(score),
        score=score,
        strength=_strength_from_score(score),
        evidence_refs=evidence,
        limitations=limitations,
        as_of=as_of,
    )


def _build_dominant_factor_result(
    *,
    weighted_scores: dict[str, float],
    tie_threshold: float,
    output_language: str,
) -> DominantFactorResult:
    if not weighted_scores:
        return DominantFactorResult(
            dominant_factor="none",
            dominant_factors=["none"],
            tie_detected=False,
            tie_threshold=tie_threshold,
            scoreboard={},
            explainer="No weighted scores were available.",
        )

    ordered = sorted(
        weighted_scores.items(),
        key=lambda item: abs(float(item[1])),
        reverse=True,
    )
    top_name, top_score = ordered[0]
    dominant_factors = [top_name]
    tie_detected = False

    if len(ordered) > 1:
        second_name, second_score = ordered[1]
        if abs(abs(top_score) - abs(second_score)) < tie_threshold:
            dominant_factors = [top_name, second_name]
            tie_detected = True

    dominant_factor = "+".join(dominant_factors)
    scoreboard = {name: round(float(score), 6) for name, score in ordered}

    if output_language.lower() == "zh":
        explainer = (
            f"主导判定按 abs(weight*score) 排序；当前主导={dominant_factor}，"
            f"前两名差值阈值={tie_threshold:.2f}。"
        )
    else:
        explainer = (
            f"Dominance ranks factors by abs(weight*score); dominant={dominant_factor}, "
            f"tie threshold={tie_threshold:.2f}."
        )

    return DominantFactorResult(
        dominant_factor=dominant_factor,
        dominant_factors=dominant_factors,
        tie_detected=tie_detected,
        tie_threshold=tie_threshold,
        scoreboard=scoreboard,
        explainer=explainer,
    )


def _missing_signal(message: str, as_of: datetime) -> FactorSignal:
    return FactorSignal(
        direction=FactorDirection.NEUTRAL,
        score=0.0,
        strength=FactorStrength.LOW,
        evidence_refs=[],
        limitations=[message],
        as_of=as_of,
    )


def _direction_from_score(score: float) -> FactorDirection:
    if score >= 0.15:
        return FactorDirection.UP
    if score <= -0.15:
        return FactorDirection.DOWN
    if abs(score) <= 0.05:
        return FactorDirection.NEUTRAL
    return FactorDirection.MIXED


def _strength_from_score(score: float) -> FactorStrength:
    magnitude = abs(score)
    if magnitude >= 0.55:
        return FactorStrength.HIGH
    if magnitude >= 0.25:
        return FactorStrength.MEDIUM
    return FactorStrength.LOW


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
