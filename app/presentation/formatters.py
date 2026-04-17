"""Formatting helpers for simplified CLI output."""

from __future__ import annotations

from typing import Any, Literal

from app.pipeline.orchestrator import PipelineResult


def format_cli_output(
    result: PipelineResult,
    language: Literal["zh", "en"] = "zh",
    style: Literal["simple", "full"] = "simple",
) -> dict[str, Any]:
    """Format pipeline result for CLI display."""
    if style == "full":
        payload: dict[str, Any] = {
            "run_id": result.run_id,
            "publish_status": result.publish_status,
            "rejection_reasons": result.rejection_reasons,
            "artifact_paths": result.artifact_paths,
        }
        if result.final_forecast is not None:
            payload["final_forecast"] = result.final_forecast.model_dump(mode="json")
        return payload

    if language == "zh":
        return _to_simple_zh(result)
    return _to_simple_en(result)


def _to_simple_zh(result: PipelineResult) -> dict[str, Any]:
    if result.final_forecast is None:
        reject_detail_path = result.artifact_paths.get("review_rejected") or result.artifact_paths.get(
            "input_rejected"
        )
        return {
            "运行ID": result.run_id,
            "发布状态": "已拒绝",
            "拒绝原因": result.rejection_reasons,
            "文件路径": {
                "拒绝详情": reject_detail_path,
                "输入时效检查": result.artifact_paths.get("input_freshness_report"),
                "反后验审查": result.artifact_paths.get("anti_hindsight_review"),
                "审查前规则报告": result.artifact_paths.get("draft_rule_report"),
                "修复后规则报告": result.artifact_paths.get("post_repair_rule_report"),
            },
        }

    forecast = result.final_forecast
    return {
        "运行ID": result.run_id,
        "发布状态": "已通过",
        "预测周期": forecast.forecast_horizon,
        "方向判断": _bias_zh(forecast.directional_bias.value),
        "置信度": _percent(forecast.confidence),
        "反后验审查": _status_zh(forecast.anti_hindsight_status.value),
        "核心驱动": forecast.dominant_drivers[:3],
        "上行触发": forecast.upside_triggers[:3],
        "下行触发": forecast.downside_triggers[:3],
        "失效条件": forecast.invalidation_conditions[:3],
        "重点监控": forecast.monitoring_list[:5],
        "结论摘要": forecast.final_thesis,
        "文件路径": {
            "最终结果": result.artifact_paths.get("final_forecast"),
            "市场原始数据": result.artifact_paths.get("market_raw"),
            "反后验审查": result.artifact_paths.get("anti_hindsight_review"),
        },
    }


def _to_simple_en(result: PipelineResult) -> dict[str, Any]:
    if result.final_forecast is None:
        reject_detail_path = result.artifact_paths.get("review_rejected") or result.artifact_paths.get(
            "input_rejected"
        )
        return {
            "run_id": result.run_id,
            "publish_status": "rejected",
            "rejection_reasons": result.rejection_reasons,
            "artifact_paths": {
                "rejected_detail": reject_detail_path,
                "input_freshness_report": result.artifact_paths.get("input_freshness_report"),
                "anti_hindsight_review": result.artifact_paths.get("anti_hindsight_review"),
                "draft_rule_report": result.artifact_paths.get("draft_rule_report"),
                "post_repair_rule_report": result.artifact_paths.get("post_repair_rule_report"),
            },
        }

    forecast = result.final_forecast
    return {
        "run_id": result.run_id,
        "publish_status": "approved",
        "horizon": forecast.forecast_horizon,
        "directional_bias": forecast.directional_bias.value,
        "confidence": _percent(forecast.confidence),
        "anti_hindsight": forecast.anti_hindsight_status.value,
        "dominant_drivers": forecast.dominant_drivers[:3],
        "upside_triggers": forecast.upside_triggers[:3],
        "downside_triggers": forecast.downside_triggers[:3],
        "invalidation_conditions": forecast.invalidation_conditions[:3],
        "monitoring_list": forecast.monitoring_list[:5],
        "thesis": forecast.final_thesis,
        "artifact_paths": {
            "final_forecast": result.artifact_paths.get("final_forecast"),
            "market_raw": result.artifact_paths.get("market_raw"),
            "anti_hindsight_review": result.artifact_paths.get("anti_hindsight_review"),
        },
    }


def _bias_zh(value: str) -> str:
    mapping = {
        "bullish": "看多",
        "bearish": "看空",
        "neutral": "中性",
    }
    return mapping.get(value, value)


def _status_zh(value: str) -> str:
    mapping = {
        "PASS": "通过",
        "FAIL": "未通过",
    }
    return mapping.get(value, value)


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"
