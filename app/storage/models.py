"""SQLAlchemy ORM models for run metadata and stored artifacts."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base declarative model class."""


class RunRecord(Base):
    """Top-level pipeline run record."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING", nullable=False)
    forecast_horizon: Mapped[str] = mapped_column(String(128), nullable=False)
    market_universe_json: Mapped[str] = mapped_column(Text, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ArtifactRecord(Base):
    """Artifact metadata for raw/intermediate/final JSON outputs."""

    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class ForecastRecord(Base):
    """Stored forecast snapshot per run (publishable or non-publishable)."""

    __tablename__ = "forecasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), unique=True, index=True)
    directional_bias: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    anti_hindsight_status: Mapped[str] = mapped_column(String(16), nullable=False)
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default="FAIL")
    run_status: Mapped[str] = mapped_column(String(32), nullable=False, default="review_fail")
    is_publishable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    decision_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    hard_fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    soft_warn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reference_levels_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_findings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
