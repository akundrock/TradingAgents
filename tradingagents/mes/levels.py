"""Deterministic trade-level helper for the MES gatekeeper pipeline.

Two public concerns:

1. :func:`suggest_trade_levels` — collects all known structural levels from a
   snapshot and checklist result, then filters them into directionally-valid
   stop and target candidates.  The gatekeeper receives these as a pre-built
   hint so it can adopt them verbatim rather than free-forming numbers.

2. :func:`normalize_trade_gonogo` — post-processes the LLM's structured output
   to enforce invariants the schema text alone cannot guarantee:

   * Wait / Stand Down → no trade levels (they go in what_would_change_my_mind)
   * Take long  → first_target > entry, stop < entry
   * Take short → first_target < entry, stop > entry
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structural-level collection
# ---------------------------------------------------------------------------


@dataclass
class TradeLevels:
    """Directionally-validated level suggestions from deterministic data."""

    entry_zone: str
    stop_level: float
    first_target: float
    level_labels: dict[float, str] = field(default_factory=dict)


def _collect_levels(
    last_price: float,
    vwap: float,
    atr: float,
    *,
    atr_upper: float | None = None,
    atr_lower: float | None = None,
    orb_high: float | None = None,
    orb_low: float | None = None,
    prior_high: float | None = None,
    prior_low: float | None = None,
    prior_close: float | None = None,
    prior_vah: float | None = None,
    prior_val: float | None = None,
    prior_poc: float | None = None,
    overnight_high: float | None = None,
    overnight_low: float | None = None,
) -> dict[float, str]:
    """Return {price: label} for every structural level we have."""
    candidates: dict[float, str] = {}

    def add(price: float | None, label: str) -> None:
        if price is not None and price > 0:
            candidates[round(price, 2)] = label

    add(vwap, "VWAP")
    add(atr_upper, "+2 ATR band")
    add(atr_lower, "-2 ATR band")
    add(orb_high, "ORB high")
    add(orb_low, "ORB low")
    add(prior_high, "prior-day high")
    add(prior_low, "prior-day low")
    add(prior_close, "prior-day close")
    add(prior_vah, "prior VAH")
    add(prior_val, "prior VAL")
    add(prior_poc, "prior POC")
    add(overnight_high, "overnight high")
    add(overnight_low, "overnight low")

    # Round-100 and round-50 reference near the last price (helpful when few
    # structural levels exist above/below current price).
    for mult in (100, 50):
        above = (last_price // mult + 1) * mult
        below = (last_price // mult) * mult
        candidates.setdefault(round(above, 2), f"round {mult}")
        candidates.setdefault(round(below, 2), f"round {mult}")

    return candidates


def suggest_trade_levels(
    side: str,
    last_price: float,
    vwap: float,
    atr: float,
    *,
    atr_upper: float | None = None,
    atr_lower: float | None = None,
    orb_high: float | None = None,
    orb_low: float | None = None,
    prior_high: float | None = None,
    prior_low: float | None = None,
    prior_close: float | None = None,
    prior_vah: float | None = None,
    prior_val: float | None = None,
    prior_poc: float | None = None,
    overnight_high: float | None = None,
    overnight_low: float | None = None,
    tick_size: float = 0.25,
    stop_atr_multiple: float = 1.0,
) -> TradeLevels | None:
    """Return directionally-valid entry/stop/target suggestions, or None if insufficient levels.

    For a *long*: target is the nearest structural level **strictly above** last_price;
    stop is the nearest structural level **strictly below** last_price (floored by
    ``stop_atr_multiple`` × ATR).

    For a *short*: the mirror applies.

    Returns ``None`` when there are not enough levels to build a valid suggestion.
    """
    if side not in ("long", "short"):
        return None

    all_levels = _collect_levels(
        last_price,
        vwap,
        atr,
        atr_upper=atr_upper,
        atr_lower=atr_lower,
        orb_high=orb_high,
        orb_low=orb_low,
        prior_high=prior_high,
        prior_low=prior_low,
        prior_close=prior_close,
        prior_vah=prior_vah,
        prior_val=prior_val,
        prior_poc=prior_poc,
        overnight_high=overnight_high,
        overnight_low=overnight_low,
    )

    long_side = side == "long"
    price_above = [p for p in all_levels if p > last_price]
    price_below = [p for p in all_levels if p < last_price]

    if long_side:
        target_candidates = sorted(price_above)
        stop_candidates = sorted(price_below, reverse=True)
    else:
        target_candidates = sorted(price_below, reverse=True)
        stop_candidates = sorted(price_above)

    if not target_candidates or not stop_candidates:
        return None

    first_target = target_candidates[0]
    stop_level = stop_candidates[0]

    # Safety floor: stop must be at least stop_atr_multiple × ATR from entry.
    min_stop_distance = max(atr * stop_atr_multiple, 1.0)
    if long_side and (last_price - stop_level) < min_stop_distance:
        stop_level = round(last_price - min_stop_distance, 2)
    elif not long_side and (stop_level - last_price) < min_stop_distance:
        stop_level = round(last_price + min_stop_distance, 2)

    # Entry zone: tight range around last_price (one tick-size buffer).
    if long_side:
        entry_zone = f"{last_price:.2f}-{last_price + tick_size:.2f}"
    else:
        entry_zone = f"{last_price - tick_size:.2f}-{last_price:.2f}"

    return TradeLevels(
        entry_zone=entry_zone,
        stop_level=round(stop_level, 2),
        first_target=round(first_target, 2),
        level_labels=all_levels,
    )


def suggest_trade_levels_from_snapshot(side: str, snapshot, result) -> TradeLevels | None:
    """Convenience wrapper that pulls all fields from a MesSnapshot + ChecklistResult."""
    mes = snapshot.mes
    prior = snapshot.prior_mes
    overnight = snapshot.overnight_mes

    return suggest_trade_levels(
        side,
        mes.close,
        result.vwap,
        result.atr,
        atr_upper=result.upper_atr_band if result.upper_atr_band else None,
        atr_lower=result.lower_atr_band if result.lower_atr_band else None,
        orb_high=result.opening_range_high,
        orb_low=result.opening_range_low,
        prior_high=prior.high if prior else None,
        prior_low=prior.low if prior else None,
        prior_close=prior.close if prior else None,
        prior_vah=prior.vah if prior else None,
        prior_val=prior.val if prior else None,
        prior_poc=prior.poc if prior else None,
        overnight_high=overnight[0] if overnight else None,
        overnight_low=overnight[1] if overnight else None,
        tick_size=snapshot.config.mes_tick_size,
        stop_atr_multiple=snapshot.config.stop_atr_multiple,
    )


def render_trade_levels_hint(levels: TradeLevels) -> str:
    """Return the prompt snippet injected into the gatekeeper."""
    target_label = levels.level_labels.get(levels.first_target, "structural level")
    stop_label = levels.level_labels.get(levels.stop_level, "structural level")
    lines = [
        f"- Entry zone: {levels.entry_zone}",
        f"- Stop: {levels.stop_level} ({stop_label})",
        f"- First target: {levels.first_target} ({target_label})",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Post-generation normalizer
# ---------------------------------------------------------------------------


def _parse_entry_zone(entry_zone: str | None) -> tuple[float, float] | None:
    """Parse 'low-high' or 'high-low' strings → (low, high), or None on failure."""
    if not entry_zone:
        return None
    m = re.match(r"([\d.]+)[\s\-–—]+?([\d.]+)", entry_zone.strip())
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    return (min(a, b), max(a, b))


def normalize_trade_gonogo(verdict) -> None:
    """Enforce directional and verdict-type constraints on a :class:`TradeGoNoGo` in-place.

    Rules applied:

    1. Wait / Stand Down → clear all trade levels; set direction to 'none'.
    2. Take + long       → first_target must be above the entry zone; stop below.
    3. Take + short      → first_target must be below the entry zone; stop above.

    When a constraint is violated the offending field is cleared (set to None)
    and a one-line warning is appended to ``reasoning`` so the issue is visible
    without raising an exception or discarding the rest of the verdict.
    """
    from tradingagents.agents.schemas import TradeVerdict

    issues: list[str] = []

    if verdict.verdict != TradeVerdict.TAKE:
        # Wait / Stand Down — trade levels belong in what_would_change_my_mind.
        if verdict.direction != "none":
            verdict.direction = "none"  # type: ignore[assignment]
        if verdict.entry_zone is not None:
            verdict.entry_zone = None
            issues.append("entry_zone cleared (not a Take)")
        if verdict.stop_level is not None:
            verdict.stop_level = None
            issues.append("stop_level cleared (not a Take)")
        if verdict.first_target is not None:
            verdict.first_target = None
            issues.append("first_target cleared (not a Take)")
        if verdict.suggested_contracts is not None:
            verdict.suggested_contracts = None
        if issues:
            logger.warning("normalize_trade_gonogo: %s", "; ".join(issues))
        return

    # Take verdict — validate directional ordering.
    direction = verdict.direction
    entry_bounds = _parse_entry_zone(verdict.entry_zone)
    entry_low = entry_bounds[0] if entry_bounds else None
    entry_high = entry_bounds[1] if entry_bounds else None
    # Use mid-point when bounds are unparseable.
    entry_ref = ((entry_low or 0) + (entry_high or 0)) / 2 if entry_bounds else None

    if direction == "long":
        if verdict.first_target is not None and entry_high is not None:
            if verdict.first_target <= entry_high:
                issues.append(
                    f"first_target {verdict.first_target} ≤ entry high {entry_high} on long — cleared"
                )
                verdict.first_target = None
        if verdict.stop_level is not None and entry_low is not None:
            if verdict.stop_level >= entry_low:
                issues.append(
                    f"stop_level {verdict.stop_level} ≥ entry low {entry_low} on long — cleared"
                )
                verdict.stop_level = None

    elif direction == "short":
        if verdict.first_target is not None and entry_low is not None:
            if verdict.first_target >= entry_low:
                issues.append(
                    f"first_target {verdict.first_target} ≥ entry low {entry_low} on short — cleared"
                )
                verdict.first_target = None
        if verdict.stop_level is not None and entry_high is not None:
            if verdict.stop_level <= entry_high:
                issues.append(
                    f"stop_level {verdict.stop_level} ≤ entry high {entry_high} on short — cleared"
                )
                verdict.stop_level = None

    if issues:
        note = " [auto-corrected: " + "; ".join(issues) + "]"
        verdict.reasoning = verdict.reasoning.rstrip() + note
        logger.warning("normalize_trade_gonogo: %s", "; ".join(issues))
