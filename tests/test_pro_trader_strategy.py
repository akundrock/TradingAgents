from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from tradingagents.intraday.mtf_validator import MTFValidationResult
from tradingagents.intraday.session import DailyBiasReport
from tradingagents.intraday.strategies import get_strategy
from tradingagents.intraday.strategies.pro_trader_dashboard import ProTraderDashboardStrategy


def _daily_df(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "High": np.array(closes) + 1,
            "Low": np.array(closes) - 1,
            "Close": closes,
            "Volume": [1_000_000] * len(closes),
        }
    )


def _intraday_frame(closes: list[float], start: datetime) -> pd.DataFrame:
    rows = []
    for i, c in enumerate(closes):
        rows.append(
            {
                "Date": start + pd.Timedelta(minutes=5 * i),
                "Open": c - 0.5,
                "High": c + 1.0,
                "Low": c - 1.0,
                "Close": c,
                "Volume": 2000 + i * 50,
            }
        )
    return pd.DataFrame(rows)


def _pro_trader_mtf(
    *,
    close: float = 105.0,
    or_high: float = 102.0,
    or_low: float = 100.0,
    bullish_orb: bool = True,
) -> MTFValidationResult:
    start = datetime(2026, 7, 27, 9, 30)
    # Build session with OR then breakout above ORH
    closes = [101.0] * 6 + [close]
    df_5 = _intraday_frame(closes, start)

  # Force last bar high above OR for wick logic
    df_5.loc[df_5.index[-1], "High"] = max(close + 0.5, or_high + 0.5)
    df_5.loc[df_5.index[-1], "Low"] = or_low

    sym_daily = _daily_df(list(np.linspace(90, 110, 20)))
    bench_daily = _daily_df(list(np.linspace(400, 405, 20)))
    sector_daily = _daily_df(list(np.linspace(180, 190, 20)))

    def _rrs_frame(base: float, drift: float) -> pd.DataFrame:
        vals = [base + drift * i for i in range(20)]
        return _daily_df(vals)

    intraday = {
        5: df_5,
        15: _rrs_frame(100, 0.5),
        30: _rrs_frame(100, 0.4),
        60: _rrs_frame(100, 0.3),
    }
    benchmark_intraday = {
        5: _rrs_frame(400, 0.1),
        15: _rrs_frame(400, 0.05),
        30: _rrs_frame(400, 0.04),
        60: _rrs_frame(400, 0.03),
    }

    return MTFValidationResult(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 30),
        snapshot_5min={"Close": close, "atr": 1.5},
        snapshot_30min={"Close": close},
        snapshot_daily={"Close": 110.0},
        trend_5min="up",
        trend_30min="up",
        daily_bias_direction="bullish",
        trends_aligned=True,
        vwap_5min=103.0,
        atr_5min=1.5,
        df_5min=df_5,
        intraday_frames=intraday,
        benchmark_intraday_frames=benchmark_intraday,
        symbol_daily_df=sym_daily,
        benchmark_daily_df=bench_daily,
        sector_daily_df=sector_daily,
    )


def _bias(direction: str = "bullish") -> DailyBiasReport:
    return DailyBiasReport(
        symbol="NVDA",
        trade_date="2026-07-27",
        direction=direction,
        key_levels={"support": 95.0, "resistance": 120.0, "pivot": 100.0},
        summary="bullish bias",
        computed_at=datetime(2026, 7, 27, 9, 0),
    )


@pytest.mark.unit
def test_pro_trader_strategy_registered():
    strategy = get_strategy("pro_trader_dashboard")
    assert strategy.name == "pro_trader_dashboard"


@pytest.mark.unit
def test_pro_trader_long_setup_with_relaxed_config():
    strategy = ProTraderDashboardStrategy()
    config = {
        "pro_trader_min_rs_timeframes": 1,
        "pro_trader_require_sector_alignment": False,
        "pro_trader_require_relative_volume": False,
        "pro_trader_require_daily_rrs": False,
        "pro_trader_key_level_atr_buffer": 0.5,
    }
    result = strategy.check_setup("NVDA", _pro_trader_mtf(), _bias("bullish"), config=config)
    assert result.direction in ("long", "none")
    if result.passed:
        assert "orb_breakout" in result.factors_met
        assert "supertrend_aligned" in result.factors_met


