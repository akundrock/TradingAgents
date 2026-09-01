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
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from tradingagents.agents.mes import (
    create_mes_gatekeeper_agent,
    create_mes_morning_agent,
    create_mes_review_agent,
)
from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients import create_llm_client
from tradingagents.mes import (
    ChecklistResult,
    MesJournal,
    MesSnapshot,
    build_snapshot,
    evaluate,
    load_mes_config,
    render_checklist,
    render_market_context,
    size_position,
    snapshot_from_csv,
    suggest_stop_points,
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
    stop = stop_points or suggest_stop_points(
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
                verdict = gatekeeper(
                    checklist_markdown=render_checklist(result, live=snapshot.rth_started),
                    market_context=render_market_context(snapshot),
                    hypothesis=hypothesis,
                    past_context=past_context,
                    tradeable=result.tradeable,
                    sizing_note=sizing_note,
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
    if not checks and not hypothesis_record:
        console.print(f"[yellow]No journal entries for {session_date}.[/yellow]")
        available = journal.available_dates()
        if available:
            console.print(f"[dim]Available dates: {', '.join(available[-10:])}[/dim]")
        raise typer.Exit(code=1)

    checks_summary = journal.summarize_checks(session_date)
    console.print(Panel(Markdown(checks_summary), title=f"Checks — {session_date}", border_style="cyan"))

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
