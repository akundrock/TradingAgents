from __future__ import annotations

import math
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
from tradingagents.intraday.indicators.volume_pressure import (
    compute_bar_volume_pressure,
    compute_premarket_volume,
    decreasing_price_volume_condition,
    increasing_price_volume_condition,
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
    buy_percent: float = 50.0
    sell_percent: float = 50.0
    premarket_volume: float = 0.0
    increasing_price_volume: bool = False
    decreasing_price_volume: bool = False
    sector_data_available: bool = False


def _resolve_sector_mode(config: dict[str, Any] | None) -> str:
    if not _cfg(config, "pro_trader_require_sector_alignment", True):
        return "off"
    mode = str(_cfg(config, "pro_trader_sector_alignment_mode", "lenient")).lower()
    return mode if mode in ("strict", "lenient", "off") else "lenient"


def _relative_volume_check(value: float, config: dict[str, Any] | None) -> bool | None:
    """Return None to skip the check when relative volume could not be computed."""
    if math.isnan(value):
        on_missing = str(_cfg(config, "pro_trader_relative_volume_on_missing", "skip")).lower()
        return False if on_missing == "fail" else None
    return value > float(_cfg(config, "pro_trader_min_relative_volume", 1.0))


def _cfg(config: dict[str, Any] | None, key: str, default: Any) -> Any:
    if config:
        return config.get(key, default)
    return get_config().get(key, default)


def _last_bar_ohlcv(mtf: MTFValidationResult) -> tuple[float, float, float, float]:
    if mtf.df_5min is not None and not mtf.df_5min.empty:
        row = mtf.df_5min.iloc[-1]
        high = float(row.get("High", row.get("high", 0.0)))
        low = float(row.get("Low", row.get("low", 0.0)))
        close = float(row.get("Close", row.get("close", 0.0)))
        volume = float(row.get("Volume", row.get("volume", 0.0)))
        return high, low, close, volume

    snap = mtf.snapshot_5min
    high = float(snap.get("High", snap.get("high", 0.0)))
    low = float(snap.get("Low", snap.get("low", 0.0)))
    close = float(snap.get("Close", snap.get("close", 0.0)))
    volume = float(snap.get("Volume", snap.get("volume", 0.0)))
    return high, low, close, volume


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
        # Report the side that came closest so gate diagnostics stay actionable.
        closer = min(long_result, short_result, key=lambda r: len(r.factors_missing))
        return StrategyResult(
            passed=False,
            direction="none",
            reason=closer.reason,
            factors_met=list(closer.factors_met),
            factors_missing=list(closer.factors_missing),
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

        bar_high, bar_low, bar_close, bar_volume = _last_bar_ohlcv(mtf)
        bar_pressure = compute_bar_volume_pressure(bar_high, bar_low, bar_close, bar_volume)
        premarket_volume = compute_premarket_volume(mtf.df_5min, timezone=tz)
        increasing_pv = increasing_price_volume_condition(mtf.df_5min)
        decreasing_pv = decreasing_price_volume_condition(mtf.df_5min)

        sector_etf = get_sector_etf(symbol)
        symbol_power = compute_power_index(symbol_frames.get("daily", pd.DataFrame()))
        sector_power = 0.0
        sector_data_available = bool(sector_etf) and not mtf.sector_daily_df.empty
        if sector_data_available:
            sector_power = compute_power_index(mtf.sector_daily_df)

        return ProTraderContext(
            opening_range=opening_range,
            supertrend=supertrend,
            rrs_by_tf=rrs_by_tf,
            relative_volume_5m=relative_volume_5m,
            sector_etf=sector_etf,
            symbol_power=symbol_power,
            sector_power=sector_power,
            buy_percent=bar_pressure.buy_percent,
            sell_percent=bar_pressure.sell_percent,
            premarket_volume=premarket_volume,
            increasing_price_volume=increasing_pv,
            decreasing_price_volume=decreasing_pv,
            sector_data_available=sector_data_available,
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
            if _cfg(config, "pro_trader_require_price_beyond_or", True):
                checks["price_beyond_or"] = close > or_state.opening_range_high
            checks["supertrend_aligned"] = ctx.supertrend.is_long

        if _cfg(config, "pro_trader_require_daily_rrs", False):
            daily_rrs = ctx.rrs_by_tf.get("daily", 0.0)
            checks["daily_rrs_positive"] = daily_rrs > 0

        min_rs = int(_cfg(config, "pro_trader_min_rs_timeframes", 2))
        aligned = count_aligned_rrs(ctx.rrs_by_tf, "long")
        checks["rs_timeframes_aligned"] = aligned >= min_rs

        if _cfg(config, "pro_trader_require_relative_volume", True):
            rvol_ok = _relative_volume_check(ctx.relative_volume_5m, config)
            if rvol_ok is not None:
                checks["relative_volume"] = rvol_ok

        sector_mode = _resolve_sector_mode(config)
        if sector_mode != "off" and ctx.sector_etf and (
            ctx.sector_data_available or sector_mode == "strict"
        ):
            checks["sector_aligned"] = sector_aligned_for_direction(
                ctx.symbol_power, ctx.sector_power, "long", mode=sector_mode
            )

        buffer = float(_cfg(config, "pro_trader_key_level_atr_buffer", 0.5))
        resistance = daily_bias.key_levels.get("resistance")
        if resistance is not None and mtf.atr_5min > 0:
            checks["not_into_daily_resistance"] = close < resistance - buffer * mtf.atr_5min

        if _cfg(config, "pro_trader_require_buy_pressure", False):
            min_buy = float(_cfg(config, "pro_trader_min_buy_percent", 55.0))
            checks["buy_pressure"] = ctx.buy_percent >= min_buy

        if _cfg(config, "pro_trader_require_price_volume_trend", False):
            checks["price_volume_trend"] = ctx.increasing_price_volume

        min_premarket = float(_cfg(config, "pro_trader_min_premarket_volume", 0))
        if min_premarket > 0:
            checks["premarket_volume"] = ctx.premarket_volume >= min_premarket

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
            if _cfg(config, "pro_trader_require_price_beyond_or", True):
                checks["price_beyond_or"] = close < or_state.opening_range_low
            checks["supertrend_aligned"] = ctx.supertrend.is_short

        if _cfg(config, "pro_trader_require_daily_rrs", False):
            daily_rrs = ctx.rrs_by_tf.get("daily", 0.0)
            checks["daily_rrs_negative"] = daily_rrs < 0

        min_rs = int(_cfg(config, "pro_trader_min_rs_timeframes", 2))
        aligned = count_aligned_rrs(ctx.rrs_by_tf, "short")
        checks["rs_timeframes_aligned"] = aligned >= min_rs

        if _cfg(config, "pro_trader_require_relative_volume", True):
            rvol_ok = _relative_volume_check(ctx.relative_volume_5m, config)
            if rvol_ok is not None:
                checks["relative_volume"] = rvol_ok

        sector_mode = _resolve_sector_mode(config)
        if sector_mode != "off" and ctx.sector_etf and (
            ctx.sector_data_available or sector_mode == "strict"
        ):
            checks["sector_aligned"] = sector_aligned_for_direction(
                ctx.symbol_power, ctx.sector_power, "short", mode=sector_mode
            )

        buffer = float(_cfg(config, "pro_trader_key_level_atr_buffer", 0.5))
        support = daily_bias.key_levels.get("support")
        if support is not None and mtf.atr_5min > 0:
            checks["not_into_daily_support"] = close > support + buffer * mtf.atr_5min

        if _cfg(config, "pro_trader_require_sell_pressure", False):
            min_sell = float(_cfg(config, "pro_trader_min_sell_percent", 55.0))
            checks["sell_pressure"] = ctx.sell_percent >= min_sell

        if _cfg(config, "pro_trader_require_price_volume_trend", False):
            checks["price_volume_trend"] = ctx.decreasing_price_volume

        min_premarket = float(_cfg(config, "pro_trader_min_premarket_volume", 0))
        if min_premarket > 0:
            checks["premarket_volume"] = ctx.premarket_volume >= min_premarket

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
        "relative_volume_5m": (
            "n/a" if math.isnan(ctx.relative_volume_5m) else round(ctx.relative_volume_5m, 2)
        ),
        "buy_percent": round(ctx.buy_percent, 1),
        "sell_percent": round(ctx.sell_percent, 1),
        "premarket_volume": round(ctx.premarket_volume, 0),
        "increasing_price_volume": str(ctx.increasing_price_volume),
        "decreasing_price_volume": str(ctx.decreasing_price_volume),
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
