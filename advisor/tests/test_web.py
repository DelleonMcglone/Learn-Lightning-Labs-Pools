"""API tests for the web UI backend (fixture provider, no lnd/network)."""

import json

from fastapi.testclient import TestClient

from advisor.config import Settings
from advisor.models import (
    Balances, ChannelState, FeeEnvironment, LoopMarket, LoopQuote,
    MarketSnapshot, NodeIdentity, NodeSnapshot, PoolMarket,
)
from advisor.recommend import recommend
from advisor.signals import compute_signals
from advisor.web import create_app

DAY = 86_400
PEER = "03e84a109cd70e57864274932fc87c5e6434c59ebb8e6e7d28532219ba38f7f6df"


def _snap():
    return NodeSnapshot(
        identity=NodeIdentity(
            alias="web-test-node", pubkey="02aa", version="v",
            block_height=100, synced_to_chain=True,
            num_active_channels=1, num_peers=1,
        ),
        balances=Balances(onchain_confirmed=111_674, onchain_unconfirmed=0,
                          ln_local=20_515, ln_remote=1_014),
        channels=[ChannelState(
            chan_point="ff:0", chan_id=1, peer_pubkey=PEER,
            capacity_sat=25_000, local_sat=20_515, remote_sat=1_014,
            active=True, private=False, uptime_s=30 * DAY,
            lifetime_s=30 * DAY, total_sent_sat=0, total_received_sat=0,
        )],
    )


def _market():
    return MarketSnapshot(
        fees=FeeEnvironment(available=True,
                            sat_per_vb={1: 1.0, 3: 1.0, 6: 1.0, 144: 1.0}),
        pool=PoolMarket(connected=True, exec_fee_base_sat=1,
                        exec_fee_rate_ppm=1_000,
                        next_batch_feerate_sat_kw=6_250,
                        last_clearing_rate_ppb={2016: 6_613},
                        account_available_sat=200_000),
        loop=LoopMarket(
            connected=True, out_min_sat=250_000, out_max_sat=120_000_000,
            in_min_sat=250_000, in_max_sat=120_000_000,
            out_quote=LoopQuote(amount_sat=500_000, swap_fee_sat=552,
                                miner_fee_sat=7_280, prepay_sat=1_330),
            in_quote=LoopQuote(amount_sat=500_000, swap_fee_sat=417,
                               miner_fee_sat=550),
        ),
    )


class FixtureProvider:
    settings = Settings()

    def gather(self):
        snap = _snap()
        sig = compute_signals(snap)
        market = _market()
        return {"snap": snap, "sig": sig, "market": market,
                "report": recommend(snap, sig, market)}


class _FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeLLM:
    """Captures the system prompt for privacy assertions."""

    last_system = None

    class messages:  # noqa: N801 — mimic anthropic client shape
        @staticmethod
        def create(**kwargs):
            _FakeLLM.last_system = kwargs.get("system", "")
            r = type("R", (), {})()
            r.content = [_FakeBlock("Your node can't receive because the "
                                    "seesaw is fully on your side.")]
            return r


def _client(llm=None):
    return TestClient(create_app(FixtureProvider(),
                                 llm_client_factory=llm))


def test_index_serves_ui():
    r = _client().get("/")
    assert r.status_code == 200
    assert "Liquidity Advisor" in r.text
    assert "Recommendations" in r.text


def test_report_endpoint_shape():
    r = _client().get("/api/report")
    assert r.status_code == 200
    d = r.json()
    assert d["node"]["alias"] == "web-test-node"
    assert d["totals"]["inbound_sat"] == 1_014
    recs = d["report"]["recommendations"]
    assert recs and recs[0]["rule"] == "R1"
    assert "pool orders submit bid" in recs[0]["command"]


def test_chat_offline_without_key(monkeypatch=None):
    import os
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        r = _client().post("/api/chat",
                           json={"messages": [{"role": "user",
                                               "content": "help"}]})
        assert r.status_code == 200
        d = r.json()
        assert d["offline"] is True
        assert "ANTHROPIC_API_KEY" in d["reply"]
    finally:
        if saved:
            os.environ["ANTHROPIC_API_KEY"] = saved


def test_chat_grounded_and_sanitized():
    client = _client(llm=lambda: _FakeLLM)
    r = client.post("/api/chat", json={
        "messages": [{"role": "user", "content": "why can't I receive?"}]
    })
    assert r.status_code == 200
    assert "seesaw" in r.json()["reply"]
    # the system prompt got the knowledge base + sanitized report…
    sys_prompt = _FakeLLM.last_system
    assert "CURRENT REPORT" in sys_prompt
    assert "ppb" in sys_prompt  # knowledge base present
    # …and no identifiers leaked into it
    assert PEER not in sys_prompt
    assert PEER[:16] not in sys_prompt
    assert "web-test-node" not in sys_prompt



def test_chat_empty_messages_guarded():
    # must not hit the LLM at all — even with a factory that would fail
    def _explode():
        raise AssertionError("LLM must not be called for empty input")
    client = _client(llm=_explode)
    r = client.post("/api/chat", json={"messages": []})
    assert r.status_code == 200
    assert "Ask me something" in r.json()["reply"]
    r2 = client.post("/api/chat", json={
        "messages": [{"role": "user", "content": "   "}]})
    assert "Ask me something" in r2.json()["reply"]


def test_report_503_when_lnd_unreachable():
    from advisor.lndclient import LndClientError

    class DownProvider:
        settings = Settings()

        def gather(self):
            raise LndClientError("lnd unreachable (connection refused)")

    client = TestClient(create_app(DownProvider()))
    r = client.get("/api/report")
    assert r.status_code == 503
    assert "unreachable" in r.json()["error"]
    # chat surfaces the same failure gracefully (not a 500)
    r2 = client.post("/api/chat", json={
        "messages": [{"role": "user", "content": "hi"}]})
    assert r2.status_code in (200, 503)

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all web tests passed")
