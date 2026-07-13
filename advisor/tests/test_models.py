"""Deterministic tests for the snapshot model's computed fields (SPEC NFR4).

Run with: python -m pytest  (or python tests/test_models.py)
"""

from advisor.models import Balances, ChannelState, NodeIdentity, NodeSnapshot


def _channel(local: int, remote: int, active: bool = True, **kw) -> ChannelState:
    cap = local + remote
    return ChannelState(
        chan_point=kw.get("chan_point", "aa:0"),
        peer_pubkey=kw.get("peer_pubkey", "03abc"),
        capacity_sat=cap,
        local_sat=local,
        remote_sat=remote,
        active=active,
        private=kw.get("private", False),
        uptime_s=kw.get("uptime_s", 90),
        lifetime_s=kw.get("lifetime_s", 100),
        total_sent_sat=0,
        total_received_sat=0,
    )


def _snapshot(channels) -> NodeSnapshot:
    return NodeSnapshot(
        identity=NodeIdentity(
            alias="t", pubkey="02", version="v", block_height=1,
            synced_to_chain=True, num_active_channels=len(channels), num_peers=1,
        ),
        balances=Balances(
            onchain_confirmed=0, onchain_unconfirmed=0, ln_local=0, ln_remote=0
        ),
        channels=channels,
    )


def test_local_and_uptime_ratio():
    c = _channel(local=90_000, remote=10_000, uptime_s=90, lifetime_s=100)
    assert c.local_ratio == 0.9
    assert c.uptime_ratio == 0.9


def test_zero_capacity_is_safe():
    c = _channel(local=0, remote=0)
    assert c.capacity_sat == 0
    assert c.local_ratio == 0.0  # no ZeroDivisionError


def test_inbound_outbound_totals_active_only():
    snap = _snapshot([
        _channel(local=90_000, remote=10_000),
        _channel(local=20_000, remote=80_000),
        _channel(local=50_000, remote=50_000, active=False),  # excluded
    ])
    assert snap.total_outbound_sat == 110_000
    assert snap.total_inbound_sat == 90_000
    assert snap.num_channels == 3  # count includes inactive


def test_json_roundtrip():
    snap = _snapshot([_channel(local=1, remote=1)])
    restored = NodeSnapshot.model_validate_json(snap.model_dump_json())
    assert restored.total_outbound_sat == snap.total_outbound_sat


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all model tests passed")
