from __future__ import annotations

import datetime
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from rich import box
from rich.layout import Layout
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from cli.display_common import (
    ANALYST_AGENT_NAMES,
    ANALYST_ORDER,
    PREMARKET_RESEARCH_AGENTS,
    REPORT_SECTION_TITLES,
    update_analyst_statuses,
    update_research_status_from_chunk,
)
from tradingagents.intraday.session import IntradaySignal, SymbolScanState, TradingSession

DETAIL_MAX_LINES = 40
LOG_MAX_LINES = 30
SIGNAL_MAX_LINES = 8


@dataclass
class SymbolPremarketState:
    selected_analysts: list[str] = field(default_factory=list)
    agent_status: dict[str, str] = field(default_factory=dict)
    report_sections: dict[str, str | None] = field(default_factory=dict)


class DashboardLogHandler(logging.Handler):
    """Capture log records for the intraday dashboard tail panel."""

    def __init__(self, buffer: IntradayDashboardBuffer, *, max_lines: int = LOG_MAX_LINES):
        super().__init__()
        self.buffer = buffer
        self.max_lines = max_lines

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith("tradingagents"):
            return
        timestamp = datetime.datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        message = self.format(record)
        self.buffer.add_log(timestamp, record.levelname, message)


class IntradayDashboardBuffer:
    def __init__(
        self,
        *,
        session: TradingSession,
        strategy_name: str,
        on_refresh: Callable[[], None] | None = None,
    ):
        self.session = session
        self.strategy_name = strategy_name
        self._on_refresh = on_refresh
        self._lock = threading.Lock()
        self.log_lines: deque[tuple[str, str, str]] = deque(maxlen=LOG_MAX_LINES)
        self.signals: deque[IntradaySignal] = deque(maxlen=20)
        self.premarket_by_symbol: dict[str, SymbolPremarketState] = {}
        self.premarket_active = False

    def set_refresh_callback(self, callback: Callable[[], None] | None) -> None:
        self._on_refresh = callback

    def _notify(self) -> None:
        if self._on_refresh is not None:
            self._on_refresh()

    def add_log(self, timestamp: str, level: str, message: str) -> None:
        with self._lock:
            self.log_lines.append((timestamp, level, message))

    def add_signal(self, signal: IntradaySignal) -> None:
        with self._lock:
            self.signals.appendleft(signal)
            self.session.selected_detail_symbol = signal.symbol
        self._notify()

    def record_scan_state(self, scan_state: SymbolScanState) -> None:
        with self._lock:
            self.session.latest_scan_by_symbol[scan_state.symbol] = scan_state
            self.session.selected_detail_symbol = scan_state.symbol
        self._notify()

    def init_premarket_symbol(self, symbol: str, analysts: list[str]) -> None:
        selected = [a.lower() for a in analysts]
        state = SymbolPremarketState(selected_analysts=selected)
        for analyst_key in ANALYST_ORDER:
            if analyst_key in selected:
                state.agent_status[ANALYST_AGENT_NAMES[analyst_key]] = "pending"
        for agent in PREMARKET_RESEARCH_AGENTS:
            state.agent_status[agent] = "pending"
        for section in (
            "market_report",
            "sentiment_report",
            "news_report",
            "fundamentals_report",
            "investment_plan",
        ):
            state.report_sections[section] = None
        with self._lock:
            self.premarket_by_symbol[symbol] = state
            self.premarket_active = True
            self.session.selected_detail_symbol = symbol
        self._notify()

    def load_premarket_from_bias(self, symbol: str, analysts: list[str], report) -> None:
        """Hydrate premarket display state from a restored DailyBiasReport."""
        self.init_premarket_symbol(symbol, analysts)
        with self._lock:
            state = self.premarket_by_symbol[symbol]
            for section, content in (report.analyst_reports or {}).items():
                if content:
                    state.report_sections[section] = content
            if report.summary:
                state.report_sections["investment_plan"] = report.summary
            for agent in list(state.agent_status):
                state.agent_status[agent] = "completed"
        self._notify()

    def update_premarket_chunk(self, symbol: str, chunk: dict) -> None:
        with self._lock:
            state = self.premarket_by_symbol.get(symbol)
            if state is None:
                return
            proxy = _PremarketStatusProxy(state)
        update_analyst_statuses(proxy, chunk)
        update_research_status_from_chunk(proxy, chunk)
        with self._lock:
            self.session.selected_detail_symbol = symbol
        self._notify()

    def finish_premarket(self) -> None:
        with self._lock:
            self.premarket_active = False
        self._notify()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "log_lines": list(self.log_lines),
                "signals": list(self.signals),
                "premarket_by_symbol": {
                    symbol: SymbolPremarketState(
                        selected_analysts=list(state.selected_analysts),
                        agent_status=dict(state.agent_status),
                        report_sections=dict(state.report_sections),
                    )
                    for symbol, state in self.premarket_by_symbol.items()
                },
                "premarket_active": self.premarket_active,
                "selected_symbol": self.session.selected_detail_symbol,
                "latest_scan": dict(self.session.latest_scan_by_symbol),
            }


