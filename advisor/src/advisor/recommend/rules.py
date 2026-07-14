"""The R1–R7 deterministic rules (SPEC §5).

Each rule is a pure function: (snapshot, signals, market) → recommendations.
Rules compute every number via economics.py and state feasibility honestly
in `caveats` (knowledge/03-heuristics #12: never overstate certainty).
"""

from __future__ import annotations

from typing import Optional

from ..models import MarketSnapshot, NodeSnapshot
from ..signals import NodeSignals
from . import economics as econ
from .models import Recommendation, Severity

# Tunables (documented defaults; overridable later via config).
INBOUND_TARGET_SHARE = 0.5      # receiver: aim for half of capacity inbound
INBOUND_LOW_SHARE = 0.2         # below this → recommend acquiring
INBOUND_CRITICAL_SHARE = 0.05   # below this → receive failure imminent
OUTBOUND_LOW_SHARE = 0.2
MIN_CHANNELS_FOR_OUTLIERS = 5   # heuristic #6: IQR weak below this
GOOD_UPTIME = 0.95
REBALANCE_FEE_PPM_MAX = 1_000   # upper bound used for rebalance cost estimate
FEE_ELEVATED_RATIO = 3.0        # fast/economy ratio considered "hot"
DEFAULT_DURATION_BLOCKS = 2016  # the liquid Pool bucket


def _fmt(n: int) -> str:
    return f"{n:,} sat"


# ----------------------------------------------------------------- R1 ----

RUNWAY_WARN_DAYS = 7.0      # trend says inbound gone within a week → fire
RUNWAY_CRITICAL_DAYS = 3.0  # …within 3 days → critical


