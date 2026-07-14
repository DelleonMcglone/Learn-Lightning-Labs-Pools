# Lightning Liquidity Advisor

A **read-only, recommend-only** tool that reads a Lightning (`lnd`) node's actual
state and gives plain-language, actionable liquidity recommendations. It never
moves funds.

> Full design in [SPEC.md](./SPEC.md). This is the reference implementation,
> built milestone by milestone against that spec. **Current status: working
> MVP — M0–M5 complete** (63 tests passing; user flows tested end-to-end on
> CLI and web against the live testnet stack). Demo script: [DEMO.md](./DEMO.md).

## What works today (M0–M5)

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

- The **LLM advisor layer** (Claude) now runs by default on
  `advisor recommend`: it re-ranks and re-phrases the deterministic report
  in plain operator language, using the [knowledge base](./knowledge/) as
  its system prompt. Three enforced guarantees:
  - **privacy** — pubkeys, channel points, and the node alias are replaced
    with stable aliases (`peer-A`, `channel-1`) before anything leaves the
    machine (unit-tested);
  - **the number contract** — model prose containing any figure ≥1,000
    that the deterministic engine didn't compute is rejected, and that item
    falls back to the engine's own summary (unit-tested);
  - **never a dependency** — no API key / network / parse failure just
    means the offline report, with a note. `--offline` forces it.

  Set `ANTHROPIC_API_KEY` to enable; model configurable via
  `ADVISOR_LLM_MODEL` (default `claude-sonnet-4-5`).

- `advisor ingest` is the **ingestion pipeline**: one compact,
  non-identifying JSONL record per run (node totals, send/receive
  counters, fee tiers, Pool clearing rates, Loop quotes) appended to
  `~/.advisor/history.jsonl` — cron-friendly (`advisor ingest --quiet`).
  `advisor history` shows the time series and derived baselines. History
  powers trend-aware rules:
  - **R6 fee baseline** — after ≥3 records, today's chain fee is compared
    against **your recorded 7-day median**, not just the intra-day spread;
  - **R1 runway** — the inbound *trend* (sat/day, ≥3 records spanning ≥1h)
    fires "acquire inbound" even when today's share looks healthy, if the
    drain rate means you run dry within 7 days (CRITICAL under 3), with
    the runway stated in the recommendation.
- The CLI auto-loads a gitignored `.env` (see `.env.example`) so
  `ANTHROPIC_API_KEY` never needs to live in your shell profile.

- **`advisor serve` — the web UI** (default `http://127.0.0.1:8899`):
  recommendation views + a grounded chat, split-screen.
  - **Views** render the deterministic engine verbatim: liquidity seesaw
    per channel, live market tiles, severity-colored recommendation cards
    with copyable commands and caveats. No LLM in that path.
  - **Chat** (Claude) answers free-form questions grounded in the same
    sanitized report + knowledge base. Commands are quoted
    character-for-character from the report (never reconstructed), inputs
    are privacy-filtered, and the UI states plainly: cards are
    authoritative, chat is conversational. Without an API key the views
    work fully and chat says it's offline.

Remaining before the repo milestone closes: record the demo video
([DEMO.md](./DEMO.md) is the script) and optionally split into its own repo.
M6 stretch items (watch-mode, more rules) stay open.

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

## Commands

| Command | What it does |
| --- | --- |
| `advisor snapshot` | Read-only node state: identity, balances, per-channel in/outbound |
| `advisor signals` | Deterministic liquidity signals + IQR outlier flags (`--multiplier`) |
| `advisor market` | Live mempool fees, Pool auction state, Loop quotes |
| `advisor ingest` | Append one history record (`--quiet` for cron) |
| `advisor history` | The ingested time series + fee baseline + inbound trend |
| `advisor recommend` | Ranked recommendations with economics + commands (`--offline`, `--all`, `--json`) |
| `advisor serve` | Local web UI: recommendation views + grounded chat (port 8899) |

All commands take `--network` / `--host`; everything is overridable via
`ADVISOR_*` env vars or `.env`.

## Troubleshooting

- **`lnd unreachable`** — is lnd running and the wallet unlocked
  (`lncli unlock`)? Check `ADVISOR_RPC_HOST` / `ADVISOR_LNDDIR`.
- **`macaroon not found`** — the default path is
  `<lnddir>/data/chain/bitcoin/<network>/readonly.macaroon`; set
  `ADVISOR_MACAROON_PATH` (ideally to a baked least-privilege macaroon,
  see above).
- **Pool/Loop shown offline** — `poold`/`loopd` aren't running or aren't on
  the expected ports (`ADVISOR_POOL_BIN`, `ADVISOR_LOOP_REST_HOST`,
  `ADVISOR_LOOP_DIR`). Market-dependent rules skip gracefully and say so.
- **Chat says offline** — set `ANTHROPIC_API_KEY` in `advisor/.env`. Views
  and `--offline` recommendations never need it.
- **Fee baseline/trend say "needs ≥3 records"** — run `advisor ingest` on a
  schedule (`0 * * * * advisor ingest --quiet`); trends also need records
  spanning ≥1 hour.
- **Port 8899 in use** — `advisor serve --port <other>`.

## Tests

```bash
for t in tests/test_*.py; do PYTHONPATH=src python "$t"; done
# (or python -m pytest if installed)
```

## Safety

The Advisor is architecturally read-only (SPEC NFR1–NFR2): it loads a macaroon
without write/sign permissions and imports no fund-moving RPCs. It emits the
commands to act on a recommendation; **you** run them.

---

_Part of [Lightning Labs Prep](../README.md)._
