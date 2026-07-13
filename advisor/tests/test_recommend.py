"""Tests for the M3 recommendation engine.

Economics tests pin the exact worked examples from note 04 (Pool auctions &
lease pricing), so the arithmetic is verified against the study notes the
spec is built on (NFR4: all displayed figures code-computed and tested).
"""

from advisor.models import (
    Balances, ChannelState, FeeEnvironment, LoopMarket, LoopQuote,
    MarketSnapshot, NodeIdentity, NodeSnapshot, PoolMarket, PoolOrder,
)
from advisor.recommend import Severity, recommend
from advisor.recommend import economics as econ
from advisor.signals import compute_signals

DAY = 86_400


# -------------------------------------------------------------- economics --

def test_premium_matches_note_04_worked_example():
    # 10M sats at 1,500 ppb for 2016 blocks = 30,240 sats premium.
    cost = econ.pool_bid_cost(
        10_000_000, 1_500, 2_016,
        exec_base_sat=0, exec_rate_ppm=0, batch_feerate_sat_kw=0,
    )
    assert cost.premium_sat == 30_240


def test_apr_conversion_matches_note_04():
    assert round(econ.ppb_to_apr_pct(1_000), 2) == 5.26
    assert round(econ.ppb_to_apr_pct(2_000), 1) == 10.5
    assert round(econ.ppb_to_apr_pct(6_613), 1) == 34.8  # observed clear


def test_breakeven_matches_note_04_worked_example():
    # 10,000 sats chain cost on 5M × 2016 blocks ≈ 992 ppb.
    assert econ.pool_breakeven_ppb(10_000, 5_000_000, 2_016) == 992


def test_exec_fee_and_chain_fee():
    cost = econ.pool_bid_cost(
        100_000, 6_613, 2_016,
        exec_base_sat=1, exec_rate_ppm=1_000, batch_feerate_sat_kw=6_250,
    )
    assert cost.exec_fee_sat == 1 + 100            # 1 base + 0.1%
    assert cost.chain_fee_sat == 8_750             # 350 vB × 25 sat/vB
    assert cost.premium_sat == 1_333               # 100k×6613×2016/1e9
    assert cost.total_sat == 1_333 + 101 + 8_750


def test_r1_command_interest_rate_is_term_total_percent():
    # note 05 calibration: 7,440 ppb over 2016 blocks ↔ 1.5% per interval.
    # At the fixture's 6,613 ppb the flag must read 1.3332 (%), NOT a
    # per-block figure.
    report = _report([_channel("a:0", 1, 20_000, 1_000)], _market())
    r = [x for x in report.recommendations if x.rule == "R1"][0]
    assert "--interest_rate_percent 1.3332" in r.command


def test_round_up_to_unit():
    assert econ.round_up_to_unit(1) == 100_000
    assert econ.round_up_to_unit(100_000) == 100_000
    assert econ.round_up_to_unit(100_001) == 200_000


# ---------------------------------------------------------------- helpers --

def _channel(cp, cid, local, remote, lifetime_s=30 * DAY, **kw):
    return ChannelState(
        chan_point=cp, chan_id=cid, peer_pubkey=kw.get("peer", "03" + cp),
        capacity_sat=local + remote, local_sat=local, remote_sat=remote,
        active=kw.get("active", True), private=kw.get("private", False),
        uptime_s=kw.get("uptime_s", lifetime_s), lifetime_s=lifetime_s,
        total_sent_sat=0, total_received_sat=0,
    )


def _snap(channels, onchain=1_000_000):
    return NodeSnapshot(
        identity=NodeIdentity(
            alias="t", pubkey="02", version="v", block_height=1,
            synced_to_chain=True, num_active_channels=len(channels),
            num_peers=len(channels),
        ),
        balances=Balances(
            onchain_confirmed=onchain, onchain_unconfirmed=0,
            ln_local=0, ln_remote=0,
        ),
        channels=channels,
    )


def _market(pool_connected=True, loop_connected=True, fees=True,
            rate_2016=6_613, own_orders=(), account_sat=200_000):
    return MarketSnapshot(
        fees=FeeEnvironment(
            available=fees,
            sat_per_vb={1: 25.0, 3: 12.0, 6: 6.0, 144: 1.0} if fees else {},
        ),
        pool=PoolMarket(
            connected=pool_connected,
            exec_fee_base_sat=1, exec_fee_rate_ppm=1_000,
            next_batch_feerate_sat_kw=6_250,
            last_clearing_rate_ppb={2016: rate_2016} if rate_2016 else {},
            account_available_sat=account_sat,
            own_orders=list(own_orders),
        ),
        loop=LoopMarket(
            connected=loop_connected,
            out_min_sat=250_000, out_max_sat=120_000_000,
            in_min_sat=250_000, in_max_sat=120_000_000,
            out_quote=LoopQuote(
                amount_sat=500_000, swap_fee_sat=552,
                miner_fee_sat=7_280, prepay_sat=1_330,
            ) if loop_connected else None,
            in_quote=LoopQuote(
                amount_sat=500_000, swap_fee_sat=417, miner_fee_sat=550,
            ) if loop_connected else None,
        ),
    )


