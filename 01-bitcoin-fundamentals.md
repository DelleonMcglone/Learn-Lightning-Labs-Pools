# 01 — Bitcoin Fundamentals

Notes on the Layer 1 (base chain) concepts that matter for Lightning: architecture,
UTXOs, transactions, blocks, the P2P network, and wallets. The through-line is that
Bitcoin's base layer makes deliberate trade-offs — security and decentralization over
speed — and those trade-offs are exactly what make a Layer 2 like the Lightning
Network necessary. Each section closes with a short **⚡ Lightning connection**.

> Source material: Bitcoin Developer Documentation, plus my own study notes.

---

## 1. Introduction & the scalability problem

At its core, Bitcoin is a distributed, decentralized digital ledger. To stay
decentralized without trusting a third party, **every full node in the P2P network
must download, verify, and store every block and transaction**. That requirement is
the whole point — it's what lets anyone audit the chain independently — but it sets a
hard ceiling on throughput.

Because global consensus takes time, the base layer prioritizes **security and
decentralization over speed**. It cannot scale to millions of instant, everyday
payments (like buying coffee). This is the structural bottleneck that every Layer 2
solution is trying to route around.

> **⚡ Lightning connection:** Lightning doesn't try to make the base layer faster.
> It moves the vast majority of payments *off* the base layer entirely, settling to
> Layer 1 only when necessary.

---

## 2. Transactions & the UTXO model

Bitcoin does **not** use an account-balance model like a bank or Ethereum. It uses the
**UTXO (Unspent Transaction Output)** model.

- **What a UTXO is:** Think of UTXOs as paper cash and coins of varying denominations.
  When you receive bitcoin, it sits in your wallet as an unspent output.
- **How spending works:** To send bitcoin, a transaction must explicitly **consume
  existing UTXOs as inputs** (destroying them) and **create brand-new UTXOs as
  outputs** — the amount to the recipient, plus any change back to you.

Your "balance" is not a stored number. It's simply your wallet adding up the value of
every unspent output currently locked to your keys.

> **⚡ Lightning connection:** Publishing a UTXO movement to the entire world for a tiny
> payment is wasteful. Lightning locks a UTXO into a 2-of-2 multisig contract between
> two parties and lets them trade ownership of it back and forth off-chain, without
> broadcasting every micro-movement.

---

## 3. UTXO deep dive

### The cash analogy (the "O" in UTXO)

Picture UTXOs as physical bills and coins in a wallet. If you hold $50, you don't have
a single field that says "50" — you might have one $20, two $10s, and two $5s. Each
bill is an **unspent output** from some earlier transaction where someone paid you.

- To buy a $7 coffee, you don't slice a piece off the $20 bill.
- You hand over the $20 bill (**the input**).
- The cashier consumes it and hands back change (**the outputs**).

### A worked example

You hold a single UTXO worth **0.5 BTC** and want to send **0.1 BTC** to a friend:

- **Input:** your 0.5 BTC UTXO is consumed in full.
- **Output 1:** 0.1 BTC to your friend's address (a new UTXO for them).
- **Output 2:** 0.399 BTC back to your own change address (a new UTXO for you).
- **The missing 0.001 BTC:** not assigned to any output — it is automatically claimed
  by the miner as the transaction fee.

A UTXO is always spent *in its entirety*. The change output is how you get the
remainder back.

### UTXO model vs. account model

| Feature | UTXO model (Bitcoin) | Account model (Ethereum / banks) |
| --- | --- | --- |
| **Analogy** | Physical cash/coins in a wallet | A checking-account ledger |
| **Data tracked** | A global set of individual unspent chunks | One state table of accounts and balances |
| **Transaction structure** | Consumes old outputs, mints new ones | Subtracts from Account A, adds to Account B |
| **Privacy** | Higher — new address per change output makes tracking harder | Lower — all history tied to one address |
| **Efficiency / scale** | Parallelizable — different UTXOs can be spent at once | Sequential — strict nonce ordering prevents double-spends |

### Why it matters for developers

