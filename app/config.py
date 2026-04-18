"""Application configuration loaded from environment variables.

The project defaults to mock data and mock LLM output so it can run fully offline.
"""

from __future__ import annotations

from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REQUIRED_HARD_INDICATORS: tuple[str, str] = ("BTC", "USDJPY")


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
        default_factory=lambda: [
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
    )

    llm_provider: Literal["mock", "openai", "kimi", "minimax"] = "mock"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_model: str = "gpt-4.1-mini"
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 45.0
    llm_max_tokens: int = 1200
    llm_compact_news_items: int = 8
    llm_compact_text_chars: int = 320

    output_language: Literal["zh", "en"] = "zh"
    output_style: Literal["simple", "telegram", "full"] = "simple"

    use_live_data: bool = False
    strict_live_mode: bool = True
    collect_in_parallel: bool = True
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
    live_news_page_size: int = 12
    live_news_query: str = (
        "\"S&P 500\" OR \"Nasdaq 100\" OR \"Russell 2000\" OR "
        "\"VIX\" OR \"Federal Reserve\" OR \"10-year Treasury\" OR "
        "\"US dollar index\" OR \"WTI crude\" OR "
        "\"Bitcoin\" OR \"USD/JPY\" OR \"yen\""
    )
    live_news_domains: list[str] = Field(
        default_factory=lambda: [
            "reuters.com",
            "bloomberg.com",
            "cnbc.com",
            "wsj.com",
            "ft.com",
            "marketwatch.com",
            "investing.com",
            "barrons.com",
        ]
    )
    sec_rss_urls: list[str] = Field(
        default_factory=lambda: [
            "https://www.sec.gov/news/pressreleases.rss",
            "https://www.sec.gov/news/speeches.rss",
        ]
    )
    fmp_news_limit: int = 40

    earnings_proxy_top_tickers: list[str] = Field(
        default_factory=lambda: [
            "AAPL",
            "MSFT",
            "NVDA",
            "AMZN",
            "GOOGL",
            "META",
            "TSLA",
            "BRK.B",
            "JPM",
            "UNH",
            "XOM",
            "V",
            "MA",
            "AVGO",
            "LLY",
            "COST",
            "PG",
            "HD",
            "JNJ",
            "ABBV",
            "MRK",
            "PEP",
            "KO",
            "WMT",
            "BAC",
            "CVX",
            "AMD",
            "NFLX",
            "ADBE",
            "CRM",
        ]
    )
    earnings_proxy_live_max_tickers: int = 30

    factor_weight_earnings_revision: float = 0.32
    factor_weight_volatility: float = 0.22
    factor_weight_rates: float = 0.18
    factor_weight_dollar: float = 0.16
    factor_weight_energy_geopolitics: float = 0.12
    factor_dominant_tie_threshold: float = 0.10

    @field_validator("market_universe", mode="before")
    @classmethod
    def _coerce_market_universe(cls, value: object) -> list[str]:
        if isinstance(value, str):
            parsed = [item.strip() for item in value.split(",") if item.strip()]
            return cls._append_required_hard_indicators(parsed)
        if isinstance(value, list):
            parsed = [str(item).strip() for item in value if str(item).strip()]
            return cls._append_required_hard_indicators(parsed)
        raise ValueError("market_universe must be a CSV string or list")

    @staticmethod
    def _append_required_hard_indicators(items: list[str]) -> list[str]:
        ordered = list(items)
        for symbol in _REQUIRED_HARD_INDICATORS:
            if symbol not in ordered:
                ordered.append(symbol)
        return ordered

    @field_validator("live_news_domains", mode="before")
    @classmethod
    def _coerce_live_news_domains(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise ValueError("live_news_domains must be a CSV string or list")

    @field_validator("sec_rss_urls", mode="before")
    @classmethod
    def _coerce_sec_rss_urls(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise ValueError("sec_rss_urls must be a CSV string or list")

    @field_validator("earnings_proxy_top_tickers", mode="before")
    @classmethod
    def _coerce_earnings_tickers(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [item.strip().upper() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip().upper() for item in value if str(item).strip()]
        raise ValueError("earnings_proxy_top_tickers must be a CSV string or list")

    def news_from_iso(self) -> str:
        """Return ISO timestamp lower bound for live news query window."""
        from_dt = datetime.now(timezone.utc) - timedelta(hours=max(1, self.max_news_age_hours))
        return from_dt.isoformat().replace("+00:00", "Z")

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
