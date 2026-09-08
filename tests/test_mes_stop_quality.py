"""Unit tests for the pure stop-quality analysis module.

Fixtures are plain dicts shaped exactly like the JSONL records written by
``MesJournal.append_trade_opened`` / ``append_trade_closed`` / ``append_check``
(see tradingagents/mes/journal.py) so the tests pin the real on-disk contract.
"""

from __future__ import annotations

import pytest

from tradingagents.mes.config import MesChecklistConfig
from tradingagents.mes.stop_quality import (
    StopQualityReport,
    StopQualityRow,
    build_stop_quality_report,
)

DATE = "2026-03-30"


def opened_record(
    *,
    side: str = "short",
    entry: float = 7692.50,
    stop: float = 7697.54,
    entry_time: str = f"{DATE}T11:00",
    entry_context: dict | None = None,
    initial_risk_points: float | None = None,
) -> dict:
    """Shape mirrors MesJournal.append_trade_opened."""
    return {
        "kind": "trade_opened",
        "logged_at": f"{entry_time[:10]}T11:00:01.123456",
        "session_date": entry_time[:10],
        "side": side,
        "contracts": 1,
        "remaining": 1,
        "entry": entry,
        "stop": stop,
        "initial_stop": stop,
        "target": 7682.0,
        "entry_time": entry_time,
        "initial_risk_points": (
            initial_risk_points
            if initial_risk_points is not None
            else round(abs(entry - stop), 4)
        ),
        "fired": {},
        "manual_events": [],
        "realized_r": 0.0,
        "entry_context": entry_context if entry_context is not None else {"atr": 5.04},
    }


def closed_record(
    *,
    side: str = "short",
    entry: float = 7692.50,
    stop: float = 7697.54,
    exit_price: float = 7697.54,
    reason: str = "stop",
    realized_r: float = -1.0,
    mfe_r: float = 0.25,
    mae_r: float = -1.0,
    entry_time: str = f"{DATE}T11:00",
    as_of: str = f"{DATE}T11:30",
) -> dict:
    """Shape mirrors MesJournal.append_trade_closed's record."""
    return {
        "kind": "trade_closed",
        "logged_at": f"{as_of[:10]}T11:30:00.654321",
        "as_of": as_of,
        "side": side,
        "entry": entry,
        "stop": stop,
        "exit_price": exit_price,
        "reason": reason,
        "realized_r": realized_r,
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "fired": {},
        "manual_events": [],
        "entry_time": entry_time,
    }


def check_record(as_of: str, atr: float) -> dict:
    """Shape mirrors MesJournal.append_check (only fields stop_quality reads)."""
    return {
        "kind": "check",
        "logged_at": f"{as_of[:10]}T{as_of[11:]}:00.123456",
        "as_of": as_of,
        "session_date": as_of[:10],
        "side": "long",
        "atr": atr,
    }


@pytest.fixture()
def cfg() -> MesChecklistConfig:
    return MesChecklistConfig()


@pytest.mark.unit
class TestTooTightFlag:
    def test_stopped_out_with_high_mfe_is_flagged(self, cfg):
        trades = [
            opened_record(side="short", entry=7692.50, stop=7697.54),
            closed_record(side="short", reason="stopped_out", mfe_r=1.8, mae_r=-1.0),
        ]
        report = build_stop_quality_report(trades, [], cfg)
        assert len(report.rows) == 1
        row = report.rows[0]
        assert "stop likely too tight (MFE reached +1.80R before stop-out)" in row.flags

    def test_stopped_out_with_low_mfe_is_not_flagged(self, cfg):
        trades = [
            opened_record(),
            closed_record(reason="stop", mfe_r=0.9),
        ]
        report = build_stop_quality_report(trades, [], cfg)
        assert report.rows[0].flags == []

    def test_reason_match_is_case_insensitive(self, cfg):
        trades = [
            opened_record(),
            closed_record(reason="STOPPED_OUT", mfe_r=2.0),
        ]
        report = build_stop_quality_report(trades, [], cfg)
        assert any(f.startswith("stop likely too tight") for f in report.rows[0].flags)

    def test_mfe_exactly_at_partial_threshold_flags(self, cfg):
        # partial_at_r defaults to 1.5; >= is the spec'd comparison.
        trades = [opened_record(), closed_record(reason="stop", mfe_r=1.5)]
        report = build_stop_quality_report(trades, [], cfg)
        assert any(f.startswith("stop likely too tight") for f in report.rows[0].flags)


