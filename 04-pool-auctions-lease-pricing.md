# 04 — Pool: Auctions & Lease Pricing

Notes on Lightning Pool's auction mechanism and the economics of channel leases:
what market Pool actually clears, how sealed-bid uniform-price batches work, why
makers can't cheat on the lease term, and how to reason about lease pricing like
an interest-rate instrument. Builds on the liquidity problem framed in
[Lightning Liquidity](./03-lightning-liquidity.md) and assumes the basic Pool
architecture (the `poold` trader daemon, the closed-source auctioneer, and the
2-of-2 account state machine).

> Source material: Lightning Labs / lightning.engineering documentation, the
> Pool client source, plus my own study notes.

---

## Part I — The auction

### 1. The market being cleared

Pool is a marketplace for **Lightning Channel Leases (LCLs)** — packaged inbound
liquidity with a fixed duration. The unit of price is a **fixed rate per block,
denominated in parts-per-billion (ppb)** of the leased capital. A bid isn't
"I'll pay 10,000 sats" — it's "I'll pay X ppb per block on the amount, for N
blocks":

```
premium_sats = amount_sats × rate_ppb × duration_blocks / 1e9
```

This makes leases directly comparable to interest-rate instruments — any
clearing rate can be annualized (see [Part II](#6-the-apr-conversion)), which is
how people quote Pool yields as APR.

Each **lease duration is its own market**: originally just 2016 blocks
(≈ 2 weeks), with longer buckets (4032 / 6048 / 8064) added later. Orders in
different duration buckets never match against each other.

### 2. Order flow and clearing

**Submission (sealed-bid).** Traders submit **Bids** (buying inbound liquidity)
and **Asks** (leasing out capital) to the auctioneer over gRPC. The book is
**private** — no one sees resting orders, which kills front-running and
quote-matching games. Orders carry constraints:

- minimum chunk size
- `max_batch_feerate` — the highest on-chain feerate the trader will clear at
- minimum node tier (Bos-score derived T0/T1)
- later options: **sidecar channels** (a lease delivered to a third party's
  node — onboarding someone without them holding sats), self-payment flags,
  unannounced-channel flags

**Batch ticks.** The auctioneer attempts to make a market roughly every 10
minutes (block cadence), but a batch only occurs if orders actually cross
*after accounting for chain fees*. If on-chain fees exceed what matched
participants will tolerate (their `max_batch_feerate`), the match is skipped.
**High-fee environments can stall clearing entirely** — a real operational
quirk worth knowing.

**Uniform clearing price.** Within a duration bucket, highest bids match
against lowest asks and *everyone* clears at a **single market clearing rate**
— not their own quoted price. Bidders often pay less than they bid; askers
often earn more than they asked. Uniform pricing plus sealed bids means the
roughly dominant strategy is to **bid your true valuation** — the
auction-theory point of the design. It mitigates the winner's curse and the
price-shading games you'd get in a pay-as-bid format.

### 3. Batch execution

This is where the account state machine plugs in. The auctioneer constructs
**one batch transaction** that:

- spends the 2-of-2 accounts of every matched trader (inputs)
- creates the channel funding outputs for each matched lease
- recreates each trader's account with an updated balance (premiums
  debited/credited, fees deducted)
- pays the auctioneer's execution fee output

Signing is **interactive**: the auctioneer proposes the batch, each trader's
`poold` independently validates that the batch matches its orders and
constraints, then co-signs its account input. If a trader is offline or
refuses, they're **ejected** and the batch is re-proposed without them.

Two implementation details worth remembering:

- The auctioneer uses an incrementing **batch key** (tweaked by G each batch),
  so account outputs are deterministically re-derivable across batches.
- Because accounts are respent every batch, batches can be **chained before
  confirmation** — a new batch can spend unconfirmed account outputs from the
  prior one, so clearing doesn't wait on confirmations.

### 4. Enforcement: why makers can't cheat

The lease duration isn't a gentleman's agreement. Pool uses **script-enforced
channel leases**: the maker's (asker's) output in the channel commitment
carries an additional **CLTV to the lease expiry height**. The capital provider
literally cannot cooperatively close and recover funds early — the script won't
let them sweep before expiry. The taker can close whenever. This asymmetry is
what makes the lease a credible fixed-term instrument.

