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


def _patch_5m_candles(monkeypatch, df: pd.DataFrame | None = None):
    sym_df = df or _ohlcv()

    def fake_5m(symbol, start, end, **kwargs):
        return sym_df.copy()

    for target in (
        "tradingagents.intraday.universe_screener.get_intraday_5m_candles",
        "tradingagents.intraday.screener_filters.rrs_filter.get_intraday_5m_candles",
    ):
        monkeypatch.setattr(target, fake_5m)
    monkeypatch.setattr(
        "tradingagents.intraday.frame_enrichment.compute_mtf_indicators",
        lambda dfs: {tf: frame.copy() for tf, frame in dfs.items()},
    )


@pytest.mark.unit
def test_universe_screener_volume_pre_filter():
    config = {
        "intraday_screener_min_price": 10.0,
        "intraday_screener_require_sp500": True,
    }
    screener = UniverseScreener(config)
    candidates = [
        ScreenerCandidate(symbol="NVDA", last_price=120.0, total_volume=1000000),
        ScreenerCandidate(symbol="SNAP", last_price=15.0, total_volume=900000),
        ScreenerCandidate(symbol="F", last_price=8.0, total_volume=800000),
        ScreenerCandidate(symbol="AAPL", last_price=190.0, total_volume=700000),
    ]
    filtered, summary = screener._filter_volume_candidates(candidates)
    symbols = [c.symbol for c in filtered]
    assert symbols == ["NVDA", "AAPL"]
    assert "not_sp500" in summary
    assert "price=" in summary


@pytest.mark.unit
def test_universe_screener_sp500_quotes_source(monkeypatch):
    config = {
        "intraday_screener_source": "sp500_quotes",
        "intraday_screener_candidate_limit": 5,
        "intraday_screener_max_watchlist": 5,
        "intraday_screener_rrs_timeframes": [5, 30, 60],
        "intraday_screener_min_rrs_aligned": 1,
        "intraday_screener_direction": "long",
        "intraday_screener_min_price": 10.0,
        "intraday_screener_require_sp500": True,
        "intraday_session_start": "09:30",
        "intraday_max_concurrent_symbols": 2,
        "pro_trader_benchmark": "SPY",
    }

    mock_candidates = [
        ScreenerCandidate(symbol="NVDA", last_price=120.0, total_volume=1000000),
        ScreenerCandidate(symbol="AAPL", last_price=190.0, total_volume=800000),
    ]

    monkeypatch.setattr(
        "tradingagents.dataflows.schwab_quotes.fetch_sp500_volume_candidates",
        lambda limit: mock_candidates[:limit],
    )
    _patch_5m_candles(monkeypatch)
    monkeypatch.setattr(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_rrs_multi_timeframe",
        lambda sym_frames, bench_frames, length=12: {"5m": 1.0, "30m": 0.5, "60m": 0.3},
    )
    monkeypatch.setattr(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_relative_volume",
        lambda df, tf: 1.5,
    )

    screener = UniverseScreener(config)
    bar_time = datetime(2026, 7, 27, 10, 30)
    watchlist, screened = screener.refresh_watchlist(bar_time, base_watchlist=["SPY"])

    assert "NVDA" in watchlist
    assert len(screened) >= 1


@pytest.mark.unit
def test_resolve_screener_source_auto():
    from tradingagents.intraday.universe_screener import resolve_screener_source

    assert (
        resolve_screener_source(
            {
                "intraday_screener_source": "auto",
                "intraday_screener_require_sp500": True,
                "intraday_screener_filters": ["rrs"],
            }
        )
        == "sp500_rs_quotes"
    )
    assert (
        resolve_screener_source(
            {
                "intraday_screener_source": "auto",
                "intraday_screener_require_sp500": True,
                "intraday_screener_filters": ["orb"],
            }
        )
        == "sp500_quotes"
    )
    assert resolve_screener_source({"intraday_screener_source": "auto", "intraday_screener_require_sp500": False}) == "streamer"
    assert resolve_screener_source({"intraday_screener_source": "streamer"}) == "streamer"
    assert resolve_screener_source({"intraday_screener_source": "sp500_rrs"}) == "sp500_rrs"
    assert resolve_screener_source({"intraday_screener_source": "sp500_rs_quotes"}) == "sp500_rs_quotes"


