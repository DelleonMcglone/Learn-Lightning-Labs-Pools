"""Local web UI for the Advisor: recommendation views + a grounded chat.

Design (mirrors the CLI's guarantees):
- The **recommendation views** render the deterministic engine's output
  verbatim — severity, economics, commands. No LLM in that path.
- The **chat** is the conversational surface: Claude answers questions
  grounded in the same knowledge base + the *sanitized* current report
  (privacy filter applies to everything that leaves the machine). Chat
  replies are clearly labeled conversational; the recommendation cards
  remain the authoritative numbers.
- No API key → views still work fully; chat explains it's offline.

Run with:  advisor serve  (default http://127.0.0.1:8899)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ..collectors.lnd_collector import collect_snapshot
from ..collectors.market import collect_market
from ..config import Settings
from ..history import HistoryStore
from ..llm.advisor import SYSTEM_PROMPT, load_knowledge
from ..llm.privacy import sanitize_report
from ..lndclient import LndClient, LndClientError
from ..recommend import recommend as run_recommend
from ..signals import compute_signals

STATIC_DIR = Path(__file__).parent / "static"

CHAT_SYSTEM = SYSTEM_PROMPT.replace(
    "Output ONLY a JSON array", "Ignore the JSON-array output rule; answer "
    "conversationally in short plain-language paragraphs"
) + """

You are in CHAT mode: the operator asks free-form questions. Ground every
answer in the CURRENT REPORT provided and the domain knowledge. If asked
about actions, point at the matching recommendation's command rather than
inventing new figures. If a needed number isn't in the report, say so.

COMMAND RULE (critical): if you share a CLI command, copy it CHARACTER-FOR-
CHARACTER from a recommendation's "command" field in the report. Never
reconstruct commands or substitute flag values — a converted or recomputed
flag (e.g. writing an APR where a per-term percent belongs) produces a
broken order. If the report has no command for it, describe the action in
words and say the report doesn't include a priced command.
"""


class ChatRequest(BaseModel):
    messages: list  # [{"role": "user"|"assistant", "content": str}]


class DataProvider:
    """Bundles the collection pipeline so tests can substitute fixtures."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def gather(self) -> dict:
        with LndClient(self.settings) as client:
            snap = collect_snapshot(client)
        sig = compute_signals(snap)
        market = collect_market(self.settings)
        store = HistoryStore(self.settings.history_path)
        report = run_recommend(
            snap, sig, market,
            fee_baseline_sat_vb=store.fee_baseline_sat_vb(),
            inbound_trend_sat_per_day=store.inbound_trend_sat_per_day(),
        )
        return {"snap": snap, "sig": sig, "market": market, "report": report}


def create_app(
    provider: DataProvider,
    llm_client_factory: Optional[Callable] = None,
) -> FastAPI:
    app = FastAPI(title="Lightning Liquidity Advisor", docs_url=None)
    state: dict = {"last": None, "last_ts": 0}

    def _gather(force: bool = False) -> dict:
        # 15s cache so the UI can poll cheaply.
        if force or state["last"] is None or time.time() - state["last_ts"] > 15:
            state["last"] = provider.gather()
            state["last_ts"] = time.time()
        return state["last"]

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/report")
    def api_report(refresh: bool = False):
        try:
            d = _gather(force=refresh)
        except LndClientError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        snap, sig, market, report = (
            d["snap"], d["sig"], d["market"], d["report"]
        )
        return {
            "generated_at": int(state["last_ts"]),
            "llm_available": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "node": {
                "alias": snap.identity.alias,
                "synced": snap.identity.synced_to_chain,
                "block_height": snap.identity.block_height,
                "version": snap.identity.version,
            },
            "balances": snap.balances.model_dump(),
            "totals": {
                "inbound_sat": sig.total_inbound_sat,
                "outbound_sat": sig.total_outbound_sat,
                "inbound_share": sig.inbound_ratio,
            },
            "channels": [c.model_dump() for c in snap.channels],
            "market": market.model_dump(),
            "report": report.model_dump(),
        }

    @app.post("/api/chat")
    def api_chat(req: ChatRequest):
        if not os.environ.get("ANTHROPIC_API_KEY") and llm_client_factory is None:
            return {
                "reply": "Chat needs an ANTHROPIC_API_KEY (see .env.example)."
                         " The recommendation views on the left work without"
                         " it and are always the authoritative numbers.",
                "offline": True,
            }
        try:
            d = _gather()
        except LndClientError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)

        payload, _ = sanitize_report(d["report"], d["sig"], d["snap"])
        system = (
            CHAT_SYSTEM + load_knowledge()
            + "\n\nCURRENT REPORT (sanitized):\n" + json.dumps(payload)
        )
        try:
            if llm_client_factory is not None:
                client = llm_client_factory()
            else:
                import anthropic
                client = anthropic.Anthropic()
            resp = client.messages.create(
                model=provider.settings.llm_model,
                max_tokens=provider.settings.llm_max_tokens,
                system=system,
                messages=[
                    {"role": m["role"], "content": m["content"]}
                    for m in req.messages[-12:]  # bounded context
                ],
            )
            reply = "".join(
                b.text for b in resp.content
                if getattr(b, "type", "") == "text"
            )
            return {"reply": reply, "offline": False}
        except Exception as exc:
            return {"reply": f"Chat failed ({exc}). The recommendation "
                             "views remain available.", "offline": True}

    return app


def serve(settings: Settings, host: str, port: int) -> None:  # pragma: no cover
    import uvicorn

    app = create_app(DataProvider(settings))
    uvicorn.run(app, host=host, port=port, log_level="warning")
