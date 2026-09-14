"""W2.2 outcome-joiner tests: known-outcome bar sequences through the joiner."""

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tradingagents.mes.backtest import ReplayRecord, join_outcomes
from tradingagents.mes.config import load_mes_config
from tradingagents.mes.snapshot import Bar, load_csv_bars

FIXTURES = Path(__file__).parent / "fixtures"
MES_CSV = FIXTURES / "mes_sample_5m.csv"
SPY_CSV = FIXTURES / "spy_sample_5m.csv"

START = datetime(2026, 3, 25, 11)


def _bar(minute: int, o: float, h: float, l: float, c: float) -> Bar:
    ts = datetime(2026, 3, 25, 11) + timedelta(minutes=5 * minute)
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=1000.0)


def _ts(minute: int) -> datetime:
    return datetime(2026, 3, 25, 11) + timedelta(minutes=5 * minute)


def _record(
    minute: int,
    *,
    side: str,
    stop: float | None,
    target: float | None,
    entry: float = 100.0,
    tier: str = "standard",
) -> ReplayRecord:
    """A tradeable verdict at ``minute`` with an explicit trade template."""
    return ReplayRecord(
        index=minute,
        timestamp=_ts(minute),
        session_date="2026-03-25",
        side=side,
        state="READY",
        tier=tier,
        tradeable=True,
        score=6,
        max_score=9,
        confirmations=3,
        required=3,
        gates_ok=True,
        gate_reasons=[],
        no_trade_reasons=[],
        spy_confirmations=3,
        spy_confluence_ok=True,
        divergence="",
        last_price=entry,
        entry_zone=f"{entry - 0.5:g}-{entry + 0.5:g}",
        stop=stop,
        first_target=(entry + 1.5) if side == "long" else (entry - 1.5),
    )


LONG_SIGNAL = dict(side="long", stop=99.0, target=101.5)  # 1R = 1 point


@pytest.mark.unit
def test_next_bar_open_entry_and_target_hit():
    bars = [
        _bar(0, 99.8, 100.1, 98.9, 100.0),  # signal bar (verdict fires at its close)
        _bar(1, 100.0, 100.4, 99.8, 100.2),  # entry bar: fill at the open, no touches
        _bar(2, 100.3, 101.6, 100.2, 101.4),  # target touch at 101.5
        _bar(3, 101.4, 101.8, 101.2, 101.6),  # post-exit bar
    ]
    records = [_record(0, **LONG_SIGNAL)]

    report = join_outcomes(records, bars, load_mes_config())

    assert report.entered == 1 and report.skipped_no_levels == 0
    outcome = report.outcomes[0]
    assert outcome.entry_price == 100.0  # next bar's open
    assert outcome.exit_reason == "target"
    assert outcome.exit_price == 101.5
    assert outcome.exit_timestamp == _ts(2)
    assert outcome.realized_r == pytest.approx(1.5)
    assert outcome.initial_risk_points == 1.0
    assert outcome.hold_bars == 2  # bars 1..2 held


@pytest.mark.unit
def test_stop_hit_with_gap_through_fills_at_open():
    bars = [
        _bar(0, 99.5, 100.0, 99.0, 99.8),  # signal bar
        _bar(1, 100.0, 100.3, 99.7, 99.9),  # entry at 100.0
        _bar(2, 98.5, 98.8, 98.4, 98.6),  # gaps through the 99 stop: fill at the open
    ]
    report = join_outcomes([_record(0, **LONG_SIGNAL)], bars, load_mes_config())

    assert len(report.outcomes) == 1
    outcome = report.outcomes[0]
    assert outcome.exit_reason == "stop"
    assert outcome.exit_price == 98.5
    assert outcome.realized_r == -1.5


@pytest.mark.unit
def test_breakeven_rung_tightens_stop_after_1r():
    bars = [
        _bar(0, 99.5, 100.0, 99.0, 99.8),  # signal
        _bar(1, 100.0, 100.9, 99.8, 100.8),  # entry 100.0; MFE 0.9R — rung not armed
        _bar(2, 100.8, 101.2, 100.6, 101.0),  # MFE 1.2R arms breakeven at this close
        _bar(3, 100.2, 100.3, 100.0, 100.1),  # pulls back: BE stop fills at entry
    ]
    report = join_outcomes([_record(0, **LONG_SIGNAL)], bars, load_mes_config())

    outcome = report.outcomes[0]
    assert outcome.exit_reason == "stop"
    assert outcome.exit_price == 100.0  # stopped at breakeven, not the initial 99
    assert outcome.realized_r == 0.0
    assert outcome.stop == 100.0 and outcome.initial_stop == 99.0
    assert outcome.mfe_r == 1.2
    assert outcome.mae_r == -0.2  # bar 1 dipped to 99.8 before the rung armed