@pytest.mark.unit
class TestTooWideFlag:
    def test_target_exit_with_tiny_mae_is_flagged(self, cfg):
        trades = [
            opened_record(),
            closed_record(reason="target", mfe_r=1.2, mae_r=-0.2, realized_r=1.5),
        ]
        report = build_stop_quality_report(trades, [], cfg)
        row = report.rows[0]
        assert "stop likely too wide (MAE never approached -0.5R)" in row.flags

    def test_manual_exit_with_deep_mae_is_not_flagged(self, cfg):
        trades = [
            opened_record(),
            closed_record(reason="manual", mae_r=-0.8),
        ]
        report = build_stop_quality_report(trades, [], cfg)
        assert not any(f.startswith("stop likely too wide") for f in report.rows[0].flags)

    def test_mae_exactly_at_half_r_does_not_flag(self, cfg):
        trades = [opened_record(), closed_record(reason="target", mae_r=-0.5)]
        report = build_stop_quality_report(trades, [], cfg)
        assert not any(f.startswith("stop likely too wide") for f in report.rows[0].flags)


@pytest.mark.unit
class TestAtrResolution:
    def test_stop_atr_multiple_from_entry_context(self, cfg):
        trades = [opened_record(entry_context={"atr": 5.04}), closed_record()]
        report = build_stop_quality_report(trades, [], cfg)
        row = report.rows[0]
        assert row.atr == pytest.approx(5.04)
        assert row.stop_atr_multiple == pytest.approx(1.0)

    def test_falls_back_to_latest_check_before_entry(self, cfg):
        trades = [
            opened_record(entry_context={}, entry_time=f"{DATE}T11:00"),
            closed_record(entry_time=f"{DATE}T11:00"),
        ]
        checks = [
            {"kind": "check", "as_of": f"{DATE}T10:45:00", "atr": 6.0},
            {"kind": "check", "as_of": f"{DATE}T11:30:00", "atr": 9.0},  # after entry
        ]
        report = build_stop_quality_report(trades, checks, cfg)
        row = report.rows[0]
        assert row.atr == pytest.approx(6.0)
        assert row.stop_atr_multiple == pytest.approx(5.04 / 6.0)

    def test_zero_atr_in_entry_context_falls_back_to_checks(self, cfg):
        trades = [
            opened_record(entry_context={"atr": 0.0}),
            closed_record(),
        ]
        checks = [{"kind": "check", "as_of": f"{DATE}T10:30:00", "atr": 4.2}]
        report = build_stop_quality_report(trades, checks, cfg)
        assert report.rows[0].atr == pytest.approx(4.2)

    def test_no_atr_anywhere_leaves_multiple_none(self, cfg):
        trades = [opened_record(entry_context={}), closed_record()]
        report = build_stop_quality_report(trades, [], cfg)
        row = report.rows[0]
        assert row.atr is None
        assert row.stop_atr_multiple is None

    def test_check_without_parseable_timestamp_is_ignored(self, cfg):
        trades = [opened_record(entry_context={}), closed_record()]
        checks = [{"kind": "check", "as_of": "not-a-timestamp", "atr": 9.0}]
        report = build_stop_quality_report(trades, checks, cfg)
        assert report.rows[0].atr is None


@pytest.mark.unit
class TestBelowOneAtrFlag:
    def test_multiple_below_one_flags(self, cfg):
        # stop_points 5.04 vs atr 8.0 -> 0.63x
        trades = [opened_record(entry_context={"atr": 8.0}), closed_record()]
        report = build_stop_quality_report(trades, [], cfg)
        assert "initial stop below 1x ATR" in report.rows[0].flags

    def test_multiple_at_or_above_one_does_not_flag(self, cfg):
        trades = [opened_record(entry_context={"atr": 5.0}), closed_record()]
        report = build_stop_quality_report(trades, [], cfg)
        assert "initial stop below 1x ATR" not in report.rows[0].flags

    def test_no_atr_means_no_flag(self, cfg):
        trades = [opened_record(entry_context={}), closed_record()]
        report = build_stop_quality_report(trades, [], cfg)
        assert "initial stop below 1x ATR" not in report.rows[0].flags


