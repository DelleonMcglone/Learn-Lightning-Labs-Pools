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
from .collectors.market import collect_market
from .config import Settings
from .lndclient import LndClient, LndClientError
from .models import MarketSnapshot, NodeSnapshot
from .recommend import RecommendationReport, recommend as run_recommend
from .signals import NodeSignals, compute_signals

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


def _render_signals(sig: NodeSignals) -> None:
    console.print(
        Panel(
            f"inbound [green]{_sats(sig.total_inbound_sat)}[/] · "
            f"outbound [green]{_sats(sig.total_outbound_sat)}[/] · "
            f"inbound share [b]{sig.inbound_ratio:.0%}[/]\n"
            f"{sig.channels_considered}/{sig.channels_total} channels "
            f"considered for outlier analysis · "
            f"{sig.channels_one_sided} one-sided · "
            f"IQR multiplier {sig.outlier_multiplier} · "
            f"forwarding lookback {sig.forwarding_lookback_days}d",
            title="📊 Node signals",
            border_style="cyan",
        )
    )

    tbl = Table(title="Per-channel signals", header_style="dim")
    tbl.add_column("peer", style="blue", no_wrap=True)
    tbl.add_column("local%", justify="right")
    tbl.add_column("imbal", justify="right")
    tbl.add_column("up%", justify="right")
    tbl.add_column("fwd in/out", justify="right")
    tbl.add_column("fees (msat)", justify="right")
    tbl.add_column("rev/cap-day", justify="right")
    tbl.add_column("flags")

    for s in sig.channels:
        flags = []
        if not s.considered:
            flags.append(f"[dim]excluded: {s.excluded_reason}[/]")
        if s.one_sided:
            side = "no inbound" if s.local_ratio > 0.5 else "no outbound"
            flags.append(f"[yellow]one-sided ({side})[/]")
        if s.revenue_outlier_low:
            flags.append("[red]revenue outlier ↓[/]")
        if s.volume_outlier_low:
            flags.append("[red]volume outlier ↓[/]")
        if s.uptime_outlier_low:
            flags.append("[red]uptime outlier ↓[/]")
        tbl.add_row(
            s.peer_pubkey[:16] + "…",
            f"{s.local_ratio:.0%}",
            f"{s.imbalance:.2f}",
            f"{s.uptime_ratio:.0%}",
            f"{s.forwards_in}/{s.forwards_out}",
            f"{s.fees_earned_msat:,}",
            f"{s.revenue_per_capacity_day:.3g}",
            " ".join(flags) or "[green]ok[/]",
        )
    console.print(tbl)


@app.command()
def signals(
    network: Optional[str] = typer.Option(None, help="bitcoin network"),
    host: Optional[str] = typer.Option(None, help="lnd gRPC host:port"),
    multiplier: float = typer.Option(
        3.0, help="IQR outlier multiplier (1.5 aggressive, 3 cautious)"
    ),
    json_out: bool = typer.Option(False, "--json", help="raw JSON output"),
) -> None:
    """Compute deterministic liquidity signals from the node (SPEC M1)."""
    settings = _settings_from_opts(network, host)
    try:
        with LndClient(settings) as client:
            snap = collect_snapshot(client)
    except LndClientError as exc:
        err.print(f"[red]error:[/] {exc}")
        raise typer.Exit(code=1)

    sig = compute_signals(snap, outlier_multiplier=multiplier)
    if json_out:
        console.print_json(sig.model_dump_json())
    else:
        _render_signals(sig)


def _render_market(m: MarketSnapshot, quote_amt: int) -> None:
    # Fee environment
    if m.fees.available:
        fees = " · ".join(
            f"{t}blk [b]{v:g}[/] sat/vB"
            for t, v in sorted(m.fees.sat_per_vb.items())
        )
        console.print(Panel(fees, title="⛓️ Fee environment", border_style="yellow"))
    else:
        console.print(Panel("[dim]unavailable[/]", title="⛓️ Fee environment"))

    # Pool
    if m.pool.connected:
        lines = [
            f"execution fee: {m.pool.exec_fee_base_sat} sat + "
            f"{m.pool.exec_fee_rate_ppm} ppm · next batch "
            f"{m.pool.next_batch_feerate_sat_kw} sat/kw"
        ]
        tbl = Table(header_style="dim", box=None)
        tbl.add_column("duration"); tbl.add_column("state")
        tbl.add_column("asks/bids", justify="right")
        tbl.add_column("units a/b", justify="right")
        tbl.add_column("last clear", justify="right")
        durations = sorted(set(m.pool.lease_durations) | set(m.pool.depth))
        for d in durations:
            dep = m.pool.depth.get(d)
            rate = m.pool.last_clearing_rate_ppb.get(d)
            apr = f" ≈{rate * 52_560 / 1e7:.1f}%" if rate else ""
            tbl.add_row(
                f"{d:,} blk",
                m.pool.lease_durations.get(d, "—"),
                f"{dep.asks}/{dep.bids}" if dep else "—",
                f"{dep.ask_units}/{dep.bid_units}" if dep else "—",
                f"{rate:,} ppb{apr}" if rate else "—",
            )
        console.print(Panel(tbl, title="🏊 Pool market", border_style="magenta",
                            subtitle=lines[0]))
    else:
        console.print(Panel("[dim]poold not reachable — market rules will "
                            "skip[/]", title="🏊 Pool market"))

    # Loop
    if m.loop.connected:
        tbl = Table(header_style="dim", box=None)
        tbl.add_column("direction"); tbl.add_column("range", justify="right")
        tbl.add_column(f"quote @ {quote_amt:,}", justify="right")
        tbl.add_column("effective", justify="right")
        for label, lo, hi, q in (
            ("Loop Out (buy inbound)", m.loop.out_min_sat, m.loop.out_max_sat,
             m.loop.out_quote),
            ("Loop In (buy outbound)", m.loop.in_min_sat, m.loop.in_max_sat,
             m.loop.in_quote),
        ):
            eff = (f"{q.total_fee_sat / q.amount_sat:.2%}"
                   if q and q.amount_sat else "—")
            tbl.add_row(
                label,
                f"{lo:,}–{hi:,}",
                f"{q.total_fee_sat:,} sat" if q else "—",
                eff,
            )
        console.print(Panel(tbl, title="🔄 Loop market", border_style="green"))
    else:
        console.print(Panel("[dim]loopd not reachable — swap rules will "
                            "skip[/]", title="🔄 Loop market"))


