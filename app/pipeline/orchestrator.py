"""Main orchestrator for a single strictly forward-looking forecast run."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable, Literal

from app.collectors.earnings_revision import collect_earnings_revision_proxy
from app.collectors.market_data import collect_market_data
from app.collectors.news import collect_news
from app.config import Settings
from app.exceptions import CollectorError
from app.llm_client import BaseLLMClient, build_llm_client
from app.pipeline.check_freshness import build_input_freshness_report
from app.pipeline.confidence import compute_confidence
from app.pipeline.extract_events import run_event_extraction
from app.pipeline.factors import build_factor_snapshot
from app.pipeline.generate_feedback import (
    build_post_feedback_fallback,
    build_pre_feedback_fallback,
    run_post_forecast_feedback,
    run_pre_forecast_feedback,
)
from app.pipeline.map_states import run_state_and_forecast
from app.pipeline.normalize import normalize_inputs
from app.pipeline.publish_forecast import select_publishable_forecast
from app.pipeline.review_forecast import run_anti_hindsight_review
from app.rules.schema_check import build_rule_report, validate_forecast_rules
from app.rules.validators import repair_forecast_payload
from app.schemas import (
    AntiHindsightStatus,
    DominantFactorResult,
    EarningsRevisionProxy,
    FactorSnapshot,
    FinalForecast,
    GovernanceIssue,
    InputFreshnessReport,
    IssueSeverity,
    NormalizedInputs,
    ReferenceLevels,
    ReviewFindings,
    RuleCheckReport,
    StateMappingResult,
    PreForecastFeedback,
    PostForecastFeedback,
)
from app.storage.db import Storage
from app.utils.prompt_loader import PromptLoader

NewsCollector = Callable[[Settings, str | None], tuple[list[dict[str, Any]], str]]
MarketDataCollector = Callable[[Settings, str | None], tuple[list[dict[str, Any]], str]]
EarningsProxyCollector = Callable[[Settings], tuple[EarningsRevisionProxy, str]]


@dataclass
class PipelineResult:
    """Return object used by CLI and API layers."""

    run_id: str
    final_forecast: FinalForecast | None
    artifact_paths: dict[str, str]
    publish_status: Literal["approved", "rejected"]
    run_status: Literal["approved", "review_warn", "review_fail", "input_stale"] = "approved"
    is_publishable: bool = True
    review_status: Literal["PASS", "FAIL"] | None = None
    decision_summary: str = ""
    rejection_reasons: list[str] = field(default_factory=list)
    review_summary: str = ""
    review_findings: dict[str, Any] = field(default_factory=dict)
    reference_levels: dict[str, Any] = field(default_factory=dict)
    collected_at: str | None = None
    reviewed_at: str | None = None
    latest_news_at: str | None = None
    latest_market_at: str | None = None
    run_started_at: str | None = None
    run_completed_at: str | None = None
    market_universe: list[str] = field(default_factory=list)
    market_snapshot: dict[str, dict[str, Any]] = field(default_factory=dict)
    news_snapshot: list[dict[str, Any]] = field(default_factory=list)
    reasoning_summary: list[str] = field(default_factory=list)
    state_snapshot: dict[str, Any] = field(default_factory=dict)
    confidence_snapshot: dict[str, Any] = field(default_factory=dict)
    runtime_assertions: dict[str, Any] = field(default_factory=dict)
    analysis_flow: list[dict[str, Any]] = field(default_factory=list)
    analysis_variants: dict[str, Any] = field(default_factory=dict)
    publish_gate_report: dict[str, Any] = field(default_factory=dict)
    market_snapshot_summary: list[str] = field(default_factory=list)
    top_news_signals: list[dict[str, Any]] = field(default_factory=list)
    top_market_signals: list[dict[str, Any]] = field(default_factory=list)
    signal_conflicts: list[str] = field(default_factory=list)
    forecast_support_map: list[str] = field(default_factory=list)
    forecast_opposition_map: list[str] = field(default_factory=list)
    monitoring_priorities: list[str] = field(default_factory=list)
    next_run_questions: list[str] = field(default_factory=list)
    pre_forecast_feedback: dict[str, Any] = field(default_factory=dict)
    post_forecast_feedback: dict[str, Any] = field(default_factory=dict)
    factor_snapshot: dict[str, Any] = field(default_factory=dict)
    dominant_factor: dict[str, Any] = field(default_factory=dict)
    dominant_factor_explainer: str = ""
    earnings_revision_proxy_summary: dict[str, Any] = field(default_factory=dict)
    earnings_proxy_source: str | None = None


@dataclass
class PipelineDependencies:
    """Injectable dependencies to improve testability and separation."""

    news_collector: NewsCollector = collect_news
    market_data_collector: MarketDataCollector = collect_market_data
    earnings_proxy_collector: EarningsProxyCollector = collect_earnings_revision_proxy
    llm_client: BaseLLMClient | None = None
    storage: Storage | None = None
    prompt_loader: PromptLoader | None = None


def _rule_issues_to_reasons(rule_report: RuleCheckReport) -> list[str]:
    return [f"{item.code}: {item.message}" for item in rule_report.issues]


def _build_runtime_assertions(
    settings: Settings,
    llm_provider: str,
    news_source: str,
    market_source: str,
) -> dict[str, Any]:
    strict_mode_active = bool(settings.use_live_data and settings.strict_live_mode)
    allowed_news_sources = {
        "live",
        "latest_available_cache",
        "live_newsapi",
        "live_sec",
        "live_fmp_news",
        "live_fmp_news+newsapi",
        "live_newsapi+sec",
        "live_fmp_news+sec",
        "live_fmp_news+newsapi+sec",
    }
    allowed_market_sources = {"live_fmp", "live_fmp+yahoo", "latest_available_cache"}

    checks = [
        {
            "name": "llm_provider_not_mock",
            "passed": llm_provider != "mock",
            "expected": "llm_provider != mock",
            "observed": llm_provider,
        },
        {
            "name": "news_source_allowed",
            "passed": news_source in allowed_news_sources or str(news_source).startswith("live_"),
            "expected": sorted(allowed_news_sources),
            "observed": news_source,
        },
        {
            "name": "market_source_allowed",
            "passed": market_source in allowed_market_sources,
            "expected": sorted(allowed_market_sources),
            "observed": market_source,
        },
        {
            "name": "news_source_not_mock",
            "passed": news_source != "mock",
            "expected": "news_source != mock",
            "observed": news_source,
        },
        {
            "name": "market_source_not_mock",
            "passed": market_source != "mock",
            "expected": "market_source != mock",
            "observed": market_source,
        },
    ]

    return {
        "strict_mode_active": strict_mode_active,
        "llm_provider": llm_provider,
        "news_source": news_source,
        "market_source": market_source,
        "checks": checks,
        "all_passed": all(item["passed"] for item in checks),
    }


def _assert_runtime_assertions(runtime_assertions: dict[str, Any]) -> None:
    strict_mode_active = bool(runtime_assertions.get("strict_mode_active"))
    if not strict_mode_active:
        return

    failed_checks = [
        item
        for item in runtime_assertions.get("checks", [])
        if isinstance(item, dict) and not bool(item.get("passed"))
    ]
    if not failed_checks:
        return

    details = ", ".join(
        f"{item.get('name')} observed={item.get('observed')}"
        for item in failed_checks
    )
    raise CollectorError(f"Strict live runtime assertions failed: {details}")


def _flow_entry(
    *,
    stage: str,
    status: str,
    elapsed_seconds: float,
    input_summary: dict[str, Any] | None = None,
    output_summary: dict[str, Any] | None = None,
    artifacts: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "status": status,
        "elapsed_seconds": round(float(elapsed_seconds), 3),
        "input_summary": input_summary or {},
        "output_summary": output_summary or {},
        "artifacts": artifacts or [],
    }


def _gate_check(name: str, passed: bool, detail: str, blocking: bool = True) -> dict[str, Any]:
    return {
        "name": name,
        "passed": passed,
        "blocking": blocking,
        "detail": detail,
    }


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


def _build_news_snapshot(normalized_inputs: NormalizedInputs, top_k: int = 8) -> list[dict[str, Any]]:
    ranked_news = sorted(normalized_inputs.news, key=lambda item: item.published_at, reverse=True)[:top_k]
    return [
        {
            "source": item.source,
            "source_type": item.source_type,
            "source_reliability": item.source_reliability,
            "headline": item.headline,
            "summary": item.summary,
            "url": item.url,
            "published_at": item.published_at.isoformat(),
        }
        for item in ranked_news
    ]


def _build_state_snapshot(state_mapping: StateMappingResult) -> dict[str, Any]:
    scenarios = sorted(state_mapping.scenarios, key=lambda item: float(item.probability), reverse=True)
    return {
        "regime_label": state_mapping.regime_label,
        "growth_state": state_mapping.growth_state,
        "inflation_state": state_mapping.inflation_state,
        "liquidity_state": state_mapping.liquidity_state,
        "volatility_state": state_mapping.volatility_state,
        "cross_asset_signals": state_mapping.cross_asset_signals,
        "scenarios": [
            {
                "name": item.name,
                "probability": item.probability,
                "directional_implication": item.directional_implication.value,
                "key_conditions": item.key_conditions,
            }
            for item in scenarios
        ],
        "narrative": state_mapping.narrative,
    }


def _truncate_text(value: str, max_chars: int) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."


def _build_llm_normalized_inputs_payload(
    normalized_inputs: NormalizedInputs,
    max_news_items: int,
    max_text_chars: int,
) -> dict[str, Any]:
    ranked_news = sorted(normalized_inputs.news, key=lambda item: item.published_at, reverse=True)
    compact_news: list[dict[str, Any]] = []
    for item in ranked_news[: max(1, max_news_items)]:
        compact_news.append(
            {
                "source": item.source,
                "source_type": item.source_type,
                "source_reliability": item.source_reliability,
                "headline": _truncate_text(item.headline, max_text_chars),
                "summary": _truncate_text(item.summary, max_text_chars),
                "url": item.url,
                "published_at": item.published_at.isoformat(),
            }
        )

    compact_indicators = [
        {
            "symbol": item.symbol,
            "name": item.name,
            "value": item.value,
            "change_pct": item.change_pct,
            "unit": item.unit,
            "as_of": item.as_of.isoformat(),
        }
        for item in normalized_inputs.indicators
    ]

    return {
        "run_id": normalized_inputs.run_id,
        "collected_at": normalized_inputs.collected_at.isoformat(),
        "forecast_horizon": normalized_inputs.forecast_horizon,
        "market_universe": normalized_inputs.market_universe,
        "news": compact_news,
        "indicators": compact_indicators,
        "state_variables": normalized_inputs.state_variables,
    }


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
    earnings_proxy_source: str | None = None
    news_source: str | None = None
    market_source: str | None = None
    latest_available_fallback_applied = False
    market_snapshot: dict[str, dict[str, Any]] = {}
    news_snapshot: list[dict[str, Any]] = []
    reasoning_summary: list[str] = []
    state_snapshot: dict[str, Any] = {}
    confidence_snapshot: dict[str, Any] = {}
    factor_snapshot_payload: dict[str, Any] = {}
    dominant_factor_payload: dict[str, Any] = {}
    dominant_factor_explainer = ""
    earnings_revision_proxy_summary: dict[str, Any] = {}
    llm_normalized_inputs_payload: dict[str, Any] = {}
    runtime_assertions: dict[str, Any] = {}
    analysis_flow: list[dict[str, Any]] = []
    analysis_variants: dict[str, Any] = {}
    publish_gate_report: dict[str, Any] = {
        "approved": False,
        "checks": [],
        "rejection_reasons": [],
    }
    review_status: Literal["PASS", "FAIL"] | None = None
    decision_summary = ""
    review_summary_text = ""
    review_findings_payload: dict[str, Any] = {}
    reference_levels_payload: dict[str, Any] = {}
    pre_feedback_result = PreForecastFeedback(
        generated_at=datetime.now(timezone.utc),
        market_snapshot_summary=[],
        top_news_signals=[],
        top_market_signals=[],
        signal_conflicts=[],
    )
    post_feedback_result = PostForecastFeedback(
        generated_at=datetime.now(timezone.utc),
        forecast_support_map=[],
        forecast_opposition_map=[],
        monitoring_priorities=[],
        next_run_questions=[],
    )
    pipeline_started = perf_counter()

    def _finalize_run_timestamp() -> str:
        reasoning_summary.append(f"总耗时: {perf_counter() - pipeline_started:.2f}s")
        return datetime.now(timezone.utc).isoformat()

    def _persist_analysis_trace(
        *,
        publish_status: Literal["approved", "rejected"],
        run_status: Literal["approved", "review_warn", "review_fail", "input_stale"],
        is_publishable: bool,
        rejection_reasons: list[str],
        run_completed_at: str,
    ) -> None:
        trace_payload = {
            "run_id": run_id,
            "publish_status": publish_status,
            "run_status": run_status,
            "is_publishable": is_publishable,
            "review_status": review_status,
            "decision_summary": decision_summary,
            "rejection_reasons": rejection_reasons,
            "review_summary": review_summary_text,
            "review_findings": review_findings_payload,
            "reference_levels": reference_levels_payload,
            "run_started_at": run_started_at_dt.isoformat(),
            "run_completed_at": run_completed_at,
            "runtime_assertions": runtime_assertions,
            "earnings_proxy_source": earnings_proxy_source,
            "analysis_flow": analysis_flow,
            "analysis_variants": analysis_variants,
            "publish_gate_report": publish_gate_report,
            "pre_forecast_feedback": pre_feedback_result.model_dump(mode="json"),
            "post_forecast_feedback": post_feedback_result.model_dump(mode="json"),
            "market_snapshot_summary": pre_feedback_result.market_snapshot_summary,
            "top_news_signals": [
                item.model_dump(mode="json") for item in pre_feedback_result.top_news_signals
            ],
            "top_market_signals": [
                item.model_dump(mode="json") for item in pre_feedback_result.top_market_signals
            ],
            "signal_conflicts": pre_feedback_result.signal_conflicts,
            "forecast_support_map": post_feedback_result.forecast_support_map,
            "forecast_opposition_map": post_feedback_result.forecast_opposition_map,
            "monitoring_priorities": post_feedback_result.monitoring_priorities,
            "next_run_questions": post_feedback_result.next_run_questions,
            "market_snapshot": market_snapshot,
            "news_snapshot": news_snapshot,
            "state_snapshot": state_snapshot,
            "confidence_snapshot": confidence_snapshot,
            "factor_snapshot": factor_snapshot_payload,
            "dominant_factor": dominant_factor_payload,
            "dominant_factor_explainer": dominant_factor_explainer,
            "earnings_revision_proxy_summary": earnings_revision_proxy_summary,
            "reasoning_summary": reasoning_summary,
            "artifact_paths": dict(artifact_paths),
        }
        artifact_paths["analysis_trace"] = str(
            storage.save_artifact(
                run_id,
                stage="final",
                artifact_name="analysis_trace.json",
                payload=trace_payload,
            )
        )

    try:
        collect_started = perf_counter()
        if settings.collect_in_parallel:
            with ThreadPoolExecutor(max_workers=2) as executor:
                news_future = executor.submit(deps.news_collector, settings, news_file)
                market_future = executor.submit(deps.market_data_collector, settings, market_file)
                raw_news_items, news_source = news_future.result()
                raw_market_indicators, market_source = market_future.result()
        else:
            raw_news_items, news_source = deps.news_collector(settings, news_file)
            raw_market_indicators, market_source = deps.market_data_collector(settings, market_file)
        collect_elapsed = perf_counter() - collect_started
        reasoning_summary.append(
            f"采集耗时: {collect_elapsed:.2f}s (news={news_source}, market={market_source})"
        )
        runtime_assertions = _build_runtime_assertions(
            settings=settings,
            llm_provider=settings.llm_provider,
            news_source=news_source,
            market_source=market_source,
        )
        _assert_runtime_assertions(runtime_assertions)

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
        analysis_flow.append(
            _flow_entry(
                stage="collect_inputs",
                status="ok",
                elapsed_seconds=collect_elapsed,
                input_summary={
                    "use_live_data": settings.use_live_data,
                    "strict_live_mode": settings.strict_live_mode,
                    "collect_in_parallel": settings.collect_in_parallel,
                },
                output_summary={
                    "news_source": news_source,
                    "market_source": market_source,
                    "news_count": len(raw_news_items),
                    "market_count": len(raw_market_indicators),
                },
                artifacts=["news_raw", "market_raw"],
            )
        )

        normalize_started = perf_counter()
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
        analysis_flow.append(
            _flow_entry(
                stage="normalize_inputs",
                status="ok",
                elapsed_seconds=perf_counter() - normalize_started,
                output_summary={
                    "forecast_horizon": normalized_inputs.forecast_horizon,
                    "market_universe_count": len(normalized_inputs.market_universe),
                    "news_items": len(normalized_inputs.news),
                    "market_indicators": len(normalized_inputs.indicators),
                },
                artifacts=["normalized_inputs"],
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
        llm_normalized_inputs_payload = _build_llm_normalized_inputs_payload(
            normalized_inputs=normalized_inputs,
            max_news_items=settings.llm_compact_news_items,
            max_text_chars=settings.llm_compact_text_chars,
        )
        reasoning_summary.append(
            "LLM输入瘦身: "
            f"news={len(llm_normalized_inputs_payload.get('news', []))}条, "
            f"text_chars={settings.llm_compact_text_chars}, "
            f"max_tokens={settings.llm_max_tokens}"
        )

        earnings_started = perf_counter()
        earnings_proxy, earnings_proxy_source = deps.earnings_proxy_collector(settings)
        earnings_elapsed = perf_counter() - earnings_started
        earnings_revision_proxy_summary = earnings_proxy.model_dump(mode="json")
        artifact_paths["earnings_revision_proxy"] = str(
            storage.save_artifact(
                run_id,
                stage="intermediate",
                artifact_name="earnings_revision_proxy.json",
                payload=earnings_revision_proxy_summary,
            )
        )
        analysis_flow.append(
            _flow_entry(
                stage="earnings_revision_proxy",
                status="ok",
                elapsed_seconds=earnings_elapsed,
                output_summary={
                    "source": earnings_proxy_source,
                    "coverage_status": earnings_proxy.coverage_status,
                    "sample_size": earnings_proxy.sample_size,
                    "available_series": earnings_proxy.available_series,
                    "score": earnings_proxy.score,
                },
                artifacts=["earnings_revision_proxy"],
            )
        )

        factor_started = perf_counter()
        factor_snapshot, dominant_factor = build_factor_snapshot(
            settings=settings,
            normalized_inputs=normalized_inputs,
            earnings_proxy=earnings_proxy,
            output_language=settings.output_language,
        )
        factor_elapsed = perf_counter() - factor_started
        factor_snapshot_payload = factor_snapshot.model_dump(mode="json")
        dominant_factor_payload = dominant_factor.model_dump(mode="json")
        dominant_factor_explainer = dominant_factor.explainer
        artifact_paths["factor_state_snapshot"] = str(
            storage.save_artifact(
                run_id,
                stage="intermediate",
                artifact_name="factor_state_snapshot.json",
                payload={
                    "factor_snapshot": factor_snapshot_payload,
                    "dominant_factor_result": dominant_factor_payload,
                },
            )
        )
        analysis_flow.append(
            _flow_entry(
                stage="factor_state_snapshot",
                status="ok",
                elapsed_seconds=factor_elapsed,
                output_summary={
                    "dominant_factor": dominant_factor.dominant_factor,
                    "tie_detected": dominant_factor.tie_detected,
                },
                artifacts=["factor_state_snapshot"],
            )
        )
        reasoning_summary.append(
            "五因子方向: "
            f"earnings={factor_snapshot.earnings_revision.direction.value}, "
            f"volatility={factor_snapshot.volatility.direction.value}, "
            f"rates={factor_snapshot.rates.direction.value}, "
            f"dollar={factor_snapshot.dollar.direction.value}, "
            f"energy_geo={factor_snapshot.energy_geopolitics.direction.value}; "
            f"dominant={dominant_factor.dominant_factor}"
        )

        freshness_started = perf_counter()
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
        analysis_flow.append(
            _flow_entry(
                stage="input_freshness_gate",
                status="ok" if not freshness_report.has_blocking_issues else "blocking",
                elapsed_seconds=perf_counter() - freshness_started,
                input_summary={
                    "max_news_age_hours": news_age_limit,
                    "max_market_age_minutes": market_age_limit,
                    "freshness_gate_enabled": freshness_gate_enabled,
                    "allow_latest_available_fallback": allow_latest_available_fallback,
                },
                output_summary={
                    "has_blocking_issues": freshness_report.has_blocking_issues,
                    "stale_news_count": len(freshness_report.stale_news),
                    "stale_market_count": len(freshness_report.stale_market),
                },
                artifacts=["input_freshness_report"],
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
                    analysis_flow.append(
                        _flow_entry(
                            stage="latest_available_fallback",
                            status="ok",
                            elapsed_seconds=0.0,
                            output_summary={
                                "applied": True,
                                "reason": "Freshness gate exceeded but latest-available fallback allowed",
                            },
                            artifacts=["input_latest_available_fallback"],
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
                    publish_gate_report = {
                        "approved": False,
                        "checks": [
                            _gate_check(
                                name="input_freshness_gate",
                                passed=False,
                                detail=freshness_report.summary,
                            ),
                            _gate_check(
                                name="latest_available_fallback_allowed",
                                passed=False,
                                detail="; ".join(fallback_reasons) or "fallback denied",
                            ),
                        ],
                        "rejection_reasons": rejection_reasons,
                    }
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
                                "earnings_revision_proxy": earnings_revision_proxy_summary,
                                "factor_snapshot": factor_snapshot_payload,
                                "dominant_factor_result": dominant_factor_payload,
                                "runtime_assertions": runtime_assertions,
                                "publish_gate_report": publish_gate_report,
                            },
                        )
                    )
                    analysis_flow.append(
                        _flow_entry(
                            stage="publish_gate",
                            status="rejected",
                            elapsed_seconds=0.0,
                            output_summary={
                                "reason_count": len(rejection_reasons),
                                "gate": "input_freshness_gate",
                            },
                            artifacts=["input_rejected"],
                        )
                    )
                    run_completed_at = _finalize_run_timestamp()
                    decision_summary = "Input freshness gate failed; run ended as input_stale."
                    _persist_analysis_trace(
                        publish_status="rejected",
                        run_status="input_stale",
                        is_publishable=False,
                        rejection_reasons=rejection_reasons,
                        run_completed_at=run_completed_at,
                    )
                    storage.complete_run(run_id, status="INPUT_STALE")
                    return PipelineResult(
                        run_id=run_id,
                        final_forecast=None,
                        artifact_paths=artifact_paths,
                        publish_status="rejected",
                        run_status="input_stale",
                        is_publishable=False,
                        review_status=None,
                        decision_summary=decision_summary,
                        rejection_reasons=rejection_reasons,
                        review_summary=review_summary_text,
                        review_findings=review_findings_payload,
                        reference_levels=reference_levels_payload,
                        collected_at=collected_at,
                        reviewed_at=reviewed_at,
                        latest_news_at=latest_news_at,
                        latest_market_at=latest_market_at,
                        run_started_at=run_started_at_dt.isoformat(),
                        run_completed_at=run_completed_at,
                        market_universe=list(universe),
                        market_snapshot=market_snapshot,
                        news_snapshot=news_snapshot,
                        reasoning_summary=reasoning_summary,
                        state_snapshot=state_snapshot,
                        confidence_snapshot=confidence_snapshot,
                        runtime_assertions=runtime_assertions,
                        analysis_flow=analysis_flow,
                        analysis_variants=analysis_variants,
                        publish_gate_report=publish_gate_report,
                        factor_snapshot=factor_snapshot_payload,
                        dominant_factor=dominant_factor_payload,
                        dominant_factor_explainer=dominant_factor_explainer,
                        earnings_revision_proxy_summary=earnings_revision_proxy_summary,
                        earnings_proxy_source=earnings_proxy_source,
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
                publish_gate_report = {
                    "approved": False,
                    "checks": [
                        _gate_check(
                            name="input_freshness_gate",
                            passed=False,
                            detail=freshness_report.summary,
                        ),
                        _gate_check(
                            name="latest_available_fallback_enabled",
                            passed=False,
                            detail="Fallback disabled by configuration",
                        ),
                    ],
                    "rejection_reasons": rejection_reasons,
                }
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
                            "earnings_revision_proxy": earnings_revision_proxy_summary,
                            "factor_snapshot": factor_snapshot_payload,
                            "dominant_factor_result": dominant_factor_payload,
                            "runtime_assertions": runtime_assertions,
                            "publish_gate_report": publish_gate_report,
                        },
                    )
                )
                analysis_flow.append(
                    _flow_entry(
                        stage="publish_gate",
                        status="rejected",
                        elapsed_seconds=0.0,
                        output_summary={
                            "reason_count": len(rejection_reasons),
                            "gate": "input_freshness_gate",
                        },
                        artifacts=["input_rejected"],
                    )
                )
                run_completed_at = _finalize_run_timestamp()
                decision_summary = "Input freshness gate failed; run ended as input_stale."
                _persist_analysis_trace(
                    publish_status="rejected",
                    run_status="input_stale",
                    is_publishable=False,
                    rejection_reasons=rejection_reasons,
                    run_completed_at=run_completed_at,
                )
                storage.complete_run(run_id, status="INPUT_STALE")
                return PipelineResult(
                    run_id=run_id,
                    final_forecast=None,
                    artifact_paths=artifact_paths,
                    publish_status="rejected",
                    run_status="input_stale",
                    is_publishable=False,
                    review_status=None,
                    decision_summary=decision_summary,
                    rejection_reasons=rejection_reasons,
                    review_summary=review_summary_text,
                    review_findings=review_findings_payload,
                    reference_levels=reference_levels_payload,
                    collected_at=collected_at,
                    reviewed_at=reviewed_at,
                    latest_news_at=latest_news_at,
                    latest_market_at=latest_market_at,
                    run_started_at=run_started_at_dt.isoformat(),
                    run_completed_at=run_completed_at,
                    market_universe=list(universe),
                    market_snapshot=market_snapshot,
                    news_snapshot=news_snapshot,
                    reasoning_summary=reasoning_summary,
                    state_snapshot=state_snapshot,
                    confidence_snapshot=confidence_snapshot,
                    runtime_assertions=runtime_assertions,
                    analysis_flow=analysis_flow,
                    analysis_variants=analysis_variants,
                    publish_gate_report=publish_gate_report,
                    factor_snapshot=factor_snapshot_payload,
                    dominant_factor=dominant_factor_payload,
                    dominant_factor_explainer=dominant_factor_explainer,
                    earnings_revision_proxy_summary=earnings_revision_proxy_summary,
                    earnings_proxy_source=earnings_proxy_source,
                )

        event_extraction_prompt = prompt_loader.load("event_extraction.txt")
        event_started = perf_counter()
        structured_events = run_event_extraction(
            llm_client=llm_client,
            prompt_template=event_extraction_prompt,
            normalized_inputs=normalized_inputs,
            normalized_inputs_payload=llm_normalized_inputs_payload,
        )
        event_elapsed = perf_counter() - event_started
        reasoning_summary.append(f"LLM阶段耗时 event_extraction={event_elapsed:.2f}s")
        artifact_paths["structured_events"] = str(
            storage.save_artifact(
                run_id,
                stage="intermediate",
                artifact_name="structured_events.json",
                payload=structured_events.model_dump(mode="json"),
            )
        )
        analysis_flow.append(
            _flow_entry(
                stage="event_extraction",
                status="ok",
                elapsed_seconds=event_elapsed,
                output_summary={
                    "event_count": len(structured_events.events),
                    "summary": structured_events.summary,
                },
                artifacts=["structured_events"],
            )
        )

        # Call 2: combined state mapping + forecast draft.
        state_forecast_prompt = prompt_loader.load("state_mapping.txt")
        state_started = perf_counter()
        state_and_forecast = run_state_and_forecast(
            llm_client=llm_client,
            prompt_template=state_forecast_prompt,
            normalized_inputs=normalized_inputs,
            structured_events=structured_events,
            factor_snapshot=factor_snapshot_payload,
            dominant_factor=dominant_factor_payload,
            output_language=settings.output_language,
            normalized_inputs_payload=llm_normalized_inputs_payload,
        )
        state_elapsed = perf_counter() - state_started
        reasoning_summary.append(f"LLM阶段耗时 state_and_forecast={state_elapsed:.2f}s")
        state_mapping = state_and_forecast.state_mapping
        forecast_draft = state_and_forecast.forecast_draft
        state_snapshot = _build_state_snapshot(state_mapping)

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
        analysis_flow.append(
            _flow_entry(
                stage="state_and_forecast",
                status="ok",
                elapsed_seconds=state_elapsed,
                output_summary={
                    "regime_label": state_mapping.regime_label,
                    "scenario_count": len(state_mapping.scenarios),
                    "draft_directional_bias": forecast_draft.directional_bias.value,
                },
                artifacts=["state_mapping", "forecast_draft"],
            )
        )

        pre_feedback_started = perf_counter()
        try:
            pre_feedback_prompt = prompt_loader.load("pre_forecast_feedback.txt")
            pre_feedback_result = run_pre_forecast_feedback(
                llm_client=llm_client,
                prompt_template=pre_feedback_prompt,
                normalized_inputs=normalized_inputs,
                structured_events=structured_events,
                state_mapping=state_mapping,
                factor_snapshot=factor_snapshot_payload,
                dominant_factor_result=dominant_factor_payload,
                market_snapshot=market_snapshot,
                news_snapshot=news_snapshot,
                output_language=settings.output_language,
                normalized_inputs_payload=llm_normalized_inputs_payload,
            )
            pre_feedback_status = "ok"
            reasoning_summary.append(
                f"LLM阶段耗时 pre_forecast_feedback={perf_counter() - pre_feedback_started:.2f}s"
            )
        except Exception:
            pre_feedback_result = build_pre_feedback_fallback(
                normalized_inputs=normalized_inputs,
                structured_events=structured_events,
                market_snapshot=market_snapshot,
            )
            pre_feedback_status = "fallback"
            reasoning_summary.append("pre_forecast_feedback 调用失败，已使用确定性 fallback。")

        artifact_paths["pre_forecast_feedback"] = str(
            storage.save_artifact(
                run_id,
                stage="intermediate",
                artifact_name="pre_forecast_feedback.json",
                payload=pre_feedback_result.model_dump(mode="json"),
            )
        )
        analysis_flow.append(
            _flow_entry(
                stage="pre_forecast_feedback",
                status=pre_feedback_status,
                elapsed_seconds=perf_counter() - pre_feedback_started,
                output_summary={
                    "market_snapshot_summary_count": len(pre_feedback_result.market_snapshot_summary),
                    "top_news_signals_count": len(pre_feedback_result.top_news_signals),
                    "top_market_signals_count": len(pre_feedback_result.top_market_signals),
                    "signal_conflicts_count": len(pre_feedback_result.signal_conflicts),
                },
                artifacts=["pre_forecast_feedback"],
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
        analysis_flow.append(
            _flow_entry(
                stage="draft_rule_check",
                status="ok" if not draft_rule_report.has_blocking_issues else "blocking",
                elapsed_seconds=0.0,
                output_summary={
                    "blocking_issue_count": len(draft_rule_report.issues),
                    "warning_count": len(draft_rule_report.warnings),
                },
                artifacts=["draft_rule_report"],
            )
        )

        # Call 3: anti-hindsight review (consumes rule report).
        review_prompt = prompt_loader.load("anti_hindsight_review.txt")
        review_started = perf_counter()
        review_result = run_anti_hindsight_review(
            llm_client=llm_client,
            prompt_template=review_prompt,
            normalized_inputs=normalized_inputs,
            state_mapping=state_mapping,
            forecast_draft=forecast_draft,
            draft_rule_report=draft_rule_report,
            output_language=settings.output_language,
            normalized_inputs_payload=llm_normalized_inputs_payload,
        )
        review_elapsed = perf_counter() - review_started
        reasoning_summary.append(f"LLM阶段耗时 anti_hindsight_review={review_elapsed:.2f}s")
        reviewed_at = review_result.reviewed_at.isoformat()
        artifact_paths["anti_hindsight_review"] = str(
            storage.save_artifact(
                run_id,
                stage="intermediate",
                artifact_name="anti_hindsight_review.json",
                payload=review_result.model_dump(mode="json"),
            )
        )
        analysis_flow.append(
            _flow_entry(
                stage="anti_hindsight_review",
                status=(
                    "ok"
                    if review_result.anti_hindsight_status == AntiHindsightStatus.PASS
                    else "blocking"
                ),
                elapsed_seconds=review_elapsed,
                output_summary={
                    "anti_hindsight_status": review_result.anti_hindsight_status.value,
                    "issue_count": len(review_result.issues),
                },
                artifacts=["anti_hindsight_review"],
            )
        )

        review_status = review_result.review_decision.review_status.value
        decision_summary = review_result.review_decision.decision_summary
        review_summary_text = review_result.review_summary
        review_findings_payload = review_result.review_findings.model_dump(mode="json")
        reference_levels_payload = review_result.reference_levels.model_dump(mode="json")

        post_review_rule_report = build_rule_report(
            review_result.reviewed_forecast,
            require_review_status=True,
            review_summary=review_result.review_summary,
            reference_levels=review_result.reference_levels,
        )
        artifact_paths["post_review_rule_report"] = str(
            storage.save_artifact(
                run_id,
                stage="intermediate",
                artifact_name="post_review_rule_report.json",
                payload=post_review_rule_report.model_dump(mode="json"),
            )
        )
        analysis_flow.append(
            _flow_entry(
                stage="post_review_rule_check",
                status="ok" if not post_review_rule_report.has_blocking_issues else "blocking",
                elapsed_seconds=0.0,
                output_summary={
                    "hard_fail_count": len(post_review_rule_report.hard_fail_issues),
                    "soft_warn_count": len(post_review_rule_report.soft_warnings),
                },
                artifacts=["post_review_rule_report"],
            )
        )

        publish_candidate = select_publishable_forecast(review_result)
        publish_candidate_payload = publish_candidate.model_dump(mode="json")
        if not isinstance(publish_candidate_payload.get("reference_levels"), dict):
            publish_candidate_payload["reference_levels"] = reference_levels_payload
        publish_candidate_payload["review_status"] = review_status
        publish_candidate_payload["anti_hindsight_status"] = review_status

        repaired_payload = repair_forecast_payload(
            publish_candidate_payload,
            output_language=settings.output_language,
        )
        repaired_payload["review_status"] = review_status
        repaired_payload["anti_hindsight_status"] = review_status
        if not isinstance(repaired_payload.get("reference_levels"), dict):
            repaired_payload["reference_levels"] = reference_levels_payload

        confidence_result = compute_confidence(
            state_mapping=state_mapping,
            structured_events_payload=structured_events.model_dump(mode="json"),
            forecast_payload=repaired_payload,
            freshness_report=freshness_report,
            latest_available_fallback_applied=latest_available_fallback_applied,
        )
        repaired_payload["confidence"] = confidence_result.confidence
        confidence_snapshot = confidence_result.breakdown.model_dump(mode="json")
        _append_state_reasoning_summary(
            reasoning_summary=reasoning_summary,
            state_mapping=state_mapping,
            structured_events_payload=structured_events.model_dump(mode="json"),
            confidence_breakdown_payload=confidence_snapshot,
        )
        artifact_paths["confidence_breakdown"] = str(
            storage.save_artifact(
                run_id,
                stage="intermediate",
                artifact_name="confidence_breakdown.json",
                payload=confidence_result.breakdown.model_dump(mode="json"),
            )
        )

        post_repair_rule_report = build_rule_report(
            repaired_payload,
            require_review_status=True,
            review_summary=review_result.review_summary,
            reference_levels=review_result.reference_levels,
        )
        artifact_paths["post_repair_rule_report"] = str(
            storage.save_artifact(
                run_id,
                stage="intermediate",
                artifact_name="post_repair_rule_report.json",
                payload=post_repair_rule_report.model_dump(mode="json"),
            )
        )
        analysis_flow.append(
            _flow_entry(
                stage="post_repair_rule_check",
                status="ok" if not post_repair_rule_report.has_blocking_issues else "blocking",
                elapsed_seconds=0.0,
                output_summary={
                    "hard_fail_count": len(post_repair_rule_report.hard_fail_issues),
                    "soft_warn_count": len(post_repair_rule_report.soft_warnings),
                },
                artifacts=["confidence_breakdown", "post_repair_rule_report"],
            )
        )

        final_forecast = FinalForecast.model_validate(repaired_payload)
        reference_levels_payload = final_forecast.reference_levels.model_dump(mode="json")
        analysis_variants = {
            "draft_forecast": forecast_draft.model_dump(mode="json"),
            "reviewed_forecast": review_result.reviewed_forecast.model_dump(mode="json"),
            "repaired_forecast": repaired_payload,
        }
        post_feedback_started = perf_counter()
        try:
            post_feedback_prompt = prompt_loader.load("post_forecast_feedback.txt")
            post_feedback_result = run_post_forecast_feedback(
                llm_client=llm_client,
                prompt_template=post_feedback_prompt,
                normalized_inputs=normalized_inputs,
                state_mapping=state_mapping,
                final_forecast=final_forecast,
                pre_feedback=pre_feedback_result,
                factor_snapshot=factor_snapshot_payload,
                dominant_factor_result=dominant_factor_payload,
                market_snapshot=market_snapshot,
                news_snapshot=news_snapshot,
                output_language=settings.output_language,
                normalized_inputs_payload=llm_normalized_inputs_payload,
            )
            post_feedback_status = "ok"
            reasoning_summary.append(
                f"LLM阶段耗时 post_forecast_feedback={perf_counter() - post_feedback_started:.2f}s"
            )
        except Exception:
            post_feedback_result = build_post_feedback_fallback(
                final_forecast=final_forecast,
                pre_feedback=pre_feedback_result,
            )
            post_feedback_status = "fallback"
            reasoning_summary.append("post_forecast_feedback 调用失败，已使用确定性 fallback。")

        artifact_paths["post_forecast_feedback"] = str(
            storage.save_artifact(
                run_id,
                stage="intermediate",
                artifact_name="post_forecast_feedback.json",
                payload=post_feedback_result.model_dump(mode="json"),
            )
        )
        analysis_flow.append(
            _flow_entry(
                stage="post_forecast_feedback",
                status=post_feedback_status,
                elapsed_seconds=perf_counter() - post_feedback_started,
                output_summary={
                    "forecast_support_map_count": len(post_feedback_result.forecast_support_map),
                    "forecast_opposition_map_count": len(post_feedback_result.forecast_opposition_map),
                    "monitoring_priorities_count": len(post_feedback_result.monitoring_priorities),
                    "next_run_questions_count": len(post_feedback_result.next_run_questions),
                },
                artifacts=["post_forecast_feedback"],
            )
        )
        hard_fail_issues = list(post_repair_rule_report.hard_fail_issues)
        soft_warnings = list(post_repair_rule_report.soft_warnings)
        info_notes = list(post_repair_rule_report.info_notes)
        rejection_reasons: list[str] = []
        publish_gate_checks: list[dict[str, Any]] = []

        if review_result.review_decision.review_status != AntiHindsightStatus.PASS:
            hard_fail_issues.append(
                GovernanceIssue(
                    code="REVIEW_STATUS_FAIL",
                    field="review_decision.review_status",
                    message="Reviewer marked review_status=FAIL",
                    severity=IssueSeverity.HARD_FAIL,
                )
            )
            reasoning_summary.append("审查结论为 FAIL，本次仅保留分析，不进入正式发布层")

        if hard_fail_issues:
            rejection_reasons.extend(f"{item.code}: {item.message}" for item in hard_fail_issues)
        rejection_reasons = list(dict.fromkeys(rejection_reasons))

        review_findings_payload = {
            "hard_fail_issues": [item.model_dump(mode="json") for item in hard_fail_issues],
            "soft_warnings": [item.model_dump(mode="json") for item in soft_warnings],
            "info_notes": [item.model_dump(mode="json") for item in info_notes],
        }

        if hard_fail_issues or review_result.review_decision.review_status == AntiHindsightStatus.FAIL:
            run_status_decision: Literal["approved", "review_warn", "review_fail", "input_stale"] = "review_fail"
            is_publishable = False
        elif soft_warnings or review_result.review_decision.soft_warn_count > 0:
            run_status_decision = "review_warn"
            is_publishable = True
        else:
            run_status_decision = "approved"
            is_publishable = True

        publish_gate_checks.append(
            _gate_check(
                name="review_status_pass",
                passed=review_result.review_decision.review_status == AntiHindsightStatus.PASS,
                detail=f"review_status={review_result.review_decision.review_status.value}",
            )
        )
        publish_gate_checks.append(
            _gate_check(
                name="hard_fail_issues",
                passed=not hard_fail_issues,
                detail=f"hard_fail_count={len(hard_fail_issues)}",
            )
        )
        publish_gate_checks.append(
            _gate_check(
                name="soft_warning_observed",
                passed=not soft_warnings,
                detail=f"soft_warn_count={len(soft_warnings)}",
                blocking=False,
            )
        )

        publish_gate_report = {
            "approved": is_publishable,
            "run_status": run_status_decision,
            "is_publishable": is_publishable,
            "checks": publish_gate_checks,
            "hard_fail_count": len(hard_fail_issues),
            "soft_warn_count": len(soft_warnings),
            "rejection_reasons": rejection_reasons,
        }
        decision_summary = (
            review_result.review_decision.decision_summary
            or (
                "Hard-fail governance findings present; analysis preserved but not publishable."
                if run_status_decision == "review_fail"
                else "Soft warnings present; publishable with caution."
                if run_status_decision == "review_warn"
                else "No hard governance issues; publishable."
            )
        )

        artifact_paths["final_forecast"] = str(
            storage.save_artifact(
                run_id,
                stage="final",
                artifact_name="final_forecast.json",
                payload=final_forecast.model_dump(mode="json"),
            )
        )

        if not is_publishable:
            artifact_paths["review_rejected"] = str(
                storage.save_artifact(
                    run_id,
                    stage="final",
                    artifact_name="review_rejected.json",
                    payload={
                        "publish_status": "rejected",
                        "run_status": run_status_decision,
                        "is_publishable": False,
                        "rejection_reasons": rejection_reasons,
                        "review_summary": review_result.review_summary,
                        "review_status": review_result.review_decision.review_status.value,
                        "review_findings": review_findings_payload,
                        "reference_levels": reference_levels_payload,
                        "draft_rule_report": draft_rule_report.model_dump(mode="json"),
                        "post_review_rule_report": post_review_rule_report.model_dump(mode="json"),
                        "post_repair_rule_report": post_repair_rule_report.model_dump(mode="json"),
                        "reviewed_forecast": review_result.reviewed_forecast.model_dump(mode="json"),
                        "auto_repaired_forecast": repaired_payload,
                        "pre_forecast_feedback": pre_feedback_result.model_dump(mode="json"),
                        "post_forecast_feedback": post_feedback_result.model_dump(mode="json"),
                        "factor_snapshot": factor_snapshot_payload,
                        "dominant_factor_result": dominant_factor_payload,
                        "dominant_factor_explainer": dominant_factor_explainer,
                        "earnings_revision_proxy": earnings_revision_proxy_summary,
                        "runtime_assertions": runtime_assertions,
                        "analysis_flow": analysis_flow,
                        "analysis_variants": analysis_variants,
                        "publish_gate_report": publish_gate_report,
                    },
                )
            )

        analysis_flow.append(
            _flow_entry(
                stage="publish_gate",
                status=run_status_decision,
                elapsed_seconds=0.0,
                output_summary={
                    "run_status": run_status_decision,
                    "is_publishable": is_publishable,
                    "review_status": review_status,
                    "hard_fail_count": len(hard_fail_issues),
                    "soft_warn_count": len(soft_warnings),
                },
                artifacts=["final_forecast", *([ "review_rejected"] if not is_publishable else [])],
            )
        )

        storage.save_forecast(
            run_id,
            final_forecast,
            run_status=run_status_decision,
            is_publishable=is_publishable,
            decision_summary=decision_summary,
            hard_fail_count=len(hard_fail_issues),
            soft_warn_count=len(soft_warnings),
            reference_levels=reference_levels_payload,
            review_findings=review_findings_payload,
            review_summary=review_summary_text,
        )
        run_completed_at = _finalize_run_timestamp()
        _persist_analysis_trace(
            publish_status="approved" if is_publishable else "rejected",
            run_status=run_status_decision,
            is_publishable=is_publishable,
            rejection_reasons=rejection_reasons,
            run_completed_at=run_completed_at,
        )
        storage.complete_run(run_id, status=run_status_decision.upper())

        return PipelineResult(
            run_id=run_id,
            final_forecast=final_forecast,
            artifact_paths=artifact_paths,
            publish_status="approved" if is_publishable else "rejected",
            run_status=run_status_decision,
            is_publishable=is_publishable,
            review_status=review_status,
            decision_summary=decision_summary,
            rejection_reasons=rejection_reasons,
            review_summary=review_summary_text,
            review_findings=review_findings_payload,
            reference_levels=reference_levels_payload,
            collected_at=collected_at,
            reviewed_at=reviewed_at,
            latest_news_at=latest_news_at,
            latest_market_at=latest_market_at,
            run_started_at=run_started_at_dt.isoformat(),
            run_completed_at=run_completed_at,
            market_universe=list(universe),
            market_snapshot=market_snapshot,
            news_snapshot=news_snapshot,
            reasoning_summary=reasoning_summary,
            state_snapshot=state_snapshot,
            confidence_snapshot=confidence_snapshot,
            runtime_assertions=runtime_assertions,
            analysis_flow=analysis_flow,
            analysis_variants=analysis_variants,
            publish_gate_report=publish_gate_report,
            market_snapshot_summary=pre_feedback_result.market_snapshot_summary,
            top_news_signals=[
                item.model_dump(mode="json") for item in pre_feedback_result.top_news_signals
            ],
            top_market_signals=[
                item.model_dump(mode="json") for item in pre_feedback_result.top_market_signals
            ],
            signal_conflicts=pre_feedback_result.signal_conflicts,
            forecast_support_map=post_feedback_result.forecast_support_map,
            forecast_opposition_map=post_feedback_result.forecast_opposition_map,
            monitoring_priorities=post_feedback_result.monitoring_priorities,
            next_run_questions=post_feedback_result.next_run_questions,
            pre_forecast_feedback=pre_feedback_result.model_dump(mode="json"),
            post_forecast_feedback=post_feedback_result.model_dump(mode="json"),
            factor_snapshot=factor_snapshot_payload,
            dominant_factor=dominant_factor_payload,
            dominant_factor_explainer=dominant_factor_explainer,
            earnings_revision_proxy_summary=earnings_revision_proxy_summary,
            earnings_proxy_source=earnings_proxy_source,
        )

    except Exception as exc:
        storage.complete_run(run_id, status="FAILED", error_message=str(exc))
        raise
    finally:
        if owns_storage:
            storage.close()
