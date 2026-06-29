# LND Setup (macOS · signet)

> **Purpose:** Reproducible notes for installing, verifying, configuring, and
> running **LND** (the Lightning Network Daemon) on macOS, wired to the signet
> Bitcoin Core node from [`bitcoin-core.md`](./bitcoin-core.md). End state: a
> running Lightning node, synced to chain, ready to open channels.
>
> Part of the [Environment Setup](../README.md) section. **Deliverable:** LND
> running and connected to Bitcoin Core — done ✅. This file logs the exact steps
> that produced it.

---

## Prerequisites

A **synced** Bitcoin Core signet node with `txindex`, `server`, ZMQ endpoints,
and hashed `rpcauth` credentials — all set up in
[`bitcoin-core.md`](./bitcoin-core.md). LND reads blocks/transactions from that
node over RPC + ZMQ; it does not talk to the P2P network for chain data.

The four values LND needs from Bitcoin Core (already configured there):

| Value | Where it comes from |
| --- | --- |
| RPC user `bitcoin` + plaintext password | the password behind the `rpcauth` hash in `bitcoin.conf` |
| RPC host `127.0.0.1:38332` | signet's default `bitcoind` RPC port |
| `zmqpubrawblock=tcp://127.0.0.1:28332` | `bitcoin.conf` `[signet]` |
| `zmqpubrawtx=tcp://127.0.0.1:28333` | `bitcoin.conf` `[signet]` |

---

## Versions (as built)

| Item | Value |
| --- | --- |
| LND | 0.21.0-beta (`lnd version 0.21.0-beta`) |
| OS | macOS, Apple Silicon `arm64` |
| Network | signet |
| Backend | `bitcoind` (Bitcoin Core 31.0) |
| Install path | `/opt/homebrew/bin` |
| Source | https://github.com/lightningnetwork/lnd/releases |

---

## 1. Download

