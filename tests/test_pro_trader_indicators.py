from __future__ import annotations

from datetime import datetime, time

import numpy as np
import pandas as pd
import pytest

from tradingagents.intraday.indicators.opening_range import compute_opening_range
from tradingagents.intraday.indicators.relative_strength import (
    compute_power_index,
    compute_rrs,
    count_aligned_rrs,
    has_sufficient_rrs_bars,
    min_rrs_bars,
    rrs_intraday_fetch_start,
    wilders_average,
    true_range,
)
from tradingagents.intraday.indicators.relative_volume import compute_relative_volume
from tradingagents.intraday.indicators.resample import resample_ohlcv
from tradingagents.intraday.indicators.sector_mapping import get_sector_etf, get_sector_key
from tradingagents.intraday.indicators.supertrend import compute_supertrend
from tradingagents.intraday.indicators.volume_pressure import (
    compute_bar_volume_pressure,
    compute_premarket_volume,
    decreasing_price_volume_condition,
    increasing_price_volume_condition,
)


def _make_intraday_bars(
    prices: list[tuple[float, float, float, float]],
    *,
    start: datetime,
    minutes: int = 5,
    volume: float = 1000.0,
) -> pd.DataFrame:
    rows = []
    for i, (o, h, l, c) in enumerate(prices):
        rows.append(
            {
                "Date": start + pd.Timedelta(minutes=minutes * i),
                "Open": o,
                "High": h,
                "Low": l,
                "Close": c,
                "Volume": volume + i * 10,
            }
        )
    return pd.DataFrame(rows)


@pytest.mark.unit
def test_wilders_average_matches_ewm():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = wilders_average(series, 3)
    expected = series.ewm(alpha=1 / 3, adjust=False).mean()
    pd.testing.assert_series_equal(result, expected)


@pytest.mark.unit
def test_compute_rrs_positive_when_symbol_outperforms():
    sym = pd.DataFrame(
        {
            "High": np.linspace(11, 20, 20),
            "Low": np.linspace(9, 18, 20),
            "Close": np.linspace(10, 19, 20),
        }
    )
    bench = pd.DataFrame(
        {
            "High": np.linspace(101, 110, 20),
            "Low": np.linspace(99, 108, 20),
            "Close": np.linspace(100, 105, 20),
        }
    )
    rrs = compute_rrs(sym, bench, length=12)
    assert rrs > 0


@pytest.mark.unit
def test_opening_range_detects_bullish_breakout():
    start = datetime(2026, 7, 27, 9, 30)
    bars = []
    # 9:30-9:55 OR window bars (6 bars) establish range 100-102
    for i in range(6):
        bars.append((100.0, 102.0, 100.0, 101.0))
    # 10:00 breakout bar
    bars.append((101.5, 103.0, 101.0, 102.5))
    df = _make_intraday_bars(bars, start=start)
    as_of = start + pd.Timedelta(minutes=30)
    state = compute_opening_range(df, as_of, entry_mode="wick_touch")
    assert state is not None
    assert state.opening_range_high == 102.0
    assert state.opening_range_low == 100.0
    assert state.bullish_orb is True


@pytest.mark.unit
def test_opening_range_with_utc_naive_schwab_style_timestamps():
    """Schwab used to expose UTC wall-clock as naive Date values before session_timezone fix."""
    from tradingagents.intraday.indicators.opening_range import compute_opening_range

    start = datetime(2026, 8, 5, 13, 30)  # 9:30 ET as UTC-naive
    bars = []
    for i in range(6):
        bars.append((100.0, 102.0, 100.0, 101.0))
    bars.append((101.5, 103.0, 101.0, 102.5))
    rows = []
    for i, (o, h, l, c) in enumerate(bars):
        rows.append(
            {
                "Date": start + pd.Timedelta(minutes=5 * i),
                "Open": o,
                "High": h,
                "Low": l,
                "Close": c,
                "Volume": 1000.0,
            }
        )
    df = pd.DataFrame(rows)
    # After schwab fix, dates are ET-naive; simulate converted ET bars directly.
    df["Date"] = [
        datetime(2026, 8, 5, 9, 30) + pd.Timedelta(minutes=5 * i) for i in range(len(bars))
    ]
    as_of = datetime(2026, 8, 5, 10, 30)
    state = compute_opening_range(df, as_of, entry_mode="wick_touch")
    assert state is not None
    assert state.opening_range_high == 102.0
    assert state.bullish_orb is True


@pytest.mark.unit
def test_relative_volume_above_one_on_surge():
    volume = [100.0] * 2000
    volume[-1] = 5000.0
    df = pd.DataFrame({"Volume": volume})
    rv = compute_relative_volume(df, "5m")
    assert rv > 1.0


