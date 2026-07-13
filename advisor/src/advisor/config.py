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
        return self