From the [latest LND release](https://github.com/lightningnetwork/lnd/releases),
grab the macOS `arm64` build plus the signed manifest and at least one
maintainer signature:

```bash
V="v0.21.0-beta"
BASE="https://github.com/lightningnetwork/lnd/releases/download/$V"
curl -sSL -O "$BASE/lnd-darwin-arm64-$V.tar.gz"   # Intel: lnd-darwin-amd64-$V.tar.gz
curl -sSL -O "$BASE/manifest-$V.txt"              # sha256 of every release asset
curl -sSL -O "$BASE/manifest-roasbeef-$V.sig"     # lead maintainer's signature
curl -sSL -O "$BASE/manifest-yyforyongyu-$V.sig"  # a second maintainer's signature
```

> Unlike Bitcoin Core, LND ships **one signed manifest** (a list of SHA256
> hashes) with **detached signatures** from multiple maintainers, rather than a
> single combined `.asc`. Verify the manifest's signature, then check your
> tarball's hash against the manifest.

---

## 2. Verify (do not skip)

```bash
# a) Confirm the tarball's hash is listed in the signed manifest
H=$(shasum -a 256 lnd-darwin-arm64-$V.tar.gz | awk '{print $1}')
grep -q "$H" manifest-$V.txt && echo "hash present in manifest ✅"

# b) Import the LND maintainer keys (published in the repo's scripts/keys/)
curl -s "https://api.github.com/repos/lightningnetwork/lnd/contents/scripts/keys" \
  | grep download_url | grep -oE "https://[a-zA-Z0-9./_-]+" \
  | while read u; do curl -s "$u" | gpg --import; done

# c) Verify the manifest signatures
gpg --verify manifest-roasbeef-$V.sig     manifest-$V.txt
gpg --verify manifest-yyforyongyu-$V.sig  manifest-$V.txt
```

**Result on this build:** hash present in the manifest, plus **two good
signatures** —

```
Good signature from "Olaoluwa Osuntokun <laolu32@gmail.com>"   # roasbeef, lead maintainer
Good signature from "yyforyongyu <yy2452@columbia.edu>"
```

The `unknown`/`not certified` warnings are normal (the keys aren't in your
personal web of trust). A matching hash plus good signatures = authentic.

---

## 3. Install

```bash
tar -xzf lnd-darwin-arm64-$V.tar.gz
cp lnd-darwin-arm64-$V/lnd lnd-darwin-arm64-$V/lncli /opt/homebrew/bin/

lnd --version    # -> lnd version 0.21.0-beta
lncli --version  # -> lncli version 0.21.0-beta
```

> Same path note as Bitcoin Core: `/opt/homebrew/bin` is user-writable and first
> on `PATH` (Apple Silicon), so no `sudo`. Upstream docs use
> `/usr/local/bin` with `sudo` — either works.

---

## 4. Configure for signet + Bitcoin Core

LND's config lives at `~/Library/Application Support/Lnd/lnd.conf`.

```bash
mkdir -p ~/Library/Application\ Support/Lnd
nano ~/Library/Application\ Support/Lnd/lnd.conf
```

```ini
[Application Options]
debuglevel=info
alias=lightning-prep-signet
maxpendingchannels=5

[Bitcoin]
bitcoin.active=1
bitcoin.signet=1
bitcoin.node=bitcoind

[Bitcoind]
bitcoind.rpchost=127.0.0.1:38332
bitcoind.rpcuser=bitcoin
bitcoind.rpcpass=<the plaintext password behind bitcoin.conf's rpcauth>
bitcoind.zmqpubrawblock=tcp://127.0.0.1:28332
bitcoind.zmqpubrawtx=tcp://127.0.0.1:28333
```

```bash
chmod 600 ~/Library/Application\ Support/Lnd/lnd.conf   # contains the plaintext RPC password
```

> **The RPC credential link.** Bitcoin Core stores only the *hash* (`rpcauth`);
> LND needs the matching *plaintext* password here. They must correspond. If the
> password was ever exposed (e.g. printed to a terminal), rotate it: generate a
> new password, regenerate the `rpcauth` line (see `bitcoin-core.md` §4), restart
> `bitcoind`, and update `bitcoind.rpcpass` here to match.
>
> **Secrets:** `lnd.conf` holds a plaintext password — keep it `600` and out of
> the repo. It's covered by `.gitignore` (`*.macaroon`, `.lnd/`, secrets globs).

### Sanity-check the credentials before starting LND

Confirm the user/password authenticate against `bitcoind` directly:

```bash
curl -s --user "bitcoin:<password>" \
  --data-binary '{"jsonrpc":"1.0","id":"t","method":"getblockchaininfo","params":[]}' \
  -H 'content-type:text/plain;' http://127.0.0.1:38332/ | grep -o '"chain":"signet"'
# -> "chain":"signet"  ⇒ credentials good
```

---

## 5. Run LND

```bash
lnd    # runs in the foreground; or background it: nohup lnd > lnd.out 2>&1 &
```

On first run with no wallet, LND starts only its **WalletUnlocker** service and
logs:

```
[INF] LTND: Waiting for wallet encryption password. Use `lncli create` to create
      a wallet, `lncli unlock` to unlock an existing wallet ...
```

Check state any time:

```bash
lncli --network=signet state    # NON_EXISTING -> no wallet yet
```

---

## 6. Create the wallet

> ⚠️ **Do this yourself, interactively.** This step generates your **24-word
> cipher seed** — the only backup of any funds — and sets the wallet password.
> Record the seed **on paper, offline**. Never screenshot, paste, or commit it.

```bash
lncli --network=signet create
```

Prompts, in order:

1. **Wallet password** (min 8 chars) — encrypts the wallet; re-entered on every
   `lncli unlock`.
2. *"Do you have an existing cipher seed…?"* → **`n`** (create a new one).
3. *"…passphrase to encrypt your cipher seed…"* → **Enter** to skip (or set one
   and remember it).
4. LND prints the **24-word cipher seed** → **write it down**.

On completion the wallet is created and unlocked; `lncli state` becomes
`SERVER_ACTIVE`.

> **On restart, LND does not auto-unlock.** Run `lncli --network=signet unlock`
> and enter the wallet password each time you start `lnd`.

---

## 7. Verify the connection to Bitcoin Core

```bash
lncli --network=signet getinfo
```

What "connected and synced" looks like (from this build):

| Field | Value |
| --- | --- |
| `identity_pubkey` | `038556574d6649918080ccc33fccf98b1038922ea28e21698d0c70b4e63f72a61f` |
| `alias` | `lightning-prep-signet` |
| `block_height` | `310963` |
| `synced_to_chain` | `true` |
| `chains` | `[{chain: bitcoin, network: signet}]` |

**Cross-checks that prove it's really wired to *your* node:**

```bash
# LND's block hash must equal bitcoind's chain tip
lncli --network=signet getinfo | grep block_hash
bitcoin-cli -signet getbestblockhash
# -> identical hashes ✅   (matched at height 310963)
```

The LND log also confirms the ZMQ wiring and a clean sync:

```
[INF] BTWL: Started listening for bitcoind block notifications via ZMQ on 127.0.0.1:28332
[INF] BTWL: Started listening for bitcoind transaction notifications via ZMQ on 127.0.0.1:28333
[INF] LTND: Chain backend is fully synced! end_height=310963
```

Derive a first on-chain address (taproot) to confirm the wallet is usable:

```bash
lncli --network=signet newaddress p2tr
# -> { "address": "tb1p..." }   (fund from a signet faucet)
```

> `num_peers: 0` and `synced_to_graph: false` right after creation are normal —
> LND has just begun bootstrapping the Lightning peer network and gossip graph.

---

## Task checklist (sprint section: Environment Setup)

- [x] Install LND (`v0.21.0-beta`, arm64) — verified (hash + 2 good signatures)
- [x] Configure LND (signet, `bitcoind` backend, ZMQ)
- [x] Connect LND to Bitcoin Core (`synced_to_chain: true`, block hashes match)
- [x] Create + test wallet (`SERVER_ACTIVE`, taproot address derived)
- [x] Document setup process (this file)

**Deliverable:** LND running and connected to Bitcoin Core ✅

---

## Next

With LND synced, the next steps are funding the wallet from a signet faucet and
opening a first channel — then **Pool** (`setup/pool.md`), which connects to this
LND node to buy/sell channel liquidity.

Useful day-to-day commands:

```bash
lncli --network=signet unlock          # after every restart
lncli --network=signet getinfo         # node identity + sync state
lncli --network=signet walletbalance   # on-chain balance
lncli --network=signet newaddress p2tr # receive on-chain (fund from a faucet)
lncli --network=signet stop            # clean shutdown
```

---

## Troubleshooting notes

| Symptom | Fix |
| --- | --- |
| `lnd`/`lncli: command not found` | Binaries not on PATH — re-run the `cp` step in §3, or open a new terminal. |
| `unable to connect to bitcoind` / RPC auth errors | `bitcoind.rpcpass` doesn't match `bitcoin.conf`'s `rpcauth`. Re-verify with the `curl` check in §4; rotate if needed. |
| `wallet locked, unlock it to enable full RPC access` | Run `lncli --network=signet unlock`. LND never auto-unlocks on restart. |
| `getinfo` shows `synced_to_chain: false` | Wait for Bitcoin Core to finish syncing first; LND can only sync to a synced backend. Check the bitcoind dashboard / `getblockchaininfo`. |
| No ZMQ notifications / LND not seeing new blocks | Confirm `bitcoind` has the `zmqpubrawblock`/`zmqpubrawtx` lines and was restarted; check `bitcoin-cli -signet getzmqnotifications`. |
| `lncli` can't reach LND (`connection refused`) | `lnd` isn't running, or you omitted `--network=signet`. |
