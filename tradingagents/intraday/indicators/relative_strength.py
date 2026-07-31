from __future__ import annotations

from typing import Literal

import pandas as pd

RRSTimeframe = Literal["daily", "60m", "30m", "15m", "5m", "3m"]


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    mapping = {}
    for col in out.columns:
        lower = str(col).lower()
        if lower == "open":
            mapping[col] = "Open"
        elif lower == "high":
            mapping[col] = "High"
        elif lower == "low":
            mapping[col] = "Low"
        elif lower == "close":
            mapping[col] = "Close"
        elif lower == "volume":
            mapping[col] = "Volume"
    return out.rename(columns=mapping)


def true_range(high: pd.Series, close: pd.Series, low: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def wilders_average(series: pd.Series, length: int) -> pd.Series:
    """Wilder's smoothing (RMA), matching ThinkScript WildersAverage."""
    return series.ewm(alpha=1.0 / length, adjust=False).mean()


def compute_power_index(df: pd.DataFrame, length: int = 12) -> float:
    """Power index: rolling price move / ATR (sector study formula)."""
    frame = _normalize_columns(df)
    if len(frame) < length + 2:
        return 0.0
    close = frame["Close"]
    high = frame["High"]
    low = frame["Low"]
    rolling_move = float(close.iloc[-1] - close.iloc[-1 - length])
    atr = wilders_average(
        true_range(high.shift(1), close.shift(1), low.shift(1)),
        length,
    )
    atr_val = float(atr.iloc[-1])
    if atr_val == 0:
        return 0.0
    return rolling_move / atr_val


def compute_rrs(
    symbol_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    length: int = 12,
) -> float:
    """Real Relative Strength vs benchmark (ThinkScript SMBD / Comparative RS)."""
    sym = _normalize_columns(symbol_df)
    bench = _normalize_columns(benchmark_df)
    if len(sym) < length + 2 or len(bench) < length + 2:
        return 0.0

    sym_close = sym["Close"]
    bench_close = bench["Close"]
    compared_move = float(bench_close.iloc[-1] - bench_close.iloc[-1 - length])
    symbol_move = float(sym_close.iloc[-1] - sym_close.iloc[-1 - length])

    symbol_atr = wilders_average(
        true_range(sym["High"].shift(1), sym_close.shift(1), sym["Low"].shift(1)),
        length,
    )
    compared_atr = wilders_average(
        true_range(bench["High"].shift(1), bench_close.shift(1), bench["Low"].shift(1)),
        length,
    )

    compared_atr_val = float(compared_atr.iloc[-1])
    symbol_atr_val = float(symbol_atr.iloc[-1])
    if compared_atr_val == 0 or symbol_atr_val == 0:
        return 0.0

    power_index = compared_move / compared_atr_val
    expected_move = power_index * symbol_atr_val
    diff = symbol_move - expected_move
    return diff / symbol_atr_val


def compute_rrs_multi_timeframe(
    symbol_frames: dict[int | str, pd.DataFrame],
    benchmark_frames: dict[int | str, pd.DataFrame],
    *,
    length: int = 12,
) -> dict[str, float]:
    """Compute RRS across multiple timeframes.

    Keys in symbol_frames/benchmark_frames: 5, 15, 30, 60 for intraday, or 'daily'.
    Returns keys: daily, 60m, 30m, 15m, 5m (only for frames provided).
    """
    label_map = {
        "daily": "daily",
        60: "60m",
        30: "30m",
        15: "15m",
        5: "5m",
        3: "3m",
    }
    result: dict[str, float] = {}
    for tf, label in label_map.items():
        sym_df = symbol_frames.get(tf)
        bench_df = benchmark_frames.get(tf)
        if sym_df is None or bench_df is None:
            continue
        if sym_df.empty or bench_df.empty:
            continue
        result[label] = compute_rrs(sym_df, bench_df, length=length)
    return result


def count_aligned_rrs(rrs_by_tf: dict[str, float], direction: Literal["long", "short"]) -> int:
    if direction == "long":
        return sum(1 for v in rrs_by_tf.values() if v > 0)
    return sum(1 for v in rrs_by_tf.values() if v < 0)
