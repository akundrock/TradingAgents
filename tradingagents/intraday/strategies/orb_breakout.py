from __future__ import annotations

from typing import Any

from tradingagents.dataflows.config import get_config
from tradingagents.intraday.indicators.opening_range import EntryMode, compute_opening_range
from tradingagents.intraday.mtf_validator import MTFValidationResult
from tradingagents.intraday.session import DailyBiasReport
from tradingagents.intraday.strategy import StrategyResult


def _cfg(config: dict[str, Any] | None, key: str, default: Any) -> Any:
    if config:
        return config.get(key, default)
    return get_config().get(key, default)


class OrbBreakoutStrategy:
    name = "orb_breakout"

    def describe(self) -> str:
        return (
            "Opening Range Breakout: 9:30-10:00 ET range, breakout latch after 10:00, "
            "price beyond OR level. No SuperTrend, RRS, or sector gates."
        )

    def check_setup(
        self,
        symbol: str,
        mtf: MTFValidationResult,
        daily_bias: DailyBiasReport,
        config: dict[str, Any] | None = None,
    ) -> StrategyResult:
        long_result = self._evaluate_long(mtf, config)
        if long_result.passed:
            return long_result
        short_result = self._evaluate_short(mtf, config)
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
        self,
        mtf: MTFValidationResult,
        config: dict[str, Any] | None,
    ) -> StrategyResult:
        met, missing = self._long_conditions(mtf, config)
        passed = len(missing) == 0
        return StrategyResult(
            passed=passed,
            direction="long" if passed else "none",
            reason="Long ORB setup confirmed." if passed else f"Long setup blocked: {', '.join(missing)}",
            factors_met=met,
            factors_missing=missing,
        )

    def _evaluate_short(
        self,
        mtf: MTFValidationResult,
        config: dict[str, Any] | None,
    ) -> StrategyResult:
        met, missing = self._short_conditions(mtf, config)
        passed = len(missing) == 0
        return StrategyResult(
            passed=passed,
            direction="short" if passed else "none",
            reason="Short ORB setup confirmed." if passed else f"Short setup blocked: {', '.join(missing)}",
            factors_met=met,
            factors_missing=missing,
        )

    def _long_conditions(
        self,
        mtf: MTFValidationResult,
        config: dict[str, Any] | None,
    ) -> tuple[list[str], list[str]]:
        close = float(mtf.snapshot_5min.get("Close", mtf.snapshot_5min.get("close", 0.0)))
        or_state = self._opening_range(mtf, config)
        checks: dict[str, bool] = {}

        if or_state is None:
            checks["opening_range_defined"] = False
        else:
            checks["orb_breakout"] = or_state.bullish_orb
            checks["entry_window"] = or_state.in_entry_window
            checks["price_beyond_or"] = close > or_state.opening_range_high

        met = [name for name, ok in checks.items() if ok]
        missing = [name for name, ok in checks.items() if not ok]
        return met, missing

    def _short_conditions(
        self,
        mtf: MTFValidationResult,
        config: dict[str, Any] | None,
    ) -> tuple[list[str], list[str]]:
        close = float(mtf.snapshot_5min.get("Close", mtf.snapshot_5min.get("close", 0.0)))
        or_state = self._opening_range(mtf, config)
        checks: dict[str, bool] = {}

        if or_state is None:
            checks["opening_range_defined"] = False
        else:
            checks["orb_breakout"] = or_state.bearish_orb
            checks["entry_window"] = or_state.in_entry_window
            checks["price_beyond_or"] = close < or_state.opening_range_low

        met = [name for name, ok in checks.items() if ok]
        missing = [name for name, ok in checks.items() if not ok]
        return met, missing

    def _opening_range(self, mtf: MTFValidationResult, config: dict[str, Any] | None):
        tz = str(_cfg(config, "intraday_timezone", "America/New_York"))
        entry_mode: EntryMode = str(_cfg(config, "pro_trader_entry_mode", "wick_touch"))  # type: ignore[assignment]
        return compute_opening_range(
            mtf.df_5min,
            mtf.bar_time,
            timezone=tz,
            entry_mode=entry_mode,
        )
