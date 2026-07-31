from __future__ import annotations

from dataclasses import dataclass
from datetime import time

import pandas as pd


@dataclass(frozen=True)
class VolumePressure:
    buying: float
    selling: float
    buy_percent: float
    sell_percent: float
    premarket_volume: float


def compute_bar_volume_pressure(high: float, low: float, close: float, volume: float) -> VolumePressure:
    """Buy/sell volume split from shared_VolumeComparison.tos."""
    if high == low or volume <= 0:
        return VolumePressure(0.0, 0.0, 50.0, 50.0, 0.0)
    buying = volume * (close - low) / (high - low)
    selling = volume * (high - close) / (high - low)
    total = buying + selling
    if total == 0:
        return VolumePressure(0.0, 0.0, 50.0, 50.0, 0.0)
    return VolumePressure(
        buying=buying,
        selling=selling,
        buy_percent=(buying / total) * 100.0,
        sell_percent=(selling / total) * 100.0,
        premarket_volume=0.0,
    )


def compute_premarket_volume(
    df: pd.DataFrame,
    *,
    timezone: str = "America/New_York",
    start: time = time(4, 0),
    end: time = time(9, 29),
) -> float:
    """Accumulate volume from 4:00–9:29 ET (VolumeComparison.tos)."""
    if df is None or df.empty:
        return 0.0

    from zoneinfo import ZoneInfo

    tz = ZoneInfo(timezone)
    if "Date" in df.columns:
        times = pd.to_datetime(df["Date"], errors="coerce")
    elif isinstance(df.index, pd.DatetimeIndex):
        times = pd.Series(df.index)
    else:
        times = pd.to_datetime(df.index, errors="coerce")

    if times.dt.tz is None:
        times = times.dt.tz_localize(tz)
    else:
        times = times.dt.tz_convert(tz)

    vol_col = "Volume" if "Volume" in df.columns else "volume"
    if vol_col not in df.columns:
        return 0.0

    mask = times.dt.time.apply(lambda t: start <= t <= end)
    return float(pd.to_numeric(df.loc[mask, vol_col], errors="coerce").fillna(0.0).sum())


def increasing_price_volume_condition(df: pd.DataFrame, bars: int = 3) -> bool:
    """Three-bar price+volume trend (SMBD, informational)."""
    if df is None or len(df) < bars + 1:
        return False
    close = pd.to_numeric(df["Close"] if "Close" in df.columns else df["close"], errors="coerce")
    volume = pd.to_numeric(df["Volume"] if "Volume" in df.columns else df["volume"], errors="coerce")
    for i in range(bars):
        idx = -1 - i
        prev = idx - 1
        if close.iloc[idx] <= close.iloc[prev] or volume.iloc[idx] <= volume.iloc[prev]:
            return False
    return True


def decreasing_price_volume_condition(df: pd.DataFrame, bars: int = 3) -> bool:
    if df is None or len(df) < bars + 1:
        return False
    close = pd.to_numeric(df["Close"] if "Close" in df.columns else df["close"], errors="coerce")
    volume = pd.to_numeric(df["Volume"] if "Volume" in df.columns else df["volume"], errors="coerce")
    for i in range(bars):
        idx = -1 - i
        prev = idx - 1
        if close.iloc[idx] >= close.iloc[prev] or volume.iloc[idx] <= volume.iloc[prev]:
            return False
    return True
