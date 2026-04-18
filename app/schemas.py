"""Pydantic schemas for pipeline inputs, intermediates, and outputs."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DirectionalBias(str, Enum):
    """Directional market outlook for the configured horizon."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class AntiHindsightStatus(str, Enum):
    """Governance status from anti-hindsight review."""

    PASS = "PASS"
    FAIL = "FAIL"


class IssueSeverity(str, Enum):
    """Severity level for governance findings."""

    HARD_FAIL = "hard_fail"
    SOFT_WARN = "soft_warn"
    INFO = "info"


class GovernanceIssue(BaseModel):
    """Single structured governance finding."""

    code: str
    field: str
    message: str
    severity: IssueSeverity


class ReviewFindings(BaseModel):
    """Detailed review findings grouped by severity."""

    hard_fail_issues: list[GovernanceIssue] = Field(default_factory=list)
    soft_warnings: list[GovernanceIssue] = Field(default_factory=list)
    info_notes: list[GovernanceIssue] = Field(default_factory=list)


class RuleCheckReport(BaseModel):
    """Structured rule/governance report emitted by local checks."""

    has_hard_fail: bool
    has_soft_warn: bool
    hard_fail_issues: list[GovernanceIssue] = Field(default_factory=list)
    soft_warnings: list[GovernanceIssue] = Field(default_factory=list)
    info_notes: list[GovernanceIssue] = Field(default_factory=list)
    coverage: dict[str, bool] = Field(default_factory=dict)

    @property
    def has_blocking_issues(self) -> bool:
        """Backward-compatible alias for legacy gate checks."""
        return self.has_hard_fail

    @property
    def issues(self) -> list[GovernanceIssue]:
        """Backward-compatible alias for hard fail issues."""
        return self.hard_fail_issues

    @property
    def warnings(self) -> list[GovernanceIssue]:
        """Backward-compatible alias for soft warnings."""
        return self.soft_warnings


class NewsItem(BaseModel):
    """Normalized news item used in downstream analysis."""

    source: str
    source_type: Literal["newsapi", "sec", "fmp_news", "manual", "mock", "other"] = "other"
    source_reliability: Literal["very_high", "high", "medium", "unknown"] = "unknown"
    headline: str
    summary: str = ""
    url: str | None = None
    published_at: datetime


class MarketIndicator(BaseModel):
    """Normalized market indicator snapshot."""

    symbol: str
    name: str
    value: float
    previous_value: float | None = None
    change_pct: float | None = None
    unit: str = "index"
    as_of: datetime


class NormalizedInputs(BaseModel):
    """Canonical run input payload for the entire pipeline."""

    run_id: str
    collected_at: datetime
    forecast_horizon: str
    market_universe: list[str]
    news: list[NewsItem]
    indicators: list[MarketIndicator]
    state_variables: dict[str, Any]


