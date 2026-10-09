"""Standing-rule trigger engine for the MES radar/copilot.

Pure and LLM-free. A *standing rule* is a discipline prescription from the
session review ("log a check at the ORB-top retest"), serialized as a plain
dict (the JSON boundary for ``standing_rules.json`` and journal records).

:func:`evaluate_standing_rules` resolves each rule's watched level against the
current snapshot/checklist and returns the rules whose condition holds right
now. It is stateless: per-session fire dedup lives in the CLI tick
(:mod:`cli.mes`), fire/skip/flip accounting in :mod:`tradingagents.mes.journal`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .checklist import ChecklistResult
from .snapshot import MesSnapshot


@dataclass(frozen=True)
class RuleHit:
    """A standing rule whose trigger condition holds on this bar."""

    rule_id: str
    """Dedup key: level id for retests (e.g. ``orb_top``), ``inv_*`` for invalidation."""
    level: str
    """Human label, e.g. ``ORB high`` — shared with the radar level tape."""
    level_price: float
    distance: float
    """Signed points: ``price - level`` (positive = price above the level)."""
    tolerance: float
    """The rule's band, echoed in the banner (``±{tolerance:g} pts``)."""
    confirmation: str
    note: str
    """Coach's one-line reason, shown verbatim in the banner."""
    kind: str = "level_retest"
    """``level_retest`` or ``invalidation`` — drives banner copy and resolution."""


_LEVEL_RESOLVERS = {
    "vwap": lambda snapshot, result: result.vwap,
    "orb_top": lambda snapshot, result: snapshot.mes.opening_range_high,
    "orb_bottom": lambda snapshot, result: snapshot.mes.opening_range_low,
    "pdh": lambda s, r: s.prior_mes.high if s.prior_mes else None,
    "pdl": lambda s, r: s.prior_mes.low if s.prior_mes else None,
    "prior_close": lambda s, r: s.prior_mes.close if s.prior_mes else None,
    "prior_vah": lambda s, r: s.prior_mes.vah if s.prior_mes else None,
    "prior_val": lambda s, r: s.prior_mes.val if s.prior_mes else None,
    "prior_poc": lambda s, r: s.prior_mes.poc if s.prior_mes else None,
    "poc": lambda s, r: s.prior_mes.poc if s.prior_mes else None,
    "onh": lambda s, r: s.overnight_mes[0] if s.overnight_mes else None,
    "onl": lambda s, r: s.overnight_mes[1] if s.overnight_mes else None,
}


def add_vold_aligned(snapshot: MesSnapshot) -> bool:
    """True when $ADD and $VOLD are both available and agree on direction sign."""
    add, vold = snapshot.add, snapshot.vold
    if add is None or vold is None:
        return False
    return (add > 0) == (vold > 0)


def _confirmation_ok(confirmation: str, snapshot: MesSnapshot) -> bool:
    if confirmation == "none":
        return True
    if confirmation == "add_vold_aligned":
        return add_vold_aligned(snapshot)
    return False  # unknown confirmation strings never fire


def _level_retest_hit(
    *,
    trigger: dict,
    rule: dict,
    price: float,
    level_price: float,
    snapshot: MesSnapshot,
    describe_level,
) -> RuleHit | None:
    distance = round(price - level_price, 2)
    tolerance = float(trigger.get("tolerance_points") or 2.0)
    if abs(distance) > tolerance:
        return None
    confirmation = trigger.get("confirmation") or "none"
    if not _confirmation_ok(confirmation, snapshot):
        return None
    return RuleHit(
        rule_id=trigger["level"],
        level=describe_level(trigger["level"]),
        level_price=float(level_price),
        distance=distance,
        tolerance=tolerance,
        confirmation=confirmation,
        note=str(rule.get("note") or ""),
        kind="level_retest",
    )


def _invalidation_hit(
    *,
    trigger: dict,
    rule: dict,
    price: float,
    level_price: float,
    snapshot: MesSnapshot,
    describe_level,
) -> RuleHit | None:
    """Fire when price is on the invalidating side of the watched level.

    ``break_side`` ``below`` means the frame dies when price is at/under the
    level (within ``tolerance_points`` above still counts as broken through);
    ``above`` is the mirror. First-slice Option C — persistence predicates
    (consecutive closes / dwell) stay backlog.
    """
    distance = round(price - level_price, 2)
    tolerance = float(trigger.get("tolerance_points") or 2.0)
    break_side = str(trigger.get("break_side") or "below")
    if break_side == "below":
        # Price at or below level+tolerance has broken / is breaking down through.
        if price > level_price + tolerance:
            return None
    elif break_side == "above":
        if price < level_price - tolerance:
            return None
    else:
        return None
    confirmation = trigger.get("confirmation") or "none"
    if not _confirmation_ok(confirmation, snapshot):
        return None
    rule_id = str(trigger.get("id") or f"inv_{trigger.get('level', 'x')}")
    return RuleHit(
        rule_id=rule_id,
        level=describe_level(trigger["level"]),
        level_price=float(level_price),
        distance=distance,
        tolerance=tolerance,
        confirmation=confirmation,
        note=str(rule.get("note") or ""),
        kind="invalidation",
    )


def evaluate_standing_rules(
    snapshot: MesSnapshot,
    result: ChecklistResult,
    rules: list[dict],
) -> list["RuleHit"]:
    """Evaluate every standing rule against this bar; return the current hits.

    ``rules`` are plain dicts (the JSON form of :class:`StandingRule`). Rules
    with unknown trigger kinds or unresolvable levels are skipped silently —
    the schema admits future kinds, and a rule that cannot be evaluated must
    never nag. Exactly-at-level counts as a retest (|distance| = 0 <=
    tolerance); once-per-session dedup lives in the CLI tick.
    """
    from tradingagents.agents.schemas import describe_level  # lazy: levels.py precedent

    hits: list[RuleHit] = []
    price = result.last_price
    for rule in rules:
        trigger = rule.get("trigger") or {}
        kind = trigger.get("kind")
        if kind not in ("level_retest", "invalidation"):
            continue
        resolver = _LEVEL_RESOLVERS.get(trigger.get("level"))
        if resolver is None:
            continue
        level_price = resolver(snapshot, result)
        if not level_price or level_price <= 0:
            continue
        if kind == "level_retest":
            hit = _level_retest_hit(
                trigger=trigger, rule=rule, price=price, level_price=level_price,
                snapshot=snapshot, describe_level=describe_level,
            )
        else:
            hit = _invalidation_hit(
                trigger=trigger, rule=rule, price=price, level_price=level_price,
                snapshot=snapshot, describe_level=describe_level,
            )
        if hit is not None:
            hits.append(hit)
    return hits
