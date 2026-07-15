# 03 — Lightning Liquidity

Notes on liquidity from the **merchant's** point of view: how to reliably and cheaply
*receive* Lightning payments, and the markets and services that make that possible
(Pool, Loop, LSPs). This builds on the channel mechanics in
[Lightning Fundamentals](./02-lightning-fundamentals.md) and the UTXO model in
[Bitcoin Fundamentals](./01-bitcoin-fundamentals.md) — where the underlying mechanics
overlap, this note points back to **Lightning Fundamentals** rather than repeating them.

> Source material: Lightning Labs / lightning.engineering documentation, plus my own
> study notes.

---

## 1. The merchant's liquidity problem

Payments on the Lightning Network settle instantly, at low cost for the sender and
typically **no cost for the receiver**. Anyone can run a node at home or in the cloud,
and tooling like **BTCPay** and **LNbits** makes it easy to wire a node into other
systems. But once you run your own node to receive payments, the obligation to **manage
channel liquidity** falls on you. This is novel for system administrators — it goes
beyond "keeping the lights on" and updating software.

Why it matters so much for a merchant:

- **Reliability is invisible when it fails.** Due to the network's architecture, you may
  not even know a customer couldn't pay you unless they file a report. A *failed* payment
  is not easily distinguishable from a payment that was *never attempted*. Customers who
  can't pay may simply look elsewhere or pick a payment method that's worse for you.
- **Cheapness signals value.** Excessive routing costs make your service look less
  valuable to the consumer. In practice, **payment failures and high fees go hand in
  hand** for mismanaged nodes.

The goal: have sufficient liquidity, in the right place, at the right time — and
automate as much of that as possible.

---

## 2. Inbound liquidity, precisely

The network routes funds through **payment channels**. A channel's **capacity is fixed
at creation** — a 10 BTC channel can facilitate at most a 10 BTC transfer, forever.
(Mechanically a channel is a single L1 UTXO in a 2-of-2 multisig; see
**Lightning Fundamentals**.)

**Inbound liquidity** is the portion of that fixed capacity currently held by your
**peer** that can be forwarded to *your* side as part of a payment.

### How liquidity shifts as you receive

![Lightning channel liquidity: a fixed channel capacity splitting into inbound and outbound, shifting as Alice receives payments](./diagrams/lightning-liquidity.svg)

Bob opens a channel to Alice for 10,000,000 satoshis (0.1 BTC). The total capacity is
**0.1 BTC forever** (though Bob could open another channel later).

- When the channel goes active, Alice has 10,000,000 sats of **inbound liquidity** — she
  can receive up to 10M sats before the channel is depleted. That can be one payment or
  a million payments of 10 sats each.
- As payments arrive, funds drift to Alice's side. **Eventually all funds sit on Alice's
  side and she can no longer receive** through this channel until she pushes some back
  out.

---

## 3. Capacity deep dive: total vs. inbound vs. outbound

Alice has 1,000,000 sats and opens a channel to Bob for her entire balance:

```bash
lncli openchannel 021c97a90a411ff2b10dc2a8e32de2f29d2fa49d41bfbb52bd416e460db0747d0d 1000000
```

- Total capacity: **1,000,000 sats**, all Alice's for now.
- Alice's **outbound** (sending) capacity: 1,000,000 sats — she can send it in one
  transaction or up to a million.
- Alice's **inbound** (receiving) capacity: **zero** — receiving even one sat would push
  her balance over the total capacity. Bob's inbound capacity is the full 1,000,000.

Alice now pays Bob 300,000 sats. Her balance is 700,000 sats; total capacity is
unchanged.

- Alice's inbound capacity is now **300,000 sats** (she can receive that much before the
  1M cap is exhausted).
- Bob's inbound capacity has shrunk to 700,000; his outbound capacity has grown to
  300,000.

Only inbound/outbound shift — **total capacity never changes without a new on-chain
transaction**. The two identities to remember:

