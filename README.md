# ⚡ Lightning Labs Prep — Bitcoin & Lightning Network Study

A public, in-progress record of my work building deep Bitcoin and Lightning
Network expertise — node operation, channel liquidity, and the Lightning Labs
software suite (LND, Pool, Loop, Faraday). Everything here is original notes,
diagrams, setup logs, and source-code analysis produced as I work through a
structured learning sprint.

**Goal:** demonstrate verifiable, hands-on Lightning expertise through public
proof of work, and build toward contributing to the Lightning ecosystem.

> This repo is updated continuously as I move through the sprint. Commit history
> is the timeline.

---

## Contents

### Foundations

- [01 — Bitcoin Fundamentals](./01-bitcoin-fundamentals.md) — architecture, UTXOs, wallets, transactions
- [02 — Lightning Fundamentals](./02-lightning-fundamentals.md) — channels, HTLCs, liquidity glossary
- [03 — Lightning Liquidity](./03-lightning-liquidity.md) — inbound capacity, merchant liquidity, LSPs
- [04 — Pool: Auctions & Lease Pricing](./04-pool-auctions-lease-pricing.md) — sealed-bid batch auctions, ppb rates, lease economics
- [05 — Pool: Observations from Running It Live](./05-pool-observations.md) — hands-on findings, theory vs. practice, L402 debugging

### Hands-on Setup

- [Bitcoin Core setup](./setup/bitcoin-core.md) — install, configure, sync, verify
- [LND setup](./setup/lnd.md) — install, configure, connect to Bitcoin Core
- [Pool setup](./setup/pool.md) — clone, configure, connect to LND
- [Lightning operations](./setup/operations.md) — opening, funding, sending, receiving

### Source-Code Analysis

- [Pool](./repo-reviews/pool.md) — auction engine, lease pricing, APIs
- [LND](./repo-reviews/lnd.md) — architecture, routing, RPC interfaces
- [Loop](./repo-reviews/loop.md) — Loop In / Loop Out workflows
- [Faraday](./repo-reviews/faraday.md) — channel analytics, recommendation engine

### Diagrams

- [/diagrams](./diagrams) — concept diagrams for liquidity, auctions, and architecture

### Writing

Long-form articles explaining what I've learned:

- [What Is a Lightning Pool? A Beginner's Guide to the Liquidity Marketplace](./writing/what-is-lightning-pool.md) — the inbound-liquidity problem, LCLs, and the sealed-bid batch auction
- [How Lightning Liquidity Works: A Beginner's Guide to Moving Money on Lightning](./writing/how-lightning-liquidity-works.md) — inbound vs. outbound, the channel seesaw, rebalancing and fees
- [How Pool Solves Inbound Liquidity: Turning a Scramble Into a Marketplace](./writing/how-pool-solves-inbound-liquidity.md) — LCLs, premium vs. principal, and why the market actually fixes the problem

### Project

- **AI Liquidity Advisor** — an LLM-assisted tool that reads node state and
  recommends liquidity actions. **Working MVP** in [/advisor](./advisor):
  read-only collectors (lnd + Pool + Loop + mempool), deterministic signal
  and recommendation engines, Claude advisory layer with a privacy filter
  and number contract, ingestion history with trend-aware rules, CLI and a
  local web UI with grounded chat. [Spec](./advisor/SPEC.md) ·
  [Demo script](./advisor/DEMO.md). _(Demo video to follow.)_

---

## Progress

- [ ] **Foundations** — Bitcoin, Lightning, and liquidity notes published
- [ ] **Environment** — Bitcoin Core + LND running and synced
- [x] **Pool** — operational locally, connected to LND, first transaction logged
- [x] **Source-code analysis** — Pool, LND, Loop, Faraday summaries published
- [x] **Writing** — 3+ technical articles published
- [ ] **Project** — AI Liquidity Advisor MVP deployed with demo video
- [ ] **Open source** — 1 merged docs PR, 1 submitted code PR
  — docs PR submitted: [faraday#247](https://github.com/lightninglabs/faraday/pull/247)
  (clarifies bitcoind connection config; closes maintainer-triaged
  [faraday#176](https://github.com/lightninglabs/faraday/issues/176))
  — code PR submitted: [aperture#249](https://github.com/lightninglabs/aperture/pull/249)
  (L402 payments via router's `SendPaymentV2` instead of the deprecated
  `SendPaymentSync` — fixes the stuck-pending-token failure found during
  the [Pool testnet session](./setup/pool.md))

---

## Reference: the Lightning Labs suite

| Tool | Repo | What it does |
| --- | --- | --- |
| LND | `lightningnetwork/lnd` | Lightning Network node implementation |
| Pool | `lightninglabs/pool` | Marketplace for buying/selling channel liquidity (LCLs) |
| Loop | `lightninglabs/loop` | Non-custodial on-chain ↔ off-chain swaps |
| Faraday | `lightninglabs/faraday` | Channel management & accounting for node operators |
| Lightning Terminal | `lightninglabs/lightning-terminal` | Bundles the above behind one UI |

---

## License

[MIT](./LICENSE) — notes and original content free to read and reuse with attribution.

---

_Maintained by [Delleon](https://github.com/DelleonMcglone). Open to feedback —
issues and discussions welcome._
