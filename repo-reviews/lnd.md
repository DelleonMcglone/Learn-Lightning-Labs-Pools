# Repo Review — `lightningnetwork/lnd`

Source-code review of the Lightning Network Daemon at tag **v0.19.0-beta** (the
version Pool builds against). LND is large — ~447k lines of Go across ~30 major
packages — so this is a **map, not an exhaustive read**: enough to know where
each subsystem lives, how the daemon is wired, and how the two areas most
relevant to the rest of this repo (the **routing system** and the **RPC
interfaces**) actually work.

> Why review LND here: Pool, Loop, and Faraday are all *clients* of an `lnd`
> node. Understanding `lnd`'s subsystem boundaries and its routing/RPC surface
> is what lets the [Pool review](./pool.md) and the AI advisor project reason
> about what the node can actually be asked to do. Companion hands-on notes:
> [LND setup](../setup/lnd.md), [Lightning operations](../setup/operations.md).

---

## 1. Architecture

### 1a. The layered daemon

LND is a single process composed of ~20 subsystems wired together in
`server.go` (~5,500 LOC) and driven by `rpcserver.go` (~9,200 LOC). The
subsystems form a clean dependency stack, and the **startup order in
`server.go` reveals the layering** — each layer depends only on those started
before it:

```
  ┌─ Foundation ─────────────────────────────────────────────┐
  │  ChainControl (chainreg/): chain backend + wallet         │
  │   • backend: bitcoind | btcd | neutrino                   │
  │   • Wallet (lnwallet.LightningWallet), ChainNotifier,     │
  │     ChainView, FeeEstimator, Signer, KeyRing              │
  └───────────────────────────────────────────────────────────┘
        │ started first: ChainNotifier → BestBlockTracker
        ▼
  ┌─ On-chain safety ────────────────────────────────────────┐
  │  sweeper (sweep/) → breachArbitrator + chainArb           │
  │  (contractcourt/): watch channels, enforce/resolve closes │
  └───────────────────────────────────────────────────────────┘
        ▼
  ┌─ Channel operation ──────────────────────────────────────┐
  │  fundingMgr (funding/) → htlcSwitch (htlcswitch/)         │
  │  peers (peer.Brontide): encrypted transport per peer      │
  └───────────────────────────────────────────────────────────┘
        ▼
  ┌─ Network view + routing ─────────────────────────────────┐
  │  graphDB → graphBuilder (graph/) → chanRouter (routing/)  │
  │  authGossiper (discovery/): BOLT-7 gossip                 │
  └───────────────────────────────────────────────────────────┘
        ▼
  ┌─ Application ────────────────────────────────────────────┐
  │  invoices (invoices/) · sphinx (hop.OnionProcessor) ·     │
  │  chanStatusMgr · connMgr · RPC server                     │
  └───────────────────────────────────────────────────────────┘
```

Actual start sequence (from `server.go` `Start()`): `ChainNotifier →
BestBlockTracker → [towerClient] → sweeper → breachArbitrator → fundingMgr →
htlcSwitch → chainArb → graphDB → graphBuilder → chanRouter → authGossiper →
invoices → sphinx → chanStatusMgr → connMgr`.

### 1b. The core subsystems and their jobs

| Subsystem | Type | Package | Role |
| --- | --- | --- | --- |
| Chain control | `ChainControl` | `chainreg/` | Owns the chain backend + wallet; the foundation everything else queries |
| Peer | `Brontide` | `peer/` | One per connected peer; the BOLT-8 Noise-encrypted transport and message router |
| HTLC switch | `Switch` + `channelLink` | `htlcswitch/` | The forwarding fabric. `SendHTLC` is the entry point for *our* outgoing payments; `CircuitMap` tracks in-flight HTLC circuits |
| Funding | `funding.Manager` | `funding/` | The channel-open state machine (BOLT-2), from `open_channel` to a live channel |
| Graph | `ChannelGraph` + `graph.Builder` | `graph/` | The node's view of the network; builder keeps it live from gossip + chain |
| Router | `ChannelRouter` | `routing/` | Pathfinding + payment lifecycle (§2) |
| Gossip | `AuthenticatedGossiper` | `discovery/` | BOLT-7 channel/node announcement validation and relay |
| Contract court | `ChainArbitrator` + `ChannelArbitrator` | `contractcourt/` | Per-channel on-chain state machine: watches for closes, resolves HTLCs, enforces penalties |
| Sweeper | `UtxoSweeper` | `sweep/` | Batches and fee-bumps sweeping of on-chain outputs |
| Invoices | `InvoiceRegistry` | `invoices/` | Invoice lifecycle and settlement (the receive side) |