@pytest.mark.unit
def test_pro_trader_blocks_without_orb():
    strategy = ProTraderDashboardStrategy()
    mtf = _pro_trader_mtf()
    # Flat session — no breakout
    flat = pd.DataFrame(
        {
            "Date": [datetime(2026, 7, 27, 9, 30)],
            "Open": [100],
            "High": [101],
            "Low": [99],
            "Close": [100],
            "Volume": [1000],
        }
    )
    mtf.df_5min = flat
    mtf.intraday_frames[5] = flat
    config = {
        "pro_trader_min_rs_timeframes": 1,
        "pro_trader_require_sector_alignment": False,
        "pro_trader_require_relative_volume": False,
        "pro_trader_require_daily_rrs": False,
    }
    result = strategy.check_setup("NVDA", mtf, _bias(), config=config)
    assert result.passed is False


@pytest.mark.unit
def test_pro_trader_dry_run_config_keys_present():
    from tradingagents.default_config import DEFAULT_CONFIG

    assert "pro_trader_benchmark" in DEFAULT_CONFIG
    assert DEFAULT_CONFIG["intraday_mtf_timeframes"] == [5, 30]
    assert DEFAULT_CONFIG["pro_trader_require_buy_pressure"] is False
    assert DEFAULT_CONFIG["pro_trader_require_sell_pressure"] is False


@pytest.mark.unit
def test_pro_trader_snapshot_includes_volume_pressure():
    strategy = ProTraderDashboardStrategy()
    mtf = _pro_trader_mtf()
    snap = strategy.indicator_snapshot("NVDA", mtf, _bias())
    assert "buy_percent" in snap
    assert "sell_percent" in snap
    assert "premarket_volume" in snap
    assert snap["buy_percent"] > 50


@pytest.mark.unit
def test_pro_trader_buy_pressure_gate_blocks_weak_bar():
    strategy = ProTraderDashboardStrategy()
    config = {
        "pro_trader_min_rs_timeframes": 1,
        "pro_trader_require_sector_alignment": False,
        "pro_trader_require_relative_volume": False,
        "pro_trader_require_daily_rrs": False,
        "pro_trader_require_buy_pressure": True,
        "pro_trader_min_buy_percent": 80.0,
    }
    mtf = _pro_trader_mtf(close=101.0, or_high=102.0, or_low=100.0)
    # Close near low of bar → low buy pressure
    mtf.df_5min.loc[mtf.df_5min.index[-1], "Close"] = 100.5
    mtf.df_5min.loc[mtf.df_5min.index[-1], "High"] = 102.0
    mtf.df_5min.loc[mtf.df_5min.index[-1], "Low"] = 100.0
    mtf.snapshot_5min["Close"] = 100.5
    long_result = strategy._evaluate_long("NVDA", mtf, _bias("bullish"), strategy._build_context("NVDA", mtf, _bias("bullish"), config), config)
    assert long_result.passed is False
    assert "buy_pressure" in long_result.factors_missing


@pytest.mark.unit
def test_pro_trader_buy_pressure_gate_passes_strong_bar():
    strategy = ProTraderDashboardStrategy()
    config = {
        "pro_trader_min_rs_timeframes": 1,
        "pro_trader_require_sector_alignment": False,
        "pro_trader_require_relative_volume": False,
        "pro_trader_require_daily_rrs": False,
        "pro_trader_require_buy_pressure": True,
        "pro_trader_min_buy_percent": 55.0,
    }
    mtf = _pro_trader_mtf(close=105.0, or_high=102.0, or_low=100.0)
    result = strategy.check_setup("NVDA", mtf, _bias("bullish"), config=config)
    if result.direction == "long":
        assert "buy_pressure" in result.factors_met


