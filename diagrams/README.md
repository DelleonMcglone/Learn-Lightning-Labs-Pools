# Diagrams

Concept diagrams for the [Learn Lightning Labs Pools](../README.md) notes — liquidity,
auctions, and architecture. Each is a self-contained SVG that renders directly on
GitHub.

| Diagram | Used in | Concept |
| --- | --- | --- |
| [lightning-liquidity.svg](./lightning-liquidity.svg) | [03 — Lightning Liquidity](../03-lightning-liquidity.md) | How a channel's fixed capacity splits into inbound/outbound and shifts as payments are received |

Mermaid diagrams (rendered natively by GitHub) live inline where they're used:

| Diagram | Where | Concept |
| --- | --- | --- |
| Pool batch auction flow | [What Is a Lightning Pool?](../writing/what-is-lightning-pool.md) | Sealed-bid batch auction, bid/ask to leased channel |
| Channel seesaw + node purposes | [How Lightning Liquidity Works](../writing/how-lightning-liquidity-works.md) | Inbound/outbound per node type |
| Old way vs. Pool way | [How Pool Solves Inbound Liquidity](../writing/how-pool-solves-inbound-liquidity.md) | Fronting principal vs. paying a premium |
| Advisor five-layer pipeline | [advisor/SPEC.md](../advisor/SPEC.md) | Deterministic core → LLM edge (design) |
| Advisor architecture (as built) | [advisor/README.md](../advisor/README.md#architecture) | Collectors → snapshot → signals → rules → LLM → CLI/web, with history |
| Advisor trust boundaries | [advisor/README.md](../advisor/README.md#trust-boundaries) | What stays local; what leaves sanitized |
| `advisor recommend` sequence | [advisor/README.md](../advisor/README.md#one-advisor-recommend-end-to-end) | Collect → rules → sanitize → Claude → validate → render |
