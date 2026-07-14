"""Run the rules, rank the output (SPEC M3, FR6).

Ranking is deterministic: severity first, then estimated benefit proxy
(cost-effectiveness), then rule order. The LLM layer (M4) may re-rank and
re-phrase, but this ordering must always stand on its own (NFR4: the
`--offline` report is the auditable baseline).
"""

from __future__ import annotations

from ..models import MarketSnapshot, NodeSnapshot
from ..signals import NodeSignals
from .models import Recommendation, RecommendationReport
from .rules import CHAIN_TOUCHING_RULES, RULES, r6_defer_onchain


def recommend(
    snap: NodeSnapshot, sig: NodeSignals, market: MarketSnapshot,
    fee_baseline_sat_vb=None,
) -> RecommendationReport:
    recs: list[Recommendation] = []
    skipped: dict[str, str] = {}

    if sig.channels_total == 0:
        skipped["all"] = "no channels — open a first channel to begin"

    for rule_fn in RULES:
        recs.extend(rule_fn(snap, sig, market))

    # R6 last: it needs to know how many chain-touching recs exist.
    chain_touching = sum(1 for r in recs if r.rule in CHAIN_TOUCHING_RULES)
    recs.extend(r6_defer_onchain(
        snap, sig, market, chain_touching,
        baseline_sat_vb=fee_baseline_sat_vb,
    ))

    # Record why market-dependent rules had nothing to price.
    if not market.pool.connected:
        skipped["pool"] = "poold not reachable"
    if not market.loop.connected:
        skipped["loop"] = "loopd not reachable"
    if not market.fees.available:
        skipped["fees"] = "fee estimates unavailable"
    from .rules import MIN_CHANNELS_FOR_OUTLIERS
    if 0 < sig.channels_considered < MIN_CHANNELS_FOR_OUTLIERS:
        skipped["R3"] = (
            f"only {sig.channels_considered} comparable channels — IQR "
            f"outlier evidence too weak below {MIN_CHANNELS_FOR_OUTLIERS}"
        )

    recs.sort(key=_rank_key, reverse=True)
    return RecommendationReport(
        recommendations=recs,
        skipped_rules=skipped,
        node_alias=snap.identity.alias,
        generated_offline=True,
    )


def _rank_key(r: Recommendation):
    # Cost-effectiveness proxy: prefer actionable (has command), cheaper recs
    # within the same severity band.
    has_command = 1 if r.command else 0
    cost = r.est_cost_sat if r.est_cost_sat is not None else 1 << 40
    return (int(r.severity), has_command, -cost)