@pytest.mark.unit
def test_eod_flatten_at_last_bar_close():
    bars = [
        _bar(0, 99.5, 100.0, 99.0, 99.8),
        _bar(1, 100.0, 100.4, 99.8, 100.2),  # entry 100.0, drifts up but never hits 101.5
        _bar(2, 100.2, 100.6, 100.1, 100.3),
        _bar(3, 100.3, 100.5, 100.0, 100.2),  # last bar: EOD flatten at its close
    ]
    report = join_outcomes([_record(0, **LONG_SIGNAL)], bars, load_mes_config())

    outcome = report.outcomes[0]
    assert outcome.exit_reason == "eod"
    assert outcome.exit_price == 100.2
    assert outcome.exit_timestamp == _ts(3)
    assert outcome.hold_bars == 3
    assert outcome.mfe_r == 0.6  # bar 2's high (100.6 vs the 100.0 entry)
    assert outcome.mae_r == -0.2  # bar 1's low of 99.8 (worst adverse excursion)


@pytest.mark.unit
def test_opposite_verdict_flips_position():
    bars = [
        _bar(0, 99.5, 100.0, 99.0, 99.8),  # long signal
        _bar(1, 100.0, 100.2, 99.8, 99.9),  # long fills at 100.0
        _bar(2, 99.9, 100.1, 99.5, 99.6),  # short verdict at this close -> flip exit
        _bar(3, 99.6, 99.8, 99.0, 99.2),  # short entry at open 99.6, stop 100.6, target 98.1
        _bar(4, 99.1, 99.4, 99.05, 99.3),  # short drifts toward target, no touch yet
        _bar(5, 99.0, 99.3, 98.0, 98.1),  # short target hit: low 98.0 <= 98.1
    ]
    records = [
        _record(0, **LONG_SIGNAL),
        _record(2, side="short", stop=100.6, target=98.1, entry=99.6),
    ]
    report = join_outcomes(records, bars, load_mes_config())

    assert len(report.outcomes) == 2
    long_out, short_out = report.outcomes
    assert long_out.side == "long" and long_out.exit_reason == "flip"
    assert long_out.exit_price == 99.6
    assert long_out.realized_r == -0.4
    assert long_out.exit_timestamp == _ts(2)
    assert short_out.side == "short"
    assert short_out.entry_price == 99.6
    assert short_out.exit_reason == "target"  # bar 5 low = 98.0 touches 98.1
    assert short_out.realized_r == 1.5


@pytest.mark.unit
def test_last_bar_verdict_never_fills():
    bars = [
        _bar(0, 99.5, 100.0, 99.0, 99.8),
        _bar(1, 99.8, 100.0, 99.5, 99.9),
        _bar(2, 99.9, 100.1, 99.7, 99.9),  # verdict fires here; no next bar exists
    ]
    signal = _record(2, **LONG_SIGNAL)
    report = join_outcomes([signal], bars, load_mes_config())

    assert report.outcomes == []
    assert report.skipped_no_fill_bar == 1


@pytest.mark.unit
def test_signal_without_levels_is_not_entered():
    bars = [_bar(0, 99.5, 100.0, 99.0, 99.8), _bar(1, 99.8, 100.2, 99.6, 100.0)]
    signal = _record(0, **LONG_SIGNAL)
    no_levels = replace(signal, stop=None, entry_zone=None, first_target=None)
    report = join_outcomes([no_levels], bars, load_mes_config())

    assert report.outcomes == []
    assert report.signals == 1
    assert report.skipped_no_levels == 1


@pytest.mark.unit
def test_fixture_session_joins_and_exits_are_valid():
    """End-to-end: the walker's real fixture-session records join to closed trades."""
    from tradingagents.mes.backtest import ReplaySession

    cfg = load_mes_config()
    session = ReplaySession(
        mes_bars=load_csv_bars(MES_CSV),
        spy_bars=load_csv_bars(SPY_CSV),
        cfg=cfg,
    )
    walked = session.walk()
    report = join_outcomes(walked.records, session.mes_bars, cfg)

    assert report.outcomes, "no trades joined from the fixture session"
    assert report.entered == len(report.outcomes)
    for outcome in report.outcomes:
        assert outcome.exit_reason in {"stop", "target", "flip", "eod"}
        assert outcome.entry_timestamp > outcome.signal_timestamp
        assert outcome.exit_timestamp >= outcome.entry_timestamp
        assert outcome.hold_bars >= 1
        assert outcome.mfe_r >= 0.0 and outcome.mae_r <= 0.0
        if outcome.exit_reason == "stop":
            assert outcome.realized_r <= 2.0  # gap-aware worst case
        if outcome.exit_reason == "target":
            assert outcome.realized_r > 0.0
