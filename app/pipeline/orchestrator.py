"""Main orchestrator for a single strictly forward-looking forecast run."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from app.collectors.market_data import collect_market_data
from app.collectors.news import collect_news
from app.config import Settings
from app.llm_client import BaseLLMClient, build_llm_client
from app.pipeline.check_freshness import build_input_freshness_report
from app.pipeline.confidence import compute_confidence
from app.pipeline.extract_events import run_event_extraction
from app.pipeline.map_states import run_state_and_forecast
from app.pipeline.normalize import normalize_inputs
from app.pipeline.publish_forecast import select_publishable_forecast
from app.pipeline.review_forecast import run_anti_hindsight_review
from app.rules.schema_check import build_rule_report, validate_forecast_rules
from app.rules.validators import repair_forecast_payload
from app.schemas import (
    AntiHindsightStatus,
    FinalForecast,
    InputFreshnessReport,
    NormalizedInputs,
    RuleCheckReport,
    StateMappingResult,
)
from app.storage.db import Storage
from app.utils.prompt_loader import PromptLoader

NewsCollector = Callable[[Settings, str | None], tuple[list[dict[str, Any]], str]]
MarketDataCollector = Callable[[Settings, str | None], tuple[list[dict[str, Any]], str]]


@dataclass
class PipelineResult:
    """Return object used by CLI and API layers."""

    run_id: str
    final_forecast: FinalForecast | None
    artifact_paths: dict[str, str]
    publish_status: Literal["approved", "rejected"]
    rejection_reasons: list[str] = field(default_factory=list)
    collected_at: str | None = None
    reviewed_at: str | None = None
    latest_news_at: str | None = None
    latest_market_at: str | None = None
    run_started_at: str | None = None
    run_completed_at: str | None = None
    market_snapshot: dict[str, dict[str, Any]] = field(default_factory=dict)
    news_snapshot: list[dict[str, str]] = field(default_factory=list)
    reasoning_summary: list[str] = field(default_factory=list)


@dataclass
class PipelineDependencies:
    """Injectable dependencies to improve testability and separation."""

    news_collector: NewsCollector = collect_news
    market_data_collector: MarketDataCollector = collect_market_data
    llm_client: BaseLLMClient | None = None
    storage: Storage | None = None
    prompt_loader: PromptLoader | None = None


def _rule_issues_to_reasons(rule_report: RuleCheckReport) -> list[str]:
    return [f"{item.code}: {item.message}" for item in rule_report.issues]


def _can_use_latest_available_fallback(
    freshness_report: InputFreshnessReport,
    settings: Settings,
) -> tuple[bool, list[str]]:
    if freshness_report.news_items_checked == 0:
        return False, ["Latest-available fallback unavailable: no news items found"]
    if freshness_report.market_items_checked == 0:
        return False, ["Latest-available fallback unavailable: no market items found"]

    reasons: list[str] = []

    news_age_cap_minutes = settings.latest_available_max_news_age_hours * 60
    market_age_cap_minutes = settings.latest_available_max_market_age_minutes

    too_old_news = [
        item
        for item in freshness_report.stale_news
        if news_age_cap_minutes > 0 and item.age_minutes > news_age_cap_minutes
    ]
    too_old_market = [
        item
        for item in freshness_report.stale_market
        if market_age_cap_minutes > 0 and item.age_minutes > market_age_cap_minutes
    ]

    if too_old_news:
        reasons.append(
            f"Latest-available fallback denied: {len(too_old_news)} news items exceed "
            f"{news_age_cap_minutes:.1f} minutes cap"
        )
    if too_old_market:
        reasons.append(
            f"Latest-available fallback denied: {len(too_old_market)} market items exceed "
            f"{market_age_cap_minutes:.1f} minutes cap"
        )

    return (len(reasons) == 0), reasons


def _build_market_snapshot(
    normalized_inputs: NormalizedInputs,
    market_universe: list[str],
) -> dict[str, dict[str, Any]]:
    indicator_map = {item.symbol: item for item in normalized_inputs.indicators}
    snapshot: dict[str, dict[str, Any]] = {}
    for symbol in market_universe:
        indicator = indicator_map.get(symbol)
        if indicator is None:
            continue
        snapshot[symbol] = {
            "name": indicator.name,
            "value": round(float(indicator.value), 4),
            "change_pct": (
                None if indicator.change_pct is None else round(float(indicator.change_pct), 4)
            ),
            "as_of": indicator.as_of.isoformat(),
        }
    return snapshot


def _build_news_snapshot(normalized_inputs: NormalizedInputs, top_k: int = 5) -> list[dict[str, str]]:
    ranked_news = sorted(normalized_inputs.news, key=lambda item: item.published_at, reverse=True)[:top_k]
    return [
        {
            "source": item.source,
            "headline": item.headline,
            "published_at": item.published_at.isoformat(),
        }
        for item in ranked_news
    ]


def _event_direction_counts(structured_events_payload: dict[str, Any]) -> dict[str, int]:
    counts = {"up": 0, "down": 0, "neutral": 0}
    events = structured_events_payload.get("events", [])
    if not isinstance(events, list):
        return counts
    for event in events:
        if not isinstance(event, dict):
            continue
        impact_bias = str(event.get("impact_bias", "neutral")).lower()
        if impact_bias in counts:
            counts[impact_bias] += 1
    return counts


def _append_state_reasoning_summary(
    reasoning_summary: list[str],
    state_mapping: StateMappingResult,
    structured_events_payload: dict[str, Any],
    confidence_breakdown_payload: dict[str, Any],
) -> None:
    reasoning_summary.append(f"状态映射: {state_mapping.regime_label}")
    if state_mapping.scenarios:
        top = max(state_mapping.scenarios, key=lambda item: float(item.probability))
        reasoning_summary.append(
            f"主情景: {top.name} ({top.probability * 100:.0f}%), 方向={top.directional_implication.value}"
        )

    counts = _event_direction_counts(structured_events_payload)
    reasoning_summary.append(
        f"事件方向统计: 上行={counts['up']} 下行={counts['down']} 中性={counts['neutral']}"
    )

    components = confidence_breakdown_payload.get("components", {})
    penalties = confidence_breakdown_payload.get("penalties", {})
    reasoning_summary.append(
        "置信度拆解: "
        f"场景一致性={components.get('scenario_alignment')} "
        f"事件一致性={components.get('event_consensus')} "
        f"跨资产确认={components.get('cross_asset_confirmation')} "
        f"证据平衡={components.get('evidence_balance')} "
        f"新鲜度惩罚={penalties.get('freshness_penalty')} "
        f"风险惩罚={penalties.get('risk_penalty')}"
    )


def run_pipeline(
    settings: Settings,
    news_file: str | None = None,
    market_file: str | None = None,
    forecast_horizon: str | None = None,
    market_universe: list[str] | None = None,
    max_news_age_hours: int | None = None,
    max_market_age_minutes: int | None = None,
    enforce_input_freshness: bool | None = None,
    dependencies: PipelineDependencies | None = None,
) -> PipelineResult:
    """Run the full pipeline end-to-end once."""

    deps = dependencies or PipelineDependencies()
    owns_storage = deps.storage is None
    storage = deps.storage or Storage(settings)
    storage.init_db()

    llm_client = deps.llm_client or build_llm_client(settings)
    prompt_loader = deps.prompt_loader or PromptLoader(settings.prompts_dir)

    horizon = forecast_horizon or settings.forecast_horizon
    universe = market_universe or settings.market_universe
    news_age_limit = settings.max_news_age_hours if max_news_age_hours is None else max_news_age_hours
    market_age_limit = (
        settings.max_market_age_minutes
        if max_market_age_minutes is None
        else max_market_age_minutes
    )
    freshness_gate_enabled = (
        settings.enforce_input_freshness
        if enforce_input_freshness is None
        else enforce_input_freshness
    )
    allow_latest_available_fallback = settings.allow_latest_available_fallback

    run_started_at_dt = datetime.now(timezone.utc)
    run_id = storage.create_run(forecast_horizon=horizon, market_universe=universe)
    artifact_paths: dict[str, str] = {}
    collected_at: str | None = None
    reviewed_at: str | None = None
    latest_news_at: str | None = None
    latest_market_at: str | None = None
    latest_available_fallback_applied = False
    market_snapshot: dict[str, dict[str, Any]] = {}
    news_snapshot: list[dict[str, str]] = []
    reasoning_summary: list[str] = []

    try:
        raw_news_items, news_source = deps.news_collector(settings, news_file)
        raw_market_indicators, market_source = deps.market_data_collector(settings, market_file)

        artifact_paths["news_raw"] = str(
            storage.save_artifact(
                run_id,
                stage="raw",
                artifact_name="news_raw.json",
                payload={"source": news_source, "items": raw_news_items},
            )
        )
        artifact_paths["market_raw"] = str(
            storage.save_artifact(
                run_id,
                stage="raw",
                artifact_name="market_indicators_raw.json",
                payload={"source": market_source, "items": raw_market_indicators},
            )
        )

        normalized_inputs = normalize_inputs(
            run_id=run_id,
            forecast_horizon=horizon,
            market_universe=universe,
            raw_news_items=raw_news_items,
            raw_market_indicators=raw_market_indicators,
        )
        artifact_paths["normalized_inputs"] = str(
            storage.save_artifact(
                run_id,
                stage="intermediate",
                artifact_name="normalized_inputs.json",
                payload=normalized_inputs.model_dump(mode="json"),
            )
        )
        collected_at = normalized_inputs.collected_at.isoformat()
        latest_news_item = max((item.published_at for item in normalized_inputs.news), default=None)
        latest_market_item = max((item.as_of for item in normalized_inputs.indicators), default=None)
        latest_news_at = latest_news_item.isoformat() if latest_news_item else None
        latest_market_at = latest_market_item.isoformat() if latest_market_item else None
        market_snapshot = _build_market_snapshot(normalized_inputs, universe)
        news_snapshot = _build_news_snapshot(normalized_inputs)
        reasoning_summary.append(
            f"输入概览: 新闻{len(normalized_inputs.news)}条, 市场指标{len(normalized_inputs.indicators)}个"
        )

        freshness_report = build_input_freshness_report(
            normalized_inputs=normalized_inputs,
            max_news_age_hours=news_age_limit,
            max_market_age_minutes=market_age_limit,
        )
        artifact_paths["input_freshness_report"] = str(
            storage.save_artifact(
                run_id,
                stage="intermediate",
                artifact_name="input_freshness_report.json",
                payload=freshness_report.model_dump(mode="json"),
            )
        )
        if freshness_gate_enabled and freshness_report.has_blocking_issues:
            if allow_latest_available_fallback:
                fallback_allowed, fallback_reasons = _can_use_latest_available_fallback(
                    freshness_report=freshness_report,
                    settings=settings,
                )
                if fallback_allowed:
                    latest_available_fallback_applied = True
                    reasoning_summary.append(
                        "输入时效超阈值，启用 latest-available fallback 继续推理"
                    )
                    artifact_paths["input_latest_available_fallback"] = str(
                        storage.save_artifact(
                            run_id,
                            stage="intermediate",
                            artifact_name="input_latest_available_fallback.json",
                            payload={
                                "applied": True,
                                "reason": "Freshness gate exceeded but latest-available fallback allowed",
                                "input_freshness_report": freshness_report.model_dump(mode="json"),
                            },
                        )
                    )
                else:
                    rejection_reasons: list[str] = [freshness_report.summary, *fallback_reasons]
                    rejection_reasons.extend(
                        f"STALE_NEWS: {item.key} age={item.age_minutes:.1f}m "
                        f"limit={item.threshold_minutes:.1f}m"
                        for item in freshness_report.stale_news[:5]
                    )
                    rejection_reasons.extend(
                        f"STALE_MARKET: {item.key} age={item.age_minutes:.1f}m "
                        f"limit={item.threshold_minutes:.1f}m"
                        for item in freshness_report.stale_market[:5]
                    )
                    artifact_paths["input_rejected"] = str(
                        storage.save_artifact(
                            run_id,
                            stage="final",
                            artifact_name="input_rejected.json",
                            payload={
                                "publish_status": "rejected",
                                "rejection_reasons": rejection_reasons,
                                "freshness_gate_enabled": freshness_gate_enabled,
                                "allow_latest_available_fallback": allow_latest_available_fallback,
                                "input_freshness_report": freshness_report.model_dump(mode="json"),
                            },
                        )
                    )
                    storage.complete_run(run_id, status="INPUT_STALE_REJECTED")
                    return PipelineResult(
                        run_id=run_id,
                        final_forecast=None,
                        artifact_paths=artifact_paths,
                        publish_status="rejected",
                        rejection_reasons=rejection_reasons,
                        collected_at=collected_at,
                        reviewed_at=reviewed_at,
                        latest_news_at=latest_news_at,
                        latest_market_at=latest_market_at,
                        run_started_at=run_started_at_dt.isoformat(),
                        run_completed_at=datetime.now(timezone.utc).isoformat(),
                        market_snapshot=market_snapshot,
                        news_snapshot=news_snapshot,
                        reasoning_summary=reasoning_summary,
                    )
            else:
                rejection_reasons = [freshness_report.summary]
                rejection_reasons.extend(
                    f"STALE_NEWS: {item.key} age={item.age_minutes:.1f}m "
                    f"limit={item.threshold_minutes:.1f}m"
                    for item in freshness_report.stale_news[:5]
                )
                rejection_reasons.extend(
                    f"STALE_MARKET: {item.key} age={item.age_minutes:.1f}m "
                    f"limit={item.threshold_minutes:.1f}m"
                    for item in freshness_report.stale_market[:5]
                )
                artifact_paths["input_rejected"] = str(
                    storage.save_artifact(
                        run_id,
                        stage="final",
                        artifact_name="input_rejected.json",
                        payload={
                            "publish_status": "rejected",
                            "rejection_reasons": rejection_reasons,
                            "freshness_gate_enabled": freshness_gate_enabled,
                            "allow_latest_available_fallback": allow_latest_available_fallback,
                            "input_freshness_report": freshness_report.model_dump(mode="json"),
                        },
                    )
                )
                storage.complete_run(run_id, status="INPUT_STALE_REJECTED")
                return PipelineResult(
                    run_id=run_id,
                    final_forecast=None,
                    artifact_paths=artifact_paths,
                    publish_status="rejected",
                    rejection_reasons=rejection_reasons,
                    collected_at=collected_at,
                    reviewed_at=reviewed_at,
                    latest_news_at=latest_news_at,
                    latest_market_at=latest_market_at,
                    run_started_at=run_started_at_dt.isoformat(),
                    run_completed_at=datetime.now(timezone.utc).isoformat(),
                    market_snapshot=market_snapshot,
                    news_snapshot=news_snapshot,
                    reasoning_summary=reasoning_summary,
                )

        event_extraction_prompt = prompt_loader.load("event_extraction.txt")
        structured_events = run_event_extraction(
            llm_client=llm_client,
            prompt_template=event_extraction_prompt,
            normalized_inputs=normalized_inputs,
        )
        artifact_paths["structured_events"] = str(
            storage.save_artifact(
                run_id,
                stage="intermediate",
                artifact_name="structured_events.json",
                payload=structured_events.model_dump(mode="json"),
            )
        )

        # Call 2: combined state mapping + forecast draft.
        state_forecast_prompt = prompt_loader.load("state_mapping.txt")
        state_and_forecast = run_state_and_forecast(
            llm_client=llm_client,
            prompt_template=state_forecast_prompt,
            normalized_inputs=normalized_inputs,
            structured_events=structured_events,
            output_language=settings.output_language,
        )
        state_mapping = state_and_forecast.state_mapping
        forecast_draft = state_and_forecast.forecast_draft

        artifact_paths["state_mapping"] = str(
            storage.save_artifact(
                run_id,
                stage="intermediate",
                artifact_name="state_mapping.json",
                payload=state_mapping.model_dump(mode="json"),
            )
        )
        artifact_paths["forecast_draft"] = str(
            storage.save_artifact(
                run_id,
                stage="intermediate",
                artifact_name="forecast_draft.json",
                payload=forecast_draft.model_dump(mode="json"),
            )
        )

        draft_rule_report = build_rule_report(forecast_draft)
        artifact_paths["draft_rule_report"] = str(
            storage.save_artifact(
                run_id,
                stage="intermediate",
                artifact_name="draft_rule_report.json",
                payload=draft_rule_report.model_dump(mode="json"),
            )
        )

        # Call 3: anti-hindsight review (consumes rule report).
        review_prompt = prompt_loader.load("anti_hindsight_review.txt")
        review_result = run_anti_hindsight_review(
            llm_client=llm_client,
            prompt_template=review_prompt,
            normalized_inputs=normalized_inputs,
            state_mapping=state_mapping,
            forecast_draft=forecast_draft,
            draft_rule_report=draft_rule_report,
            output_language=settings.output_language,
        )
        reviewed_at = review_result.reviewed_at.isoformat()
        artifact_paths["anti_hindsight_review"] = str(
            storage.save_artifact(
                run_id,
                stage="intermediate",
                artifact_name="anti_hindsight_review.json",
                payload=review_result.model_dump(mode="json"),
            )
        )

        post_review_rule_report = build_rule_report(review_result.reviewed_forecast)
        artifact_paths["post_review_rule_report"] = str(
            storage.save_artifact(
                run_id,
                stage="intermediate",
                artifact_name="post_review_rule_report.json",
                payload=post_review_rule_report.model_dump(mode="json"),
            )
        )

        publish_candidate = select_publishable_forecast(review_result)

        repaired_payload = repair_forecast_payload(
            publish_candidate.model_dump(mode="json"),
            output_language=settings.output_language,
        )
        confidence_result = compute_confidence(
            state_mapping=state_mapping,
            structured_events_payload=structured_events.model_dump(mode="json"),
            forecast_payload=repaired_payload,
            freshness_report=freshness_report,
            latest_available_fallback_applied=latest_available_fallback_applied,
        )
        repaired_payload["confidence"] = confidence_result.confidence
        _append_state_reasoning_summary(
            reasoning_summary=reasoning_summary,
            state_mapping=state_mapping,
            structured_events_payload=structured_events.model_dump(mode="json"),
            confidence_breakdown_payload=confidence_result.breakdown.model_dump(mode="json"),
        )
        artifact_paths["confidence_breakdown"] = str(
            storage.save_artifact(
                run_id,
                stage="intermediate",
                artifact_name="confidence_breakdown.json",
                payload=confidence_result.breakdown.model_dump(mode="json"),
            )
        )

        post_repair_rule_report = build_rule_report(repaired_payload)
        artifact_paths["post_repair_rule_report"] = str(
            storage.save_artifact(
                run_id,
                stage="intermediate",
                artifact_name="post_repair_rule_report.json",
                payload=post_repair_rule_report.model_dump(mode="json"),
            )
        )

        final_forecast = FinalForecast.model_validate(repaired_payload)

        rejection_reasons: list[str] = []
        if review_result.anti_hindsight_status != AntiHindsightStatus.PASS:
            rejection_reasons.append("ANTI_HINDSIGHT_FAIL: review status is FAIL")
            rejection_reasons.extend(review_result.issues)
            reasoning_summary.append("反后验审查未通过，进入发布拒绝流程")

        if post_repair_rule_report.has_blocking_issues:
            rejection_reasons.extend(_rule_issues_to_reasons(post_repair_rule_report))
            reasoning_summary.append("规则引擎在修复后仍发现阻断项，发布拒绝")

        if rejection_reasons:
            artifact_paths["review_rejected"] = str(
                storage.save_artifact(
                    run_id,
                    stage="final",
                    artifact_name="review_rejected.json",
                    payload={
                        "publish_status": "rejected",
                        "rejection_reasons": rejection_reasons,
                        "review_summary": review_result.review_summary,
                        "anti_hindsight_status": review_result.anti_hindsight_status.value,
                        "draft_rule_report": draft_rule_report.model_dump(mode="json"),
                        "post_review_rule_report": post_review_rule_report.model_dump(mode="json"),
                        "post_repair_rule_report": post_repair_rule_report.model_dump(mode="json"),
                        "reviewed_forecast": review_result.reviewed_forecast.model_dump(mode="json"),
                        "auto_repaired_forecast": repaired_payload,
                    },
                )
            )
            storage.complete_run(run_id, status="REVIEW_REJECTED")
            return PipelineResult(
                run_id=run_id,
                final_forecast=None,
                artifact_paths=artifact_paths,
                publish_status="rejected",
                rejection_reasons=rejection_reasons,
                collected_at=collected_at,
                reviewed_at=reviewed_at,
                latest_news_at=latest_news_at,
                latest_market_at=latest_market_at,
                run_started_at=run_started_at_dt.isoformat(),
                run_completed_at=datetime.now(timezone.utc).isoformat(),
                market_snapshot=market_snapshot,
                news_snapshot=news_snapshot,
                reasoning_summary=reasoning_summary,
            )

        validate_forecast_rules(final_forecast)
        reasoning_summary.append("审查与规则门禁全部通过，发布最终预测")
        artifact_paths["final_forecast"] = str(
            storage.save_artifact(
                run_id,
                stage="final",
                artifact_name="final_forecast.json",
                payload=final_forecast.model_dump(mode="json"),
            )
        )

        storage.save_forecast(run_id, final_forecast)
        storage.complete_run(run_id, status="SUCCEEDED")
        return PipelineResult(
            run_id=run_id,
            final_forecast=final_forecast,
            artifact_paths=artifact_paths,
            publish_status="approved",
            rejection_reasons=[],
            collected_at=collected_at,
            reviewed_at=reviewed_at,
            latest_news_at=latest_news_at,
            latest_market_at=latest_market_at,
            run_started_at=run_started_at_dt.isoformat(),
            run_completed_at=datetime.now(timezone.utc).isoformat(),
            market_snapshot=market_snapshot,
            news_snapshot=news_snapshot,
            reasoning_summary=reasoning_summary,
        )

    except Exception as exc:
        storage.complete_run(run_id, status="FAILED", error_message=str(exc))
        raise
    finally:
        if owns_storage:
            storage.close()
