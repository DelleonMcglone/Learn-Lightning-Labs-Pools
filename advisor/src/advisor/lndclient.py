"""Minimal read-only gRPC client for lnd's Lightning service.

Handles the two lnd-specific connection quirks: the self-signed TLS cert
(used as the channel root) and the macaroon (attached as call metadata, hex
encoded). Only read RPCs are exposed here — by construction the Advisor cannot
move funds (SPEC NFR1).
"""

from __future__ import annotations

import codecs
import os
from pathlib import Path

import grpc

from .config import Settings
from .lnrpc import lightning_pb2 as ln
from .lnrpc import lightning_pb2_grpc as lnrpc

# lnd's TLS cert uses an ECDSA key; some grpcio builds need this cipher hint.
os.environ.setdefault(
    "GRPC_SSL_CIPHER_SUITES",
    "HIGH+ECDSA:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384",
)


class LndClientError(RuntimeError):
    """Raised when the lnd connection or a call fails in an expected way."""


class LndClient:
    """A thin, read-only wrapper around the lnd Lightning stub."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._channel = self._build_channel(settings)
        self._stub = lnrpc.LightningStub(self._channel)

    @staticmethod
    def _build_channel(settings: Settings) -> grpc.Channel:
        cert_path = Path(settings.tls_cert_path)
        mac_path = Path(settings.macaroon_path)
        if not cert_path.exists():
            raise LndClientError(f"TLS cert not found: {cert_path}")
        if not mac_path.exists():
            raise LndClientError(f"macaroon not found: {mac_path}")

        cert = cert_path.read_bytes()
        macaroon = codecs.encode(mac_path.read_bytes(), "hex")

        def _metadata(_context, callback):
            callback([("macaroon", macaroon)], None)

        ssl_creds = grpc.ssl_channel_credentials(cert)
        auth_creds = grpc.metadata_call_credentials(_metadata)
        combined = grpc.composite_channel_credentials(ssl_creds, auth_creds)
        return grpc.secure_channel(settings.rpc_host, combined)

    def close(self) -> None:
        self._channel.close()

    def __enter__(self) -> "LndClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # --- read RPCs used by the M0 snapshot ---------------------------------

    def get_info(self) -> ln.GetInfoResponse:
        return self._call(self._stub.GetInfo, ln.GetInfoRequest())

    def list_channels(self) -> ln.ListChannelsResponse:
        return self._call(self._stub.ListChannels, ln.ListChannelsRequest())

    def wallet_balance(self) -> ln.WalletBalanceResponse:
        return self._call(self._stub.WalletBalance, ln.WalletBalanceRequest())

    def channel_balance(self) -> ln.ChannelBalanceResponse:
        return self._call(self._stub.ChannelBalance, ln.ChannelBalanceRequest())

    @staticmethod
    def _call(method, request):
        try:
            return method(request, timeout=15)
        except grpc.RpcError as exc:  # pragma: no cover - network dependent
            code = exc.code()  # type: ignore[attr-defined]
            detail = exc.details()  # type: ignore[attr-defined]
            if code == grpc.StatusCode.UNAVAILABLE:
                raise LndClientError(
                    f"lnd unreachable ({detail}). Is it running and unlocked?"
                ) from exc
            raise LndClientError(f"lnd RPC failed [{code}]: {detail}") from exc
