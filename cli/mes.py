"""``tradingagents mes`` — MES intraday trade copilot.

Three subcommands mirror the trading day: ``premarket`` sets a hypothesis,
``check`` answers "is this the right time to take an MES trade?", and ``review``
grades the day. The deterministic checklist in :mod:`tradingagents.mes` decides
every hard threshold; the LLM only adds judgement on top of its verdict.
"""

from __future__ import annotations

import dataclasses
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from tradingagents.agents.mes import (
    create_mes_gatekeeper_agent,
    create_mes_manager_agent,
    create_mes_morning_agent,
    create_mes_review_agent,
)
from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients import create_llm_client
from tradingagents.mes import (
    ChecklistResult,
    LevelDistance,
    MesJournal,
    MesSnapshot,
    ProximityReport,
    SetupState,
    build_proximity,
    build_snapshot,
    evaluate,
    load_mes_config,
    render_checklist,
    render_market_context,
    size_position,
    snapshot_from_csv,
    suggest_stop_distance_points,
)
from tradingagents.mes.stop_quality import build_stop_quality_report
from tradingagents.mes.levels import (
    render_trade_levels_hint,
    suggest_trade_levels_from_snapshot,
)
from tradingagents.mes.management import (
    MgmtReport,
    OpenTrade,
    _r_of,
    _tighter,
    evaluate_management,
)

console = Console()

mes_app = typer.Typer(
    name="mes",
    help="MES intraday trade copilot: premarket hypothesis, live go/no-go checks, EOD review.",
    no_args_is_help=True,
)

MES_TICKER = "/MES"

_VERDICT_STYLE = {"Take": "bold green", "Wait": "bold yellow", "Stand Down": "bold red"}
_TIER_STYLE = {"premium": "bold green", "standard": "green", "marginal": "yellow", "low": "red"}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _market_now(cfg) -> datetime:
    return datetime.now(ZoneInfo(cfg.session_timezone)).replace(tzinfo=None, second=0, microsecond=0)


def _parse_as_of(value: str | None, cfg, *, date: str | None = None) -> datetime:
    """Accept ``HH:MM``, ``YYYY-MM-DD HH:MM``, or a full ISO timestamp.

    Bare times resolve against ``date`` (or today) in the session timezone, which is
    the naive wall-clock convention the snapshot builder expects.
    """
    base = _market_now(cfg)
    if date:
        base = datetime.strptime(date, "%Y-%m-%d").replace(hour=base.hour, minute=base.minute)
    if not value:
        return base

    raw = value.strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return base.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)

    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise typer.BadParameter(
            f"Could not parse '{value}'. Use HH:MM, 'YYYY-MM-DD HH:MM', or an ISO timestamp."
        ) from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo(cfg.session_timezone)).replace(tzinfo=None)
    return parsed.replace(second=0, microsecond=0)


def _make_llm(config: dict, *, deep: bool = False):
    model = config["deep_think_llm"] if deep else config["quick_think_llm"]
    client = create_llm_client(
        provider=config["llm_provider"],
        model=model,
        base_url=config.get("backend_url"),
    )
    return client.get_llm()


def _past_context(config: dict) -> str:
    try:
        return TradingMemoryLog(config).get_past_context(MES_TICKER)
    except Exception as exc:
        console.print(f"[dim]No prior-session context available: {exc}[/dim]")
        return ""


def _load_snapshot(as_of: datetime, cfg, mes_csv: Path | None, spy_csv: Path | None) -> MesSnapshot:
    if bool(mes_csv) != bool(spy_csv):
        raise typer.BadParameter("--mes-csv and --spy-csv must be supplied together.")
    if mes_csv and spy_csv:
        return snapshot_from_csv(mes_csv, spy_csv, as_of, cfg)
    return build_snapshot(as_of, cfg)


def _render_result_table(result: ChecklistResult) -> Table:
    table = Table(title=f"MES Checklist — {result.as_of_label} ({result.side})", expand=True)
    table.add_column("Chart", width=6)
    table.add_column("Check", width=26)
    table.add_column("", width=6, justify="center")
    table.add_column("Observed")
    table.add_column("Threshold")
    table.add_column("W", width=3, justify="right")
    for item in result.all_items:
        mark = "[green]PASS[/green]" if item.passed else "[red]FAIL[/red]"
        table.add_row(
            item.chart,
            item.name,
            mark,
            item.observed,
            item.threshold,
            str(item.weight) if item.weight else "",
        )
    return table


