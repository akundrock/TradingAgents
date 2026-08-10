from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tradingagents.intraday.scanner import WatchlistScanner
from tradingagents.intraday.session import TradingSession


def _build_df(session_start: datetime, as_of: datetime, step_minutes: int) -> pd.DataFrame:
    dates = pd.date_range(session_start, as_of, freq=f"{step_minutes}min")
    close = pd.Series([100 + i * 0.1 for i in range(len(dates))])
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": close - 0.1,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": [1000] * len(dates),
        }
    )


@pytest.mark.unit
def test_scanner_prepares_benchmark_once_per_scan(monkeypatch):
    session_start = datetime(2026, 7, 27, 9, 30)
    as_of = datetime(2026, 7, 27, 12, 0)
    five_m_calls: list[str] = []

    def fake_get_5m(symbol, start, end, **kwargs):
        five_m_calls.append(symbol)
        return _build_df(session_start, as_of, 5)

    monkeypatch.setattr(
        "tradingagents.dataflows.schwab.get_intraday_5m_candles",
        fake_get_5m,
    )

    def fake_compute_mtf(candle_dfs):
        return {tf: df.copy() for tf, df in candle_dfs.items()}

    def fake_compute_tf(df):
        row = df.iloc[-1]
        return df.assign(
            close_10_ema=row["Close"],
            close_20_sma=row["Close"] - 0.5,
            atr=1.0,
            vwap=row["Close"],
        )

    monkeypatch.setattr(
        "tradingagents.intraday.frame_enrichment.compute_mtf_indicators",
        fake_compute_mtf,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.frame_enrichment.compute_tf_indicators",
        fake_compute_tf,
    )

    config = {
        "watchlist": ["NVDA", "AAPL"],
        "intraday_strategy": "pro_trader_dashboard",
        "intraday_mtf_timeframes": [5, 30],
        "intraday_mtf_fetch_mode": "5m_resample",
        "intraday_benchmark_cache_per_scan": True,
        "intraday_timezone": "America/New_York",
        "intraday_session_start": "09:30",
        "intraday_session_end": "16:00",
        "intraday_bar_close_delay_seconds": 0,
        "intraday_max_concurrent_symbols": 2,
        "intraday_output_dir": "/tmp/tradingagents-scan-cache-test",
        "pro_trader_benchmark": "SPY",
    }

    scanner = WatchlistScanner(config, MagicMock(), dry_run=True, skip_premarket=True)
    scanner.session = TradingSession(
        session_date="2026-07-27",
        watchlist=["NVDA", "AAPL"],
        base_watchlist=["NVDA", "AAPL"],
    )

    scanner._prepare_scan_cache(as_of)

    assert five_m_calls == ["SPY"]
    assert scanner.session.intraday_scan_bar_time == as_of
    assert 5 in scanner.session.benchmark_intraday_frames
