"""Shared application exceptions for clearer error boundaries."""

from __future__ import annotations


class ForecastAppError(RuntimeError):
    """Base exception for application-specific failures."""


class PromptLoadError(ForecastAppError):
    """Raised when a prompt template cannot be loaded."""


class CollectorError(ForecastAppError):
    """Raised when collector input or fetch behavior fails."""


class PipelineStepError(ForecastAppError):
    """Raised when a pipeline stage fails in a controlled manner."""


class RuleViolationError(ForecastAppError):
    """Raised when forecast output violates governance rules."""
