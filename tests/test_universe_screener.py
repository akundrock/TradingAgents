from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tradingagents.dataflows.schwab_streamer import ScreenerCandidate
from tradingagents.intraday.universe_screener import UniverseScreener


def _ohlcv(rows: int = 30) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Date": pd.date_range("2026-07-27 09:30", periods=rows, freq="5min"),
            "Open": [100.0] * rows,
            "High": [101.0] * rows,
            "Low": [99.0] * rows,
            "Close": [100.0 + i * 0.1 for i in range(rows)],
            "Volume": [1000 + i * 10 for i in range(rows)],
        }
    )


@pytest.mark.unit
def test_universe_screener_rrs_filter(monkeypatch):
    config = {
        "intraday_screener_keys": ["NASDAQ_VOLUME_0"],
        "intraday_screener_candidate_limit": 10,
        "intraday_screener_max_watchlist": 5,
        "intraday_screener_rrs_timeframes": [5, 30, 60],
        "intraday_screener_min_rrs_aligned": 1,
        "intraday_screener_direction": "long",
        "intraday_session_start": "09:30",
        "intraday_max_concurrent_symbols": 2,
        "pro_trader_benchmark": "SPY",
    }

    mock_screener = MagicMock()
    mock_screener.fetch_top_symbols.return_value = [
        ScreenerCandidate(symbol="NVDA", total_volume=1000000, volume=50000),
        ScreenerCandidate(symbol="AAPL", total_volume=800000, volume=40000),
    ]

    sym_df = _ohlcv()
    bench_df = _ohlcv()

    def fake_mtf(symbol, start, end, timeframes=None):
        return {tf: sym_df.copy() for tf in (timeframes or [5, 30])}

    def fake_indicators(dfs):
        return {tf: df.copy() for tf, df in dfs.items()}

    monkeypatch.setattr(
        "tradingagents.intraday.universe_screener.get_candles_multi_timeframe",
        fake_mtf,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.universe_screener.compute_mtf_indicators",
        fake_indicators,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.universe_screener.compute_rrs_multi_timeframe",
        lambda sym_frames, bench_frames, length=12: {"5m": 1.0, "30m": 0.5, "60m": 0.3},
    )
    monkeypatch.setattr(
        "tradingagents.intraday.universe_screener.compute_relative_volume",
        lambda df, tf: 1.5,
    )

    screener = UniverseScreener(config, screener=mock_screener)
    bar_time = datetime(2026, 7, 27, 10, 30)
    watchlist, screened = screener.refresh_watchlist(bar_time, base_watchlist=["SPY"])

    assert "SPY" in watchlist
    assert len(screened) >= 1
    assert screened[0].aligned_count >= 1