class _PremarketStatusProxy:
    def __init__(self, state: SymbolPremarketState):
        self._state = state

    @property
    def selected_analysts(self) -> list[str]:
        return self._state.selected_analysts

    @property
    def agent_status(self) -> dict[str, str]:
        return self._state.agent_status

    @property
    def report_sections(self) -> dict[str, str | None]:
        return self._state.report_sections

    def update_agent_status(self, agent: str, status: str) -> None:
        self._state.agent_status[agent] = status

    def update_report_section(self, section_name: str, content: str) -> None:
        self._state.report_sections[section_name] = content


def attach_dashboard_logging(buffer: IntradayDashboardBuffer) -> DashboardLogHandler:
    handler = DashboardLogHandler(buffer)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    pkg_logger = logging.getLogger("tradingagents")
    pkg_logger.addHandler(handler)
    if pkg_logger.level == logging.NOTSET or pkg_logger.getEffectiveLevel() > logging.INFO:
        pkg_logger.setLevel(logging.INFO)
    return handler


def detach_dashboard_logging(handler: DashboardLogHandler) -> None:
    logging.getLogger("tradingagents").removeHandler(handler)


def create_intraday_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="bottom", size=10),
        Layout(name="footer", size=3),
    )
    layout["main"].split_row(
        Layout(name="watchlist", ratio=3),
        Layout(name="detail", ratio=4),
    )
    layout["bottom"].split_row(
        Layout(name="signals", ratio=2),
        Layout(name="logs", ratio=3),
    )
    return layout


def _bias_style(direction: str) -> str:
    if direction == "bullish":
        return "green"
    if direction == "bearish":
        return "red"
    return "yellow"


def _gate_cell(passed: bool | None) -> str:
    if passed is None:
        return "-"
    return "[green]✓[/green]" if passed else "[red]✗[/red]"


def _truncate_text(text: str, max_lines: int = DETAIL_MAX_LINES) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + "\n\n…truncated"


