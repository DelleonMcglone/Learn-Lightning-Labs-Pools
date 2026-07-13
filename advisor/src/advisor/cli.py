"""Advisor CLI (SPEC FR9). M0 ships the `snapshot` command; `signals` (M1) and
`recommend` (M3+) follow the roadmap.
"""

from __future__ import annotations

import sys
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .collectors.lnd_collector import collect_snapshot
from .config import Settings
from .lndclient import LndClient, LndClientError
from .models import NodeSnapshot

app = typer.Typer(
    add_completion=False,
    help="Lightning Liquidity Advisor — read-only, recommend-only.",
)
console = Console()
err = Console(stderr=True)


def _sats(n: int) -> str:
    return f"{n:,} sat"


def _settings_from_opts(
    network: Optional[str], host: Optional[str]
) -> Settings:
    overrides = {}
    if network:
        overrides["network"] = network
    if host:
        overrides["rpc_host"] = host
    return Settings(**overrides)


def _render_snapshot(snap: NodeSnapshot) -> None:
    i = snap.identity
    sync = "[green]synced[/]" if i.synced_to_chain else "[yellow]syncing[/]"
    console.print(
        Panel(
            f"[b]{i.alias}[/]  {sync}\n"
            f"[dim]{i.pubkey}[/]\n"
            f"version {i.version} · block {i.block_height:,} · "
            f"{i.num_active_channels} active channels · {i.num_peers} peers",
            title="⚡ Node",
            border_style="magenta",
        )
    )

    b = snap.balances
    bal = Table(show_header=False, box=None, pad_edge=False)
    bal.add_row("On-chain confirmed", _sats(b.onchain_confirmed))
    bal.add_row("On-chain unconfirmed", _sats(b.onchain_unconfirmed))
    bal.add_row("Lightning local (outbound)", _sats(snap.total_outbound_sat))
    bal.add_row("Lightning remote (inbound)", _sats(snap.total_inbound_sat))
    console.print(Panel(bal, title="💰 Balances", border_style="green"))

    if not snap.channels:
        console.print("[dim]No channels open.[/]")
        return

    tbl = Table(title=f"📡 Channels ({snap.num_channels})", header_style="dim")
    tbl.add_column("peer", style="blue", no_wrap=True)
    tbl.add_column("capacity", justify="right")
    tbl.add_column("local→ (out)", justify="right")
    tbl.add_column("←remote (in)", justify="right")
    tbl.add_column("balance", justify="center")
    tbl.add_column("up", justify="right")
    tbl.add_column("act", justify="center")

    for c in snap.channels:
        filled = round(c.local_ratio * 10)
        bar = "[green]" + "█" * filled + "[/][dim]" + "█" * (10 - filled) + "[/]"
        tbl.add_row(
            c.peer_pubkey[:16] + "…",
            _sats(c.capacity_sat),
            _sats(c.local_sat),
            _sats(c.remote_sat),
            bar,
            f"{c.uptime_ratio:.0%}",
            "🟢" if c.active else "🔴",
        )
    console.print(tbl)
    console.print(
        f"[dim]Total inbound (receive): {_sats(snap.total_inbound_sat)} · "
        f"outbound (send): {_sats(snap.total_outbound_sat)}[/]"
    )


@app.command()
def snapshot(
    network: Optional[str] = typer.Option(None, help="bitcoin network"),
    host: Optional[str] = typer.Option(None, help="lnd gRPC host:port"),
    json_out: bool = typer.Option(False, "--json", help="raw JSON output"),
) -> None:
    """Read the node's current state and print it (SPEC M0)."""
    settings = _settings_from_opts(network, host)
    try:
        with LndClient(settings) as client:
            snap = collect_snapshot(client)
    except LndClientError as exc:
        err.print(f"[red]error:[/] {exc}")
        raise typer.Exit(code=1)

    if json_out:
        console.print_json(snap.model_dump_json())
    else:
        _render_snapshot(snap)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"advisor {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    _version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True
    ),
) -> None:
    """Lightning Liquidity Advisor."""


def run() -> None:  # console-script entry point
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
