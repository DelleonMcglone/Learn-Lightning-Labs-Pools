# Repo Review — `lightninglabs/faraday`

Source-code review of Faraday at tag **v0.2.16-alpha** (~13.6k LOC — by far the
smallest of the four). Faraday is the **analytics and recommendation** tool of
the suite: a read-only daemon that connects to one `lnd` node and produces
channel-performance insights, accounting/audit reports, and — the part most
relevant here — **channel-close recommendations**. Unlike Pool and Loop, it
never moves funds; it observes and advises.

> **Why this one matters most for the project.** Faraday is a *recommend-only
> engine over lnd data* — which is exactly the shape of the
> [AI Liquidity Advisor](../README.md#project). This review studies how a
> shipping Lightning-Labs recommender is actually built, so the advisor can
> borrow its structure. Companion reviews: [Pool](./pool.md), [LND](./lnd.md),
> [Loop](./loop.md).

---

## 1. Architecture

Faraday is a thin, read-mostly service layer over `lndclient`:

```
  frcli (CLI)  ──gRPC──►  frdrpcserver.RPCServer
                              │  (macaroon-gated, dual-mode)
                              ▼
                       lndclient.LndServices  ──►  lnd (all subservers)
                              │
        ┌──────────┬──────────┼───────────┬──────────┬─────────┐
        ▼          ▼          ▼           ▼          ▼         ▼
    insights   recommend   accounting   revenue    fiat   resolutions
   (perf data) (close recs) (reports)  (reports) (price)  (close info)
```

- **`frdrpcserver/rpcserver.go`** is the whole server (~600 LOC). It holds an
  `lndclient.LndServices` handle and is **macaroon-permission-gated** — each RPC
  maps to a `bakery.Op` required-permission set, the same entity/action model
  as [LND](./lnd.md#3d-macaroon-permissions--rest).
- **Dual-mode**, which is the key architectural fact: `Start()` runs it as a
  standalone daemon (`faraday` connecting to its own `lnd`), while
  `StartAsSubserver(lndClient)` lets it be **embedded inside Lightning Terminal
  (`litd`)** sharing the host's lnd connection. Same handlers, two hosting
  models.
- It requires `lnd` **built with all subservers** (`signrpc walletrpc chainrpc
  invoicesrpc`) because its reports read forwarding history, on-chain txns,
  invoices, and channel state.

The RPC surface (`frdrpc/faraday.proto`) is seven methods, cleanly split into
**recommendations** (`OutlierRecommendations`, `ThresholdRecommendations`),
**reports** (`RevenueReport`, `NodeAudit`, `CloseReport`), **insights**
(`ChannelInsights`), and **fiat** (`ExchangeRate`).

The feature packages:

| Package | Role |
| --- | --- |
| `insights/` | Per-channel performance data (`ChannelInfo`) — the input to recommendations |
| `recommend/` + `dataset/` | The recommendation engine (§2) |
| `accounting/` | On/off-chain financial reports as line-item entries, for bookkeeping |
| `revenue/` | Per-channel routing-revenue reports |
| `fiat/` | Historical BTC price lookup (CoinDesk / CoinCap backends) to denominate reports in fiat |
| `resolutions/` | How force-closed channel outputs resolved on-chain |
| `chain/`, `fees/`, `lndwrap/`, `paginater/` | Supporting: chain queries, fee lookups, lnd call wrappers, paginated RPC iteration |

---

## 2. The recommendation engine

This is the heart of the review and it's remarkably small — `recommend/`
(394 LOC) over `dataset/` (272 LOC). It answers one question: **which of my
channels should I consider closing?** The design is worth studying precisely
because it's so simple.

### 2a. The input — channel insights

Recommendations run over `[]*insights.ChannelInfo`, each carrying:
`ChannelPoint`, `MonitoredFor` (age), `Uptime`, `VolumeIncoming`,
`VolumeOutgoing`, `FeesEarned`, `Confirmations`, `Private`.

### 2b. Filtering — who's even eligible

`filterChannels` drops two kinds of channel before any statistics run
(`recommend.go`):

- **Too young** — monitored for less than the configured `MinimumMonitored`
  (you can't judge a channel you've barely watched).
- **Private** — unannounced channels aren't there to route, so
  performance metrics don't apply.

### 2c. The metric — normalize by committed capital

The insight is that raw fees/volume aren't comparable across channels of
different size and age. So every metric except uptime is **scaled per
confirmation** (a proxy for "per block that capital has been committed"):

```go
valuePerConfirmation = getValue(channel) / float64(channel.Confirmations)
```

Five selectable metrics: **Uptime** (uptime/monitored ratio), **Revenue**
(fees earned), **IncomingVolume**, **OutgoingVolume**, **Volume** (total) — each
of the latter four confirmation-scaled. This turns a heterogeneous channel set
into one comparable dataset of `channelPoint → float64`.

### 2d. The classifier — IQR outliers or a hard threshold

Two strategies produce the actual close/keep decision:

**Outlier mode** (`dataset.GetOutliers`) — classic **inter-quartile-range
outlier detection**. It computes the lower/upper quartiles (the "exclusive"
method: split the sorted data in half, dropping the median for odd counts),
takes the IQR = UQ − LQ, and flags a value as a lower outlier when:

```
value < lowerQuartile − (IQR × multiplier)
```

The **`outlierMultiplier`** tunes strictness — the doc-commented example is
crisp: for data with LQ=5, UQ=6 (IQR=1), a multiplier of **3** (the default,
cautious) flags only value 1 as a low outlier, while **1.5** (aggressive) also
flags 2. So a low-revenue channel is recommended for close only when it's a
statistical outlier *relative to your other channels* — no magic absolute
number. (Fewer than 3 channels → no quartiles → no recommendations, handled
gracefully.)

**Threshold mode** (`dataset.GetThreshold`) — the simpler alternative: flag any
channel whose metric is `<=` a user-supplied absolute threshold. Useful when you
have an explicit floor ("close anything earning under X").

### 2e. The output — a report, not an action

`OutlierRecommendations` / `ThresholdRecommendations` return a `Report`:
`TotalChannels`, `ConsideredChannels`, and `Recommendations` (a map of
channelPoint → `{Value, RecommendClose}`). Crucially it **only recommends** —
Faraday never closes anything. The operator (or a higher-level tool like the
advisor) decides.

---

## 3. The rest of the codebase

- **`accounting/` + `NodeAudit`** — the largest report (`node_audit.go`, ~428
  LOC). It walks on-chain transactions, channel opens/closes, forwards, and
  payments/invoices to produce a categorized, timestamped ledger of every
  sat that moved — the bookkeeping backbone, optionally denominated in fiat.
- **`fiat/`** — pluggable historical-price backends (CoinDesk historical API,
  CoinCap) so reports can be expressed in a chosen currency at the timestamp of
  each entry. A clean example of an external-data adapter behind an interface.
- **`revenue/`** — attributes routing fee revenue to the channels that earned
  it (feeds both reports and the RevenueMetric recommendation).
- **`resolutions/`** — reconstructs how a force-closed channel's outputs were
  ultimately swept/resolved on-chain, for close reports.

The whole codebase is small, interface-driven, and read-only — a good first
codebase to actually contribute to in the suite.

---

## 4. Takeaways — Faraday as the advisor's blueprint

Faraday *is* the recommend-only pattern the AI Liquidity Advisor targets, so its
choices are directly instructive:

1. **Recommend, never act.** The hard architectural line — produce a `Report`
   with `RecommendClose` booleans and stop — is exactly the safety posture the
   advisor should adopt. The tool advises; the human/operator executes.
2. **Normalize before you compare.** The per-confirmation scaling is the key
   modeling move: never compare raw fees across channels of different size/age.
   The advisor's Pool/Loop suggestions need the same treatment (e.g. rate *per
   sat committed*, which is what [note 04](../04-pool-auctions-lease-pricing.md)'s
   ppb already is).
3. **Statistical relativity beats magic numbers.** IQR outliers judge a channel
   against *your* portfolio, not an absolute cutoff — far more robust than
   "close anything under 100 sats". An advisor recommending swaps/leases should
   likewise reason relative to the node's own history and the current market.
4. **Same lnd surface, richer brain.** Faraday adds real value with only
   read-only lnd RPCs (`insights`, forwarding history) + a bit of statistics.
   The advisor can start exactly here — Faraday's `ChannelInfo` is essentially
   the advisor's feature vector — and layer Pool/Loop economics on top.

Where the advisor goes *beyond* Faraday: Faraday only recommends **closing**;
the advisor recommends **acquiring** (Pool bids, Loop swaps) and **pricing**
them. But the skeleton — read lnd → build a comparable dataset → classify →
emit a recommend-only report — is Faraday's, and it's proven.

---

## File map (where to look first)

| To understand… | Read |
| --- | --- |
| The recommendation logic | `recommend/recommend.go` |
| The IQR outlier / threshold math | `dataset/dataset.go` |
| The channel feature vector | `insights/insights.go` (`ChannelInfo`) |
| The server + dual-mode hosting | `frdrpcserver/rpcserver.go` |
| The accounting/audit ledger | `frdrpcserver/node_audit.go`, `accounting/` |
| Fiat price adapters | `fiat/coindesk_api.go`, `fiat/` |
| The API + CLI | `frdrpc/faraday.proto`, `cmd/frcli/` |

---

_Part of [Learn Lightning Labs Pools](../README.md). Reviewed at tag `v0.2.16-alpha`.
Companion: [Pool](./pool.md), [LND](./lnd.md), [Loop](./loop.md) — this completes
the four-repo source-analysis set._
