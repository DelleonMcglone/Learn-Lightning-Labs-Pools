# Lightning Operations (signet)

> **Purpose:** Log of first real Lightning operations on the signet node from
> [`lnd.md`](./lnd.md): funding the wallet, opening channels, and sending /
> receiving payments. Every txid, channel point, and preimage below is real and
> verifiable on [mempool.space/signet](https://mempool.space/signet).
>
> Part of the [Environment Setup](../README.md) section. **Deliverable:** an
> active Lightning channel — done ✅ (two of them).

---

## 0. Architecture for this exercise

A payment needs two endpoints you can observe. A brand-new channel has **all
liquidity on the funder's side**, so to demonstrate *receiving*, a second node
you control is the cleanest counterparty:

```
                       ┌──────────────────────┐
                       │  bitcoind (signet)   │
                       │  RPC 38332 · ZMQ     │
                       └──────┬───────┬───────┘
                              │       │
              ┌───────────────┘       └───────────────┐
   ┌──────────┴───────────┐            ┌──────────────┴────────┐
   │  lnd (node1)         │  channel B │  lnd (node2)          │
   │  gRPC :10009         │◀══════════▶│  gRPC :10011          │
   │  lightning-prep-     │  100k sat  │  lightning-prep-node2 │
   │  signet              │            │  (local counterparty) │
   └──────────┬───────────┘            └───────────────────────┘
              │ channel A · 100k sat
              ▼
   024e679c… (public signet node, 34.138.165.14)
```

Both LND instances share the one `bitcoind` — multiple ZMQ subscribers are fine.
Node2 runs from its own `--lnddir` with distinct ports (`rpclisten=10011`,
`restlisten=8082`, `listen=9737`).

---

## 1. Fund the wallet (signet faucets)

Generate a receive address, then hit a faucet:

```bash
lncli --network=signet newaddress p2wkh
```

Faucet findings (July 2026) — this took some trial and error:

| Faucet | Verdict |
| --- | --- |
| `signetfaucet.com` | Accepted requests but **silently dropped every payout** (queue message, nothing ever broadcast — verified via explorer). Has a captcha + 30s-wait rule. |
| `signet.bc-2.jp` | **Dead** — domain now serves an unrelated site. |
| `bitcoinsignetfaucet.com` | ✅ Works. No captcha (Cloudflare Turnstile, auto-passes). 1k–10k sats per request, ~5/day per IP. **Turnstile tokens are single-use: reload the page before every request.** |
| Alt Signet Faucet (`alt.signetfaucet.com`) | ✅ Works, generous. Sends a **random amount** (222k sats for us) via an RBF tx-chain, so the txid/amount may change in-flight before confirming. |

Result: **241,928 sats confirmed** across three payouts.

> Etiquette: faucets ask you to return coins when done. Alt faucet has a
> `recycle` page; keep some sats aside for closing time.

---

## 2. Connect to a peer

Signet's Lightning bootstrap DNS (`signet.nodes.lightning.wiki`) is **dead** —
`lnd` logs `unable to query bootstrapper … no such host` and sits at 0 peers.
Manual peering is required.

Finding a live peer was its own adventure: mempool.space's signet Lightning
directory **froze in Sep 2024**, so most listed nodes are gone or have rotated
keys (TCP connects, then `EOF` during the noise handshake = stale pubkey).
One listed node still worked:

```bash
lncli --network=signet connect \
  024e679c1a77143029b806f396f935fa6cd0744970f412667adfc75edbbab54d7a@34.138.165.14:9735
```

After connecting: `sync_type: ACTIVE_SYNC`, `synced_to_graph: true`.

---

## 3. Open channels

### Channel A — to the public network (100,000 sats)

```bash
lncli --network=signet openchannel \
  --node_key=024e679c1a77143029b806f396f935fa6cd0744970f412667adfc75edbbab54d7a \
  --local_amt=100000 --sat_per_vbyte=1
# funding_txid: 52eba3774a2331bca731eed4a28dbb8f780fe2cbbd5007f79f1d28f95d582b55
```

### Channel B — to node2, with a push (100,000 sats, 25,000 pushed)

`--push_amt` transfers part of the channel balance to the peer at open — node2
gets spendable Lightning balance **without ever touching the chain**. This is
what makes the receive demo possible immediately:

```bash
lncli --network=signet openchannel \
  --node_key=<node2 pubkey> \
  --local_amt=100000 --push_amt=25000 --sat_per_vbyte=1
# funding_txid: b815dc6027570bff4fc020c13f0422a7520f66a5866425d04663c86584cc1114
```

Both showed in `pendingchannels` until their funding txs got 3 confirmations
(~30 min on signet), then flipped to `listchannels` with `active: true`.

Observed detail: a 100k channel shows `local_balance: 96,530` — the ~3.5k
difference is the commitment-transaction fee reserve the funder carries.

---

## 4. Send a payment

Node2 creates an invoice; node1 pays it:

```bash
# node2 (note the extra flags — second lnd instance)
lncli --network=signet --rpcserver=localhost:10011 \
  --lnddir="$HOME/Library/Application Support/Lnd-signet2" \
  addinvoice --amt 5000 --memo "first Lightning payment: node1 -> node2"

# node1 pays
lncli --network=signet payinvoice --force <payment_request>
```

Result:

```
Payment status: SUCCEEDED
amount: 5000 sat   fee: 0 sat   time: 0.233s
preimage: b769b7489d8ebe3addbede08d3ebdbc85801a2d07c6a21536632a6031673fa39
```

Zero fee because it's a direct channel — no routing hops.

## 5. Receive a payment

Reverse direction — node1 invoices, node2 pays:

```bash
lncli --network=signet addinvoice --amt 2000 --memo "receive test: node2 -> node1"
# node2: payinvoice ... → SUCCEEDED, 0.221s
lncli --network=signet listinvoices   # → state: SETTLED, amt_paid: 2000 sat
```

### The ledger math (proof it's off-chain)

Channel B balances, before → after both payments:

| | local (node1) | remote (node2) |
| --- | --- | --- |
| after open | 71,530 | 25,000 |
| after send −5,000 / receive +2,000 | **68,530** | **28,000** |

No on-chain transactions moved — just HTLC updates to the channel state,
each settling in ~0.2 s.

---

## Watching it live

- **Dashboard** — `.preview/sync.html` served at `http://localhost:8000`
  (started via `.claude/launch.json` → `docs-static`; fed by
  `.preview/update_status.py`). Cards for bitcoind, node1 (pending/open
  channels, txns), node2. 2s refresh.
- **Raw log** — `tail -f ~/Library/Application\ Support/Lnd/logs/bitcoin/signet/lnd.log`:
  `FNDG` funding workflow, `CNCT` channel confirmations, `HSWC` HTLC settlement.
- **Public explorer** — funding txs visible at
  `https://mempool.space/signet/tx/<funding_txid>`.

---

## Gotchas worth remembering

| Gotcha | Detail |
| --- | --- |
| Signet LN bootstrap is dead | Manual `lncli connect` required; most directory listings are stale. |
| `EOF` on connect | Host alive, node key rotated — find a fresher pubkey. |
| Faucet queues lie | "Queued" ≠ broadcast. Verify via explorer, not the faucet UI. |
| `--push_amt` | Fastest way to give a test counterparty spendable balance. |
| Seed reuse | Reusing one cipher seed on two networks gives both nodes the **same identity pubkey** (same coin type ⇒ same derivation). Fine for tests, never for real funds. |
| Wallets don't auto-unlock | Every `lnd` restart needs `lncli unlock` (per instance). |

---

## Task checklist (sprint section: Lightning operations)

- [x] Fund wallet from faucets (241,928 sats)
- [x] Connect to a Lightning peer
- [x] Open a channel (two: public + local)
- [x] Send a Lightning payment (5,000 sats, 0.233s)
- [x] Receive a Lightning payment (2,000 sats, SETTLED)
- [x] Document operations (this file)

**Deliverable:** Active Lightning channel ✅

---

## Next

[`pool.md`](./pool.md) — Pool on **testnet** (signet is unsupported by Pool's
hosted auctioneer; a parallel neutrino-backed testnet LND was set up for it).
