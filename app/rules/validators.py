"""Deterministic local repair utilities for forecast payloads."""

from __future__ import annotations

import json
import re
from typing import Any

from app.schemas import ForecastDraft, FinalForecast

_REQUIRED_LIST_FIELDS: tuple[str, ...] = (
    "dominant_drivers",
    "supportive_evidence",
    "opposing_evidence",
    "upside_triggers",
    "downside_triggers",
    "invalidation_conditions",
    "monitoring_list",
)


def repair_forecast_payload(
    forecast: ForecastDraft | FinalForecast | dict[str, Any],
    output_language: str = "zh",
) -> dict[str, Any]:
    """Apply deterministic local repairs without extra LLM calls."""
    payload = forecast if isinstance(forecast, dict) else forecast.model_dump(mode="json")
    repaired = json.loads(json.dumps(payload, ensure_ascii=False))

    _normalize_strings(repaired)
    _replace_banned_phrases(repaired)
    _ensure_required_lists(repaired, output_language)
    _ensure_horizon(repaired)
    _ensure_min_thesis(repaired, output_language)

    return repaired


def _normalize_strings(payload: dict[str, Any]) -> None:
    for key, value in list(payload.items()):
        if isinstance(value, str):
            payload[key] = " ".join(value.split())
        elif isinstance(value, list):
            normalized_items: list[Any] = []
            for item in value:
                if isinstance(item, str):
                    normalized_items.append(" ".join(item.split()))
                else:
                    normalized_items.append(item)
            payload[key] = normalized_items


def _replace_banned_phrases(payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False)
    replacements: dict[str, str] = {
        r"target\s+price": "directional threshold",
        r"will\s+reach": "may trend toward",
        r"break\s+above": "move stronger than",
        r"fall\s+to": "weaken toward",
        r"hit\s+\$?\d{3,6}(?:\.\d+)?": "touch a new zone",
        r"目标价": "方向阈值",
        r"(?:将到|会到|将会到|到达)\s*\d+(?:\.\d+)?": "可能走向关键区间",
        r"突破\s*\d+(?:\.\d+)?": "走强至关键区间",
        r"跌破\s*\d+(?:\.\d+)?": "走弱至关键区间",
        r"触及\s*\d+(?:\.\d+)?\s*(?:点|美元|元)?": "触及关键区间",
    }
    for pattern, replacement in replacements.items():
        serialized = re.sub(pattern, replacement, serialized, flags=re.IGNORECASE)

    payload.clear()
    payload.update(json.loads(serialized))


def _ensure_required_lists(payload: dict[str, Any], output_language: str) -> None:
    zh = output_language.lower() == "zh"
    default_text = "待补充" if zh else "to be completed"
    for field_name in _REQUIRED_LIST_FIELDS:
        value = payload.get(field_name)
        if not isinstance(value, list):
            payload[field_name] = [default_text]
            continue
        cleaned = [item for item in value if isinstance(item, str) and item.strip()]
        payload[field_name] = cleaned if cleaned else [default_text]


def _ensure_horizon(payload: dict[str, Any]) -> None:
    horizon = payload.get("forecast_horizon")
    if isinstance(horizon, str) and horizon.strip():
        return
    payload["forecast_horizon"] = "5 trading days"


def _ensure_min_thesis(payload: dict[str, Any], output_language: str) -> None:
    thesis = payload.get("final_thesis")
    if isinstance(thesis, str) and len(thesis.strip()) >= 20:
        return

    payload["final_thesis"] = (
        "结论待补充：当前输出经过规则修复后可用于审阅，但建议补充更完整的驱动与反证。"
        if output_language.lower() == "zh"
        else "Thesis placeholder: output was repaired by local rules; add fuller drivers and opposing evidence before use."
    )