@pytest.mark.unit
def test_universe_screener_rrs_filter(monkeypatch):
    config = {
        "intraday_screener_source": "streamer",
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
        ScreenerCandidate(symbol="NVDA", last_price=120.0, total_volume=1000000, volume=50000),
        ScreenerCandidate(symbol="AAPL", last_price=190.0, total_volume=800000, volume=40000),
    ]

    _patch_5m_candles(monkeypatch)
    monkeypatch.setattr(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_rrs_multi_timeframe",
        lambda sym_frames, bench_frames, length=12: {"5m": 1.0, "30m": 0.5, "60m": 0.3},
    )
    monkeypatch.setattr(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_relative_volume",
        lambda df, tf: 1.5,
    )

    screener = UniverseScreener(config, screener=mock_screener)
    bar_time = datetime(2026, 7, 27, 10, 30)
    watchlist, screened = screener.refresh_watchlist(bar_time, base_watchlist=["SPY"])

    assert "SPY" in watchlist
    assert len(screened) >= 1
    assert screened[0].aligned_count >= 1


@pytest.mark.unit
def test_universe_screener_rrs_filter_default_timeframes_include_30m(monkeypatch):
    """Regression: default config must include 30m or rrs_by_tf["30m"] never gets populated."""
    from tradingagents.default_config import DEFAULT_CONFIG

    config = {
        **DEFAULT_CONFIG,
        "intraday_screener_source": "streamer",
        "intraday_screener_keys": ["NASDAQ_VOLUME_0"],
        "intraday_screener_candidate_limit": 10,
        "intraday_screener_max_watchlist": 5,
        "intraday_screener_min_rrs_aligned": 1,
        "intraday_screener_direction": "long",
        "intraday_session_start": "09:30",
        "intraday_max_concurrent_symbols": 2,
        "pro_trader_benchmark": "SPY",
    }

    mock_screener = MagicMock()
    mock_screener.fetch_top_symbols.return_value = [
        ScreenerCandidate(symbol="NVDA", last_price=120.0, total_volume=1000000, volume=50000),
        ScreenerCandidate(symbol="AAPL", last_price=190.0, total_volume=800000, volume=40000),
    ]

    _patch_5m_candles(monkeypatch)
    monkeypatch.setattr(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_rrs_multi_timeframe",
        lambda sym_frames, bench_frames, length=12: {"5m": 1.0, "30m": 0.5, "60m": 0.3},
    )
    monkeypatch.setattr(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_relative_volume",
        lambda df, tf: 1.5,
    )

    screener = UniverseScreener(config, screener=mock_screener)
    bar_time = datetime(2026, 7, 27, 10, 30)
    _watchlist, screened = screener.refresh_watchlist(bar_time, base_watchlist=["SPY"])

    assert len(screened) >= 1
    assert screened[0].rrs_by_tf.get("30m") == 0.5


@pytest.mark.unit
def test_universe_screener_sp500_rrs_ranks_and_merges_screener_first(monkeypatch):
    config = {
        "intraday_screener_source": "sp500_rrs",
        "intraday_screener_candidate_limit": 2,
        "intraday_screener_max_watchlist": 4,
        "intraday_screener_rrs_timeframes": [5, 30, 60],
        "intraday_screener_min_rrs_aligned": 1,
        "intraday_screener_direction": "long",
        "intraday_screener_min_price": 10.0,
        "intraday_session_start": "09:30",
        "intraday_max_concurrent_symbols": 2,
        "pro_trader_benchmark": "SPY",
    }

    mock_candidates = [
        ScreenerCandidate(symbol="NVDA", last_price=120.0, total_volume=100),
        ScreenerCandidate(symbol="AAPL", last_price=190.0, total_volume=200),
        ScreenerCandidate(symbol="MSFT", last_price=400.0, total_volume=300),
    ]

    monkeypatch.setattr(
        "tradingagents.dataflows.schwab_quotes.fetch_sp500_quote_candidates",
        lambda **kwargs: list(mock_candidates),
    )
    sym_df = _ohlcv()
    close_by_symbol = {"MSFT": 103.0, "AAPL": 102.9, "NVDA": 102.0}

    def fake_5m(symbol, start, end, **kwargs):
        df = sym_df.copy()
        if symbol in close_by_symbol:
            df["Close"] = close_by_symbol[symbol]
        return df

    monkeypatch.setattr(
        "tradingagents.intraday.universe_screener.get_intraday_5m_candles",
        fake_5m,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.screener_filters.rrs_filter.get_intraday_5m_candles",
        fake_5m,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.frame_enrichment.compute_mtf_indicators",
        lambda dfs: {tf: frame.copy() for tf, frame in dfs.items()},
    )

    def fake_rrs(sym_frames, bench_frames, length=12):
        symbol_close = float(sym_frames[5]["Close"].iloc[-1])
        score = symbol_close - 100.0
        return {"5m": score, "30m": 0.5, "60m": 0.3}

    monkeypatch.setattr(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_rrs_multi_timeframe",
        fake_rrs,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_relative_volume",
        lambda df, tf: 1.5,
    )

    screener = UniverseScreener(config)
    bar_time = datetime(2026, 7, 27, 10, 30)
    watchlist, screened = screener.refresh_watchlist(bar_time, base_watchlist=["SPY"])

    assert len(screened) == 2
    assert screened[0].symbol == "MSFT"
    assert screened[1].symbol == "AAPL"
    assert watchlist[0] == "MSFT"
    assert "SPY" in watchlist


