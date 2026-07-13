"""Pool market collector (SPEC FR3).

Uses the ``pool`` CLI's JSON output as the interface to a running ``poold``
(the CLI is a stable JSON surface; a poolrpc gRPC client is the later
upgrade path). Degrades gracefully: if the binary or daemon is unavailable,
returns ``connected=False`` and market-dependent rules skip.

Parsing is split from I/O so it can be unit-tested on captured fixtures from
the real testnet auctioneer (see setup/pool.md).
"""

from __future__ import annotations

import json
import subprocess
from typing import Optional

from ..models import PoolDepth, PoolMarket, PoolOrder


def _run_pool(pool_bin: str, network: str, args: list, timeout: float = 15.0
              ) -> Optional[dict]:
    """Run a pool CLI subcommand, returning parsed JSON or None."""
    cmd = [pool_bin, f"--network={network}", *args]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        return json.loads(out.stdout)
    except Exception:
        return None


# ------------------------------------------------------------- parsing ----

def parse_depth(market_info: dict) -> dict:
    """getinfo.market_info → {duration_blocks: PoolDepth} (tiers summed)."""
    def _sum(entries) -> int:
        return sum(int(e.get("value", 0)) for e in entries or [])

    depth = {}
    for blocks, m in (market_info or {}).items():
        depth[int(blocks)] = PoolDepth(
            asks=_sum(m.get("num_asks")),
            bids=_sum(m.get("num_bids")),
            ask_units=_sum(m.get("ask_open_interest_units")),
            bid_units=_sum(m.get("bid_open_interest_units")),
        )
    return depth


def parse_clearing_rates(snapshot: list) -> dict:
    """auction snapshot → {duration_blocks: clearing_price_rate_ppb} from the
    most recent batch that matched each market."""
    rates: dict = {}
    for batch in snapshot or []:  # newest first
        for blocks, market in (batch.get("matched_markets") or {}).items():
            b = int(blocks)
            if b not in rates and market.get("clearing_price_rate"):
                rates[b] = int(market["clearing_price_rate"])
    return rates


def parse_account_available(accounts: Optional[dict]) -> int:
    """accounts list → total available sats across OPEN accounts."""
    total = 0
    for acct in (accounts or {}).get("accounts", []):
        if acct.get("state") == "OPEN":
            total += int(acct.get("available_balance", acct.get("value", 0)))
    return total


# Order states that still rest on the book.
_ACTIVE_ORDER_STATES = {"ORDER_SUBMITTED", "ORDER_PARTIALLY_FILLED"}


def parse_own_orders(orders: Optional[dict]) -> list:
    """orders list → our active resting orders (bids + asks)."""
    result = []
    for side in ("bids", "asks"):
        for o in (orders or {}).get(side, []):
            details = o.get("details", {})
            if details.get("state") not in _ACTIVE_ORDER_STATES:
                continue
            result.append(
                PoolOrder(
                    side=side[:-1],
                    amt_sat=int(details.get("amt", 0)),
                    rate_ppb=int(details.get("rate_fixed", 0)),
                    duration_blocks=int(o.get("lease_duration_blocks", 0)),
                    state=details.get("state", ""),
                    units=int(details.get("units", 0)),
                )
            )
    return result


def build_pool_market(
    fee: Optional[dict],
    durations: Optional[dict],
    next_batch: Optional[dict],
    info: Optional[dict],
    snapshot: Optional[list],
    accounts: Optional[dict] = None,
    orders: Optional[dict] = None,
) -> PoolMarket:
    """Assemble a PoolMarket from raw CLI payloads (any may be None)."""
    if info is None and fee is None:
        return PoolMarket(connected=False)

    exec_fee = (fee or {}).get("execution_fee", {})
    buckets = (durations or {}).get("lease_duration_buckets", {})
    return PoolMarket(
        connected=True,
        exec_fee_base_sat=int(exec_fee.get("base_fee", 0)),
        exec_fee_rate_ppm=int(exec_fee.get("fee_rate", 0)),
        lease_durations={int(k): v for k, v in buckets.items()},
        next_batch_feerate_sat_kw=int(
            (next_batch or {}).get("fee_rate_sat_per_kw", 0)
        ),
        next_batch_clear_unix=int((next_batch or {}).get("clear_timestamp", 0)),
        depth=parse_depth((info or {}).get("market_info", {})),
        last_clearing_rate_ppb=parse_clearing_rates(snapshot or []),
        account_available_sat=parse_account_available(accounts),
        own_orders=parse_own_orders(orders),
    )


# ------------------------------------------------------------ collector ----

def collect_pool_market(pool_bin: str, network: str) -> PoolMarket:
    """Query a running poold via the pool CLI; never raises."""
    info = _run_pool(pool_bin, network, ["getinfo"])
    if info is None:
        return PoolMarket(connected=False)
    return build_pool_market(
        fee=_run_pool(pool_bin, network, ["auction", "fee"]),
        durations=_run_pool(pool_bin, network, ["auction", "leasedurations"]),
        next_batch=_run_pool(pool_bin, network, ["auction", "nextbatchinfo"]),
        info=info,
        snapshot=_run_pool(pool_bin, network, ["auction", "snapshot"]),
        accounts=_run_pool(pool_bin, network, ["accounts", "list"]),
        orders=_run_pool(pool_bin, network, ["orders", "list"]),
    )
