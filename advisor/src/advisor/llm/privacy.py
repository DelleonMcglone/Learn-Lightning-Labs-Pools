"""Privacy filter for LLM prompts (SPEC NFR3) and the number contract
(SPEC NFR4).

Two jobs, both unit-tested:

1. ``sanitize_report`` — nothing identifying leaves the machine. Every peer
   pubkey and channel point (including the 16-char prefixes used in
   summaries, and the node's own alias/pubkey) is replaced with a stable
   alias like ``peer-A`` / ``channel-1`` before the report is serialized
   into a prompt.

2. ``collect_numeric_facts`` / ``narrative_violations`` — the LLM narrates,
   it does not compute. Any number ≥ NUMBER_GUARD_MIN appearing in model
   prose must already exist in the deterministic fact set, or the narrative
   is rejected and the caller falls back to the engine's own summary.
"""

from __future__ import annotations

import re
import string
from typing import Iterable, Tuple

from ..models import NodeSnapshot
from ..recommend.models import RecommendationReport
from ..signals import NodeSignals

# Prose numbers below this are allowed freely (percentages, counts, block
# targets); at or above it they must come from the fact sheet.
NUMBER_GUARD_MIN = 1_000

# Summaries truncate identifiers to this many chars before an ellipsis.
_PREFIX_LEN = 16


def _peer_alias(i: int) -> str:
    letters = string.ascii_uppercase
    return "peer-" + (letters[i] if i < 26 else f"Z{i}")


def build_alias_map(snap_channels: Iterable, node_pubkey: str = "",
                    node_alias: str = "") -> dict:
    """Map every identifying string (and its display prefix) to an alias."""
    aliases: dict = {}
    if node_pubkey:
        aliases[node_pubkey] = "this-node"
        aliases[node_pubkey[:_PREFIX_LEN]] = "this-node"
    if node_alias:
        aliases[node_alias] = "this-node"
    for i, ch in enumerate(snap_channels):
        aliases[ch.peer_pubkey] = _peer_alias(i)
        aliases[ch.peer_pubkey[:_PREFIX_LEN]] = _peer_alias(i)
        aliases[ch.chan_point] = f"channel-{i + 1}"
        txid = ch.chan_point.split(":")[0]
        aliases[txid] = f"channel-{i + 1}-funding"
    return aliases


def _apply_aliases(value, aliases: dict):
    if isinstance(value, str):
        for needle, alias in aliases.items():
            if needle and needle in value:
                value = value.replace(needle, alias)
        return value
    if isinstance(value, dict):
        return {k: _apply_aliases(v, aliases) for k, v in value.items()}
    if isinstance(value, list):
        return [_apply_aliases(v, aliases) for v in value]
    return value


def sanitize_report(
    report: RecommendationReport, sig: NodeSignals, snap: NodeSnapshot
) -> Tuple[dict, dict]:
    """Produce the prompt-safe payload and the alias map used.

    Longest identifiers are replaced first so prefixes never leave partial
    matches behind.
    """
    aliases = dict(sorted(
        build_alias_map(
            snap.channels, snap.identity.pubkey, snap.identity.alias
        ).items(),
        key=lambda kv: len(kv[0]), reverse=True,
    ))

    payload = {
        "node": {
            "total_inbound_sat": sig.total_inbound_sat,
            "total_outbound_sat": sig.total_outbound_sat,
            "inbound_share": round(sig.inbound_ratio, 4),
            "channels_total": sig.channels_total,
            "channels_one_sided": sig.channels_one_sided,
            "onchain_confirmed_sat": snap.balances.onchain_confirmed,
        },
        "recommendations": [
            {
                "id": f"{r.rule}-{i}",
                "rule": r.rule,
                "severity": r.severity.name,
                "title": r.title,
                "summary": r.summary,
                "data": r.data,
                "est_cost_sat": r.est_cost_sat,
                "est_benefit": r.est_benefit,
                "caveats": r.caveats,
                # sanitized like everything else (funding txids → aliases);
                # chat must quote this verbatim, never reconstruct it
                "command": r.command,
            }
            for i, r in enumerate(report.recommendations)
        ],
        "skipped": report.skipped_rules,
    }
    return _apply_aliases(payload, aliases), aliases


# ------------------------------------------------------- number contract --

_NUM_RE = re.compile(r"\d[\d,]*\.?\d*")


def collect_numeric_facts(report: RecommendationReport,
                          sig: NodeSignals, snap: NodeSnapshot) -> set:
    """Every number the deterministic layer computed — the allowed set."""
    facts: set = set()

    def _add(v) -> None:
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            facts.add(round(float(v), 4))
            facts.add(float(int(v)))
        elif isinstance(v, dict):
            for x in v.values():
                _add(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                _add(x)
        elif isinstance(v, str):
            for m in _NUM_RE.findall(v):
                try:
                    facts.add(round(float(m.replace(",", "")), 4))
                except ValueError:
                    pass

    for r in report.recommendations:
        _add(r.data)
        _add(r.est_cost_sat)
        _add(r.summary)
        _add(r.est_benefit)
        _add(r.caveats)
        _add(r.command)
    for v in (sig.total_inbound_sat, sig.total_outbound_sat,
              snap.balances.onchain_confirmed,
              snap.balances.onchain_unconfirmed):
        _add(v)
    return facts


def narrative_violations(text: str, facts: set) -> list:
    """Numbers ≥ NUMBER_GUARD_MIN in prose that aren't deterministic facts."""
    bad = []
    for m in _NUM_RE.findall(text):
        try:
            val = float(m.replace(",", ""))
        except ValueError:
            continue
        if val >= NUMBER_GUARD_MIN and round(val, 4) not in facts:
            bad.append(m)
    return bad
