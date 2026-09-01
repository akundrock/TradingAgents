"""Prior-session levels, overnight range, and pre-open snapshot behaviour."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from tradingagents.mes.config import load_mes_config
from tradingagents.mes.profile import volume_profile
from tradingagents.mes.render import render_checklist, render_market_context
from tradingagents.mes.checklist import evaluate
from tradingagents.mes.snapshot import (
    Bar,
    MesSnapshot,
    _replay,
    build_snapshot,
    overnight_range,
    prior_session_levels,
)

PRIOR_OPEN = datetime(2026, 3, 30, 9, 30)
TODAY = datetime(2026, 3, 31)


@pytest.fixture()
def cfg():
    return load_mes_config()


def _bar(ts: datetime, price: float, volume: float = 1000.0) -> Bar:
    return Bar(ts, price, price + 1.0, price - 1.0, price + 0.5, volume)


def _two_day_bars() -> list[Bar]:
    """Prior RTH session, an overnight globex stretch, then today's pre-market."""
    bars = [
        _bar(PRIOR_OPEN + timedelta(minutes=5 * i), 6400.0 + (i % 7), 1000.0 + (500.0 if i % 7 == 3 else 0.0))
        for i in range(78)
    ]
    bars += [_bar(datetime(2026, 3, 30, 18, 0) + timedelta(minutes=5 * i), 6410.0, 100.0) for i in range(30)]
    bars += [_bar(TODAY.replace(hour=8, minute=30) + timedelta(minutes=5 * i), 6420.0, 200.0) for i in range(12)]
    return bars


# ---------------------------------------------------------------------------
# Volume profile
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_poc_lands_on_the_heaviest_traded_price():
    bars = [_bar(PRIOR_OPEN, 100.0, 100.0), _bar(PRIOR_OPEN, 200.0, 5000.0)]
    profile = volume_profile(bars, tick_size=0.25)
    assert profile is not None
    assert 199.0 <= profile.poc <= 201.0


@pytest.mark.unit
def test_value_area_brackets_the_poc():
    profile = volume_profile(_two_day_bars()[:78], tick_size=0.25)
    assert profile is not None
    assert profile.val <= profile.poc <= profile.vah


@pytest.mark.unit
@pytest.mark.parametrize(
    "bars, tick_size",
    [
        ([], 0.25),
        ([_bar(PRIOR_OPEN, 100.0, 0.0)], 0.25),
        ([_bar(PRIOR_OPEN, 100.0, 10.0)], 0.0),
        ([_bar(PRIOR_OPEN, 100.0, 10.0)], -1.0),
    ],
)
def test_profile_returns_none_for_unusable_input(bars, tick_size):
    assert volume_profile(bars, tick_size=tick_size) is None


@pytest.mark.unit
def test_wider_value_area_never_narrows_the_range():
    bars = _two_day_bars()[:78]
    narrow = volume_profile(bars, tick_size=0.25, value_area=0.5)
    wide = volume_profile(bars, tick_size=0.25, value_area=0.9)
    assert narrow is not None and wide is not None
    assert wide.vah - wide.val >= narrow.vah - narrow.val


# ---------------------------------------------------------------------------
# Prior session levels
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prior_session_uses_the_last_completed_rth_day(cfg):
    levels = prior_session_levels(_two_day_bars(), TODAY.replace(hour=8, minute=55), cfg, cfg.mes_tick_size)
    assert levels is not None
    assert levels.session_date == "2026-03-30"
    assert levels.high == 6407.0
    assert levels.low == 6399.0
    assert levels.close == 6400.5
    assert levels.low <= levels.vwap <= levels.high
    assert levels.val <= levels.poc <= levels.vah


@pytest.mark.unit
def test_prior_session_ignores_overnight_and_premarket_bars(cfg):
    """Globex prints at 6410 sit above the prior RTH high and must not leak in."""
    levels = prior_session_levels(_two_day_bars(), TODAY.replace(hour=8, minute=55), cfg, cfg.mes_tick_size)
    assert levels is not None
    assert levels.high == 6407.0


@pytest.mark.unit
def test_prior_session_is_none_without_an_earlier_day(cfg):
    bars = [_bar(PRIOR_OPEN + timedelta(minutes=5 * i), 6400.0) for i in range(10)]
    assert prior_session_levels(bars, PRIOR_OPEN.replace(hour=11), cfg, cfg.mes_tick_size) is None


@pytest.mark.unit
def test_overnight_range_spans_prior_close_to_as_of(cfg):
    result = overnight_range(_two_day_bars(), TODAY.replace(hour=8, minute=55), cfg)
    assert result is not None
    high, low = result
    assert high == 6421.0
    assert low == 6409.0


