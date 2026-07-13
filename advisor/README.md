# Lightning Liquidity Advisor

A **read-only, recommend-only** tool that reads a Lightning (`lnd`) node's actual
state and gives plain-language, actionable liquidity recommendations. It never
moves funds.

> Full design in [SPEC.md](./SPEC.md). This is the reference implementation,
> built milestone by milestone against that spec. **Current status: M0
> (scaffold + node snapshot).**

## What works today (M0)

- Connects to `lnd` over gRPC with a **read-only macaroon** (never `admin`).
- Collects a typed `NodeSnapshot` — identity, balances, and per-channel
  inbound/outbound liquidity.
- `advisor snapshot` prints it as a readable report (or `--json`).

Signals (M1), market + fee collectors (M2), the recommendation engine (M3), and
the LLM advisor (M4) follow the [roadmap](./SPEC.md#8-roadmap).

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
├── src/advisor/
│   ├── config.py            connection settings (env / CLI overridable)
│   ├── models.py            NodeSnapshot + typed sub-models
│   ├── lndclient.py         read-only gRPC client (TLS + macaroon)
│   ├── collectors/          data collectors (M0: lnd; M2: pool/loop/fees)
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
