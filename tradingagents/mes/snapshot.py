"""Builds the point-in-time market picture the MES checklist evaluates.

Joins SPY and /MES 5m bars with $ADD/$TICK/$VOLD internals, then replays them
through the streaming indicators so the state at ``as_of`` is identical to what
a bar-by-bar engine would have produced.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from .config import MesChecklistConfig
from .internals import (
    tick_burst as _tick_burst,
    tick_confirms_side,
    tick_effective_threshold as _tick_effective_threshold,
    tick_signal as _tick_signal,
    tick_streak as _tick_streak,
    vold_bar_divergence,
    vold_confirms_side,
    vold_z_score as _vold_z_score,
)
from .indicators import (
    AverageVolume,
    LaguerreRSI,
    SessionVWAP,
    SMA,
    WilderATR,
    hhmm_to_tod,
    is_rth,
    tod,
)
from .profile import volume_profile

logger = logging.getLogger(__name__)


@dataclass
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    add: float | None = None
    tick: float | None = None
    vold: float | None = None


@dataclass
class SessionLevels:
    """Completed-session reference levels used to mark up the pre-market chart."""

    session_date: str
    open: float
    high: float
    low: float
    close: float
    vwap: float
    poc: float | None = None
    vah: float | None = None
    val: float | None = None


@dataclass
class SeriesState:
    """Indicator state and session structure at the final bar of a series."""

    symbol: str
    bars: list[Bar] = field(default_factory=list)

    vwap: float = 0.0
    vwap_ready: bool = False
    vwap_sigma: float = 0.0
    prev_vwap: float = 0.0
    prev_vwap_ready: bool = False

    sma: float = 0.0
    sma_ready: bool = False
    prev_sma: float = 0.0
    prev_sma_ready: bool = False

    atr: float = 0.0
    atr_ready: bool = False
    upper_atr_band: float = 0.0
    lower_atr_band: float = 0.0
    session_start_price: float = 0.0
    has_session_start: bool = False

    laguerre: float = 0.0
    prev_laguerre: float = 0.0
    laguerre_ready: bool = False

    avg_volume: float = 0.0
    volume_ready: bool = False

    opening_range_high: float | None = None
    opening_range_low: float | None = None

    prev_vold: float | None = None

    @property
    def last(self) -> Bar:
        return self.bars[-1]

    @property
    def close(self) -> float:
        return self.bars[-1].close

    def vwap_band(self, sigma_multiple: float) -> float:
        return self.vwap + sigma_multiple * self.vwap_sigma

    def session_change(self) -> float:
        """Points moved since the first RTH bar."""
        if not self.has_session_start:
            return 0.0
        return self.close - self.session_start_price


def _replay(symbol: str, bars: list[Bar], cfg: MesChecklistConfig) -> SeriesState:
    state = SeriesState(symbol=symbol, bars=bars)
    sma = SMA(cfg.sma_length)
    atr = WilderATR(cfg.atr_length)
    vwap = SessionVWAP()
    laguerre = LaguerreRSI(cfg.nfe)
    avg_vol = AverageVolume(cfg.volume_lookback)

    or_end = hhmm_to_tod(cfg.opening_range_end)
    rth_start = hhmm_to_tod(cfg.rth_start)
    last_session_key = ""

    for bar in bars:
        session_key = bar.timestamp.strftime("%Y-%m-%d")
        if session_key != last_session_key:
            last_session_key = session_key
            state.has_session_start = False
            state.opening_range_high = None
            state.opening_range_low = None

        state.prev_sma, state.prev_sma_ready = state.sma, state.sma_ready
        state.prev_vwap, state.prev_vwap_ready = state.vwap, state.vwap_ready

        typical = (bar.high + bar.low + bar.close) / 3.0
        state.vwap, state.vwap_ready = vwap.update(session_key, typical, bar.volume)
        state.vwap_sigma = vwap.sigma()
        state.sma, state.sma_ready = sma.update(bar.close)
        state.atr, state.atr_ready = atr.update(bar.high, bar.low, bar.close)
        state.avg_volume, state.volume_ready = avg_vol.update(bar.volume)
        state.laguerre, state.prev_laguerre, state.laguerre_ready = laguerre.update(
            bar.open, bar.high, bar.low, bar.close
        )

        in_rth = is_rth(bar.timestamp, cfg.rth_start, cfg.rth_end)
        if in_rth and not state.has_session_start:
            state.session_start_price = bar.close
            state.has_session_start = True

        if in_rth and rth_start <= tod(bar.timestamp) < or_end:
            state.opening_range_high = (
                bar.high if state.opening_range_high is None else max(state.opening_range_high, bar.high)
            )
            state.opening_range_low = (
                bar.low if state.opening_range_low is None else min(state.opening_range_low, bar.low)
            )

    if state.has_session_start and state.atr_ready:
        state.upper_atr_band = state.session_start_price + state.atr * cfg.atr_multiplier
        state.lower_atr_band = state.session_start_price - state.atr * cfg.atr_multiplier

    # prev_vold must be the value one bar back, not the current bar's.
    state.prev_vold = bars[-2].vold if len(bars) >= 2 else None
    return state


def prior_session_levels(
    bars: list[Bar],
    as_of: datetime,
    cfg: MesChecklistConfig,
    tick_size: float,
) -> SessionLevels | None:
    """Levels from the most recent RTH session that closed before ``as_of``'s date."""
    rth = [b for b in bars if is_rth(b.timestamp, cfg.rth_start, cfg.rth_end)]
    prior = [b for b in rth if b.timestamp.date() < as_of.date()]
    if not prior:
        return None

    session_date = max(b.timestamp.date() for b in prior)
    session = [b for b in prior if b.timestamp.date() == session_date]
    if not session:
        return None

    volume = sum(b.volume or 0.0 for b in session)
    typical = sum(((b.high + b.low + b.close) / 3.0) * (b.volume or 0.0) for b in session)
    profile = volume_profile(session, tick_size)

    return SessionLevels(
        session_date=session_date.strftime("%Y-%m-%d"),
        open=session[0].open,
        high=max(b.high for b in session),
        low=min(b.low for b in session),
        close=session[-1].close,
        vwap=(typical / volume) if volume > 0 else session[-1].close,
        poc=profile.poc if profile else None,
        vah=profile.vah if profile else None,
        val=profile.val if profile else None,
    )


