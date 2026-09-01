"""Deterministic evaluation of the MES entry checklist.

Pure functions over a :class:`~tradingagents.mes.snapshot.MesSnapshot`. No LLM,
no I/O. The scoring half mirrors ``mes-tuner``'s weighted engine so results can
be diffed bar-for-bar; the SPY half encodes the discretionary confluence rules
from ``azp-dual-chart-workflow.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .config import MesChecklistConfig
from .indicators import hhmm_to_tod, is_rth, tod
from .snapshot import MesSnapshot, SeriesState

Side = Literal["long", "short"]
Direction = Literal["long", "short", "none"]
Divergence = Literal["bullish", "bearish", "none"]


@dataclass
class CheckItem:
    name: str
    chart: Literal["SPY", "MES"]
    passed: bool
    observed: str
    threshold: str
    weight: int = 0
    note: str = ""


@dataclass
class ChecklistResult:
    as_of_label: str
    side: Side
    direction: Direction

    spy_items: list[CheckItem] = field(default_factory=list)
    mes_items: list[CheckItem] = field(default_factory=list)

    score: int = 0
    max_score: int = 0
    confirmations: int = 0
    required: int = 0
    tier: str = "low"
    score_ok: bool = False

    gates_ok: bool = False
    gate_reasons: list[str] = field(default_factory=list)
    no_trade_reasons: list[str] = field(default_factory=list)

    spy_confirmations: int = 0
    spy_confluence_ok: bool = False
    divergence: Divergence = "none"

    vwap: float = 0.0
    atr: float = 0.0
    upper_atr_band: float = 0.0
    lower_atr_band: float = 0.0
    opening_range_high: float | None = None
    opening_range_low: float | None = None
    last_price: float = 0.0

    warnings: list[str] = field(default_factory=list)

    @property
    def tradeable(self) -> bool:
        """Hard verdict. The LLM gatekeeper may narrow this but never widen it."""
        # A tier that sizes to zero contracts is a skip, not a trade.
        return (
            self.gates_ok
            and self.score_ok
            and self.spy_confluence_ok
            and tier_contract_band(self.tier)[1] > 0
            and not self.no_trade_reasons
        )

    @property
    def all_items(self) -> list[CheckItem]:
        return self.spy_items + self.mes_items


def score_tier(count: int) -> str:
    if count >= 8:
        return "premium"
    if count >= 6:
        return "standard"
    if count >= 4:
        return "marginal"
    return "low"


def required_confirmations(count: int, cfg: MesChecklistConfig) -> int:
    if not cfg.enable_tier_min_confirmations:
        return cfg.min_confirmations
    return {
        "premium": cfg.min_confirmations_premium,
        "standard": cfg.min_confirmations_standard,
        "marginal": cfg.min_confirmations_marginal,
        "low": cfg.min_confirmations_marginal,
    }[score_tier(count) if count > 0 else "low"]


def max_achievable_score(cfg: MesChecklistConfig) -> int:
    pairs = (
        (cfg.enable_momentum, cfg.momentum_score_weight),
        (cfg.enable_vwap, cfg.vwap_score_weight),
        (cfg.enable_atr_range, cfg.atr_range_score_weight),
        (cfg.enable_pattern, cfg.pattern_score_weight),
        (cfg.enable_add, cfg.add_score_weight),
        (cfg.enable_tick, cfg.tick_score_weight),
        (cfg.enable_vold, cfg.vold_score_weight),
        (cfg.enable_volume_surge, cfg.volume_surge_score_weight),
    )
    return sum(weight for enabled, weight in pairs if enabled)


def tier_contract_band(tier: str) -> tuple[int, int]:
    """Suggested contract range per quality tier (azp-dual-chart-workflow.md)."""
    return {"premium": (2, 3), "standard": (1, 2), "marginal": (1, 1)}.get(tier, (0, 0))


def _momentum(state: SeriesState, cfg: MesChecklistConfig, side: Side) -> bool:
    if not cfg.enable_momentum or not state.laguerre_ready:
        return False
    if not (state.prev_sma_ready and state.prev_vwap_ready and state.sma_ready and state.vwap_ready):
        return False
    if side == "long":
        trend = state.laguerre > 0.2 and state.laguerre >= state.prev_laguerre
        return state.prev_sma < state.prev_vwap and state.sma > state.vwap and trend
    trend = state.laguerre < 0.8 and state.laguerre <= state.prev_laguerre
    return state.prev_sma > state.prev_vwap and state.sma < state.vwap and trend


def _pattern(state: SeriesState, cfg: MesChecklistConfig, side: Side) -> bool:
    if not cfg.enable_pattern:
        return False
    bar = state.last
    total_range = bar.high - bar.low
    if total_range <= 0:
        return False
    body = abs(bar.close - bar.open)
    upper_wick = bar.high - max(bar.open, bar.close)
    lower_wick = min(bar.open, bar.close) - bar.low
    body_ratio_ok = (body / total_range) <= cfg.max_body_to_range_ratio
    retrace = cfg.retracement_percent / 100.0

    if side == "long":
        hammer = lower_wick >= cfg.wick_to_body_ratio * body and body_ratio_ok
        confirmed = hammer and bar.close >= bar.low + retrace * total_range
        if cfg.pattern_require_vwap_side:
            confirmed = confirmed and state.vwap_ready and bar.close < state.vwap
        return confirmed
    inverse = upper_wick >= cfg.wick_to_body_ratio * body and body_ratio_ok
    confirmed = inverse and bar.close <= bar.high - retrace * total_range
    if cfg.pattern_require_vwap_side:
        confirmed = confirmed and state.vwap_ready and bar.close > state.vwap
    return confirmed


def detect_divergence(snapshot: MesSnapshot) -> Divergence:
    """Bar-over-bar SPY direction versus $VOLD sign (TOS MES_VOLD_Histogram parity)."""
    return snapshot.vold_bar_divergence()  # type: ignore[return-value]


def _evaluate_spy(snapshot: MesSnapshot, side: Side) -> list[CheckItem]:
    cfg = snapshot.config
    spy = snapshot.spy
    add, tick, vold = snapshot.add, snapshot.tick, snapshot.vold
    slope = snapshot.vold_slope()
    items: list[CheckItem] = []

    vwap_ok = spy.vwap_ready and (
        spy.close > spy.vwap if side == "long" else spy.close < spy.vwap
    )
    items.append(
        CheckItem(
            name="SPY accepted on correct side of VWAP",
            chart="SPY",
            passed=vwap_ok,
            observed=f"close {spy.close:.2f} vs VWAP {spy.vwap:.2f}",
            threshold="above VWAP" if side == "long" else "below VWAP",
        )
    )

    add_ok = add is not None and (
        add > cfg.add_trend_threshold if side == "long" else add < -cfg.add_trend_threshold
    )
    items.append(
        CheckItem(
            name="$ADD breadth trending",
            chart="SPY",
            passed=add_ok,
            observed="unavailable" if add is None else f"{add:+.0f}",
            threshold=f"{'>' if side == 'long' else '<'} {'+' if side == 'long' else '-'}{cfg.add_trend_threshold:.0f}",
        )
    )

    vold_ok = slope is not None and (slope > 0 if side == "long" else slope < 0)
    items.append(
        CheckItem(
            name="$VOLD trending with direction",
            chart="SPY",
            passed=vold_ok,
            observed="unavailable" if slope is None else f"{slope:+.0f} over {cfg.vold_slope_bars} bars",
            threshold="rising" if side == "long" else "falling",
        )
    )

    tick_ok = snapshot.tick_sustained(side)
    eff_thresh = snapshot.tick_effective_threshold()
    items.append(
        CheckItem(
            name="$TICK holding directionally",
            chart="SPY",
            passed=tick_ok,
            observed="unavailable" if tick is None else f"{tick:+.0f}",
            threshold=(
                f"{cfg.tick_sustain_bars} bars "
                f"{'>' if side == 'long' else '<'} {'+' if side == 'long' else '-'}{eff_thresh:.0f}"
            ),
        )
    )

    exhausted = tick is not None and abs(tick) >= cfg.tick_extreme_threshold and (
        tick > 0 if side == "long" else tick < 0
    )
    items.append(
        CheckItem(
            name="Room to target (not at exhaustion extreme)",
            chart="SPY",
            passed=not exhausted,
            observed="unavailable" if tick is None else f"$TICK {tick:+.0f}",
            threshold=f"|$TICK| < {cfg.tick_extreme_threshold:.0f}",
            note="chase risk into an exhausted extreme" if exhausted else "",
        )
    )
    return items


def _evaluate_mes(snapshot: MesSnapshot, side: Side) -> tuple[list[CheckItem], int, int]:
    cfg = snapshot.config
    mes = snapshot.mes
    add, tick, vold = snapshot.add, snapshot.tick, snapshot.vold
    long_side = side == "long"

    momentum = _momentum(mes, cfg, side)
    vwap = cfg.enable_vwap and mes.vwap_ready and (
        mes.close > mes.vwap if long_side else mes.close < mes.vwap
    )
    atr_observed = (
        "session not started"
        if not mes.has_session_start
        else f"close {mes.close:.2f} in [{mes.lower_atr_band:.2f}, {mes.upper_atr_band:.2f}]"
    )
    atr_range = (
        cfg.enable_atr_range
        and mes.atr_ready
        and mes.has_session_start
        and mes.lower_atr_band < mes.close < mes.upper_atr_band
    )
    pattern = _pattern(mes, cfg, side)

    add_sig = cfg.enable_add and add is not None and (
        add > cfg.add_threshold if long_side else add < -cfg.add_threshold
    )
    tick_sig = cfg.enable_tick and snapshot.tick_confirms(side)
    eff_thresh = snapshot.tick_effective_threshold()
    divergence = snapshot.vold_bar_divergence()
    if cfg.enable_vold and vold is not None:
        if cfg.vold_use_trend:
            prev = snapshot.spy.prev_vold
            trend_ok = prev is not None and (
                (vold > cfg.vold_threshold and vold > prev)
                if long_side
                else (vold < cfg.vold_threshold and vold < prev)
            )
            confirms_ok = snapshot.vold_confirms(side)
            opposing = (long_side and divergence == "bearish") or (
                not long_side and divergence == "bullish"
            )
            vold_sig = (trend_ok or confirms_ok) and not opposing
        else:
            vold_sig = vold > cfg.vold_threshold if long_side else vold < cfg.vold_threshold
    else:
        vold_sig = False

    vol_surge = (
        cfg.enable_volume_surge
        and mes.volume_ready
        and mes.last.volume > mes.avg_volume * cfg.volume_multiplier
    )

    signals: list[tuple[str, bool, int, bool, str, str]] = [
        (
            "Momentum (SMA/VWAP cross + Laguerre)",
            momentum,
            cfg.momentum_score_weight,
            cfg.enable_momentum,
            f"SMA {mes.sma:.2f} vs VWAP {mes.vwap:.2f}, LagRSI {mes.laguerre:.2f}",
            "cross with trend" if long_side else "cross down with trend",
        ),
        (
            "MES on correct side of VWAP",
            vwap,
            cfg.vwap_score_weight,
            cfg.enable_vwap,
            f"close {mes.close:.2f} vs VWAP {mes.vwap:.2f}",
            "above VWAP" if long_side else "below VWAP",
        ),
        (
            "Inside \u00b12 ATR session bands",
            atr_range,
            cfg.atr_range_score_weight,
            cfg.enable_atr_range,
            atr_observed,
            f"within \u00b1{cfg.atr_multiplier:g} ATR",
        ),
        (
            "Reversal pattern",
            pattern,
            cfg.pattern_score_weight,
            cfg.enable_pattern,
            "hammer" if long_side else "inverse hammer",
            f"wick \u2265 {cfg.wick_to_body_ratio:g}\u00d7 body, {cfg.retracement_percent:g}% retrace",
        ),
        (
            "$ADD confirms",
            add_sig,
            cfg.add_score_weight,
            cfg.enable_add,
            "unavailable" if add is None else f"{add:+.0f}",
            f"{'>' if long_side else '<'} {'+' if long_side else '-'}{cfg.add_threshold:.0f}",
        ),
        (
            "$TICK confirms",
            tick_sig,
            cfg.tick_score_weight,
            cfg.enable_tick,
            _tick_observed(snapshot, tick),
            _tick_threshold_label(snapshot, long_side, eff_thresh),
        ),
        (
            "$VOLD confirms",
            vold_sig,
            cfg.vold_score_weight,
            cfg.enable_vold,
            "unavailable" if vold is None else f"{vold:+.0f}",
            "rising above 0" if long_side else "falling below 0",
        ),
        (
            "Volume surge",
            vol_surge,
            cfg.volume_surge_score_weight,
            cfg.enable_volume_surge,
            f"{mes.last.volume:.0f} vs avg {mes.avg_volume:.0f}",
            f"> {cfg.volume_multiplier:g}\u00d7 {cfg.volume_lookback}-bar avg",
        ),
    ]

    items = [
        CheckItem(
            name=name,
            chart="MES",
            passed=passed,
            observed=observed,
            threshold=threshold,
            weight=weight,
            note="" if enabled else "signal disabled",
        )
        for name, passed, weight, enabled, observed, threshold in signals
    ]
    score = sum(weight for _, passed, weight, enabled, _, _ in signals if passed and enabled)
    confirmations = sum(1 for _, passed, _, enabled, _, _ in signals if passed and enabled)
    return items, score, confirmations


def _tick_observed(snapshot: MesSnapshot, tick: float | None) -> str:
    if tick is None:
        return "unavailable"
    signal = snapshot.tick_signal()
    base = f"{tick:+.0f}"
    if signal == "persistent_buy":
        return f"{base} (persistent buy x{snapshot.tick_streak(positive=True)})"
    if signal == "persistent_sell":
        return f"{base} (persistent sell x{snapshot.tick_streak(positive=False)})"
    if snapshot.tick_burst("long" if tick > 0 else "short"):
        return f"{base} (burst)"
    return base


def _tick_threshold_label(snapshot: MesSnapshot, long_side: bool, eff_thresh: float) -> str:
    cfg = snapshot.config
    if cfg.use_dynamic_tick_threshold:
        prefix = f"dynamic ±{eff_thresh:.0f}"
    else:
        prefix = f"{'>' if long_side else '<'} {'+' if long_side else '-'}{eff_thresh:.0f}"
    return f"{prefix} or {cfg.tick_persistent_bars}-bar persistent"


def _evaluate_gates(snapshot: MesSnapshot, side: Side) -> tuple[bool, list[str]]:
    cfg = snapshot.config
    ts = snapshot.as_of
    now = tod(ts)
    reasons: list[str] = []

    if ts.weekday() >= 5:
        reasons.append("weekend \u2014 market closed")
    if cfg.require_rth and not is_rth(ts, cfg.rth_start, cfg.rth_end):
        reasons.append(f"outside RTH ({cfg.rth_start}\u2013{cfg.rth_end} ET)")

    if cfg.enforce_session_filters:
        block = hhmm_to_tod(cfg.block_morning_until)
        if now < block and not cfg.allow_orb_window:
            reasons.append(f"morning block until {cfg.block_morning_until} ET (ORB window disabled)")
        if now >= hhmm_to_tod(cfg.last_entry_time):
            reasons.append(f"past last entry time {cfg.last_entry_time} ET")
        if now >= hhmm_to_tod(cfg.exit_time):
            reasons.append(f"past EOD flatten {cfg.exit_time} ET")
        if cfg.min_atr_points > 0 and not (
            snapshot.mes.atr_ready and snapshot.mes.atr >= cfg.min_atr_points
        ):
            reasons.append(f"ATR {snapshot.mes.atr:.2f} below minimum {cfg.min_atr_points:g}")

    if not cfg.allow_missing_internals:
        missing = [
            label
            for label, enabled, value in (
                ("$ADD", cfg.enable_add, snapshot.add),
                ("$TICK", cfg.enable_tick, snapshot.tick),
                ("$VOLD", cfg.enable_vold, snapshot.vold),
            )
            if enabled and value is None
        ]
        if missing:
            reasons.append(f"internals missing: {', '.join(missing)}")

    return not reasons, reasons


def _no_trade_reasons(snapshot: MesSnapshot, side: Side, spy_confirmations: int) -> list[str]:
    cfg = snapshot.config
    add, tick = snapshot.add, snapshot.tick
    slope = snapshot.vold_slope()
    reasons: list[str] = []

    if add is not None and abs(add) <= cfg.add_chop_threshold:
        if slope is None or abs(slope) < 1:
            reasons.append(
                f"$ADD in chop zone (\u00b1{cfg.add_chop_threshold:.0f}) with flat $VOLD"
            )
    if snapshot.tick_whipsawing():
        reasons.append("$TICK whipsawing around 0 with no directional control")
    divergence = detect_divergence(snapshot)
    if side == "long" and divergence == "bearish":
        reasons.append("$VOLD diverging bearishly from SPY bar direction")
    if side == "short" and divergence == "bullish":
        reasons.append("$VOLD diverging bullishly from SPY bar direction")
    if spy_confirmations < 3:
        reasons.append(f"only {spy_confirmations}/5 SPY confirmations (3 required)")
    if tick is not None and abs(tick) >= cfg.tick_extreme_threshold and (
        (tick > 0) == (side == "long")
    ):
        reasons.append(f"$TICK at exhaustion extreme ({tick:+.0f}) for a {side}")
    return reasons


def _pick_side(snapshot: MesSnapshot) -> Side:
    long_items, long_score, _ = _evaluate_mes(snapshot, "long")
    _, short_score, _ = _evaluate_mes(snapshot, "short")
    if short_score > long_score:
        return "short"
    return "long"


def evaluate(
    snapshot: MesSnapshot,
    side: Side | Literal["auto"] = "auto",
) -> ChecklistResult:
    """Run the full checklist for one side (or the stronger side when ``auto``)."""
    cfg = snapshot.config
    resolved: Side = _pick_side(snapshot) if side == "auto" else side

    spy_items = _evaluate_spy(snapshot, resolved)
    mes_items, score, confirmations = _evaluate_mes(snapshot, resolved)
    gates_ok, gate_reasons = _evaluate_gates(snapshot, resolved)

    spy_confirmations = sum(1 for item in spy_items if item.passed)
    required = required_confirmations(confirmations, cfg)
    tier = score_tier(confirmations)
    no_trade = _no_trade_reasons(snapshot, resolved, spy_confirmations)

    result = ChecklistResult(
        as_of_label=snapshot.as_of.strftime("%Y-%m-%d %H:%M ET"),
        side=resolved,
        direction="none",
        spy_items=spy_items,
        mes_items=mes_items,
        score=score,
        max_score=max_achievable_score(cfg),
        confirmations=confirmations,
        required=required,
        tier=tier,
        score_ok=confirmations >= required,
        gates_ok=gates_ok,
        gate_reasons=gate_reasons,
        no_trade_reasons=no_trade,
        spy_confirmations=spy_confirmations,
        spy_confluence_ok=spy_confirmations >= 3,
        divergence=detect_divergence(snapshot),
        vwap=snapshot.mes.vwap,
        atr=snapshot.mes.atr,
        upper_atr_band=snapshot.mes.upper_atr_band,
        lower_atr_band=snapshot.mes.lower_atr_band,
        opening_range_high=snapshot.mes.opening_range_high,
        opening_range_low=snapshot.mes.opening_range_low,
        last_price=snapshot.mes.close,
        warnings=list(snapshot.warnings),
    )
    result.direction = resolved if result.tradeable else "none"
    return result
