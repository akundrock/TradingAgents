from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any

import pandas as pd

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.stockstats_utils import load_ohlcv
from tradingagents.intraday.indicators.opening_range import (
    EntryMode,
    OpeningRangeState,
    compute_opening_range,
)
from tradingagents.intraday.indicators.relative_strength import (
    compute_power_index,
    compute_rrs_multi_timeframe,
    count_aligned_rrs,
)
from tradingagents.intraday.indicators.relative_volume import compute_relative_volume
from tradingagents.intraday.indicators.sector_mapping import (
    get_sector_etf,
    sector_aligned_for_direction,
)
from tradingagents.intraday.indicators.supertrend import SuperTrendState, compute_supertrend
from tradingagents.intraday.mtf_validator import MTFValidationResult
from tradingagents.intraday.session import DailyBiasReport
from tradingagents.intraday.strategy import StrategyResult


@dataclass
class ProTraderContext:
    opening_range: OpeningRangeState | None
    supertrend: SuperTrendState
    rrs_by_tf: dict[str, float]
    relative_volume_5m: float
    sector_etf: str | None
    symbol_power: float
    sector_power: float


def _cfg(config: dict[str, Any] | None, key: str, default: Any) -> Any:
    if config:
        return config.get(key, default)
    return get_config().get(key, default)


@lru_cache(maxsize=128)
def _load_benchmark_daily(benchmark: str, trade_date: str) -> pd.DataFrame:
    try:
        return load_ohlcv(benchmark, trade_date).tail(60)
    except Exception:
        return pd.DataFrame()


