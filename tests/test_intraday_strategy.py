from __future__ import annotations

import pytest

from tradingagents.intraday.mtf_validator import MTFValidationResult
from tradingagents.intraday.session import DailyBiasReport
from tradingagents.intraday.strategies.base_momentum import BaseMomentumStrategy


def _mtf(
    *,
    close: float = 100.0,
    ema: float = 101.0,
    sma: float = 99.0,
    rsi: float = 55.0,
    vwap: float = 99.0,
    trend_30min: str = "up",
) -> MTFValidationResult:
    return MTFValidationResult(
        symbol="NVDA",
        bar_time=__import__("datetime").datetime(2026, 7, 27, 10, 0),
        snapshot_5min={
            "Close": close,
            "close_10_ema": ema,
            "close_20_sma": sma,
            "rsi": rsi,
        },
        snapshot_30min={"Close": close, "close_10_ema": ema, "close_20_sma": sma},
        snapshot_daily={},
        trend_5min="up",
        trend_30min=trend_30min,
        daily_bias_direction="bullish",
        trends_aligned=True,
        vwap_5min=vwap,
        atr_5min=1.5,
    )


def _bias(direction: str = "bullish") -> DailyBiasReport:
    return DailyBiasReport(
        symbol="NVDA",
        trade_date="2026-07-27",
        direction=direction,
        key_levels={},
        summary="bias",
        computed_at=__import__("datetime").datetime(2026, 7, 27, 9, 0),
    )


@pytest.mark.unit
def test_base_momentum_long_setup():
    strategy = BaseMomentumStrategy()
    result = strategy.check_setup("NVDA", _mtf(), _bias("bullish"))
    assert result.passed is True
    assert result.direction == "long"


@pytest.mark.unit
def test_base_momentum_blocks_on_rsi_overbought():
    strategy = BaseMomentumStrategy()
    result = strategy.check_setup("NVDA", _mtf(rsi=75.0), _bias("bullish"))
    assert result.passed is False
    assert "rsi_in_range" in result.reason


@pytest.mark.unit
def test_base_momentum_long_passes_without_bullish_bias_at_strategy_level():
    """Daily bias alignment is Gate 2; strategy only checks entry technicals."""
    strategy = BaseMomentumStrategy()
    result = strategy.check_setup("NVDA", _mtf(), _bias("bearish"))
    assert result.passed is True
    assert result.direction == "long"


@pytest.mark.unit
def test_base_momentum_reports_both_sides_when_neither_passes():
    strategy = BaseMomentumStrategy()
    result = strategy.check_setup("NVDA", _mtf(rsi=75.0), _bias("bullish"))
    assert result.passed is False
    assert "Long setup blocked" in result.reason
    assert "Short setup blocked" in result.reason


@pytest.mark.unit
def test_base_momentum_short_setup():
    strategy = BaseMomentumStrategy()
    result = strategy.check_setup(
        "NVDA",
        _mtf(close=98.0, ema=97.0, sma=99.0, rsi=45.0, vwap=99.0, trend_30min="down"),
        _bias("bearish"),
    )
    assert result.passed is True
    assert result.direction == "short"
