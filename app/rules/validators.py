"""Deterministic minimal local repair utilities for forecast payloads."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.schemas import ForecastDraft, FinalForecast

_LIST_FIELDS: tuple[str, ...] = (
    "dominant_drivers",
    "supportive_evidence",
    "opposing_evidence",
    "upside_triggers",
    "downside_triggers",
    "invalidation_conditions",
    "monitoring_list",
)

_REFERENCE_LEVEL_FIELDS: tuple[str, ...] = (
    "support_levels",
    "resistance_levels",
    "risk_triggers",
    "confirmation_levels",
)


def repair_forecast_payload(
    forecast: ForecastDraft | FinalForecast | dict[str, Any],
    output_language: str = "zh",
) -> dict[str, Any]:
    """Apply minimal deterministic repairs without changing analytical semantics."""
    del output_language
    payload = forecast if isinstance(forecast, dict) else forecast.model_dump(mode="json")
    repaired = json.loads(json.dumps(payload, ensure_ascii=False))

    _normalize_strings_inplace(repaired)
    _normalize_list_fields(repaired)
    _normalize_reference_levels(repaired)
    _normalize_timestamp_field(repaired, field_name="generated_at")

    return repaired


def _normalize_strings_inplace(payload: Any) -> Any:
    if isinstance(payload, str):
        return " ".join(payload.split())
    if isinstance(payload, list):
        return [_normalize_strings_inplace(item) for item in payload]
    if isinstance(payload, dict):
        for key in list(payload.keys()):
            payload[key] = _normalize_strings_inplace(payload[key])
        return payload
    return payload


def _normalize_list_fields(payload: dict[str, Any]) -> None:
    for field_name in _LIST_FIELDS:
        value = payload.get(field_name)
        if isinstance(value, list):
            payload[field_name] = [item for item in value if isinstance(item, str) and item.strip()]
            continue
        if isinstance(value, str) and value.strip():
            payload[field_name] = [value.strip()]
            continue
        payload[field_name] = []


def _normalize_reference_levels(payload: dict[str, Any]) -> None:
    value = payload.get("reference_levels")
    if isinstance(value, dict):
        for field_name in _REFERENCE_LEVEL_FIELDS:
            field_value = value.get(field_name)
            if isinstance(field_value, list):
                value[field_name] = [item for item in field_value if isinstance(item, str) and item.strip()]
            elif isinstance(field_value, str) and field_value.strip():
                value[field_name] = [field_value.strip()]
            else:
                value[field_name] = []
        payload["reference_levels"] = value
        return

    payload["reference_levels"] = {field_name: [] for field_name in _REFERENCE_LEVEL_FIELDS}


def _normalize_timestamp_field(payload: dict[str, Any], field_name: str) -> None:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        payload[field_name] = datetime.now(timezone.utc).isoformat()
        return

    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        payload[field_name] = datetime.now(timezone.utc).isoformat()
        return

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    payload[field_name] = dt.isoformat()