def r1_acquire_inbound(
    snap: NodeSnapshot, sig: NodeSignals, market: MarketSnapshot,
    inbound_trend_sat_per_day: Optional[float] = None,
) -> list:
    """Low inbound headroom → price Loop Out vs. a Pool bid, side by side.

    Two triggers: the static share threshold, and — when ingestion history
    provides a trend — the *runway*: even a healthy-looking share fires if
    inbound is draining fast enough to run dry within RUNWAY_WARN_DAYS.
    """
    if sig.channels_total == 0:
        return []

    runway_days: Optional[float] = None
    if (inbound_trend_sat_per_day is not None
            and inbound_trend_sat_per_day < 0 and sig.total_inbound_sat > 0):
        runway_days = sig.total_inbound_sat / -inbound_trend_sat_per_day

    share_low = sig.inbound_ratio < INBOUND_LOW_SHARE
    runway_low = runway_days is not None and runway_days < RUNWAY_WARN_DAYS
    if not (share_low or runway_low):
        return []

    total = sig.total_inbound_sat + sig.total_outbound_sat
    deficit = int(total * INBOUND_TARGET_SHARE) - sig.total_inbound_sat
    severity = (
        Severity.CRITICAL
        if (sig.inbound_ratio < INBOUND_CRITICAL_SHARE
            or (runway_days is not None
                and runway_days < RUNWAY_CRITICAL_DAYS))
        else Severity.HIGH
    )

    data = {
        "inbound_sat": sig.total_inbound_sat,
        "outbound_sat": sig.total_outbound_sat,
        "inbound_share": round(sig.inbound_ratio, 4),
        "deficit_to_target_sat": deficit,
    }
    if inbound_trend_sat_per_day is not None:
        data["inbound_trend_sat_per_day"] = round(inbound_trend_sat_per_day)
    if runway_days is not None:
        data["runway_days"] = round(runway_days, 1)
    caveats = []
    options = []

    # Option A: Loop Out (uses existing outbound; no new channel).
    loop = market.loop
    if loop.connected and loop.out_quote:
        amt = loop.out_quote.amount_sat
        if sig.total_outbound_sat < loop.out_min_sat:
            caveats.append(
                f"Loop Out infeasible: outbound {_fmt(sig.total_outbound_sat)}"
                f" is below the server minimum {_fmt(loop.out_min_sat)}"
            )
        else:
            options.append((
                "loop_out",
                loop.out_quote.total_fee_sat,
                amt,
                f"loop out --amt {amt} --conf_target 6",
            ))
            data["loop_out_fee_sat"] = loop.out_quote.total_fee_sat
    elif not loop.connected:
        caveats.append("loopd not reachable — Loop Out not priced")

    # Option B: Pool bid (a new leased channel with an enforced term).
    pool = market.pool
    if pool.connected:
        rate = pool.last_clearing_rate_ppb.get(DEFAULT_DURATION_BLOCKS)
        if rate:
            amt = econ.round_up_to_unit(max(deficit, 1))
            cost = econ.pool_bid_cost(
                amt, rate, DEFAULT_DURATION_BLOCKS,
                pool.exec_fee_base_sat, pool.exec_fee_rate_ppm,
                pool.next_batch_feerate_sat_kw,
            )
            # pool CLI's --interest_rate_percent is the TOTAL percent over
            # the lease term (note 05: 1.5% over 2016 blocks ↔ 7,440 ppb):
            # pct = rate_ppb × duration / 1e9 × 100
            rate_pct = rate * DEFAULT_DURATION_BLOCKS / 1e9 * 100
            cmd = (
                f"pool orders submit bid --amt {amt} "
                f"--interest_rate_percent {rate_pct:.4f} "
                f"--lease_duration_blocks {DEFAULT_DURATION_BLOCKS} "
                f"--max_batch_fee_rate "
                f"{econ.sat_per_kw_to_sat_per_vb(pool.next_batch_feerate_sat_kw):.0f}"
            )
            options.append(("pool_bid", cost.total_sat, amt, cmd))
            data.update({
                "pool_rate_ppb": rate,
                "pool_rate_apr_pct": round(econ.ppb_to_apr_pct(rate), 2),
                "pool_bid_amount_sat": amt,
                "pool_premium_sat": cost.premium_sat,
                "pool_exec_fee_sat": cost.exec_fee_sat,
                "pool_chain_fee_sat": cost.chain_fee_sat,
                "pool_total_sat": cost.total_sat,
            })
            if pool.account_available_sat < cost.total_sat:
                caveats.append(
                    "Pool account balance "
                    f"({_fmt(pool.account_available_sat)}) can't cover this "
                    f"bid ({_fmt(cost.total_sat)}) — fund or open an account "
                    "first (pool accounts new)"
                )
            caveats.append(
                "Pool fills depend on the auction crossing — resting depth "
                "is not a fill guarantee (heuristic: depth ≠ liquidity)"
            )
        else:
            caveats.append(
                "no recent Pool clearing rate for the 2016-block market — "
                "bid not priced"
            )
    else:
        caveats.append("poold not reachable — Pool bid not priced")

    runway_note = (
        f" At the current drain rate you have ≈{runway_days:.1f} days of "
        "receive headroom left."
        if runway_low else ""
    )

    if not options:
        summary = (
            f"Inbound liquidity is {sig.inbound_ratio:.0%} of capacity "
            f"({_fmt(sig.total_inbound_sat)}) — the node can barely receive, "
            "but neither acquisition option could be priced right now."
            + runway_note
        )
        return [Recommendation(
            rule="R1", title="Acquire inbound liquidity",
            severity=severity, summary=summary, data=data, caveats=caveats,
        )]

    options.sort(key=lambda o: o[1])
    kind, cost_sat, amt, cmd = options[0]
    via = "Loop Out" if kind == "loop_out" else "a Pool bid (2016-block lease)"
    lead = (
        f"Inbound is only {sig.inbound_ratio:.0%} of capacity "
        f"({_fmt(sig.total_inbound_sat)} to receive with)."
        if share_low else
        f"Inbound looks OK today ({sig.inbound_ratio:.0%} of capacity) but "
        "is draining."
    )
    summary = (
        f"{lead}{runway_note} Cheapest priced route to more inbound: {via} — "
        f"{_fmt(amt)} of inbound for ≈{_fmt(cost_sat)} ({cost_sat / amt:.2%})."
    )
    return [Recommendation(
        rule="R1", title="Acquire inbound liquidity", severity=severity,
        summary=summary, data=data, est_cost_sat=cost_sat,
        est_benefit=f"+{_fmt(amt)} receive headroom",
        command=cmd, caveats=caveats,
    )]