Account safety mirrors it: every account has an absolute expiry height with a
**trader-only timeout path**, so if the auctioneer disappears, funds are
recoverable unilaterally — consistent with the non-custodial framing despite
the centralized matching.

---

## Part II — Lease pricing economics

### 5. The pricing primitive: rate per block

The premium is **prepaid in full at batch execution** — debited from the
bidder's account and credited to the asker's account inside the batch
transaction itself.

Worked example: 10M sats at 1,500 ppb for 2016 blocks:

```
10,000,000 × 1,500 × 2,016 / 1e9 = 30,240 sats premium
```

Orders are sized in **units of 100,000 sats** — the quantization for matching.
A bid can be partially filled in unit increments across multiple asks.

### 6. The APR conversion

To compare against anything else in finance, annualize with ~52,560
blocks/year:

```
APR ≈ rate_ppb × 52,560 / 1e9
```

| rate (ppb/block) | ≈ APR |
| --- | --- |
| 500 | 2.6% |
| 1,000 | 5.26% |
| 2,000 | 10.5% |

This is the sanity check for whether a clearing rate is rich or cheap relative
to other sat-denominated yields. Worth drilling until it's automatic — anyone
evaluating Pool fluency expects you to move between ppb/block and annualized
yield without thinking.

### 7. What the quoted rate doesn't capture

The clearing rate is the headline, but effective economics on both sides are
dominated by **fixed costs**:

**Chain fees.** Each batch participant pays for its own footprint in the batch
transaction — account input, recreated account output, and share of channel
funding outputs. This cost is independent of lease size, so it sets a
**breakeven floor that scales inversely with `amount × duration`**. Worked
example: an asker leasing 5M sats for 2016 blocks who eats ~10,000 sats of
on-chain costs (batch share now, channel close later) needs

```
10,000 / (5,000,000 × 2,016) × 1e9 ≈ 992 ppb
```

just to break even before any actual return. This is the single most important
structural fact about Pool pricing: **when mempool fees rise, clearing rates
must rise, and small/short leases get priced out entirely.** It's why
`max_batch_feerate` exists and why batches skip in fee spikes.

*(Rule of thumb for estimating: ~350 vbytes per participant — the real number
depends on account script type and how many channel outputs you're party to,
but the shape of the conclusion holds.)*

**Execution fee.** The auctioneer charges both sides a base fee plus a
proportional component on matched volume. The current schedule is queryable via
the Terms RPC (`pool auction fee` in the CLI). Small relative to premiums on
large leases; meaningful on small ones.

**Asker's offsetting income.** The asker's capital sits on their side of the
leased channel, so it can earn **routing fees** during the term:

```
effective asker yield = premium + routing revenue − chain costs − execution fee
```

…against capital locked for the duration. The lock is script-enforced — no
early exit — which is precisely why the premium is compensation for a committed
term.

**Bidder's alternative cost.** The bidder is buying inbound liquidity for a
fixed term, so the rational comparison set is: Loop Out cost to manufacture
inbound, LSP channel-open fees, or simply waiting. A bidder should bid their
**true reservation rate** — uniform-price clearing means you pay the market
rate, not your quote, so shading a bid only risks non-execution.

### 8. Term structure, tiers, and rollover risk

- **Yield curve.** Each duration bucket (2016 / 4032 / 6048 / 8064 blocks) is a
  separate market, so Pool has a genuine term structure — longer commitments
  typically demand higher per-block rates to compensate for locked optionality,
  though thin liquidity in longer buckets makes the curve noisy.
