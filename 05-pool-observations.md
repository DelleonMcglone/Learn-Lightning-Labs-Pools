# 05 — Pool: Observations from Running It Live

Field notes from a hands-on session driving `poold` against Pool's public test
auctioneer — an account opened, an order placed, the live book read. This is the
**synthesis layer**: what running the real thing confirmed, contradicted, or
added on top of the theory. It sits between the two other Pool docs and links
back to both:

- Theory & mechanism design → [04 — Pool: Auctions & Lease Pricing](./04-pool-auctions-lease-pricing.md)
- Exact commands, output, and gotchas → [Pool setup & first operations](./setup/pool.md)

> Environment: testnet3, `poold` v0.7.1-beta → `test.pool.lightning.finance:12010`,
> neutrino-backed LND. Session date 2026-07-08. Every figure below is from real
> RPC output, not documentation.

---

## 1. The headline observations

1. **The public auction is fully readable with zero funds.** Fee schedule, open
   duration buckets, per-market depth, next-batch parameters, and historical
   batch snapshots all come back before you own an account or a satoshi. For an
   advisor tool this matters: the entire *market-state* input is free and
   queryable — only *acting* on it costs money.

2. **Pricing really is one number — and the APR math is the readable form.** A
   resting order carries `rate_fixed` in ppb/block and nothing more; the sats
   premium is derived. My bid at 1.5%-over-interval came back as **7,440 ppb**,
   which is only legible once you run it through `ppb × 52,560 / 1e9 ≈ 39.1% APR`
   (see [note 04 §6](./04-pool-auctions-lease-pricing.md#6-the-apr-conversion)).
   The conversion isn't academic — it's the only way to eyeball whether an order
   is sane.

3. **Uniform-price clearing is visible in the historical data.** The last public
   batch matched an ask quoted at 3,306 ppb against a bid quoted at 6,613 ppb and
   cleared *everyone* at **6,613** — the asker earned exactly double their quote.
   That's the sealed-bid uniform-price property from theory, sitting right there
   in `auction snapshot` output as a concrete number.

4. **The token wall is the real cost of entry.** The single largest time sink was
   not chain confirmation or funding — it was the **L402 payment** gating every
   auctioneer RPC (details below). Worth internalizing: on Pool, "connecting to
   the market" is itself a Lightning payment, and when that payment path breaks,
   *nothing* works, not even reads that require auth.

---

## 2. What matched the theory

| Prediction (note 04) | Observed |
| --- | --- |
| Each duration is its own market | 5 open buckets (2016/4032/6048/12096/52416), each with independent depth; orders never cross buckets |
| The 2016-block lease is the liquid instrument | By far the deepest book: 30 asks / 47 bids vs. low single digits elsewhere. All other buckets are effectively one-sided |
| Premium is prepaid, debited from the account | Placing the bid immediately reserved **7,725 sats** from my account (`reserved_value_sat`) — premium + chain-fee headroom, locked before any match |
| Account is an on-chain 2-of-2 with a timeout path | Account opened as a **taproot v2** output (`ACCOUNT_VERSION_TAPROOT_V2`) with an absolute `expiration_height` for unilateral recovery |
| Order sizing is quantized to 100k-sat units | Minimum account and minimum order both 1 unit; my 100k bid = exactly `units: 1` |
| `max_batch_feerate` is a first-class order field | `--max_batch_fee_rate 50` (sat/vB) stored as `12,500 sat/kw` on the order — the fee cap travels *with* the order, exactly as the mechanism requires |

The theory held up cleanly. The one translation to remember: the CLI takes fee
rate in **sat/vB**, the order stores it in **sat/kw** (×250).

---

## 3. What surprised me (not in the theory)

- **Testnet is a graveyard with a heartbeat.** The market has live batch *ticks*
  and hundreds of resting orders across all buckets, but the most recent batch
  that actually *cleared* is from **May 2023**. Orders rest indefinitely; nothing
  crosses. So "there is depth" and "you will get filled" are completely
  independent facts. An advisor that reads depth as liquidity would be badly
  wrong here.

- **Ghost markets in the depth data.** `getinfo` reports resting orders in
  **144-** and **1440-block** buckets that `leasedurations` does *not* list as
  open. The book remembers markets the auctioneer no longer offers — a reminder
  that `market_info` is descriptive telemetry, not the authoritative list of
  what you can trade.

- **Account activation doesn't wait out the conf target.** I set
  `--conf_target 6`, but the account flipped `PENDING_OPEN → OPEN` almost as soon
  as the funding tx got its first confirmation. The conf target governs the
  funding *fee estimate*, not a hard activation gate.

- **Neutrino hides your own incoming money.** The light-client backend didn't
  surface the faucet deposit while it sat unconfirmed in the mempool — the
  balance simply appeared after the block. With a light backend, the mempool is
  effectively invisible; confirmation is the only signal.

---

## 4. The L402 debugging story (the transferable skill)

The most instructive part wasn't Pool-specific — it was cross-repo debugging.

**Symptom:** every account/order call died with `payment isn't initiated` or
`payment timed out`, and LND's `listpayments` was empty — the payment never
even started.

**Trace:** the auctioneer gates RPCs behind an L402 token (a macaroon unlocked by
paying a 1,000-sat invoice; `ReserveAccount` returns `payment required` until
paid). Following the call path —

```
poold → aperture/l402/client_interceptor.go → lndclient.PayInvoice → SendPaymentSync (legacy)
```

— the payment used LND's **legacy `SendPaymentSync`** RPC, which on this
0.21-beta build never initiated. Aperture then timed out and left a poisoned
`l402.token.pending` that broke every retry.

**Fix:** pay the invoice out-of-band with the **modern router**
(`payinvoice` → `SendPaymentV2`, which routed and settled in ~1s), then
hand-serialize a *completed* token in aperture's binary format
(`macLen ‖ macaroon ‖ payment_hash ‖ preimage ‖ amt ‖ fee ‖ created`, big-endian)
with `sha256(preimage) == payment_hash` verified. `poold` picked it up and every
RPC worked.

**Why it matters for the contributor path:** the actionable finding is a concrete
one — *aperture's L402 client still depends on the deprecated `SendPaymentSync`
path*. That's exactly the shape of a real issue/PR: reproducible, traced to a
specific RPC across three repos (`pool` → `aperture` → `lndclient`), with a clear
remedy (migrate to `SendPaymentV2`). Full reproduction in
[setup/pool.md §3](./setup/pool.md).

---

## 5. Implications for the AI Liquidity Advisor

Running Pool sharpened three design points from
[note 04 §9](./04-pool-auctions-lease-pricing.md#9-takeaways-for-the-ai-liquidity-advisor):

1. **Depth ≠ liquidity — model fill probability separately.** The dormant
   testnet proves resting-order depth can be totally decoupled from clearing. An
   advisor must estimate *probability of match* (from batch-snapshot history and
   the crossing spread), not just report the book.

2. **The market-read is free; budget only for actions.** Since all market state
   is free to poll, an advisor can run a rich always-on read loop and spend sats
   only when it recommends *placing* an order. The cost model is asymmetric in
   the tool's favor.

3. **The fee cap is inseparable from the rate.** `max_batch_feerate` living on
   the order — checked against the auctioneer's announced next-batch fee rate —
   confirms the (rate, fee-cap) *pair* is the real decision variable. Recommending
   a rate without a matched fee cap is recommending an order that may never be
   eligible to clear.

---

## Key numbers from this session

| Thing | Value |
| --- | --- |
| Auctioneer | `test.pool.lightning.finance:12010` (Pool v0.7.1-beta) |
| Execution fee | 1 sat base + 1,000 ppm per side |
| L402 token | 1,000 sats (routed via Olympus by ZEUS → aranguren.org) |
| Account | 150,000 sats, taproot v2, `expiration_height` 5,071,406 |
| Account funding tx | [`7960f595…7a2822`](https://mempool.space/testnet/tx/7960f5952a18a6d3609f62004b41623a24fc21bfd1e0bc7fdb680a4d7b7a2822) @ block 5,058,448 |
| My bid | 100k sats · 7,440 ppb · 2016 blocks · ≈ 39.1% APR · min tier T1 |
| Last cleared batch | 6,613 ppb (≈ 34.8% APR), 1.5M sats — **May 2023** |

---

_Part of [Lightning Labs Prep](./README.md). Previous:
[04 — Pool: Auctions & Lease Pricing](./04-pool-auctions-lease-pricing.md)._