@pytest.mark.unit
def test_overnight_range_is_none_without_a_prior_session(cfg):
    bars = [_bar(PRIOR_OPEN + timedelta(minutes=5 * i), 6400.0) for i in range(10)]
    assert overnight_range(bars, PRIOR_OPEN.replace(hour=11), cfg) is None


# ---------------------------------------------------------------------------
# Pre-open snapshot behaviour
# ---------------------------------------------------------------------------


def _frame(start: datetime, count: int) -> pd.DataFrame:
    rows = [
        {
            "Date": start + timedelta(minutes=5 * i),
            "Open": 100.0,
            "High": 101.0,
            "Low": 99.0,
            "Close": 100.5,
            "Volume": 1000.0,
        }
        for i in range(count)
    ]
    return pd.DataFrame(rows)


@pytest.mark.unit
def test_internals_are_not_requested_before_the_open(cfg):
    calls = []

    def fetch_internals(*args, **kwargs):
        calls.append(args)
        raise AssertionError("internals must not be fetched pre-market")

    snapshot = build_snapshot(
        TODAY.replace(hour=8, minute=40),
        cfg,
        fetch_bars=lambda *a, **k: _frame(datetime(2026, 3, 30, 9, 30), 100),
        fetch_internals=fetch_internals,
    )

    assert calls == []
    assert any("do not publish until after" in w for w in snapshot.warnings)
    assert snapshot.rth_started is False


@pytest.mark.unit
def test_internals_are_requested_once_the_session_is_underway(cfg):
    calls = []

    def fetch_internals(session_start, as_of, interval):
        calls.append((session_start, as_of, interval))
        return pd.DataFrame(columns=["Date", "add", "tick", "vold"])

    build_snapshot(
        TODAY.replace(hour=11, minute=0),
        cfg,
        fetch_bars=lambda *a, **k: _frame(TODAY.replace(hour=9, minute=30), 18),
        fetch_internals=fetch_internals,
    )

    assert len(calls) == 1
    assert calls[0][2] == "5m"


@pytest.mark.unit
def test_premarket_context_reports_unset_levels_instead_of_zero(cfg):
    bars = _two_day_bars()
    as_of = TODAY.replace(hour=8, minute=55)
    snapshot = MesSnapshot(
        as_of=as_of,
        session_date="2026-03-31",
        config=cfg,
        mes=_replay("/MES", bars, cfg),
        spy=_replay("SPY", bars, cfg),
        prior_mes=prior_session_levels(bars, as_of, cfg, cfg.mes_tick_size),
        prior_spy=prior_session_levels(bars, as_of, cfg, cfg.spy_tick_size),
        overnight_mes=overnight_range(bars, as_of, cfg),
    )
    context = render_market_context(snapshot)

    assert "not yet set" in context
    assert "0.00" not in context.split("### Prior session")[0]
    assert "Prior session (2026-03-30)" in context
    assert "Overnight range" in context


@pytest.mark.unit
def test_intraday_context_still_reports_live_session_levels(cfg):
    bars = _two_day_bars() + [
        _bar(TODAY.replace(hour=9, minute=30) + timedelta(minutes=5 * i), 6430.0) for i in range(12)
    ]
    as_of = TODAY.replace(hour=10, minute=25)
    snapshot = MesSnapshot(
        as_of=as_of,
        session_date="2026-03-31",
        config=cfg,
        mes=_replay("/MES", bars, cfg),
        spy=_replay("SPY", bars, cfg),
        prior_mes=prior_session_levels(bars, as_of, cfg, cfg.mes_tick_size),
    )
    context = render_market_context(snapshot)

    assert snapshot.rth_started is True
    assert "not yet set" not in context


@pytest.mark.unit
def test_pre_open_checklist_renders_unset_atr_bands(cfg):
    bars = _two_day_bars()
    as_of = TODAY.replace(hour=8, minute=55)
    snapshot = MesSnapshot(
        as_of=as_of,
        session_date="2026-03-31",
        config=cfg,
        mes=_replay("/MES", bars, cfg),
        spy=_replay("SPY", bars, cfg),
        prior_mes=prior_session_levels(bars, as_of, cfg, cfg.mes_tick_size),
        prior_spy=prior_session_levels(bars, as_of, cfg, cfg.spy_tick_size),
        overnight_mes=overnight_range(bars, as_of, cfg),
    )
    result = evaluate(snapshot, "long")
    checklist = render_checklist(result, live=False)

    assert "bands: not yet set" in checklist
    assert "VWAP: not yet set" in checklist
    assert "0.00 / 0.00" not in checklist

    atr_item = next(i for i in result.mes_items if "ATR session bands" in i.name)
    assert atr_item.observed == "session not started"
