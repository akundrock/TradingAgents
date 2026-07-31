from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Literal

import pandas as pd

EntryMode = Literal["wick_touch", "close_above"]


@dataclass(frozen=True)
class OpeningRangeState:
    opening_range_high: float
    opening_range_low: float
    range_width: float
    half_range: float
    mid_range: float
    bullish_orb: bool
    bearish_orb: bool
    in_opening_range: bool
    in_entry_window: bool
    target_half_up: float
    target_full_up: float
    target_half_down: float
    target_full_down: float


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    rename = {c: c.capitalize() if c.lower() in ("open", "high", "low", "close", "volume") else c for c in out.columns}
    return out.rename(columns={k: v for k, v in rename.items() if k != v})


def _bar_times(df: pd.DataFrame, timezone: str) -> pd.Series:
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
    return times


def _time_in_window(t: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= t < end
    return t >= start or t < end


def compute_opening_range(
    df_5min: pd.DataFrame,
    as_of: datetime,
    *,
    timezone: str = "America/New_York",
    or_start: time = time(9, 30),
    or_end: time = time(10, 0),
    entry_start: time = time(10, 0),
    entry_end: time = time(16, 0),
    entry_mode: EntryMode = "wick_touch",
) -> OpeningRangeState | None:
    """Compute opening range state from 5-minute session bars."""
    if df_5min is None or df_5min.empty:
        return None

    df = _normalize_columns(df_5min)
    times = _bar_times(df, timezone)

    try:
        from zoneinfo import ZoneInfo

        local_as_of = as_of.astimezone(ZoneInfo(timezone)) if as_of.tzinfo else as_of.replace(tzinfo=ZoneInfo(timezone))
    except Exception:
        local_as_of = as_of

    session_date = local_as_of.date()
    mask = times.dt.date == session_date
    session = df.loc[mask].copy()
    session_times = times.loc[mask]
    if session.empty:
        return None

    or_high: float | None = None
    or_low: float | None = None
    bullish_orb = False
    bearish_orb = False

    for idx, bar_time in zip(session.index, session_times):
        bar_t = bar_time.time()
        high = float(session.at[idx, "High"])
        low = float(session.at[idx, "Low"])
        close = float(session.at[idx, "Close"])
        prev_high = float(session.at[session.index[session.index.get_loc(idx) - 1], "High"]) if session.index.get_loc(idx) > 0 else high
        prev_low = float(session.at[session.index[session.index.get_loc(idx) - 1], "Low"]) if session.index.get_loc(idx) > 0 else low
        prev_close = float(session.at[session.index[session.index.get_loc(idx) - 1], "Close"]) if session.index.get_loc(idx) > 0 else close

        in_or = _time_in_window(bar_t, or_start, or_end)
        in_entry = _time_in_window(bar_t, entry_start, entry_end)

        if in_or:
            or_high = high if or_high is None else max(or_high, high)
            or_low = low if or_low is None else min(or_low, low)

        if or_high is None or or_low is None:
            continue

        if in_entry:
            if entry_mode == "wick_touch":
                bull_break = high > or_high and prev_high <= or_high
                bear_break = low < or_low and prev_low >= or_low
            else:
                bull_break = close > or_high and prev_close <= or_high
                bear_break = close < or_low and prev_close >= or_low
            if bull_break:
                bullish_orb = True
            if bear_break:
                bearish_orb = True

    if or_high is None or or_low is None:
        return None

    range_width = or_high - or_low
    half_range = range_width / 2.0
    mid_range = or_low + half_range
    bar_t = local_as_of.time()
    in_or_now = _time_in_window(bar_t, or_start, or_end)
    in_entry_now = _time_in_window(bar_t, entry_start, entry_end)

    return OpeningRangeState(
        opening_range_high=or_high,
        opening_range_low=or_low,
        range_width=range_width,
        half_range=half_range,
        mid_range=mid_range,
        bullish_orb=bullish_orb,
        bearish_orb=bearish_orb,
        in_opening_range=in_or_now,
        in_entry_window=in_entry_now,
        target_half_up=or_high + half_range,
        target_full_up=or_high + range_width,
        target_half_down=or_low - half_range,
        target_full_down=or_low - range_width,
    )
