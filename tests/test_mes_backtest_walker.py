"""W2.1 walker tests: replay must equal the live checklist at every bar, with no lookahead."""

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from tradingagents.mes.backtest import ReplaySession, discover_sessions, load_session
from tradingagents.mes.checklist import evaluate
from tradingagents.mes.config import load_mes_config
from tradingagents.mes.radar import build_proximity
from tradingagents.mes.snapshot import load_csv_bars, snapshot_from_bars, snapshot_from_csv
from tradingagents.mes.levels import suggest_trade_levels_from_snapshot

FIXTURES = Path(__file__).parent / "fixtures"
MES_CSV = FIXTURES / "mes_sample_5m.csv"
SPY_CSV = FIXTURES / "spy_sample_5m.csv"

CSV_HEADER = "timestamp,open,high,low,close,volume,add,tick,vold\n"


def _write_csv(path: Path, rows: list[str]) -> Path:
    path.write_text(CSV_HEADER + "".join(rows), encoding="utf-8")
    return path


@pytest.fixture()
def cfg():
    return load_mes_config()


@pytest.mark.unit
def test_snapshot_from_bars_matches_snapshot_from_csv(cfg):
    """The pre-loaded bars path must build exactly the snapshot the CSV path builds."""
    mes_bars = load_csv_bars(MES_CSV)
    spy_bars = load_csv_bars(SPY_CSV)
    as_of = datetime(2026, 3, 30, 10, 15)

    from_csv = snapshot_from_csv(MES_CSV, SPY_CSV, as_of, cfg)
    from_bars = snapshot_from_bars(mes_bars, spy_bars, as_of, cfg)
    assert from_bars == from_csv


@pytest.mark.unit
def test_load_csv_bars_sorts_and_strips_timezone():
    bars = load_csv_bars(MES_CSV)
    assert bars == sorted(bars, key=lambda bar: bar.timestamp)
    assert {bar.timestamp.tzinfo for bar in bars} == {None}
    assert bars[-1].timestamp <= datetime(2026, 3, 30, 23, 59)


@pytest.mark.unit
def test_walker_matches_snapshot_from_csv_at_every_bar(cfg):
    """Every walked record must equal a fresh snapshot_from_csv + evaluate at that bar."""
    session = ReplaySession(
        mes_bars=load_csv_bars(MES_CSV), spy_bars=load_csv_bars(SPY_CSV), cfg=cfg
    )
    walked = session.walk()

    assert walked.records, "walker produced no records"
    by_timestamp = {record.timestamp: record for record in walked.records}
    assert len(by_timestamp) == len(session.mes_bars)
    for bar in session.mes_bars:
        record = by_timestamp[bar.timestamp]
        snapshot = snapshot_from_csv(MES_CSV, SPY_CSV, bar.timestamp, cfg)
        result = evaluate(snapshot, "auto")
        proximity = build_proximity(snapshot, result)
        levels = None
        if result.tradeable:
            levels = suggest_trade_levels_from_snapshot(result.side, snapshot, result)
        assert record.tradeable == result.tradeable
        assert record.tier == result.tier
        assert record.score == result.score
        assert record.max_score == result.max_score
        assert record.side == str(result.side)
        assert record.state == str(proximity.state.value)
        assert record.last_price == result.last_price
        assert record.gates_ok == result.gates_ok
        assert record.gate_reasons == list(result.gate_reasons)
        assert record.no_trade_reasons == list(result.no_trade_reasons)
        assert record.spy_confirmations == result.spy_confirmations
        assert record.entry_zone == (levels.entry_zone if levels else None)
        assert record.stop == (levels.stop_level if levels else None)
        assert record.first_target == (levels.first_target if levels else None)


@pytest.mark.unit
def test_walker_has_no_lookahead(cfg):
    """Poisoning future bars must not change any record at or before the cut."""
    session = ReplaySession(
        mes_bars=load_csv_bars(MES_CSV), spy_bars=load_csv_bars(SPY_CSV), cfg=cfg
    )
    full = session.walk()

    cut = datetime(2026, 3, 30, 10, 30)
    poisoned_mes = [
        replace(bar, close=bar.close + 500.0, high=bar.high + 500.0, low=bar.low + 500.0)
        for bar in session.mes_bars
        if bar.timestamp > cut
    ]
    poisoned_spy = [
        replace(bar, close=bar.close + 50.0) for bar in session.spy_bars if bar.timestamp > cut
    ]
    truncated = ReplaySession(
        mes_bars=[bar for bar in session.mes_bars if bar.timestamp <= cut] + poisoned_mes,
        spy_bars=[bar for bar in session.spy_bars if bar.timestamp <= cut] + poisoned_spy,
        cfg=cfg,
    )
    partial = truncated.walk()

    prefix_full = [record for record in full.records if record.timestamp <= cut]
    assert [
        record.timestamp for record in partial.records if record.timestamp <= cut
    ] == [record.timestamp for record in prefix_full]
    for fresh, old in zip(partial.records, prefix_full):
        assert (fresh.tradeable, fresh.tier, fresh.score, fresh.last_price) == (
            old.tradeable,
            old.tier,
            old.score,
            old.last_price,
        )
        assert fresh.no_trade_reasons == old.no_trade_reasons
        assert fresh.entry_zone == old.entry_zone and fresh.stop == old.stop


