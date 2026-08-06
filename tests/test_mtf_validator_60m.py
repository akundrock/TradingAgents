from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.intraday.frame_enrichment import (
    effective_requested_timeframes,
    synthesize_60m,
)
from tradingagents.intraday.mtf_validator import MultiTimeframeValidator, _effective_timeframes
from tradingagents.intraday.session import DailyBiasReport, TradingSession


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
def test_effective_timeframes_expands_for_pro_trader():
    fetch, need_60 = _effective_timeframes(
        {"intraday_strategy": "pro_trader_dashboard", "intraday_mtf_timeframes": [5, 30]}
    )
    assert fetch == [5, 15, 30]
    assert need_60 is True


@pytest.mark.unit
def test_effective_timeframes_base_momentum_default():
    fetch, need_60 = _effective_timeframes(
        {"intraday_strategy": "base_momentum", "intraday_mtf_timeframes": [5, 30]}
    )
    assert fetch == [5, 30]
    assert need_60 is False


@pytest.mark.unit
def test_synthesize_60m_from_30m(monkeypatch):
    session_start = datetime(2026, 7, 27, 9, 30)
    as_of = datetime(2026, 7, 27, 12, 0)
    df_30 = _build_df(session_start, as_of, 30)

    enriched_30 = {
        5: _build_df(session_start, as_of, 5),
        30: df_30,
    }

    def fake_compute_tf(df):
        return df.copy()

    monkeypatch.setattr(
        "tradingagents.intraday.frame_enrichment.compute_tf_indicators",
        fake_compute_tf,
    )
    result = synthesize_60m(enriched_30)
    assert 60 in result
    assert len(result[60]) >= 2


@pytest.mark.unit
def test_mtf_validator_5m_resample_uses_single_fetch(monkeypatch):
    session_start = datetime(2026, 7, 27, 9, 30)
    as_of = datetime(2026, 7, 27, 12, 0)
    five_m_calls: list[str] = []
    multi_calls: list[str] = []

    def fake_get_5m(symbol, start, end):
        five_m_calls.append(symbol)
        return _build_df(session_start, as_of, 5)

    def fake_get_candles(symbol, start, end, timeframes=None):
        multi_calls.append(symbol)
        return {tf: _build_df(session_start, as_of, tf) for tf in (timeframes or [])}

    def fake_load_ohlcv(symbol, trade_date):
        return _build_df(datetime(2026, 6, 1), datetime(2026, 7, 27), 1440)

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
        "tradingagents.intraday.mtf_validator.get_intraday_5m_candles",
        fake_get_5m,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.mtf_validator.get_candles_multi_timeframe",
        fake_get_candles,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.mtf_validator.load_ohlcv",
        fake_load_ohlcv,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.frame_enrichment.compute_mtf_indicators",
        fake_compute_mtf,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.frame_enrichment.compute_tf_indicators",
        fake_compute_tf,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.mtf_validator.get_sector_etf",
        lambda symbol: None,
    )

    session = TradingSession(
        session_date="2026-07-27",
        watchlist=["NVDA"],
        daily_bias_cache={
            "NVDA": DailyBiasReport(
                symbol="NVDA",
                trade_date="2026-07-27",
                direction="neutral",
                key_levels={},
                summary="",
                computed_at=as_of,
            )
        },
        intraday_scan_bar_time=as_of,
        benchmark_intraday_frames={
            5: _build_df(session_start, as_of, 5),
            30: _build_df(session_start, as_of, 30),
        },
    )
    config = {
        "intraday_strategy": "pro_trader_dashboard",
        "intraday_mtf_timeframes": [5, 30],
        "intraday_mtf_fetch_mode": "5m_resample",
        "intraday_benchmark_cache_per_scan": True,
        "intraday_timezone": "America/New_York",
        "intraday_session_start": "09:30",
    }

    result = MultiTimeframeValidator().evaluate("NVDA", as_of, session, config)

    assert five_m_calls == ["NVDA"]
    assert multi_calls == []
    assert 60 in result.intraday_frames
    assert result.snapshot_60min


@pytest.mark.unit
def test_mtf_validator_synthesizes_60m_without_schwab_60(monkeypatch):
    session_start = datetime(2026, 7, 27, 9, 30)
    as_of = datetime(2026, 7, 27, 12, 0)
    schwab_calls: list[list[int]] = []

    def fake_get_candles(symbol, start, end, timeframes=None):
        schwab_calls.append(list(timeframes or []))
        return {tf: _build_df(session_start, as_of, tf) for tf in (timeframes or [])}

    def fake_load_ohlcv(symbol, trade_date):
        return _build_df(datetime(2026, 6, 1), datetime(2026, 7, 27), 1440)

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
        "tradingagents.intraday.mtf_validator.get_candles_multi_timeframe",
        fake_get_candles,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.mtf_validator.load_ohlcv",
        fake_load_ohlcv,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.frame_enrichment.compute_mtf_indicators",
        fake_compute_mtf,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.frame_enrichment.compute_tf_indicators",
        fake_compute_tf,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.mtf_validator.get_sector_etf",
        lambda symbol: None,
    )

    session = TradingSession(
        session_date="2026-07-27",
        watchlist=["NVDA"],
        daily_bias_cache={
            "NVDA": DailyBiasReport(
                symbol="NVDA",
                trade_date="2026-07-27",
                direction="neutral",
                key_levels={},
                summary="",
                computed_at=as_of,
            )
        },
    )
    config = {
        "intraday_strategy": "pro_trader_dashboard",
        "intraday_mtf_timeframes": [5, 30],
        "intraday_mtf_fetch_mode": "multi",
        "intraday_benchmark_cache_per_scan": False,
        "intraday_timezone": "America/New_York",
        "intraday_session_start": "09:30",
    }

    result = MultiTimeframeValidator().evaluate("NVDA", as_of, session, config)

    assert 60 not in schwab_calls[0]
    assert 60 in result.intraday_frames
    assert result.snapshot_60min