class FactorDirection(str, Enum):
    """Factor direction on forward US-equity bias."""

    UP = "up"
    DOWN = "down"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class FactorStrength(str, Enum):
    """Signal strength of a factor score."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FactorSignal(BaseModel):
    """Single factor signal used by deterministic factor engine."""

    direction: FactorDirection
    score: float = Field(ge=-1.0, le=1.0)
    strength: FactorStrength
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    as_of: datetime

    @field_validator("evidence_refs", "limitations")
    @classmethod
    def _normalize_string_list(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]


class EarningsRevisionMetrics(BaseModel):
    """Core metrics for earnings revision proxy."""

    eps_avg_7d_delta: float | None = None
    eps_avg_30d_delta: float | None = None
    rating_upgrade_ratio: float | None = None
    coverage_change: float | None = None


class EarningsRevisionProxy(BaseModel):
    """FMP-based earnings revision proxy for top sample basket."""

    generated_at: datetime
    as_of: datetime
    coverage_status: Literal["full", "partial", "none"] = "none"
    sample_size: int = Field(ge=0, default=0)
    available_series: int = Field(ge=0, default=0)
    metrics: EarningsRevisionMetrics = Field(default_factory=EarningsRevisionMetrics)
    signal: FactorDirection
    score: float = Field(ge=-1.0, le=1.0)
    summary: str
    limitations: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator("limitations", "evidence_refs")
    @classmethod
    def _normalize_proxy_strings(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]


class FactorSnapshot(BaseModel):
    """Deterministic five-factor state snapshot."""

    generated_at: datetime
    earnings_revision: FactorSignal
    volatility: FactorSignal
    rates: FactorSignal
    dollar: FactorSignal
    energy_geopolitics: FactorSignal
    weights: dict[str, float] = Field(default_factory=dict)
    weighted_scores: dict[str, float] = Field(default_factory=dict)


class DominantFactorResult(BaseModel):
    """Dominant factor decision from weighted scores."""

    dominant_factor: str
    dominant_factors: list[str]
    tie_detected: bool = False
    tie_threshold: float = Field(ge=0.0, default=0.10)
    scoreboard: dict[str, float] = Field(default_factory=dict)
    explainer: str


class InputStalenessItem(BaseModel):
    """Single stale input finding for freshness governance."""

    source_type: Literal["news", "market"]
    key: str
    observed_at: datetime
    age_minutes: float
    threshold_minutes: float


class InputFreshnessReport(BaseModel):
    """Structured freshness report generated before LLM calls."""

    checked_at: datetime
    max_news_age_hours: int
    max_market_age_minutes: int
    news_items_checked: int
    market_items_checked: int
    stale_news: list[InputStalenessItem] = Field(default_factory=list)
    stale_market: list[InputStalenessItem] = Field(default_factory=list)
    has_blocking_issues: bool
    summary: str


class ConfidenceComponentScores(BaseModel):
    """Component scores used by deterministic confidence model."""

    scenario_alignment: float
    event_consensus: float
    cross_asset_confirmation: float
    evidence_balance: float


class ConfidencePenaltyScores(BaseModel):
    """Penalty terms used by deterministic confidence model."""

    freshness_penalty: float
    risk_penalty: float


class ConfidenceBreakdown(BaseModel):
    """Auditable confidence formula output."""

    formula: str
    directional_bias: str
    components: ConfidenceComponentScores
    penalties: ConfidencePenaltyScores
    raw_confidence: float
    final_confidence: float
    notes: list[str] = Field(default_factory=list)


class StructuredEvent(BaseModel):
    """Event extracted from current observable data."""

    event_id: str
    category: Literal["macro", "policy", "earnings", "geopolitics", "market", "other"]
    description: str
    impact_bias: Literal["up", "down", "neutral"]
    impact_pathway: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[str]
    source_type: str | None = None
    source_reliability: str | None = None


class EventExtractionResult(BaseModel):
    """Structured event output from the extraction stage."""

    generated_at: datetime
    summary: str
    events: list[StructuredEvent]


class StateScenario(BaseModel):
    """Scenario branch under current observed state."""

    name: str
    probability: float = Field(ge=0.0, le=1.0)
    directional_implication: DirectionalBias
    key_conditions: list[str]


class StateMappingResult(BaseModel):
    """Mapped macro/market state with explicit scenarios."""

    generated_at: datetime
    regime_label: str
    growth_state: str
    inflation_state: str
    liquidity_state: str
    volatility_state: str
    cross_asset_signals: list[str]
    scenarios: list[StateScenario]
    narrative: str


class ReferenceLevels(BaseModel):
    """Structured appendix for reference levels/thresholds."""

    support_levels: list[str] = Field(default_factory=list)
    resistance_levels: list[str] = Field(default_factory=list)
    risk_triggers: list[str] = Field(default_factory=list)
    confirmation_levels: list[str] = Field(default_factory=list)

    @field_validator(
        "support_levels",
        "resistance_levels",
        "risk_triggers",
        "confirmation_levels",
    )
    @classmethod
    def _normalize_level_list(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]


class ForecastBase(BaseModel):
    """Shared forecast contract used by draft and reviewed outputs."""

    forecast_horizon: str
    market_universe: list[str]
    directional_bias: DirectionalBias
    confidence: float = Field(ge=0.0, le=1.0)
    dominant_drivers: list[str]
    supportive_evidence: list[str]
    opposing_evidence: list[str]
    upside_triggers: list[str]
    downside_triggers: list[str]
    invalidation_conditions: list[str]
    monitoring_list: list[str]
    reference_levels: ReferenceLevels = Field(default_factory=ReferenceLevels)
    final_thesis: str

    @field_validator(
        "dominant_drivers",
        "supportive_evidence",
        "opposing_evidence",
        "upside_triggers",
        "downside_triggers",
        "invalidation_conditions",
        "monitoring_list",
    )
    @classmethod
    def _normalize_non_empty_lists(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        if not cleaned:
            raise ValueError("List field must contain at least one non-empty string")
        return cleaned


class ForecastDraft(ForecastBase):
    """Forecast candidate before anti-hindsight review."""

    generated_at: datetime


class FinalForecast(ForecastBase):
    """Final reviewed forecast artifact (publishable or not)."""

    model_config = ConfigDict(populate_by_name=True)

    generated_at: datetime
    review_status: AntiHindsightStatus = Field(alias="anti_hindsight_status")

    @property
    def anti_hindsight_status(self) -> AntiHindsightStatus:
        """Backward-compatible alias for old call sites."""
        return self.review_status


class StateAndForecastResult(BaseModel):
    """Combined call output containing state-map and forecast-draft."""

    state_mapping: StateMappingResult
    forecast_draft: ForecastDraft


class FeedbackSignal(BaseModel):
    """Structured signal summary emitted by feedback layers."""

    signal: str
    direction: Literal["up", "down", "neutral", "mixed"] = "neutral"
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    evidence_refs: list[str] = Field(default_factory=list)
    rationale: str = ""

    @field_validator("signal", "rationale")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("evidence_refs")
    @classmethod
    def _normalize_refs(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]


class PreForecastFeedback(BaseModel):
    """Prompt-driven visibility layer before forecast publish gate."""

    generated_at: datetime
    market_snapshot_summary: list[str] = Field(default_factory=list)
    top_news_signals: list[FeedbackSignal] = Field(default_factory=list)
    top_market_signals: list[FeedbackSignal] = Field(default_factory=list)
    signal_conflicts: list[str] = Field(default_factory=list)

    @field_validator("market_snapshot_summary", "signal_conflicts")
    @classmethod
    def _normalize_list(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]


class PostForecastFeedback(BaseModel):
    """Prompt-driven mapping from observed inputs to forecast stance."""

    generated_at: datetime
    forecast_support_map: list[str] = Field(default_factory=list)
    forecast_opposition_map: list[str] = Field(default_factory=list)
    monitoring_priorities: list[str] = Field(default_factory=list)
    next_run_questions: list[str] = Field(default_factory=list)

    @field_validator(
        "forecast_support_map",
        "forecast_opposition_map",
        "monitoring_priorities",
        "next_run_questions",
    )
    @classmethod
    def _normalize_list(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]


class ReviewDecision(BaseModel):
    """Top-level review decision used by publish gate."""

    review_status: AntiHindsightStatus
    is_publishable: bool
    decision_summary: str
    hard_fail_count: int = Field(ge=0, default=0)
    soft_warn_count: int = Field(ge=0, default=0)


class AntiHindsightReviewResult(BaseModel):
    """Review artifact containing governance verdict and reviewed forecast."""

    model_config = ConfigDict(populate_by_name=True)

    reviewed_at: datetime
    review_decision: ReviewDecision
    review_findings: ReviewFindings
    review_summary: str
    reviewed_forecast: FinalForecast
    reference_levels: ReferenceLevels = Field(default_factory=ReferenceLevels)

    @property
    def anti_hindsight_status(self) -> AntiHindsightStatus:
        """Backward-compatible alias for old call sites."""
        return self.review_decision.review_status

    @property
    def issues(self) -> list[str]:
        """Flatten hard-fail findings for compatibility with legacy code."""
        return [f"{item.code}: {item.message}" for item in self.review_findings.hard_fail_issues]