def _report(channels, market, **snapkw):
    snap = _snap(channels, **snapkw)
    return recommend(snap, compute_signals(snap), market)


# ------------------------------------------------------------------ rules --

def test_r1_fires_critical_and_prefers_pool_when_loop_infeasible():
    # Mirrors the real testnet node: tiny inbound, outbound below Loop min.
    report = _report([_channel("a:0", 1, 20_000, 1_000)], _market())
    r1 = [r for r in report.recommendations if r.rule == "R1"]
    assert len(r1) == 1
    r = r1[0]
    assert r.severity == Severity.CRITICAL          # <5% inbound
    assert "pool orders submit bid" in r.command    # loop infeasible
    assert any("Loop Out infeasible" in c for c in r.caveats)
    # 100k unit at 6,613 ppb: exact economics from the fixture
    assert r.data["pool_total_sat"] == 1_333 + 101 + 8_750
    assert r.est_cost_sat == r.data["pool_total_sat"]


def test_r1_prefers_cheaper_loop_when_feasible():
    # Plenty of outbound → Loop Out (9,162) beats Pool 500k bid.
    report = _report([_channel("a:0", 1, 900_000, 30_000)], _market())
    r = [x for x in report.recommendations if x.rule == "R1"][0]
    assert "loop out" in r.command
    assert r.est_cost_sat == 552 + 7_280 + 1_330


def test_r1_quiet_when_inbound_healthy():
    report = _report([_channel("a:0", 1, 400_000, 600_000)], _market())
    assert not [r for r in report.recommendations if r.rule == "R1"]


def test_r2_fires_when_outbound_depleted():
    report = _report([_channel("a:0", 1, 10_000, 990_000)], _market())
    r2 = [r for r in report.recommendations if r.rule == "R2"]
    assert len(r2) == 1
    assert "loop in" in r2[0].command
    assert r2[0].est_cost_sat == 967


def test_r3_requires_enough_channels():
    # 4 comparable channels → R3 must not fire; skip note recorded.
    chans = [_channel(f"c{i}:0", i, 500_000, 500_000) for i in range(1, 5)]
    report = _report(chans, _market())
    assert not [r for r in report.recommendations if r.rule == "R3"]
    assert "R3" in report.skipped_rules


def test_r5_suggests_fee_retune_for_dead_channel():
    chans = [
        _channel("c1:0", 1, 500_000, 500_000),
        _channel("c2:0", 2, 500_000, 500_000),
    ]
    report = _report(chans, _market())
    r5 = [r for r in report.recommendations if r.rule == "R5"]
    assert len(r5) == 2                       # neither forwarded anything
    assert r5[0].est_cost_sat == 0


def test_r6_fires_only_when_fees_elevated():
    calm = _report([_channel("a:0", 1, 500_000, 500_000)],
                   _market())  # 6 sat/vB vs 1 economy = 6x → elevated
    assert [r for r in calm.recommendations if r.rule == "R6"]

    flat = _market()
    flat.fees.sat_per_vb = {1: 1.0, 3: 1.0, 6: 1.0, 144: 1.0}
    quiet = _report([_channel("a:0", 1, 500_000, 500_000)], flat)
    assert not [r for r in quiet.recommendations if r.rule == "R6"]


def test_r7_consolidates_small_orders():
    orders = [
        PoolOrder(side="bid", amt_sat=100_000, rate_ppb=6_613,
                  duration_blocks=2016, state="ORDER_SUBMITTED", units=1),
        PoolOrder(side="bid", amt_sat=100_000, rate_ppb=6_500,
                  duration_blocks=2016, state="ORDER_SUBMITTED", units=1),
    ]
    report = _report([_channel("a:0", 1, 500_000, 500_000)],
                     _market(own_orders=orders))
    r7 = [r for r in report.recommendations if r.rule == "R7"]
    assert len(r7) == 1
    assert r7[0].data["total_sat"] == 200_000


def test_ranking_severity_first():
    # Critical R1 must outrank everything else present.
    report = _report([_channel("a:0", 1, 20_000, 1_000)], _market())
    assert report.recommendations[0].rule == "R1"
    sevs = [int(r.severity) for r in report.recommendations]
    assert sevs == sorted(sevs, reverse=True)


def test_degrades_when_market_missing():
    report = _report(
        [_channel("a:0", 1, 20_000, 1_000)],
        _market(pool_connected=False, loop_connected=False, fees=False),
    )
    r1 = [r for r in report.recommendations if r.rule == "R1"][0]
    assert r1.command == ""                      # nothing priceable
    assert {"pool", "loop", "fees"} <= set(report.skipped_rules)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all recommend tests passed")
