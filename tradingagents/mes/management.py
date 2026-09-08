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

    # ---- Ladder triggers, evaluated in order on this bar close ----

    # 1. Breakeven: open profit at or past the configured R.
    if "breakeven" not in updated.fired and r_now >= cfg.breakeven_at_r:
        cushion = cfg.breakeven_cushion_ticks * cfg.mes_tick_size
        be_stop = updated.entry + cushion if updated.side == "long" else updated.entry - cushion
        new_stop = _tighter(updated.stop, _round_tick(be_stop, cfg.mes_tick_size), updated.side)
        if new_stop != updated.stop:
            events.append(MgmtEvent(
                "breakeven", snapshot.as_of,
                f"stop {updated.stop:.2f} -> {new_stop:.2f} (BE at +{r_now:.2f}R)",
            ))
            updated.stop = new_stop
        updated.fired["breakeven"] = snapshot.as_of.isoformat(timespec="minutes")

    # 2. Partial: bank a fraction of the position, or lock the gain with 1 contract.
    if (
        "breakeven" in updated.fired
        and "partial" not in updated.fired
        and r_now >= cfg.partial_at_r
    ):
        if updated.remaining > 1:
            closed = max(1, round(updated.remaining * cfg.partial_fraction))
            updated.realized_r = round(updated.realized_r + closed * r_now, 4)
            updated.remaining -= closed
            events.append(MgmtEvent(
                "partial", snapshot.as_of,
                f"closed {closed} contract(s) at +{r_now:g}R; {updated.remaining} remain",
            ))
        else:
            lock = updated.entry + _sign(updated.side) * (
                r_now * cfg.partial_fraction
            ) * updated.initial_risk_points
            new_stop = _tighter(
                updated.stop, _round_tick(lock, cfg.mes_tick_size), updated.side
            )
            if new_stop != updated.stop:
                events.append(MgmtEvent(
                    "partial", snapshot.as_of,
                    f"1-contract degradation: stop -> {new_stop:.2f} "
                    f"locking {cfg.partial_fraction:.0%} of the open gain",
                ))
            updated.stop = new_stop
        updated.fired["partial"] = snapshot.as_of.isoformat(timespec="minutes")

    # 3. Trail after the partial: every bar, never looser. Gated on the PRE-call
    # fired dict (trade, not updated) so the trail starts the bar AFTER the
    # partial fires, never on the same bar as a partial/BE move.
    if "partial" in trade.fired and updated.remaining > 0 and snapshot.mes.atr_ready:
        closes_since_entry = [
            b.close for b in snapshot.mes.bars if b.timestamp >= updated.entry_time
        ]
        if updated.side == "long":
            candidate = max(closes_since_entry) - snapshot.mes.atr * cfg.trail_atr_multiple
        else:
            candidate = min(closes_since_entry) + snapshot.mes.atr * cfg.trail_atr_multiple
        trail_stop = _tighter(updated.stop, _round_tick(candidate, cfg.mes_tick_size), updated.side)
        if trail_stop != updated.stop:
            events.append(MgmtEvent(
                "trail", snapshot.as_of,
                f"stop {updated.stop:.2f} -> {trail_stop:.2f} ({cfg.trail_atr_multiple:g}xATR)",
            ))
            updated.stop = trail_stop

    # 4. Time stop: flatten before session exit regardless of P&L.
    hour, minute = divmod(int(cfg.exit_time.replace(":", "")), 100)
    flatten = snapshot.as_of.replace(hour=hour, minute=minute)
    if snapshot.as_of >= flatten - timedelta(minutes=cfg.time_stop_buffer_minutes):
        updated.realized_r = round(
            updated.realized_r + updated.remaining * r_now, 4
        )
        updated.remaining = 0
        updated.fired.setdefault("time_stop", snapshot.as_of.isoformat(timespec="minutes"))
        return updated, MgmtReport(
            r_now=r_now, mfe_r=mfe_r, mae_r=mae_r, stop=updated.stop,
            target=updated.target, events=events + [
                MgmtEvent("time_stop", snapshot.as_of, f"flatten by {cfg.exit_time} ET")
            ],
            next_event="", recommendation="FLATTEN",
            reasons=[f"time stop {cfg.time_stop_buffer_minutes} min before {cfg.exit_time} ET"],
        )

    # 5. Confluence flip against the position: EXIT advisory (or hard exit).
    against = (
        not result.spy_confluence_ok
        or (not result.tradeable and result.gates_ok)
        or (result.tradeable and result.side != trade.side)
    )
    if against:
        reasons.append("checklist flipped against the position (SPY confluence lost)")
        if cfg.exit_on_confluence_loss:
            updated.realized_r = round(
                updated.realized_r + updated.remaining * r_now, 4
            )
            updated.remaining = 0
            updated.fired.setdefault(
                "confluence_exit", snapshot.as_of.isoformat(timespec="minutes")
            )
            return updated, MgmtReport(
                r_now=r_now, mfe_r=mfe_r, mae_r=mae_r, stop=updated.stop,
                target=updated.target, events=events,
                next_event="", recommendation="CLOSED",
                reasons=reasons + ["exit_on_confluence_loss=True"],
            )
        recommendation = "EXIT"
    else:
        recommendation = "HOLD"

    return updated, MgmtReport(
        r_now=r_now, mfe_r=mfe_r, mae_r=mae_r, stop=updated.stop,
        target=updated.target, events=events,
        next_event=_next_event(cfg, updated),
        recommendation=recommendation,
        reasons=reasons,
    )
