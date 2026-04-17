"""Formatting helpers for CLI/API-friendly output payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.pipeline.orchestrator import PipelineResult


def format_cli_output(
    result: PipelineResult,
    language: Literal["zh", "en"] = "zh",
    style: Literal["simple", "telegram", "full"] = "simple",
) -> dict[str, Any]:
    """Format pipeline result for CLI display."""
    if style == "full":
        payload: dict[str, Any] = {
            "run_id": result.run_id,
            "publish_status": result.publish_status,
            "rejection_reasons": result.rejection_reasons,
            "artifact_paths": result.artifact_paths,
            "collected_at": result.collected_at,
            "reviewed_at": result.reviewed_at,
            "latest_news_at": result.latest_news_at,
            "latest_market_at": result.latest_market_at,
            "run_started_at": result.run_started_at,
            "run_completed_at": result.run_completed_at,
        }
        if result.final_forecast is not None:
            payload["final_forecast"] = result.final_forecast.model_dump(mode="json")
        return payload

    if style == "telegram":
        return _to_telegram_zh(result) if language == "zh" else _to_telegram_en(result)

    if language == "zh":
        return _to_simple_zh(result)
    return _to_simple_en(result)


def _fmt_dt(dt_str: str | None, tz_name: str = "Asia/Shanghai") -> dict[str, str] | None:
    if not dt_str:
        return None

    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    local_dt = dt.astimezone(ZoneInfo(tz_name))
    return {
        "utc": dt.astimezone(timezone.utc).isoformat(),
        "local": local_dt.strftime("%Y-%m-%d %H:%M:%S %Z"),
    }


def _to_telegram_zh(result: PipelineResult) -> dict[str, Any]:
    if result.final_forecast is None:
        return {
            "运行信息": {
                "运行ID": result.run_id,
                "运行开始": _fmt_dt(result.run_started_at),
                "运行完成": _fmt_dt(result.run_completed_at),
                "输入采集时间": _fmt_dt(result.collected_at),
                "最新新闻发布时间": _fmt_dt(result.latest_news_at),
                "最新市场数据时间": _fmt_dt(result.latest_market_at),
                "审查时间": _fmt_dt(result.reviewed_at),
            },
            "发布状态": "已拒绝",
            "拒绝原因": result.rejection_reasons,
            "文件路径": result.artifact_paths,
        }

    forecast = result.final_forecast
    return {
        "运行信息": {
            "运行ID": result.run_id,
            "运行开始": _fmt_dt(result.run_started_at),
            "运行完成": _fmt_dt(result.run_completed_at),
            "输入采集时间": _fmt_dt(result.collected_at),
            "审查时间": _fmt_dt(result.reviewed_at),
            "最新新闻发布时间": _fmt_dt(result.latest_news_at),
            "最新市场数据时间": _fmt_dt(result.latest_market_at),
            "发布时间": _fmt_dt(forecast.generated_at.isoformat()),
        },
        "结论": {
            "预测周期": forecast.forecast_horizon,
            "方向判断": _bias_zh(forecast.directional_bias.value),
            "置信度": _percent(forecast.confidence),
            "反后验审查": _status_zh(forecast.anti_hindsight_status.value),
            "结论摘要": forecast.final_thesis,
        },
        "核心依据": {
            "核心驱动": forecast.dominant_drivers,
            "支持证据": forecast.supportive_evidence,
            "反对证据": forecast.opposing_evidence,
        },
        "条件结构": {
            "上行触发": forecast.upside_triggers,
            "下行触发": forecast.downside_triggers,
            "失效条件": forecast.invalidation_conditions,
            "重点监控": forecast.monitoring_list,
        },
        "文件路径": {
            "最终结果": result.artifact_paths.get("final_forecast"),
            "反后验审查": result.artifact_paths.get("anti_hindsight_review"),
            "输入时效检查": result.artifact_paths.get("input_freshness_report"),
            "置信度拆解": result.artifact_paths.get("confidence_breakdown"),
        },
    }


def _to_telegram_en(result: PipelineResult) -> dict[str, Any]:
    if result.final_forecast is None:
        return {
            "run": {
                "run_id": result.run_id,
                "run_started_at": _fmt_dt(result.run_started_at, "UTC"),
                "run_completed_at": _fmt_dt(result.run_completed_at, "UTC"),
                "collected_at": _fmt_dt(result.collected_at, "UTC"),
                "latest_news_at": _fmt_dt(result.latest_news_at, "UTC"),
                "latest_market_at": _fmt_dt(result.latest_market_at, "UTC"),
                "reviewed_at": _fmt_dt(result.reviewed_at, "UTC"),
            },
            "publish_status": "rejected",
            "rejection_reasons": result.rejection_reasons,
            "artifact_paths": result.artifact_paths,
        }

    forecast = result.final_forecast
    return {
        "run": {
            "run_id": result.run_id,
            "run_started_at": _fmt_dt(result.run_started_at, "UTC"),
            "run_completed_at": _fmt_dt(result.run_completed_at, "UTC"),
            "collected_at": _fmt_dt(result.collected_at, "UTC"),
            "reviewed_at": _fmt_dt(result.reviewed_at, "UTC"),
            "latest_news_at": _fmt_dt(result.latest_news_at, "UTC"),
            "latest_market_at": _fmt_dt(result.latest_market_at, "UTC"),
            "published_at": _fmt_dt(forecast.generated_at.isoformat(), "UTC"),
        },
        "conclusion": {
            "horizon": forecast.forecast_horizon,
            "directional_bias": forecast.directional_bias.value,
            "confidence": _percent(forecast.confidence),
            "anti_hindsight": forecast.anti_hindsight_status.value,
            "thesis": forecast.final_thesis,
        },
        "evidence": {
            "dominant_drivers": forecast.dominant_drivers,
            "supportive_evidence": forecast.supportive_evidence,
            "opposing_evidence": forecast.opposing_evidence,
        },
        "conditions": {
            "upside_triggers": forecast.upside_triggers,
            "downside_triggers": forecast.downside_triggers,
            "invalidation_conditions": forecast.invalidation_conditions,
            "monitoring_list": forecast.monitoring_list,
        },
        "artifact_paths": {
            "final_forecast": result.artifact_paths.get("final_forecast"),
            "anti_hindsight_review": result.artifact_paths.get("anti_hindsight_review"),
            "input_freshness_report": result.artifact_paths.get("input_freshness_report"),
            "confidence_breakdown": result.artifact_paths.get("confidence_breakdown"),
        },
    }


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
            "置信度拆解": result.artifact_paths.get("confidence_breakdown"),
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
            "confidence_breakdown": result.artifact_paths.get("confidence_breakdown"),
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
