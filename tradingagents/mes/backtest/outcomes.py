"""Next-bar-open outcome joiner (AZP Phase 2, W2.2).

Joins :class:`~tradingagents.mes.backtest.walker.ReplayRecord` verdicts to
realized outcomes with a conservative fill model:

* **Entry** — a tradeable verdict on bar *i* fills at bar *i+1*'s open
  (next-bar open; the signal bar itself is never the fill bar). The trade
  template is the checklist's own levels: stop and first target come straight
  off the record, ``initial_risk_points = |entry - stop|``.
* **Exit ladder, adverse-first** — per held bar, in priority order: stop touch
  (gap-aware worst-case fill, mirroring ``management._stop_fill``),
  first-target touch, EOD flatten at the session's last bar. One fill per bar
  max. The live ladder's first rung (breakeven stop once the trade has been up
  ``cfg.breakeven_at_r``, applied on bar closes) is included; partials and
  ATR trailing are intentionally out of scope for the replay fill model — they
  need per-bar ATR state the record stream does not carry, and duplicating
  them here risks diverging from ``management.evaluate_management``.
* **MFE/MAE** — same semantics as ``management.excursions_r`` (favorable
  excursion in the trade's direction, MAE <= 0), over the post-entry bars
  including the exit bar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from ..config import MesChecklistConfig
from .ablations import config_fingerprint as fingerprint_config
from .walker import ReplayRecord


@dataclass(frozen=True)
class TradeOutcome:
    """One closed next-bar-open trade, joined to its signal verdict."""

    session_date: str
    signal_timestamp: datetime
    entry_timestamp: datetime
    exit_timestamp: datetime
    side: str
    tier: str
    entry_price: float
    stop: float
    """The stop actually in force at exit (initial stop, or breakeven after the rung fires)."""
    initial_stop: float
    target: float | None
    initial_risk_points: float
    exit_price: float
    exit_reason: str
    """``stop`` | ``target`` | ``flip`` | ``eod``."""
    realized_r: float
    mfe_r: float
    mae_r: float
    hold_bars: int
    config_name: str = ""
    ablation: str = "baseline"
    """Named ablation the signal record was produced under ("baseline" = defaults)."""
    config_fingerprint: str = ""
    """Short hash of the config values the trade was evaluated under (W2.4)."""


@dataclass
class JoinResult:
    """Closed outcomes plus the accounting the report layer needs."""

    outcomes: list[TradeOutcome]
    signals: int = 0
    """Tradeable verdicts seen (actionable signals)."""
    entered: int = 0
    skipped_no_levels: int = 0
    """Tradeable verdicts whose stop/levels template was missing or degenerate."""
    skipped_no_fill_bar: int = 0
    """Verdicts on the session's last bar: no next bar exists to fill at."""


def _sign(side: str) -> float:
    return 1.0 if side == "long" else -1.0


def _stop_fill(side: str, stop: float, bar) -> float | None:
    """Gap-aware worst-case stop fill (mirrors ``management._stop_fill``)."""
    if side == "long":
        return min(stop, bar.open) if bar.low <= stop else None
    return max(stop, bar.open) if bar.high >= stop else None


def _target_fill(side: str, target: float | None, bar) -> float | None:
    """Limit-style target fill; gaps beyond the target fill at the open."""
    if target is None:
        return None
    if side == "long":
        return max(target, bar.open) if bar.high >= target else None
    return min(target, bar.open) if bar.low <= target else None


