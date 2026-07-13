# Lightning Liquidity Advisor

A **read-only, recommend-only** tool that reads a Lightning (`lnd`) node's actual
state and gives plain-language, actionable liquidity recommendations. It never
moves funds.

> Full design in [SPEC.md](./SPEC.md). This is the reference implementation,
> built milestone by milestone against that spec. **Current status: M3
> (deterministic recommendation engine).**

## What works today (M0–M3)

- Connects to `lnd` over gRPC with a **read-only macaroon** (never `admin`).
- Collects a typed `NodeSnapshot` — identity, balances, per-channel
  inbound/outbound liquidity, and 30-day forwarding history.
- `advisor snapshot` prints it as a readable report (or `--json`).
- `advisor signals` computes deterministic liquidity signals: per-channel
  balance/imbalance, uptime, routing performance normalized per
  capacity-day, and **Faraday-style IQR outlier flags** (the IQR port is
  unit-tested against Faraday's own documented example). Private and
  too-young channels are filtered before statistics, exactly like Faraday.

- `advisor market` collects the live outside world (SPEC FR3/FR4), each
  source degrading independently:
  - **mempool fees** at 1/3/6/144-block targets (mempool.space);
  - **Pool auction state** via a running `poold` (execution fee, open
    duration markets, per-bucket depth, last clearing rate with APR);
  - **Loop terms + quotes** via a running `loopd`'s REST API (Loop Out /
    Loop In cost at a reference amount, effective %).

- `advisor recommend` runs the deterministic rule engine (R1–R7 from
  SPEC §5) over signals + market + fees and emits **ranked, plain-language
  recommendations with computed economics and ready-to-run commands** —
  e.g. on the testnet node it correctly fires a CRITICAL "acquire inbound"
  with a fully-priced Pool bid (premium + execution fee + chain footprint)
  and marks Loop Out infeasible below the server minimum. Top-3 by default
  (`--all` for everything); every number is unit-tested against the worked
  examples in note 04.

The LLM advisor (M4) follows the [roadmap](./SPEC.md#8-roadmap). The
[knowledge base](./knowledge/) it will load is drafted; `advisor recommend`
is already the auditable offline baseline it must never contradict.

## Quickstart

```bash
cd advisor
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
./scripts/gen_proto.sh          # generate gRPC stubs from vendored protos

# Point at your node (defaults target the local testnet lnd; all overridable)
export ADVISOR_LND_HOST=localhost:10010
export ADVISOR_NETWORK=testnet

advisor snapshot                # human-readable
advisor snapshot --json         # machine-readable
```

By default the Advisor reads
`<lnddir>/data/chain/bitcoin/<network>/readonly.macaroon` and `<lnddir>/tls.cert`.
Override with `ADVISOR_LNDDIR`, `ADVISOR_MACAROON_PATH`, `ADVISOR_TLS_CERT_PATH`,
or the `--network` / `--host` flags.

### Least-privilege credential (recommended)

Bake a scoped read-only macaroon so the Advisor is *incapable* of moving funds:

```bash
lncli bakemacaroon info:read offchain:read onchain:read \
  --save_to advisor.macaroon
export ADVISOR_MACAROON_PATH=$PWD/advisor.macaroon
```

## Layout

```
advisor/
├── SPEC.md                  design record (requirements, architecture, roadmap)
├── proto/lightning.proto    vendored lnd proto (v0.19.0-beta)
├── scripts/gen_proto.sh     regenerate gRPC stubs
├── knowledge/               curated domain corpus for the M4 LLM layer
├── src/advisor/
│   ├── config.py            connection settings (env / CLI overridable)
│   ├── models.py            NodeSnapshot + typed sub-models
│   ├── lndclient.py         read-only gRPC client (TLS + macaroon)
│   ├── collectors/          data collectors (M0: lnd; M2: pool/loop/fees)
│   ├── signals/             M1 signal engine (IQR dataset + engine)
│   ├── lnrpc/               generated gRPC stubs (git-tracked)
│   └── cli.py               `advisor` CLI
└── tests/                   deterministic unit tests
```

## Tests

```bash
python -m pytest        # or: python tests/test_models.py
```

## Safety

The Advisor is architecturally read-only (SPEC NFR1–NFR2): it loads a macaroon
without write/sign permissions and imports no fund-moving RPCs. It emits the
commands to act on a recommendation; **you** run them.

---

_Part of [Lightning Labs Prep](../README.md)._
