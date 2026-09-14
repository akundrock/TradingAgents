from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from tradingagents.mes.config import load_mes_config
from tradingagents.mes.snapshot import (
    Bar,
    MesSnapshot,
    build_snapshot,
    session_bounds,
    snapshot_from_csv,
)

from tests.mes_factories import make_snapshot, make_spy_series

FIXTURES = Path(__file__).parent / "fixtures"
MES_CSV = FIXTURES / "mes_sample_5m.csv"
SPY_CSV = FIXTURES / "spy_sample_5m.csv"
AS_OF = datetime(2026, 3, 30, 11, 30)


@pytest.fixture()
def csv_snapshot():
    return snapshot_from_csv(MES_CSV, SPY_CSV, AS_OF, load_mes_config())


@pytest.mark.unit
def test_snapshot_from_csv_stops_at_as_of(csv_snapshot):
    for state in (csv_snapshot.mes, csv_snapshot.spy):
        assert state.bars
        assert state.bars[-1].timestamp <= AS_OF
        assert state.bars == sorted(state.bars, key=lambda b: b.timestamp)
    assert csv_snapshot.mes.bars[-1].timestamp == AS_OF
    assert csv_snapshot.session_date == "2026-03-30"


@pytest.mark.unit
def test_snapshot_from_csv_produces_sane_levels(csv_snapshot):
    mes = csv_snapshot.mes
    session_low = min(b.low for b in mes.bars)
    session_high = max(b.high for b in mes.bars)

    assert mes.vwap_ready is True
    assert session_low <= mes.vwap <= session_high
    assert mes.atr_ready is True
    assert mes.atr > 0
    assert mes.opening_range_high >= mes.opening_range_low
    assert mes.lower_atr_band < mes.session_start_price < mes.upper_atr_band


@pytest.mark.unit
def test_snapshot_from_csv_opening_range_only_covers_the_first_bars(csv_snapshot):
    mes = csv_snapshot.mes
    or_bars = [b for b in mes.bars if b.timestamp.hour == 9 and b.timestamp.minute < 45]
    assert mes.opening_range_high == max(b.high for b in or_bars)
    assert mes.opening_range_low == min(b.low for b in or_bars)


@pytest.mark.unit
def test_snapshot_internals_come_from_the_last_spy_bar(csv_snapshot):
    last = csv_snapshot.spy.bars[-1]
    assert csv_snapshot.add == last.add
    assert csv_snapshot.tick == last.tick
    assert csv_snapshot.vold == last.vold
    assert csv_snapshot.add is not None


@pytest.mark.unit
def test_snapshot_from_csv_raises_when_nothing_precedes_as_of():
    with pytest.raises(ValueError):
        snapshot_from_csv(MES_CSV, SPY_CSV, datetime(2020, 1, 1), load_mes_config())


@pytest.mark.unit
@pytest.mark.parametrize(
    ("volds", "expected"),
    [
        ([1.0, 2.0, 3.0], 2.0),
        ([3.0, 2.0, 1.0], -2.0),
        ([5.0, 5.0], 0.0),
        ([5.0], None),
        ([], None),
    ],
)
def test_vold_slope_is_the_net_change_across_the_window(volds, expected):
    internals = [(0.0, 0.0, v) for v in volds] or [(0.0, 0.0, None)]
    snapshot = make_snapshot(spy=make_spy_series(internals=internals))
    assert snapshot.vold_slope() == expected


@pytest.mark.unit
def test_vold_slope_uses_only_the_configured_lookback():
    cfg = load_mes_config({"vold_slope_bars": 3})
    internals = [(0.0, 0.0, float(v)) for v in (100, 200, 10, 20, 30)]
    snapshot = make_snapshot(cfg=cfg, spy=make_spy_series(internals=internals))
    assert snapshot.vold_slope() == 20.0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ticks", "direction", "expected"),
    [
        ([700.0, 800.0, 900.0], "long", True),
        ([700.0, 500.0, 900.0], "long", False),
        ([-700.0, -800.0, -900.0], "short", True),
        ([-700.0, -800.0, -900.0], "long", False),
        ([700.0, 800.0], "long", False),
        # The threshold itself is exclusive.
        ([600.0, 700.0, 800.0], "long", False),
        ([600.01, 700.0, 800.0], "long", True),
    ],
)
def test_tick_sustained(ticks, direction, expected):
    cfg = load_mes_config({"use_dynamic_tick_threshold": False})
    snapshot = make_snapshot(
        cfg=cfg,
        spy=make_spy_series(internals=[(0.0, t, 0.0) for t in ticks]),
    )
    assert snapshot.tick_sustained(direction) is expected


