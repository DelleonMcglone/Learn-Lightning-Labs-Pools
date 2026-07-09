# Repo Review — `lightninglabs/pool`

Source-code review of the Pool **trader client** at tag **v0.7.1-beta**
(`go.mod` module `github.com/lightninglabs/pool`, built against
`lnd v0.19.0-beta` / `lndclient v0.19.0-7` / `aperture v0.3.8-beta`). The goal is
to map the codebase well enough to contribute: what the packages are, what the
API surface looks like, and how the auction engine actually validates and signs a
batch.

> **Scope note.** This repo is the **client only** (`poold` + the `pool` CLI).
> The auctioneer that runs the matching and constructs batches is closed-source.
> So the "auction engine" you can read here is really the **client-side
> verification and signing** of batches the server proposes — which, as it turns
> out, is the more security-critical half. Concepts behind this review:
> [04 — Pool: Auctions & Lease Pricing](../04-pool-auctions-lease-pricing.md);
> hands-on driving of the same binary:
> [05 — Observations](../05-pool-observations.md) and
> [Pool setup log](../setup/pool.md).

---

## 1. Project structure

The repo is a flat Go module with feature packages at the top level and the
daemon wiring in the root package. Rough sizes (non-test LOC):

| Package | LOC | Responsibility |
| --- | ---: | --- |
| `account/` | ~5,160 | Account lifecycle: reservation, funding, the **10-state machine**, deposits/withdrawals/renewals, recovery, on-chain watching (`account/watcher/`) |
| `order/` | ~4,220 | Orders, the **batch verifier/signer/storer**, fee & premium math, supply-unit quantization, RPC parsing |
| `clientdb/` | ~4,140 | `bbolt` persistence for accounts, orders, batches, sidecars |
| `auctioneer/` | ~2,100 | gRPC client to the auctioneer + the bidirectional auction message stream |
| `funding/` | ~1,660 | Bridges matched orders to `lnd`'s funding manager (shims, peer connections, channel setup) |
| `sidecar/` + `*sidecar*.go` | ~1,200+ | Sidecar channels (leasing a channel to a third-party node) |
| `poolscript/` | ~900 | Account output scripts: p2wsh 2-of-2+CLTV and taproot MuSig2 |
| `terms/` | ~110 | Auctioneer fee schedule types |
| `chaninfo/`, `event/`, `codec/` | small | Channel enforcement info, event log, wire encoding |

Root package (`server.go`, `rpcserver.go`, `config.go`, `run.go`,
`sidecar_acceptor.go`, `auto_sidecar.go`) is the daemon: it wires the packages
together, exposes the local gRPC/REST API, and **drives the batch execution
loop**. `rpcserver.go` alone is ~3,550 LOC and is where the auction message
handler lives.

**Layering, cleanly separated:**

```
pool CLI ── gRPC ──► poold (rpcserver.go / server.go)
                        │
       ┌────────────────┼─────────────────┬──────────────┐
       ▼                ▼                 ▼              ▼
   order/           account/          funding/      auctioneer/
  (verify/sign)   (state machine)   (lnd funding)  (server stream)
       │                │                 │              │
       └──────── clientdb/ (bbolt) ───────┘         (to auctioneer)
                        │
                   poolscript/  ◄── lnd wallet (lndclient)
```

The dependency edges only point downward, and `interfaces.go` + generated
`mock_interfaces.go` in each package keep the layers testable in isolation.

---

## 2. The API surface

### 2a. Local trader API (`poolrpc`) — what you drive

The `Trader` gRPC service (`poolrpc/trader.proto`) exposes **30 RPCs**. Grouped:

- **Accounts** — `QuoteAccount`, `InitAccount`, `ListAccounts`, `CloseAccount`,
  `WithdrawAccount`, `DepositAccount`, `RenewAccount`, `BumpAccountFee`,
  `RecoverAccounts`, `AccountModificationFees`
- **Orders** — `SubmitOrder`, `ListOrders`, `CancelOrder`, `QuoteOrder`
- **Auction (read-only market state)** — `AuctionFee`, `LeaseDurations`,
  `NextBatchInfo`, `BatchSnapshot`, `BatchSnapshots`, `Leases`, `NodeRatings`
- **Sidecar** — `OfferSidecar`, `RegisterSidecar`, `ExpectSidecarChannel`,
  `DecodeSidecarTicket`, `ListSidecars`, `CancelSidecar`
- **Misc** — `GetInfo`, `StopDaemon`, `GetLsatTokens`

The `pool` CLI (`cmd/pool/`) is a thin gRPC wrapper, organized into command
groups: `accounts`, `orders`, `auction`, `sidecar`, plus top-level `getinfo`
etc. The read-only `auction` subtree (`fee`, `leasedurations`, `nextbatchinfo`,
`snapshot`, `leases`, `ratings`) is queryable **with no account and no funds** —
the entire market-state input is free, which I confirmed hands-on in
[note 05](../05-pool-observations.md).

REST is auto-generated via grpc-gateway (`poolrpc/*.gw.go`).

### 2b. Auctioneer client (`auctioneer/client.go`) — what poold calls upstream