def _print_result(result: ChecklistResult, snapshot: MesSnapshot) -> None:
    console.print(_render_result_table(result))

    tier_style = _TIER_STYLE.get(result.tier, "white")
    gates = "[green]GATES OK[/green]" if result.gates_ok else "[red]GATES X[/red]"
    console.print(
        f"Score [bold]{result.score}/{result.max_score}[/bold]  "
        f"Confirmations [bold]{result.confirmations}/{result.required}[/bold]  "
        f"Tier [{tier_style}]{result.tier}[/{tier_style}]  {gates}  "
        f"SPY confluence {result.spy_confirmations} "
        f"({'ok' if result.spy_confluence_ok else 'insufficient'})"
    )
    if result.divergence != "none":
        console.print(f"[yellow]Divergence: {result.divergence}[/yellow]")
    eff_tick = snapshot.tick_effective_threshold()
    tick_sig = snapshot.tick_signal()
    console.print(
        f"$TICK effective threshold: ±{eff_tick:.0f}  "
        f"(signal: {tick_sig.replace('_', ' ')})"
    )
    vold_div = snapshot.vold_bar_divergence()
    if vold_div != "none":
        console.print(f"[yellow]$VOLD bar divergence: {vold_div}[/yellow]")
    for reason in result.gate_reasons:
        console.print(f"  [red]gate:[/red] {reason}")
    for reason in result.no_trade_reasons:
        console.print(f"  [red]no-trade:[/red] {reason}")
    for warning in snapshot.warnings + result.warnings:
        console.print(f"  [yellow]warning:[/yellow] {warning}")


def _sizing_payload(result: ChecklistResult, cfg, risk: float, stop_points: float | None) -> tuple[dict, str]:
    stop = stop_points or suggest_stop_distance_points(
        result.side, result.last_price, result.vwap, result.atr
    )
    sizing = size_position(risk, stop, result.tier, cfg)
    note = (
        f"Risk ${risk:,.0f} over a {stop:.2f}-point stop at ${cfg.contract_multiplier:.0f}/point "
        f"allows at most {sizing.contracts} contract(s) (capped by {sizing.capped_by})."
    )
    return dataclasses.asdict(sizing), note


def _verdict_headline(verdict_markdown: str, tradeable: bool) -> str:
    """Read the rendered ``**Verdict**:`` line; a failed checklist can never show Take."""
    headline = "Wait"
    for line in verdict_markdown.splitlines():
        if not line.startswith("**Verdict**"):
            continue
        stated = line.split(":", 1)[-1].strip().strip("*").strip()
        headline = next((v for v in _VERDICT_STYLE if v.lower() == stated.lower()), "Wait")
        break
    if not tradeable and headline == "Take":
        return "Stand Down"
    return headline


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@mes_app.command("premarket")
def premarket(
    date: str | None = typer.Option(None, "--date", help="Session date (YYYY-MM-DD). Defaults to today."),
    as_of: str | None = typer.Option(
        None, "--as-of", help="Timestamp to build context from. Defaults to now."
    ),
    no_llm: bool = typer.Option(False, "--no-llm", help="Print market context only; skip the LLM hypothesis."),
    mes_csv: Path | None = typer.Option(None, "--mes-csv", help="Replay from a recorded /MES bar CSV."),
    spy_csv: Path | None = typer.Option(None, "--spy-csv", help="Replay from a recorded SPY bar CSV."),
):
    """Build the opening hypothesis and persist it for the day."""
    cfg = load_mes_config()
    config = DEFAULT_CONFIG.copy()
    stamp = _parse_as_of(as_of, cfg, date=date)

    try:
        snapshot = _load_snapshot(stamp, cfg, mes_csv, spy_csv)
    except Exception as exc:
        console.print(f"[red]Could not build market snapshot:[/red] {exc}")
        raise typer.Exit(code=1)

    market_context = render_market_context(snapshot)
    console.print(Panel(Markdown(market_context), title="Market Context", border_style="cyan"))

    if no_llm:
        return

    try:
        agent = create_mes_morning_agent(_make_llm(config))
        hypothesis = agent(
            market_context=market_context,
            past_context=_past_context(config),
            rth_started=snapshot.rth_started,
        )
    except Exception as exc:
        console.print(f"[red]Hypothesis generation failed:[/red] {exc}")
        raise typer.Exit(code=1)

    console.print(Panel(Markdown(hypothesis), title="Morning Hypothesis", border_style="green"))

    journal = MesJournal(config)
    journal.save_hypothesis(
        date=snapshot.session_date,
        hypothesis_markdown=hypothesis,
        market_context=market_context,
    )
    console.print(f"[dim]Saved to {journal.directory / (snapshot.session_date + '.jsonl')}[/dim]")


