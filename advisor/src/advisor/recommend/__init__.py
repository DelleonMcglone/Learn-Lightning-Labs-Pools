"""Deterministic recommendation engine (SPEC M3): rules R1–R7 over
signals + market state, emitting ranked recommendations with computed
economics and ready-to-run commands."""

from .engine import recommend
from .models import Recommendation, RecommendationReport, Severity

__all__ = ["recommend", "Recommendation", "RecommendationReport", "Severity"]
