"""Storage gateway for run metadata, artifacts, and reviewed forecasts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, desc, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.schemas import FinalForecast
from app.storage.models import ArtifactRecord, Base, ForecastRecord, RunRecord


class Storage:
    """Persistence interface for SQLite/PostgreSQL-compatible backends."""

    def __init__(self, settings: Settings):
        self._settings = settings
        engine_kwargs: dict[str, object] = {"future": True}
        if settings.database_url.startswith("sqlite"):
            # SQLite runs are short-lived and benefit from deterministic connection lifecycle.
            engine_kwargs["poolclass"] = NullPool

        self._engine = create_engine(settings.database_url, **engine_kwargs)
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)

    def init_db(self) -> None:
        """Create database tables if they do not exist."""
        Base.metadata.create_all(self._engine)

    def close(self) -> None:
        """Release engine resources and underlying DB connections."""
        self._engine.dispose()

    def create_run(self, forecast_horizon: str, market_universe: list[str]) -> str:
        """Create run record with RUNNING status and return run id."""
        run_id = uuid4().hex
        with self._session_factory() as session:
            session.add(
                RunRecord(
                    id=run_id,
                    forecast_horizon=forecast_horizon,
                    market_universe_json=json.dumps(market_universe),
                    status="RUNNING",
                )
            )
            session.commit()
        return run_id

    def complete_run(self, run_id: str, status: str, error_message: str | None = None) -> None:
        """Mark run as completed/failed."""
        with self._session_factory() as session:
            run_record = session.get(RunRecord, run_id)
            if run_record is None:
                return
            run_record.status = status
            run_record.completed_at = datetime.now(timezone.utc)
            run_record.error_message = error_message
            session.commit()

    def save_artifact(self, run_id: str, stage: str, artifact_name: str, payload: dict) -> Path:
        """Persist JSON artifact to filesystem and store metadata row."""
        run_dir = Path(self._settings.artifacts_dir) / run_id / stage
        run_dir.mkdir(parents=True, exist_ok=True)

        artifact_path = run_dir / artifact_name
        serialized_payload = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        artifact_path.write_text(serialized_payload, encoding="utf-8")
        digest = hashlib.sha256(serialized_payload.encode("utf-8")).hexdigest()

        with self._session_factory() as session:
            session.add(
                ArtifactRecord(
                    run_id=run_id,
                    stage=stage,
                    artifact_name=artifact_name,
                    path=str(artifact_path.resolve()),
                    sha256=digest,
                )
            )
            session.commit()

        return artifact_path.resolve()

    def save_forecast(self, run_id: str, forecast: FinalForecast) -> None:
        """Persist reviewed final forecast payload in structured storage."""
        with self._session_factory() as session:
            session.add(
                ForecastRecord(
                    run_id=run_id,
                    directional_bias=forecast.directional_bias.value,
                    confidence=float(forecast.confidence),
                    anti_hindsight_status=forecast.anti_hindsight_status.value,
                    content_json=json.dumps(forecast.model_dump(mode="json"), ensure_ascii=False),
                )
            )
            session.commit()

    def get_latest_forecast(self) -> FinalForecast | None:
        """Fetch most recent reviewed forecast from storage."""
        with self._session_factory() as session:
            statement = select(ForecastRecord).order_by(desc(ForecastRecord.created_at)).limit(1)
            row = session.execute(statement).scalar_one_or_none()

        if row is None:
            return None
        return FinalForecast.model_validate(json.loads(row.content_json))
