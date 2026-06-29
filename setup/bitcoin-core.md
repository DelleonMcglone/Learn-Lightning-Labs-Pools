# Bitcoin Core Setup (macOS · signet)

> **Purpose:** Reproducible notes for installing, verifying, configuring, and
> syncing a Bitcoin Core node on macOS, running on **signet** for learning and
> Lightning development. This is the foundation the LND node connects to later.
>
> Part of the [Environment Setup](../README.md) section. **Deliverable:** a
> working, synced Bitcoin node — done ✅. This file is a log of the exact steps
> that produced it.

---

## Why signet?

A full mainnet node is a one-time ~400 GB download plus 5–10 GB/month, and the
initial sync takes hours to days. For learning and for building against LND,
Pool, and Loop, that's unnecessary.

**signet** is a shared test network that syncs in well under an hour and behaves
like the real peer-to-peer network — real peers, real gossip, real channels —
but with worthless coins. It's the right environment for the hands-on Lightning
work in this sprint.

> Alternative: `regtest` is an instant, fully private chain where you mine your
> own blocks. Good for isolated unit-style testing; signet is better here because
> it gives realistic network behavior and peers to open channels with.

---

## Versions (as built)

| Item | Value |
| --- | --- |
| Bitcoin Core | 31.0 (`bitcoind v31.0.0`) |
| OS | macOS, Apple Silicon `arm64` |
| Network | signet |
| Install path | `/opt/homebrew/bin` (see §3) |
| Source | https://bitcoincore.org/bin/bitcoin-core-31.0/ |

> **Download only from `bitcoincore.org`** (or `bitcoin.org/en/download`).
> Do **not** use GitHub's auto-generated asset links — the bitcoincore.org
> binaries are built deterministically and signed.

---

## 0. Check your chip

```bash
uname -m
# arm64   -> Apple Silicon (M1/M2/M3/M4)  -> use the arm64 build   <- this machine
# x86_64  -> Intel                        -> use the x86_64 build
```

---

## 1. Download

From `https://bitcoincore.org/bin/bitcoin-core-31.0/`, grab three files:

- The macOS binary tarball (gives the CLI tools `bitcoind` / `bitcoin-cli`):
  - Apple Silicon: `bitcoin-31.0-arm64-apple-darwin.tar.gz`
  - Intel: `bitcoin-31.0-x86_64-apple-darwin.tar.gz`
- `SHA256SUMS` — the signed checksum list
- `SHA256SUMS.asc` — the signatures on that list

```bash
BASE="https://bitcoincore.org/bin/bitcoin-core-31.0"
curl -sSL -O "$BASE/bitcoin-31.0-arm64-apple-darwin.tar.gz"
curl -sSL -O "$BASE/SHA256SUMS"
curl -sSL -O "$BASE/SHA256SUMS.asc"
```

> The `.dmg` GUI installer also works, but the tarball provides the
> command-line daemon used with LND in the next section.

---

## 2. Verify (do not skip)

Verification confirms the binary wasn't tampered with in transit. Skipping it is
the classic mistake. Run these from the download folder.

```bash
# Install GnuPG if needed
brew install gnupg

# a) Confirm the binary's hash appears in the signed checksum list
shasum -a 256 --ignore-missing --check SHA256SUMS
# -> bitcoin-31.0-arm64-apple-darwin.tar.gz: OK

# b) Import the Bitcoin Core builder keys
curl -s "https://api.github.com/repositories/355107265/contents/builder-keys" \
  | grep download_url | grep -oE "https://[a-zA-Z0-9./_-]+" \
  | while read u; do curl -s "$u" | gpg --import; done

# c) Verify the signatures on the checksum file
gpg --verify SHA256SUMS.asc SHA256SUMS
# -> multiple "Good signature from ..." lines
```

**Result on this build:** hash `OK` plus **13 "Good signature"** lines. The
`unknown`/`not certified with a trusted signature` warnings are normal — they
mean the keys aren't in your personal web of trust, not that the signatures are
bad. A passing hash plus several good signatures means the download is authentic.

---

## 3. Install

```bash
# Extract (match your actual filename)
tar -xzf bitcoin-31.0-arm64-apple-darwin.tar.gz

# Copy binaries onto your PATH
cp -r bitcoin-31.0/bin/* /opt/homebrew/bin/

# Confirm
bitcoind --version   # -> Bitcoin Core daemon version v31.0.0
```

> **Install path note.** The upstream docs use `sudo cp -r bitcoin-31.0/bin/*
> /usr/local/bin/`. On Apple Silicon, `/opt/homebrew/bin` is already first on
> `PATH` and user-writable, so no `sudo` is needed — the binaries resolve
> identically. Use whichever you prefer; if you want the canonical location:
> `sudo cp /opt/homebrew/bin/bitcoin* /usr/local/bin/`.

> **Gatekeeper:** if macOS blocks the binary on first run, go to
> System Settings → Privacy & Security → "Open Anyway", then retry.

---

## 4. Configure for signet

```bash
mkdir -p ~/Library/Application\ Support/Bitcoin
nano ~/Library/Application\ Support/Bitcoin/bitcoin.conf
```

