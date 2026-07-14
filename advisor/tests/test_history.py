"""Tests for the ingestion pipeline (history store + baselines) and the
baseline-aware R6."""

import tempfile
from pathlib import Path

from advisor.history import HistoryStore, build_record
from advisor.models import (
    Balances, ChannelState, FeeEnvironment, LoopMarket, LoopQuote,
    MarketSnapshot, NodeIdentity, NodeSnapshot, PoolMarket,
)
from advisor.recommend import recommend
from advisor.signals import compute_signals

DAY = 86_400
T0 = 1_800_000_000  # fixed epoch for deterministic tests


def _snap():
    return NodeSnapshot(
        identity=NodeIdentity(
            alias="t", pubkey="02aa", version="v", block_height=100,
            synced_to_chain=True, num_active_channels=1, num_peers=1,
        ),
        balances=Balances(onchain_confirmed=111_674, onchain_unconfirmed=0,
                          ln_local=20_515, ln_remote=1_014),
        channels=[ChannelState(
            chan_point="ff:0", chan_id=1, peer_pubkey="03bb",
            capacity_sat=25_000, local_sat=20_515, remote_sat=1_014,
            active=True, private=False, uptime_s=30 * DAY,
            lifetime_s=30 * DAY, total_sent_sat=0, total_received_sat=0,
        )],
    )


def _market(fee6=1.0, fee144=1.0):
    return MarketSnapshot(
        fees=FeeEnvironment(available=True,
                            sat_per_vb={1: fee6 * 2, 3: fee6, 6: fee6,
                                        144: fee144}),
        pool=PoolMarket(connected=True,
                        last_clearing_rate_ppb={2016: 6_613},
                        next_batch_feerate_sat_kw=6_250,
                        exec_fee_base_sat=1, exec_fee_rate_ppm=1_000),
        loop=LoopMarket(
            connected=True, out_min_sat=250_000, out_max_sat=120_000_000,
            in_min_sat=250_000, in_max_sat=120_000_000,
            out_quote=LoopQuote(amount_sat=500_000, swap_fee_sat=552,
                                miner_fee_sat=7_280, prepay_sat=1_330),
            in_quote=LoopQuote(amount_sat=500_000, swap_fee_sat=417,
                               miner_fee_sat=550),
        ),
    )


def _store() -> HistoryStore:
    tmp = tempfile.mkdtemp()
    return HistoryStore(Path(tmp) / "history.jsonl")


# ------------------------------------------------------------- pipeline --

def test_build_record_shape_and_privacy():
    rec = build_record(_snap(), _market(), ts=T0)
    assert rec["ts"] == T0
    assert rec["inbound_sat"] == 1_014
    assert rec["outbound_sat"] == 20_515
    assert rec["fees_sat_per_vb"]["6"] == 1.0
    assert rec["pool_clearing_ppb"]["2016"] == 6_613
    assert rec["loop_in_fee_sat"] == 967
    blob = str(rec)
    assert "03bb" not in blob and "ff:0" not in blob  # no identifiers stored


def test_append_and_windowed_read():
    store = _store()
    for i in range(5):
        store.append(build_record(_snap(), _market(), ts=T0 + i * DAY))
    assert store.count() == 5
    recent = list(store.records(since_ts=T0 + 3 * DAY))
    assert len(recent) == 2


def test_store_tolerates_torn_write():
    store = _store()
    store.append(build_record(_snap(), _market(), ts=T0))
    with store.path.open("a") as f:
        f.write('{"broken json…\n')
    store.append(build_record(_snap(), _market(), ts=T0 + 1))
    assert store.count() == 2


# ------------------------------------------------------------ baselines --

def test_fee_baseline_median_and_minimum_count():
    store = _store()
    now = T0 + 10 * DAY
    # two records → no baseline yet
    for i, fee in enumerate([2.0, 4.0]):
        store.append(build_record(_snap(), _market(fee6=fee),
                                  ts=now - (i + 1) * DAY))
    assert store.fee_baseline_sat_vb(now=now) is None
    # third record → median of {2,4,6} = 4
    store.append(build_record(_snap(), _market(fee6=6.0), ts=now - 3 * DAY))
    assert store.fee_baseline_sat_vb(now=now) == 4.0
    # old records outside the window are ignored
    store.append(build_record(_snap(), _market(fee6=100.0),
                              ts=now - 30 * DAY))
    assert store.fee_baseline_sat_vb(now=now) == 4.0


