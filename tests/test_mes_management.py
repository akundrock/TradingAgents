"""Tests for tradingagents.mes.management — the post-entry ladder."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from tradingagents.mes.checklist import ChecklistResult
from tradingagents.mes.config import load_mes_config
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
def test_input_trade_fired_dict_not_mutated_on_fill():
    """replace() is shallow: fill paths must not write through to the input."""
    trade = make_trade()
    evaluate_management(
        snap_at(97.9, open_=99.5, high=100.2, low=97.9), result_at(97.9), trade
    )
    assert trade.fired == {}
    trade2 = make_trade()
    evaluate_management(snap_at(102.0, high=102.4), result_at(102.0), trade2)
    assert trade2.fired == {}


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


# ---------------------------------------------------------------------------
# Ladder triggers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_breakeven_fires_at_1r():
    """+1.0R close moves the stop to entry + 1 tick cushion (0.25)."""
    trade = make_trade(target=None)  # target None: otherwise the fill branch closes first
    updated, report = evaluate_management(snap_at(102.0), result_at(102.0), trade)
    assert "breakeven" in updated.fired
    assert updated.stop == pytest.approx(100.25)  # entry + 1 tick
    assert report.events[0].name == "breakeven"
    assert "stop 98.00 -> 100.25" in report.events[0].detail


@pytest.mark.unit
def test_breakeven_does_not_refire():
    trade = make_trade(fired={"breakeven": "2026-03-30T10:30"}, stop=100.25, target=None)
    updated, report = evaluate_management(snap_at(102.0), result_at(102.0), trade)
    assert [e.name for e in report.events if e.name == "breakeven"] == []
    assert updated.stop == 100.25


@pytest.mark.unit
def test_stop_never_loosens_via_trail():
    """Trail candidate below the current stop never loosens it."""
    trade = make_trade(
        fired={"breakeven": "10:30", "partial": "10:35"},
        stop=101.5, target=None,
        entry_time=DEFAULT_AS_OF.replace(hour=9, minute=30),
    )
    # highest close 101.5 - 1.0 ATR = 100.5 -> candidate looser than current stop 101.5
    updated, _ = evaluate_management(snap_at(101.5), result_at(101.5), trade)
    assert updated.stop == pytest.approx(101.5)


@pytest.mark.unit
def test_partial_single_contract_degrades_to_stop_lock():
    trade = make_trade(fired={"breakeven": "10:30"}, stop=100.25, target=None)
    # +1.5R exactly: partial fires; 1 contract -> tighten stop, not sell
    updated, report = evaluate_management(snap_at(103.0), result_at(103.0), trade)
    assert "partial" in updated.fired
    assert updated.remaining == 1
    # lock = entry + r_now(1.5) * 0.5 * risk(2.0) = 101.50
    assert updated.stop == pytest.approx(101.5)
    assert any("1-contract" in e.detail for e in report.events)


@pytest.mark.unit
def test_partial_multi_contract_reduces_remaining():
    trade = make_trade(contracts=2, remaining=2, target=None)
    updated, report = evaluate_management(snap_at(103.0), result_at(103.0), trade)
    assert updated.remaining == 1
    assert updated.realized_r == pytest.approx(1.5)  # 1 closed at +1.5R (per-contract credit)
    assert "partial" in [e.name for e in report.events]


@pytest.mark.unit
def test_trail_rides_atr_after_partial():
    trade = make_trade(
        fired={"breakeven": "10:30", "partial": "10:40"},
        stop=100.5, target=None,
        entry_time=DEFAULT_AS_OF.replace(hour=9, minute=30),
    )
    # highest close since entry = 103.25; trail = 103.25 - 1.0*1.0 = 102.25
    updated, _ = evaluate_management(snap_at(103.25), result_at(103.25), trade)
    assert updated.stop == pytest.approx(102.25)


@pytest.mark.unit
def test_time_stop_flattens_before_exit():
    cfg = load_mes_config({"exit_time": "11:15", "time_stop_buffer_minutes": 10})
    as_of = DEFAULT_AS_OF.replace(hour=11, minute=5)  # 11:15 - 10 min
    updated, report = evaluate_management(
        snap_at(100.2, as_of=as_of), result_at(100.2), make_trade(target=None), cfg
    )
    assert report.recommendation == "FLATTEN"
    assert report.events[0].name == "time_stop"
    assert updated.remaining == 0


@pytest.mark.unit
def test_confluence_flip_is_advisory_by_default():
    trade = make_trade(target=None)
    result = result_at(100.5)
    result.spy_confluence_ok = False
    _, report = evaluate_management(snap_at(100.5), result, trade)
    assert report.recommendation == "EXIT"
    assert any("confluence" in r.lower() for r in report.reasons)


@pytest.mark.unit
def test_exit_on_confluence_loss_hard_closes():
    cfg = load_mes_config({"exit_on_confluence_loss": True})
    result = result_at(100.5)
    result.spy_confluence_ok = False
    updated, report = evaluate_management(snap_at(100.5), result, make_trade(target=None), cfg)
    assert report.recommendation == "CLOSED"
    assert updated.remaining == 0
    assert "confluence_exit" in updated.fired


@pytest.mark.unit
def test_management_types_are_public():
    import tradingagents.mes as pkg

    for name in ("OpenTrade", "MgmtEvent", "MgmtReport", "evaluate_management"):
        assert hasattr(pkg, name), f"tradingagents.mes.{name} must be re-exported"