This config is already **LND-ready** — it uses hashed RPC credentials
(`rpcauth`) instead of a plaintext password, and exposes the ZMQ endpoints LND
subscribes to for block/tx notifications.

```ini
signet=1
server=1
txindex=1          # transaction index — required by Lightning tooling

[signet]
# Hashed RPC credentials (safe to share/commit). User: bitcoin
# The plaintext password is NOT stored here — bitcoin-cli authenticates via the
# datadir cookie; LND uses the plaintext password paired with this user.
rpcauth=bitcoin:<salt>$<hmac-sha256-hash>

# ZMQ endpoints — LND subscribes here for block/tx notifications
zmqpubrawblock=tcp://127.0.0.1:28332
zmqpubrawtx=tcp://127.0.0.1:28333
```

```bash
chmod 600 ~/Library/Application\ Support/Bitcoin/bitcoin.conf
```

### Generating the `rpcauth` line

`rpcauth` is a salted HMAC-SHA256 of your chosen password — the same scheme as
Bitcoin Core's `share/rpcauth/rpcauth.py`. Generate it without storing the
plaintext anywhere on disk:

```bash
PW=$(openssl rand -hex 24)          # your local RPC password — save it for LND
python3 - "$PW" <<'PY'
import sys, os, hmac, hashlib, binascii
salt = binascii.hexlify(os.urandom(16)).decode()
h = hmac.new(salt.encode(), sys.argv[1].encode(), hashlib.sha256).hexdigest()
print(f"rpcauth=bitcoin:{salt}${h}")
PY
echo "RPC password (store in a password manager): $PW"
```

Paste the printed `rpcauth=...` line into the config.

> **Secrets:** the `rpcauth` hash is safe to commit; the **plaintext password is
> not**. It can't be recovered from the hash, so save it in a password manager —
> LND needs it as `bitcoind.rpcpass`. Lost it? Just regenerate a new
> `rpcauth` line with a fresh password.

---

## 5. Run and sync

```bash
# Start the daemon in the background
bitcoind -daemon

# Watch the sync progress
bitcoin-cli -signet getblockchaininfo
```

Look for:

- `blocks` climbing toward `headers`
- `verificationprogress` approaching `1.0`
- `initialblockdownload` flipping from `true` to `false`

Useful checks:

```bash
bitcoin-cli -signet getblockcount        # current block height
bitcoin-cli -signet getconnectioncount   # number of peers
bitcoin-cli -signet getzmqnotifications  # confirm ZMQ endpoints are live
bitcoin-cli -signet stop                 # clean shutdown
```

> Because the config uses `rpcauth` (not a plaintext `rpcpassword`), local
> `bitcoin-cli` calls authenticate automatically via the `.cookie` file in the
> datadir — no flags needed. If you ever revert to a plaintext `rpcpassword`,
> no cookie is generated and `bitcoin-cli` needs the credentials passed
> explicitly.

---

## 6. Verify synchronization

On this run the node connected to **10 peers**, fetched all **310,953 headers**,
and validated blocks up from zero. Sync is complete when:

```bash
bitcoin-cli -signet getblockchaininfo | grep -E '"verificationprogress"|"initialblockdownload"'
# "verificationprogress": 0.9999...
# "initialblockdownload": false
```

`verificationprogress` ~`0.9999` **and** `initialblockdownload: false` ⇒ signet
is fully synced and the node is ready for LND.

---

## Task checklist (sprint section: Environment Setup)

- [x] Install Bitcoin Core (`v31.0.0`, arm64)
- [x] Verify download (hash `OK` + 13 good signatures)
- [x] Configure Bitcoin Core (signet, `txindex`, `rpcauth`, ZMQ)
- [x] Run and sync the node
- [x] Verify synchronization
- [x] Document setup process (this file)

**Deliverable:** Working Bitcoin node ✅

---

## Next

In **LND Setup**, this same node is wired to LND using the values already
configured here:

- `bitcoind.rpcuser=bitcoin` + `bitcoind.rpcpass=<the plaintext password>`
- `bitcoind.zmqpubrawblock=tcp://127.0.0.1:28332`
- `bitcoind.zmqpubrawtx=tcp://127.0.0.1:28333`

See [`lnd.md`](./lnd.md) _(coming next)_.

---

## Troubleshooting notes

| Symptom | Fix |
| --- | --- |
| `bitcoind: command not found` | Binaries not on PATH — re-run the `cp` step in §3, or open a new terminal. |
| Gatekeeper blocks the binary | System Settings → Privacy & Security → "Open Anyway". |
| `Could not connect to the server` on `bitcoin-cli` | The daemon isn't running, or you omitted `-signet`. Start with `bitcoind -daemon` and include the network flag. |
| `Could not locate RPC credentials` | You changed `rpcauth`/password while the daemon was running under the old credentials. Stop it with the old creds (`bitcoin-cli -signet -rpcuser=... -rpcpassword=... stop`), then restart. |
| Sync stuck at 0 peers | Check your network/firewall; signet peer discovery can take a minute on first start. |
| ZMQ changes not taking effect | ZMQ and `rpcauth` are read at startup only — restart `bitcoind` after editing them. |