def overnight_range(
    bars: list[Bar],
    as_of: datetime,
    cfg: MesChecklistConfig,
) -> tuple[float, float] | None:
    """High/low since the prior RTH close, covering globex and pre-market."""
    prior_rth = [
        b
        for b in bars
        if is_rth(b.timestamp, cfg.rth_start, cfg.rth_end) and b.timestamp.date() < as_of.date()
    ]
    if not prior_rth:
        return None
    after = max(b.timestamp for b in prior_rth)
    window = [b for b in bars if after < b.timestamp <= as_of]
    if not window:
        return None
    return max(b.high for b in window), min(b.low for b in window)


@dataclass
class MesSnapshot:
    as_of: datetime
    session_date: str
    config: MesChecklistConfig
    mes: SeriesState
    spy: SeriesState
    warnings: list[str] = field(default_factory=list)
    prior_mes: SessionLevels | None = None
    prior_spy: SessionLevels | None = None
    overnight_mes: tuple[float, float] | None = None

    @property
    def session_open(self) -> datetime:
        return session_bounds(self.as_of, self.config)[0]

    @property
    def rth_started(self) -> bool:
        """True once today's cash session has printed a bar.

        Checked against ``as_of``'s own date rather than the replay state, so a
        holiday or a stale feed reads as "not started" instead of inheriting the
        previous session's anchor.
        """
        cfg = self.config
        return any(
            bar.timestamp.date() == self.as_of.date()
            and is_rth(bar.timestamp, cfg.rth_start, cfg.rth_end)
            for bar in self.mes.bars
        )

    @property
    def add(self) -> float | None:
        return self.spy.last.add

    @property
    def tick(self) -> float | None:
        return self.spy.last.tick

    @property
    def vold(self) -> float | None:
        return self.spy.last.vold

    def _tick_history(self) -> list[float]:
        return [b.tick for b in self.spy.bars if b.tick is not None]

    def _vold_history(self) -> list[float]:
        return [b.vold for b in self.spy.bars if b.vold is not None]

    def _prev_spy_close(self) -> float | None:
        if len(self.spy.bars) < 2:
            return None
        return self.spy.bars[-2].close

    def tick_effective_threshold(self) -> float:
        return _tick_effective_threshold(self._tick_history(), self.config)

    def tick_streak(self, *, positive: bool) -> int:
        return _tick_streak(self._tick_history(), positive=positive)

    def tick_signal(self) -> str:
        return _tick_signal(self.tick, self._tick_history(), self.config)

    def tick_confirms(self, side: str) -> bool:
        return tick_confirms_side(self.tick_signal(), side)

    def tick_burst(self, side: str) -> bool:
        return _tick_burst(self.tick, self._tick_history(), self.config, side)

    def vold_z_score(self) -> float | None:
        return _vold_z_score(self._vold_history(), self.config.vold_zscore_lookback)

    def vold_confirms(self, side: str) -> bool:
        return vold_confirms_side(
            self.vold,
            self.spy.close,
            self._prev_spy_close(),
            side,
            zero_line=self.config.vold_threshold,
        )

    def vold_bar_divergence(self) -> str:
        return vold_bar_divergence(
            self.vold,
            self.spy.close,
            self._prev_spy_close(),
            zero_line=self.config.vold_threshold,
        )

    def vold_slope(self) -> float | None:
        """Net change in $VOLD across the configured lookback window."""
        window = [b.vold for b in self.spy.bars[-self.config.vold_slope_bars :] if b.vold is not None]
        if len(window) < 2:
            return None
        return window[-1] - window[0]

    def tick_sustained(self, direction: str) -> bool:
        """True when the last N bars all held beyond the $TICK threshold."""
        n = self.config.tick_sustain_bars
        window = [b.tick for b in self.spy.bars[-n:] if b.tick is not None]
        if len(window) < n:
            return False
        threshold = self.tick_effective_threshold()
        if direction == "long":
            return all(v > threshold for v in window)
        return all(v < -threshold for v in window)

    def tick_whipsawing(self) -> bool:
        """True when $TICK keeps crossing zero with no directional control."""
        window = [b.tick for b in self.spy.bars[-self.config.tick_sustain_bars * 2 :] if b.tick is not None]
        if len(window) < 4:
            return False
        crossings = sum(1 for a, b in zip(window, window[1:]) if (a > 0) != (b > 0))
        threshold = self.tick_effective_threshold()
        return crossings >= 2 and all(abs(v) < threshold for v in window)


