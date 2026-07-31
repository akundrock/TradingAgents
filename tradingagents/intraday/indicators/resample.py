from __future__ import annotations

import pandas as pd


def _datetime_index(df: pd.DataFrame) -> pd.DatetimeIndex:
    if "Date" in df.columns:
        return pd.DatetimeIndex(pd.to_datetime(df["Date"], errors="coerce"))
    if isinstance(df.index, pd.DatetimeIndex):
        return df.index
    return pd.DatetimeIndex(pd.to_datetime(df.index, errors="coerce"))


def resample_ohlcv(df: pd.DataFrame, target_minutes: int) -> pd.DataFrame:
    """Resample OHLCV bars to a coarser interval (e.g. 30m -> 60m).

    Schwab does not support minute/60; use this to derive hourly bars locally.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    frame = df.copy()
    frame.index = _datetime_index(frame)
    frame = frame.sort_index()
    if frame.index.hasnans:
        frame = frame.loc[~frame.index.isna()]

    rule = f"{target_minutes}min"
    ohlc = {
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }
    present = {col: agg for col, agg in ohlc.items() if col in frame.columns}
    if not present:
        return pd.DataFrame()

    resampled = frame.resample(rule, label="left", closed="left").agg(present)
    resampled = resampled.dropna(subset=["Close"])
    resampled = resampled.reset_index().rename(columns={"index": "Date"})
    if "Date" not in resampled.columns and resampled.columns[0] != "Date":
        resampled = resampled.rename(columns={resampled.columns[0]: "Date"})
    return resampled