@pytest.mark.unit
def test_mtf_validator_reuses_session_benchmark_cache(monkeypatch):
    session_start = datetime(2026, 7, 27, 9, 30)
    as_of = datetime(2026, 7, 27, 12, 0)
    five_m_calls: list[str] = []

    def fake_get_5m(symbol, start, end):
        five_m_calls.append(symbol)
        return _build_df(session_start, as_of, 5)

    def fake_load_ohlcv(symbol, trade_date):
        return _build_df(datetime(2026, 6, 1), datetime(2026, 7, 27), 1440)

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
        "tradingagents.intraday.mtf_validator.get_intraday_5m_candles",
        fake_get_5m,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.mtf_validator.load_ohlcv",
        fake_load_ohlcv,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.frame_enrichment.compute_mtf_indicators",
        fake_compute_mtf,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.frame_enrichment.compute_tf_indicators",
        fake_compute_tf,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.mtf_validator.get_sector_etf",
        lambda symbol: None,
    )

    bench_frames = {
        5: _build_df(session_start, as_of, 5),
        30: _build_df(session_start, as_of, 30),
    }
    session = TradingSession(
        session_date="2026-07-27",
        watchlist=["NVDA", "AAPL"],
        daily_bias_cache={
            sym: DailyBiasReport(
                symbol=sym,
                trade_date="2026-07-27",
                direction="neutral",
                key_levels={},
                summary="",
                computed_at=as_of,
            )
            for sym in ("NVDA", "AAPL")
        },
        intraday_scan_bar_time=as_of,
        benchmark_intraday_frames=bench_frames,
    )
    config = {
        "intraday_strategy": "pro_trader_dashboard",
        "intraday_mtf_timeframes": [5, 30],
        "intraday_mtf_fetch_mode": "5m_resample",
        "intraday_benchmark_cache_per_scan": True,
        "intraday_timezone": "America/New_York",
        "intraday_session_start": "09:30",
    }
    validator = MultiTimeframeValidator()

    validator.evaluate("NVDA", as_of, session, config)
    validator.evaluate("AAPL", as_of, session, config)

    assert five_m_calls == ["NVDA", "AAPL"]
    assert "SPY" not in five_m_calls


@pytest.mark.unit
def test_mtf_validator_reuses_screener_5m_cache(monkeypatch):
    session_start = datetime(2026, 7, 27, 9, 30)
    as_of = datetime(2026, 7, 27, 12, 0)
    five_m_calls: list[str] = []

    def fake_get_5m(symbol, start, end):
        five_m_calls.append(symbol)
        return _build_df(session_start, as_of, 5)

    def fake_load_ohlcv(symbol, trade_date):
        return _build_df(datetime(2026, 6, 1), datetime(2026, 7, 27), 1440)

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
        "tradingagents.intraday.mtf_validator.get_intraday_5m_candles",
        fake_get_5m,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.mtf_validator.load_ohlcv",
        fake_load_ohlcv,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.frame_enrichment.compute_mtf_indicators",
        fake_compute_mtf,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.frame_enrichment.compute_tf_indicators",
        fake_compute_tf,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.mtf_validator.get_sector_etf",
        lambda symbol: None,
    )

    cached_df = _build_df(session_start, as_of, 5)
    session = TradingSession(
        session_date="2026-07-27",
        watchlist=["NVDA"],
        daily_bias_cache={
            "NVDA": DailyBiasReport(
                symbol="NVDA",
                trade_date="2026-07-27",
                direction="neutral",
                key_levels={},
                summary="",
                computed_at=as_of,
            )
        },
        intraday_scan_bar_time=as_of,
        intraday_5m_cache={"NVDA": cached_df},
        benchmark_intraday_frames={5: cached_df},
    )
    config = {
        "intraday_strategy": "pro_trader_dashboard",
        "intraday_mtf_timeframes": [5, 30],
        "intraday_mtf_fetch_mode": "5m_resample",
        "intraday_benchmark_cache_per_scan": True,
        "intraday_timezone": "America/New_York",
        "intraday_session_start": "09:30",
    }

    MultiTimeframeValidator().evaluate("NVDA", as_of, session, config)

    assert five_m_calls == []


@pytest.mark.unit
def test_get_candles_error_includes_timeframe(monkeypatch):
    from tradingagents.dataflows import schwab

    session_start = datetime(2026, 7, 27, 9, 30)
    as_of = datetime(2026, 7, 27, 10, 0)

    def fake_fetch(symbol, start_dt, end_dt, frequency_type, frequency):
        if frequency == 15:
            raise NoMarketDataError(symbol, symbol, "Schwab HTTP 400")
        return [{"datetime": 1}]

    def fake_candles_to_df(candles, symbol, curr_date):
        return _build_df(session_start, as_of, 5)

    monkeypatch.setattr(schwab, "_fetch_price_history_range", fake_fetch)
    monkeypatch.setattr(schwab, "_candles_to_df", fake_candles_to_df)

    with pytest.raises(NoMarketDataError, match="at 15m"):
        schwab.get_candles_multi_timeframe("AAPL", session_start, as_of, [5, 15])
