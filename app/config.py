"""Application configuration loaded from environment variables.

The project defaults to mock data and mock LLM output so it can run fully offline.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for collectors, pipeline, and storage."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "us-equities-forecast"
    environment: str = "local"

    database_url: str = "sqlite:///./data/forecast.db"
    artifacts_dir: str = "./artifacts"
    prompts_dir: str = "./prompts"

    mock_news_file: str = "./data/mock/news_latest.json"
    mock_market_file: str = "./data/mock/market_latest.json"
    latest_news_cache_file: str = "./data/cache/news_latest_available.json"
    latest_market_cache_file: str = "./data/cache/market_latest_available.json"

    forecast_horizon: str = "5 trading days"
    market_universe: list[str] = Field(
        default_factory=lambda: ["SPY", "QQQ", "IWM", "VIX", "US10Y", "DXY", "OIL"]
    )

    llm_provider: Literal["mock", "openai", "kimi", "minimax"] = "mock"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_model: str = "gpt-4.1-mini"
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 45.0

    output_language: Literal["zh", "en"] = "zh"
    output_style: Literal["simple", "full"] = "simple"

    use_live_data: bool = False
    request_timeout_seconds: float = 15.0
    user_agent: str = "macro-forecast-system/0.1"
    enforce_input_freshness: bool = True
    max_news_age_hours: int = 72
    max_market_age_minutes: int = 60
    allow_latest_available_fallback: bool = True
    latest_available_max_news_age_hours: int = 168
    latest_available_max_market_age_minutes: int = 10080

    fmp_api_key: str | None = None
    fmp_base_url: str = "https://financialmodelingprep.com/stable"

    news_api_key: str | None = None
    news_api_url: str = "https://newsapi.org/v2/everything"

    @field_validator("market_universe", mode="before")
    @classmethod
    def _coerce_market_universe(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise ValueError("market_universe must be a CSV string or list")

    def ensure_directories(self) -> None:
        """Create required local directories if they do not exist."""
        Path("./data").mkdir(parents=True, exist_ok=True)
        Path("./data/cache").mkdir(parents=True, exist_ok=True)
        Path(self.artifacts_dir).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings object for process lifetime."""
    settings = Settings()
    settings.ensure_directories()
    return settings
