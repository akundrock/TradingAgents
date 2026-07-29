from __future__ import annotations

import pandas as pd
import pytest

from tradingagents.dataflows.stockstats_utils import (
    _INTRADAY_INDICATORS,
    compute_intraday_vwap,
    compute_mtf_indicators,
    compute_tf_indicators,
)


def _synthetic_ohlcv(rows: int = 30) -> pd.DataFrame:
    dates = pd.date_range("2026-07-27 09:30", periods=rows, freq="5min")
    close = pd.Series([100 + i * 0.5 for i in range(rows)])
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": close - 0.2,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": [1000 + i * 10 for i in range(rows)],
        }
    )


@pytest.mark.unit
def test_all_indicators_computed():
    df = _synthetic_ohlcv()
    enriched = compute_tf_indicators(df)

    for col in _INTRADAY_INDICATORS:
        assert col in enriched.columns, f"missing indicator column: {col}"
    assert "vwap" in enriched.columns

    last_row = enriched.iloc[-1]
    for col in _INTRADAY_INDICATORS:
        assert pd.notna(last_row[col]), f"{col} is NaN on last row"


@pytest.mark.unit
def test_vwap_correct_formula():
    df = pd.DataFrame(
        {
            "Date": pd.date_range("2026-07-27 09:30", periods=3, freq="5min"),
            "Open": [10.0, 11.0, 12.0],
            "High": [12.0, 13.0, 14.0],
            "Low": [9.0, 10.0, 11.0],
            "Close": [11.0, 12.0, 13.0],
            "Volume": [100.0, 200.0, 300.0],
        }
    )
    vwap = compute_intraday_vwap(df)

    typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
    expected = (typical * df["Volume"]).cumsum() / df["Volume"].cumsum()

    pd.testing.assert_series_equal(vwap.reset_index(drop=True), expected.reset_index(drop=True))


@pytest.mark.unit
def test_handles_single_row():
    df = _synthetic_ohlcv(rows=1)
    enriched = compute_tf_indicators(df)

    assert len(enriched) == 1
    assert "vwap" in enriched.columns
    assert pd.notna(enriched["vwap"].iloc[0])


@pytest.mark.unit
def test_compute_mtf_indicators_returns_same_keys():
    dfs = {5: _synthetic_ohlcv(30), 30: _synthetic_ohlcv(10)}
    result = compute_mtf_indicators(dfs)

    assert set(result.keys()) == {5, 30}
    for tf, enriched in result.items():
        assert "vwap" in enriched.columns
        assert "close_10_ema" in enriched.columns
        assert "Close" in enriched.columns
        assert len(enriched) == len(dfs[tf])


@pytest.mark.unit
def test_compute_tf_indicators_plain_frame_for_magpie():
    import warnings

    from tradingagents.agents.trader.magpie import build_magpie_factor_inputs_from_intraday

    enriched = compute_tf_indicators(_synthetic_ohlcv())
    assert type(enriched).__name__ == "DataFrame"

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        factors = build_magpie_factor_inputs_from_intraday(enriched)

    assert factors is not None