@mes_app.command("check")
def check(
    as_of: str | None = typer.Option(None, "--as-of", help="Bar timestamp to evaluate. Defaults to now."),
    date: str | None = typer.Option(None, "--date", help="Session date when --as-of is a bare time."),
    side: str = typer.Option("auto", "--side", help="Evaluate 'long', 'short', or 'auto'."),
    risk: float | None = typer.Option(None, "--risk", help="Dollars at risk for sizing."),
    stop_points: float | None = typer.Option(
        None, "--stop-points", help="Stop distance in points. Derived from VWAP/ATR when omitted."
    ),
    no_llm: bool = typer.Option(False, "--no-llm", help="Deterministic checklist only; skip the gatekeeper."),
    as_json: bool = typer.Option(False, "--json", help="Emit the checklist result as JSON."),
    watch: int | None = typer.Option(None, "--watch", help="Re-run every N seconds until interrupted."),
    no_log: bool = typer.Option(False, "--no-log", help="Do not append this check to the journal."),
    mes_csv: Path | None = typer.Option(None, "--mes-csv", help="Replay from a recorded /MES bar CSV."),
    spy_csv: Path | None = typer.Option(None, "--spy-csv", help="Replay from a recorded SPY bar CSV."),
):
    """Answer: is right now a valid time to take an MES trade?"""
    if side not in {"auto", "long", "short"}:
        raise typer.BadParameter("--side must be one of: auto, long, short")

    cfg = load_mes_config()
    config = DEFAULT_CONFIG.copy()
    journal = MesJournal(config)
    risk_dollars = risk if risk is not None else cfg.default_risk_dollars

    hypothesis_record = journal.load_hypothesis(_parse_as_of(as_of, cfg, date=date).strftime("%Y-%m-%d"))
    hypothesis = (hypothesis_record or {}).get("hypothesis", "")
    past_context = "" if no_llm else _past_context(config)

    gatekeeper = None
    if not no_llm:
        try:
            gatekeeper = create_mes_gatekeeper_agent(_make_llm(config))
        except Exception as exc:
            console.print(f"[yellow]Gatekeeper unavailable, running deterministic only:[/yellow] {exc}")

    while True:
        stamp = _parse_as_of(as_of, cfg, date=date) if as_of else _market_now(cfg)
        try:
            snapshot = _load_snapshot(stamp, cfg, mes_csv, spy_csv)
        except Exception as exc:
            console.print(f"[red]Could not build market snapshot:[/red] {exc}")
            raise typer.Exit(code=1)

        result = evaluate(snapshot, side)
        sizing, sizing_note = _sizing_payload(result, cfg, risk_dollars, stop_points)

        if as_json:
            console.print_json(
                json.dumps(
                    {
                        "as_of": snapshot.as_of.isoformat(),
                        "result": dataclasses.asdict(result),
                        "sizing": sizing,
                        "warnings": snapshot.warnings,
                    },
                    default=str,
                )
            )
        else:
            _print_result(result, snapshot)
            console.print(f"[dim]{sizing_note}[/dim]")

        verdict = ""
        if gatekeeper is not None:
            try:
                levels_hint = suggest_trade_levels_from_snapshot(result.side, snapshot, result)
                verdict = gatekeeper(
                    checklist_markdown=render_checklist(result, live=snapshot.rth_started),
                    market_context=render_market_context(snapshot),
                    hypothesis=hypothesis,
                    past_context=past_context,
                    tradeable=result.tradeable,
                    sizing_note=sizing_note,
                    current_price=snapshot.mes.close,
                    trade_levels_hint=render_trade_levels_hint(levels_hint) if levels_hint else "",
                )
            except Exception as exc:
                console.print(f"[yellow]Gatekeeper call failed:[/yellow] {exc}")

        if verdict and not as_json:
            headline = _verdict_headline(verdict, result.tradeable)
            console.print(
                Panel(
                    Markdown(verdict),
                    title=f"Verdict: {headline}",
                    border_style=_VERDICT_STYLE[headline].split()[-1],
                )
            )
        elif not verdict and not as_json:
            call = "TRADEABLE" if result.tradeable else "STAND DOWN"
            style = "bold green" if result.tradeable else "bold red"
            console.print(Panel(f"[{style}]{call}[/{style}]", title="Deterministic Verdict"))

        if not no_log:
            journal.append_check(
                snapshot=snapshot,
                result=result,
                verdict_markdown=verdict,
                sizing=sizing,
            )

        if not watch:
            break
        try:
            time.sleep(watch)
        except KeyboardInterrupt:
            break


