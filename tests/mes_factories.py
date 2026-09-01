"""Builders for fully controlled MES snapshots used by the checklist tests."""

from __future__ import annotations

from datetime import datetime, timedelta

from tradingagents.mes.config import MesChecklistConfig, load_mes_config
from tradingagents.mes.snapshot import Bar, MesSnapshot, SeriesState

# 2026-03-30 is a Monday, so weekday gates stay open by default.
SESSION_DATE = datetime(2026, 3, 30)
DEFAULT_AS_OF = SESSION_DATE.replace(hour=11, minute=0)


def make_bar(
    ts: datetime,
    *,
    open_: float = 99.0,
    high: float = 100.2,
    low: float = 98.9,
    close: float = 100.0,
    volume: float = 1000.0,
    add: float | None = None,
    tick: float | None = None,
    vold: float | None = None,
) -> Bar:
    return Bar(
        timestamp=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        add=add,
        tick=tick,
        vold=vold,
    )


def make_mes_series(
    *,
    close: float = 100.0,
    volume: float = 1000.0,
    bar_kwargs: dict | None = None,
    **overrides,
) -> SeriesState:
    """MES state where VWAP + ATR-range pass for a long and everything else fails."""
    kwargs = {"close": close, "volume": volume}
    kwargs.update(bar_kwargs or {})
    state = SeriesState(
        symbol="/MES",
        bars=[make_bar(DEFAULT_AS_OF, **kwargs)],
        vwap=99.0,
        vwap_ready=True,
        vwap_sigma=0.5,
        prev_vwap=99.0,
        prev_vwap_ready=True,
        sma=99.5,
        sma_ready=True,
        prev_sma=99.5,
        prev_sma_ready=True,
        atr=1.0,
        atr_ready=True,
        upper_atr_band=104.0,
        lower_atr_band=96.0,
        session_start_price=100.0,
        has_session_start=True,
        laguerre=0.5,
        prev_laguerre=0.5,
        laguerre_ready=False,
        avg_volume=1000.0,
        volume_ready=True,
        opening_range_high=101.0,
        opening_range_low=98.0,
    )
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


def make_spy_series(
    *,
    internals: list[tuple[float | None, float | None, float | None]] | None = None,
    close: float = 500.0,
    **overrides,
) -> SeriesState:
    """SPY state whose bars carry the ($ADD, $TICK, $VOLD) tuples in ``internals``."""
    if internals is None:
        internals = [(300.0, 100.0, 1000.0)] * 6
    bars = []
    first_ts = DEFAULT_AS_OF - timedelta(minutes=5 * (len(internals) - 1))
    for index, (add, tick, vold) in enumerate(internals):
        bars.append(
            make_bar(
                first_ts + timedelta(minutes=5 * index),
                open_=close - 1,
                high=close + 0.2,
                low=close - 1.1,
                close=close,
                add=add,
                tick=tick,
                vold=vold,
            )
        )
    state = SeriesState(
        symbol="SPY",
        bars=bars,
        vwap=499.0,
        vwap_ready=True,
        sma=499.5,
        sma_ready=True,
        prev_sma=499.5,
        prev_sma_ready=True,
        prev_vwap=499.0,
        prev_vwap_ready=True,
        atr=1.0,
        atr_ready=True,
        session_start_price=499.0,
        has_session_start=True,
        avg_volume=1000.0,
        volume_ready=True,
        prev_vold=internals[-2][2] if len(internals) >= 2 else None,
    )
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


def make_snapshot(
    *,
    as_of: datetime | None = None,
    cfg: MesChecklistConfig | None = None,
    mes: SeriesState | None = None,
    spy: SeriesState | None = None,
    warnings: list[str] | None = None,
) -> MesSnapshot:
    as_of = as_of or DEFAULT_AS_OF
    return MesSnapshot(
        as_of=as_of,
        session_date=as_of.strftime("%Y-%m-%d"),
        config=cfg or load_mes_config(),
        mes=mes or make_mes_series(),
        spy=spy or make_spy_series(),
        warnings=list(warnings or []),
    )


def find_item(items, name_fragment: str):
    for item in items:
        if name_fragment in item.name:
            return item
    raise AssertionError(f"no check item matching {name_fragment!r} in {[i.name for i in items]}")
