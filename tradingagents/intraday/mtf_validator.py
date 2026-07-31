from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import pandas as pd

from tradingagents.dataflows.schwab import SCHWAB_INTRADAY_MINUTES, get_candles_multi_timeframe
from tradingagents.dataflows.stockstats_utils import compute_mtf_indicators, compute_tf_indicators, load_ohlcv
from tradingagents.intraday.indicators.resample import resample_ohlcv
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
    requested = list(config.get("intraday_mtf_timeframes", [5, 30]))
    strategy = str(config.get("intraday_strategy", "base_momentum"))
    if strategy == "pro_trader_dashboard":
        for tf in (5, 15, 30, 60):
            if tf not in requested:
                requested.append(tf)

    need_60m = 60 in requested
    fetch_tfs = sorted({tf for tf in requested if tf in SCHWAB_INTRADAY_MINUTES})
    if not fetch_tfs:
        fetch_tfs = [5, 30]
    return fetch_tfs, need_60m


def _synthesize_60m(enriched: dict[int, pd.DataFrame]) -> dict[int, pd.DataFrame]:
    """Derive 60m indicators from 30m (preferred) or 5m bars."""
    if 60 in enriched and not enriched[60].empty:
        return enriched
    source_tf = 30 if 30 in enriched and not enriched[30].empty else 5
    if source_tf not in enriched or enriched[source_tf].empty:
        return enriched
    raw_60 = resample_ohlcv(enriched[source_tf], 60)
    if raw_60.empty:
        return enriched
    enriched = dict(enriched)
    enriched[60] = compute_tf_indicators(raw_60)
    return enriched


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

        intraday_dfs = get_candles_multi_timeframe(
            symbol, session_start, as_of, timeframes=fetch_tfs
        )
        enriched = compute_mtf_indicators(intraday_dfs)
        if need_60m:
            enriched = _synthesize_60m(enriched)

        benchmark = str(config.get("pro_trader_benchmark", "SPY"))
        benchmark_intraday_frames: dict[int, pd.DataFrame] = {}
        benchmark_daily_df = pd.DataFrame()
        sector_daily_df = pd.DataFrame()
        try:
            bench_dfs = get_candles_multi_timeframe(
                benchmark, session_start, as_of, timeframes=fetch_tfs
            )
            benchmark_intraday_frames = compute_mtf_indicators(bench_dfs)
            if need_60m:
                benchmark_intraday_frames = _synthesize_60m(benchmark_intraday_frames)
            benchmark_daily_df = load_ohlcv(benchmark, trade_date).tail(60)
        except Exception:
            pass

        daily_raw = load_ohlcv(symbol, trade_date).tail(60)
        daily_enriched = compute_tf_indicators(daily_raw)

        sector_etf = get_sector_etf(symbol)
        if sector_etf:
            try:
                sector_daily_df = load_ohlcv(sector_etf, trade_date).tail(60)
            except Exception:
                sector_daily_df = pd.DataFrame()

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

    @staticmethod
    def _session_start(as_of: datetime, config: dict) -> datetime:
        tz_name = str(config.get("intraday_timezone", "America/New_York"))
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(tz_name)
            local = as_of.astimezone(tz) if as_of.tzinfo else as_of.replace(tzinfo=tz)
        except Exception:
            local = as_of

        start_str = str(config.get("intraday_session_start", "09:30"))
        hour, minute = [int(x) for x in start_str.split(":", maxsplit=1)]
        session_start = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if session_start.tzinfo:
            return session_start.replace(tzinfo=None)
        return session_start
