from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import pandas as pd

from tradingagents.dataflows.schwab import get_candles_multi_timeframe
from tradingagents.dataflows.stockstats_utils import compute_mtf_indicators, compute_tf_indicators, load_ohlcv
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
    df_5min: pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)


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


class MultiTimeframeValidator:
    def evaluate(
        self,
        symbol: str,
        as_of: datetime,
        session: TradingSession,
        config: dict,
    ) -> MTFValidationResult:
        timeframes = list(config.get("intraday_mtf_timeframes", [5, 30]))
        session_start = self._session_start(as_of, config)
        trade_date = as_of.strftime("%Y-%m-%d")

        intraday_dfs = get_candles_multi_timeframe(
            symbol, session_start, as_of, timeframes=timeframes
        )
        enriched = compute_mtf_indicators(intraday_dfs)

        daily_raw = load_ohlcv(symbol, trade_date).tail(60)
        daily_enriched = compute_tf_indicators(daily_raw)

        tf5 = int(timeframes[0]) if timeframes else 5
        tf30 = int(timeframes[1]) if len(timeframes) > 1 else 30
        row_5 = enriched[tf5].iloc[-1]
        row_30 = enriched[tf30].iloc[-1]
        row_daily = daily_enriched.iloc[-1]

        trend_5min = _trend_direction(row_5)
        trend_30min = _trend_direction(row_30)
        bias_report = session.daily_bias_cache.get(symbol)
        daily_bias_direction = bias_report.direction if bias_report else "neutral"

        return MTFValidationResult(
            symbol=symbol,
            bar_time=as_of,
            snapshot_5min=_row_snapshot(row_5),
            snapshot_30min=_row_snapshot(row_30),
            snapshot_daily=_row_snapshot(row_daily),
            trend_5min=trend_5min,
            trend_30min=trend_30min,
            daily_bias_direction=daily_bias_direction,
            trends_aligned=_directions_aligned(trend_5min, trend_30min, daily_bias_direction),
            vwap_5min=float(row_5.get("vwap", 0.0)),
            atr_5min=float(row_5.get("atr", 0.0)),
            df_5min=enriched[tf5],
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