# ----------------------------------------------------------------- R2 ----

def r2_acquire_outbound(
    snap: NodeSnapshot, sig: NodeSignals, market: MarketSnapshot
) -> list:
    """Outbound depleted → Loop In vs. opening a channel."""
    out_share = 1 - sig.inbound_ratio
    if sig.channels_total == 0 or out_share >= OUTBOUND_LOW_SHARE:
        return []

    data = {"outbound_sat": sig.total_outbound_sat,
            "outbound_share": round(out_share, 4)}
    caveats = []
    options = []

    loop = market.loop
    if loop.connected and loop.in_quote:
        amt = loop.in_quote.amount_sat
        if snap.balances.onchain_confirmed < amt:
            caveats.append(
                f"Loop In needs {_fmt(amt)} on-chain; wallet has "
                f"{_fmt(snap.balances.onchain_confirmed)}"
            )
        else:
            options.append((
                "loop_in", loop.in_quote.total_fee_sat, amt,
                f"loop in --amt {amt}",
            ))
            data["loop_in_fee_sat"] = loop.in_quote.total_fee_sat

    if market.fees.available:
        open_rate = market.fees.at_target(6) or 0
        open_fee = econ.chain_tx_fee_sat(econ.CHANNEL_OPEN_VBYTES, open_rate)
        data["channel_open_fee_sat"] = open_fee
        caveats.append(
            f"alternative: open a new channel (≈{_fmt(open_fee)} chain fee at "
            f"{open_rate:g} sat/vB) — adds capacity but needs a good peer"
        )

    if not options:
        return [Recommendation(
            rule="R2", title="Acquire outbound liquidity",
            severity=Severity.MEDIUM,
            summary=(
                f"Outbound is only {out_share:.0%} of capacity — the node "
                "can barely send or route out; no priced option available."
            ),
            data=data, caveats=caveats,
        )]

    kind, cost_sat, amt, cmd = options[0]
    return [Recommendation(
        rule="R2", title="Acquire outbound liquidity",
        severity=Severity.MEDIUM,
        summary=(
            f"Outbound is only {out_share:.0%} of capacity. Loop In converts "
            f"{_fmt(amt)} of on-chain funds into channel balance for "
            f"≈{_fmt(cost_sat)} ({cost_sat / amt:.2%}) — the cheap swap "
            "direction, since the client publishes the HTLC."
        ),
        data=data, est_cost_sat=cost_sat,
        est_benefit=f"+{_fmt(amt)} send headroom",
        command=cmd, caveats=caveats,
    )]


# ----------------------------------------------------------------- R3 ----

def r3_close_underperformer(
    snap: NodeSnapshot, sig: NodeSignals, market: MarketSnapshot
) -> list:
    """IQR-lower-outlier channels → candidate closes (never the only channel)."""
    if sig.channels_considered < MIN_CHANNELS_FOR_OUTLIERS:
        return []

    recs = []
    for s in sig.channels:
        if not s.considered or sig.channels_total < 2:
            continue
        if not (s.revenue_outlier_low or s.volume_outlier_low):
            continue
        close_rate = market.fees.at_target(6) or 0
        close_fee = econ.chain_tx_fee_sat(econ.CHANNEL_CLOSE_VBYTES, close_rate)
        funding_txid, _, idx = s.chan_point.partition(":")
        metrics = []
        if s.revenue_outlier_low:
            metrics.append("revenue")
        if s.volume_outlier_low:
            metrics.append("volume")
        recs.append(Recommendation(
            rule="R3", title="Close underperforming channel",
            severity=Severity.MEDIUM,
            summary=(
                f"Channel to {s.peer_pubkey[:16]}… is a statistical "
                f"underperformer ({'/'.join(metrics)} far below your other "
                f"channels). Closing frees {_fmt(s.capacity_sat)} of capital "
                f"for ≈{_fmt(close_fee)} in chain fees."
            ),
            data={
                "chan_point": s.chan_point,
                "fees_earned_msat": s.fees_earned_msat,
                "volume_sat": s.volume_sat,
                "revenue_per_capacity_day": s.revenue_per_capacity_day,
                "close_fee_sat": close_fee,
            },
            est_cost_sat=close_fee,
            est_benefit=f"frees {_fmt(s.capacity_sat)} of committed capital",
            command=f"lncli closechannel {funding_txid} {idx or 0}",
            caveats=[
                f"based on {sig.forwarding_lookback_days}d of history; "
                "verify the peer isn't strategically important"
            ],
        ))
    return recs