```
Alice's inbound capacity  = Bob's outbound capacity
Alice's outbound capacity = Bob's inbound capacity
```

**The channel reserve.** A small amount of capacity is reserved to cover the fees of a
future close. So you can't fully empty a channel with a Lightning payment, and the
spendable balance may appear slightly smaller than the total capacity.

---

## 4. Acquiring inbound capacity

To receive payments, a merchant must acquire inbound capacity. Four routes:

**1. Spend satoshis (the simplest).** Every satoshi you spend out of a channel becomes a
satoshi of inbound capacity, until the channel is empty and its entire capacity is
inbound. Pay for things, pay suppliers/staff, or sell at a Lightning-deposit exchange.
A single channel may be all a small operator needs — but your balance can never exceed
the channel's capacity, so to receive *more* you must add inbound capacity.

**2. Ask your clients.** As a new merchant, your most enthusiastic Lightning customers
may be willing and able to open inbound channels to you.

**3. Loop Out (submarine swaps).** [Loop Out](https://lightning.engineering/loop) lets
you grow inbound capacity beyond your starting capital.
[Lightning Loop](https://lightning.engineering/loop) is a marketplace for **submarine
swaps** — two transactions made conditional on each other so they either both execute or
neither does, enabling a trustless swap without external contract enforcement. To acquire
inbound capacity you swap an **outbound Lightning** payment for an **inbound on-chain**
transaction:

- Your channel balance limits the swap size; because each channel keeps a small reserve,
  you'd typically swap ~80–90% of the balance.
- After the Lightning payment to Loop, you receive your balance back as an on-chain UTXO
  (minus fees), which you use to open a **second** channel with a different node.
- Net result: roughly the same capital (minus fees), but ~80–90% more **total** capacity
  across two channels, with ~40–45% of total capacity now available for **receiving**.

This can be automated with **Autoloop** in the Lightning Terminal UI.

**4. Buy a channel on Lightning Pool.**
[Lightning Pool](https://lightning.engineering/pool) is a marketplace where you signal a
need for inbound liquidity and pay well-capitalized, well-connected nodes to open
channels to you using *their* capital. Those nodes are compensated and commit to keeping
the channel open for a specified term. Pool is the fastest way to acquire **large**
amounts of inbound liquidity — the option of choice for operators who must seamlessly
receive from many users making deposits or payments.

---

## 5. Acquiring outbound capacity

Starting from a node with zero channels, you need a UTXO to open a channel with a good
peer — ideally one with strong uptime, good connectivity, and ample capital.

```bash
# Basic open
lncli openchannel [node key] local-amt

# Optionally target a confirmation speed, or set the fee manually
lncli openchannel [node key] local-amt --conf_target 6
lncli openchannel [node key] local-amt --sat_per_byte 12

# Keep a payments-only node off the routing graph
lncli openchannel [node key] local-amt --private
```

Use `--conf_target` (how quickly you want it confirmed) or `--sat_per_byte` (manual
fee). Use `--private` if your node is purely for making and receiving payments and you'd
rather not route others' traffic. A channel typically needs **three confirmations** on
the blockchain before it becomes active. After that, you have outbound capacity to spend
across the network.

---

## 6. Maintaining inbound liquidity

Repeatedly soliciting or buying new channels is costly — peers expect compensation for
their capital and for the open/close fees. The cheaper path is to **reuse** channels so
the opening cost amortizes over a longer period, which means **emptying** channels by
pushing funds back out. Three ways to push out:

1. **Make Lightning payments.** Ideally a merchant pays suppliers or staff the same way
   it gets paid. When channels are used in **both** directions, fees tend to be lowest.
2. **Swap off-chain for on-chain via Loop / Autoloop.** Preferable when funds are to be
   held in Bitcoin (a reserve or savings position). Automate with **Autoloop** in
   Lightning Terminal.
3. **Cash out via a Lightning-deposit platform.** If funds must become fiat, do it
   through platforms that accept Lightning deposits — this minimizes on-chain fees and
   keeps channels open longer.

---

## 7. Identifying good peers

Good peers route payments to you **reliably and cheaply**. Rules of thumb:

- **Whatever channels deplete the quickest are your best peers** — they're the ones
  actually bringing you payments.
- **Watch the cost of pushing out.** If a peer reliably routes payments *to* you but
  charges significantly more than others to push payments *out*, you may deprioritize
  maintenance on that channel.

---

## 8. Lightning Service Providers (LSPs)

A **Lightning Service Provider** is an entity that provides liquidity services on behalf
of others. Channels are naturally constrained by size, and further limited by local and
remote balances, so LSPs typically help in one of two ways:

- **Swapping** on-chain funds for off-chain funds (or vice versa).
- **Opening channels** to increase a user's inbound capacity or improve their position
  in the network graph.

Ideally an LSP operates **non-custodially**. Swaps can be built as **submarine swaps** so
the provider can never abscond with funds. When opening channels, the LSP keeps custody
of *its* side and earns routing fees as it forwards payments.

The most common use today: providing inbound channels to users of **non-custodial
wallets**, so a user can immediately receive Lightning payments without active channel
management or owning a UTXO. The LSP charges an upfront fee to cover mining fees and
capital cost — often **deducted directly from an incoming payment**, though it can be
charged upfront. Sizing a new channel for a fresh user is a hard judgment call. An LSP
may deploy its own funds, borrow bitcoin, or **buy channels on the open market** (e.g.
Lightning Pool, via **sidecar channels**) rather than opening them itself.

---

## 9. Channel fees & summary

**The three fee types to keep straight:**

- **On-chain open fee** — the initiator pays Bitcoin miners to confirm the funding
  transaction.
- **Lightning routing fee** — paid to the routing nodes that forward a payment to its
  destination.
- **The close reserve** — a small slice of capacity withheld to cover the eventual close,
  which is why a channel can't be emptied completely.

**Maintenance summary:**

- Avoid closing channels unless a peer is offline or funds can't be pushed out over
  Lightning.
- Identify your good peers and regularly push payments out through their channels — by
  making Lightning payments yourself, or by swapping into your on-chain wallet with Loop
  or Autoloop.

---

## Key terms

| Term | Meaning |
| --- | --- |
| **Capacity** | The fixed total value of a channel, set at creation and unchanging without an on-chain tx |
| **Inbound capacity** | Portion of capacity held by your peer that can flow to you — your receiving headroom |
| **Outbound capacity** | Your own balance in a channel — your sending/forwarding headroom |
| **Channel reserve** | Capacity withheld to fund a future close; prevents fully emptying a channel |
| **Submarine swap** | Two interdependent txs that execute together or not at all — a trustless swap |
| **Loop Out** | Swap an outbound Lightning payment for an inbound on-chain UTXO to gain inbound capacity |
| **Autoloop** | Automated Loop, configured in Lightning Terminal |
| **Lightning Pool** | Marketplace to buy/lease channels (inbound liquidity) from capitalized nodes |
| **Sidecar channel** | A channel bought on Pool and directed to a third party |
| **LSP** | Lightning Service Provider — provides liquidity (swaps, channels) on behalf of users |
| **Dust limit** | Minimum relay size for a UTXO |

---

## Further reading

- Lightning Loop — <https://lightning.engineering/loop>
- Lightning Pool — <https://lightning.engineering/pool>
- Loop / Autoloop and Lightning Terminal docs (lightning.engineering)

---

_Part of [Learn Lightning Labs Pools](./README.md). Previous:
[02 — Lightning Fundamentals](./02-lightning-fundamentals.md). Next:
[04 — Pool: Auctions & Lease Pricing](./04-pool-auctions-lease-pricing.md)._