@pytest.mark.unit
def test_tick_sustained_needs_the_full_window_of_values():
    cfg = load_mes_config({"use_dynamic_tick_threshold": False})
    snapshot = make_snapshot(
        cfg=cfg,
        spy=make_spy_series(internals=[(0.0, None, 0.0), (0.0, 700.0, 0.0), (0.0, 800.0, 0.0)]),
    )
    assert snapshot.tick_sustained("long") is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ticks", "expected"),
    [
        ([100.0, -100.0, 120.0, -80.0, 90.0, -70.0], True),
        ([100.0, 200.0, 300.0, 400.0, 500.0, 550.0], False),
        ([100.0, -100.0, 700.0, -800.0, 900.0, -900.0], False),
        ([100.0, -100.0, -120.0], False),
    ],
)
def test_tick_whipsawing(ticks, expected):
    cfg = load_mes_config({"use_dynamic_tick_threshold": False})
    snapshot = make_snapshot(
        cfg=cfg,
        spy=make_spy_series(internals=[(0.0, t, 0.0) for t in ticks]),
    )
    assert snapshot.tick_whipsawing() is expected


@pytest.mark.unit
def test_tick_effective_threshold_uses_dynamic_series_from_spy_bars():
    cfg = load_mes_config(
        {"use_dynamic_tick_threshold": True, "tick_lookback": 4, "tick_multiplier": 1.5}
    )
    internals = [(0.0, float(v), 0.0) for v in (100, 200, 300, 400)]
    snapshot = make_snapshot(cfg=cfg, spy=make_spy_series(internals=internals))
    assert snapshot.tick_effective_threshold() == pytest.approx(375.0)


@pytest.mark.unit
def test_tick_streak_and_persistent_signal_on_snapshot():
    cfg = load_mes_config({"tick_persistent_bars": 3})
    internals = [(0.0, 50.0, 0.0)] * 4
    snapshot = make_snapshot(cfg=cfg, spy=make_spy_series(internals=internals))
    assert snapshot.tick_streak(positive=True) == 4
    assert snapshot.tick_signal() == "persistent_buy"
    assert snapshot.tick_confirms("long") is True


@pytest.mark.unit
def test_session_bounds_returns_session_open_and_a_five_day_warm_up():
    cfg = load_mes_config()
    session_start, fetch_start = session_bounds(AS_OF, cfg)
    assert session_start == datetime(2026, 3, 30, 9, 30)
    assert fetch_start == session_start - timedelta(days=5)


@pytest.mark.unit
def test_session_bounds_honours_a_custom_rth_start():
    cfg = load_mes_config({"rth_start": "08:15"})
    session_start, _ = session_bounds(AS_OF, cfg)
    assert (session_start.hour, session_start.minute) == (8, 15)


def _bar_frame(count: int = 20, base: float = 100.0) -> pd.DataFrame:
    start = datetime(2026, 3, 30, 9, 30)
    rows = []
    for index in range(count):
        close = base + index * 0.25
        rows.append(
            {
                "Date": start + timedelta(minutes=5 * index),
                "Open": close - 0.25,
                "High": close + 0.5,
                "Low": close - 0.75,
                "Close": close,
                "Volume": 1000 + index,
            }
        )
    return pd.DataFrame(rows)


def _internals_frame(count: int = 20) -> pd.DataFrame:
    start = datetime(2026, 3, 30, 9, 30)
    return pd.DataFrame(
        {
            "Date": [start + timedelta(minutes=5 * i) for i in range(count)],
            "add": [1200.0 + i for i in range(count)],
            "tick": [650.0 + i for i in range(count)],
            "vold": [10_000.0 + 100 * i for i in range(count)],
        }
    )