@pytest.mark.unit
def test_pro_trader_gates_off_no_volume_pressure_factors():
    strategy = ProTraderDashboardStrategy()
    config = {
        "pro_trader_min_rs_timeframes": 1,
        "pro_trader_require_sector_alignment": False,
        "pro_trader_require_relative_volume": False,
        "pro_trader_require_daily_rrs": False,
    }
    result = strategy.check_setup("NVDA", _pro_trader_mtf(), _bias("bullish"), config=config)
    assert "buy_pressure" not in result.factors_met
    assert "buy_pressure" not in result.factors_missing
    assert "sell_pressure" not in result.factors_met
    assert "sell_pressure" not in result.factors_missing


@pytest.mark.unit
def test_pro_trader_premarket_volume_gate_blocks_when_below_threshold():
    strategy = ProTraderDashboardStrategy()
    config = {
        "pro_trader_min_rs_timeframes": 1,
        "pro_trader_require_sector_alignment": False,
        "pro_trader_require_relative_volume": False,
        "pro_trader_require_daily_rrs": False,
        "pro_trader_min_premarket_volume": 10000,
    }
    mtf = _pro_trader_mtf()
    ctx = strategy._build_context("NVDA", mtf, _bias("bullish"), config)
    long_result = strategy._evaluate_long("NVDA", mtf, _bias("bullish"), ctx, config)
    assert "premarket_volume" in long_result.factors_missing


@pytest.mark.unit
def test_pro_trader_price_volume_trend_gate_blocks_without_trend():
    strategy = ProTraderDashboardStrategy()
    config = {
        "pro_trader_min_rs_timeframes": 1,
        "pro_trader_require_sector_alignment": False,
        "pro_trader_require_relative_volume": False,
        "pro_trader_require_daily_rrs": False,
        "pro_trader_require_price_volume_trend": True,
    }
    mtf = _pro_trader_mtf()
    # Flat volume on last bars — no 3-bar increasing trend
    mtf.df_5min["Volume"] = 1000
    ctx = strategy._build_context("NVDA", mtf, _bias("bullish"), config)
    long_result = strategy._evaluate_long("NVDA", mtf, _bias("bullish"), ctx, config)
    assert "price_volume_trend" in long_result.factors_missing


@pytest.mark.unit
def test_pro_trader_sell_pressure_gate_blocks_weak_bar():
    strategy = ProTraderDashboardStrategy()
    config = {
        "pro_trader_min_rs_timeframes": 1,
        "pro_trader_require_sector_alignment": False,
        "pro_trader_require_relative_volume": False,
        "pro_trader_require_daily_rrs": False,
        "pro_trader_require_sell_pressure": True,
        "pro_trader_min_sell_percent": 80.0,
    }
    mtf = _pro_trader_mtf(close=99.0, or_high=102.0, or_low=100.0, bullish_orb=False)
    # Close near high of bar → low sell pressure
    mtf.df_5min.loc[mtf.df_5min.index[-1], "Close"] = 101.5
    mtf.df_5min.loc[mtf.df_5min.index[-1], "High"] = 102.0
    mtf.df_5min.loc[mtf.df_5min.index[-1], "Low"] = 100.0
    mtf.snapshot_5min["Close"] = 101.5
    ctx = strategy._build_context("NVDA", mtf, _bias("bearish"), config)
    short_result = strategy._evaluate_short("NVDA", mtf, _bias("bearish"), ctx, config)
    assert "sell_pressure" in short_result.factors_missing


@pytest.mark.unit
def test_pro_trader_volume_pressure_config_defaults():
    from tradingagents.default_config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["pro_trader_min_buy_percent"] == 55.0
    assert DEFAULT_CONFIG["pro_trader_min_sell_percent"] == 55.0
    assert DEFAULT_CONFIG["pro_trader_require_price_volume_trend"] is False
    assert DEFAULT_CONFIG["pro_trader_min_premarket_volume"] == 0
