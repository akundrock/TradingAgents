from __future__ import annotations

from typing import Literal

import pandas as pd

# Bar offsets from ThinkScript shared_SMBD.tos relative volume sections.
RELATIVE_VOLUME_OFFSETS: dict[str, tuple[int, ...]] = {
    "daily": tuple(range(20)),
    "60m": tuple(range(0, 141, 7)),
    "30m": tuple(range(0, 274, 13)),
    "15m": tuple(range(0, 521, 26)),
    "5m": tuple(range(0, 1561, 78)),
    "3m": tuple(range(0, 1951, 130)),
}

RELATIVE_VOLUME_MULTIPLIERS: dict[str, tuple[int, float]] = {
    "daily": (20, 1.5),
    "60m": (20, 1.2),
    "30m": (20, 1.2),
    "15m": (20, 1.2),
    "5m": (20, 1.2),
    "3m": (15, 1.2),
}

RelativeVolumeTF = Literal["daily", "60m", "30m", "15m", "5m", "3m"]


def _volume_series(df: pd.DataFrame) -> pd.Series:
    for col in ("Volume", "volume"):
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return pd.Series([0.0] * len(df))


def compute_relative_volume(
    df: pd.DataFrame,
    timeframe: RelativeVolumeTF = "5m",
) -> float:
    """Relative volume matching ThinkScript SMBD rvolume labels."""
    if df is None or df.empty:
        return 0.0

    offsets = RELATIVE_VOLUME_OFFSETS.get(timeframe)
    n_samples, multiplier = RELATIVE_VOLUME_MULTIPLIERS.get(timeframe, (20, 1.2))
    if offsets is None:
        return 0.0

    volume = _volume_series(df)
    max_offset = max(offsets)
    if len(volume) <= max_offset:
        return 0.0

    cumulative = sum(float(volume.iloc[-1 - offset]) for offset in offsets[:n_samples])
    avg_volume = (cumulative / n_samples) * multiplier
    if avg_volume == 0:
        return 0.0
    return float(volume.iloc[-1]) / avg_volume


def daily_volume_percent_of_average(df_daily: pd.DataFrame, length: int = 20) -> float:
    """Percent of average daily volume (SMBD RPCTV label)."""
    if df_daily is None or len(df_daily) < length:
        return 0.0
    volume = _volume_series(df_daily)
    mav = volume.rolling(length).mean().iloc[-1]
    if mav == 0:
        return 0.0
    return float(volume.iloc[-1] / mav * 100.0)