@app.command()
def market(
    network: Optional[str] = typer.Option(None, help="bitcoin network"),
    host: Optional[str] = typer.Option(None, help="lnd gRPC host:port"),
    json_out: bool = typer.Option(False, "--json", help="raw JSON output"),
) -> None:
    """Collect live market + fee state: mempool fees, Pool auction, Loop
    quotes (SPEC M2). Each source degrades independently."""
    settings = _settings_from_opts(network, host)
    m = collect_market(settings)
    if json_out:
        console.print_json(m.model_dump_json())
    else:
        _render_market(m, settings.quote_amount_sat)


_SEV_STYLE = {
    "CRITICAL": "bold red",
    "HIGH": "red",
    "MEDIUM": "yellow",
    "LOW": "cyan",
    "INFO": "dim",
}


def _render_report(report: RecommendationReport, top: int) -> None:
    recs = report.recommendations
    if not recs:
        console.print(Panel(
            "[green]No recommendations — liquidity looks healthy for now.[/]",
            title="✅ All clear",
        ))
    shown = recs if top <= 0 else recs[:top]
    for i, r in enumerate(shown, 1):
        style = _SEV_STYLE.get(r.severity.name, "white")
        body = [r.summary]
        econ_bits = []
        if r.est_cost_sat is not None:
            econ_bits.append(f"est. cost [b]{r.est_cost_sat:,} sat[/]")
        if r.est_benefit:
            econ_bits.append(f"benefit: {r.est_benefit}")
        if econ_bits:
            body.append(" · ".join(econ_bits))
        if r.command:
            body.append(f"[bold white on grey23] {r.command} [/]")
        for c in r.caveats:
            body.append(f"[dim]⚠ {c}[/]")
        console.print(Panel(
            "\n\n".join(body),
            title=f"#{i} [{r.severity.name}] {r.title}  [dim]({r.rule})[/]",
            border_style=style,
        ))
    if len(recs) > len(shown):
        console.print(
            f"[dim]…{len(recs) - len(shown)} more — use --all to show "
            "everything.[/]"
        )
    if report.skipped_rules:
        notes = " · ".join(
            f"{k}: {v}" for k, v in report.skipped_rules.items()
        )
        console.print(f"[dim]skipped: {notes}[/]")


@app.command()
def recommend(
    network: Optional[str] = typer.Option(None, help="bitcoin network"),
    host: Optional[str] = typer.Option(None, help="lnd gRPC host:port"),
    offline: bool = typer.Option(
        True, "--offline/--llm",
        help="offline = deterministic engine only (LLM layer lands in M4)",
    ),
    show_all: bool = typer.Option(
        False, "--all", help="show every recommendation, not just the top 3"
    ),
    multiplier: float = typer.Option(3.0, help="IQR outlier multiplier"),
    json_out: bool = typer.Option(False, "--json", help="raw JSON output"),
) -> None:
    """Produce ranked, plain-language liquidity recommendations with computed
    economics and ready-to-run commands (SPEC M3)."""
    if not offline:
        err.print("[yellow]LLM mode is milestone M4 — running offline.[/]")
    settings = _settings_from_opts(network, host)
    try:
        with LndClient(settings) as client:
            snap = collect_snapshot(client)
    except LndClientError as exc:
        err.print(f"[red]error:[/] {exc}")
        raise typer.Exit(code=1)

    sig = compute_signals(snap, outlier_multiplier=multiplier)
    market = collect_market(settings)
    report = run_recommend(snap, sig, market)

    if json_out:
        console.print_json(report.model_dump_json())
    else:
        console.print(Panel(
            f"[b]{report.node_alias}[/] · deterministic engine "
            f"(offline) · {len(report.recommendations)} recommendation(s)",
            title="🧭 Liquidity Advisor", border_style="magenta",
        ))
        _render_report(report, top=0 if show_all else 3)


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