Two more foundational packages worth knowing: **`lnwire/`** (the BOLT wire
protocol message types — every `open_channel`, `update_add_htlc`, etc.) and
**`channeldb/`** (the `bbolt`-backed persistence for channels, payments, and
historically the graph). The trust-critical on-chain enforcement lives in
`contractcourt/` — that's the code that makes a channel safe to close
unilaterally.

---

## 2. The routing system

Routing is where a payment becomes a concrete sequence of HTLCs. It lives in
`routing/` (~15k LOC) reading from `graph/`, and hands finished HTLCs to
`htlcswitch/`.

### 2a. `ChannelRouter` — the orchestrator

`routing/router.go` defines `ChannelRouter`, the "layer 3" between the
application and the switch. Payment entry points:

- `SendPayment(payment *LightningPayment)` — blocking; the workhorse.
- `SendPaymentAsync(...)` — non-blocking goroutine variant.
- `SendToRoute(hash, route, ...)` — send along a **pre-computed** route
  (what `BuildRoute` + manual sends use).
- `FindRoute(req *RouteRequest)` — pure pathfinding, no send.

`PreparePayment` sets up a `PaymentSession` and `ShardTracker`, registers the
payment with the **ControlTower**, and hands off to the lifecycle loop. The
router's `Config` injects everything it needs as interfaces/closures:
`RoutingGraph`, `Payer` (the switch dispatcher), `Control` (control tower),
`MissionControl`, `SessionSource`, and `GetLink` (live channel bandwidth).

### 2b. Pathfinding — backwards Dijkstra with a probability-weighted cost

`routing/pathfind.go`'s `findPath` runs a **modified Dijkstra search
*backwards*, from destination toward source**. Searching backward is what lets
fees and amounts accumulate correctly (the amount at each hop depends on
downstream fees). The edge cost combines three things:

```go
// edgeWeight: fee plus a time-lock risk penalty
timeLockPenalty := lockedAmt * timeLockDelta * RiskFactorBillionths / 1e9
weight := fee + timeLockPenalty                 // RiskFactorBillionths = 15
```

That weight is then folded together with **success probability** (from Mission
Control) via `getProbabilityBasedDist`, so the router trades off cheap-but-risky
against expensive-but-reliable. A per-payment `timePref` ∈ [−1, 1] tunes that
tradeoff (fees-only vs. speed/reliability). CLTV is bounded: `MinCLTVDelta = 18`
blocks, a 3-block `BlockPadding` on the final hop, and the cumulative timelock
is checked against the payment's `CltvLimit` at every step. Payload size is also
tracked per hop against `sphinx.MaxPayloadSize`.

Edges aren't only public channels: `unified_edges.go` / `additional_edge.go`
unify graph channels with **BOLT-11 route hints** (`PrivateEdge`) and **blinded
paths** (`BlindedEdge`) behind one interface, each with its own payload-size
function.

### 2c. Payment lifecycle + control tower

`routing/payment_lifecycle.go`'s `paymentLifecycle.resumePayment` is the retry
loop for a single payment:

1. `decideNextStep` — proceed (more HTLCs), skip (wait on in-flight), or exit.
2. `requestRoute` → `PaymentSession.RequestRoute` → `findPath`.
3. `registerAttempt` — persist an `HTLCAttemptInfo` via the control tower.
4. `sendAttempt` — build `UpdateAddHTLC`, dispatch via `Payer.SendHTLC`, then
   collect the result asynchronously through `Payer.GetAttemptResult`.
5. On result: report to Mission Control, then decide retry vs. terminal.

The **`ControlTower`** (`routing/control_tower.go`) is the payment state machine
and its persistence: `InitPayment → RegisterAttempt → SettleAttempt/FailAttempt
→ FailPayment`, plus `FetchInFlightPayments` to **resume across restarts**. This
is exactly why a crashed `lnd` doesn't double-pay — attempts are journaled
before they're sent. **MPP/AMP** ride on the `ShardTracker`, which mints a
unique HTLC hash/identifier per shard.

### 2d. Mission Control — learned success probabilities

