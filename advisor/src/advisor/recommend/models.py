"""The Recommendation type (SPEC §6, FR8): plain advice + the data that
triggered it + computed economics + the exact command."""

from __future__ import annotations

from enum import IntEnum
from typing import Optional

from pydantic import BaseModel


class Severity(IntEnum):
    """Higher sorts first. CRITICAL = user-visible failure imminent."""

    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class Recommendation(BaseModel):
    rule: str                       # "R1".."R7"
    title: str
    severity: Severity
    summary: str                    # deterministic plain-language advice
    data: dict = {}                 # the signal/market values that triggered it
    est_cost_sat: Optional[int] = None
    est_benefit: str = ""
    command: str = ""               # ready-to-run; empty if informational
    caveats: list[str] = []         # feasibility notes, hedges (heuristic #12)

    @property
    def severity_label(self) -> str:
        return self.severity.name


class RecommendationReport(BaseModel):
    """Ranked output of the deterministic engine."""

    recommendations: list[Recommendation]
    skipped_rules: dict[str, str] = {}   # rule → why it didn't run
    node_alias: str = ""
    generated_offline: bool = True