@pytest.mark.unit
def test_resolve_rank_rrs_timeframe():
    from tradingagents.intraday.universe_screener import resolve_rank_rrs_timeframe

    assert resolve_rank_rrs_timeframe({"intraday_screener_rank_rrs_timeframe": "30m"}) == "30m"
    assert resolve_rank_rrs_timeframe({"intraday_screener_rank_rrs_timeframe": "60"}) == "60m"
    assert resolve_rank_rrs_timeframe({}) == "5m"


@pytest.mark.unit
def test_universe_screener_ranks_by_30m_rrs(monkeypatch):
    config = {
        "intraday_screener_source": "sp500_rrs",
        "intraday_screener_candidate_limit": 2,
        "intraday_screener_max_watchlist": 4,
        "intraday_screener_rrs_timeframes": [5, 30, 60],
        "intraday_screener_rank_rrs_timeframe": "30m",
        "intraday_screener_min_rrs_aligned": 1,
        "intraday_screener_direction": "long",
        "intraday_screener_min_price": 10.0,
        "intraday_session_start": "09:30",
        "intraday_max_concurrent_symbols": 2,
        "pro_trader_benchmark": "SPY",
    }

    mock_candidates = [
        ScreenerCandidate(symbol="NVDA", last_price=120.0, total_volume=100),
        ScreenerCandidate(symbol="AAPL", last_price=190.0, total_volume=200),
        ScreenerCandidate(symbol="MSFT", last_price=400.0, total_volume=300),
    ]

    monkeypatch.setattr(
        "tradingagents.dataflows.schwab_quotes.fetch_sp500_quote_candidates",
        lambda **kwargs: list(mock_candidates),
    )
    _patch_5m_candles(monkeypatch)

    rrs_30m_by_symbol = {"MSFT": 2.5, "AAPL": 1.8, "NVDA": 0.5}

    def fake_rrs(sym_frames, bench_frames, length=12):
        close = float(sym_frames[5]["Close"].iloc[-1])
        sym = "MSFT" if close > 102 else "AAPL" if close > 101 else "NVDA"
        return {
            "5m": close - 100.0,
            "30m": rrs_30m_by_symbol.get(sym, 0.0),
            "60m": 0.3,
        }

    sym_df = _ohlcv()
    close_by_symbol = {"MSFT": 103.0, "AAPL": 102.0, "NVDA": 101.0}

    def fake_5m(symbol, start, end, **kwargs):
        df = sym_df.copy()
        if symbol in close_by_symbol:
            df["Close"] = close_by_symbol[symbol]
        return df

    monkeypatch.setattr(
        "tradingagents.intraday.universe_screener.get_intraday_5m_candles",
        fake_5m,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.screener_filters.rrs_filter.get_intraday_5m_candles",
        fake_5m,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.frame_enrichment.compute_mtf_indicators",
        lambda dfs: {tf: frame.copy() for tf, frame in dfs.items()},
    )
    monkeypatch.setattr(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_rrs_multi_timeframe",
        fake_rrs,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_relative_volume",
        lambda df, tf: 1.5,
    )

    screener = UniverseScreener(config)
    bar_time = datetime(2026, 7, 27, 10, 30)
    watchlist, screened = screener.refresh_watchlist(bar_time, base_watchlist=["SPY"])

    assert len(screened) == 2
    assert screened[0].symbol == "MSFT"
    assert screened[0].rank_rrs_timeframe == "30m"
    assert screened[0].rank_score == 2.5
    assert screened[1].symbol == "AAPL"
    assert watchlist[0] == "MSFT"


