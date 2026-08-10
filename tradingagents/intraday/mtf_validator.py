from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Literal

import pandas as pd

from tradingagents.dataflows.schwab import get_candles_multi_timeframe, get_intraday_5m_candles
from tradingagents.dataflows.stockstats_utils import compute_mtf_indicators, compute_tf_indicators, load_ohlcv
from tradingagents.intraday.indicators.relative_strength import rrs_intraday_fetch_start
from tradingagents.intraday.frame_enrichment import (
    effective_requested_timeframes,
    enriched_frames_from_5m,
    resolve_mtf_fetch_mode,
    synthesize_60m,
)
from tradingagents.intraday.indicators.sector_mapping import get_sector_etf
from tradingagents.intraday.session import TradingSession


@dataclass
class MTFValidationResult:
    symbol: str
    bar_time: datetime
    snapshot_5min: dict[str, float]
    snapshot_30min: dict[str, float]
    snapshot_daily: dict[str, float]
    trend_5min: Literal["up", "down", "flat"]
    trend_30min: Literal["up", "down", "flat"]
    daily_bias_direction: str
    trends_aligned: bool
    vwap_5min: float
    atr_5min: float
    snapshot_15min: dict[str, float] = field(default_factory=dict)
    snapshot_60min: dict[str, float] = field(default_factory=dict)
    df_5min: pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)
    intraday_frames: dict[int, pd.DataFrame] = field(repr=False, default_factory=dict)
    benchmark_intraday_frames: dict[int, pd.DataFrame] = field(repr=False, default_factory=dict)
    symbol_daily_df: pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)
    benchmark_daily_df: pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)
    sector_daily_df: pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)


def _row_snapshot(row: pd.Series) -> dict[str, float]:
    result: dict[str, float] = {}
    for key, value in row.items():
        if pd.isna(value):
            continue
        if isinstance(value, (int, float)):
            result[str(key)] = float(value)
    return result


def _trend_direction(row: pd.Series) -> Literal["up", "down", "flat"]:
    close = float(row.get("Close", row.get("close", 0.0)))
    ema = float(row.get("close_10_ema", 0.0))
    sma = float(row.get("close_20_sma", 0.0))
    if close > ema > sma:
        return "up"
    if close < ema < sma:
        return "down"
    return "flat"


def _directions_aligned(
    trend_5min: str, trend_30min: str, daily_bias: str
) -> bool:
    if daily_bias == "bullish":
        return trend_5min == "up" and trend_30min == "up"
    if daily_bias == "bearish":
        return trend_5min == "down" and trend_30min == "down"
    return False


def _effective_timeframes(config: dict) -> tuple[list[int], bool]:
    """Return Schwab-fetchable TFs and whether to synthesize 60m bars."""
    return effective_requested_timeframes(config)


@lru_cache(maxsize=32)
def _load_sector_daily_cached(sector_etf: str, trade_date: str) -> pd.DataFrame:
    try:
        return load_ohlcv(sector_etf, trade_date).tail(60)
    except Exception:
        return pd.DataFrame()


