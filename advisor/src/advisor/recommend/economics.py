"""All recommendation arithmetic lives here (SPEC NFR4).

Pure, unit-tested functions. Formulas come from the study notes:
- Pool premium/breakeven: 04-pool-auctions-lease-pricing.md
- chain footprint ≈ 350 vB per batch participant: note 04 §7
- sat/kw → sat/vB: rate_kw / 250
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

BLOCKS_PER_YEAR = 52_560
POOL_UNIT_SAT = 100_000
POOL_MIN_ACCOUNT_SAT = 100_000
BATCH_PARTICIPANT_VBYTES = 350
CHANNEL_OPEN_VBYTES = 200
CHANNEL_CLOSE_VBYTES = 110


def ppb_to_apr_pct(rate_ppb: int) -> float:
    """1,000 ppb/block ≈ 5.26% APR."""
    return rate_ppb * BLOCKS_PER_YEAR / 1e7


def sat_per_kw_to_sat_per_vb(rate_kw: int) -> float:
    return rate_kw / 250


def round_up_to_unit(amount_sat: int) -> int:
    """Pool orders are quantized in 100k-sat units (min one unit)."""
    units = max(1, -(-amount_sat // POOL_UNIT_SAT))
    return units * POOL_UNIT_SAT


@dataclass(frozen=True)
class PoolBidCost:
    amount_sat: int
    rate_ppb: int
    duration_blocks: int
    premium_sat: int
    exec_fee_sat: int
    chain_fee_sat: int

    @property
    def total_sat(self) -> int:
        return self.premium_sat + self.exec_fee_sat + self.chain_fee_sat

    @property
    def effective_pct(self) -> float:
        return self.total_sat / self.amount_sat * 100 if self.amount_sat else 0.0


def pool_bid_cost(
    amount_sat: int,
    rate_ppb: int,
    duration_blocks: int,
    exec_base_sat: int,
    exec_rate_ppm: int,
    batch_feerate_sat_kw: int,
) -> PoolBidCost:
    """Full cost of buying `amount_sat` of inbound via a Pool bid.

    premium = amount × rate_ppb × duration / 1e9
    exec    = base + amount × ppm / 1e6
    chain   = 350 vB × batch feerate
    """
    premium = amount_sat * rate_ppb * duration_blocks // 10**9
    exec_fee = exec_base_sat + amount_sat * exec_rate_ppm // 10**6
    chain_fee = int(
        BATCH_PARTICIPANT_VBYTES * sat_per_kw_to_sat_per_vb(batch_feerate_sat_kw)
    )
    return PoolBidCost(
        amount_sat=amount_sat,
        rate_ppb=rate_ppb,
        duration_blocks=duration_blocks,
        premium_sat=premium,
        exec_fee_sat=exec_fee,
        chain_fee_sat=chain_fee,
    )


def pool_breakeven_ppb(
    chain_cost_sat: int, amount_sat: int, duration_blocks: int
) -> Optional[int]:
    """The rate at which a lease merely recovers its fixed chain cost."""
    if not amount_sat or not duration_blocks:
        return None
    return int(chain_cost_sat / (amount_sat * duration_blocks) * 1e9)


def chain_tx_fee_sat(vbytes: int, sat_per_vb: float) -> int:
    return int(vbytes * sat_per_vb)


def fee_savings_sat(
    vbytes: int, now_sat_per_vb: float, later_sat_per_vb: float
) -> int:
    """Sats saved by deferring a chain tx from `now` to `later` rates."""
    return max(0, int(vbytes * (now_sat_per_vb - later_sat_per_vb)))