@pytest.mark.unit
class TestPairing:
    def test_open_trade_produces_no_row(self, cfg):
        report = build_stop_quality_report([opened_record()], [], cfg)
        assert report.is_empty()
        assert report.summary_lines() == []

    def test_two_open_close_sequences_pair_in_order(self, cfg):
        trades = [
            opened_record(side="short", entry=7692.50, stop=7697.54,
                          entry_time=f"{DATE}T10:30"),
            closed_record(side="short", entry=7692.50, entry_time=f"{DATE}T10:30",
                          reason="target", mae_r=-0.1),
            opened_record(side="long", entry=7700.00, stop=7695.00,
                          entry_time=f"{DATE}T13:00",
                          entry_context={"atr": 5.0}),
            closed_record(side="short", entry=7692.50, entry_time=f"{DATE}T13:00",
                          reason="eod", realized_r=0.4),
        ]
        report = build_stop_quality_report(trades, [], cfg)
        assert len(report.rows) == 2
        assert report.rows[0].entry == pytest.approx(7692.50)
        assert report.rows[0].exit_reason == "target"
        assert report.rows[1].entry == pytest.approx(7700.00)
        assert report.rows[1].exit_reason == "eod"

    def test_adjusted_records_do_not_break_pairing(self, cfg):
        trades = [
            opened_record(),
            {"kind": "trade_adjusted", "stop": 7695.0, "note": "breakeven"},
            closed_record(reason="stop"),
        ]
        report = build_stop_quality_report(trades, [], cfg)
        assert len(report.rows) == 1
        # Initial-stop analysis uses the opened record's stop, not the adjusted one.
        assert report.rows[0].stop == pytest.approx(7697.54)


@pytest.mark.unit
class TestRowFields:
    def test_row_carries_core_trade_fields(self, cfg):
        trades = [opened_record(), closed_record(reason="target", realized_r=2.1,
                                                 mfe_r=2.1, mae_r=-0.3)]
        report = build_stop_quality_report(trades, [], cfg)
        row = report.rows[0]
        assert isinstance(row, StopQualityRow)
        assert row.side == "short"
        assert row.entry == pytest.approx(7692.50)
        assert row.stop == pytest.approx(7697.54)
        assert row.stop_points == pytest.approx(5.04)
        assert row.mae_r == pytest.approx(-0.3)
        assert row.mfe_r == pytest.approx(2.1)
        assert row.exit_reason == "target"

    def test_missing_excursion_fields_are_none_not_zero(self, cfg):
        closed = closed_record()
        del closed["mfe_r"]
        del closed["mae_r"]
        del closed["realized_r"]
        report = build_stop_quality_report([opened_record(), closed], [], cfg)
        row = report.rows[0]
        assert row.mfe_r is None
        assert row.mae_r is None
        assert row.realized_r is None


@pytest.mark.unit
class TestSummary:
    def test_empty_inputs_give_empty_report(self, cfg):
        report = build_stop_quality_report([], [], cfg)
        assert report.is_empty()
        assert report.rows == []
        assert report.summary_lines() == []

    def test_unflagged_rows_still_count_in_aggregate(self, cfg):
        # Clean target exit with a healthy stop: no flags, but the aggregate
        # line is omitted entirely when there are no flags at all.
        trades = [opened_record(), closed_record(reason="target", mae_r=-0.8)]
        report = build_stop_quality_report(trades, [], cfg)
        assert not report.is_empty()
        assert report.summary_lines() == []

    def test_aggregate_line_counts_tight_and_wide(self, cfg):
        trades = [
            opened_record(entry_time=f"{DATE}T10:30"),
            closed_record(entry_time=f"{DATE}T10:30", reason="stopped_out", mfe_r=1.8),
            opened_record(side="long", entry=7700.00, stop=7695.00,
                          entry_time=f"{DATE}T13:00",
                          entry_context={"atr": 5.0}),
            closed_record(side="long", entry=7700.00, entry_time=f"{DATE}T13:00",
                          reason="target", mae_r=-0.1, realized_r=1.0),
        ]
        report = build_stop_quality_report(trades, [], cfg)
        lines = report.summary_lines()
        assert lines[0] == "short @ 7692.50: stop likely too tight (MFE reached +1.80R before stop-out)"
        assert lines[1] == "long @ 7700.00: stop likely too wide (MAE never approached -0.5R)"
        assert lines[-1] == "2 trades: 1 too-tight flags, 1 too-wide flags"

    def test_below_atr_flag_appears_in_summary(self, cfg):
        trades = [opened_record(entry_context={"atr": 8.0}), closed_record(reason="eod")]
        report = build_stop_quality_report(trades, [], cfg)
        assert report.summary_lines() == [
            "short @ 7692.50: initial stop below 1x ATR",
            "1 trades: 0 too-tight flags, 0 too-wide flags",
        ]


@pytest.mark.unit
def test_cfg_none_uses_default_partial_threshold():
    trades = [opened_record(), closed_record(reason="stop", mfe_r=1.6)]
    report = build_stop_quality_report(trades, [], None)
    assert any(f.startswith("stop likely too tight") for f in report.rows[0].flags)
