"""Publish step: expose only review-approved forecast output."""

from __future__ import annotations

from app.schemas import AntiHindsightReviewResult, FinalForecast


def select_publishable_forecast(review_result: AntiHindsightReviewResult) -> FinalForecast:
    """Return final publishable forecast from review artifact.

    This enforces the rule that publishing may use only reviewed forecast output.
    """
    return review_result.reviewed_forecast
