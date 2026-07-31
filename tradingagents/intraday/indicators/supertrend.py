from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from tradingagents.intraday.indicators.relative_strength import true_range, wilders_average

TrendDirection = Literal["long", "short", "init"]


@dataclass(frozen=True)
class SuperTrendState:
    direction: TrendDirection
    trend_line: float
    is_long: bool
    is_short: bool


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    mapping = {}
    for col in df.columns:
        lower = str(col).lower()
        if lower in ("open", "high", "low", "close", "volume"):
            mapping[col] = lower.capitalize() if lower != "volume" else "Volume"
    return df.rename(columns=mapping)


def _modified_true_range(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    atr_period: int,
) -> pd.Series:
    avg_hl = (high - low).rolling(atr_period).mean()
    hi_lo = pd.concat([high - low, 1.5 * avg_hl], axis=1).min(axis=1)
    h_ref = pd.Series(0.0, index=high.index)
    l_ref = pd.Series(0.0, index=high.index)
    for i in range(1, len(high)):
        if low.iloc[i] <= high.iloc[i - 1]:
            h_ref.iloc[i] = high.iloc[i] - close.iloc[i - 1]
        else:
            h_ref.iloc[i] = (high.iloc[i] - close.iloc[i - 1]) - 0.5 * (low.iloc[i] - high.iloc[i - 1])
        if high.iloc[i] >= low.iloc[i - 1]:
            l_ref.iloc[i] = close.iloc[i - 1] - low.iloc[i]
        else:
            l_ref.iloc[i] = (close.iloc[i - 1] - low.iloc[i]) - 0.5 * (low.iloc[i - 1] - high.iloc[i])
    return pd.concat([hi_lo, h_ref, l_ref], axis=1).max(axis=1)


def compute_supertrend(
    df: pd.DataFrame,
    *,
    atr_period: int = 2,
    atr_factor: float = 1.5,
    trend_type: Literal["modified", "unmodified"] = "modified",
    trade_type: Literal["long", "short"] = "long",
) -> SuperTrendState:
    """Modified ATR SuperTrend from shared_SMBD.tos lines 576-638."""
    frame = _normalize_columns(df)
    if frame.empty:
        return SuperTrendState("init", float("nan"), False, False)

    high = frame["High"]
    low = frame["Low"]
    close = frame["Close"]

    if trend_type == "modified":
        tr = _modified_true_range(high, low, close, atr_period)
    else:
        tr = true_range(high, close, low)

    loss = atr_factor * wilders_average(tr, atr_period)

    direction: TrendDirection = "init"
    trend = float("nan")

    for i in range(len(frame)):
        c = float(close.iloc[i])
        l = float(loss.iloc[i]) if not pd.isna(loss.iloc[i]) else float("nan")
        if pd.isna(l):
            direction = "init"
            trend = float("nan")
            continue

        if direction == "init":
            direction = trade_type
            trend = c - l if direction == "long" else c + l
        elif direction == "long":
            if c > trend:
                direction = "long"
                trend = max(trend, c - l)
            else:
                direction = "short"
                trend = c + l
        else:
            if c < trend:
                direction = "short"
                trend = min(trend, c + l)
            else:
                direction = "long"
                trend = c - l

    return SuperTrendState(
        direction=direction,
        trend_line=trend,
        is_long=direction == "long",
        is_short=direction == "short",
    )
