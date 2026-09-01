"""Streaming indicators ported from ``mes-tuner/mes_tuner/engine/indicators.py``.

Kept as a port rather than an import: the tuner lives in a separate repo and is
tuned/versioned independently. Any change here must keep TOS parity.
"""

from __future__ import annotations

import math
from collections import deque
from datetime import datetime


class SMA:
    def __init__(self, length: int) -> None:
        self.length = max(1, length)
        self._queue: deque[float] = deque()
        self._sum = 0.0

    def update(self, value: float) -> tuple[float, bool]:
        self._queue.append(value)
        self._sum += value
        if len(self._queue) > self.length:
            self._sum -= self._queue.popleft()
        if len(self._queue) < self.length:
            return 0.0, False
        return self._sum / self.length, True


class WilderATR:
    def __init__(self, length: int) -> None:
        self.length = max(1, length)
        self._prev_close: float | None = None
        self._trs: list[float] = []
        self._atr = 0.0
        self._ready = False

    def update(self, high: float, low: float, close: float) -> tuple[float, bool]:
        tr = high - low
        if self._prev_close is not None:
            tr = max(tr, abs(high - self._prev_close), abs(low - self._prev_close))

        if not self._ready:
            self._trs.append(tr)
            if len(self._trs) == self.length:
                self._atr = sum(self._trs) / self.length
                self._ready = True
        else:
            self._atr = ((self._atr * (self.length - 1)) + tr) / self.length

        self._prev_close = close
        if not self._ready:
            return 0.0, False
        return self._atr, True


class SessionVWAP:
    def __init__(self) -> None:
        self._session_key = ""
        self._pv_sum = 0.0
        self._v_sum = 0.0
        self._pv2_sum = 0.0

    def update(self, session_key: str, price: float, volume: float) -> tuple[float, bool]:
        if session_key != self._session_key:
            self._session_key = session_key
            self._pv_sum = 0.0
            self._v_sum = 0.0
            self._pv2_sum = 0.0
        if volume <= 0:
            if self._v_sum == 0:
                return 0.0, False
            return self._pv_sum / self._v_sum, True
        self._pv_sum += price * volume
        self._pv2_sum += price * price * volume
        self._v_sum += volume
        if self._v_sum == 0:
            return 0.0, False
        return self._pv_sum / self._v_sum, True

    def sigma(self) -> float:
        """Volume-weighted standard deviation of price around VWAP."""
        if self._v_sum <= 0:
            return 0.0
        mean = self._pv_sum / self._v_sum
        variance = (self._pv2_sum / self._v_sum) - (mean * mean)
        return math.sqrt(variance) if variance > 0 else 0.0


class LaguerreRSI:
    """ThinkScript-style Laguerre RSI with dynamic gamma (nFE window)."""

    def __init__(self, nfe: int = 13) -> None:
        self.nfe = max(2, nfe)
        self._prev_close: float | None = None
        self._l0 = self._l1 = self._l2 = self._l3 = 0.0
        self._has_laguerre = False
        self._rsi = 0.0
        self._tr_window: deque[float] = deque(maxlen=self.nfe)
        self._high_window: deque[float] = deque(maxlen=self.nfe)
        self._low_window: deque[float] = deque(maxlen=self.nfe)

    def update(
        self, open_: float, high: float, low: float, close: float
    ) -> tuple[float, float, bool]:
        if self._prev_close is None:
            self._prev_close = close
            return 0.0, 0.0, False

        h = max(high, self._prev_close)
        lw = min(low, self._prev_close)
        self._tr_window.append(h - lw)
        self._high_window.append(high)
        self._low_window.append(low)

        if len(self._tr_window) < self.nfe:
            self._prev_close = close
            return 0.0, 0.0, False

        o = (open_ + self._prev_close) / 2.0
        c = (o + h + lw + close) / 4.0

        sum_tr = sum(self._tr_window)
        den = max(self._high_window) - min(self._low_window)

        gamma = 0.0
        if den > 0 and sum_tr > 0:
            ratio = sum_tr / den
            if ratio > 0:
                gamma = math.log(ratio) / math.log(self.nfe)
        if math.isnan(gamma) or math.isinf(gamma):
            gamma = 0.0
        gamma = max(0.0, min(1.0, gamma))

        prev_l0, prev_l1, prev_l2, prev_l3 = self._l0, self._l1, self._l2, self._l3
        if not self._has_laguerre:
            prev_l0 = prev_l1 = prev_l2 = prev_l3 = c

        new_l0 = (1 - gamma) * c + gamma * prev_l0
        new_l1 = -gamma * new_l0 + prev_l0 + gamma * prev_l1
        new_l2 = -gamma * new_l1 + prev_l1 + gamma * prev_l2
        new_l3 = -gamma * new_l2 + prev_l2 + gamma * prev_l3

        cu = cd = 0.0
        for a, b in ((new_l0, new_l1), (new_l1, new_l2), (new_l2, new_l3)):
            if a >= b:
                cu += a - b
            else:
                cd += b - a

        prev_rsi = self._rsi
        rsi = cu / (cu + cd) if cu + cd > 0 else 0.5

        self._rsi = rsi
        self._l0, self._l1, self._l2, self._l3 = new_l0, new_l1, new_l2, new_l3
        self._has_laguerre = True
        self._prev_close = close
        return rsi, prev_rsi, True


class AverageVolume:
    def __init__(self, lookback: int) -> None:
        self._sma = SMA(lookback)

    def update(self, volume: float) -> tuple[float, bool]:
        return self._sma.update(volume)


def tod(ts: datetime) -> int:
    return ts.hour * 100 + ts.minute


def parse_hhmm(value: str) -> tuple[int, int]:
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError(f"expected HH:MM got {value!r}")
    return int(parts[0]), int(parts[1])


def hhmm_to_tod(value: str) -> int:
    hour, minute = parse_hhmm(value)
    return hour * 100 + minute


def is_rth(ts: datetime, rth_start: str, rth_end: str) -> bool:
    return hhmm_to_tod(rth_start) <= tod(ts) <= hhmm_to_tod(rth_end)
