# Repo Review — `lightninglabs/loop`

Source-code review **plus a live workflow test** of Loop at tag **v0.33.3-beta**
(~79k LOC). Loop is the non-custodial **submarine-swap** client: it moves value
between on-chain and off-chain in both directions, which is how a node acquires
inbound *or* outbound liquidity without manually opening channels. This review
studies both swap directions, traces the swap workflow/state machine, and then
**actually runs `loopd` against the live testnet swap server** to confirm the
economics.

> The two directions map directly onto the liquidity problem from
> [03 — Lightning Liquidity](../03-lightning-liquidity.md): **Loop Out** manufactures
> **inbound** liquidity; **Loop In** manufactures **outbound**. Companion
> reviews: [Pool](./pool.md), [LND](./lnd.md).

---

## 1. The primitive: a submarine-swap HTLC

Both directions are built on one object — an on-chain **HTLC with two spend
paths** (`swap/htlc.go`):

- **Success path** — spend with the **preimage** (+ receiver signature).
- **Timeout path** — spend after an absolute **CLTV expiry** (+ sender
  signature).

The same secret (preimage) unlocks *both* an on-chain HTLC and a Lightning
invoice, which is what makes the swap atomic: revealing the preimage to claim
one side necessarily reveals it to the other. Two script versions exist:
`HtlcV2` (P2WSH) and `HtlcV3` (Taproot tapscript with three spend paths, using
MuSig2). The `HtlcScript` interface abstracts success/timeout witnesses, and the
`Htlc` struct carries the pkScript, hash, and address.

A **swap contract** (`loopdb.SwapContract`) is the shared spine of both
directions: `Preimage`, `AmountRequested`, `HtlcKeys`, `CltvExpiry`,
`MaxSwapFee`, `MaxMinerFee`, `InitiationHeight`, `ProtocolVersion`.
`LoopOutContract` and `LoopInContract` embed it and add direction-specific
fields.

---

## 2. Loop Out — buying inbound liquidity (off-chain → on-chain)

**Goal:** spend Lightning balance, receive on-chain BTC. Net effect on the node:
outbound shrinks, **inbound grows**. `LoopOutContract` (`loopdb/loopout.go`) adds
`DestAddr`, `SwapInvoice`, `MaxSwapRoutingFee`, `SweepConfTarget`,
`HtlcConfirmations`, and — distinctively — a **prepayment**.

### The two-invoice design

Loop Out uses **two** Lightning invoices (`loopout.go`):

1. **Prepay invoice** — a small upfront payment the client makes *before* the
   server does anything on-chain. It's the server's "no-show fee": it compensates
   the server for publishing the on-chain HTLC even if the client never sweeps.
   Non-refundable.
2. **Swap invoice** — the main payment. Paying it is what ultimately releases the
   preimage to the server.

### The workflow (`loopOutSwap.executeSwap`)

```
newLoopOutSwap ─ negotiate swap+prepay invoices with server
      │
      ▼
executeSwap ─► payInvoices()            client pays prepay + swap invoice
      │                                 (off-chain, via lnd router)
      ▼
waitForConfirmedHtlc()                  SERVER publishes the on-chain HTLC;
      │                                 client waits for confirmations
      ▼
setStatePreimageRevealed()  ◄─ critical: only after the HTLC is safely
      │                        confirmed does the client reveal the preimage
      ▼
sweep (waitForHtlcSpendConfirmedV2)     client sweeps the HTLC to DestAddr
      │                                 using the preimage (success path)
      ▼
executeAndFinalize ─► StateSuccess
```

The key safety property is in the ordering: the client will not reveal its
preimage until it has seen the server's on-chain HTLC confirmed for enough
blocks — otherwise it could pay off-chain and get nothing on-chain. If the server
never publishes, the client's only loss is the prepay. `pushPreimage` can also
proactively hand the server the preimage once the client is committed, to speed
settlement.

---

## 3. Loop In — buying outbound liquidity (on-chain → off-chain)

