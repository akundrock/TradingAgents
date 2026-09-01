"""Derived market-internals metrics ported from TOS internals-dashboard panels.

Pure functions over raw $TICK / $VOLD series — no snapshot or I/O coupling.
"""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .config import MesChecklistConfig

Divergence = Literal["bullish", "bearish", "none"]
TickSignal = Literal["bull", "bear", "persistent_buy", "persistent_sell", "neutral"]


def tick_effective_threshold(ticks: list[float], cfg: MesChecklistConfig) -> float:
    """Adaptive $TICK gate: mean(abs(TICK), lookback) * multiplier, or static fallback."""
    if not cfg.use_dynamic_tick_threshold:
        return cfg.tick_threshold
    if not ticks:
        return cfg.tick_threshold
    window = ticks[-cfg.tick_lookback :]
    if not window:
        return cfg.tick_threshold
    return statistics.mean(abs(v) for v in window) * cfg.tick_multiplier


def tick_streak(ticks: list[float], *, positive: bool) -> int:
    """Consecutive bars above (positive) or below (negative) zero ending at the last bar."""
    if not ticks:
        return 0
    streak = 0
    for value in reversed(ticks):
        if positive and value > 0:
            streak += 1
        elif not positive and value < 0:
            streak += 1
        else:
            break
    return streak


def tick_signal(
    tick: float | None,
    ticks: list[float],
    cfg: MesChecklistConfig,
) -> TickSignal:
    """Classify the current $TICK reading (TOS MES_TICK_Panel parity)."""
    if tick is None:
        return "neutral"
    threshold = tick_effective_threshold(ticks, cfg)
    if tick > threshold:
        return "bull"
    if tick < -threshold:
        return "bear"
    pos_streak = tick_streak(ticks, positive=True)
    neg_streak = tick_streak(ticks, positive=False)
    if pos_streak >= cfg.tick_persistent_bars:
        return "persistent_buy"
    if neg_streak >= cfg.tick_persistent_bars:
        return "persistent_sell"
    return "neutral"


def tick_confirms_side(signal: TickSignal, side: str) -> bool:
    """True when $TICK supports the evaluated direction (burst or persistent)."""
    if side == "long":
        return signal in {"bull", "persistent_buy"}
    return signal in {"bear", "persistent_sell"}


def tick_burst(tick: float | None, ticks: list[float], cfg: MesChecklistConfig, side: str) -> bool:
    """True when |$TICK| exceeds burst_multiplier × effective threshold."""
    if tick is None:
        return False
    threshold = tick_effective_threshold(ticks, cfg) * cfg.tick_burst_multiplier
    if side == "long":
        return tick > threshold
    return tick < -threshold


def vold_z_score(volds: list[float], lookback: int) -> float | None:
    """20-bar z-score of raw $VOLD (TOS histogram magnitude)."""
    window = [v for v in volds[-lookback:] if v is not None]
    if len(window) < 2:
        return None
    avg = statistics.mean(window)
    stdev = statistics.stdev(window)
    if stdev == 0:
        return 0.0
    return (window[-1] - avg) / stdev


def vold_bar_divergence(
    vold: float | None,
    close: float | None,
    prev_close: float | None,
    *,
    zero_line: float = 0.0,
) -> Divergence:
    """Bar-over-bar $VOLD sign vs compare-symbol direction (TOS MES_VOLD_Histogram)."""
    if vold is None or close is None or prev_close is None:
        return "none"
    price_up = close > prev_close
    price_down = close < prev_close
    vold_bull = vold > zero_line
    vold_bear = vold < zero_line
    if vold_bear and price_up:
        return "bearish"
    if vold_bull and price_down:
        return "bullish"
    return "none"


def vold_confirms_side(
    vold: float | None,
    close: float | None,
    prev_close: float | None,
    side: str,
    *,
    zero_line: float = 0.0,
) -> bool:
    """True when $VOLD flow confirms the bar's price direction."""
    if vold is None or close is None or prev_close is None:
        return False
    price_up = close > prev_close
    price_down = close < prev_close
    if side == "long":
        return vold > zero_line and price_up
    return vold < zero_line and price_down
