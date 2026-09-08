"""Proximity radar for the MES gatekeeper pipeline.

Answers "how close am I to a valid trade?" without running the LLM.

:func:`build_proximity` takes an already-evaluated :class:`~.checklist.ChecklistResult`
and :class:`~.snapshot.MesSnapshot` and returns a :class:`ProximityReport` containing:

* A :class:`SetupState` enum that classifies the current moment into one of six
  mutually-exclusive states (from ``GATES_CLOSED`` to ``READY``).
* A sorted list of :class:`LevelDistance` entries — nearest structural levels
  above and below the current price, with signed points and a "cleared" flag.
* A list of failing check names that are the shortest path to ``tradeable=True``.
* A Boolean ``near_level`` that is True when the current price is within the
  configured proximity band of any structural level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .checklist import ChecklistResult
from .levels import _collect_levels
from .snapshot import MesSnapshot


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class SetupState(str, Enum):
    """Deterministic classification of the current setup moment."""

    GATES_CLOSED = "GATES_CLOSED"
    """Session or time gates block a trade (weekend, morning block, past last entry, low ATR, etc.)."""

    BLOCKED = "BLOCKED"
    """Gates are open but no-trade soft blocks are active (chop, TICK exhaustion, SPY < 3/5, …)."""

    AT_LEVEL_MISSING_CONFLUENCE = "AT_LEVEL_MISSING_CONFLUENCE"
    """Price is near a structural level but the checklist is not yet ``tradeable``.
    This is the "watch this level carefully" state."""

    CONFLUENCE_OK_WAITING_LOCATION = "CONFLUENCE_OK_WAITING_LOCATION"
    """Checklist passes (``tradeable=True``) but price is not near any structural level.
    Waiting for price to reach a level before entry — this is valid patience, not hesitation."""

    BUILDING = "BUILDING"
    """Neither close to a level nor tradeable. Signals are forming; no action needed."""

    READY = "READY"
    """``tradeable=True`` **and** price is within the proximity band of a structural level.
    Run ``mes check`` now for the gatekeeper Take / Wait judgement."""


@dataclass
class LevelDistance:
    """A single structural level with its signed distance from the current price."""

    price: float
    label: str
    distance: float
    """Signed: positive = level is *above* price; negative = level is *below* price."""
    within_band: bool = False
    """True when |distance| <= the configured proximity band."""

    @property
    def cleared(self) -> bool:
        """A level is 'cleared' if price has already passed through it.

        For longs, levels below price are behind (cleared).  For shorts, levels
        above.  Radar shows these but flags them so the user does not treat a
        prior-day high that is now *below* price as a valid target.
        """
        return self.distance < 0


@dataclass
class ProximityReport:
    """Full proximity picture for a single point in time."""

    # ---- Core status ----
    state: SetupState
    price: float
    side: str
    score: int
    max_score: int
    confirmations: int
    required: int
    tier: str
    tradeable: bool
    spy_confirmations: int
    spy_confluence_ok: bool
    gates_ok: bool

    # ---- Why not tradeable? ----
    gate_reasons: list[str] = field(default_factory=list)
    no_trade_reasons: list[str] = field(default_factory=list)
    missing_items: list[str] = field(default_factory=list)
    """Names of failing checklist items whose passage would most help reach ``tradeable``."""

    # ---- Level tape ----
    levels_above: list[LevelDistance] = field(default_factory=list)
    """Nearest structural levels above current price, nearest first (max 3)."""
    levels_below: list[LevelDistance] = field(default_factory=list)
    """Nearest structural levels below current price, nearest first (max 3)."""
    near_level: bool = False
    """True when at least one level is within the proximity band."""
    proximity_band: float = 4.0
    """Band width in points used to classify near_level / SetupState."""


# ---------------------------------------------------------------------------
# Level tape builder
# ---------------------------------------------------------------------------


def _level_distances(
    last_price: float,
    snapshot: MesSnapshot,
    result: ChecklistResult,
    proximity_band: float,
    max_per_side: int = 3,
) -> tuple[list[LevelDistance], list[LevelDistance], bool]:
    """Return (above, below, near_level) sorted by closeness."""
    mes = snapshot.mes
    prior = snapshot.prior_mes
    overnight = snapshot.overnight_mes

    all_levels = _collect_levels(
        last_price,
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
    )

    above: list[LevelDistance] = []
    below: list[LevelDistance] = []
    near = False

    for price, label in sorted(all_levels.items()):
        dist = round(price - last_price, 2)
        in_band = abs(dist) <= proximity_band
        if in_band:
            near = True
        ld = LevelDistance(price=price, label=label, distance=dist, within_band=in_band)
        if price > last_price:
            above.append(ld)
        elif price < last_price:
            below.append(ld)
        # Skip exact price matches (on the level)

    # Sort above by distance ascending (nearest first), below by |distance| ascending
    above.sort(key=lambda x: x.distance)
    below.sort(key=lambda x: -x.distance)  # distance is negative; -x gives most-negative last

    return above[:max_per_side], below[:max_per_side], near


# ---------------------------------------------------------------------------
# Missing-item extraction
# ---------------------------------------------------------------------------


def _missing_items(result: ChecklistResult) -> list[str]:
    """Return names of failing MES checks + SPY checks that block tradeable.

    Prioritise weighted MES items first (they affect score), then SPY.
    """
    failing: list[str] = []

    # MES items: failing and weight > 0 first, then failing and weight == 0
    weighted = [i for i in result.mes_items if not i.passed and i.weight > 0]
    unweighted = [i for i in result.mes_items if not i.passed and i.weight == 0]
    weighted.sort(key=lambda i: -i.weight)
    failing.extend(i.name for i in weighted)
    failing.extend(i.name for i in unweighted)

    # SPY items only when SPY confluence is already failing
    if not result.spy_confluence_ok:
        failing.extend(i.name for i in result.spy_items if not i.passed)

    return failing


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def _classify_state(
    result: ChecklistResult,
    near_level: bool,
) -> SetupState:
    if not result.gates_ok:
        return SetupState.GATES_CLOSED
    if result.no_trade_reasons:
        return SetupState.BLOCKED
    if result.tradeable and near_level:
        return SetupState.READY
    if result.tradeable:
        return SetupState.CONFLUENCE_OK_WAITING_LOCATION
    if near_level:
        return SetupState.AT_LEVEL_MISSING_CONFLUENCE
    return SetupState.BUILDING


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def build_proximity(
    snapshot: MesSnapshot,
    result: ChecklistResult,
    *,
    proximity_band: float | None = None,
    max_levels_per_side: int = 3,
) -> ProximityReport:
    """Build a :class:`ProximityReport` from an already-evaluated checklist.

    Parameters
    ----------
    snapshot:
        The point-in-time market picture (used to read structural levels).
    result:
        Output of :func:`~.checklist.evaluate`.
    proximity_band:
        Points within which a structural level is considered "in band".
        Defaults to ``min(4.0, 0.5 * atr)`` when ATR is available, else 4.0.
    max_levels_per_side:
        Maximum levels to include in the above/below lists (default 3).
    """
    last_price = result.last_price
    atr = result.atr

    if proximity_band is None:
        proximity_band = min(4.0, 0.5 * atr) if atr > 0 else 4.0

    above, below, near = _level_distances(
        last_price, snapshot, result, proximity_band, max_levels_per_side
    )
    missing = _missing_items(result)
    state = _classify_state(result, near)

    return ProximityReport(
        state=state,
        price=last_price,
        side=result.side,
        score=result.score,
        max_score=result.max_score,
        confirmations=result.confirmations,
        required=result.required,
        tier=result.tier,
        tradeable=result.tradeable,
        spy_confirmations=result.spy_confirmations,
        spy_confluence_ok=result.spy_confluence_ok,
        gates_ok=result.gates_ok,
        gate_reasons=result.gate_reasons,
        no_trade_reasons=result.no_trade_reasons,
        missing_items=missing,
        levels_above=above,
        levels_below=below,
        near_level=near,
        proximity_band=round(proximity_band, 2),
    )