- **No double-spend race conditions:** A UTXO can be spent only once and only in full,
  so validation is simple — a node checks whether the input exists in the global UTXO
  set. Present → valid; already consumed → rejected instantly.
- **The dust limit:** Creating a UTXO writes data to the chain. A very tiny UTXO (e.g.
  0.000005 BTC) can cost more in fees to spend later than it is worth. These are
  "dust" and clutter the network.
- **Change management:** Good wallet architecture auto-generates fresh addresses for
  change. Handle it wrong and you either reduce privacy (change returns to the original
  address) or, worse, leak the change to miners as an oversized fee.

---

## 4. Coin selection algorithms

When a user sends X BTC, the wallet looks at its pool of UTXOs and decides **which ones
to combine** to reach the target. This is a variant of the classic **knapsack
problem**, balancing three competing goals:

1. **Minimize fees** — fewer/smaller inputs mean a smaller transaction (in bytes).
2. **Prevent dust** — avoid creating change too small to be worth spending later.
3. **Preserve privacy** — avoid merging UTXOs from unrelated sources, which signals to
   chain analysts that those addresses share an owner.

The main algorithms used by modern wallets (Bitcoin Core, Electrum):

**A. Branch and Bound (BnB)** — the gold standard for fee optimization (designed by
Murch / Mark Erhardt). It searches a binary tree for a combination matching the target
*plus* the exact fee, leaving **zero change**. Eliminating the change output shrinks
the transaction and avoids creating future dust. If no exact match is found within a
compute budget, the wallet falls back to another algorithm.

**B. Knapsack solver** — when no exact match exists, it finds a set slightly larger
than the target, aiming for a small but manageable change output. Built-in randomness
also helps privacy by hiding the wallet's selection logic from chain analysis.

**C. First In, First Out (FIFO) / oldest-first** — selects the oldest UTXOs until the
target is met. Keeps the global UTXO set healthy by cleaning out dormant outputs and
lets UTXOs accumulate "age."

**D. Stochastic Random Draw (SRD) / accumulate** — randomly selects UTXOs one at a time
until the target is reached. Computationally cheap; a baseline fallback when BnB fails.

---

## 5. Transaction anatomy

A transaction isn't a text log — it's a structured binary payload built from two arrays:
**inputs** and **outputs**.

### An input

An input carries **no amount**. It points backward to a previous output:

- **TXID** — the hash of the previous transaction.
- **VOUT (index)** — which output of that transaction is being spent (Output 0, 1, …).
- **ScriptSig / Witness** — the cryptographic proof (signature + public key) that
  satisfies the spending conditions on that previous output.

### An output

An output defines where value goes and the conditions to spend it next:

- **Value** — amount in satoshis (1 BTC = 100,000,000 sats).
- **ScriptPubKey (locking script)** — a short program in Bitcoin's **Script** language
  setting the spend rules, e.g. "provide a signature matching public key X."

### Why size in bytes matters

Miners charge by **data size (vBytes)**, not the monetary value sent.

- Sending 1,000 BTC with 1 input and 2 outputs → a tiny, cheap transaction.
- Sending 0.005 BTC by cobbling together 50 micro-UTXOs → a large, expensive one.

This is the direct reason coin selection matters: input count drives cost.

---

## 6. Blocks & the blockchain

Transactions are bundled into **blocks**, linked chronologically by cryptographic
hashes to form the **blockchain**.

- **Block size limit:** Restricted so ordinary users can still host a node — effectively
  ~2–4 MB max with SegWit.
- **Block time:** A new block is found roughly every **10 minutes**.

Together these impose a hard throughput limit of roughly **4–7 transactions per second
(TPS)**. When demand exceeds supply, a bidding war for block space erupts and fees
spike.

> **⚡ Lightning connection:** Blocks cap how many UTXOs can be modified per 10 minutes.
> Lightning sidesteps the cap by keeping balance updates off-chain — no block space is
> consumed until a channel closes.

---

## 7. P2P network

