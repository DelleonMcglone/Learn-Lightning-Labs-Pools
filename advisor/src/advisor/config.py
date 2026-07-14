"""Connection configuration for the Advisor.

Defaults target the local testnet ``lnd`` from the Pool operations session
(see ../../setup/pool.md). Every field is overridable via ``ADVISOR_*`` env
vars or CLI flags.

Safety (SPEC NFR2): the default macaroon is ``readonly.macaroon``, not
``admin.macaroon`` — the Advisor only ever needs read access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# lnd stores its chain macaroons under this sub-path.
_CHAIN = "data/chain/bitcoin"


class Settings(BaseSettings):
    """Advisor connection settings.

    Derived paths (macaroon, TLS cert) are computed from ``lnddir`` + ``network``
    when not set explicitly, so the common case needs no configuration.
    """

    model_config = SettingsConfigDict(env_prefix="ADVISOR_", extra="ignore")

    network: str = "testnet"
    rpc_host: str = "localhost:10010"
    lnddir: Path = Path("~/Library/Application Support/Lnd-testnet").expanduser()

    # Derived from lnddir/network if left as None.
    macaroon_path: Optional[Path] = None
    tls_cert_path: Optional[Path] = None

    # --- M2 market/fee collectors ---------------------------------------
    # Pool: the `pool` CLI is used as the JSON interface to poold.
    pool_bin: str = "pool"
    # Loop: loopd's REST API (host:port) and its data dir for TLS/macaroon.
    loop_rest_host: str = "localhost:8091"
    loop_dir: Path = Path("/tmp/loopbuild/data")
    # Reference amount used when asking Loop for quotes.
    quote_amount_sat: int = 500_000
    # mempool.space API base; derived from network if left empty.
    mempool_api_base: str = ""

    # --- M4 LLM advisor ---------------------------------------------------
    llm_model: str = "claude-sonnet-4-5"
    llm_max_tokens: int = 2000

    # --- Ingestion pipeline ------------------------------------------------
    # Append-only JSONL time series written by `advisor ingest`.
    history_path: Path = Path("~/.advisor/history.jsonl")

    @model_validator(mode="after")
    def _derive_paths(self) -> "Settings":
        self.lnddir = Path(self.lnddir).expanduser()
        if self.macaroon_path is None:
            self.macaroon_path = (
                self.lnddir / _CHAIN / self.network / "readonly.macaroon"
            )
        if self.tls_cert_path is None:
            self.tls_cert_path = self.lnddir / "tls.cert"
        self.macaroon_path = Path(self.macaroon_path).expanduser()
        self.tls_cert_path = Path(self.tls_cert_path).expanduser()
        self.loop_dir = Path(self.loop_dir).expanduser()
        self.history_path = Path(self.history_path).expanduser()
        if not self.mempool_api_base:
            prefix = "" if self.network == "mainnet" else f"/{self.network}"
            self.mempool_api_base = f"https://mempool.space{prefix}/api"
        return self
