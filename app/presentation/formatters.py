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
            "market_snapshot": result.market_snapshot,
            "news_snapshot": result.news_snapshot,
            "reasoning_summary": result.reasoning_summary,
            "state_snapshot": result.state_snapshot,
            "confidence_snapshot": result.confidence_snapshot,
            "runtime_assertions": result.runtime_assertions,
            "analysis_flow": result.analysis_flow,
            "analysis_variants": result.analysis_variants,
            "publish_gate_report": result.publish_gate_report,
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
            "市场信息": _market_snapshot_zh(result),
            "最新新闻": _news_snapshot_zh(result),
            "思维总结": _thinking_summary_telegram_zh(result),
            "运行断言": _runtime_assertions_zh(result),
            "发布门禁": result.publish_gate_report,
            "流程阶段": _analysis_flow_summary(result),
            "分析版本": result.analysis_variants,
            "判断摘要": result.reasoning_summary,
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
        "条件结构": _condition_structure_zh(result),
        "市场信息": _market_snapshot_zh(result),
        "最新新闻": _news_snapshot_zh(result),
        "思维总结": _thinking_summary_telegram_zh(result),
        "运行断言": _runtime_assertions_zh(result),
        "发布门禁": result.publish_gate_report,
        "流程阶段": _analysis_flow_summary(result),
        "判断摘要": result.reasoning_summary,
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
            "market_snapshot": _market_snapshot_en(result),
            "news_snapshot": _news_snapshot_en(result),
            "thinking_summary": _thinking_summary_telegram_en(result),
            "runtime_assertions": result.runtime_assertions,
            "publish_gate_report": result.publish_gate_report,
            "analysis_flow": _analysis_flow_summary(result),
            "analysis_variants": result.analysis_variants,
            "reasoning_summary": result.reasoning_summary,
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
        "conditions": _condition_structure_en(result),
        "market_snapshot": _market_snapshot_en(result),
        "news_snapshot": _news_snapshot_en(result),
        "thinking_summary": _thinking_summary_telegram_en(result),
        "runtime_assertions": result.runtime_assertions,
        "publish_gate_report": result.publish_gate_report,
        "analysis_flow": _analysis_flow_summary(result),
        "reasoning_summary": result.reasoning_summary,
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
            "市场信息": _market_snapshot_zh(result),
            "最新新闻": _news_snapshot_zh(result),
            "思维总结": _thinking_summary_zh(result),
            "运行断言": _runtime_assertions_zh(result),
            "发布门禁": result.publish_gate_report,
            "流程阶段": _analysis_flow_summary(result),
            "判断摘要": result.reasoning_summary,
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
        "市场信息": _market_snapshot_zh(result),
        "最新新闻": _news_snapshot_zh(result),
        "思维总结": _thinking_summary_zh(result),
        "运行断言": _runtime_assertions_zh(result),
        "发布门禁": result.publish_gate_report,
        "流程阶段": _analysis_flow_summary(result),
        "判断摘要": result.reasoning_summary,
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
            "market_snapshot": _market_snapshot_en(result),
            "news_snapshot": _news_snapshot_en(result),
            "thinking_summary": _thinking_summary_en(result),
            "runtime_assertions": result.runtime_assertions,
            "publish_gate_report": result.publish_gate_report,
            "analysis_flow": _analysis_flow_summary(result),
            "reasoning_summary": result.reasoning_summary,
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
        "market_snapshot": _market_snapshot_en(result),
        "news_snapshot": _news_snapshot_en(result),
        "thinking_summary": _thinking_summary_en(result),
        "runtime_assertions": result.runtime_assertions,
        "publish_gate_report": result.publish_gate_report,
        "analysis_flow": _analysis_flow_summary(result),
        "reasoning_summary": result.reasoning_summary,
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


