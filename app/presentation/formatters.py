"""Formatting helpers for CLI/API-friendly output payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.pipeline.orchestrator import PipelineResult

DEFAULT_MARKET_UNIVERSE: list[str] = [
    "SPY",
    "QQQ",
    "IWM",
    "VIX",
    "US10Y",
    "DXY",
    "OIL",
    "BTC",
    "USDJPY",
]


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
            "run_status": result.run_status,
            "is_publishable": result.is_publishable,
            "review_status": result.review_status,
            "decision_summary": result.decision_summary,
            "rejection_reasons": result.rejection_reasons,
            "review_summary": result.review_summary,
            "review_findings": result.review_findings,
            "reference_levels": result.reference_levels,
            "artifact_paths": result.artifact_paths,
            "collected_at": result.collected_at,
            "reviewed_at": result.reviewed_at,
            "latest_news_at": result.latest_news_at,
            "latest_market_at": result.latest_market_at,
            "run_started_at": result.run_started_at,
            "run_completed_at": result.run_completed_at,
            "market_universe": _expected_market_universe(result),
            "market_snapshot": result.market_snapshot,
            "news_snapshot": result.news_snapshot,
            "reasoning_summary": result.reasoning_summary,
            "state_snapshot": result.state_snapshot,
            "confidence_snapshot": result.confidence_snapshot,
            "runtime_assertions": result.runtime_assertions,
            "analysis_flow": result.analysis_flow,
            "analysis_variants": result.analysis_variants,
            "publish_gate_report": result.publish_gate_report,
            "market_snapshot_summary": result.market_snapshot_summary,
            "top_news_signals": result.top_news_signals,
            "top_market_signals": result.top_market_signals,
            "signal_conflicts": result.signal_conflicts,
            "forecast_support_map": result.forecast_support_map,
            "forecast_opposition_map": result.forecast_opposition_map,
            "monitoring_priorities": result.monitoring_priorities,
            "next_run_questions": result.next_run_questions,
            "pre_forecast_feedback": result.pre_forecast_feedback,
            "post_forecast_feedback": result.post_forecast_feedback,
            "factor_snapshot": result.factor_snapshot,
            "dominant_factor": result.dominant_factor,
            "dominant_factor_explainer": result.dominant_factor_explainer,
            "earnings_revision_proxy_summary": result.earnings_revision_proxy_summary,
            "earnings_proxy_source": result.earnings_proxy_source,
        }
        if result.final_forecast is not None:
            payload["final_forecast"] = result.final_forecast.model_dump(mode="json")
        return payload

    if style == "telegram":
        return _to_telegram_zh(result) if language == "zh" else _to_telegram_en(result)

    if language == "zh":
        return _to_simple_zh(result)
    return _to_simple_en(result)


def render_cli_output(
    result: PipelineResult,
    language: Literal["zh", "en"] = "zh",
    style: Literal["simple", "telegram", "full"] = "simple",
) -> str:
    """Render CLI output as readable text panels."""
    if language == "zh":
        if style == "telegram":
            return _render_telegram_zh_text(result)
        if style == "full":
            return _render_full_zh_text(result)
        return _render_simple_zh_text(result)

    if style == "telegram":
        return _render_telegram_en_text(result)
    if style == "full":
        return _render_full_en_text(result)
    return _render_simple_en_text(result)


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
    forecast = result.final_forecast
    conclusion: dict[str, Any] = {
        "运行ID": result.run_id,
        "运行开始": _fmt_dt(result.run_started_at),
        "运行完成": _fmt_dt(result.run_completed_at),
        "输入采集时间": _fmt_dt(result.collected_at),
        "审查时间": _fmt_dt(result.reviewed_at),
        "最新新闻发布时间": _fmt_dt(result.latest_news_at),
        "最新市场数据时间": _fmt_dt(result.latest_market_at),
        "运行状态": result.run_status,
        "审查状态": result.review_status,
        "可正式发布": result.is_publishable,
        "发布状态": "已通过" if result.is_publishable else "已拒绝",
        "主导因子": result.dominant_factor.get("dominant_factor"),
    }

    if forecast is None:
        conclusion.update(
            {
                "预测周期": None,
                "方向判断": None,
                "置信度": None,
                "结论摘要": "输入时效门禁未通过，未进入审查阶段。",
                "核心驱动": [],
                "支持证据": [],
                "反对证据": [],
            }
        )
    else:
        conclusion.update(
            {
                "发布时间": _fmt_dt(forecast.generated_at.isoformat()),
                "预测周期": forecast.forecast_horizon,
                "方向判断": _bias_zh(forecast.directional_bias.value),
                "置信度": _percent(forecast.confidence),
                "结论摘要": forecast.final_thesis,
                "核心驱动": forecast.dominant_drivers,
                "支持证据": forecast.supportive_evidence,
                "反对证据": forecast.opposing_evidence,
            }
        )

    payload: dict[str, Any] = {
        "结论": conclusion,
        "审查风险摘要": {
            "decision_summary": result.decision_summary,
            "review_summary": result.review_summary,
            "hard_fail_issues": result.review_findings.get("hard_fail_issues", []),
            "soft_warnings": result.review_findings.get("soft_warnings", []),
            "info_notes": result.review_findings.get("info_notes", []),
        },
    }

    if not result.is_publishable:
        payload["拒绝详情"] = {
            "rejection_reasons": result.rejection_reasons,
            "publish_gate_report": result.publish_gate_report,
            "analysis_variants": result.analysis_variants,
        }

    payload["监控与参考位"] = (
        {
            "上行触发": forecast.upside_triggers,
            "下行触发": forecast.downside_triggers,
            "失效条件": forecast.invalidation_conditions,
            "重点监控": forecast.monitoring_list,
            "reference_levels": forecast.reference_levels.model_dump(mode="json"),
        }
        if forecast is not None
        else {
            "上行触发": [],
            "下行触发": [],
            "失效条件": [],
            "重点监控": [],
            "reference_levels": result.reference_levels,
        }
    )
    payload["条件结构化观察"] = _condition_structure_zh(result)
    payload["五因子与主导"] = _factor_card_zh(result)
    payload["数据反馈层"] = {
        "market_snapshot_summary": _market_snapshot_summary_zh(result),
        "top_news_signals": result.top_news_signals,
        "top_market_signals": result.top_market_signals,
        "signal_conflicts": result.signal_conflicts,
        "forecast_support_map": result.forecast_support_map,
        "forecast_opposition_map": result.forecast_opposition_map,
        "monitoring_priorities": result.monitoring_priorities,
        "next_run_questions": result.next_run_questions,
    }
    payload["市场信息"] = _market_snapshot_zh(result)
    payload["最新新闻"] = _news_snapshot_zh(result)
    payload["思维总结"] = _thinking_summary_telegram_zh(result)
    payload["运行断言"] = _runtime_assertions_zh(result)
    payload["流程阶段"] = _analysis_flow_summary(result)
    payload["文件路径"] = {
        "最终结果": result.artifact_paths.get("final_forecast"),
        "反后验审查": result.artifact_paths.get("anti_hindsight_review"),
        "拒绝详情": result.artifact_paths.get("review_rejected"),
        "输入时效检查": result.artifact_paths.get("input_freshness_report"),
    }
    return payload


def _to_telegram_en(result: PipelineResult) -> dict[str, Any]:
    forecast = result.final_forecast
    conclusion: dict[str, Any] = {
        "run_id": result.run_id,
        "run_started_at": _fmt_dt(result.run_started_at, "UTC"),
        "run_completed_at": _fmt_dt(result.run_completed_at, "UTC"),
        "collected_at": _fmt_dt(result.collected_at, "UTC"),
        "reviewed_at": _fmt_dt(result.reviewed_at, "UTC"),
        "latest_news_at": _fmt_dt(result.latest_news_at, "UTC"),
        "latest_market_at": _fmt_dt(result.latest_market_at, "UTC"),
        "run_status": result.run_status,
        "review_status": result.review_status,
        "is_publishable": result.is_publishable,
        "publish_status": "approved" if result.is_publishable else "rejected",
        "dominant_factor": result.dominant_factor.get("dominant_factor"),
    }
    if forecast is None:
        conclusion.update(
            {
                "horizon": None,
                "directional_bias": None,
                "confidence": None,
                "thesis": "Input freshness gate failed before review stage.",
                "dominant_drivers": [],
                "supportive_evidence": [],
                "opposing_evidence": [],
            }
        )
    else:
        conclusion.update(
            {
                "published_at": _fmt_dt(forecast.generated_at.isoformat(), "UTC"),
                "horizon": forecast.forecast_horizon,
                "directional_bias": forecast.directional_bias.value,
                "confidence": _percent(forecast.confidence),
                "thesis": forecast.final_thesis,
                "dominant_drivers": forecast.dominant_drivers,
                "supportive_evidence": forecast.supportive_evidence,
                "opposing_evidence": forecast.opposing_evidence,
            }
        )

    payload: dict[str, Any] = {
        "conclusion": conclusion,
        "review_risk_summary": {
            "decision_summary": result.decision_summary,
            "review_summary": result.review_summary,
            "hard_fail_issues": result.review_findings.get("hard_fail_issues", []),
            "soft_warnings": result.review_findings.get("soft_warnings", []),
            "info_notes": result.review_findings.get("info_notes", []),
        },
    }
    if not result.is_publishable:
        payload["rejection_details"] = {
            "rejection_reasons": result.rejection_reasons,
            "publish_gate_report": result.publish_gate_report,
            "analysis_variants": result.analysis_variants,
        }
    payload["monitoring_and_reference_levels"] = (
        {
            "upside_triggers": forecast.upside_triggers,
            "downside_triggers": forecast.downside_triggers,
            "invalidation_conditions": forecast.invalidation_conditions,
            "monitoring_list": forecast.monitoring_list,
            "reference_levels": forecast.reference_levels.model_dump(mode="json"),
        }
        if forecast is not None
        else {
            "upside_triggers": [],
            "downside_triggers": [],
            "invalidation_conditions": [],
            "monitoring_list": [],
            "reference_levels": result.reference_levels,
        }
    )
    payload["condition_structure"] = _condition_structure_en(result)
    payload["five_factor_card"] = _factor_card_en(result)
    payload["data_feedback_layer"] = {
        "market_snapshot_summary": _market_snapshot_summary_en(result),
        "top_news_signals": result.top_news_signals,
        "top_market_signals": result.top_market_signals,
        "signal_conflicts": result.signal_conflicts,
        "forecast_support_map": result.forecast_support_map,
        "forecast_opposition_map": result.forecast_opposition_map,
        "monitoring_priorities": result.monitoring_priorities,
        "next_run_questions": result.next_run_questions,
    }
    payload["market_snapshot"] = _market_snapshot_en(result)
    payload["news_snapshot"] = _news_snapshot_en(result)
    payload["thinking_summary"] = _thinking_summary_telegram_en(result)
    payload["analysis_flow"] = _analysis_flow_summary(result)
    payload["artifact_paths"] = result.artifact_paths
    return payload


def _to_simple_zh(result: PipelineResult) -> dict[str, Any]:
    if result.final_forecast is None:
        reject_detail_path = result.artifact_paths.get("review_rejected") or result.artifact_paths.get(
            "input_rejected"
        )
        return {
            "运行ID": result.run_id,
            "发布状态": "已拒绝",
            "拒绝原因": result.rejection_reasons,
            "五因子与主导": _factor_card_zh(result),
            "数据反馈": {
                "market_snapshot_summary": _market_snapshot_summary_zh(result),
                "top_news_signals": result.top_news_signals,
                "top_market_signals": result.top_market_signals,
                "signal_conflicts": result.signal_conflicts,
                "forecast_support_map": result.forecast_support_map,
                "forecast_opposition_map": result.forecast_opposition_map,
                "monitoring_priorities": result.monitoring_priorities,
                "next_run_questions": result.next_run_questions,
            },
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
    status_text = "已通过" if result.is_publishable else "已拒绝"
    return {
        "运行ID": result.run_id,
        "发布状态": status_text,
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
        "五因子与主导": _factor_card_zh(result),
        "数据反馈": {
            "market_snapshot_summary": _market_snapshot_summary_zh(result),
            "top_news_signals": result.top_news_signals,
            "top_market_signals": result.top_market_signals,
            "signal_conflicts": result.signal_conflicts,
            "forecast_support_map": result.forecast_support_map,
            "forecast_opposition_map": result.forecast_opposition_map,
            "monitoring_priorities": result.monitoring_priorities,
            "next_run_questions": result.next_run_questions,
        },
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
            "five_factor_card": _factor_card_en(result),
            "data_feedback": {
                "market_snapshot_summary": _market_snapshot_summary_en(result),
                "top_news_signals": result.top_news_signals,
                "top_market_signals": result.top_market_signals,
                "signal_conflicts": result.signal_conflicts,
                "forecast_support_map": result.forecast_support_map,
                "forecast_opposition_map": result.forecast_opposition_map,
                "monitoring_priorities": result.monitoring_priorities,
                "next_run_questions": result.next_run_questions,
            },
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
    status_text = "approved" if result.is_publishable else "rejected"
    return {
        "run_id": result.run_id,
        "publish_status": status_text,
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
        "five_factor_card": _factor_card_en(result),
        "data_feedback": {
            "market_snapshot_summary": _market_snapshot_summary_en(result),
            "top_news_signals": result.top_news_signals,
            "top_market_signals": result.top_market_signals,
            "signal_conflicts": result.signal_conflicts,
            "forecast_support_map": result.forecast_support_map,
            "forecast_opposition_map": result.forecast_opposition_map,
            "monitoring_priorities": result.monitoring_priorities,
            "next_run_questions": result.next_run_questions,
        },
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


def _expected_market_universe(result: PipelineResult) -> list[str]:
    if result.market_universe:
        return list(result.market_universe)
    if result.final_forecast is not None and result.final_forecast.market_universe:
        return list(result.final_forecast.market_universe)
    if result.market_snapshot:
        return list(result.market_snapshot.keys())
    return list(DEFAULT_MARKET_UNIVERSE)


def _build_market_summary_line_zh(symbol: str, payload: dict[str, Any] | None) -> str:
    if not payload:
        return f"{symbol}: 数据缺失（本轮未采集到该标的行情）"
    return (
        f"{symbol}: 最新值={payload.get('value')}, 日变动={payload.get('change_pct')}%, "
        f"时间={payload.get('as_of')}"
    )


def _build_market_summary_line_en(symbol: str, payload: dict[str, Any] | None) -> str:
    if not payload:
        return f"{symbol}: data missing (not collected in this run)"
    return (
        f"{symbol}: value={payload.get('value')}, daily_change={payload.get('change_pct')}%, "
        f"as_of={payload.get('as_of')}"
    )


def _market_snapshot_summary_zh(result: PipelineResult) -> list[str]:
    summary = [item for item in result.market_snapshot_summary if isinstance(item, str) and item.strip()]
    universe = _expected_market_universe(result)
    lowered = [item.lower() for item in summary]
    for symbol in universe:
        if any(symbol.lower() in item for item in lowered):
            continue
        summary.append(_build_market_summary_line_zh(symbol, result.market_snapshot.get(symbol)))
    return summary


def _market_snapshot_summary_en(result: PipelineResult) -> list[str]:
    summary = [item for item in result.market_snapshot_summary if isinstance(item, str) and item.strip()]
    universe = _expected_market_universe(result)
    lowered = [item.lower() for item in summary]
    for symbol in universe:
        if any(symbol.lower() in item for item in lowered):
            continue
        summary.append(_build_market_summary_line_en(symbol, result.market_snapshot.get(symbol)))
    return summary


def _equity_impact_meaning_zh(direction: str | None) -> str:
    mapping = {
        "up": "对权益偏多（美股）",
        "down": "对权益偏空（美股）",
        "neutral": "对权益中性（美股）",
        "mixed": "对权益信号混合（美股）",
    }
    return mapping.get(str(direction or "").lower(), "对美股影响未定义")


def _equity_impact_meaning_en(direction: str | None) -> str:
    mapping = {
        "up": "bullish impact on US equities",
        "down": "bearish impact on US equities",
        "neutral": "neutral impact on US equities",
        "mixed": "mixed impact on US equities",
    }
    return mapping.get(str(direction or "").lower(), "impact undefined")


def _factor_logic_note_zh(factor_name: str) -> str:
    notes = {
        "earnings_revision": "盈利预期上修通常支撑估值与风险偏好。",
        "volatility": "VIX回落通常对应风险偏好改善，VIX上行通常压制权益。",
        "rates": "注意：这里的方向是“对权益影响”，不是“收益率本身方向”。",
        "dollar": "美元走强通常抬升全球融资压力，美元走弱相对有利风险资产。",
        "energy_geopolitics": "油价与地缘风险上行通常抬升风险溢价，压制权益表现。",
    }
    return notes.get(factor_name, "基于当前可观测输入进行方向归因。")


def _factor_logic_note_en(factor_name: str) -> str:
    notes = {
        "earnings_revision": "Upward earnings revisions usually support equity valuation and risk appetite.",
        "volatility": "Lower VIX usually indicates improving risk appetite, while rising VIX pressures equities.",
        "rates": "Direction here means equity impact, not the raw yield direction itself.",
        "dollar": "A stronger dollar tends to tighten global financial conditions for risk assets.",
        "energy_geopolitics": "Higher oil/geopolitical risk usually raises risk premium and pressures equities.",
    }
    return notes.get(factor_name, "Direction is inferred from currently observable cross-asset inputs.")


def _factor_card_zh(result: PipelineResult) -> dict[str, Any]:
    snapshot = result.factor_snapshot or {}
    dominant = result.dominant_factor or {}
    factor_names = [
        "earnings_revision",
        "volatility",
        "rates",
        "dollar",
        "energy_geopolitics",
    ]
    factor_rows: list[dict[str, Any]] = []
    for name in factor_names:
        payload = snapshot.get(name, {})
        if not isinstance(payload, dict):
            continue
        direction = payload.get("direction")
        factor_rows.append(
            {
                "因子": name,
                "方向": direction,
                "对权益含义": _equity_impact_meaning_zh(direction),
                "分数": payload.get("score"),
                "强度": payload.get("strength"),
                "证据": payload.get("evidence_refs", []),
                "限制": payload.get("limitations", []),
                "时间": _fmt_dt(payload.get("as_of")),
                "逻辑说明": _factor_logic_note_zh(name),
            }
        )

    scoreboard = dominant.get("scoreboard", {})
    ranked = sorted(
        ((name, float(score), abs(float(score))) for name, score in scoreboard.items()),
        key=lambda item: item[2],
        reverse=True,
    )
    return {
        "主导因子": dominant.get("dominant_factor"),
        "并列主导": dominant.get("dominant_factors", []),
        "并列触发": bool(dominant.get("tie_detected")),
        "记分板(weight*score)": scoreboard,
        "主导排序(abs(weight*score))": [
            {
                "rank": index,
                "factor": name,
                "weighted_score": score,
                "abs_weighted_score": abs_score,
            }
            for index, (name, score, abs_score) in enumerate(ranked, start=1)
        ],
        "解释": result.dominant_factor_explainer,
        "盈利修正代理摘要": {
            "source": result.earnings_proxy_source,
            "signal": result.earnings_revision_proxy_summary.get("signal"),
            "score": result.earnings_revision_proxy_summary.get("score"),
            "summary": result.earnings_revision_proxy_summary.get("summary"),
            "limitations": result.earnings_revision_proxy_summary.get("limitations", []),
        },
        "因子明细": factor_rows,
    }


def _factor_card_en(result: PipelineResult) -> dict[str, Any]:
    snapshot = result.factor_snapshot or {}
    dominant = result.dominant_factor or {}
    factor_names = [
        "earnings_revision",
        "volatility",
        "rates",
        "dollar",
        "energy_geopolitics",
    ]
    factor_rows: list[dict[str, Any]] = []
    for name in factor_names:
        payload = snapshot.get(name, {})
        if not isinstance(payload, dict):
            continue
        direction = payload.get("direction")
        factor_rows.append(
            {
                "factor": name,
                "direction": direction,
                "equity_impact_meaning": _equity_impact_meaning_en(direction),
                "score": payload.get("score"),
                "strength": payload.get("strength"),
                "evidence": payload.get("evidence_refs", []),
                "limitations": payload.get("limitations", []),
                "as_of": _fmt_dt(payload.get("as_of"), "UTC"),
                "logic_note": _factor_logic_note_en(name),
            }
        )

    scoreboard = dominant.get("scoreboard", {})
    ranked = sorted(
        ((name, float(score), abs(float(score))) for name, score in scoreboard.items()),
        key=lambda item: item[2],
        reverse=True,
    )
    return {
        "dominant_factor": dominant.get("dominant_factor"),
        "dominant_factors": dominant.get("dominant_factors", []),
        "tie_detected": bool(dominant.get("tie_detected")),
        "scoreboard(weight*score)": scoreboard,
        "dominance_rank(abs(weight*score))": [
            {
                "rank": index,
                "factor": name,
                "weighted_score": score,
                "abs_weighted_score": abs_score,
            }
            for index, (name, score, abs_score) in enumerate(ranked, start=1)
        ],
        "explainer": result.dominant_factor_explainer,
        "earnings_revision_proxy": {
            "source": result.earnings_proxy_source,
            "signal": result.earnings_revision_proxy_summary.get("signal"),
            "score": result.earnings_revision_proxy_summary.get("score"),
            "summary": result.earnings_revision_proxy_summary.get("summary"),
            "limitations": result.earnings_revision_proxy_summary.get("limitations", []),
        },
        "factors": factor_rows,
    }


def _market_snapshot_zh(result: PipelineResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol in _expected_market_universe(result):
        payload = result.market_snapshot.get(symbol)
        if payload is None:
            rows.append(
                {
                    "标的": symbol,
                    "名称": "数据缺失",
                    "最新值": None,
                    "日变动%": None,
                    "时间": None,
                    "状态": "本轮未采集到该标的行情",
                }
            )
            continue
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
    for symbol in _expected_market_universe(result):
        payload = result.market_snapshot.get(symbol)
        if payload is None:
            rows.append(
                {
                    "symbol": symbol,
                    "name": "data_missing",
                    "value": None,
                    "change_pct": None,
                    "as_of": None,
                    "status": "not collected in this run",
                }
            )
            continue
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
                "来源类型": item.get("source_type"),
                "来源可信度": item.get("source_reliability"),
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
                "source_type": item.get("source_type"),
                "source_reliability": item.get("source_reliability"),
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
        f"主导因子：{result.dominant_factor.get('dominant_factor') or 'n/a'}",
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
        f"Dominant factor: {result.dominant_factor.get('dominant_factor') or 'n/a'}",
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

    governance_line = (
        "审查层：通过治理门禁，可进入正式发布层。"
        if result.is_publishable
        else "审查层：存在治理风险，保留分析但不进入正式发布层。"
    )
    return {
        "结论路径": [
            f"输入层：收集新闻 {len(result.news_snapshot)} 条，市场指标 {len(result.market_snapshot)} 个。",
            f"状态层：{state.get('regime_label', 'n/a')}。",
            governance_line,
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

    governance_line = (
        "Governance layer: checks passed and output is publishable."
        if result.is_publishable
        else "Governance layer: risks found; analysis retained but not publishable."
    )
    return {
        "reasoning_path": [
            f"Input layer: {len(result.news_snapshot)} news items, {len(result.market_snapshot)} indicators.",
            f"State layer: {state.get('regime_label', 'n/a')}.",
            governance_line,
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
        "BTC": ["btc", "bitcoin", "比特币", "crypto"],
        "USDJPY": ["usdjpy", "usd/jpy", "美元日元", "yen", "日元"],
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


def _list_preview(items: list[str], limit: int = 4) -> str:
    if not items:
        return "无"
    if len(items) <= limit:
        return "；".join(items)
    return f"{'；'.join(items[:limit])}；等{len(items)}项"


def _dt_text(dt_value: str | None, tz_name: str = "Asia/Shanghai") -> str:
    payload = _fmt_dt(dt_value, tz_name)
    if payload is None:
        return "n/a"
    return f"{payload['local']} | UTC {payload['utc']}"


def _render_section(title: str, rows: list[str]) -> list[str]:
    lines = [f"[{title}]"]
    lines.extend(rows if rows else ["- n/a"])
    return lines + [""]


def _render_signal_detail_rows_zh(
    title: str,
    signals: list[dict[str, Any]],
    *,
    max_items: int = 6,
) -> list[str]:
    rows: list[str] = [f"- {title}（{len(signals)}条）:"]
    if not signals:
        rows.append("  1. 无")
        return rows
    for index, signal in enumerate(signals[:max_items], start=1):
        signal_text = str(signal.get("signal") or "").strip()
        direction = signal.get("direction")
        confidence = signal.get("confidence")
        rationale = str(signal.get("rationale") or "").strip()
        refs = signal.get("evidence_refs", [])
        ref_text = "；".join(str(item) for item in refs[:4]) if isinstance(refs, list) and refs else "无"
        rows.append(
            f"  {index}. {signal_text} | direction={direction} | confidence={confidence}"
        )
        rows.append(f"     理由: {rationale or '无'}")
        rows.append(f"     证据引用: {ref_text}")
    return rows


def _render_list_detail_rows_zh(
    title: str,
    items: list[str],
    *,
    max_items: int = 8,
) -> list[str]:
    rows: list[str] = [f"- {title}（{len(items)}项）:"]
    if not items:
        rows.append("  1. 无")
        return rows
    for index, item in enumerate(items[:max_items], start=1):
        rows.append(f"  {index}. {item}")
    return rows


def _render_telegram_zh_text(result: PipelineResult) -> str:
    forecast = result.final_forecast
    lines: list[str] = []
    conclusion_rows = [
        f"- 运行ID: {result.run_id}",
        f"- 发布状态: {'已通过' if result.is_publishable else '已拒绝'} | 运行状态: {result.run_status}",
        f"- 主导因子: {result.dominant_factor.get('dominant_factor') or 'n/a'}",
        f"- 输入采集时间: {_dt_text(result.collected_at)}",
        f"- 审查时间: {_dt_text(result.reviewed_at)}",
        f"- 最新新闻发布时间: {_dt_text(result.latest_news_at)}",
        f"- 最新市场数据时间: {_dt_text(result.latest_market_at)}",
    ]
    if forecast is not None:
        conclusion_rows.extend(
            [
                f"- 预测周期: {forecast.forecast_horizon}",
                f"- 方向判断: {_bias_zh(forecast.directional_bias.value)} | 置信度: {_percent(forecast.confidence)}",
                f"- 结论摘要: {forecast.final_thesis}",
            ]
        )
    else:
        conclusion_rows.append("- 预测结果: 无（输入时效门禁未通过）")
    lines.extend(_render_section("结论", conclusion_rows))

    factor_card = _factor_card_zh(result)
    factor_rows = [
        f"- 主导因子: {factor_card.get('主导因子')} | 并列主导: {','.join(factor_card.get('并列主导', [])) or '无'}",
        f"- 主导解释: {factor_card.get('解释')}",
    ]
    for row in factor_card.get("因子明细", []):
        factor_rows.append(
            f"- {row.get('因子')}: 方向={row.get('方向')}（{row.get('对权益含义')}）, "
            f"分数={row.get('分数')}, 强度={row.get('强度')}"
        )
        factor_rows.append(f"  逻辑: {row.get('逻辑说明')}")
        factor_rows.append(f"  证据: {_list_preview(row.get('证据', []), limit=3)}")
        factor_rows.append(f"  限制: {_list_preview(row.get('限制', []), limit=2)}")
    lines.extend(_render_section("五因子真实表现", factor_rows))

    market_rows: list[str] = []
    for row in _market_snapshot_zh(result):
        status = row.get("状态")
        status_text = f" | 状态={status}" if status else ""
        market_rows.append(
            f"- {row.get('标的')}: 值={row.get('最新值')}, 日变动={row.get('日变动%')}%, "
            f"时间={_dt_text(row.get('时间', {}).get('utc') if isinstance(row.get('时间'), dict) else None)}{status_text}"
        )
    lines.extend(_render_section("市场快照（9标的）", market_rows))

    observation_rows: list[str] = []
    if forecast is not None:
        observation_rows.append(f"- 观察结论: {forecast.final_thesis}")
        observation_rows.extend(_render_list_detail_rows_zh("支持证据", forecast.supportive_evidence))
        observation_rows.extend(_render_list_detail_rows_zh("反向证据", forecast.opposing_evidence))
    observation_rows.extend(_render_list_detail_rows_zh("市场摘要", _market_snapshot_summary_zh(result), max_items=9))
    lines.extend(_render_section("关键观察", observation_rows))

    condition_rows: list[str] = []
    condition_structure = _condition_structure_zh(result)
    for group_name, items in condition_structure.items():
        condition_rows.append(f"- {group_name}:")
        for item in items:
            condition_rows.append(
                f"  {item.get('序号')}. {item.get('条件', item.get('监控项'))} | "
                f"关联={','.join(item.get('关联市场', []))} | "
                f"当前观测={_list_preview(item.get('当前观测', []), limit=2)}"
            )
    lines.extend(_render_section("条件结构（可执行观察）", condition_rows))

    review_rows = [
        f"- decision_summary: {result.decision_summary or 'n/a'}",
        f"- review_summary: {result.review_summary or 'n/a'}",
    ]
    review_rows.extend(_render_list_detail_rows_zh("reject_reasons", result.rejection_reasons, max_items=6))
    review_rows.extend(
        [
        f"- hard_fail_count: {len(result.review_findings.get('hard_fail_issues', []))}",
        f"- soft_warn_count: {len(result.review_findings.get('soft_warnings', []))}",
        ]
    )
    lines.extend(_render_section("审查与门禁", review_rows))

    feedback_rows: list[str] = []
    feedback_rows.extend(_render_signal_detail_rows_zh("顶层新闻信号", result.top_news_signals))
    feedback_rows.extend(_render_signal_detail_rows_zh("顶层市场信号", result.top_market_signals))
    feedback_rows.extend(_render_list_detail_rows_zh("信号冲突", result.signal_conflicts))
    feedback_rows.extend(_render_list_detail_rows_zh("预测支持映射", result.forecast_support_map))
    feedback_rows.extend(_render_list_detail_rows_zh("预测反对映射", result.forecast_opposition_map))
    feedback_rows.extend(_render_list_detail_rows_zh("监控优先级", result.monitoring_priorities))
    feedback_rows.extend(_render_list_detail_rows_zh("下一轮问题", result.next_run_questions))
    lines.extend(_render_section("数据反馈层", feedback_rows))

    runtime_rows = [
        f"- 运行开始: {_dt_text(result.run_started_at)}",
        f"- 运行完成: {_dt_text(result.run_completed_at)}",
        f"- 严格模式: {bool(result.runtime_assertions.get('strict_mode_active'))}",
        f"- 断言全通过: {bool(result.runtime_assertions.get('all_passed'))}",
        f"- 新闻来源: {result.runtime_assertions.get('news_source')}",
        f"- 市场来源: {result.runtime_assertions.get('market_source')}",
    ]
    lines.extend(_render_section("运行与时效信息", runtime_rows))
    return "\n".join(lines).rstrip()


def _render_simple_zh_text(result: PipelineResult) -> str:
    return _render_telegram_zh_text(result)


def _render_full_zh_text(result: PipelineResult) -> str:
    base = _render_telegram_zh_text(result)
    flow_rows = [f"- {item.get('stage')}: {item.get('status')} ({item.get('elapsed_seconds')}s)" for item in result.analysis_flow]
    return f"{base}\n\n[流程阶段]\n" + ("\n".join(flow_rows) if flow_rows else "- n/a")


def _render_telegram_en_text(result: PipelineResult) -> str:
    forecast = result.final_forecast
    lines = [
        "[Conclusion]",
        f"- run_id: {result.run_id}",
        f"- publish_status: {'approved' if result.is_publishable else 'rejected'} | run_status: {result.run_status}",
        f"- dominant_factor: {result.dominant_factor.get('dominant_factor') or 'n/a'}",
    ]
    if forecast is not None:
        lines.append(
            f"- horizon={forecast.forecast_horizon}, bias={forecast.directional_bias.value}, confidence={_percent(forecast.confidence)}"
        )
        lines.append(f"- thesis: {forecast.final_thesis}")
    lines.extend(
        [
            "",
            "[Market Snapshot]",
        ]
    )
    for row in _market_snapshot_en(result):
        lines.append(
            f"- {row.get('symbol')}: value={row.get('value')}, change={row.get('change_pct')}%, status={row.get('status', 'ok')}"
        )
    return "\n".join(lines).rstrip()


def _render_simple_en_text(result: PipelineResult) -> str:
    return _render_telegram_en_text(result)


def _render_full_en_text(result: PipelineResult) -> str:
    base = _render_telegram_en_text(result)
    flow_rows = [f"- {item.get('stage')}: {item.get('status')} ({item.get('elapsed_seconds')}s)" for item in result.analysis_flow]
    return f"{base}\n\n[Analysis Flow]\n" + ("\n".join(flow_rows) if flow_rows else "- n/a")


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
