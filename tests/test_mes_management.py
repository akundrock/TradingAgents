"""Tests for tradingagents.mes.management — the post-entry ladder."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from tradingagents.mes.checklist import ChecklistResult
from tradingagents.mes.management import (
    MgmtEvent,
    MgmtReport,
    OpenTrade,
    evaluate_management,
)
from tests.mes_factories import DEFAULT_AS_OF, make_mes_series, make_snapshot

# entry 100.00, initial stop 98.00 -> 1R = 2.0 points
ENTRY = 100.0
STOP = 98.0
TARGET = 102.0
RISK = 2.0


def make_trade(**overrides) -> OpenTrade:
    trade = OpenTrade(
        side="long",
        contracts=1,
        remaining=1,
        entry=100.0,
        stop=98.0,
        initial_stop=98.0,
        target=102.0,
        entry_time=datetime(2026, 3, 30, 10, 0),
        initial_risk_points=2.0,
        fired={},
        manual_events=[],
        realized_r=0.0,
    )
    return replace(trade, **overrides)


def snap_at(
    price: float,
    *,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    atr: float = 1.0,
    as_of=None,
):
    """Snapshot whose single MES bar closes at `price` with controllable OHLC."""
    open_ = price if open_ is None else open_
    high = max(open_, price) if high is None else high
    low = min(open_, price) if low is None else low
    mes = make_mes_series(
        close=price,
        atr=atr,
        bar_kwargs={"open_": open_, "high": high, "low": low, "close": price},
    )
    return make_snapshot(mes=mes, as_of=as_of)


def result_at(price: float, *, side: str = "long", atr: float = 1.0):
    return ChecklistResult(
        as_of_label="11:00",
        side=side,
        direction=side,
        last_price=price,
        vwap=99.0,
        atr=atr,
        gates_ok=True,
        score_ok=True,
        confirmations=5,
        required=4,
        tier="standard",
        spy_confirmations=4,
        spy_confluence_ok=True,
    )


@pytest.mark.unit
def test_r_now_long_positive_above_entry():
    _, report = evaluate_management(snap_at(101.0), result_at(101.0), make_trade())
    assert report.r_now == pytest.approx(0.5)  # +1.0 pts over a 2.0-pt risk


@pytest.mark.unit
def test_r_now_short_side_sign():
    # Short fixtures need mirrored levels: stop above entry, target below.
    trade = make_trade(side="short", stop=102.0, initial_stop=102.0, target=98.0)
    _, report = evaluate_management(snap_at(99.0), result_at(99.0), trade)
    assert report.r_now == pytest.approx(0.5)  # 1.0 favorable pt / 2.0


@pytest.mark.unit
def test_r_uses_initial_risk_not_current_stop():
    trade = make_trade(fired={"breakeven": "2026-03-30T10:30"}, stop=100.25)
    _, report = evaluate_management(snap_at(101.0), result_at(101.0), trade)
    assert report.r_now == pytest.approx(0.5)


@pytest.mark.unit
def test_hold_before_any_trigger():
    _, report = evaluate_management(snap_at(100.5), result_at(100.5), make_trade())
    assert report.recommendation == "HOLD"
    assert report.events == []
    assert report.next_event.startswith("BE stop")


@pytest.mark.unit
def test_input_trade_is_not_mutated():
    trade = make_trade()
    evaluate_management(snap_at(101.5), result_at(101.5), trade)
    assert trade.stop == 98.0
    assert trade.fired == {}


@pytest.mark.unit
def test_stop_touched_closes_at_level_price():
    updated, report = evaluate_management(
        snap_at(97.9, open_=99.5, high=100.2, low=97.9), result_at(97.9), make_trade()
    )
    assert updated.remaining == 0
    assert updated.fired["stopped_out"].startswith("2026-03-30T11:00")
    assert report.recommendation == "CLOSED"
    assert updated.realized_r == pytest.approx(-1.0)  # filled at the 98.00 stop


@pytest.mark.unit
def test_stop_gap_fills_at_bar_open():
    """Open 97.00 gaps through the 98.00 stop: fill at 97.00, not 98.00."""
    updated, report = evaluate_management(
        snap_at(97.0, open_=97.0, low=96.8), result_at(97.0), make_trade()
    )
    assert updated.remaining == 0
    assert updated.realized_r == pytest.approx(-1.5)  # 97.0 vs entry 100.0 over 2.0 risk
    assert report.events[0].name == "stopped_out"
    assert "filled at 97.00" in report.events[0].detail


@pytest.mark.unit
def test_target_hit_closes_at_target_price():
    updated, report = evaluate_management(
        snap_at(102.0, high=102.4), result_at(102.0), make_trade()
    )
    assert updated.remaining == 0
    assert updated.realized_r == pytest.approx(1.0)  # 2.0 pts / 2.0-pt risk
    assert report.events[0].name == "target"
    assert report.recommendation == "CLOSED"


@pytest.mark.unit
def test_mfe_mae_cover_bars_since_entry():
    trade = make_trade()
    _, report = evaluate_management(snap_at(100.5), result_at(100.5), trade)
    assert report.mfe_r >= report.r_now
    assert report.mae_r <= 0.0