**Goal:** send on-chain BTC, receive Lightning balance. Net effect: **outbound
grows** (and inbound shrinks). `LoopInContract` adds `HtlcConfTarget`, `LastHop`
(pin the swap to a specific channel), and `ExternalHtlc` (let an external wallet
fund the HTLC).

### The workflow (`loopInSwap.executeSwap`)

```
newLoopInSwap ─ negotiate the swap invoice with server
      │
      ▼
publishOnChainHtlc()        CLIENT publishes the on-chain HTLC itself
      │                     (this is why Loop In is cheaper — see §5)
      ▼
waitForHtlcConf()           wait for the HTLC to confirm
      │
      ▼
waitForSwapComplete()       server pays the client's Lightning invoice,
      │                     revealing the preimage; server sweeps the HTLC
      ▼
processHtlcSpend ─► StateInvoiceSettled ─► StateSuccess

  (failure branch) publishTimeoutTx()   if the server never pays, the client
                                        reclaims the HTLC via the timeout path
                                        after CLTV expiry
```

Here the roles invert: the **client** publishes and funds the on-chain HTLC, and
the **server** is the one who must pay a Lightning invoice to claim it. The
client's protection is the timeout path — if the server never pays, the client
sweeps its own money back after expiry (`publishTimeoutTx`).

---

## 4. The swap workflow engine

### State machine (`loopdb/swapstate.go`)

Every swap is a persisted state machine with **14 states**, keyed by a
`SwapStateType` of pending / success / fail:

| State | Meaning |
| --- | --- |
| `StateInitiated` (0) | Swap created |
| `StatePreimageRevealed` (1) | Preimage disclosed — past the point of no return |
| `StateHtlcPublished` (8) | On-chain HTLC broadcast |
| `StateInvoiceSettled` (9) | Off-chain invoice settled |
| `StateSuccess` (2) | Swept successfully |
| `StateFailTimeout` (4) | On-chain HTLC never confirmed in time |
| `StateFailSweepTimeout` (5) | HTLC confirmed but not swept before expiry |
| `StateFailOffchainPayments` (3) | Couldn't route the off-chain payment |
| `StateFailInsufficientValue` (6), `…IncorrectHtlcAmt` (10), `…Abandoned` (11), `…InsufficientConfirmedBalance` (12), `…Temporary` (7), `…IncorrectHtlcAmtSwept` (13) | Various failure modes |

State is journaled to `loopdb` (bbolt/sqlite) at every transition, so a swap
**resumes across `loopd` restarts** — essential, because a swap can span many
blocks and a crash mid-swap must not lose the preimage or the timeout deadline.

### Block-driven executor (`executor.go`)

The `executor` drives all in-flight swaps off **block-epoch notifications** from
lnd's `ChainNotifier`. Swaps advance on new blocks because every meaningful
event (HTLC confirmation, sweep, timeout) is height-relative. One notable safety
comment: the executor waits for a current block height *before* starting, so it
never reveals a preimage for a swap that has already expired.

### Autoloop (`liquidity/`)

The `liquidity` package is **Autoloop** — it monitors per-channel balance
against a configured rule and automatically suggests/dispatches swaps to
maintain it, gated by fee limits. Its documented max-fee formula is the whole
Loop cost model in one line:

```
maxFee = (swapAmount × serverPPM/1e6)   // server swap fee
       + minerFee                        // on-chain sweep/HTLC
       + (swapAmount × routingPPM/1e6)   // off-chain routing of the swap invoice
       + (prepayAmount × prepayPPM/1e6)  // off-chain routing of the prepay
```

