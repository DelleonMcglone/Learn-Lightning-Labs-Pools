# ⚡ Learn Lightning Labs Pools

A public, practice guide for hands-on Bitcoin & Lightning Network expertise —
node ops, channel liquidity, and the Lightning Labs suite (LND, Pool, Loop,
Faraday).

This repo is a **study guide you can follow**, built by actually doing every
step: original notes, reproducible setup logs, source-code walkthroughs of the
four core repos, teaching articles, and a working capstone project. Nothing
here is theory-only — every number, command, and gotcha was hit on a real node.

**Who it's for:** developers and node operators who want to go from "I know
what Lightning is" to *running nodes, reasoning about liquidity markets,
reading the Lightning Labs codebases, and contributing upstream*.

---

## How to use this guide

Work the phases in order — each builds on the one before. Read the notes,
run the setups yourself, and check items off as you go. Where a phase has a
worked example in this repo (a log, a review, a PR), use it as the answer key
after you've tried it yourself.

### Phase 1 — Foundations

Build the mental models everything else stands on.

- [01 — Bitcoin Fundamentals](./01-bitcoin-fundamentals.md) — architecture, UTXOs, wallets, transactions
- [02 — Lightning Fundamentals](./02-lightning-fundamentals.md) — channels, HTLCs, liquidity glossary
- [03 — Lightning Liquidity](./03-lightning-liquidity.md) — inbound capacity, merchant liquidity, LSPs
- [04 — Pool: Auctions & Lease Pricing](./04-pool-auctions-lease-pricing.md) — sealed-bid batch auctions, ppb rates, lease economics
- [05 — Pool: Observations from Running It Live](./05-pool-observations.md) — theory vs. practice, what only shows up hands-on

**You're done when:** you can explain the liquidity seesaw, and say why Pool uses a uniform-price sealed-bid auction.

### Phase 2 — Environment

Stand up your own stack. These are reproducible logs — follow them
command-by-command (testnet/signet, no real funds).

- [Bitcoin Core setup](./setup/bitcoin-core.md) — install, configure, sync, verify
- [LND setup](./setup/lnd.md) — install, configure, connect to Bitcoin Core
- [Pool setup](./setup/pool.md) — poold against the public test auctioneer

**You're done when:** your node is synced, unlocked, and `pool auction fee`
answers from the live test auctioneer.

### Phase 3 — Hands-on operations

Use the network for real: this is where the concepts become muscle memory.

- [Lightning operations](./setup/operations.md) — funding via faucets, peering, opening channels, sending & receiving
- [Pool operations](./setup/pool.md) — open a 2-of-2 account on-chain, read the live book, place a bid

**You're done when:** you've settled a Lightning payment in each direction
and placed (or priced) a real Pool order — and hit at least one gotcha the
logs warned you about.

### Phase 4 — Source-code analysis

Read the code behind what you just ran. Each review is a map: architecture,
the subsystem that matters most, and where to look first.

- [Pool](./repo-reviews/pool.md) — auction engine, batch verification, APIs
- [LND](./repo-reviews/lnd.md) — daemon architecture, routing system, RPC interfaces
- [Loop](./repo-reviews/loop.md) — Loop In / Loop Out swap workflows (+ live cost test)
- [Faraday](./repo-reviews/faraday.md) — channel analytics, the recommend-only engine pattern

**You're done when:** given a behavior you saw in Phase 3, you can name the
package that implements it.

### Phase 5 — Teach it

Writing is the test of understanding. Three worked examples of turning the
notes into blog posts:

- [What Is a Lightning Pool?](./writing/what-is-lightning-pool.md) — the liquidity marketplace, explained from zero
- [How Lightning Liquidity Works](./writing/how-lightning-liquidity-works.md) — inbound vs. outbound, the channel seesaw
- [How Pool Solves Inbound Liquidity](./writing/how-pool-solves-inbound-liquidity.md) — premium vs. principal, why the market works

**You're done when:** you've published an explanation of one concept in your
own words, that a newcomer can follow.

### Phase 6 — Build & contribute

Apply everything: build something real on the stack, and send fixes upstream
for what you break against.

- **Capstone — AI Liquidity Advisor** ([/advisor](./advisor)): a read-only,
  recommend-only tool — collectors for lnd + Pool + Loop + mempool,
  deterministic signal and recommendation engines, a Claude advisory layer
  with a privacy filter and number contract, ingestion history with
  trend-aware rules, a CLI and a local web UI with grounded chat.
  [Spec](./advisor/SPEC.md) · [Demo script](./advisor/DEMO.md)
- **Upstream contributions** — real examples produced by this guide's path:
  - [faraday#247](https://github.com/lightninglabs/faraday/pull/247) — docs:
    clarify bitcoind connection config (closes maintainer-triaged
    [faraday#176](https://github.com/lightninglabs/faraday/issues/176))
  - [aperture#249](https://github.com/lightninglabs/aperture/pull/249) — code:
    L402 payments via router's `SendPaymentV2` instead of deprecated
    `SendPaymentSync` — fixing a failure first hit in
    [Phase 3](./setup/pool.md)

**You're done when:** you've shipped a small tool against a real node and
opened at least one upstream PR — docs count.

---

## Your checklist

- [ ] **Foundations** — notes read; seesaw + ppb→APR fluent
- [ ] **Environment** — Bitcoin Core + LND running and synced
- [ ] **Pool** — connected to the test auctioneer; first transaction done
- [ ] **Source** — all four repo maps read; can navigate each codebase
- [ ] **Teach** — published one explanation in your own words
- [ ] **Build** — a small tool running against your node
- [ ] **Contribute** — one upstream PR opened

---

## Reference: the Lightning Labs suite

| Tool | Repo | What it does |
| --- | --- | --- |
| LND | `lightningnetwork/lnd` | Lightning Network node implementation |
| Pool | `lightninglabs/pool` | Marketplace for buying/selling channel liquidity (LCLs) |
| Loop | `lightninglabs/loop` | Non-custodial on-chain ↔ off-chain swaps |
| Faraday | `lightninglabs/faraday` | Channel management & accounting for node operators |
| Lightning Terminal | `lightninglabs/lightning-terminal` | Bundles the above behind one UI |

Also: [/diagrams](./diagrams) — concept and architecture diagrams used
throughout the guide.

---

## License

[MIT](./LICENSE) — notes and original content free to read and reuse with attribution.

---

_Maintained by [Delleon](https://github.com/DelleonMcglone). Open to feedback —
issues and discussions welcome._