class MultiTimeframeValidator:
    def evaluate(
        self,
        symbol: str,
        as_of: datetime,
        session: TradingSession,
        config: dict,
    ) -> MTFValidationResult:
        fetch_tfs, need_60m = _effective_timeframes(config)
        session_start = self._session_start(as_of, config)
        trade_date = as_of.strftime("%Y-%m-%d")
        fetch_mode = resolve_mtf_fetch_mode(config)

        if fetch_mode == "5m_resample":
            enriched = self._fetch_symbol_enriched_5m(
                symbol, session_start, as_of, session, fetch_tfs, need_60m
            )
        else:
            intraday_dfs = get_candles_multi_timeframe(
                symbol, session_start, as_of, timeframes=fetch_tfs
            )
            enriched = compute_mtf_indicators(intraday_dfs)
            if need_60m:
                enriched = synthesize_60m(enriched)

        benchmark = str(config.get("pro_trader_benchmark", "SPY"))
        benchmark_intraday_frames: dict[int, pd.DataFrame] = {}
        benchmark_daily_df = pd.DataFrame()
        sector_daily_df = pd.DataFrame()

        use_scan_benchmark = bool(config.get("intraday_benchmark_cache_per_scan", True))
        if (
            use_scan_benchmark
            and session.intraday_scan_bar_time == as_of
            and session.benchmark_intraday_frames
        ):
            benchmark_intraday_frames = session.benchmark_intraday_frames
            benchmark_daily_df = session.benchmark_daily_df
        else:
            try:
                if fetch_mode == "5m_resample":
                    bench_tfs = list(fetch_tfs)
                    if need_60m and 60 not in bench_tfs:
                        bench_tfs = sorted(set(bench_tfs) | {60})
                    fetch_start = rrs_intraday_fetch_start(as_of, session_start, bench_tfs)
                    df_5m = get_intraday_5m_candles(
                        benchmark, session_start, as_of, fetch_start=fetch_start
                    )
                    benchmark_intraday_frames = enriched_frames_from_5m(df_5m, bench_tfs)
                else:
                    bench_dfs = get_candles_multi_timeframe(
                        benchmark, session_start, as_of, timeframes=fetch_tfs
                    )
                    benchmark_intraday_frames = compute_mtf_indicators(bench_dfs)
                    if need_60m:
                        benchmark_intraday_frames = synthesize_60m(benchmark_intraday_frames)
                benchmark_daily_df = load_ohlcv(benchmark, trade_date).tail(60)
            except Exception:
                pass

        daily_raw = load_ohlcv(symbol, trade_date).tail(60)
        daily_enriched = compute_tf_indicators(daily_raw)

        sector_etf = get_sector_etf(symbol)
        if sector_etf:
            sector_daily_df = _load_sector_daily_cached(sector_etf, trade_date)

        tf5 = 5 if 5 in enriched else min(enriched.keys())
        tf15 = 15 if 15 in enriched else tf5
        tf30 = 30 if 30 in enriched else tf5
        tf60 = 60 if 60 in enriched else tf30

        row_5 = enriched[tf5].iloc[-1]
        row_15 = enriched[tf15].iloc[-1] if tf15 in enriched else row_5
        row_30 = enriched[tf30].iloc[-1] if tf30 in enriched else row_5
        row_60 = enriched[tf60].iloc[-1] if tf60 in enriched else row_30
        row_daily = daily_enriched.iloc[-1]

        trend_5min = _trend_direction(row_5)
        trend_30min = _trend_direction(row_30)
        bias_report = session.daily_bias_cache.get(symbol)
        daily_bias_direction = bias_report.direction if bias_report else "neutral"

        return MTFValidationResult(
            symbol=symbol,
            bar_time=as_of,
            snapshot_5min=_row_snapshot(row_5),
            snapshot_15min=_row_snapshot(row_15),
            snapshot_30min=_row_snapshot(row_30),
            snapshot_60min=_row_snapshot(row_60),
            snapshot_daily=_row_snapshot(row_daily),
            trend_5min=trend_5min,
            trend_30min=trend_30min,
            daily_bias_direction=daily_bias_direction,
            trends_aligned=_directions_aligned(trend_5min, trend_30min, daily_bias_direction),
            vwap_5min=float(row_5.get("vwap", 0.0)),
            atr_5min=float(row_5.get("atr", 0.0)),
            df_5min=enriched[tf5],
            intraday_frames=enriched,
            benchmark_intraday_frames=benchmark_intraday_frames,
            symbol_daily_df=daily_raw,
            benchmark_daily_df=benchmark_daily_df,
            sector_daily_df=sector_daily_df,
        )

    def _fetch_symbol_enriched_5m(
        self,
        symbol: str,
        session_start: datetime,
        as_of: datetime,
        session: TradingSession,
        timeframes: list[int],
        need_60m: bool,
    ) -> dict[int, pd.DataFrame]:
        tf_list = list(timeframes)
        if need_60m and 60 not in tf_list:
            tf_list = sorted(set(tf_list) | {60})

        df_5m: pd.DataFrame | None = None
        if (
            session.intraday_scan_bar_time == as_of
            and symbol in session.intraday_5m_cache
        ):
            df_5m = session.intraday_5m_cache[symbol]

        if df_5m is None:
            fetch_start = rrs_intraday_fetch_start(as_of, session_start, tf_list)
            df_5m = get_intraday_5m_candles(
                symbol, session_start, as_of, fetch_start=fetch_start
            )

        return enriched_frames_from_5m(df_5m, tf_list)

    @staticmethod
    def _session_start(as_of: datetime, config: dict) -> datetime:
        local = MultiTimeframeValidator._naive_market_bar_time(as_of, config)
        start_str = str(config.get("intraday_session_start", "09:30"))
        hour, minute = [int(x) for x in start_str.split(":", maxsplit=1)]
        return local.replace(hour=hour, minute=minute, second=0, microsecond=0)

    @staticmethod
    def _naive_market_bar_time(bar_time: datetime, config: dict) -> datetime:
        tz_name = str(config.get("intraday_timezone", "America/New_York"))
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(tz_name)
            if bar_time.tzinfo:
                return bar_time.astimezone(tz).replace(tzinfo=None)
            return bar_time
        except Exception:
            if bar_time.tzinfo:
                return bar_time.replace(tzinfo=None)
            return bar_time