The base fee math is `swap/fees.go`: `CalcFee = feeBase + amount·feeRate/1e6`
(parts-per-million, like Pool's ppb but coarser).

### Testing model (the "workflows" under test)

Swaps are tested by driving the FSM against **mocked lnd + mocked swap server +
mock store** (`test/` has ~14 mock files: chain notifier, invoices, router,
signer, lnd services). Each test injects chain/lightning events and asserts the
persisted state — e.g. `TestLateHtlcPublish` asserts `StateFailTimeout`,
`TestPreimagePush` and `TestLoopOutMuSig2Sweep` walk to `StateSuccess`,
`TestFailedOffChainCancellation` exercises the off-chain failure branch. This is
how the workflow branches are verified without a live server or real coins.

---

## 5. Live workflow test (testnet)

I built `loopd`/`loop` from this tag and ran them against the **live testnet
swap server** `test.swap.lightning.today:11010`, pointed at the same testnet
`lnd` from the [Pool session](../setup/pool.md). Real negotiated output:

**Server terms** (both directions): amount 250,000 – 120,000,000 sat; Loop Out
CLTV delta 100 – 400.

**Quotes at 500,000 sat** (real server responses):

| Direction | Send | Receive | Total fee | Effective |
| --- | --- | --- | --- | --- |
| **Loop Out** | 500,000 off-chain | 490,838 on-chain | **9,162 sat** | ~1.83% |
| **Loop In** | 500,000 on-chain | 499,033 off-chain | **967 sat** | ~0.19% |

**Loop Out fee scaling** (confirms the fee model is mostly a *fixed* on-chain
cost + a small proportional part):

| Amount | Total fee |
| --- | --- |
| 250,000 | 8,912 sat |
| 1,000,000 | 9,662 sat |
| 5,000,000 | 13,662 sat |

A 20× increase in amount (250k → 5M) raised the fee only ~1.5× — exactly what
`feeBase + amount·feeRate/1e6` predicts when `feeBase` (dominated by the on-chain
HTLC + sweep miner fees) is the large term. **The empirical numbers match the
source-level model.**

**Why Loop In is ~10× cheaper than Loop Out** — and the code says exactly why:
in Loop In the *client* publishes the on-chain HTLC (§3), so the server bears no
chain cost and charges little; in Loop Out the *server* publishes on-chain and
the client also pays to sweep (§2), so two on-chain footprints plus the swap fee
land on the client. This is the single most useful intuition from running it.

*(A full swap needs ≥250,000 sat and our testnet wallet held ~117k, so I tested
the negotiation/quote workflow end-to-end rather than settling a swap — the quote
path exercises the real server handshake, terms, and fee computation.)*

---

## 6. Takeaways

- **Two inverse workflows, one primitive.** Loop Out and Loop In are mirror
  images over the same success/timeout HTLC; which party publishes on-chain is
  what flips the cost profile and the direction of liquidity gained.
- **The prepay is the asymmetry that makes Loop Out safe for the server**;
  the timeout path is the asymmetry that makes Loop In safe for the client.
  Neither side can steal — worst case each reclaims via its escape path.
- **For the advisor project**, Loop is the *rebalancing actuator* that Pool
  isn't: where Pool buys a whole new leased channel, Loop shifts liquidity within
  existing channels. An advisor recommending "acquire inbound" has two priced
  options — Loop Out vs. a Pool bid — and the quote numbers above are exactly the
  comparison it must make. Autoloop's fee formula is the ready-made cost model.
- **`loopd` mirrors `poold`.** Same shape: a daemon backed by `lnd`, an L402-gated
  swap server, a persisted state machine, a CLI. Everything learned wiring up
  Pool transferred directly to standing this up.

---

## File map (where to look first)

| To understand… | Read |
| --- | --- |
| The swap HTLC (success/timeout, V2/V3) | `swap/htlc.go` |
| Loop Out workflow + prepay | `loopout.go`, `loopdb/loopout.go` |
| Loop In workflow + timeout reclaim | `loopin.go`, `loopdb/loopin.go` |
| Swap state machine | `loopdb/swapstate.go`, `loopdb/loop.go` |
| Block-driven orchestration | `executor.go` |
| Autoloop + full fee model | `liquidity/liquidity.go`, `swap/fees.go` |
| Testing the workflow branches | `loopout_test.go`, `loopin_test.go`, `test/` |
| Local API you drive | `looprpc/`, `cmd/loop/`, `loopd/` |

---

_Part of [Lightning Labs Prep](../README.md). Reviewed at tag `v0.33.3-beta`;
workflows tested live against `test.swap.lightning.today`. Companion:
[Pool review](./pool.md), [LND review](./lnd.md)._