@pytest.mark.unit
def test_build_snapshot_uses_injected_fetchers():
    cfg = load_mes_config()
    requested = []

    def fetch_bars(symbol, session_start, as_of, *, fetch_start=None):
        requested.append((symbol, session_start, as_of, fetch_start))
        return _bar_frame()

    def fetch_internals(session_start, as_of, interval):
        assert interval == "5m"
        return _internals_frame()

    snapshot = build_snapshot(
        AS_OF, cfg, fetch_bars=fetch_bars, fetch_internals=fetch_internals
    )

    assert isinstance(snapshot, MesSnapshot)
    assert [r[0] for r in requested] == [cfg.mes_symbol, cfg.spy_symbol]
    assert requested[0][1] == datetime(2026, 3, 30, 9, 30)
    assert requested[0][3] == datetime(2026, 3, 30, 9, 30) - timedelta(days=5)
    assert snapshot.warnings == []
    assert snapshot.add == 1200.0 + 19
    assert snapshot.tick == 650.0 + 19
    assert snapshot.vold == 10_000.0 + 100 * 19


@pytest.mark.unit
def test_build_snapshot_records_a_warning_when_internals_fail():
    cfg = load_mes_config()

    def fetch_internals(*args, **kwargs):
        raise RuntimeError("schwab down")

    snapshot = build_snapshot(
        AS_OF,
        cfg,
        fetch_bars=lambda *a, **k: _bar_frame(),
        fetch_internals=fetch_internals,
    )

    assert len(snapshot.warnings) == 1
    assert "schwab down" in snapshot.warnings[0]
    assert snapshot.add is None
    assert snapshot.tick is None
    assert snapshot.vold is None


@pytest.mark.unit
def test_build_snapshot_warns_when_tick_coverage_is_sparse():
    cfg = load_mes_config()
    frame = _internals_frame()
    # 16 of 20 session bars have no readable $TICK (Schwab close=0 candles).
    frame.loc[frame.index[4:], "tick"] = pd.NA

    snapshot = build_snapshot(
        AS_OF,
        cfg,
        fetch_bars=lambda *a, **k: _bar_frame(),
        fetch_internals=lambda *a, **k: frame,
    )

    # Unreadable bars carry the last KNOWN reading forward (653.0) — never a
    # fabricated one-sided candle value — and the sparseness is surfaced.
    assert snapshot.tick == 653.0

    assert any("$TICK data sparse" in w for w in snapshot.warnings)


@pytest.mark.unit
def test_tick_coverage_warning_tolerates_healthy_coverage():
    from tradingagents.mes.snapshot import _tick_coverage_warning

    start = datetime(2026, 3, 30, 9, 30)
    assert _tick_coverage_warning(_internals_frame(), start, AS_OF) is None


@pytest.mark.unit
def test_build_snapshot_raises_when_a_symbol_has_no_bars():
    cfg = load_mes_config()
    empty = pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    with pytest.raises(ValueError, match="no bars"):
        build_snapshot(
            AS_OF,
            cfg,
            fetch_bars=lambda *a, **k: empty,
            fetch_internals=lambda *a, **k: _internals_frame(),
        )


@pytest.mark.unit
def test_merge_internals_onto_bars_forward_fills_same_day_gaps():
    from tradingagents.mes.snapshot import _merge_internals_onto_bars

    bars = pd.DataFrame(
        {
            "Date": pd.to_datetime(
                ["2026-09-01 09:30:00", "2026-09-01 09:35:00", "2026-09-01 09:40:00"]
            ),
            "Open": [1.0, 2.0, 3.0],
        }
    )
    internals = pd.DataFrame(
        {
            "Date": pd.to_datetime(["2026-09-01 09:30:00", "2026-09-01 09:40:00"]),
            "add": [100.0, 200.0],
            "tick": [10.0, 20.0],
            "vold": [1_000.0, 2_000.0],
        }
    )
    merged = _merge_internals_onto_bars(bars, internals)
    assert merged["add"].tolist() == [100.0, 100.0, 200.0]
    assert merged["vold"].tolist() == [1_000.0, 1_000.0, 2_000.0]


