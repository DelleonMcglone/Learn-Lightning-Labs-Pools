"""Build a NodeSnapshot from lnd's read RPCs (M0 collector, SPEC FR2)."""

from __future__ import annotations

from ..lndclient import LndClient
from ..models import Balances, ChannelState, NodeIdentity, NodeSnapshot


def collect_snapshot(client: LndClient) -> NodeSnapshot:
    """Query lnd and normalize the result into a typed NodeSnapshot."""
    info = client.get_info()
    channels = client.list_channels()
    wallet = client.wallet_balance()
    chan_bal = client.channel_balance()

    identity = NodeIdentity(
        alias=info.alias,
        pubkey=info.identity_pubkey,
        version=info.version,
        block_height=info.block_height,
        synced_to_chain=info.synced_to_chain,
        num_active_channels=info.num_active_channels,
        num_peers=info.num_peers,
    )

    balances = Balances(
        onchain_confirmed=wallet.confirmed_balance,
        onchain_unconfirmed=wallet.unconfirmed_balance,
        ln_local=chan_bal.local_balance.sat,
        ln_remote=chan_bal.remote_balance.sat,
    )

    channel_states = [
        ChannelState(
            chan_point=c.channel_point,
            peer_pubkey=c.remote_pubkey,
            capacity_sat=c.capacity,
            local_sat=c.local_balance,
            remote_sat=c.remote_balance,
            active=c.active,
            private=c.private,
            uptime_s=c.uptime,
            lifetime_s=c.lifetime,
            total_sent_sat=c.total_satoshis_sent,
            total_received_sat=c.total_satoshis_received,
        )
        for c in channels.channels
    ]

    return NodeSnapshot(
        identity=identity, balances=balances, channels=channel_states
    )
