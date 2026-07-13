"""Typed data model for a node snapshot (SPEC §6).

These are the deterministic, normalized inputs that later milestones' signal
and recommendation engines consume. Kept intentionally close to what lnd's
``ListChannels`` / ``GetInfo`` / ``*Balance`` RPCs return, in satoshis.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, computed_field


class NodeIdentity(BaseModel):
    """Node-level facts from ``GetInfo``."""

    alias: str
    pubkey: str
    version: str
    block_height: int
    synced_to_chain: bool
    num_active_channels: int
    num_peers: int


class ChannelState(BaseModel):
    """One channel's liquidity state from ``ListChannels``.

    ``local_sat`` is outbound liquidity (what we can send); ``remote_sat`` is
    inbound liquidity (what we can receive) — the seesaw from note 03.
    """

    chan_point: str
    chan_id: int  # short channel id; joins to forwarding events
    peer_pubkey: str
    capacity_sat: int
    local_sat: int
    remote_sat: int
    active: bool
    private: bool
    uptime_s: int
    lifetime_s: int
    total_sent_sat: int
    total_received_sat: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def local_ratio(self) -> float:
        """Fraction of capacity on our side (outbound). 0.5 is balanced."""
        return self.local_sat / self.capacity_sat if self.capacity_sat else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def uptime_ratio(self) -> float:
        """Fraction of monitored lifetime the channel's peer was online."""
        return self.uptime_s / self.lifetime_s if self.lifetime_s else 0.0


class ForwardingStats(BaseModel):
    """Aggregated forwarding activity for one channel over the lookback window.

    A forward touches two channels (in and out); fees are earned on the
    outgoing side, so ``fee_msat`` is attributed to the outgoing channel.
    """

    chan_id: int
    events_in: int = 0
    events_out: int = 0
    amt_in_sat: int = 0
    amt_out_sat: int = 0
    fee_msat: int = 0


class Balances(BaseModel):
    """On-chain and aggregate off-chain balances, in satoshis."""

    onchain_confirmed: int
    onchain_unconfirmed: int
    ln_local: int
    ln_remote: int


class FeeEnvironment(BaseModel):
    """On-chain fee estimates (sat/vB) at common confirmation targets.

    Keys are conf targets in blocks: 1 (fastest), 3 (~30 min), 6 (~1 h),
    144 (~1 day / economy).
    """

    available: bool = False
    sat_per_vb: dict[int, float] = {}
    source: str = ""

    def at_target(self, target: int) -> Optional[float]:
        """Best estimate at or above the given conf target."""
        candidates = [t for t in self.sat_per_vb if t >= target]
        if candidates:
            return self.sat_per_vb[min(candidates)]
        return self.sat_per_vb.get(max(self.sat_per_vb)) if self.sat_per_vb else None


class PoolDepth(BaseModel):
    """Open interest for one lease-duration market (both tiers summed)."""

    asks: int = 0
    bids: int = 0
    ask_units: int = 0
    bid_units: int = 0


class PoolOrder(BaseModel):
    """One of our own resting orders on the Pool book."""

    side: str                 # "bid" | "ask"
    amt_sat: int
    rate_ppb: int
    duration_blocks: int
    state: str
    units: int = 0


class PoolMarket(BaseModel):
    """Live Pool auction state via poold (SPEC FR3)."""

    connected: bool = False
    exec_fee_base_sat: int = 0
    exec_fee_rate_ppm: int = 0
    lease_durations: dict[int, str] = {}      # blocks → market state
    next_batch_feerate_sat_kw: int = 0
    next_batch_clear_unix: int = 0
    depth: dict[int, PoolDepth] = {}          # blocks → open interest
    last_clearing_rate_ppb: dict[int, int] = {}  # blocks → most recent rate
    account_available_sat: int = 0            # sum over OPEN accounts
    own_orders: list[PoolOrder] = []          # our active resting orders


class LoopQuote(BaseModel):
    """A swap quote for the reference amount, in satoshis."""

    amount_sat: int
    swap_fee_sat: int = 0
    miner_fee_sat: int = 0
    prepay_sat: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_fee_sat(self) -> int:
        return self.swap_fee_sat + self.miner_fee_sat + self.prepay_sat


class LoopMarket(BaseModel):
    """Live Loop server terms + quotes via loopd (SPEC FR3)."""

    connected: bool = False
    out_min_sat: int = 0
    out_max_sat: int = 0
    in_min_sat: int = 0
    in_max_sat: int = 0
    out_quote: Optional[LoopQuote] = None
    in_quote: Optional[LoopQuote] = None


class MarketSnapshot(BaseModel):
    """Everything the Advisor knows about the outside market right now."""

    fees: FeeEnvironment = FeeEnvironment()
    pool: PoolMarket = PoolMarket()
    loop: LoopMarket = LoopMarket()


class NodeSnapshot(BaseModel):
    """The normalized read-only view of the node at a moment in time."""

    identity: NodeIdentity
    balances: Balances
    channels: list[ChannelState]
    # chan_id → forwarding stats over the lookback window (default 30 days).
    forwarding: dict[int, ForwardingStats] = {}
    forwarding_lookback_days: int = 30

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_inbound_sat(self) -> int:
        """Total inbound liquidity across active channels (receive headroom)."""
        return sum(c.remote_sat for c in self.channels if c.active)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_outbound_sat(self) -> int:
        """Total outbound liquidity across active channels (send headroom)."""
        return sum(c.local_sat for c in self.channels if c.active)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def num_channels(self) -> int:
        return len(self.channels)