@pytest.mark.unit
def test_build_snapshot_exposes_vold_slope_when_internals_span_the_session():
    cfg = load_mes_config()
    snapshot = build_snapshot(
        AS_OF,
        cfg,
        fetch_bars=lambda *a, **k: _bar_frame(),
        fetch_internals=lambda *a, **k: _internals_frame(),
    )
    assert snapshot.vold is not None
    assert snapshot.vold_slope() == pytest.approx(500.0)


@pytest.mark.unit
def test_prev_vold_is_the_value_one_bar_back():
    snapshot = make_snapshot()
    replayed = snapshot_from_csv(MES_CSV, SPY_CSV, AS_OF, load_mes_config())
    assert replayed.spy.prev_vold == replayed.spy.bars[-2].vold


@pytest.mark.unit
def test_series_helpers_expose_last_bar_and_session_change():
    state = snapshot_from_csv(MES_CSV, SPY_CSV, AS_OF, load_mes_config()).mes
    assert isinstance(state.last, Bar)
    assert state.close == state.bars[-1].close
    assert state.session_change() == pytest.approx(
        state.close - state.session_start_price
    )


@pytest.mark.unit
def test_build_snapshot_exposes_internals_provenance_and_flags_synthetic_vold():
    cfg = load_mes_config()
    frame = _internals_frame()
    frame.attrs["provenance"] = {"add": "candles", "tick": "candles", "vold": "synthetic"}
    frame.attrs["backfilled"] = {"vold": "synthetic_quote"}

    def fetch_internals(session_start, as_of, interval):
        return frame

    snapshot = build_snapshot(
        AS_OF,
        cfg,
        fetch_bars=lambda *a, **k: _bar_frame(),
        fetch_internals=fetch_internals,
    )

    assert snapshot.internals_provenance["vold"] == "synthetic"
    assert snapshot.internals_provenance["tick"] == "candles"
    assert snapshot.vold_is_synthetic is True
    assert any("$VOLD is synthetic" in w for w in snapshot.warnings)


@pytest.mark.unit
def test_direct_vold_provenance_adds_no_warning():
    cfg = load_mes_config()

    snapshot = build_snapshot(
        AS_OF,
        cfg,
        fetch_bars=lambda *a, **k: _bar_frame(),
        fetch_internals=lambda *a, **k: _internals_frame(),
    )

    assert snapshot.vold_is_synthetic is False
    assert snapshot.warnings == []


@pytest.mark.unit
def test_build_snapshot_forwards_the_configured_internals_source_interval(monkeypatch):
    cfg = load_mes_config({"internals_source_interval": "1m"})
    calls: list[dict] = []

    def fake_get_internals_frame(session_start, as_of, interval, *, source_interval=None):
        calls.append({"interval": interval, "source_interval": source_interval})
        return _internals_frame()

    import tradingagents.dataflows.schwab as schwab_module

    monkeypatch.setattr(schwab_module, "get_internals_frame", fake_get_internals_frame)
    build_snapshot(AS_OF, cfg, fetch_bars=lambda *a, **k: _bar_frame())
    assert calls and calls[0]["interval"] == "5m"
    assert calls[0]["source_interval"] == "1m"


# 1m-internals rationale: 1m source candles are aggregated onto 5m bars; config default internals_source_interval="1m" (tradingagents/mes/config.py:110) — ~5 raw readings per bar so a single defective candle cannot blank the bar.
@pytest.mark.unit
def test_build_snapshot_defaults_to_1m_source_when_not_configured(monkeypatch):
    cfg = load_mes_config()
    calls: list[dict] = []

    def fake_get_internals_frame(session_start, as_of, interval, *, source_interval=None):
        calls.append({"interval": interval, "source_interval": source_interval})
        return _internals_frame()

    import tradingagents.dataflows.schwab as schwab_module

    monkeypatch.setattr(schwab_module, "get_internals_frame", fake_get_internals_frame)
    build_snapshot(AS_OF, cfg, fetch_bars=lambda *a, **k: _bar_frame())
    assert calls[0]["source_interval"] == "1m"

