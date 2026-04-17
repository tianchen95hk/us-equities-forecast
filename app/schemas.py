"""Pydantic schemas for pipeline inputs, intermediates, and outputs."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class DirectionalBias(str, Enum):
    """Directional market outlook for the configured horizon."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class AntiHindsightStatus(str, Enum):
    """Governance status from anti-hindsight review."""

    PASS = "PASS"
    FAIL = "FAIL"


class RuleSeverity(str, Enum):
    """Severity level for rule-engine findings."""

    BLOCKING = "blocking"
    WARNING = "warning"


class RuleCheckItem(BaseModel):
    """Single structured rule finding."""

    code: str
    message: str
    severity: RuleSeverity


class RuleCheckReport(BaseModel):
    """Structured report emitted by rule-engine checks."""

    has_blocking_issues: bool
    issues: list[RuleCheckItem] = Field(default_factory=list)
    warnings: list[RuleCheckItem] = Field(default_factory=list)
    coverage: dict[str, bool] = Field(default_factory=dict)


class NewsItem(BaseModel):
    """Normalized news item used in downstream analysis."""

    source: str
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
    """Final review-approved forecast artifact."""

    generated_at: datetime
    anti_hindsight_status: AntiHindsightStatus


class StateAndForecastResult(BaseModel):
    """Combined call output containing state-map and forecast-draft."""

    state_mapping: StateMappingResult
    forecast_draft: ForecastDraft


class AntiHindsightReviewResult(BaseModel):
    """Review artifact containing governance verdict and reviewed forecast."""

    reviewed_at: datetime
    anti_hindsight_status: AntiHindsightStatus
    issues: list[str] = Field(default_factory=list)
    review_summary: str
    reviewed_forecast: FinalForecast