The `Client` wraps the upstream `ChannelAuctioneer` gRPC service and holds the
**long-lived bidirectional stream** that carries batch execution. Key methods:
`ReserveAccount`, `InitAccount`, `ModifyAccount`, `SubmitOrder`, `CancelOrder`,
`OrderState`, `StartAccountSubscription`, `RecoverAccounts`,
`SendAuctionMessage`, `Terms`, `BatchSnapshot(s)`, `MarketInfo`, `NodeRating`,
plus cipher-box methods for the sidecar/account mailbox (used to relay
negotiation messages between traders).

**Auth is L402.** Every upstream call is gated by an L402 token (a macaroon
unlocked by paying a Lightning invoice), handled by `aperture`'s client
interceptor. This is the exact path that broke in my hands-on session —
`aperture` → `lndclient.PayInvoice` → the deprecated `SendPaymentSync` RPC —
documented in [setup/pool.md §3](../setup/pool.md). Worth an upstream issue:
migrate that call to `SendPaymentV2`.

---

## 3. The auction engine (client side)

### 3a. The order/batch data model

- **`FixedRatePremium`** (`order/tradingfees.go`) — a `uint32` rate in
  parts-per-billion **per block**. `FeeRateTotalParts = 1e9`. The core formulas
  from [note 04](../04-pool-auctions-lease-pricing.md) are literally these two
  functions:
  ```go
  PerBlockPremium(amt, rate) = float64(amt) * float64(rate) / 1e9
  LumpSumPremium(amt, dur)   = PerBlockPremium(amt, rate) * float64(dur)
  ```
- **`SupplyUnit`** — orders are quantized to 100,000-sat units; matching happens
  in whole units (`order/supplyunit.go`).
- **`Batch`** (`order/batch.go`) — everything the auctioneer sends for a trader
  to validate: `MatchedOrders` (our nonce → the other side's orders),
  `AccountDiffs`, `ExecutionFee`, `ClearingPrices` (**per duration bucket**),
  the full `BatchTX`, `BatchTxFeeRate`, `HeightHint`, and MuSig2 `ServerNonces`.
- **Batch versioning** is a neat detail: early versions were linear (0,1,2…10),
  then it switched to a **flag-based scheme** (`ZeroConfChannelsFlag = 0x10`,
  `UpgradeAccountTaprootV2Flag = 0x20`) so lnd-independent features compose
  bitwise. `LatestBatchVersion` = taproot-upgrade | zeroconf | taproot-v2.

### 3b. `BatchVerifier` — the public contract

`order/batch_verifier.go` (~400 LOC) is the heart of the review: the trader
**independently re-derives the batch** and refuses to sign if the server's
numbers don't match. `Verify(batch, bestHeight)` checks, in order:

1. **Version match** — reject if the server's batch version ≠ ours.
2. **Height sanity** — reject if our best height is more than `±3` blocks
   (`heightHintPadding`) from the batch's height hint.
3. **Per matched order** (`validateMatchedOrder`):
   - opposite order type (bid matches ask), same auction type, **not our own
     node**;
   - durations overlap; **ask price ≤ bid price**;
   - tally the account balance delta via `CalcMakerDelta` / `CalcTakerDelta`.
4. **Channel output exists** (`validateChannelOutput` → `ChannelOutput`) — there
   must be an output in `BatchTX` with the exact value and the exact 2-of-2
   funding script we expect (re-derived from our multisig key + theirs).
5. **Clearing-price compliance** — our bid's price must be **≥** the clearing
   price; our ask's must be **≤** it (this is the uniform-price guarantee,
   enforced client-side).
6. **Fill bounds** — not over-filled beyond `UnitsUnfulfilled`, not under
   `MinUnitsMatch`.
7. **Per account diff** — add chain fees (`ChainFees`), then require the
   server's `EndingBalance` to **exactly equal our independent tally**, and
   `validateEndingState` must confirm the recreated account output (script +
   amount) or correct dust handling.

If any check fails it returns a `MismatchErr` (which unwraps to
`ErrMismatchErr`) and the daemon sends a **reject**. This is why "reading the
verifier is the fastest way to internalize what a valid batch even means" — it
is the complete, executable definition of a correct batch from the client's
side.

### 3c. The fee / tally math

`AccountTally` (`order/tradingfees.go`) accumulates, per account, across all its
orders in the batch:

- **Maker delta**: `−channelAmt + premium − executionFee` (maker locks capital,
  earns the premium, pays exec fee).
- **Taker delta**: `−premium − selfChanBalance − executionFee` (taker prepays the
  premium).
- **Chain fees** (`EstimateTraderFee`): each trader pays for its **account input
  + recreated account output + half of each channel output** (makers and takers
  split the funding-output cost), scaled by the witness factor, plus the witness
  size for its account version (taproot vs. p2wsh). This is the fixed,
  size-independent cost that sets the breakeven floor I worked through in note
  04 — and here it is in code.

`MinNoDustAccountSize` defines when a recreated account output is dust (and thus
folded into fees / spent) vs. re-created — the branch `validateEndingState`
enforces.

### 3d. The three-step execution loop