- **Quality tiers.** Bids can specify a minimum node tier (Bos-score derived),
  so quality-constrained demand can clear at different rates than tier-agnostic
  demand.
- **Rollover risk.** Leases don't renew: at expiry the channel persists but
  enforcement lapses. A bidder with ongoing needs faces rollover at the *next*
  clearing rate — the same duration-mismatch dynamic seen in stablecoin
  liquidity provisioning.

### 9. Takeaways for the AI Liquidity Advisor

1. **Pricing is two-dimensional.** A naive advisor recommends a rate; a good
   one recommends a **(rate, max_batch_feerate) pair**, because the fee cap
   determines whether you execute at all, and the optimal rate depends on the
   fee environment you're willing to clear in. Since batches skip when fees
   exceed tolerance, fee-aware order management is a genuinely underserved
   problem — a credible contribution wedge.
2. **Size and duration are levers against the floor.** Breakeven scales as
   `1/(amount × duration)`, so the same fee environment that kills a 1M-sat
   2016-block lease barely dents a 50M-sat 8064-block one. Pool structurally
   favors larger, longer commitments in high-fee regimes — an advisor that
   consolidates small orders into fewer larger ones adds real economic value.
3. **Mechanism-design fluency is the pitch.** Being able to articulate *why*
   uniform-price sealed-bid was the right format for this market — thin
   liquidity, repeat participants, front-running risk — is what distinguishes a
   contributor pitch. Turning liquidity provisioning into a priced, term-structured market rather than ad-hoc
   capital allocation.

---

## Where the mechanics live in code

The auctioneer is closed-source, so the auction logic that *can* be read is the
client-side validation — effectively the auction's public contract:

| Where | What |
| --- | --- |
| `pool/order/batch.go` + the `BatchVerifier` | Exactly what `poold` checks before signing a batch — the fastest way to internalize what a valid batch even means |
| `account` package | Account state machine transitions |
| `poolrpc` | The trader ↔ auctioneer protocol |

---

## Key terms

| Term | Meaning |
| --- | --- |
| **LCL** | Lightning Channel Lease — packaged inbound liquidity with a fixed, script-enforced duration |
| **ppb rate** | Price per block in parts-per-billion of the leased amount — Pool's pricing primitive |
| **Premium** | `amount × rate_ppb × duration / 1e9`, prepaid in full inside the batch transaction |
| **Duration bucket** | A distinct market per lease length (2016 / 4032 / 6048 / 8064 blocks); buckets never cross-match |
| **Sealed-bid book** | Private order book — no resting orders visible, killing front-running |
| **Uniform clearing price** | Everyone in a bucket clears at one market rate, not their quoted price |
| **`max_batch_feerate`** | Per-order cap on tolerable on-chain fees; batches skip participants when exceeded |
| **Batch transaction** | Single tx spending matched accounts, funding channels, recreating accounts, paying the execution fee |
| **Batch key** | Incrementing key (tweaked by G per batch) making account outputs deterministically re-derivable |
| **Script-enforced lease** | Maker's commitment output carries a CLTV to lease expiry — no early exit for the capital provider |
| **Sidecar channel** | A lease bought on Pool but delivered to a third party's node |
| **Node tier** | Bos-score-derived quality tier (T0/T1) a bid can require of makers |
| **Execution fee** | Auctioneer's base + proportional fee, queryable via the Terms RPC |
| **Rollover risk** | Leases don't renew — ongoing inbound needs re-price at the next clearing rate |

---

## Further reading

- Lightning Pool — <https://lightning.engineering/pool>
- Pool client source — <https://github.com/lightninglabs/pool>
- Lightning Pool whitepaper (lightning.engineering)

---

_Part of [Learn Lightning Labs Pools](./README.md). Previous:
[03 — Lightning Liquidity](./03-lightning-liquidity.md). Next:
[05 — Pool: Observations from Running It Live](./05-pool-observations.md)._