def update_intraday_display(
    layout: Layout,
    buffer: IntradayDashboardBuffer,
    *,
    stats_handler=None,
    start_time: datetime.datetime | None = None,
) -> None:
    session = buffer.session
    snap = buffer.snapshot()
    selected = snap["selected_symbol"]
    scan_time = (
        session.last_scan_time.strftime("%H:%M")
        if session.last_scan_time is not None
        else "—"
    )
    screener_time = (
        session.screener_last_refresh.strftime("%H:%M")
        if session.screener_last_refresh is not None
        else "—"
    )
    screener_note = ""
    if session.screener_last_refresh is not None or session.screener_last_candidate_count:
        screener_note = (
            f" | screener @ {screener_time} "
            f"candidates={session.screener_last_candidate_count}"
        )

    layout["header"].update(
        Panel(
            Text.from_markup(
                f"[bold]Intraday[/bold] {session.session_date} | "
                f"strategy={buffer.strategy_name} | "
                f"status={session.status} | "
                f"scan #{session.scan_count} @ {scan_time} ET"
                f"{screener_note}"
            ),
            border_style="cyan",
        )
    )

    watchlist_table = Table(
        show_header=True,
        header_style="bold magenta",
        box=box.SIMPLE_HEAD,
        expand=True,
    )
    watchlist_table.add_column("Symbol", style="bold")
    watchlist_table.add_column("Src", style="dim")
    watchlist_table.add_column("Bias")
    watchlist_table.add_column("G1", justify="center")
    watchlist_table.add_column("G2", justify="center")
    watchlist_table.add_column("Score", justify="center")
    watchlist_table.add_column("Dir")
    watchlist_table.add_column("Updated", style="dim")

    for symbol in session.watchlist:
        bias = session.daily_bias_cache.get(symbol)
        bias_dir = bias.direction if bias else "—"
        source = session.symbol_sources.get(symbol, "static")
        scan = snap["latest_scan"].get(symbol)
        marker = "› " if symbol == selected else "  "
        if scan is not None:
            total_factors = len(scan.factors_met) + len(scan.factors_missing)
            score = f"{scan.setup_score}/{total_factors}" if total_factors else str(scan.setup_score)
            watchlist_table.add_row(
                f"{marker}{symbol}",
                source[:4],
                f"[{_bias_style(bias_dir)}]{bias_dir}[/]",
                _gate_cell(scan.gate1_passed),
                _gate_cell(scan.gate2_passed if scan.gate1_passed else None),
                score,
                scan.final_direction or scan.strategy_direction or "—",
                scan.bar_time.strftime("%H:%M"),
            )
        else:
            watchlist_table.add_row(
                f"{marker}{symbol}",
                source[:4],
                f"[{_bias_style(bias_dir)}]{bias_dir}[/]" if bias else "—",
                "-",
                "-",
                "—",
                "—",
                "—",
            )

    layout["watchlist"].update(
        Panel(watchlist_table, title="Watchlist & Strategy Scoring", border_style="blue")
    )

    detail_text = _render_detail_panel(buffer, snap, selected)
    layout["detail"].update(
        Panel(
            Markdown(detail_text) if detail_text else Text("No symbol selected.", style="dim"),
            title=f"Detail — {selected or '—'}",
            border_style="green",
        )
    )

    signals_table = Table(show_header=True, box=box.SIMPLE_HEAD, expand=True)
    signals_table.add_column("Time")
    signals_table.add_column("Symbol")
    signals_table.add_column("Action")
    signals_table.add_column("Conf")
    for signal in list(snap["signals"])[:SIGNAL_MAX_LINES]:
        signals_table.add_row(
            signal.bar_time.strftime("%H:%M"),
            signal.symbol,
            signal.action,
            signal.confidence,
        )
    if not snap["signals"]:
        signals_table.add_row("—", "—", "—", "—")

    layout["signals"].update(Panel(signals_table, title="Recent Signals", border_style="green"))

    log_lines = snap["log_lines"][-LOG_MAX_LINES:]
    log_text = "\n".join(f"{ts} {level:<7} {msg}" for ts, level, msg in log_lines) or "—"
    layout["logs"].update(Panel(log_text, title="Logs", border_style="dim"))

    footer_parts = [
        f"G1 fail: {session.gate_stats['gate1_fail']}",
        f"G2 fail: {session.gate_stats['gate2_fail']}",
        f"pass: {session.gate_stats['all_pass']}",
        f"signals: {len(session.signal_log)}",
    ]
    if stats_handler is not None:
        stats = stats_handler.get_stats()
        footer_parts.extend(
            [
                f"LLM: {stats['llm_calls']}",
                f"tokens: {stats['tokens_in']}+{stats['tokens_out']}",
            ]
        )
    if start_time is not None:
        elapsed = datetime.datetime.now() - start_time
        footer_parts.append(f"elapsed: {int(elapsed.total_seconds())}s")

    layout["footer"].update(
        Panel(" | ".join(footer_parts), border_style="dim")
    )


def _render_detail_panel(buffer: IntradayDashboardBuffer, snap: dict, selected: str | None) -> str:
    if not selected:
        return ""

    session = buffer.session
    if snap["premarket_active"] or (
        session.status == "pre_market" and selected in snap["premarket_by_symbol"]
    ):
        return _render_premarket_detail(snap["premarket_by_symbol"].get(selected))

    bias = session.daily_bias_cache.get(selected)
    scan = snap["latest_scan"].get(selected)
    parts: list[str] = []

    if bias and bias.analyst_reports:
        parts.append("## Pre-market Analyst Reports")
        for section, content in bias.analyst_reports.items():
            title = REPORT_SECTION_TITLES.get(section, section)
            parts.append(f"### {title}\n{_truncate_text(content, max_lines=12)}")

    if scan is not None:
        parts.append("## Strategy Factors")
        for factor in scan.factors_met:
            parts.append(f"- ✓ {factor}")
        for factor in scan.factors_missing:
            parts.append(f"- ✗ {factor}")
        if scan.indicator_snapshot:
            parts.append("## Indicators")
            for key, value in scan.indicator_snapshot.items():
                parts.append(f"- **{key}**: {value}")
    screener_snap = session.screener_snapshots.get(selected)
    if screener_snap:
        parts.append("## Screener RRS")
        for key, value in screener_snap.items():
            parts.append(f"- **{key}**: {value}")
    if scan is None and not screener_snap and bias:
        parts.append(f"## Daily Bias\n**Direction:** {bias.direction}\n\n{_truncate_text(bias.summary)}")

    return _truncate_text("\n\n".join(parts))


def _render_premarket_detail(state: SymbolPremarketState | None) -> str:
    if state is None:
        return "Pre-market setup in progress…"

    parts = ["## Agent Progress"]
    for agent, status in state.agent_status.items():
        parts.append(f"- {agent}: {status}")

    for section, content in state.report_sections.items():
        if content:
            title = REPORT_SECTION_TITLES.get(section, section)
            parts.append(f"## {title}\n{_truncate_text(content, max_lines=12)}")

    return _truncate_text("\n\n".join(parts))