def _merge_internals_onto_bars(frame: pd.DataFrame, internals: pd.DataFrame | None) -> pd.DataFrame:
    """Attach internals columns to OHLCV bars, filling same-day gaps after the open."""
    if internals is None or internals.empty or frame.empty:
        return frame

    merged = frame.merge(internals, on="Date", how="left", suffixes=("", "_internal"))
    for col in ("add", "tick", "vold"):
        if col not in merged.columns:
            continue
        merged[col] = merged.groupby(merged["Date"].dt.date, group_keys=False)[col].ffill()
    return merged


def _frame_to_bars(frame: pd.DataFrame, internals: pd.DataFrame | None) -> list[Bar]:
    if internals is not None and not internals.empty:
        frame = _merge_internals_onto_bars(frame, internals)
    bars: list[Bar] = []
    for row in frame.itertuples(index=False):
        bars.append(
            Bar(
                timestamp=pd.Timestamp(row.Date).to_pydatetime(),
                open=float(row.Open),
                high=float(row.High),
                low=float(row.Low),
                close=float(row.Close),
                volume=float(row.Volume or 0.0),
                add=_opt_float(getattr(row, "add", None)),
                tick=_opt_float(getattr(row, "tick", None)),
                vold=_opt_float(getattr(row, "vold", None)),
            )
        )
    return bars