@pytest.mark.unit
def test_benchmark_fetched_once_per_refresh(monkeypatch):
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
    spy_calls = 0

    def fake_5m(symbol, start, end, **kwargs):
        nonlocal spy_calls
        if symbol == "SPY":
            spy_calls += 1
        return sym_df.copy()

    monkeypatch.setattr(
        "tradingagents.intraday.universe_screener.get_intraday_5m_candles",
        fake_5m,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.screener_filters.rrs_filter.get_intraday_5m_candles",
        fake_5m,
    )
    monkeypatch.setattr(
        "tradingagents.intraday.frame_enrichment.compute_mtf_indicators",
        lambda dfs: {tf: frame.copy() for tf, frame in dfs.items()},
    )
    monkeypatch.setattr(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_rrs_multi_timeframe",
        lambda sym_frames, bench_frames, length=12: {"5m": 1.0, "30m": 0.5, "60m": 0.3},
    )
    monkeypatch.setattr(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_relative_volume",
        lambda df, tf: 1.5,
    )

    screener = UniverseScreener(config, screener=mock_screener)
    bar_time = datetime(2026, 7, 27, 10, 30)
    screener.refresh_watchlist(bar_time, base_watchlist=["SPY"])

    assert spy_calls == 1


@pytest.mark.unit
def test_universe_screener_sp500_rs_quotes_source(monkeypatch):
    config = {
        "intraday_screener_source": "sp500_rs_quotes",
        "intraday_screener_prefilter_limit": 2,
        "intraday_screener_candidate_limit": 2,
        "intraday_screener_max_watchlist": 5,
        "intraday_screener_rrs_timeframes": [5, 30, 60],
        "intraday_screener_min_rrs_aligned": 1,
        "intraday_screener_direction": "long",
        "intraday_screener_min_price": 10.0,
        "intraday_session_start": "09:30",
        "intraday_max_concurrent_symbols": 2,
        "pro_trader_benchmark": "SPY",
    }

    mock_candidates = [
        ScreenerCandidate(symbol="FAST", last_price=80.0, net_change=2.5, total_volume=500000),
        ScreenerCandidate(symbol="AAPL", last_price=190.0, net_change=1.0, total_volume=800000),
    ]

    monkeypatch.setattr(
        "tradingagents.dataflows.schwab_quotes.fetch_sp500_rs_quote_candidates",
        lambda limit, direction, **kwargs: mock_candidates[:limit],
    )
    _patch_5m_candles(monkeypatch)
    monkeypatch.setattr(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_rrs_multi_timeframe",
        lambda sym_frames, bench_frames, length=12: {"5m": 1.0, "30m": 0.5, "60m": 0.3},
    )
    monkeypatch.setattr(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_relative_volume",
        lambda df, tf: 1.5,
    )

    screener = UniverseScreener(config)
    bar_time = datetime(2026, 7, 27, 10, 30)
    watchlist, screened = screener.refresh_watchlist(bar_time, base_watchlist=[])

    assert "FAST" in watchlist
    assert len(screened) >= 1


@pytest.mark.unit
def test_zero_price_quote_is_rejected():
    """A quote with last_price == 0 (missing quote) must be rejected, not
    silently passed through the min-price gate."""
    from tradingagents.dataflows.schwab_streamer import ScreenerCandidate
    from tradingagents.intraday.universe_screener import UniverseScreener

    config = {"intraday_screener_min_price": 10.0, "intraday_screener_require_sp500": False}
    screener = UniverseScreener(config)
    candidates = [
        ScreenerCandidate(symbol="GOOD", last_price=50.0, total_volume=1000),
        ScreenerCandidate(symbol="ZERO", last_price=0.0, total_volume=1000),
        ScreenerCandidate(symbol="CHEAP", last_price=5.0, total_volume=1000),
    ]
    filtered, summary = screener._filter_volume_candidates(candidates)

    assert [c.symbol for c in filtered] == ["GOOD"]
    assert "price_missing=1" in summary


@pytest.mark.unit
def test_screener_tf_set_is_superset_of_strategy_effective_timeframes():
    """Screener RRS alignment must cover every TF the strategy checks, else a
    screener pass would not imply strategy rs_timeframes_aligned on the same
    data."""
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.intraday.frame_enrichment import effective_requested_timeframes

    fetch_tfs, need_60m = effective_requested_timeframes(DEFAULT_CONFIG)
    strategy_tfs = set(fetch_tfs) | ({60} if need_60m else set())
    screener_tfs = set(DEFAULT_CONFIG["intraday_screener_rrs_timeframes"])
    missing = strategy_tfs - screener_tfs
    assert not missing, (
        f"strategy needs TFs {sorted(missing)} that the screener doesn't score; "
        "a screener pass would not guarantee strategy rs_timeframes_aligned"
    )