`routing/missioncontrol.go` turns payment history into edge probabilities.
`ReportPaymentFail` / `ReportPaymentSuccess` record per-node-pair outcomes
(`TimedPairResult`: fail/success time and amount), decayed over time, and
`GetProbability` feeds pathfinding. Two pluggable estimators:

- **Apriori** (`probability_apriori.go`, the default) — untried hops get a base
  `AprioriHopProbability` (0.6), previously-successful hops ~0.95, blended with
  history by `AprioriWeight`; penalties decay with a `PenaltyHalfLife` (1h).
- **Bimodal** (`probability_bimodal.go`) — models channel liquidity as a bimodal
  distribution with a `BimodalScaleMsat` and multi-day `BimodalDecayTime`.

Results persist (`missioncontrol_store.go`) keyed by
`namespace/fromNode/toNode`, flushed ~1s, capped at ~1000 entries.

### 2e. The graph source

Pathfinding reads through a narrow `Graph` interface (`routing/graph.go`):
`ForEachNodeDirectedChannel` + `FetchNodeFeatures`. The concrete store is
`graph/db`'s `ChannelGraph`, exposing `DirectedChannel` (capacity, in/out
policy, inbound fee, other node). `graph.Builder` keeps it live — applying
channel/node announcements, watching the chain for closes, and pruning zombie
channels (no update in ~14 days).

---

## 3. RPC interfaces

LND's API is a **two-tier gRPC model**: one main service plus pluggable
subservers, all fronted by macaroon auth and auto-generated REST.

### 3a. The main `Lightning` service

`lnrpc/lightning.proto` defines the `Lightning` service — **68 RPCs** spanning:

- **On-chain wallet** — `WalletBalance`, `SendCoins`, `ListUnspent`, `NewAddress`
- **Channels** — `OpenChannel`, `CloseChannel`, `ListChannels`, `PendingChannels`
- **Payments (legacy)** — `SendPaymentSync`, `SendToRoute`, `ListPayments`
- **Invoices** — `AddInvoice`, `LookupInvoice`, `SubscribeInvoices`, `DecodePayReq`
- **Graph/info** — `DescribeGraph`, `GetChanInfo`, `QueryRoutes`, `GetInfo`
- **Peers** — `ConnectPeer`, `ListPeers`, `SubscribePeerEvents`
- **Ops** — `UpdateChannelPolicy`, `ForwardingHistory`, channel backups, macaroon baking

### 3b. The subserver pattern

Twelve `lnrpc/*rpc/` packages add ~105 more RPCs. Each subserver:

1. is **gated by a build tag** (`config_active.go` with `//go:build walletrpc`
   vs. an empty `config_default.go`), so features compile in or out;
2. implements the `SubServer` interface (`Start/Stop/Name`) and a `GrpcHandler`
   (`RegisterWithRootServer` / `RegisterWithRestServer` / `CreateSubServer`);
3. registers itself in an `init()` via `lnrpc.RegisterSubServer` (`driver.go`);
4. returns its **own macaroon permission map** from `CreateSubServer`.

The main server discovers them by **reflection**: `subrpcserver_config.go`'s
`subRPCServerConfigs` looks up each subserver's config struct field by name and
`PopulateDependencies` injects the shared subsystems. The notable subservers:

| Subserver | Service | Notable RPCs |
| --- | --- | --- |
| **routerrpc** | `Router` | `SendPaymentV2`, `TrackPaymentV2`, `EstimateRouteFee`, `BuildRoute`, `QueryMissionControl`, `HtlcInterceptor` |
| walletrpc | `WalletKit` | PSBT funding, `BumpFee`, key derivation, UTXO leasing |
| signrpc | `Signer` | `SignOutputRaw`, `MuSig2*` |
| invoicesrpc | `Invoices` | `AddHoldInvoice`, `SettleInvoice`, `CancelInvoice` |
| chainrpc | `ChainNotifier` + `ChainKit` | block/tx notifications |
| wtclientrpc / watchtowerrpc | watchtower client/server | tower registration |
| peersrpc, neutrinorpc, autopilotrpc, verrpc, devrpc | — | node/peer/version/dev ops |

### 3c. `routerrpc` — the modern payment API

This is the one that matters most for clients (it's what `pool`'s L402 payment
*should* use — see below). `router_backend.go`'s `RouterBackend` holds closures
into the router/graph/switch; `router_server.go` exposes:

- **`SendPaymentV2`** — streaming payment with MPP/AMP; replaces the deprecated
  `SendPayment`.
- **`TrackPaymentV2`** / `TrackPayments` — stream payment state by hash.
- **`SendToRouteV2`**, `EstimateRouteFee`, `BuildRoute` — manual routing.
- **`QueryMissionControl`** / `SetMissionControlConfig` — inspect/tune the
  probability model (directly exposes §2d).
- **`HtlcInterceptor`** / `SubscribeHtlcEvents` — intercept and observe forwards.

> **Cross-repo tie-in.** In the [Pool session](../setup/pool.md) the L402 token
> payment failed because `aperture` → `lndclient.PayInvoice` used the **legacy
> `SendPaymentSync`** (main service) rather than `routerrpc.SendPaymentV2`. This
> review confirms V2 is the intended path — the migration is the fix.

### 3d. Macaroon permissions + REST

Every method maps to `bakery.Op{Entity, Action}` — entities like `onchain`,
`offchain`, `invoices`, `peers`, `info`, `macaroon`; actions `read`/`write`/
`generate`. `rpcserver.go`'s `MainRPCServerPermissions()` holds the main map;
subserver maps are **merged into the interceptor chain at startup**
(`addDeps`). E.g. `OpenChannel` needs both `onchain:write` and `offchain:write`.
This entity/action scheme is exactly what `lncli bakemacaroon` slices up.

REST is auto-generated: every proto yields a `*.pb.gw.go` grpc-gateway proxy and
a `*.swagger.json`, registered alongside the gRPC handlers. So every gRPC method
is reachable over HTTP/JSON without extra code.

### 3e. How the RPC layer reaches the subsystems

The `rpcServer` struct holds `server *server` and reaches subsystems as
`r.server.htlcSwitch`, `r.server.chanRouter`, `r.server.graphDB`,
`r.server.cc.Wallet`, `r.server.controlTower`, etc. Rather than importing those
packages directly, backends like `RouterBackend` receive **closures**
(`FindRoute: s.chanRouter.FindRoute`, `FetchChannelCapacity: …`), which keeps the
RPC layer decoupled and mockable.

---

## 4. Takeaways for contributing / for the advisor project

- **The subsystem boundaries are the mental model.** Almost any change lands in
  one subsystem behind an interface (`htlcswitch`, `routing`, `contractcourt`,
  …), and `server.go` shows exactly how they compose. Start from the startup
  order.
- **Mission Control is directly queryable** (`routerrpc.QueryMissionControl`),
  and pathfinding config is tunable at runtime. For a liquidity advisor, that's
  a real, structured signal source about which peers/routes actually work — not
  just static graph topology.
- **`routerrpc` is the API to build on.** `SendPaymentV2` / `TrackPaymentV2` /
  `HtlcInterceptor` / `SubscribeHtlcEvents` are the modern surface; the main
  service's payment RPCs are deprecated. The Pool L402 bug is a concrete example
  of code still on the old path.
- **The macaroon entity/action model** is how you scope a least-privilege
  credential for any tool that drives a node (the advisor should mint a
  read-mostly macaroon, not use `admin.macaroon`).

---

## File map (where to look first)

| To understand… | Read |
| --- | --- |
| How the daemon is wired | `server.go` (subsystem struct + `Start()` order), `chainreg/chainregistry.go` |
| Pathfinding | `routing/pathfind.go` (`findPath`, `edgeWeight`) |
| Payment retries + persistence | `routing/payment_lifecycle.go`, `routing/control_tower.go` |
| Learned route probabilities | `routing/missioncontrol.go`, `routing/probability_{apriori,bimodal}.go` |
| HTLC forwarding fabric | `htlcswitch/switch.go`, `htlcswitch/link.go` |
| On-chain enforcement | `contractcourt/chain_arbitrator.go`, `channel_arbitrator.go` |
| Main API + permissions | `lnrpc/lightning.proto`, `rpcserver.go` (`MainRPCServerPermissions`) |
| Modern payment API | `lnrpc/routerrpc/router.proto`, `router_backend.go` |
| Subserver plumbing | `lnrpc/sub_server.go`, `subrpcserver_config.go` |

---

_Part of [Lightning Labs Prep](../README.md). Reviewed at tag `v0.19.0-beta`.
Companion: [Pool repo review](./pool.md), [LND setup](../setup/lnd.md)._