def _market_snapshot_zh(result: PipelineResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol, payload in result.market_snapshot.items():
        rows.append(
            {
                "标的": symbol,
                "名称": payload.get("name"),
                "最新值": payload.get("value"),
                "日变动%": payload.get("change_pct"),
                "时间": _fmt_dt(payload.get("as_of")),
            }
        )
    return rows


def _market_snapshot_en(result: PipelineResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol, payload in result.market_snapshot.items():
        rows.append(
            {
                "symbol": symbol,
                "name": payload.get("name"),
                "value": payload.get("value"),
                "change_pct": payload.get("change_pct"),
                "as_of": _fmt_dt(payload.get("as_of"), "UTC"),
            }
        )
    return rows


def _news_snapshot_zh(result: PipelineResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in result.news_snapshot:
        age_minutes = _minutes_from_base(result.collected_at, item.get("published_at"))
        rows.append(
            {
                "来源": item.get("source"),
                "标题": item.get("headline"),
                "摘要": item.get("summary") or "",
                "发布时间": _fmt_dt(item.get("published_at")),
                "距采集分钟": age_minutes,
                "链接": item.get("url"),
            }
        )
    return rows


def _news_snapshot_en(result: PipelineResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in result.news_snapshot:
        age_minutes = _minutes_from_base(result.collected_at, item.get("published_at"))
        rows.append(
            {
                "source": item.get("source"),
                "headline": item.get("headline"),
                "summary": item.get("summary") or "",
                "published_at": _fmt_dt(item.get("published_at"), "UTC"),
                "minutes_since_collected": age_minutes,
                "url": item.get("url"),
            }
        )
    return rows


def _thinking_summary_zh(result: PipelineResult) -> list[str]:
    if result.final_forecast is None:
        reasons = "；".join(result.rejection_reasons[:2]) if result.rejection_reasons else "规则门禁未通过"
        return [f"本次未发布：{reasons}"] + result.reasoning_summary[:3]

    forecast = result.final_forecast
    drivers = "；".join(forecast.dominant_drivers[:2]) if forecast.dominant_drivers else "暂无主驱动"
    support = forecast.supportive_evidence[0] if forecast.supportive_evidence else "暂无支持证据"
    oppose = forecast.opposing_evidence[0] if forecast.opposing_evidence else "暂无反方证据"
    invalidation = (
        forecast.invalidation_conditions[0] if forecast.invalidation_conditions else "暂无失效条件"
    )
    return [
        f"方向={_bias_zh(forecast.directional_bias.value)}，置信度={_percent(forecast.confidence)}。",
        f"核心原因：{drivers}",
        f"支持证据：{support}",
        f"反方证据：{oppose}",
        f"失效条件：{invalidation}",
    ]


def _thinking_summary_en(result: PipelineResult) -> list[str]:
    if result.final_forecast is None:
        reasons = "; ".join(result.rejection_reasons[:2]) if result.rejection_reasons else "rule gate failed"
        return [f"Publish rejected: {reasons}"] + result.reasoning_summary[:3]

    forecast = result.final_forecast
    drivers = "; ".join(forecast.dominant_drivers[:2]) if forecast.dominant_drivers else "n/a"
    support = forecast.supportive_evidence[0] if forecast.supportive_evidence else "n/a"
    oppose = forecast.opposing_evidence[0] if forecast.opposing_evidence else "n/a"
    invalidation = forecast.invalidation_conditions[0] if forecast.invalidation_conditions else "n/a"
    return [
        f"Bias={forecast.directional_bias.value}, confidence={_percent(forecast.confidence)}.",
        f"Core reason: {drivers}",
        f"Support: {support}",
        f"Opposition: {oppose}",
        f"Invalidation: {invalidation}",
    ]


def _thinking_summary_telegram_zh(result: PipelineResult) -> dict[str, Any]:
    if result.final_forecast is None:
        return {
            "状态": "未发布",
            "拒绝原因": result.rejection_reasons,
            "过程摘要": result.reasoning_summary,
        }

    forecast = result.final_forecast
    state = result.state_snapshot
    confidence = result.confidence_snapshot
    top_scenario = (state.get("scenarios") or [None])[0]
    components = confidence.get("components", {})
    penalties = confidence.get("penalties", {})

    return {
        "结论路径": [
            f"输入层：收集新闻 {len(result.news_snapshot)} 条，市场指标 {len(result.market_snapshot)} 个。",
            f"状态层：{state.get('regime_label', 'n/a')}。",
            "审查层：已通过反后验审查与规则门禁。",
        ],
        "状态与情景": {
            "增长/通胀/流动性/波动": (
                f"{state.get('growth_state', 'n/a')} / "
                f"{state.get('inflation_state', 'n/a')} / "
                f"{state.get('liquidity_state', 'n/a')} / "
                f"{state.get('volatility_state', 'n/a')}"
            ),
            "主情景": (
                None
                if not top_scenario
                else {
                    "名称": top_scenario.get("name"),
                    "概率": _percent(float(top_scenario.get("probability", 0.0))),
                    "方向含义": top_scenario.get("directional_implication"),
                    "关键条件": top_scenario.get("key_conditions"),
                }
            ),
            "跨资产信号": state.get("cross_asset_signals", []),
        },
        "置信度拆解": {
            "最终置信度": _percent(forecast.confidence),
            "贡献项": {
                "场景一致性": components.get("scenario_alignment"),
                "事件一致性": components.get("event_consensus"),
                "跨资产确认": components.get("cross_asset_confirmation"),
                "证据平衡": components.get("evidence_balance"),
            },
            "惩罚项": {
                "新鲜度惩罚": penalties.get("freshness_penalty"),
                "风险惩罚": penalties.get("risk_penalty"),
            },
        },
        "支持与反证": {
            "支持证据": forecast.supportive_evidence,
            "反对证据": forecast.opposing_evidence,
            "失效条件": forecast.invalidation_conditions,
        },
        "过程摘要": result.reasoning_summary,
    }


def _thinking_summary_telegram_en(result: PipelineResult) -> dict[str, Any]:
    if result.final_forecast is None:
        return {
            "status": "rejected",
            "rejection_reasons": result.rejection_reasons,
            "trace": result.reasoning_summary,
        }

    forecast = result.final_forecast
    state = result.state_snapshot
    confidence = result.confidence_snapshot
    top_scenario = (state.get("scenarios") or [None])[0]
    components = confidence.get("components", {})
    penalties = confidence.get("penalties", {})

    return {
        "reasoning_path": [
            f"Input layer: {len(result.news_snapshot)} news items, {len(result.market_snapshot)} indicators.",
            f"State layer: {state.get('regime_label', 'n/a')}.",
            "Governance layer: anti-hindsight and rule gates passed.",
        ],
        "state_and_scenarios": {
            "growth_inflation_liquidity_volatility": (
                f"{state.get('growth_state', 'n/a')} / "
                f"{state.get('inflation_state', 'n/a')} / "
                f"{state.get('liquidity_state', 'n/a')} / "
                f"{state.get('volatility_state', 'n/a')}"
            ),
            "top_scenario": (
                None
                if not top_scenario
                else {
                    "name": top_scenario.get("name"),
                    "probability": _percent(float(top_scenario.get("probability", 0.0))),
                    "directional_implication": top_scenario.get("directional_implication"),
                    "key_conditions": top_scenario.get("key_conditions"),
                }
            ),
            "cross_asset_signals": state.get("cross_asset_signals", []),
        },
        "confidence_breakdown": {
            "final_confidence": _percent(forecast.confidence),
            "components": {
                "scenario_alignment": components.get("scenario_alignment"),
                "event_consensus": components.get("event_consensus"),
                "cross_asset_confirmation": components.get("cross_asset_confirmation"),
                "evidence_balance": components.get("evidence_balance"),
            },
            "penalties": {
                "freshness_penalty": penalties.get("freshness_penalty"),
                "risk_penalty": penalties.get("risk_penalty"),
            },
        },
        "support_vs_opposition": {
            "supportive_evidence": forecast.supportive_evidence,
            "opposing_evidence": forecast.opposing_evidence,
            "invalidation_conditions": forecast.invalidation_conditions,
        },
        "trace": result.reasoning_summary,
    }


def _analysis_flow_summary(result: PipelineResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for step in result.analysis_flow:
        if not isinstance(step, dict):
            continue
        rows.append(
            {
                "stage": step.get("stage"),
                "status": step.get("status"),
                "elapsed_seconds": step.get("elapsed_seconds"),
                "output_summary": step.get("output_summary"),
            }
        )
    return rows


def _runtime_assertions_zh(result: PipelineResult) -> dict[str, Any]:
    runtime_assertions = result.runtime_assertions or {}
    checks = runtime_assertions.get("checks", [])
    mapped_checks: list[dict[str, Any]] = []
    for item in checks:
        if not isinstance(item, dict):
            continue
        mapped_checks.append(
            {
                "检查项": item.get("name"),
                "通过": bool(item.get("passed")),
                "期望": item.get("expected"),
                "观测": item.get("observed"),
            }
        )
    return {
        "严格模式生效": bool(runtime_assertions.get("strict_mode_active")),
        "全部通过": bool(runtime_assertions.get("all_passed")),
        "LLM提供方": runtime_assertions.get("llm_provider"),
        "新闻来源": runtime_assertions.get("news_source"),
        "市场来源": runtime_assertions.get("market_source"),
        "检查": mapped_checks,
    }


def _condition_structure_zh(result: PipelineResult) -> dict[str, Any]:
    if result.final_forecast is None:
        return {}
    forecast = result.final_forecast
    return {
        "上行触发（满足项越多，越偏上行）": _condition_rows_zh(
            result, forecast.upside_triggers, "满足后，上调上行概率；若连续出现可提高风险偏好。"
        ),
        "下行触发（满足项越多，越偏下行）": _condition_rows_zh(
            result, forecast.downside_triggers, "满足后，上调下行概率；若连续出现需收敛风险暴露。"
        ),
        "失效条件（触发即降级/反转）": _condition_rows_zh(
            result, forecast.invalidation_conditions, "触发后应降级当前观点，必要时切换方向。"
        ),
        "重点监控（建议市场60分钟、新闻72小时更新）": _monitor_rows_zh(result, forecast.monitoring_list),
    }


def _condition_structure_en(result: PipelineResult) -> dict[str, Any]:
    if result.final_forecast is None:
        return {}
    forecast = result.final_forecast
    return {
        "upside_triggers": _condition_rows_en(
            result,
            forecast.upside_triggers,
            "If sustained, increase upside probability and allow more risk-on positioning.",
        ),
        "downside_triggers": _condition_rows_en(
            result,
            forecast.downside_triggers,
            "If sustained, increase downside probability and reduce risk exposure.",
        ),
        "invalidation_conditions": _condition_rows_en(
            result,
            forecast.invalidation_conditions,
            "If triggered, downgrade confidence or flip directional view after review.",
        ),
        "monitoring_list": _monitor_rows_en(result, forecast.monitoring_list),
    }


def _condition_rows_zh(result: PipelineResult, items: list[str], action: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        related_symbols = _related_symbols(item)
        rows.append(
            {
                "序号": index,
                "条件": item,
                "关联市场": related_symbols or ["多因子"],
                "当前观测": _market_observations_zh(result, related_symbols),
                "动作建议": action,
            }
        )
    return rows


def _condition_rows_en(result: PipelineResult, items: list[str], action: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        related_symbols = _related_symbols(item)
        rows.append(
            {
                "index": index,
                "condition": item,
                "related_markets": related_symbols or ["multi-factor"],
                "current_observation": _market_observations_en(result, related_symbols),
                "action": action,
            }
        )
    return rows


def _monitor_rows_zh(result: PipelineResult, items: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        related_symbols = _related_symbols(item)
        rows.append(
            {
                "序号": index,
                "监控项": item,
                "关联市场": related_symbols or ["多因子"],
                "当前观测": _market_observations_zh(result, related_symbols),
                "刷新建议": "市场<=60分钟；新闻<=72小时",
            }
        )
    return rows


def _monitor_rows_en(result: PipelineResult, items: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        related_symbols = _related_symbols(item)
        rows.append(
            {
                "index": index,
                "monitor": item,
                "related_markets": related_symbols or ["multi-factor"],
                "current_observation": _market_observations_en(result, related_symbols),
                "refresh_hint": "market<=60m; news<=72h",
            }
        )
    return rows


def _related_symbols(text: str) -> list[str]:
    normalized = text.lower()
    mappings: dict[str, list[str]] = {
        "SPY": ["spy", "标普", "s&p"],
        "QQQ": ["qqq", "纳指", "tech"],
        "IWM": ["iwm", "小盘", "russell"],
        "VIX": ["vix", "波动率", "volatility"],
        "US10Y": ["10y", "us10y", "利率", "收益率", "yield"],
        "DXY": ["dxy", "美元", "dollar"],
        "OIL": ["oil", "原油", "油价", "wti"],
    }
    matched: list[str] = []
    for symbol, aliases in mappings.items():
        if any(alias in normalized for alias in aliases):
            matched.append(symbol)
    return matched


def _market_observations_zh(result: PipelineResult, symbols: list[str]) -> list[str]:
    observations: list[str] = []
    for symbol in symbols:
        payload = result.market_snapshot.get(symbol)
        if not payload:
            continue
        observations.append(
            f"{symbol}: 值={payload.get('value')}，日变动={payload.get('change_pct')}%，时间={payload.get('as_of')}"
        )
    return observations if observations else ["暂无直接对应行情，按多因子综合判断"]


def _market_observations_en(result: PipelineResult, symbols: list[str]) -> list[str]:
    observations: list[str] = []
    for symbol in symbols:
        payload = result.market_snapshot.get(symbol)
        if not payload:
            continue
        observations.append(
            f"{symbol}: value={payload.get('value')}, change={payload.get('change_pct')}%, as_of={payload.get('as_of')}"
        )
    return observations if observations else ["No direct ticker match; use multi-factor context."]


def _minutes_from_base(base_dt_str: str | None, event_dt_str: str | None) -> float | None:
    base_dt = _parse_iso_datetime(base_dt_str)
    event_dt = _parse_iso_datetime(event_dt_str)
    if base_dt is None or event_dt is None:
        return None
    delta = base_dt - event_dt
    return round(delta.total_seconds() / 60.0, 1)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