class ProTraderDashboardStrategy:
    name = "pro_trader_dashboard"

    def describe(self) -> str:
        return (
            "Pro Trader Dashboard: ORB breakout with SuperTrend confirmation, "
            "multi-timeframe RRS vs SPY, relative volume, and sector alignment."
        )

    def check_setup(
        self,
        symbol: str,
        mtf: MTFValidationResult,
        daily_bias: DailyBiasReport,
        config: dict[str, Any] | None = None,
    ) -> StrategyResult:
        ctx = self._build_context(symbol, mtf, daily_bias, config)
        long_result = self._evaluate_long(symbol, mtf, daily_bias, ctx, config)
        if long_result.passed:
            return long_result
        short_result = self._evaluate_short(symbol, mtf, daily_bias, ctx, config)
        if short_result.passed:
            return short_result
        return StrategyResult(
            passed=False,
            direction="none",
            reason=f"{long_result.reason}; {short_result.reason}",
            factors_met=[],
            factors_missing=[],
        )

    def _build_context(
        self,
        symbol: str,
        mtf: MTFValidationResult,
        daily_bias: DailyBiasReport,
        config: dict[str, Any] | None,
    ) -> ProTraderContext:
        tz = str(_cfg(config, "intraday_timezone", "America/New_York"))
        entry_mode: EntryMode = str(_cfg(config, "pro_trader_entry_mode", "wick_touch"))  # type: ignore[assignment]
        benchmark = str(_cfg(config, "pro_trader_benchmark", "SPY"))

        opening_range = compute_opening_range(
            mtf.df_5min,
            mtf.bar_time,
            timezone=tz,
            entry_mode=entry_mode,
        )
        supertrend = compute_supertrend(mtf.df_5min)

        symbol_frames: dict[int | str, pd.DataFrame] = {"daily": mtf.symbol_daily_df}
        benchmark_frames: dict[int | str, pd.DataFrame] = {"daily": mtf.benchmark_daily_df}

        if mtf.intraday_frames:
            for tf, df in mtf.intraday_frames.items():
                symbol_frames[tf] = df
        if mtf.benchmark_intraday_frames:
            for tf, df in mtf.benchmark_intraday_frames.items():
                benchmark_frames[tf] = df

        if benchmark_frames.get("daily") is None or (
            isinstance(benchmark_frames.get("daily"), pd.DataFrame)
            and benchmark_frames["daily"].empty
        ):
            trade_date = daily_bias.trade_date or mtf.bar_time.strftime("%Y-%m-%d")
            benchmark_frames["daily"] = _load_benchmark_daily(benchmark, trade_date)
        if symbol_frames.get("daily") is None or (
            isinstance(symbol_frames.get("daily"), pd.DataFrame) and symbol_frames["daily"].empty
        ):
            trade_date = daily_bias.trade_date or mtf.bar_time.strftime("%Y-%m-%d")
            try:
                symbol_frames["daily"] = load_ohlcv(symbol, trade_date).tail(60)
            except Exception:
                symbol_frames["daily"] = pd.DataFrame()

        rrs_by_tf = compute_rrs_multi_timeframe(symbol_frames, benchmark_frames)
        relative_volume_5m = compute_relative_volume(mtf.df_5min, "5m")

        sector_etf = get_sector_etf(symbol)
        symbol_power = compute_power_index(symbol_frames.get("daily", pd.DataFrame()))
        sector_power = 0.0
        if sector_etf and not mtf.sector_daily_df.empty:
            sector_power = compute_power_index(mtf.sector_daily_df)

        return ProTraderContext(
            opening_range=opening_range,
            supertrend=supertrend,
            rrs_by_tf=rrs_by_tf,
            relative_volume_5m=relative_volume_5m,
            sector_etf=sector_etf,
            symbol_power=symbol_power,
            sector_power=sector_power,
        )

    def _evaluate_long(
        self,
        symbol: str,
        mtf: MTFValidationResult,
        daily_bias: DailyBiasReport,
        ctx: ProTraderContext,
        config: dict[str, Any] | None,
    ) -> StrategyResult:
        met, missing = self._long_conditions(symbol, mtf, daily_bias, ctx, config)
        passed = len(missing) == 0
        return StrategyResult(
            passed=passed,
            direction="long" if passed else "none",
            reason="Long Pro Trader setup confirmed." if passed else f"Long setup blocked: {', '.join(missing)}",
            factors_met=met,
            factors_missing=missing,
        )

    def _evaluate_short(
        self,
        symbol: str,
        mtf: MTFValidationResult,
        daily_bias: DailyBiasReport,
        ctx: ProTraderContext,
        config: dict[str, Any] | None,
    ) -> StrategyResult:
        met, missing = self._short_conditions(symbol, mtf, daily_bias, ctx, config)
        passed = len(missing) == 0
        return StrategyResult(
            passed=passed,
            direction="short" if passed else "none",
            reason="Short Pro Trader setup confirmed." if passed else f"Short setup blocked: {', '.join(missing)}",
            factors_met=met,
            factors_missing=missing,
        )

    def _long_conditions(
        self,
        symbol: str,
        mtf: MTFValidationResult,
        daily_bias: DailyBiasReport,
        ctx: ProTraderContext,
        config: dict[str, Any] | None,
    ) -> tuple[list[str], list[str]]:
        close = float(mtf.snapshot_5min.get("Close", mtf.snapshot_5min.get("close", 0.0)))
        or_state = ctx.opening_range
        checks: dict[str, bool] = {}

        if or_state is None:
            checks["opening_range_defined"] = False
        else:
            checks["orb_breakout"] = or_state.bullish_orb
            checks["entry_window"] = or_state.in_entry_window
            checks["price_beyond_or"] = close > or_state.opening_range_high
            checks["supertrend_aligned"] = ctx.supertrend.is_long

        if _cfg(config, "pro_trader_require_daily_rrs", True):
            daily_rrs = ctx.rrs_by_tf.get("daily", 0.0)
            checks["daily_rrs_positive"] = daily_rrs > 0

        min_rs = int(_cfg(config, "pro_trader_min_rs_timeframes", 4))
        aligned = count_aligned_rrs(ctx.rrs_by_tf, "long")
        checks[f"rs_timeframes_aligned_{aligned}"] = aligned >= min_rs

        if _cfg(config, "pro_trader_require_relative_volume", True):
            checks["relative_volume_above_1"] = ctx.relative_volume_5m > 1.0

        if _cfg(config, "pro_trader_require_sector_alignment", True) and ctx.sector_etf:
            checks["sector_aligned"] = sector_aligned_for_direction(
                ctx.symbol_power, ctx.sector_power, "long"
            )

        buffer = float(_cfg(config, "pro_trader_key_level_atr_buffer", 0.5))
        resistance = daily_bias.key_levels.get("resistance")
        if resistance is not None and mtf.atr_5min > 0:
            checks["not_into_daily_resistance"] = close < resistance - buffer * mtf.atr_5min

        met = [name for name, ok in checks.items() if ok]
        missing = [name for name, ok in checks.items() if not ok]
        return met, missing

    def _short_conditions(
        self,
        symbol: str,
        mtf: MTFValidationResult,
        daily_bias: DailyBiasReport,
        ctx: ProTraderContext,
        config: dict[str, Any] | None,
    ) -> tuple[list[str], list[str]]:
        close = float(mtf.snapshot_5min.get("Close", mtf.snapshot_5min.get("close", 0.0)))
        or_state = ctx.opening_range
        checks: dict[str, bool] = {}

        if or_state is None:
            checks["opening_range_defined"] = False
        else:
            checks["orb_breakout"] = or_state.bearish_orb
            checks["entry_window"] = or_state.in_entry_window
            checks["price_beyond_or"] = close < or_state.opening_range_low
            checks["supertrend_aligned"] = ctx.supertrend.is_short

        if _cfg(config, "pro_trader_require_daily_rrs", True):
            daily_rrs = ctx.rrs_by_tf.get("daily", 0.0)
            checks["daily_rrs_negative"] = daily_rrs < 0

        min_rs = int(_cfg(config, "pro_trader_min_rs_timeframes", 4))
        aligned = count_aligned_rrs(ctx.rrs_by_tf, "short")
        checks[f"rs_timeframes_aligned_{aligned}"] = aligned >= min_rs

        if _cfg(config, "pro_trader_require_relative_volume", True):
            checks["relative_volume_above_1"] = ctx.relative_volume_5m > 1.0

        if _cfg(config, "pro_trader_require_sector_alignment", True) and ctx.sector_etf:
            checks["sector_aligned"] = sector_aligned_for_direction(
                ctx.symbol_power, ctx.sector_power, "short"
            )

        buffer = float(_cfg(config, "pro_trader_key_level_atr_buffer", 0.5))
        support = daily_bias.key_levels.get("support")
        if support is not None and mtf.atr_5min > 0:
            checks["not_into_daily_support"] = close > support + buffer * mtf.atr_5min

        met = [name for name, ok in checks.items() if ok]
        missing = [name for name, ok in checks.items() if not ok]
        return met, missing

    def indicator_snapshot(
        self,
        symbol: str,
        mtf: MTFValidationResult,
        daily_bias: DailyBiasReport,
        config: dict[str, Any] | None = None,
    ) -> dict[str, float | str]:
        ctx = self._build_context(symbol, mtf, daily_bias, config)
        return indicator_snapshot_from_context(ctx)


def indicator_snapshot_from_context(ctx: ProTraderContext) -> dict[str, float | str]:
    snapshot: dict[str, float | str] = {
        "supertrend": "long" if ctx.supertrend.is_long else "short",
        "relative_volume_5m": round(ctx.relative_volume_5m, 2),
        "symbol_power": round(ctx.symbol_power, 2),
        "sector_power": round(ctx.sector_power, 2),
        "rs_aligned_long": count_aligned_rrs(ctx.rrs_by_tf, "long"),
        "rs_aligned_short": count_aligned_rrs(ctx.rrs_by_tf, "short"),
    }
    if ctx.sector_etf:
        snapshot["sector_etf"] = ctx.sector_etf
    if ctx.opening_range is not None:
        or_state = ctx.opening_range
        snapshot["orb_bullish"] = str(or_state.bullish_orb)
        snapshot["orb_bearish"] = str(or_state.bearish_orb)
        if or_state.opening_range_high is not None:
            snapshot["or_high"] = round(or_state.opening_range_high, 2)
        if or_state.opening_range_low is not None:
            snapshot["or_low"] = round(or_state.opening_range_low, 2)
    return snapshot
