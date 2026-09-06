"""Post-entry trade management: the deterministic exit ladder for /MES.

Pure functions over an already-evaluated :class:`~.checklist.ChecklistResult`
and a declared :class:`OpenTrade`. The ladder decides every hard level move
(breakeven, partial, trail, time stop); the CLI and the LLM manager may
comment on the plan but never move a level. Stops only ever tighten; events
are idempotent via the trade's ``fired`` dict; a stop or target touched
intrabar fills at the bar's open when it gaps through the level.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from .checklist import ChecklistResult
from .config import MesChecklistConfig
from .snapshot import MesSnapshot


@dataclass
class OpenTrade:
    """A declared position being managed by the copilot."""

    side: str  # "long" | "short"
    contracts: int
    remaining: int
    entry: float
    stop: float
    initial_stop: float
    target: float | None
    entry_time: datetime
    initial_risk_points: float
    fired: dict[str, str] = field(default_factory=dict)
    """Event name -> ISO timestamp it first fired. Idempotency guard."""
    manual_events: list[str] = field(default_factory=list)
    realized_r: float = 0.0
    """R banked by partial closes / stop-outs so far."""

    @property
    def is_open(self) -> bool:
        return self.remaining > 0


@dataclass
class MgmtEvent:
    name: str
    as_of: datetime
    detail: str


@dataclass
class MgmtReport:
    r_now: float
    mfe_r: float
    mae_r: float
    stop: float
    target: float | None
    events: list[MgmtEvent] = field(default_factory=list)
    next_event: str = ""
    recommendation: str = "HOLD"
    reasons: list[str] = field(default_factory=list)


def _sign(side: str) -> float:
    return 1.0 if side == "long" else -1.0


def _tighter(old: float, candidate: float, side: str) -> float:
    """The more protective of two stop levels for the trade's side."""
    return max(old, candidate) if side == "long" else min(old, candidate)


def _round_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 4)


def _r_of(price: float, trade: OpenTrade) -> float:
    """Signed open R of ``price`` against the trade's initial risk."""
    risk = trade.initial_risk_points or 1.0
    return round(_sign(trade.side) * (price - trade.entry) / risk, 4)


def _stop_fill(trade: OpenTrade, bar) -> float | None:
    """Fill price when the bar touched the stop; gaps fill at the open."""
    if trade.side == "long":
        return min(trade.stop, bar.open) if bar.low <= trade.stop else None
    return max(trade.stop, bar.open) if bar.high >= trade.stop else None


def _target_fill(trade: OpenTrade, bar) -> float | None:
    if trade.target is None:
        return None
    if trade.side == "long":
        return max(trade.target, bar.open) if bar.high >= trade.target else None
    return min(trade.target, bar.open) if bar.low <= trade.target else None


def excursions_r(snapshot: MesSnapshot, trade: OpenTrade) -> tuple[float, float]:
    """(mfe_r, mae_r) over the bars since entry; MAE <= 0 by construction."""
    sign = _sign(trade.side)
    risk = trade.initial_risk_points or 1.0
    mfe = 0.0
    mae = 0.0
    for bar in snapshot.mes.bars:
        if bar.timestamp < trade.entry_time:
            continue
        if trade.side == "long":
            fav, adv = bar.high - trade.entry, bar.low - trade.entry
        else:
            fav, adv = trade.entry - bar.low, trade.entry - bar.high
        mfe = max(mfe, fav / risk)
        mae = min(mae, adv / risk)
    return round(mfe, 4), round(mae, 4)


def _next_event(cfg, trade: OpenTrade) -> str:
    if trade.remaining <= 0:
        return ""
    if "breakeven" not in trade.fired:
        return f"BE stop at +{cfg.breakeven_at_r:g}R"
    if "partial" not in trade.fired:
        return f"partial at +{cfg.partial_at_r:g}R"
    return f"trail {cfg.trail_atr_multiple:g}xATR"


def evaluate_management(
    snapshot: MesSnapshot,
    result: ChecklistResult,
    trade: OpenTrade,
    cfg: MesChecklistConfig | None = None,
) -> tuple[OpenTrade, MgmtReport]:
    """Evaluate the ladder on the latest bar close. Returns (updated_trade, report)."""
    cfg = cfg or snapshot.config
    bar = snapshot.mes.last
    updated = replace(trade)
    # replace() is a shallow copy: detach the nested mutables so fill-path
    # writes never leak into the caller's trade.
    updated.fired = dict(trade.fired)
    updated.manual_events = list(trade.manual_events)
    events: list[MgmtEvent] = []
    reasons: list[str] = []

    if updated.remaining <= 0:
        return updated, MgmtReport(
            r_now=0.0, mfe_r=0.0, mae_r=0.0, stop=updated.stop,
            target=updated.target, events=[], next_event="",
            recommendation="CLOSED", reasons=["position already closed"],
        )

    r_now = _r_of(snapshot.mes.close, updated)
    mfe_r, mae_r = excursions_r(snapshot, updated)
    stop_fill = _stop_fill(updated, bar)
    target_fill = _target_fill(updated, bar)

    if stop_fill is not None:
        updated.realized_r = round(
            updated.realized_r + updated.remaining * _r_of(stop_fill, updated), 4
        )
        updated.remaining = 0
        updated.fired.setdefault("stopped_out", snapshot.as_of.isoformat(timespec="minutes"))
        return updated, MgmtReport(
            r_now=0.0, mfe_r=mfe_r, mae_r=mae_r, stop=updated.stop,
            target=updated.target,
            events=[MgmtEvent("stopped_out", snapshot.as_of, f"stop filled at {stop_fill:.2f}")],
            next_event="",
            recommendation="CLOSED",
            reasons=[f"stop touched; filled at {stop_fill:.2f}"],
        )

    if target_fill is not None:
        updated.realized_r = round(
            updated.realized_r + updated.remaining * _r_of(target_fill, updated), 4
        )
        updated.remaining = 0
        updated.fired.setdefault("target", snapshot.as_of.isoformat(timespec="minutes"))
        return updated, MgmtReport(
            r_now=0.0, mfe_r=mfe_r, mae_r=mae_r, stop=updated.stop,
            target=updated.target,
            events=[MgmtEvent("target", snapshot.as_of, f"target filled at {target_fill:.2f}")],
            next_event="",
            recommendation="CLOSED",
            reasons=[f"target hit at {target_fill:.2f}"],
        )

    # No fill this bar: Task 3's ladder triggers run here. Task 2 reports HOLD.
    return updated, MgmtReport(
        r_now=r_now, mfe_r=mfe_r, mae_r=mae_r, stop=updated.stop,
        target=updated.target, events=events,
        next_event=_next_event(cfg, updated),
        recommendation="HOLD",
        reasons=reasons,
    )
