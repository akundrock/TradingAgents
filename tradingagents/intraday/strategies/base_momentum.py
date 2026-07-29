from __future__ import annotations

from tradingagents.intraday.mtf_validator import MTFValidationResult
from tradingagents.intraday.session import DailyBiasReport
from tradingagents.intraday.strategy import StrategyResult


class BaseMomentumStrategy:
    name = "base_momentum"

    def describe(self) -> str:
        return (
            "Trend-following momentum: price above VWAP, EMA(10) > SMA(20), "
            "RSI 40-70 for long (short is inverse). Daily bias and 30-min trend "
            "alignment are enforced by Gate 2 when enabled."
        )

    def check_setup(
        self,
        symbol: str,
        mtf: MTFValidationResult,
        daily_bias: DailyBiasReport,
    ) -> StrategyResult:
        long_result = self._evaluate_long(mtf, daily_bias)
        if long_result.passed:
            return long_result
        short_result = self._evaluate_short(mtf, daily_bias)
        if short_result.passed:
            return short_result
        return StrategyResult(
            passed=False,
            direction="none",
            reason=f"{long_result.reason}; {short_result.reason}",
            factors_met=[],
            factors_missing=[],
        )

    def _evaluate_long(
        self, mtf: MTFValidationResult, daily_bias: DailyBiasReport
    ) -> StrategyResult:
        met, missing = self._long_conditions(mtf, daily_bias)
        passed = len(missing) == 0
        return StrategyResult(
            passed=passed,
            direction="long" if passed else "none",
            reason="Long momentum setup confirmed." if passed else f"Long setup blocked: {', '.join(missing)}",
            factors_met=met,
            factors_missing=missing,
        )

    def _evaluate_short(
        self, mtf: MTFValidationResult, daily_bias: DailyBiasReport
    ) -> StrategyResult:
        met, missing = self._short_conditions(mtf, daily_bias)
        passed = len(missing) == 0
        return StrategyResult(
            passed=passed,
            direction="short" if passed else "none",
            reason="Short momentum setup confirmed." if passed else f"Short setup blocked: {', '.join(missing)}",
            factors_met=met,
            factors_missing=missing,
        )

    def _long_conditions(
        self, mtf: MTFValidationResult, daily_bias: DailyBiasReport
    ) -> tuple[list[str], list[str]]:
        snap = mtf.snapshot_5min
        close = snap.get("Close", snap.get("close", 0.0))
        ema = snap.get("close_10_ema", 0.0)
        sma = snap.get("close_20_sma", 0.0)
        rsi = snap.get("rsi", 50.0)

        checks = {
            "close_above_vwap": close > mtf.vwap_5min,
            "ema_above_sma": ema > sma,
            "rsi_in_range": 40 <= rsi <= 70,
        }
        met = [name for name, ok in checks.items() if ok]
        missing = [name for name, ok in checks.items() if not ok]
        return met, missing

    def _short_conditions(
        self, mtf: MTFValidationResult, daily_bias: DailyBiasReport
    ) -> tuple[list[str], list[str]]:
        snap = mtf.snapshot_5min
        close = snap.get("Close", snap.get("close", 0.0))
        ema = snap.get("close_10_ema", 0.0)
        sma = snap.get("close_20_sma", 0.0)
        rsi = snap.get("rsi", 50.0)

        checks = {
            "close_below_vwap": close < mtf.vwap_5min,
            "ema_below_sma": ema < sma,
            "rsi_in_range": 30 <= rsi <= 60,
        }
        met = [name for name, ok in checks.items() if ok]
        missing = [name for name, ok in checks.items() if not ok]
        return met, missing
