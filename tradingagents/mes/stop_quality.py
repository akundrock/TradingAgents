"""Advisory stop-quality analysis for journaled MES trades.

Pure functions over the raw JSONL records produced by
:class:`~tradingagents.mes.journal.MesJournal` — no I/O, no LLM, no Rich, fully
deterministic (same pattern as :mod:`~tradingagents.mes.checklist` and
:mod:`~tradingagents.mes.radar`).

The heuristics are *advisory*, not mechanical: they compare each closed
trade's initial stop against how the trade actually moved (MFE/MAE in R, and
the stop distance in ATR multiples) and emit human-readable flags for the
review report. A flag is a prompt to look at the trade, never an instruction
to change a level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .config import MesChecklistConfig

# MAE below this (in R) on a non-stop exit means the initial stop was never
# seriously tested — a candidate for tightening.
_WIDE_MAE_R_THRESHOLD = 0.5
# Stop distances under this many ATRs are flagged as structurally tight.
_MIN_STOP_ATR_MULTIPLE = 1.0


@dataclass
class StopQualityRow:
    """One closed trade's initial-stop quality, with advisory flags."""

    side: str
    entry: float
    stop: float
    stop_points: float  # abs(entry - stop)
    atr: float | None  # from entry_context, else nearest prior check record's atr
    stop_atr_multiple: float | None
    mae_r: float | None
    mfe_r: float | None
    realized_r: float | None
    exit_reason: str
    flags: list[str]


@dataclass
class StopQualityReport:
    """Per-trade rows plus a plain-text summary for the review panel."""

    rows: list[StopQualityRow]

    def is_empty(self) -> bool:
        return not self.rows

    def summary_lines(self) -> list[str]:
        """Human-readable advisory lines; empty when nothing was flagged."""
        lines: list[str] = []
        n_tight = 0
        n_wide = 0
        for row in self.rows:
            for flag in row.flags:
                lines.append(f"{row.side} @ {row.entry:.2f}: {flag}")
                if flag.startswith("stop likely too tight"):
                    n_tight += 1
                elif flag.startswith("stop likely too wide"):
                    n_wide += 1
        if lines:
            lines.append(
                f"{len(self.rows)} trades: {n_tight} too-tight flags, "
                f"{n_wide} too-wide flags"
            )
        return lines


def _num(record: dict, key: str) -> float | None:
    """Read a numeric field, tolerating missing keys and junk values."""
    value = record.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_ts(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_stop_out(reason: str | None) -> bool:
    return "stop" in (reason or "").lower()


def _resolve_atr(
    *,
    entry_context: dict | None,
    entry_time: datetime,
    parsed_checks: list[tuple[datetime, float]],
) -> float | None:
    """ATR from entry_context, else the latest check at/before the entry."""
    atr = _num(entry_context or {}, "atr")
    if atr:
        return atr
    best: float | None = None
    for as_of, check_atr in parsed_checks:
        if as_of > entry_time:
            break  # parsed_checks is sorted by timestamp
        best = check_atr
    return best


def _pair_closed_trades(trades: list[dict]) -> list[tuple[dict, dict]]:
    """Pair each trade_closed with its trade_opened, sequentially.

    The journal has no trade id; records for one date are appended in
    chronological order, so a close belongs to the most recent open. Records
    of other kinds (trade_adjusted) are ignored.
    """
    pairs: list[tuple[dict, dict]] = []
    open_stack: list[dict] = []
    for record in trades:
        kind = record.get("kind")
        if kind == "trade_opened":
            open_stack.append(record)
        elif kind == "trade_closed" and open_stack:
            pairs.append((open_stack.pop(0), record))
    return pairs


def _flags_for(row: StopQualityRow, partial_at_r: float) -> list[str]:
    flags: list[str] = []
    if (
        _is_stop_out(row.exit_reason)
        and row.mfe_r is not None
        and row.mfe_r >= partial_at_r
    ):
        flags.append(
            f"stop likely too tight (MFE reached +{row.mfe_r:.2f}R before stop-out)"
        )
    if (
        not _is_stop_out(row.exit_reason)
        and row.mae_r is not None
        and abs(row.mae_r) < _WIDE_MAE_R_THRESHOLD
    ):
        flags.append("stop likely too wide (MAE never approached -0.5R)")
    if (
        row.stop_atr_multiple is not None
        and row.stop_atr_multiple < _MIN_STOP_ATR_MULTIPLE
    ):
        flags.append("initial stop below 1x ATR")
    return flags


def build_stop_quality_report(
    trades: list[dict],
    checks: list[dict],
    cfg: MesChecklistConfig | None,
) -> StopQualityReport:
    """Evaluate initial-stop quality for every closed trade in ``trades``.

    ``trades`` are raw journal records (trade_opened / trade_adjusted /
    trade_closed) for one date, as returned by ``MesJournal.load_trades``;
    ``checks`` are the day's check records from ``MesJournal.load_checks``.
    Open (unclosed) trades are skipped. All flags are advisory.
    """
    partial_at_r = getattr(cfg, "partial_at_r", 1.5) if cfg is not None else 1.5
    parsed_checks: list[tuple[datetime, float]] = []
    for check in checks:
        as_of = _parse_ts(check.get("as_of"))
        atr = _num(check, "atr")
        if as_of is not None and atr:
            parsed_checks.append((as_of, atr))
    parsed_checks.sort(key=lambda pair: pair[0])

    rows: list[StopQualityRow] = []
    for opened, closed in _pair_closed_trades(trades):
        entry = _num(opened, "entry") or 0.0
        stop = _num(opened, "stop") or 0.0
        entry_time = _parse_ts(opened.get("entry_time")) or datetime.min
        atr = _resolve_atr(entry_context=opened.get("entry_context"),
                           entry_time=entry_time, parsed_checks=parsed_checks)
        stop_points = abs(entry - stop)
        stop_atr_multiple = stop_points / atr if atr else None
        row = StopQualityRow(
            side=str(opened.get("side", "?")),
            entry=entry,
            stop=stop,
            stop_points=stop_points,
            atr=atr,
            stop_atr_multiple=round(stop_atr_multiple, 4) if stop_atr_multiple is not None else None,
            mae_r=_num(closed, "mae_r"),
            mfe_r=_num(closed, "mfe_r"),
            realized_r=_num(closed, "realized_r"),
            exit_reason=str(closed.get("reason", "?")),
            flags=[],
        )
        row.flags = _flags_for(row, partial_at_r)
        rows.append(row)
    return StopQualityReport(rows=rows)
