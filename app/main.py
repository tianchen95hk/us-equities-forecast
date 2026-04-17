"""CLI scheduler entrypoint and optional FastAPI exposure."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.exceptions import ForecastAppError
from app.pipeline.orchestrator import PipelineResult, run_pipeline
from app.presentation.formatters import format_cli_output
from app.storage.db import Storage


class RunRequest(BaseModel):
    """API payload for one pipeline run."""

    news_file: str | None = None
    market_file: str | None = None
    forecast_horizon: str | None = None
    max_news_age_hours: int | None = None
    max_market_age_minutes: int | None = None
    enforce_input_freshness: bool | None = None


def create_api() -> FastAPI:
    """Build FastAPI app with lightweight operational endpoints."""
    settings = get_settings()
    storage = Storage(settings)
    storage.init_db()

    app = FastAPI(title="US Equities Forecast System", version="0.3.0")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": settings.app_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @app.post("/run")
    def run_once(request: RunRequest) -> dict[str, Any]:
        try:
            result = run_pipeline(
                settings,
                news_file=request.news_file,
                market_file=request.market_file,
                forecast_horizon=request.forecast_horizon,
                max_news_age_hours=request.max_news_age_hours,
                max_market_age_minutes=request.max_market_age_minutes,
                enforce_input_freshness=request.enforce_input_freshness,
            )
        except ForecastAppError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _pipeline_result_to_response(result)

    @app.get("/forecast/latest")
    def latest_forecast() -> dict[str, Any]:
        forecast = storage.get_latest_forecast()
        if not forecast:
            raise HTTPException(status_code=404, detail="No forecast found")
        return forecast.model_dump(mode="json")

    return app


def _pipeline_result_to_response(result: PipelineResult) -> dict[str, Any]:
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
        payload.update(
            {
                "directional_bias": result.final_forecast.directional_bias.value,
                "confidence": result.final_forecast.confidence,
                "anti_hindsight_status": result.final_forecast.anti_hindsight_status.value,
                "final_forecast": result.final_forecast.model_dump(mode="json"),
            }
        )
    else:
        payload.update(
            {
                "directional_bias": None,
                "confidence": None,
                "anti_hindsight_status": None,
            }
        )
    return payload


def run_cli(args: argparse.Namespace) -> int:
    """Run one full pipeline pass via CLI and print JSON summary."""
    settings = get_settings()
    if args.live:
        settings.use_live_data = True

    output_language = args.output_lang or settings.output_language
    output_style = args.output_style or settings.output_style

    try:
        result = run_pipeline(
            settings=settings,
            news_file=args.news_file,
            market_file=args.market_file,
            forecast_horizon=args.forecast_horizon,
            max_news_age_hours=args.max_news_age_hours,
            max_market_age_minutes=args.max_market_age_minutes,
            enforce_input_freshness=(
                False if args.disable_freshness_gate else None
            ),
        )
    except ForecastAppError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, indent=2, ensure_ascii=False))
        return 1

    rendered = format_cli_output(result, language=output_language, style=output_style)
    print(json.dumps(rendered, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build command line parser."""
    parser = argparse.ArgumentParser(description="US equities directional forecast pipeline")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run one full pipeline pass")
    run_parser.add_argument("--news-file", default=None, help="Optional manual news JSON file")
    run_parser.add_argument("--market-file", default=None, help="Optional manual market JSON file")
    run_parser.add_argument("--forecast-horizon", default=None, help="Override forecast horizon")
    run_parser.add_argument("--live", action="store_true", help="Attempt live APIs before mock fallback")
    run_parser.add_argument(
        "--max-news-age-hours",
        type=int,
        default=None,
        help="Override news freshness threshold in hours (default from .env is 72).",
    )
    run_parser.add_argument(
        "--max-market-age-minutes",
        type=int,
        default=None,
        help="Override market freshness threshold in minutes (default from .env is 60).",
    )
    run_parser.add_argument(
        "--disable-freshness-gate",
        action="store_true",
        help="Disable hard freshness rejection for this run.",
    )
    run_parser.add_argument(
        "--output-lang",
        choices=["zh", "en"],
        default=None,
        help="CLI output language. Defaults to OUTPUT_LANGUAGE from .env.",
    )
    run_parser.add_argument(
        "--output-style",
        choices=["simple", "telegram", "full"],
        default=None,
        help="CLI output style. Defaults to OUTPUT_STYLE from .env.",
    )

    serve_parser = subparsers.add_parser("serve", help="Run FastAPI server")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", default=8000, type=int)
    serve_parser.add_argument("--reload", action="store_true")

    return parser


def main() -> int:
    """Process entrypoint for scheduler and local CLI usage."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command in {None, "run"}:
        return run_cli(args)

    if args.command == "serve":
        uvicorn.run("app.main:api", host=args.host, port=args.port, reload=args.reload)
        return 0

    parser.print_help()
    return 1


api = create_api()


if __name__ == "__main__":
    raise SystemExit(main())
