from __future__ import annotations

import pytest

from tradingagents.intraday.gating import GatingLayer, resolve_require_daily_bias_alignment
from tradingagents.intraday.mtf_validator import MTFValidationResult
from tradingagents.intraday.session import DailyBiasReport
from tradingagents.intraday.strategy import StrategyResult


def _mtf(*, trend_30min: str = "up") -> MTFValidationResult:
    return MTFValidationResult(
        symbol="NVDA",
        bar_time=__import__("datetime").datetime(2026, 7, 27, 10, 0),
        snapshot_5min={"Close": 100.0, "close_10_ema": 101.0, "close_20_sma": 99.0, "rsi": 55.0},
        snapshot_30min={"Close": 100.0, "close_10_ema": 101.0, "close_20_sma": 99.0},
        snapshot_daily={},
        trend_5min="up",
        trend_30min=trend_30min,
        daily_bias_direction="bullish",
        trends_aligned=True,
        vwap_5min=99.0,
        atr_5min=1.5,
    )


def _bias(direction: str = "bullish") -> DailyBiasReport:
    return DailyBiasReport(
        symbol="NVDA",
        trade_date="2026-07-27",
        direction=direction,
        key_levels={},
        summary="bullish bias",
        computed_at=__import__("datetime").datetime(2026, 7, 27, 9, 0),
    )


def _strategy_result(
    *,
    passed: bool = True,
    direction: str = "long",
    reason: str = "ok",
) -> StrategyResult:
    return StrategyResult(
        passed=passed,
        direction=direction,
        reason=reason,
        factors_met=["close_above_vwap"] if passed else [],
        factors_missing=[] if passed else ["rsi_in_range"],
    )


@pytest.mark.unit
def test_gate1_blocks_on_failed_strategy():
    gate = GatingLayer()
    result = gate.evaluate(
        _mtf(),
        _strategy_result(passed=False, direction="none", reason="blocked"),
        _bias(),
        {},
    )
    assert result.passed is False
    assert result.gate1_strategy is False
    assert result.gate2_mtf_alignment is False


@pytest.mark.unit
def test_gate2_blocks_on_misaligned_trend():
    gate = GatingLayer()
    result = gate.evaluate(
        _mtf(trend_30min="down"),
        _strategy_result(),
        _bias("bullish"),
        {"intraday_require_daily_bias_alignment": True},
    )
    assert result.passed is False
    assert result.gate1_strategy is True
    assert result.gate2_mtf_alignment is False


@pytest.mark.unit
def test_all_gates_pass():
    gate = GatingLayer()
    result = gate.evaluate(
        _mtf(),
        _strategy_result(),
        _bias(),
        {"intraday_require_daily_bias_alignment": True},
    )
    assert result.passed is True
    assert result.final_direction == "long"


@pytest.mark.unit
def test_gate_result_direction_from_strategy():
    gate = GatingLayer()
    result = gate.evaluate(
        _mtf(trend_30min="down"),
        _strategy_result(direction="short"),
        _bias("bearish"),
        {"intraday_require_daily_bias_alignment": True},
    )
    assert result.final_direction == "short"
    assert result.passed is True


@pytest.mark.unit
def test_gate2_skipped_when_alignment_disabled():
    gate = GatingLayer()
    result = gate.evaluate(
        _mtf(trend_30min="down"),
        _strategy_result(),
        _bias("bullish"),
        {"intraday_require_daily_bias_alignment": False},
    )
    assert result.passed is True
    assert "disabled" in result.gate2_reason


@pytest.mark.unit
def test_resolve_require_daily_bias_alignment_orb_breakout_screener():
    config = {
        "intraday_require_daily_bias_alignment": True,
        "intraday_orb_breakout_screener_disable_gate2": True,
        "intraday_strategy": "orb_breakout",
        "intraday_screener_enabled": True,
    }
    assert resolve_require_daily_bias_alignment(config) is False


@pytest.mark.unit
def test_resolve_require_daily_bias_alignment_orb_breakout_static_watchlist():
    config = {
        "intraday_require_daily_bias_alignment": True,
        "intraday_orb_breakout_screener_disable_gate2": True,
        "intraday_strategy": "orb_breakout",
        "intraday_screener_enabled": False,
    }
    assert resolve_require_daily_bias_alignment(config) is True


@pytest.mark.unit
def test_resolve_require_daily_bias_alignment_orb_breakout_screener_opt_in():
    config = {
        "intraday_require_daily_bias_alignment": True,
        "intraday_orb_breakout_screener_disable_gate2": False,
        "intraday_strategy": "orb_breakout",
        "intraday_screener_enabled": True,
    }
    assert resolve_require_daily_bias_alignment(config) is True


@pytest.mark.unit
def test_gate2_skipped_for_orb_breakout_screener_with_neutral_bias():
    gate = GatingLayer()
    result = gate.evaluate(
        _mtf(trend_30min="down"),
        _strategy_result(direction="short"),
        _bias("neutral"),
        {
            "intraday_require_daily_bias_alignment": True,
            "intraday_orb_breakout_screener_disable_gate2": True,
            "intraday_strategy": "orb_breakout",
            "intraday_screener_enabled": True,
        },
    )
    assert result.passed is True
    assert "disabled" in result.gate2_reason