# ----------------------------------------------------------------- R4 ----

def r4_rebalance(
    snap: NodeSnapshot, sig: NodeSignals, market: MarketSnapshot
) -> list:
    """One-sided channel with a good, well-uptime peer → rebalance it."""
    recs = []
    outliers = {
        s.chan_point for s in sig.channels
        if s.revenue_outlier_low or s.volume_outlier_low
    }
    for s in sig.channels:
        if not s.one_sided or not s.active or s.chan_point in outliers:
            continue
        if s.uptime_ratio < GOOD_UPTIME or not s.considered:
            continue
        shift = abs(int(s.local_ratio * s.capacity_sat) - s.capacity_sat // 2)
        max_fee = shift * REBALANCE_FEE_PPM_MAX // 10**6
        side = "outbound-only" if s.local_ratio > 0.5 else "inbound-only"
        recs.append(Recommendation(
            rule="R4", title="Rebalance channel",
            severity=Severity.LOW,
            summary=(
                f"Channel to {s.peer_pubkey[:16]}… is {side} "
                f"(local {s.local_ratio:.0%}) but the peer is reliable "
                f"({s.uptime_ratio:.0%} uptime) — shift ≈{_fmt(shift)} to "
                f"restore two-way routing (≤{_fmt(max_fee)} at "
                f"{REBALANCE_FEE_PPM_MAX} ppm)."
            ),
            data={"chan_point": s.chan_point,
                  "local_ratio": round(s.local_ratio, 4),
                  "shift_sat": shift, "max_fee_sat": max_fee},
            est_cost_sat=max_fee,
            est_benefit="restores bidirectional routing on a good peer",
            command="# circular rebalance via your tool of choice, e.g. "
                    f"rebalance-lnd --amount {shift}",
            caveats=["only worth it if two-way routing on this peer earns "
                     "more than the rebalance fee"],
        ))
    return recs


# ----------------------------------------------------------------- R5 ----

def r5_retune_fees(
    snap: NodeSnapshot, sig: NodeSignals, market: MarketSnapshot
) -> list:
    """Active, mature channel that never forwards → try cheaper fees first."""
    recs = []
    for s in sig.channels:
        if not s.considered or not s.active or s.private:
            continue
        if s.forwards_in + s.forwards_out > 0 or sig.channels_total < 2:
            continue
        funding_txid, _, idx = s.chan_point.partition(":")
        recs.append(Recommendation(
            rule="R5", title="Retune routing fees",
            severity=Severity.LOW,
            summary=(
                f"Channel to {s.peer_pubkey[:16]}… forwarded nothing in "
                f"{sig.forwarding_lookback_days}d despite being active. "
                "Before closing or rebalancing, try lowering its fee rate — "
                "free and reversible."
            ),
            data={"chan_point": s.chan_point,
                  "forwards": 0,
                  "lookback_days": sig.forwarding_lookback_days},
            est_cost_sat=0,
            est_benefit="may start earning routing fees at no cost",
            command=(
                "lncli updatechanpolicy --base_fee_msat 1000 "
                f"--fee_rate_ppm 100 --time_lock_delta 80 "
                f"--chan_point {s.chan_point}"
            ),
            caveats=["cheap-and-reversible first (heuristic #2)"],
        ))
    return recs


# ----------------------------------------------------------------- R6 ----

FEE_BASELINE_RATIO = 2.0  # fast vs. recorded 7-day norm considered "hot"


def r6_defer_onchain(
    snap: NodeSnapshot, sig: NodeSignals, market: MarketSnapshot,
    chain_touching: int = 0,
    baseline_sat_vb: Optional[float] = None,
) -> list:
    """Mempool hot → recommend waiting; compute the savings.

    Two triggers: the static intra-day spread (fast ≥ 3× economy), and —
    when the ingestion history provides one — today's fast rate vs. the
    node's own recorded baseline (fast ≥ 2× the 7-day median).
    """
    fees = market.fees
    if not fees.available:
        return []
    fast = fees.at_target(6)
    economy = fees.at_target(144)
    if not fast or not economy:
        return []

    spread_hot = fast >= FEE_ELEVATED_RATIO * economy
    baseline_hot = (
        baseline_sat_vb is not None and fast >= FEE_BASELINE_RATIO * baseline_sat_vb
    )
    if not (spread_hot or baseline_hot):
        return []

    reference = economy if spread_hot else float(baseline_sat_vb)
    against = ("economy rate" if spread_hot
               else f"your 7-day norm of {baseline_sat_vb:g} sat/vB")
    savings = econ.fee_savings_sat(
        econ.BATCH_PARTICIPANT_VBYTES, fast, reference
    )
    return [Recommendation(
        rule="R6", title="Defer on-chain actions",
        severity=Severity.HIGH if chain_touching else Severity.INFO,
        summary=(
            f"Chain fees are elevated ({fast:g} sat/vB for ~1h confirm vs "
            f"{against}). Waiting saves ≈{_fmt(savings)} per typical "
            "on-chain action; defer non-urgent opens/closes/swaps."
        ),
        data={"sat_per_vb_6": fast, "sat_per_vb_144": economy,
              "baseline_sat_per_vb": baseline_sat_vb,
              "savings_per_350vb_sat": savings,
              "affected_recommendations": chain_touching},
        est_benefit=f"≈{_fmt(savings)} saved per deferred chain action",
        caveats=["urgency can override — an imminent receive failure is "
                 "worth paying up for"],
    )]


# ----------------------------------------------------------------- R7 ----

def r7_consolidate_orders(
    snap: NodeSnapshot, sig: NodeSignals, market: MarketSnapshot
) -> list:
    """Multiple small resting Pool orders → one larger order amortizes the
    fixed chain footprint (breakeven ∝ 1/(amount × duration))."""
    pool = market.pool
    if not pool.connected:
        return []
    by_key: dict = {}
    for o in pool.own_orders:
        by_key.setdefault((o.side, o.duration_blocks), []).append(o)

    recs = []
    for (side, duration), orders in by_key.items():
        small = [o for o in orders if o.amt_sat <= 2 * econ.POOL_UNIT_SAT]
        if len(small) < 2:
            continue
        total = sum(o.amt_sat for o in small)
        chain_now = len(small) * econ.BATCH_PARTICIPANT_VBYTES
        chain_merged = econ.BATCH_PARTICIPANT_VBYTES
        rate_vb = econ.sat_per_kw_to_sat_per_vb(pool.next_batch_feerate_sat_kw)
        saved = econ.chain_tx_fee_sat(chain_now - chain_merged, rate_vb)
        recs.append(Recommendation(
            rule="R7", title="Consolidate small Pool orders",
            severity=Severity.LOW,
            summary=(
                f"{len(small)} small {side}s resting in the {duration}-block "
                f"market. One {_fmt(total)} order has the same exposure but "
                f"1/{len(small)} of the fixed chain footprint — "
                f"≈{_fmt(saved)} less in batch chain fees."
            ),
            data={"orders": len(small), "total_sat": total,
                  "duration_blocks": duration, "chain_fee_saved_sat": saved},
            est_benefit=f"≈{_fmt(saved)} chain fees saved per batch",
            command=f"# pool orders cancel <ids…> && pool orders submit "
                    f"{side} --amt {total} --lease_duration_blocks {duration}",
        ))
    return recs


RULES: list = [
    r2_acquire_outbound,
    r3_close_underperformer,
    r4_rebalance,
    r5_retune_fees,
    r7_consolidate_orders,
    # r1 and r6 are invoked explicitly by the engine: r1 receives the
    # history-derived inbound trend, r6 the fee baseline + chain-touching
    # count.
]

CHAIN_TOUCHING_RULES = {"R1", "R2", "R3", "R7"}
