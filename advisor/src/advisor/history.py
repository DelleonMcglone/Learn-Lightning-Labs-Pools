"""Ingestion pipeline: a local, append-only history of node + market state.

``advisor ingest`` appends one compact JSONL record per run (cron-friendly).
History turns single-shot views into baselines — the first consumer is R6,
which compares today's chain fee against the recorded 7-day norm instead of
only a static ratio.

Plain JSONL, one file, no daemon: auditable with `jq`, trivially portable.
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Iterator, Optional

from .models import MarketSnapshot, NodeSnapshot

SCHEMA_VERSION = 1


def build_record(
    snap: NodeSnapshot, market: MarketSnapshot, ts: Optional[int] = None
) -> dict:
    """One compact, non-identifying time-series record.

    Deliberately excludes pubkeys/channel points (NFR3 applies to what we
    persist too — history may be shared for debugging).
    """
    active = [c for c in snap.channels if c.active]
    return {
        "v": SCHEMA_VERSION,
        "ts": int(ts if ts is not None else time.time()),
        "block_height": snap.identity.block_height,
        "inbound_sat": snap.total_inbound_sat,
        "outbound_sat": snap.total_outbound_sat,
        "onchain_sat": snap.balances.onchain_confirmed,
        "channels_total": len(snap.channels),
        "channels_active": len(active),
        "fees_sat_per_vb": {str(k): v for k, v in market.fees.sat_per_vb.items()}
        if market.fees.available else {},
        "pool_clearing_ppb": {
            str(k): v for k, v in market.pool.last_clearing_rate_ppb.items()
        } if market.pool.connected else {},
        "pool_bid_units_2016": (
            market.pool.depth[2016].bid_units
            if market.pool.connected and 2016 in market.pool.depth else None
        ),
        "loop_out_fee_sat": (
            market.loop.out_quote.total_fee_sat
            if market.loop.connected and market.loop.out_quote else None
        ),
        "loop_in_fee_sat": (
            market.loop.in_quote.total_fee_sat
            if market.loop.connected and market.loop.in_quote else None
        ),
    }


class HistoryStore:
    """Append-only JSONL store with time-windowed reads."""

    def __init__(self, path: Path):
        self.path = Path(path).expanduser()

    def append(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")

    def records(self, since_ts: Optional[int] = None) -> Iterator[dict]:
        if not self.path.exists():
            return
        with self.path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # tolerate a torn write
                if since_ts is None or rec.get("ts", 0) >= since_ts:
                    yield rec

    def count(self) -> int:
        return sum(1 for _ in self.records())

    # ------------------------------------------------------- baselines ----

    def fee_baseline_sat_vb(
        self, days: int = 7, target: int = 6, now: Optional[int] = None
    ) -> Optional[float]:
        """Median fee at `target` blocks over the lookback window.

        None until at least 3 observations exist — a baseline from fewer
        points is noise (same hedging rule as the IQR engine).
        """
        now = int(now if now is not None else time.time())
        values = [
            rec["fees_sat_per_vb"][str(target)]
            for rec in self.records(since_ts=now - days * 86_400)
            if str(target) in rec.get("fees_sat_per_vb", {})
        ]
        if len(values) < 3:
            return None
        return float(statistics.median(values))