The daemon drives batch execution over the auctioneer stream in `rpcserver.go`
(the handler around line 357), matching the closed-source auctioneer's
Prepare/Sign/Finalize protocol:

1. **`Prepare`** — parse the batch → `OrderMatchValidate` (the verifier above) →
   `fundingManager.PrepChannelFunding` (connect to peers, register funding
   shims) → **send Accept** (or Reject on any failure). Idempotent: a re-sent
   Prepare clears the previous pending batch's artifacts first.
2. **`Sign`** — parse server MuSig2 nonces + prev-outputs →
   `BatchChannelSetup` (negotiate the actual channels with matched peers) →
   `BatchSign` (sign **our account inputs**) → send our sigs + channel keys.
3. **`Finalize`** — the batch tx is broadcast; open the channels, persist account
   & order diffs (`BatchStorer`), resume watching accounts.

So the trust model is exactly as advertised: the auctioneer proposes, but the
client **validates every number and only signs its own account inputs** — it can
never be made to sign a batch that doesn't match its own orders and constraints.

---

## 4. The account state machine

`account/interfaces.go` defines **10 states**; `account/manager.go` (~2,670 LOC)
implements the transitions, keyed off chain events and batch participation:

| State | Meaning |
| --- | --- |
| `StateInitiated` (0) | Reserved with the auctioneer, funding tx not yet made |
| `StatePendingOpen` (1) | Funding tx broadcast, awaiting confirmation |
| `StatePendingUpdate` (2) | Deposit/withdraw/renew in flight |
| `StateOpen` (3) | Confirmed and usable |
| `StateExpired` (4) | Reached absolute expiry height |
| `StatePendingClosed` (5) | Close tx broadcast |
| `StateClosed` (6) | Close confirmed |
| `StateCanceledAfterRecovery` (7) | Abandoned during recovery |
| `StatePendingBatch` (8) | Spent by a batch, awaiting confirmation |
| `StateExpiredPendingUpdate` (9) | Update in flight on an expired account |

The **on-chain script** (`poolscript/script.go`) is what makes the non-custodial
claim real. Two versions:

- **p2wsh (v0):** `<trader_key> OP_CHECKSIGVERIFY <auctioneer_key> OP_CHECKSIG
  OP_IFDUP OP_NOTIF <expiry> OP_CHECKLOCKTIMEVERIFY OP_ENDIF` — cooperative
  2-of-2, **or** the trader alone after `expiry` (CLTV).
- **taproot MuSig2 (v1/v2):** internal key = MuSig2(trader, auctioneer); single
  tap leaf = `<trader_key> OP_CHECKSIGVERIFY <expiry> OP_CHECKLOCKTIMEVERIFY`.

Both encode the same guarantee I called out in note 04: **a trader-only timeout
path**, so if the auctioneer vanishes the trader unilaterally recovers funds
after expiry. Keys are tweaked per batch by the **batch key + a shared secret**,
which is what makes account outputs deterministically re-derivable across batches
(`DecrementingBatchIDs` walks the batch-key chain for recovery). My hands-on
account opened as `ACCOUNT_VERSION_TAPROOT_V2` — the v2 MuSig2 (RC2 spec) path.

---

## 5. Takeaways for contributing

- **The verifier is the contribution surface.** Because it's the executable
  spec of a valid batch, most client-side correctness work (new order
  constraints, new channel types, fee changes) touches
  `order/batch_verifier.go` + `tradingfees.go` + `batch.go` together. That's the
  triad to understand first.
- **A concrete, reproducible bug is already in hand.** The L402 auto-pay path
  depends on the deprecated `SendPaymentSync` (via `lndclient.PayInvoice`),
  which fails on recent lnd; migrating to `SendPaymentV2` is a well-scoped PR
  spanning `pool` → `aperture` → `lndclient`. Repro in
  [setup/pool.md §3](../setup/pool.md).
- **Flag-based batch versioning is extensible by design.** New lnd-independent
  features get a bit in the high nibble, not a new linear version — a clean place
  to add an order feature without a protocol bump.
- **Everything is interface + mock.** Each package ships `interfaces.go` and
  `mock_interfaces.go`, so new behavior is unit-testable without a live
  auctioneer — the practical on-ramp for a first PR.

---

## File map (where to look first)

| To understand… | Read |
| --- | --- |
| What a valid batch is | `order/batch_verifier.go` |
| Premium / fee / chain-cost math | `order/tradingfees.go` |
| Batch data model & versioning | `order/batch.go` |
| The Prepare/Sign/Finalize loop | `rpcserver.go` (~L357) |
| Account lifecycle | `account/manager.go`, `account/interfaces.go` |
| The non-custodial account script | `poolscript/script.go` |
| Upstream auctioneer protocol | `auctioneer/client.go`, `auctioneerrpc/auctioneer.proto` |
| Local API you drive | `poolrpc/trader.proto`, `cmd/pool/` |

---

_Part of [Lightning Labs Prep](../README.md). Reviewed at tag `v0.7.1-beta`.
Companion notes: [04 — Auctions & Lease Pricing](../04-pool-auctions-lease-pricing.md),
[05 — Observations from Running It Live](../05-pool-observations.md)._
