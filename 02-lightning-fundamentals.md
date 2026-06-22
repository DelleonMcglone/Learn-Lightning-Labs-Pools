# 02 — Lightning Fundamentals

Notes on the Layer 2 (Lightning Network) mechanics that sit on top of the Bitcoin base
layer from [note 01](./01-bitcoin-fundamentals.md): channels, commitment transactions,
local/remote balance, node profiles, HTLCs, force closes, and the day-to-day game of
liquidity management.

The core paradigm shift: on **Layer 1**, liquidity is a function of global block space
and time — every payment competes in the ~10-minute mempool auction. On **Layer 2**,
liquidity is a dynamic, localized game of physics and capital allocation. The same
satoshis in your node can simultaneously provide liquidity to you *and* consume the
liquidity of others.

> Source material: my own study notes plus a Lightning node-management walkthrough
> (see [Sources](#sources)).

---

## 1. Channels are dynamic UTXOs

Mechanically, a **Lightning channel is nothing more than a single Layer 1 UTXO locked
in a 2-of-2 multisignature script** between you and your peer. Everything else happens
off-chain.

When you open a channel with **1,000,000 satoshis** (0.01 BTC):

- That single UTXO is broadcast and confirmed on the blockchain (a normal L1
  transaction, with normal confirmation times).
- Off-chain, you and your peer each hold an updated **commitment transaction**, managed
  by your node's wallet — a fully-signed, broadcastable snapshot of the current balance.
- If all 1,000,000 sats sit on your side, you have **outbound liquidity** (spending
  capacity) and your peer has **zero inbound liquidity** (receiving capacity).
- When you route a payment or buy a coffee, you do **not** broadcast a new on-chain
  transaction. You and your peer simply sign a **new local commitment transaction** that
  shifts the balance inside that exact same UTXO — e.g. 800,000 sats for you, 200,000
  for them.

This is why Lightning liquidity is **zero-sum per channel**: you cannot receive money
if the balance is entirely on your side, because there is no "room" for it to slide
toward your peer. Every satoshi of outbound capacity you gain is a satoshi of inbound
capacity you lose, and vice versa.

---

## 2. Local vs. remote balance

The two distinct capital pools a node operator manages:

- **On-chain UTXOs** — predictable and secure, but limited by block confirmation times
  (~10–60 minutes depending on fee rate and confirmation target).
- **Off-chain channels** — 2-of-2 multisig contracts holding Bitcoin on-chain while
  enabling instant, near-zero-fee routing. The catch: off-chain liquidity needs
  constant active balance management to stay performant.

Your core routing metric is the split between **local** and **remote** balance:

| Term | Also called | Meaning |
| --- | --- | --- |
| **Local balance** | Outbound capacity | Your own deployed capital — the max you can send or forward outward through that channel |
| **Remote balance** | Inbound capacity | Your peer's capital — the max you can receive or accept as incoming routed traffic |

When routing a payment, your node sits in the middle. You **receive** capital on an
incoming channel (turning remote balance there into local balance) and immediately
**forward** it out an outgoing channel (turning local balance there into remote
balance). Routing is, mechanically, just shuffling which side of each UTXO the satoshis
rest on.

---

## 3. Node profiles

Different nodes optimize for different balance distributions. Each profile maps to a
distinct UTXO and network-management strategy.

### A. Spenders (outbound-heavy)

- **Goal:** push balance out.
- **Wallet state:** funds start on your side. As you spend, outbound capacity
  decreases and inbound capacity increases.
- **Rebalancing:** when channels run "empty" of spending power, use **Lightning Loop
  Out** — your node sends Lightning sats to a service provider off-chain, and they send
  an equal amount of L1 BTC back to your on-chain address, clearing the channel so you
  can spend through it again.

### B. Receivers / merchants (inbound-heavy)

- **Goal:** pull balance in.
- **Wallet state:** the balance must start on your **peer's** side. If a channel opens
  with all the money on your side, you cannot receive a dime.
- **Capital-inefficiency challenge:** inbound liquidity is expensive because it
  requires someone else to lock up their capital (their UTXOs) to face you. This is why
  markets like **Lightning Pool** exist — a merchant pays an upfront fee to **lease** a
  UTXO commitment from an operator for a set period (e.g. 2,016 blocks).

### C. Routing nodes (the optimization puzzle)

- **Goal:** keep the balance roughly **centered** in each channel.
- **Technical challenge:** a channel skewed entirely to one side (100% inbound or 100%
  outbound) becomes a **dead end** — it can no longer route in both directions.
- **The fee game:** routing nodes run automated scripts to adjust fees dynamically. If
  a channel is rapidly losing outbound capacity because everyone routes through it, the
  node **raises** fees there to slow the drain — or **lowers** fees on the opposite
  channel to incentivize the market to push funds back.

> If you're designing a node deployment, the first question is which profile your system
> aligns with — Spender, Receiver, or Router. The underlying coin-selection and
> channel-creation logic changes drastically depending on the answer.

---

## 4. HTLCs, private channels & force close

### Private channels and trapped capital

Private channels are typically used by mobile consumer wallets. For a routing-node
operator, they carry a risk: if a mobile peer opens a channel with you and then goes
offline, **your satoshis can become highly illiquid inside that private channel**. If a
payment gets stuck in flight due to an unresolved **HTLC (Hashed Timelock Contract)**,
your node cannot access those satoshis. The only options are to **force-close** the
channel or wait for the peer to come back online.

### What a force close costs

To recover funds unilaterally, your wallet broadcasts the latest signed commitment
transaction directly to Layer 1. To prevent cheating — i.e. to stop you from
broadcasting an old, more-favorable balance state — the protocol enforces a **relative
timelock** (`OP_CHECKSEQUENCEVERIFY`) on the party that initiates the close:

- Your funds are mathematically locked on-chain for a set number of blocks (e.g. 144
  blocks ≈ 24 hours, up to roughly two weeks).
- During that window your capital is completely illiquid — it earns no fees, can't be
  spent on-chain, and can't route payments.

This is exactly why managing L2 liquidity means weighing **peer reliability** alongside
fee yield. A high-fee channel to an unreliable peer can cost you far more in locked
capital than it earns.

---

## 5. Liquidity management strategy

### Capital allocation & channel strategy

When capitalizing a routing node (say, deploying a starting balance of 10,000,000
satoshis), your opening strategy determines your network centrality:

1. **Avoid micro-channels.** Don't allocate tiny amounts (e.g. 20,000 sats) to a
   channel — high on-chain mempool fees will erode that capital the moment you open or
   force-close it.
2. **Use a hybrid approach.** Spread capital across:
   - **Core high-girth channels** — large-capacity openings to top-tier, highly
     connected backbone nodes (e.g. top-20 nodes on terminal indices), acting as major
     liquidity-highway entries.
   - **Network/community channels** — mid-sized channels into active peer networks
     (like Plebnet) to capture localized, distributed routing flows.
3. **Validate centrality.** Use analytical tools (e.g. `small world lnd` simulations or
   network visualizers) to test whether opening to a specific node public key actually
   improves your overall network centrality.

### Two schools: fee management vs. rebalancing

**1. Dynamic fee management (circuit breakers).** Rather than spending sats to force
channels back to a clean 50/50, adjust outgoing fee rates to track market velocity:

- **High outbound flow:** if a channel is draining fast, **raise** the fee policy
  (`base_fee_msat` or `fee_per_mil`). This acts as a circuit breaker — slowing the drain
  while maximizing fee yield on a high-demand path.
- **Stagnant flow:** if liquidity is stuck entirely on your local side, **drop** the fee
  rate to incentivize routing actors to clear the channel out.

**2. Circular rebalancing.** Pay yourself through a looped path — spend local balance
from a high-capacity channel to route back into a channel with high remote balance:

- **The cost trap:** compute net margin strictly —
  `Net Sats = Routing Fees Earned − Rebalancing Fees − On-chain Gas`.
- **The loss risk:** because Lightning currently lacks pairwise *inbound* fee
  expressions, you can't perfectly predict which path a rebalanced channel will use to
  exit. If you buy expensive inbound liquidity and it later exits through a cheap,
  low-fee peer, you absorb a net capital loss.

---

## 6. `lncli` command reference

Practical commands for deployment, fee adjustment, and channel closure. Addresses and
public keys below are placeholders — substitute your own.

### Wallet & on-chain management

```bash
# Generate a new native SegWit taproot (p2tr) address for wallet funding
lncli newaddress p2tr

# Check your internal on-chain wallet balance parameters
lncli walletbalance

# Sweep an exact satoshi amount to cold storage using a targeted fee rate
lncli sendcoins --sat_per_vbyte 12 --label "ColdStorageColdVault" bc1qaxcxcpunn6ns3gpu6ywcy57tcmy2vsjzwdklxr 5000000

# Full wallet evacuation: sweep entire balance targeting a 6-block confirmation
lncli sendcoins --sweepall --conf_target 6 bc1qaxcxcpunn6ns3gpu6ywcy57tcmy2vsjzwdklxr
```

### Channel initialization & policy updates

```bash
# Open a 2,500,000 sat public routing channel to a verified infrastructure peer
lncli openchannel --sat_per_vbyte 10 --local-amt 2500000 021c97a90a411ff2b10dc2a8e32de2f29d2fa49d41bfbb52bd416e460db0747d0d

# Update channel routing policy globally to enforce a higher circuit-breaker fee
lncli updatechanpolicy --base_fee_msat 1500 --fee_rate 0.000250 --time_lock_delta 40
```

### Analytical diagnostics

```bash
# Audit forwarded routing history to discover highly productive pairs
lncli fwdinghistory --start_time 1767225600 --end_time 1770000000

# Review per-channel metrics, uptime, and exact local/remote splits
lncli listchannels --active_only
```

### Channel closures

```bash
# Cooperative close specifying a dedicated out-of-wallet delivery address
lncli closechannel --funding_txid 83b5a55b21255915dbc0d005230b2c026a004c839edaa716247b96b66490c66a --output_index 1 --sat_per_vbyte 15 --delivery_addr bc1q6tcemsjadwgt938gkrmcqyvt79wxla42js8r4l

# Force (unilateral) close on an offline zombie channel — triggers the timelock wait
lncli closechannel --force 83b5a55b21255915dbc0d005230b2c026a004c839edaa716247b96b66490c66a 1
```

---

## Key terms

| Term | Meaning |
| --- | --- |
| **Channel** | A single L1 UTXO locked in a 2-of-2 multisig between two peers |
| **Commitment transaction** | The off-chain, fully-signed snapshot of a channel's current balance |
| **Local balance / outbound capacity** | Your capital — what you can send or forward out |
| **Remote balance / inbound capacity** | Peer's capital — what you can receive |
| **HTLC** | Hashed Timelock Contract — conditional, time-bound payment used in routing |
| **Cooperative close** | Both peers agree to settle the channel on-chain |
| **Force close** | Unilateral close by broadcasting the latest commitment tx |
| **Relative timelock** | `OP_CHECKSEQUENCEVERIFY` delay enforced on a force-closer to prevent cheating |
| **Loop Out** | Swap Lightning sats for on-chain BTC to clear outbound capacity |
| **Pool** | Marketplace to lease inbound liquidity (a UTXO commitment) for a set term |
| **Circular rebalancing** | Paying yourself through a loop to shift balance between channels |
| **Circuit breaker** | Raising a channel's fee to slow a rapid liquidity drain |

---

## Sources

- My own study notes on Lightning liquidity mechanics.
- *Lightning node management & liquidity optimization* walkthrough (YouTube):
  [overview](https://www.youtube.com/watch?v=LRZy-VtCPe4) — referenced timestamps
  include node routing (0:52), rebalancing cost (6:03), circular rebalancing (19:09),
  dynamic fees (23:46, 26:56), pairwise fee limits (29:24), centrality tooling (34:04),
  and capital allocation (57:14, 58:00).

---

_Part of [Lightning Labs Prep](./README.md). Previous:
[01 — Bitcoin Fundamentals](./01-bitcoin-fundamentals.md). Next:
[03 — Lightning Liquidity](./03-lightning-liquidity.md)._