# -------------------------------------------------- baseline-aware R6 ----

def test_r6_fires_on_baseline_even_when_spread_flat():
    # Flat intra-day spread (6 vs 144 identical) but 5× the recorded norm.
    snap = _snap()
    market = _market(fee6=10.0, fee144=10.0)   # spread ratio 1 → not hot
    sig = compute_signals(snap)
    quiet = recommend(snap, sig, market)
    assert not [r for r in quiet.recommendations if r.rule == "R6"]

    hot = recommend(snap, sig, market, fee_baseline_sat_vb=2.0)
    r6 = [r for r in hot.recommendations if r.rule == "R6"]
    assert len(r6) == 1
    assert "7-day norm" in r6[0].summary
    assert r6[0].data["baseline_sat_per_vb"] == 2.0
    # savings vs the baseline: 350 vB × (10 − 2) = 2,800 sats
    assert r6[0].data["savings_per_350vb_sat"] == 2_800


def test_inbound_trend_endpoint_slope():
    store = _store()
    now = T0 + 10 * DAY
    # 300k → 100k over 2 days = −100k/day
    base = build_record(_snap(), _market(), ts=now - 2 * DAY)
    base["inbound_sat"] = 300_000
    store.append(base)
    mid = build_record(_snap(), _market(), ts=now - DAY)
    mid["inbound_sat"] = 200_000
    store.append(mid)
    last = build_record(_snap(), _market(), ts=now)
    last["inbound_sat"] = 100_000
    store.append(last)
    assert store.inbound_trend_sat_per_day(now=now) == -100_000


def test_inbound_trend_needs_observations_and_span():
    store = _store()
    now = T0
    store.append({**build_record(_snap(), _market(), ts=now - 60)})
    store.append({**build_record(_snap(), _market(), ts=now - 30)})
    assert store.inbound_trend_sat_per_day(now=now) is None  # only 2
    store.append({**build_record(_snap(), _market(), ts=now)})
    # 3 records but all within 60s → span too short
    assert store.inbound_trend_sat_per_day(now=now) is None


def _healthy_snapshot():
    """40% inbound — the static share trigger must NOT fire."""
    s = _snap()
    s.channels[0].local_sat = 600_000
    s.channels[0].remote_sat = 400_000
    s.channels[0].capacity_sat = 1_000_000
    return s


def test_r1_runway_fires_on_drain_despite_healthy_share():
    snap = _healthy_snapshot()
    sig = compute_signals(snap)
    market = _market()

    quiet = recommend(snap, sig, market)  # no trend → no trigger
    assert not [r for r in quiet.recommendations if r.rule == "R1"]

    # draining 100k/day with 400k inbound → 4 days of runway
    hot = recommend(snap, sig, market, inbound_trend_sat_per_day=-100_000)
    r1 = [r for r in hot.recommendations if r.rule == "R1"]
    assert len(r1) == 1
    from advisor.recommend import Severity
    assert r1[0].severity == Severity.HIGH
    assert r1[0].data["runway_days"] == 4.0
    assert "days of receive headroom" in r1[0].summary


def test_r1_runway_critical_under_3_days():
    snap = _healthy_snapshot()
    sig = compute_signals(snap)
    hot = recommend(snap, sig, _market(),
                    inbound_trend_sat_per_day=-200_000)  # 2 days
    r1 = [r for r in hot.recommendations if r.rule == "R1"][0]
    from advisor.recommend import Severity
    assert r1.severity == Severity.CRITICAL
    assert r1.data["runway_days"] == 2.0


def test_r1_growing_inbound_stays_quiet():
    snap = _healthy_snapshot()
    sig = compute_signals(snap)
    report = recommend(snap, sig, _market(),
                       inbound_trend_sat_per_day=+50_000)
    assert not [r for r in report.recommendations if r.rule == "R1"]


def test_r6_baseline_quiet_when_fees_normal():
    snap = _snap()
    market = _market(fee6=2.0, fee144=1.5)
    sig = compute_signals(snap)
    report = recommend(snap, sig, market, fee_baseline_sat_vb=2.0)
    assert not [r for r in report.recommendations if r.rule == "R6"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all history tests passed")