Bitcoin is a **peer-to-peer mesh** where nodes communicate via **gossip protocols**.
When you broadcast a transaction, it hops node to node across the globe and waits in
each node's local **mempool** until a miner includes it.

- Propagation takes time (latency).
- Because of latency plus the 10-minute target, transactions are **never instantly
  final**. A transaction is considered secure only once buried under a few blocks
  (confirmations) — typically **30–60 minutes**.

> **⚡ Lightning connection:** Retail commerce needs sub-second finality — you can't
> wait 10+ minutes at a register. Lightning bypasses gossip for individual payments,
> routing them instantly over direct, pre-established peer connections.

---

## 8. Wallets

A wallet does **not** hold coins. It's software that manages **cryptographic key
pairs**:

- **Private keys** — generate the digital signatures that unlock and spend UTXOs.
- **Public keys / addresses** — build the scripts that lock UTXOs so only you can spend
  them.

### HD wallets

Modern wallets are **Hierarchical Deterministic (HD)**, built on BIP-32, BIP-39, and
BIP-44:

1. **Seed phrase (BIP-39):** a 12- or 24-word recovery phrase converted into a master
   binary seed.
2. **Master private key (BIP-32):** from that one seed, the wallet derives a tree of
   millions of unique key pairs.
3. **Derivation paths (BIP-44):** standardized paths organize coins, accounts, and
   change addresses. A standard path: `m / 44' / 0' / 0' / 0 / 0`
   - `44'` — BIP purpose
   - `0'` — coin type (`0'` = Bitcoin mainnet, `1'` = testnet)
   - `0'` — account number (multiple sub-accounts in one wallet)
   - `0` — chain: `0` external/receiving, `1` internal/change
   - `0` — address index (increments from 0 upward)

### The indexer

The blockchain keeps no "address → balance" index, so wallet software scans the chain
itself: it records every address it could generate, finds matching transactions,
aggregates the unspent outputs into a local database, and computes spendable balance.

When you hit **Send**, the wallet passes that UTXO database to its coin-selection
algorithm, constructs the inputs/outputs, signs the payload with the private keys, and
broadcasts the raw bytes to the P2P network.

---

## 9. Why Lightning exists — putting it together

The base layer has three built-in physical constraints:

1. **UTXOs** require explicit, on-chain data tracking.
2. **Blocks** limit how many UTXOs can be modified per ~10 minutes.
3. **The P2P network** introduces propagation delay, so finality takes confirmations.

The Lightning Network answers all three with **payment channels**:

- Two users use their **wallets** to sign a base-layer **transaction** that locks a
  **UTXO** into a **2-of-2 multisig contract** on the **blockchain**.
- Once locked, they can re-allocate the balance between themselves **millions of times
  off-chain, instantly**. The P2P network never sees these updates, no block space is
  consumed, and no miner fees are paid.
- Only when they **close the channel** is a final transaction broadcast to settle the
  net balance back onto Layer 1.

That's the whole idea: do the slow, expensive, globally-verified work once to open a
channel, then transact freely off-chain, and settle once at the end.

---

## Key terms

| Term | Meaning |
| --- | --- |
| **UTXO** | Unspent Transaction Output — a discrete chunk of spendable bitcoin |
| **Input** | A UTXO being consumed, referenced by TXID + VOUT |
| **Output** | A newly created UTXO with a value and a locking script |
| **ScriptPubKey** | Locking script defining how an output may be spent |
| **ScriptSig / Witness** | Unlocking proof (signature + public key) |
| **Satoshi** | Smallest unit; 1 BTC = 100,000,000 sats |
| **Dust** | A UTXO too small to spend economically |
| **Mempool** | A node's local pool of unconfirmed transactions |
| **Confirmation** | A block mined on top of the one containing your transaction |
| **HD wallet** | Hierarchical Deterministic wallet (BIP-32/39/44) |
| **2-of-2 multisig** | Output requiring both parties' signatures — the basis of a Lightning channel |

---

_Part of [Learn Lightning Labs Pools](./README.md). Next: [02 — Lightning Fundamentals](./02-lightning-fundamentals.md)._