def _opt_float(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def session_bounds(as_of: datetime, cfg: MesChecklistConfig) -> tuple[datetime, datetime]:
    """Session open and the ATR warm-up fetch start for the given ``as_of``."""
    hour, minute = divmod(hhmm_to_tod(cfg.rth_start), 100)
    session_start = as_of.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # ATR(14) + Laguerre(13) + volume(20) need prior-session bars to warm up.
    fetch_start = session_start - timedelta(days=5)
    return session_start, fetch_start


# NYSE breadth ($ADD, $VOLD) is cumulative over the cash session and does not
# publish until after the 09:30 ET open; $TICK can appear earlier. Skip the
# internals fetch until one full 5m bar has elapsed so we avoid empty-candle
# warnings and wasted API calls.
INTERNALS_FIRST_BAR = timedelta(minutes=5)


def build_snapshot(
    as_of: datetime,
    cfg: MesChecklistConfig,
    *,
    fetch_bars=None,
    fetch_internals=None,
) -> MesSnapshot:
    """Fetch and replay SPY + /MES + internals up to ``as_of``.

    ``fetch_bars`` / ``fetch_internals`` are injection points for tests and for
    replaying recorded sessions; both default to the Schwab dataflow.
    """
    from ..dataflows.schwab import get_internals_frame, get_intraday_5m_candles

    fetch_bars = fetch_bars or get_intraday_5m_candles
    fetch_internals = fetch_internals or get_internals_frame

    session_start, fetch_start = session_bounds(as_of, cfg)
    warnings: list[str] = []

    internals: pd.DataFrame | None = None
    if as_of < session_start + INTERNALS_FIRST_BAR:
        warnings.append(
            f"$ADD/$TICK/$VOLD do not publish until after the {cfg.rth_start} ET open; "
            "breadth is unavailable this early"
        )
    else:
        try:
            internals = fetch_internals(session_start, as_of, "5m")
        except Exception as exc:
            warnings.append(f"market internals unavailable: {exc}")
            logger.warning("MES snapshot: internals unavailable: %s", exc)

    mes_frame = fetch_bars(cfg.mes_symbol, session_start, as_of, fetch_start=fetch_start)
    spy_frame = fetch_bars(cfg.spy_symbol, session_start, as_of, fetch_start=fetch_start)

    mes_bars = _frame_to_bars(mes_frame, internals)
    spy_bars = _frame_to_bars(spy_frame, internals)
    if not mes_bars or not spy_bars:
        raise ValueError("no bars available for the requested as_of")

    snapshot = MesSnapshot(
        as_of=as_of,
        session_date=as_of.strftime("%Y-%m-%d"),
        config=cfg,
        mes=_replay(cfg.mes_symbol, mes_bars, cfg),
        spy=_replay(cfg.spy_symbol, spy_bars, cfg),
        warnings=warnings,
        prior_mes=prior_session_levels(mes_bars, as_of, cfg, cfg.mes_tick_size),
        prior_spy=prior_session_levels(spy_bars, as_of, cfg, cfg.spy_tick_size),
        overnight_mes=overnight_range(mes_bars, as_of, cfg),
    )
    if snapshot.rth_started and as_of >= session_start + INTERNALS_FIRST_BAR and internals is not None:
        missing = [
            label
            for label, value in (
                ("$ADD", snapshot.add),
                ("$TICK", snapshot.tick),
                ("$VOLD", snapshot.vold),
            )
            if value is None
        ]
        if missing:
            warnings.append(
                "Schwab returned no live readings for "
                f"{', '.join(missing)} this session after REST, quotes, and streamer. "
                "Breadth gates will block until those symbols publish or you set "
                "TRADINGAGENTS_MES_ALLOW_MISSING_INTERNALS=true."
            )
            snapshot.warnings = warnings
    return snapshot


def snapshot_from_csv(
    mes_csv: str | Path,
    spy_csv: str | Path,
    as_of: datetime,
    cfg: MesChecklistConfig,
) -> MesSnapshot:
    """Build a snapshot from recorded ``timestamp,open,high,low,close,volume,add,tick,vold`` CSVs."""

    def _load(path: str | Path) -> list[Bar]:
        frame = pd.read_csv(path)
        frame["Date"] = pd.to_datetime(frame["timestamp"]).dt.tz_localize(None)
        frame = frame[frame["Date"] <= pd.to_datetime(as_of)].sort_values("Date")
        frame = frame.rename(
            columns={
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
            }
        )
        return _frame_to_bars(frame, None)

    mes_bars = _load(mes_csv)
    spy_bars = _load(spy_csv)
    if not mes_bars or not spy_bars:
        raise ValueError(f"no bars at or before {as_of} in the supplied CSVs")

    return MesSnapshot(
        as_of=as_of,
        session_date=as_of.strftime("%Y-%m-%d"),
        config=cfg,
        mes=_replay(cfg.mes_symbol, mes_bars, cfg),
        spy=_replay(cfg.spy_symbol, spy_bars, cfg),
        prior_mes=prior_session_levels(mes_bars, as_of, cfg, cfg.mes_tick_size),
        prior_spy=prior_session_levels(spy_bars, as_of, cfg, cfg.spy_tick_size),
        overnight_mes=overnight_range(mes_bars, as_of, cfg),
    )