def _session_rows(date: str, start_hour: int, count: int, *, base: float = 6450.0) -> list[str]:
    """Consecutive 5-minute bar rows (tz-stamped like the recorder writes them)."""
    rows = []
    for index in range(count):
        total_minutes = start_hour * 60 + 5 * index
        hour, minute = divmod(total_minutes, 60)
        rows.append(
            f"{date}T{hour:02d}:{minute:02d}:00-0400,"
            f"{base},{base + 2},{base - 2},{base + 0.5},1000,1718.0,754.0,44394963.0\n"
        )
    return rows


@pytest.mark.unit
def test_discover_sessions_pairs_and_excludes(tmp_path):
    _write_csv(tmp_path / "mes_2026-03-25.csv", _session_rows("2026-03-25", 10, 6))
    _write_csv(tmp_path / "spy_2026-03-25.csv", _session_rows("2026-03-25", 10, 6, base=640.0))

    _write_csv(tmp_path / "mes_2026-03-24.csv", [])  # header-only recording
    _write_csv(tmp_path / "spy_2026-03-24.csv", _session_rows("2026-03-24", 10, 6))

    _write_csv(tmp_path / "mes_2026-03-23.csv", _session_rows("2026-03-23", 10, 6))
    _write_csv(tmp_path / "spy_2026-03-22.csv", _session_rows("2026-03-22", 10, 6))  # no MES partner

    candidates = discover_sessions(tmp_path, min_bars=5)
    by_date = {candidate.date: candidate for candidate in candidates}

    assert set(by_date) == {"2026-03-23", "2026-03-24", "2026-03-25"}
    assert by_date["2026-03-25"].excluded is None
    assert by_date["2026-03-25"].spy_path == tmp_path / "spy_2026-03-25.csv"
    assert by_date["2026-03-25"].mes_bars == 6 and by_date["2026-03-25"].spy_bars == 6
    assert by_date["2026-03-24"].excluded and "0 data bars" in by_date["2026-03-24"].excluded
    assert "spy" in by_date["2026-03-23"].excluded

    bounded = discover_sessions(tmp_path, start="2026-03-24", end="2026-03-25", min_bars=5)
    assert [candidate.date for candidate in bounded] == ["2026-03-24", "2026-03-25"]


@pytest.mark.unit
def test_walker_skips_bars_before_both_series_have_data(tmp_path, cfg):
    date = "2026-03-25"
    mes_path = _write_csv(tmp_path / f"mes_{date}.csv", _session_rows(date, 11, 6))
    spy_path = _write_csv(tmp_path / f"spy_{date}.csv", _session_rows(date, 11, 6, base=640.0))

    session = load_session(mes_path, spy_path, cfg)
    # Drop the first three SPY bars: the session now starts with SPY data 15 minutes late.
    session.spy_bars = session.spy_bars[3:]
    walked = session.walk()

    first_spy = session.spy_bars[0].timestamp
    assert walked.skipped_bars == 3
    assert all(record.timestamp >= first_spy for record in walked.records)
    assert walked.session_date == date


@pytest.mark.unit
def test_walker_warns_once_when_recording_has_no_internals(tmp_path, cfg):
    date = "2026-03-25"
    mes_rows = [
        f"{date}T11:{5 * index:02d}:00-0400,6450,6452,6448,6450.5,1000,,,\n"
        for index in range(6)
    ]
    spy_rows = [
        f"{date}T11:{5 * index:02d}:00-0400,640,640.3,639.7,640.1,500000,,,\n"
        for index in range(6)
    ]
    mes_path = _write_csv(tmp_path / f"mes_{date}.csv", mes_rows)
    spy_path = _write_csv(tmp_path / f"spy_{date}.csv", spy_rows)

    walked = load_session(mes_path, spy_path, cfg).walk()

    assert len(walked.records) == 6
    assert len(walked.warnings) == 2
    assert any("MES recording" in warning for warning in walked.warnings)
    assert any("SPY recording" in warning for warning in walked.warnings)


@pytest.mark.unit
def test_load_session_defaults_config(tmp_path):
    mes_path = _write_csv(tmp_path / "mes_2026-03-25.csv", _session_rows("2026-03-25", 11, 6))
    spy_path = _write_csv(
        tmp_path / "spy_2026-03-25.csv", _session_rows("2026-03-25", 11, 6, base=640.0)
    )

    session = load_session(mes_path, spy_path)
    assert session.cfg == load_mes_config()
    assert len(session.mes_bars) == 6 and len(session.spy_bars) == 6
    assert session.mes_bars[0].timestamp == datetime(2026, 3, 25, 11, 0)
    assert session.spy_bars[0].close == 640.5