@mes_app.command("review")
def review(
    date: str | None = typer.Option(None, "--date", help="Session date to review. Defaults to today."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Print the day's checks without grading them."),
    no_memory: bool = typer.Option(False, "--no-memory", help="Skip appending the review to the memory log."),
    mes_csv: Path | None = typer.Option(None, "--mes-csv", help="Replay from a recorded /MES bar CSV."),
    spy_csv: Path | None = typer.Option(None, "--spy-csv", help="Replay from a recorded SPY bar CSV."),
):
    """Grade the morning hypothesis against how the session actually traded."""
    cfg = load_mes_config()
    config = DEFAULT_CONFIG.copy()
    journal = MesJournal(config)
    session_date = date or _market_now(cfg).strftime("%Y-%m-%d")

    checks = journal.load_checks(session_date)
    hypothesis_record = journal.load_hypothesis(session_date)
    if not checks and not hypothesis_record and not journal.load_trades(session_date):
        console.print(f"[yellow]No journal entries for {session_date}.[/yellow]")
        available = journal.available_dates()
        if available:
            console.print(f"[dim]Available dates: {', '.join(available[-10:])}[/dim]")
        raise typer.Exit(code=1)

    checks_summary = journal.summarize_checks(session_date)
    trades_summary = journal.summarize_trades(session_date)
    open_trade = journal.find_open_trade(session_date)
    if open_trade is not None:
        console.print(
            "[yellow]Warning: the trade opened "
            f"{open_trade.entry_time:%H:%M} is still marked open for {session_date}; "
            "grading assumes an EOD flatten.[/yellow]"
        )
    console.print(Panel(Markdown(checks_summary), title=f"Checks — {session_date}", border_style="cyan"))
    if trades_summary and not trades_summary.startswith("No trades"):
        console.print(Panel(Markdown(trades_summary), title="Trades", border_style="green"))

    stop_quality = build_stop_quality_report(
        journal.load_trades(session_date), checks, cfg
    )
    if not stop_quality.is_empty():
        stop_lines = ["- " + line for line in stop_quality.summary_lines()]
        if not stop_lines:
            stop_lines = ["- No stop-quality flags on closed trades."]
        console.print(Panel(Markdown("\n".join(stop_lines)), title="Stop Quality", border_style="yellow"))

    hour, minute = divmod(int(cfg.exit_time.replace(":", "")), 100)
    close_stamp = datetime.strptime(session_date, "%Y-%m-%d").replace(hour=hour, minute=minute)
    outcome_summary = "Session bars unavailable."
    session_change = 0.0
    try:
        snapshot = _load_snapshot(close_stamp, cfg, mes_csv, spy_csv)
        outcome_summary = render_market_context(snapshot)
        session_change = snapshot.mes.session_change()
    except Exception as exc:
        console.print(f"[yellow]Could not load closing bars:[/yellow] {exc}")

    console.print(Panel(Markdown(outcome_summary), title="Session Outcome", border_style="magenta"))

    if no_llm:
        return

    hypothesis = (hypothesis_record or {}).get("hypothesis", "No hypothesis was recorded this morning.")
    try:
        agent = create_mes_review_agent(_make_llm(config))
        review_markdown = agent(
            hypothesis=hypothesis,
            checks_summary=checks_summary,
            outcome_summary=outcome_summary,
            trades_summary=trades_summary,
        )
    except Exception as exc:
        console.print(f"[red]Review generation failed:[/red] {exc}")
        raise typer.Exit(code=1)

    console.print(Panel(Markdown(review_markdown), title="Session Review", border_style="green"))

    if no_memory:
        return

    memory_log = TradingMemoryLog(config)
    inserted = memory_log.append_imported_resolved_entry(
        ticker=MES_TICKER,
        trade_date=session_date,
        rating="Buy" if session_change >= 0 else "Sell",
        raw_return=session_change,
        reflection=review_markdown,
        decision_summary=hypothesis,
        technical_summary=outcome_summary,
        import_fingerprint=f"mes-review-{session_date}",
    )
    if inserted:
        console.print("[green]Review appended to the memory log.[/green]")
    else:
        console.print("[dim]Review already present in the memory log; skipped.[/dim]")


# ---------------------------------------------------------------------------
# Radar helpers
# ---------------------------------------------------------------------------

_STATE_STYLE: dict[SetupState, str] = {
    SetupState.READY: "bold green",
    SetupState.CONFLUENCE_OK_WAITING_LOCATION: "bold cyan",
    SetupState.AT_LEVEL_MISSING_CONFLUENCE: "bold yellow",
    SetupState.BUILDING: "white",
    SetupState.BLOCKED: "bold red",
    SetupState.GATES_CLOSED: "dim",
}

_STATE_LABEL: dict[SetupState, str] = {
    SetupState.READY: "READY — run mes check",
    SetupState.CONFLUENCE_OK_WAITING_LOCATION: "WAITING LOCATION — valid patience",
    SetupState.AT_LEVEL_MISSING_CONFLUENCE: "AT LEVEL — confluence missing",
    SetupState.BUILDING: "BUILDING — signals forming",
    SetupState.BLOCKED: "BLOCKED — no-trade conditions",
    SetupState.GATES_CLOSED: "GATES CLOSED",
}


def _level_row(ld: LevelDistance) -> Text:
    sign = "+" if ld.distance >= 0 else ""
    dist_str = f"{sign}{ld.distance:+.2f} pts"
    flag = " [dim](cleared)[/dim]" if ld.cleared else ""
    band_mark = " [bold yellow]◄[/bold yellow]" if ld.within_band else ""
    return Text.from_markup(
        f"  {ld.price:.2f}  {ld.label:<22} {dist_str}{flag}{band_mark}"
    )


def _render_radar(report: ProximityReport, as_of: datetime) -> Table:
    """Build a compact Rich Table displaying the proximity report."""
    state_style = _STATE_STYLE[report.state]
    state_label = _STATE_LABEL[report.state]
    tier_style = _TIER_STYLE.get(report.tier, "white")

    outer = Table.grid(padding=(0, 1))

    # ---- Header row ----
    header = (
        f"[bold]/MES {report.price:.2f}[/bold]  "
        f"{report.side.upper()}  "
        f"[{tier_style}]{report.tier}[/{tier_style}]  "
        f"Score [bold]{report.score}/{report.max_score}[/bold]  "
        f"Conf [bold]{report.confirmations}/{report.required}[/bold]  "
        f"SPY [bold]{report.spy_confirmations}/5[/bold]  "
        f"Band ±{report.proximity_band:.1f} pts  "
        f"[dim]{as_of.strftime('%H:%M')}[/dim]"
    )
    outer.add_row(Text.from_markup(header))

    # ---- State banner ----
    outer.add_row(Text.from_markup(f"[{state_style}]{state_label}[/{state_style}]"))

    # ---- Gates / blockers ----
    if not report.gates_ok:
        for r in report.gate_reasons:
            outer.add_row(Text.from_markup(f"  [dim]gate:[/dim] {r}"))
    if report.no_trade_reasons:
        for r in report.no_trade_reasons:
            outer.add_row(Text.from_markup(f"  [red]blocked:[/red] {r}"))

    # ---- Missing items ----
    if report.missing_items and not report.tradeable:
        items_txt = ", ".join(report.missing_items[:4])
        more = len(report.missing_items) - 4
        suffix = f" (+{more} more)" if more > 0 else ""
        outer.add_row(Text.from_markup(f"  [yellow]need:[/yellow] {items_txt}{suffix}"))

    # ---- Level tape ----
    if report.levels_above or report.levels_below:
        outer.add_row(Text(""))
        outer.add_row(Text.from_markup("  [bold]Levels above[/bold]"))
        if report.levels_above:
            for ld in report.levels_above:
                outer.add_row(_level_row(ld))
        else:
            outer.add_row(Text("    (none in range)"))
        outer.add_row(Text.from_markup(f"  [bold dim]── price {report.price:.2f} ──[/bold dim]"))
        outer.add_row(Text.from_markup("  [bold]Levels below[/bold]"))
        if report.levels_below:
            for ld in report.levels_below:
                outer.add_row(_level_row(ld))
        else:
            outer.add_row(Text("    (none in range)"))

    return outer


# ---------------------------------------------------------------------------
# Radar command
# ---------------------------------------------------------------------------


@mes_app.command("radar")
def radar(
    side: str = typer.Option("auto", "--side", help="Evaluate 'long', 'short', or 'auto'."),
    watch: int = typer.Option(60, "--watch", help="Re-run every N seconds. Use --no-watch for one-shot."),
    no_watch: bool = typer.Option(False, "--no-watch", help="Run once then exit."),
    within: float | None = typer.Option(
        None, "--within", help="Proximity band in points (default: min(4.0, 0.5×ATR))."
    ),
    alert: bool = typer.Option(
        False, "--alert", help="Print a bell character when state is READY or AT_LEVEL."
    ),
    as_of: str | None = typer.Option(None, "--as-of", help="Bar timestamp to evaluate. Defaults to now."),
    date: str | None = typer.Option(None, "--date", help="Session date when --as-of is a bare time."),
    as_json: bool = typer.Option(False, "--json", help="Emit ProximityReport as JSON then exit."),
    mes_csv: Path | None = typer.Option(None, "--mes-csv", help="Replay from a recorded /MES bar CSV."),
    spy_csv: Path | None = typer.Option(None, "--spy-csv", help="Replay from a recorded SPY bar CSV."),
):
    """Compact live proximity view: how close is a valid MES trade entry?

    Runs without the LLM gatekeeper and does not write to the journal.
    When state is READY or AT_LEVEL_MISSING_CONFLUENCE, run `mes check` for the
    full gatekeeper verdict.

    Examples:

        tradingagents mes radar

        tradingagents mes radar --watch 15 --within 3 --side long --alert

        tradingagents mes radar --no-watch --json
    """
    if side not in {"auto", "long", "short"}:
        raise typer.BadParameter("--side must be one of: auto, long, short")

    cfg = load_mes_config()
    interval = watch if not no_watch else 0

    _ALERT_STATES = {SetupState.READY, SetupState.AT_LEVEL_MISSING_CONFLUENCE}

    import dataclasses as _dc

    def _one_shot(stamp: datetime) -> ProximityReport:
        snapshot = _load_snapshot(stamp, cfg, mes_csv, spy_csv)
        result = evaluate(snapshot, side)
        return build_proximity(snapshot, result, proximity_band=within)

    if as_json:
        stamp = _parse_as_of(as_of, cfg, date=date) if as_of else _market_now(cfg)
        try:
            report = _one_shot(stamp)
        except Exception as exc:
            console.print(f"[red]Snapshot failed:[/red] {exc}")
            raise typer.Exit(code=1)
        import dataclasses as _dc2
        console.print_json(
            __import__("json").dumps(_dc2.asdict(report), default=str)
        )
        return

    if no_watch or interval == 0:
        stamp = _parse_as_of(as_of, cfg, date=date) if as_of else _market_now(cfg)
        try:
            report = _one_shot(stamp)
        except Exception as exc:
            console.print(f"[red]Snapshot failed:[/red] {exc}")
            raise typer.Exit(code=1)
        console.print(Panel(_render_radar(report, stamp), title="MES Radar", border_style="blue"))
        if alert and report.state in _ALERT_STATES:
            console.print("\a", end="")
        return

    # ---- Watch loop with Rich Live ----
    prev_state: SetupState | None = None
    with Live(console=console, refresh_per_second=1, screen=False) as live:
        while True:
            stamp = _parse_as_of(as_of, cfg, date=date) if as_of else _market_now(cfg)
            try:
                report = _one_shot(stamp)
                panel = Panel(_render_radar(report, stamp), title="MES Radar", border_style="blue")
                live.update(panel)
                if alert and report.state in _ALERT_STATES and report.state != prev_state:
                    console.print("\a", end="")
                prev_state = report.state
            except Exception as exc:
                live.update(Panel(f"[red]Snapshot error:[/red] {exc}", border_style="red"))

            try:
                __import__("time").sleep(interval)
            except KeyboardInterrupt:
                break


# ---------------------------------------------------------------------------
# Trade management
# ---------------------------------------------------------------------------

trade_app = typer.Typer(name="trade", help="Declare and manage an open /MES position.")
mes_app.add_typer(trade_app, name="trade")


def _trade_journal(cfg, journal_dir: Path | None):
    """Journal for trade commands; --journal-dir (hidden) keeps tests hermetic.

    The default branch must use DEFAULT_CONFIG, not ``cfg.to_dict()``: the
    checklist config carries neither ``mes_journal_dir`` nor ``results_dir``,
    so MesJournal would fall back to a CWD-relative ``./mes_journal`` that
    ``mes review`` (built from DEFAULT_CONFIG) never reads. DEFAULT_CONFIG
    derives ``<results_dir>/mes_journal``, matching the sibling commands.
    """
    if journal_dir is not None:
        return MesJournal(cfg.to_dict() | {"mes_journal_dir": str(journal_dir)})
    return MesJournal(DEFAULT_CONFIG.copy())


def _render_mgmt_panel(trade: OpenTrade, report: MgmtReport, price: float) -> Panel:
    grid = Table.grid(padding=(0, 1))
    grid.add_row(Text.from_markup(
        f"[bold]{trade.side.upper()} {trade.contracts} @ {trade.entry:.2f}[/bold]  "
        f"entry {trade.entry_time:%H:%M}"
    ))
    grid.add_row(Text.from_markup(
        f"Now [bold]{price:.2f}[/bold]  "
        f"[{'green' if report.r_now >= 0 else 'red'}]{report.r_now:+.2f}R[/{'green' if report.r_now >= 0 else 'red'}]  "
        f"MFE {report.mfe_r:+.2f}R  MAE {report.mae_r:+.2f}R"
    ))
    grid.add_row(Text.from_markup(
        f"PLAN  stop {report.stop:.2f}  target {report.target if report.target is not None else '-'}"
    ))
    grid.add_row(Text.from_markup(f"NEXT  {report.next_event or '-'}"))
    body = Text("")
    for event in report.events:
        body.append(f"✓ {event.name}: {event.detail}\n", style="green")
    for reason in report.reasons:
        body.append(f"· {reason}\n", style="yellow")
    return Panel(Group(grid, body) if body.plain else grid,
                 title="MES Trade Manager", border_style="blue")


@trade_app.command("enter")
def trade_enter(
    side: str = typer.Option(..., "--side", help="'long' or 'short'."),
    contracts: int = typer.Option(1, "--contracts", min=1),
    entry: float | None = typer.Option(None, "--entry", help="Fill price. Defaults to the latest close."),
    stop: float | None = typer.Option(None, "--stop", help="Initial stop. Defaults to the structural suggestion."),
    target: float | None = typer.Option(None, "--target"),
    as_of: str | None = typer.Option(None, "--as-of"),
    date: str | None = typer.Option(None, "--date"),
    journal_dir: Path | None = typer.Option(None, "--journal-dir", hidden=True),
    mes_csv: Path | None = typer.Option(None, "--mes-csv"),
    spy_csv: Path | None = typer.Option(None, "--spy-csv"),
    force: bool = typer.Option(False, "--force", help="Override the single-open-trade guard."),
):
    """Declare a filled entry; the copilot starts managing it."""
    if side not in {"long", "short"}:
        raise typer.BadParameter("--side must be 'long' or 'short'")
    cfg = load_mes_config()
    journal = _trade_journal(cfg, journal_dir)
    stamp = _parse_as_of(as_of, cfg, date=date) if as_of else _market_now(cfg)

    existing = journal.find_open_trade(stamp.strftime("%Y-%m-%d"))
    if existing is not None and not force:
        console.print("[red]A trade is already open for this date. Close it first.[/red]")
        raise typer.Exit(code=1)

    try:
        snapshot = _load_snapshot(stamp, cfg, mes_csv, spy_csv)
        result = evaluate(snapshot, side)
    except Exception as exc:
        console.print(f"[red]Could not build snapshot:[/red] {exc}")
        raise typer.Exit(code=1)

    entry_px = entry if entry is not None else snapshot.mes.close
    levels = suggest_trade_levels_from_snapshot(side, snapshot, result)
    if stop is not None:
        stop_px = stop
        stop_anchor = "manual"
    elif levels is not None:
        stop_px = levels.stop_level
        stop_anchor = levels.level_labels.get(levels.stop_level, "structural level")
    else:
        stop_distance = suggest_stop_distance_points(side, entry_px, result.vwap, result.atr)
        stop_px = entry_px - stop_distance if side == "long" else entry_px + stop_distance
        stop_anchor = "vwap_atr_distance"
    target_px = target if target is not None else (levels.first_target if levels else None)

    trade = OpenTrade(
        side=side,
        contracts=contracts,
        remaining=contracts,
        entry=round(entry_px, 2),
        stop=round(stop_px, 2),
        initial_stop=round(stop_px, 2),
        target=target_px,
        entry_time=snapshot.as_of,
        initial_risk_points=round(abs(entry_px - stop_px), 4),
    )
    if trade.initial_risk_points <= 0 or (side == "long" and stop_px >= entry_px) or (side == "short" and stop_px <= entry_px):
        raise typer.BadParameter("stop must be strictly beyond the entry price on the trade's side")

    journal.append_trade_opened(trade, entry_context={
        "score": result.score,
        "tier": result.tier,
        "confirmations": result.confirmations,
        "spy_confirmations": result.spy_confirmations,
        "side": side,
        "atr": result.atr,
        "vwap_distance": round(abs(entry_px - result.vwap), 4),
        "stop_distance_points": round(abs(entry_px - stop_px), 4),
        "stop_atr_multiple": round(abs(entry_px - stop_px) / result.atr, 4) if result.atr > 0 else None,
        "stop_anchor": stop_anchor,
    })
    stop_suffix = f" ({stop_anchor})" if stop_anchor != "manual" else ""
    console.print(
        f"[bold green]Trade opened[/bold green]: {side} {contracts} @ {entry_px:.2f}, "
        f"stop {stop_px:.2f}{stop_suffix}, target {target if target is not None else '-'} "
        f"(1R = {trade.initial_risk_points:.2f} pts)"
    )


@trade_app.command("status")
def trade_status(
    watch: int = typer.Option(15, "--watch"),
    no_watch: bool = typer.Option(False, "--no-watch"),
    no_llm: bool = typer.Option(False, "--no-llm"),
    as_json: bool = typer.Option(False, "--json"),
    alert: bool = typer.Option(False, "--alert"),
    date: str | None = typer.Option(None, "--date"),
    as_of: str | None = typer.Option(None, "--as-of"),
    journal_dir: Path | None = typer.Option(None, "--journal-dir", hidden=True),
    mes_csv: Path | None = typer.Option(None, "--mes-csv"),
    spy_csv: Path | None = typer.Option(None, "--spy-csv"),
):
    """Live management view of the open trade."""
    cfg = load_mes_config()
    journal = _trade_journal(cfg, journal_dir)
    manager = None
    if not no_llm:
        try:
            manager = create_mes_manager_agent(_make_llm(DEFAULT_CONFIG.copy()))
        except Exception as exc:
            console.print(f"[yellow]Manager unavailable, mechanical only:[/yellow] {exc}")
            manager = None

    def _one_shot(stamp):
        trade = journal.find_open_trade(stamp.strftime("%Y-%m-%d"))
        if trade is None:
            return None
        snapshot = _load_snapshot(stamp, cfg, mes_csv, spy_csv)
        result = evaluate(snapshot, trade.side)
        updated, report = evaluate_management(snapshot, result, trade, cfg)
        return snapshot, updated, report

    if no_watch or as_json:
        stamp = _parse_as_of(as_of, cfg, date=date) if as_of else _market_now(cfg)
        shot = _one_shot(stamp)
        if shot is None:
            console.print("[yellow]No open trade for this date. Run `mes trade enter` first.[/yellow]")
            raise typer.Exit(code=1)
        snapshot, updated, report = shot
        if as_json:
            console.print_json(json.dumps({
                "as_of": snapshot.as_of.isoformat(),
                "trade": dataclasses.asdict(updated),
                "report": dataclasses.asdict(report),
            }, default=str))
            return
        console.print(_render_mgmt_panel(updated, report, snapshot.mes.close))
        return

    # Watch loop (same skeleton as radar).
    with Live(console=console, refresh_per_second=1, screen=False) as live:
        seen: set[str] = set()
        while True:
            stamp = _market_now(cfg)
            try:
                shot = _one_shot(stamp)
                if shot is None:
                    live.update(Panel("[yellow]No open trade.[/yellow]", title="MES Trade Manager"))
                else:
                    snapshot, updated, report = shot
                    live.update(_render_mgmt_panel(updated, report, snapshot.mes.close))
                    # Persist each ladder event exactly once; advisory LLM text
                    # below never mutates state.
                    for event in report.events:
                        key = f"{event.name}@{event.as_of.isoformat(timespec='minutes')}"
                        if key in seen:
                            continue
                        seen.add(key)
                        journal.append_trade_adjusted(
                            stamp.strftime("%Y-%m-%d"),
                            stop=updated.stop,
                            note=f"{event.name}: {event.detail}",
                            as_of=event.as_of.isoformat(timespec="minutes"),
                        )
                        if alert and event.name in {"stopped_out", "target", "time_stop"}:
                            console.print("\a")
                    if manager is not None:
                        try:
                            advisory = manager(
                                mgmt_summary=(
                                    f"{updated.side} {updated.contracts} @ {updated.entry:.2f} | "
                                    f"{report.r_now:+.2f}R | stop {report.stop:.2f} | "
                                    f"next: {report.next_event or 'closed'}"
                                ),
                                market_context=render_market_context(snapshot),
                                current_price=snapshot.mes.close,
                            )
                            live.update(Panel(Markdown(advisory), title="Manager Advisory",
                                              border_style="dim"))
                        except Exception as exc:
                            console.print(f"[yellow]Manager call failed:[/yellow] {exc}")
            except Exception as exc:
                live.update(Panel(f"[red]Snapshot failed:[/red] {exc}", title="MES Trade Manager"))
            try:
                time.sleep(watch)
            except KeyboardInterrupt:
                break


@trade_app.command("close")
def trade_close(
    price: float = typer.Option(..., "--price"),
    reason: str = typer.Option("manual", "--reason", help="manual | stop | target | eod"),
    date: str | None = typer.Option(None, "--date"),
    as_of: str | None = typer.Option(None, "--as-of"),
    journal_dir: Path | None = typer.Option(None, "--journal-dir", hidden=True),
    mes_csv: Path | None = typer.Option(None, "--mes-csv"),
    spy_csv: Path | None = typer.Option(None, "--spy-csv"),
):
    """Record the exit for the open trade (execution stays in TOS)."""
    cfg = load_mes_config()
    journal = _trade_journal(cfg, journal_dir)
    stamp = _parse_as_of(as_of, cfg, date=date) if as_of else _market_now(cfg)
    session_date = stamp.strftime("%Y-%m-%d")
    trade = journal.find_open_trade(session_date)
    if trade is None:
        console.print("[yellow]No open trade for this date.[/yellow]")
        raise typer.Exit(code=1)

    snapshot = _load_snapshot(stamp, cfg, mes_csv, spy_csv)
    result = evaluate(snapshot, trade.side)
    updated, report = evaluate_management(snapshot, result, trade, cfg)
    if updated.remaining > 0:
        updated.realized_r = round(
            updated.realized_r + updated.remaining * _r_of(price, updated), 4
        )
        updated.remaining = 0

    journal.append_trade_closed(updated, exit_price=price, reason=reason, as_of=stamp)
    console.print(
        f"[bold green]Closed[/bold green] {updated.side} @ {price:.2f} ({reason}) — "
        f"realized {updated.realized_r:+.2f}R, MFE {report.mfe_r:+.2f}R, MAE {report.mae_r:+.2f}R"
    )


@trade_app.command("adjust")
def trade_adjust(
    stop: float | None = typer.Option(None, "--stop", help="New stop level (tightens only)."),
    note: str | None = typer.Option(None, "--note", help="Freeform manual note."),
    journal_dir: Path | None = typer.Option(None, "--journal-dir", hidden=True),
):
    """Record a manual adjustment you made at the broker."""
    cfg = load_mes_config()
    journal = _trade_journal(cfg, journal_dir)
    stamp = _market_now(cfg)
    trade = journal.find_open_trade(stamp.strftime("%Y-%m-%d"))
    if trade is None:
        console.print("[yellow]No open trade to adjust.[/yellow]")
        raise typer.Exit(code=1)
    if stop is not None:
        tightened = _tighter(trade.stop, stop, trade.side)
        if tightened != stop:
            console.print("[yellow]Refusing to loosen the stop; kept the tighter level.[/yellow]")
        journal.append_trade_adjusted(
            stamp.strftime("%Y-%m-%d"), stop=tightened, note=note or "manual stop move"
        )
        console.print(f"[green]Stop adjusted to {tightened:.2f}.[/green]")
    elif note:
        journal.append_trade_adjusted(stamp.strftime("%Y-%m-%d"), note=note)
        console.print(f"[green]Noted:[/green] {note}")