def join_outcomes(
    records: Sequence[ReplayRecord],
    mes_bars: Sequence,
    cfg: MesChecklistConfig | None = None,
    *,
    config_name: str = "",
) -> JoinResult:
    """Walk a session's records and bar clock, emitting one outcome per signal.

    Signals while a trade is open are ignored except for opposite-side
    tradeable verdicts, which flip the position (exit at that bar's close and
    a fresh entry on the next bar's open if the new verdict also templates).
    """
    cfg = cfg or MesChecklistConfig()
    record_at = {record.timestamp: record for record in records}
    report = JoinResult(outcomes=[])
    n_bars = len(mes_bars)

    bar_index = 0
    while bar_index < n_bars:
        bar = mes_bars[bar_index]
        record = record_at.get(bar.timestamp)
        if record is None or not record.tradeable:
            bar_index += 1
            continue

        report.signals += 1
        if record.stop is None or record.entry_zone is None:
            report.skipped_no_levels += 1
            bar_index += 1
            continue

        entry_bar_index = bar_index + 1
        if entry_bar_index >= n_bars:
            # Verdict on the last bar: the session ends before a fill bar exists.
            report.skipped_no_fill_bar += 1
            break

        entry_bar = mes_bars[entry_bar_index]
        entry_price = entry_bar.open
        initial_stop = float(record.stop)
        target = record.first_target
        risk = abs(entry_price - initial_stop)
        if risk <= 0:
            report.skipped_no_levels += 1
            bar_index += 1
            continue

        stop = initial_stop
        be_armed = False
        mfe_r = 0.0
        mae_r = 0.0
        exit_price: float | None = None
        exit_reason: str | None = None
        exit_timestamp: datetime | None = None

        scan = entry_bar_index
        while scan < n_bars:
            held_bar = mes_bars[scan]

            # Adverse first: stop touch, gap-aware worst-case fill.
            stop_fill = _stop_fill(record.side, stop, held_bar)
            if stop_fill is not None:
                exit_price = stop_fill
                exit_reason = "stop"
                exit_timestamp = held_bar.timestamp
                break

            target_fill = _target_fill(record.side, target, held_bar)
            if target_fill is not None:
                exit_price = target_fill
                exit_reason = "target"
                exit_timestamp = held_bar.timestamp
                break

            # Running excursion through this held bar (entry bar included).
            if record.side == "long":
                mfe_r = max(mfe_r, (held_bar.high - entry_price) / risk)
                mae_r = min(mae_r, (held_bar.low - entry_price) / risk)
            else:
                mfe_r = max(mfe_r, (entry_price - held_bar.low) / risk)
                mae_r = min(mae_r, (entry_price - held_bar.high) / risk)

            # Breakeven rung: armed on the bar CLOSE once MFE reached the rung.
            if not be_armed and cfg.breakeven_at_r and mfe_r >= cfg.breakeven_at_r:
                stop = entry_price
                be_armed = True

            # A tradeable verdict of the opposite side flips the position at this close.
            held_record = record_at.get(held_bar.timestamp)
            if held_record is not None and held_record.tradeable and held_record.side != record.side:
                exit_price = held_bar.close
                exit_reason = "flip"
                exit_timestamp = held_bar.timestamp
                break

            if scan == n_bars - 1:
                # EOD flatten at the session's last close.
                exit_price = held_bar.close
                exit_reason = "eod"
                exit_timestamp = held_bar.timestamp
                break

            scan += 1

        # exit_timestamp is always set: the scan either breaks on a fill/flip
        # or ends with the EOD flatten at the last bar.
        assert exit_price is not None and exit_timestamp is not None and exit_reason is not None
        realized_r = round(_sign(record.side) * (exit_price - entry_price) / risk, 4)
        report.outcomes.append(
            TradeOutcome(
                session_date=record.session_date,
                signal_timestamp=record.timestamp,
                entry_timestamp=entry_bar.timestamp,
                exit_timestamp=exit_timestamp,
                side=record.side,
                tier=record.tier,
                entry_price=round(entry_price, 4),
                stop=round(stop, 4),
                initial_stop=round(initial_stop, 4),
                target=target,
                initial_risk_points=round(risk, 4),
                exit_price=round(exit_price, 4),
                exit_reason=exit_reason,
                realized_r=round(realized_r, 4),
                mfe_r=round(mfe_r, 4),
                mae_r=round(mae_r, 4),
                hold_bars=scan - bar_index,
                config_name=config_name,
                ablation=record.ablation,
                config_fingerprint=record.config_fingerprint or fingerprint_config(cfg),
            )
        )

        # Resume after the exit bar; on a flip, the exit bar itself carries the
        # new verdict, so re-scan it to open the opposite trade on the next bar.
        bar_index = scan if exit_reason == "flip" else scan + 1

    report.entered = len(report.outcomes)
    return report
