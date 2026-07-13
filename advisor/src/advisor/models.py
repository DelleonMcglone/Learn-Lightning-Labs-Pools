"""Typed data model for a node snapshot (SPEC §6).

These are the deterministic, normalized inputs that later milestones' signal
and recommendation engines consume. Kept intentionally close to what lnd's
``ListChannels`` / ``GetInfo`` / ``*Balance`` RPCs return, in satoshis.
"""

from __future__ import annotations

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


class Balances(BaseModel):
    """On-chain and aggregate off-chain balances, in satoshis."""

    onchain_confirmed: int
    onchain_unconfirmed: int
    ln_local: int
    ln_remote: int


class NodeSnapshot(BaseModel):
    """The normalized read-only view of the node at a moment in time."""

    identity: NodeIdentity
    balances: Balances
    channels: list[ChannelState]

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