@pytest.mark.unit
def test_supertrend_direction_follows_price():
    close = np.concatenate([np.full(10, 100.0), np.full(10, 110.0)])
    df = pd.DataFrame(
        {
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
        }
    )
    state = compute_supertrend(df)
    assert state.is_long is True


@pytest.mark.unit
def test_volume_pressure_split():
    pressure = compute_bar_volume_pressure(110, 100, 108, 1000)
    assert pressure.buying > pressure.selling
    assert pressure.buy_percent > 50


@pytest.mark.unit
def test_sector_mapping_for_nvda():
    assert get_sector_key("NVDA") == "semis"
    assert get_sector_etf("NVDA") == "SMH"


@pytest.mark.unit
def test_count_aligned_rrs():
    rrs = {"daily": 1.0, "60m": 0.5, "30m": -0.1, "15m": 0.2, "5m": 0.3}
    assert count_aligned_rrs(rrs, "long") == 4
    assert count_aligned_rrs(rrs, "short") == 1


@pytest.mark.unit
def test_power_index_nonzero_on_trend():
    df = pd.DataFrame(
        {
            "High": np.linspace(11, 20, 20),
            "Low": np.linspace(9, 18, 20),
            "Close": np.linspace(10, 19, 20),
        }
    )
    assert compute_power_index(df) > 0


@pytest.mark.unit
def test_increasing_price_volume_condition():
    df = pd.DataFrame(
        {
            "Close": [100, 101, 102, 103],
            "Volume": [1000, 1100, 1200, 1300],
        }
    )
    assert increasing_price_volume_condition(df, bars=3) is True


@pytest.mark.unit
def test_decreasing_price_volume_condition():
    df = pd.DataFrame(
        {
            "Close": [103, 102, 101, 100],
            "Volume": [1000, 1100, 1200, 1300],
        }
    )
    assert decreasing_price_volume_condition(df, bars=3) is True


@pytest.mark.unit
def test_premarket_volume_accumulator_uses_extended_hours():
    start = datetime(2026, 7, 27, 4, 0)
    rows = []
    for i in range(3):
        t = start + pd.Timedelta(minutes=5 * i)
        rows.append({"Date": t, "Volume": 1000})
    for i in range(2):
        t = datetime(2026, 7, 27, 9, 30) + pd.Timedelta(minutes=5 * i)
        rows.append({"Date": t, "Volume": 500})
    df = pd.DataFrame(rows)
    assert compute_premarket_volume(df) == 3000.0
    assert compute_premarket_volume(df.iloc[3:]) == 0.0


@pytest.mark.unit
def test_resample_ohlcv_to_60m_from_30m():
    start = datetime(2026, 7, 27, 9, 30)
    rows = []
    for i in range(4):
        t = start + pd.Timedelta(minutes=30 * i)
        rows.append(
            {
                "Date": t,
                "Open": 100 + i,
                "High": 101 + i,
                "Low": 99 + i,
                "Close": 100.5 + i,
                "Volume": 1000 * (i + 1),
            }
        )
    df_30 = pd.DataFrame(rows)
    df_60 = resample_ohlcv(df_30, 60)
    assert len(df_60) >= 2
    assert df_60.iloc[0]["Open"] == 100
    assert df_60["Volume"].sum() == 10000


@pytest.mark.unit
def test_rrs_intraday_fetch_start_extends_for_hourly():
    session_start = datetime(2026, 7, 27, 9, 30)
    as_of = datetime(2026, 7, 27, 13, 30)
    assert rrs_intraday_fetch_start(as_of, session_start, [5]) == session_start
    lookback = rrs_intraday_fetch_start(as_of, session_start, [5, 60])
    assert lookback < session_start
    assert (session_start - lookback).days >= 7


@pytest.mark.unit
def test_hourly_rrs_zero_with_session_only_bars():
    start = datetime(2026, 7, 27, 9, 30)
    sym = _make_intraday_bars(
        [(100 + i, 101 + i, 99 + i, 100 + i) for i in range(14)],
        start=start,
        minutes=60,
    )
    bench = _make_intraday_bars(
        [(200, 201, 199, 200) for _ in range(14)],
        start=start,
        minutes=60,
    )
    assert len(sym) == 14
    assert has_sufficient_rrs_bars(sym)
    assert compute_rrs(sym, bench) != 0.0


@pytest.mark.unit
def test_hourly_rrs_insufficient_session_bars_returns_zero():
    start = datetime(2026, 7, 27, 9, 30)
    sym = _make_intraday_bars(
        [(100 + i, 101 + i, 99 + i, 100 + i) for i in range(7)],
        start=start,
        minutes=60,
    )
    bench = _make_intraday_bars(
        [(200, 201, 199, 200) for _ in range(7)],
        start=start,
        minutes=60,
    )
    assert len(sym) < min_rrs_bars()
    assert not has_sufficient_rrs_bars(sym)
    assert compute_rrs(sym, bench) == 0.0
