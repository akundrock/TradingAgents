# MES Standing Rules — Closing the Review→Session Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `mes review` prescribe machine-checkable standing rules (level-retest checks), surface a persistent "LOG A CHECK NOW" banner in the radar/copilot when a rule triggers intraday, and grade compliance (checked / skipped / missed) back into the next review.

**Architecture:** Three cooperating pieces, all LLM-free at tick time. (A) `SessionReview.standing_rules` — the review agent returns structured rules; `mes review` persists them to `standing_rules.json` beside the journal. (B) `tradingagents/mes/rules.py` — a pure trigger engine resolving each rule's level from the snapshot, firing a per-session-deduplicated banner into the radar/copilot panel. (C) `mes skip --reason` records a skipped check; unresolved fires tally as MISSED and feed back into `mes review`.

**Tech Stack:** Python 3.11, pydantic v2, typer + rich CLI, pytest (`@pytest.mark.unit`). No new dependencies.

**Spec:** this document — the approved brainstorm design is inlined below. Related: `plans/2026-09-22-mes-copilot.md`, `plans/2026-09-17-radar-auto-check.md`.

## Approved design (spec decisions, inlined from the brainstorm)

Verified gap: `SessionReview` output goes only to `TradingMemoryLog.append_imported_resolved_entry` (`cli/mes.py:536-546`); the journal never receives it. `one_improvement` (`schemas.py:693`) resurfaces only as fuzzy LLM prose via `_past_context()` (`cli/mes.py:153-158`). Nothing in the copilot tick (`cli/mes.py:821-918`) knows what the review prescribed, and no review record type exists in `MesJournal`.

Approved composite (user sign-off):
- **A (architecture):** structured standing rules on `SessionReview`; persisted as `standing_rules.json` in the journal dir; evaluated per tick.
- **B (trigger):** `level_retest` is the only initial rule kind; levels ORB top/bottom, VWAP, PDH/PDL, prior close, prior VAH/VAL/POC, overnight high/low; optional `add_vold_aligned` confirmation; banner is a persistent panel row (not a modal), bell only with `--alert`.
- **C (enforcement):** `mes skip --reason` writes a skipped-check record; triggered-but-ignored fires tally as MISSED in the copilot panel and in `mes review`.

Out of scope (future work, not tasks): threading rules into the gatekeeper prompt; rule kinds beyond `level_retest`; same-day re-firing of a level.

## Global Constraints

- Pydantic v2 (`BaseModel`, `Field`, `Literal`), typer CLI conventions in `cli/mes.py`.
- Journal stays append-only JSONL; the only mutable file is `standing_rules.json`.
- Import direction: `mes/rules.py` imports `agents.schemas` (mes→agents edge already precedented by `mes/levels.py:251`'s lazy import); `schemas.py` imports `describe_standing_rule` lazily inside the renderer to avoid import cycles. Never the reverse.
- All tests `@pytest.mark.unit`; run from `/Users/akundrock/sandbox/TradingAgents`.
- Branch `feat/mes-copilot` already checked out; `tradingagents/mes/config.py` has unrelated local modifications — never stage or commit that file.
- Commit style: short imperative subjects (`feat:`, `test:`), matching `git log --oneline -5`.

## Data contracts (single source of truth)

Standing-rule dict (the boundary form everywhere: JSON file, journal records, trigger engine):

```json
{
  "trigger": {"kind": "level_retest", "level": "orb_top", "confirmation": "none", "tolerance_points": 2.0},
  "requirement": "log_check_or_skip",
  "note": "Fade the first ORB-top retest.",
  "expires_on": "2026-03-31"
}
```

Level ids (`RuleLevel` enum): `vwap`, `orb_top`, `orb_bottom`, `pdh`, `pdl`, `prior_close`, `prior_vah`, `prior_val`, `prior_poc`, `onh`, `onl` (11 total, one resolver each).

`standing_rules.json` format:

```json
{
  "version": 1,
  "reviewed_on": "2026-03-30",
  "rules": [{"trigger": {"kind": "level_retest", "level": "orb_top", "confirmation": "none", "tolerance_points": 2.0}, "requirement": "log_check_or_skip", "note": "...", "expires_on": "2026-03-31"}]
}
```

Journal record kinds added (one JSON line each, like existing `check`/`hypothesis` records):

```json
{"kind": "rule_fired", "logged_at": "2026-03-31T10:35:00", "as_of": "2026-03-31T10:35:00", "session_date": "2026-03-31", "rule_id": "orb_top", "level": "ORB top", "confirmation": "none", "tolerance": 2.0, "last_price": 7675.25, "distance": 1.25}
{"kind": "rule_skip", "logged_at": "2026-03-31T10:38:00", "as_of": "2026-03-31T10:38:00", "session_date": "2026-03-31", "rule_id": "orb_top", "level": "ORB high", "reason": "internals diverging", "last_price": 7675.5}
```

Semantics: `rule_id` = trigger level id; **one fire per rule per session** (per-session dedup). A fire is *resolved* when any later `check` record, or a same-`rule_id` `rule_skip` record, exists with `as_of >= fire.as_of`. Fires unresolved at session end are **missed**.

## File structure

| File | Change | Responsibility |
| --- | --- | --- |
| `tradingagents/agents/schemas.py` | Modify | `RuleLevel`, `Confirmation`, `LevelRetestTrigger`, `StandingRule`, `SessionReview.standing_rules`, `describe_level`/`describe_standing_rule`, renderer |
| `tradingagents/mes/rules.py` | Create | Pure trigger engine: level resolvers, `RuleHit`, `evaluate_standing_rules`, `add_vold_aligned` |
| `tradingagents/mes/journal.py` | Modify | rules-file persistence, `rule_fired`/`rule_skip` records, fire accounting, compliance summary |
| `tradingagents/agents/utils/structured.py` | Modify | optional `on_model` callback on structured invoke |
| `tradingagents/agents/mes/review_agent.py` | Modify | standing-rules prompt section + `on_review` capture hook |
| `cli/mes.py` | Modify | banner rendering, tick integration, `mes skip` command, review wiring |
| `tests/test_mes_rules.py`, `tests/test_mes_agents.py`, `tests/test_mes_journal.py`, `tests/test_mes_cli_copilot.py`, `tests/test_mes_cli_skip.py` | Test | per task |

---
### Task 1: Schema — `StandingRule`, `SessionReview.standing_rules`, renderers

**Files:**
- Modify: `tradingagents/agents/schemas.py` (new models inserted directly above `class SessionReview`, currently at line 657; renderer replaced at lines 712-725)
- Test: `tests/test_mes_agents.py` (append; `FakeLLM` at line 34 and `_review()` at line 91 already exist here)

**Interfaces:**
- Consumes: nothing new (pydantic v2 already used throughout `schemas.py`).
- Produces (consumed by Tasks 2, 3, 4, 5, 6): `RuleLevel` and `Confirmation` (str enums); `StandingRule` with `trigger: LevelRetestTrigger`, `requirement: Literal["log_check_or_skip"] = "log_check_or_skip"`, `note: str = ""`, `expires_on: str | None = None`; `LevelRetestTrigger` with `kind: Literal["level_retest"] = "level_retest"`, `level: RuleLevel`, `confirmation: Confirmation = Confirmation.NONE`, `tolerance_points: float = 2.0` (ge=0.25, le=10.0); `describe_level(level) -> str`; `describe_standing_rule(rule) -> str`; `SessionReview.standing_rules: list[StandingRule]` (default empty).

- [ ] **Step 1: Write the failing tests**

In `tests/test_mes_agents.py`: add `from pydantic import ValidationError` to the imports, extend the `from tradingagents.agents.schemas import (...)` block (line ~9-16) to also import `Confirmation, RuleLevel, StandingRule, render_session_review`, then append after `test_review_agent_falls_back_to_free_text` (line ~385):

```python
# ---------------------------------------------------------------------------
# Standing rules (review -> session loop)
# ---------------------------------------------------------------------------


def _rule(**overrides) -> StandingRule:
    trigger = {"kind": "level_retest", "level": "orb_top",
               "confirmation": "none", "tolerance_points": 2.0}
    payload = {"trigger": trigger, "requirement": "log_check_or_skip",
               "note": "Fade the first ORB-top retest.", "expires_on": "2026-03-31"}
    payload.update(overrides)
    return StandingRule(**payload)


@pytest.mark.unit
def test_standing_rule_defaults():
    rule = StandingRule(trigger={"kind": "level_retest", "level": "vwap"})
    assert rule.trigger.level is RuleLevel.VWAP
    assert rule.trigger.confirmation is Confirmation.NONE
    assert rule.trigger.tolerance_points == 2.0
    assert rule.requirement == "log_check_or_skip"
    assert rule.note == ""
    assert rule.expires_on is None


@pytest.mark.unit
def test_standing_rule_rejects_unknown_level():
    with pytest.raises(ValidationError):
        StandingRule(trigger={"kind": "level_retest", "level": "round_100"})


@pytest.mark.unit
def test_session_review_standing_rules_default_empty():
    assert _review().standing_rules == []


@pytest.mark.unit
def test_render_session_review_lists_standing_rules():
    review = _review()
    review.standing_rules = [_rule()]
    output = render_session_review(review)
    assert "**Standing Rules for the next session**:" in output
    assert "ORB high retest" in output
    assert "Fade the first ORB-top retest." in output


@pytest.mark.unit
def test_render_session_review_omits_rules_when_empty():
    assert "Standing Rules" not in render_session_review(_review())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_agents.py -k standing -v`
Expected: FAIL — `ImportError: cannot import name 'Confirmation'` (models do not exist yet).

- [ ] **Step 3: Implement the schema**

Insert above `class SessionReview:` in `tradingagents/agents/schemas.py`:

```python
class RuleLevel(str, Enum):
    """Structural levels a standing rule can watch (resolved in mes/rules.py)."""

    VWAP = "vwap"
    ORB_TOP = "orb_top"
    ORB_BOTTOM = "orb_bottom"
    PDH = "pdh"
    PDL = "pdl"
    PRIOR_CLOSE = "prior_close"
    PRIOR_VAH = "prior_vah"
    PRIOR_VAL = "prior_val"
    PRIOR_POC = "prior_poc"
    ONH = "onh"
    ONL = "onl"


class Confirmation(str, Enum):
    """Optional extra condition required before a rule trigger fires."""

    NONE = "none"
    ADD_VOLD_ALIGNED = "add_vold_aligned"


class LevelRetestTrigger(BaseModel):
    """Day-one rule trigger: a retest of one structural level.

    `kind` is a Literal so later trigger kinds (time-of-day, internals flips)
    can be added without a schema migration; the trigger engine simply ignores
    kinds it does not implement.
    """

    kind: Literal["level_retest"] = "level_retest"
    level: RuleLevel = Field(description="Which structural level to watch.")
    confirmation: Confirmation = Field(
        default=Confirmation.NONE,
        description=(
            "Optional condition required at the touch: 'add_vold_aligned' fires "
            "only when $ADD and $VOLD agree on direction; 'none' fires on the "
            "retest itself."
        ),
    )
    tolerance_points: float = Field(
        default=2.0, ge=0.25, le=10.0,
        description="Price distance from the level that counts as a retest.",
    )


class StandingRule(BaseModel):
    """A machine-checkable discipline prescription for the next session.

    `requirement` pins the observable: when the trigger fires, the trader must
    either log a gatekeeper check or acknowledge with `mes skip --reason`.
    """

    trigger: LevelRetestTrigger
    requirement: Literal["log_check_or_skip"] = "log_check_or_skip"
    note: str = Field(default="", description="One-line 'why' shown in the radar banner.")
    expires_on: str | None = Field(
        default=None,
        description="ISO date after which the rule no longer fires; None = end of the next session.",
    )
```

Add to `SessionReview` (after the `one_improvement` field, line ~699):

```python
    standing_rules: list[StandingRule] = Field(
        default_factory=list,
        description=(
            "Zero to three machine-checkable rules for the NEXT session, "
            "prescribed from this review's discipline findings. Only chart-"
            "watchable level retests qualify (kind=level_retest); each note is "
            "one concrete sentence. Omit entirely when the improvement is not "
            "chart-watchable."
        ),
    )
```

Add helpers below the `SessionReview` class (before `render_session_review`):

```python
_LEVEL_LABELS: dict[RuleLevel, str] = {
    RuleLevel.VWAP: "VWAP",
    RuleLevel.ORB_TOP: "ORB high",
    RuleLevel.ORB_BOTTOM: "ORB low",
    RuleLevel.PDH: "prior-day high",
    RuleLevel.PDL: "prior-day low",
    RuleLevel.PRIOR_CLOSE: "prior-day close",
    RuleLevel.PRIOR_VAH: "prior VAH",
    RuleLevel.PRIOR_VAL: "prior VAL",
    RuleLevel.PRIOR_POC: "prior POC",
    RuleLevel.ONH: "overnight high",
    RuleLevel.ONL: "overnight low",
}


def describe_level(level: RuleLevel | str) -> str:
    """Human label for a rule level, shared with the radar banner."""
    return _LEVEL_LABELS.get(RuleLevel(level), str(level))


def describe_standing_rule(rule: StandingRule) -> str:
    """One-line human description used by panels, banners, and prompts."""
    confirmation = ""
    if rule.trigger.confirmation is Confirmation.ADD_VOLD_ALIGNED:
        confirmation = " with $ADD/$VOLD aligned"
    note = f" — {rule.note}" if rule.note else ""
    return (
        f"{describe_level(rule.trigger.level)} retest "
        f"(±{rule.trigger.tolerance_points:g} pts{confirmation}) "
        f"→ run `mes check` or `mes skip`{note}"
    )
```

Replace `render_session_review` (lines 712-725) — body only; call the local helper directly (no import; it is defined above in this module):

```python
def render_session_review(review: SessionReview) -> str:
    """Render a SessionReview to the markdown the CLI displays and logs."""
    parts = [
        f"**Hypothesis Grade**: {review.hypothesis_grade.value}",
        f"**Discipline Grade**: {review.discipline_grade}",
        "",
        f"**What Worked**: {review.what_worked}",
        "",
        f"**What Failed**: {review.what_failed}",
        "",
        f"**One Improvement**: {review.one_improvement}",
        "",
    ]
    if review.standing_rules:
        parts.append("**Standing Rules for the next session**:")
        parts.append("")
        parts.extend(
            f"{index}. {describe_standing_rule(rule)}"
            for index, rule in enumerate(review.standing_rules, start=1)
        )
        parts.append("")
    parts.append(review.narrative)
    return "\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_agents.py -v`
Expected: PASS — 5 new tests plus every existing agent test.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/agents/schemas.py tests/test_mes_agents.py
git commit -m "feat: add StandingRule schema and SessionReview.standing_rules"
```

### Task 2: Trigger engine — `tradingagents/mes/rules.py` (pure, stateless, LLM-free)

**Files:**
- Create: `tradingagents/mes/rules.py`
- Test: `tests/test_mes_rules.py` (new file)

**Interfaces:**
- Consumes: `describe_level` from Task 1 (`tradingagents.agents.schemas`); `MesSnapshot` (`tradingagents/mes/snapshot.py`): `mes.close`, `mes.opening_range_high/low`, `prior_mes.high/.low/.close/.vah/.val/.poc`, `overnight_mes`, `add`, `vold`; `ChecklistResult` from `tradingagents/mes/checklist.py`: `last_price`, `vwap`.
- Produces (consumed by Tasks 4, 5): `RuleHit` dataclass with fields `rule_id: str`, `level: str` (human label), `level_price: float`, `distance: float` (signed, `price - level`), `tolerance: float`, `confirmation: str`, `note: str`; `evaluate_standing_rules(snapshot, result, rules: list[dict]) -> list[RuleHit]` (rules are plain dicts — the JSON boundary); `add_vold_aligned(snapshot) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mes_rules.py`:

```python
"""Tests for the standing-rule trigger engine."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradingagents.mes.checklist import evaluate
from tradingagents.mes.rules import evaluate_standing_rules
from tests.mes_factories import (
    DEFAULT_AS_OF,
    make_mes_series,
    make_snapshot,
    make_spy_series,
)


def _rule_dict(**trigger_overrides) -> dict:
    """JSON-dict form of a StandingRule — the boundary the engine consumes."""
    trigger = {"kind": "level_retest", "level": "orb_top",
               "confirmation": "none", "tolerance_points": 2.0}
    trigger.update(trigger_overrides)
    return {"trigger": trigger, "requirement": "log_check_or_skip",
            "note": "Fade the first ORB-top retest.", "expires_on": None}


def _snapshot(price=100.0, *, prior=None, overnight=None, internals=None):
    """Factory snapshot: vwap 99.0, ORB 98/101, add=+300, vold=+1000 (aligned)."""
    snap = make_snapshot(
        mes=make_mes_series(close=price),
        spy=make_spy_series(internals=internals),
        as_of=DEFAULT_AS_OF,
    )
    if prior is not None:
        snap.prior_mes = SimpleNamespace(**prior)
    if overnight is not None:
        snap.overnight_mes = overnight
    return snap


def _evaluate(snapshot):
    return evaluate(snapshot, "auto")
```

Factory defaults (from `tests/mes_factories.py`): MES vwap=99.0, atr=1.0, `opening_range_high=101.0`, `opening_range_low=98.0`; SPY internals default `[(300.0, 100.0, 1000.0)] * 6` so `add > 0 and vold > 0` (aligned). The prior/overnight levels default to `None` in `make_snapshot`, so pdh/onh rules resolve only when the test injects them.

Test bodies (same file):

```python
@pytest.mark.unit
def test_orb_top_rule_fires_within_tolerance():
    snapshot = _snapshot(100.5)
    hits = evaluate_standing_rules(snapshot, _evaluate(snapshot), [_rule_dict()])
    assert [hit.rule_id for hit in hits] == ["orb_top"]
    assert hits[0].level == "ORB high"
    assert hits[0].level_price == 101.0
    assert hits[0].distance == pytest.approx(-1.0)
    assert hits[0].note == "Fade the first ORB-top retest."


@pytest.mark.unit
def test_outside_tolerance_does_not_fire():
    snapshot = _snapshot(104.0)
    assert evaluate_standing_rules(snapshot, _evaluate(snapshot), [_rule_dict()]) == []


@pytest.mark.unit
def test_unavailable_level_never_fires():
    snapshot = _snapshot(100.0)  # no prior/overnight levels in factory snapshot
    assert evaluate_standing_rules(snapshot, _evaluate(snapshot),
                                   [_rule_dict(level="pdh", tolerance_points=50.0)]) == []


@pytest.mark.unit
def test_unknown_trigger_kind_is_skipped():
    snapshot = _snapshot(100.0)
    rules = [{"trigger": {"kind": "time_of_day", "level": "vwap"}}]
    assert evaluate_standing_rules(snapshot, _evaluate(snapshot), rules) == []


@pytest.mark.unit
def test_prior_and_overnight_levels_resolve():
    prior = {"high": 102.0, "low": 95.0, "close": 99.0,
             "vah": 101.5, "val": 96.5, "poc": 100.0}
    snapshot = _snapshot(100.0, prior=prior, overnight=(103.0, 94.0))
    result = _evaluate(snapshot)
    assert evaluate_standing_rules(snapshot, result, [_rule_dict(level="pdh")])[0].level_price == 102.0
    assert evaluate_standing_rules(snapshot, result, [_rule_dict(level="pdl")])[0].level_price == 95.0
    assert evaluate_standing_rules(snapshot, result, [_rule_dict(level="onh")])[0].level_price == 103.0


@pytest.mark.unit
def test_add_vold_aligned_confirmation_blocks_on_disagreement():
    snapshot = _snapshot(100.0, internals=[(300.0, 100.0, -1000.0)] * 6)
    assert evaluate_standing_rules(
        snapshot, _evaluate(snapshot), [_rule_dict(confirmation="add_vold_aligned")]) == []
    aligned = _snapshot(100.0, internals=[(300.0, 100.0, 1000.0)] * 6)
    hits = evaluate_standing_rules(aligned, _evaluate(aligned),
                                   [_rule_dict(confirmation="add_vold_aligned")])
    assert hits and hits[0].confirmation == "add_vold_aligned"


@pytest.mark.unit
def test_engine_is_stateless_same_input_same_hits():
    snapshot = _snapshot(101.0)  # exactly at the ORB high
    result = _evaluate(snapshot)
    first = evaluate_standing_rules(snapshot, result, [_rule_dict()])
    second = evaluate_standing_rules(snapshot, result, [_rule_dict()])
    assert [h.level_price for h in first] == [h.level_price for h in second]
```

Note the exact-at-level case (price 101.0 = ORB high): `|distance| = 0 <= tolerance`, so the hit fires — intentionally forgiving; the per-session dedup in Task 4 prevents noise.

Add one more test — VWAP resolves from the checklist result, not the snapshot:

```python
@pytest.mark.unit
def test_vwap_rule_uses_checklist_vwap():
    snapshot = _snapshot(100.0)  # factory vwap = 99.0
    hits = evaluate_standing_rules(snapshot, _evaluate(snapshot), [_rule_dict(level="vwap")])
    assert [hit.rule_id for hit in hits] == ["vwap"]
    assert hits[0].level == "VWAP"
    assert hits[0].level_price == pytest.approx(99.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_rules.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingagents.mes.rules'`.

- [ ] **Step 3: Implement the engine**

Create `tradingagents/mes/rules.py`:

```python
"""Standing-rule trigger engine for the MES radar/copilot.

Pure and LLM-free. A *standing rule* is a discipline prescription from the
session review ("log a check at the ORB-top retest"), serialized as a plain
dict (the JSON boundary for ``standing_rules.json`` and journal records).

:func:`evaluate_standing_rules` resolves each rule's watched level against the
current snapshot/checklist and returns the rules whose retest condition holds
right now. It is stateless: per-session fire dedup and banner rendering live in
the CLI (:mod:`cli.mes`), fire/skip accounting in :mod:`tradingagents.mes.journal`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .checklist import ChecklistResult
from .snapshot import MesSnapshot


@dataclass(frozen=True)
class RuleHit:
    """A standing rule whose retest condition holds on this bar."""

    rule_id: str
    """Trigger level id, e.g. ``orb_top`` (also the per-session dedup key)."""
    level: str
    """Human label, e.g. ``ORB high`` — shared with the radar level tape."""
    level_price: float
    distance: float
    """Signed points: ``price - level`` (positive = price above the level)."""
    tolerance: float
    """The rule's retest band, echoed in the banner (``±{tolerance:g} pts``)."""
    confirmation: str
    note: str
    """Coach's one-line reason, shown verbatim in the banner."""


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


def evaluate_standing_rules(
    snapshot: MesSnapshot,
    result: ChecklistResult,
    rules: list[dict],
) -> list["RuleHit"]:
    """Evaluate every standing rule against this bar; return the current hits.

    ``rules`` are plain dicts (the JSON form of :class:`StandingRule`). Rules
    with unknown trigger kinds or confirmations are skipped silently — the
    schema admits future kinds, and a rule that cannot be evaluated must never
    nag. Exactly-at-level counts as a retest (|distance| = 0 <= tolerance);
    once-per-session dedup lives in the CLI tick.
    """
    from tradingagents.agents.schemas import describe_level  # lazy: levels.py:251 precedent

    hits: list[RuleHit] = []
    price = result.last_price
    for rule in rules:
        trigger = rule.get("trigger") or {}
        if trigger.get("kind") != "level_retest":
            continue
        resolver = _LEVEL_RESOLVERS.get(trigger.get("level"))
        if resolver is None:
            continue
        level_price = resolver(snapshot, result)
        if not level_price or level_price <= 0:
            continue
        distance = round(price - level_price, 2)
        tolerance = float(trigger.get("tolerance_points") or 2.0)
        if abs(distance) > tolerance:
            continue
        confirmation = trigger.get("confirmation") or "none"
        if not _confirmation_ok(confirmation, snapshot):
            continue
        hits.append(RuleHit(
            rule_id=trigger["level"],
            level=describe_level(trigger["level"]),
            level_price=float(level_price),
            distance=distance,
            tolerance=tolerance,
            confirmation=confirmation,
            note=str(rule.get("note") or ""),
        ))
    return hits
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_rules.py -v`
Expected: PASS — 7 tests.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/mes/rules.py tests/test_mes_rules.py
git commit -m "feat: add standing-rule level_retest trigger engine"
```

### Task 3: Journal — standing-rules persistence, fire/skip records, compliance tally

**Files:**
- Modify: `tradingagents/mes/journal.py` (new methods after `save_hypothesis`, line 135; new module helper above `MesJournal`)
- Test: `tests/test_mes_journal.py` (append; the `journal` fixture at line 16-18 already exists)

**Interfaces:**
- Consumes: `MesJournal.__init__` (`self._dir`), `load_day`, `load_checks` (existing).
- Produces (consumed by Tasks 4, 5, 6):
  - `save_standing_rules(rules: list[dict], *, reviewed_on: str) -> None` — atomic JSON write to `<journal_dir>/standing_rules.json` (`{"version": 1, "reviewed_on": ..., "rules": [...]}`); a later save fully supersedes the previous one. Expiry defaults are applied by the caller (Task 6 sets them from the review date).
  - `active_standing_rules(session_date: str) -> list[dict]` — rules whose `expires_on` is `None` or `>= session_date`; malformed file reads as no rules.
  - `append_rule_fired(date, *, rule_id, level, confirmation, tolerance, last_price, distance) -> None` — appends kind `rule_fired`.
  - `append_rule_skip(date, *, rule_id, level, reason, last_price=None, as_of=None) -> None` — appends `kind: "rule_skip"`.
  - `load_rule_fires(date) -> list[dict]`, `load_rule_skips(date) -> list[dict]`.
  - `rule_compliance(date, *, resolve_as_of: datetime | None = None) -> dict` — `{"triggered": int, "checked": int, "skipped": int, "missed": int, "open": list[dict], "detail": list[dict]}`; a fire is `checked` if any `check` record has `as_of >= fire.as_of`, `skipped` if a same-`rule_id` `rule_skip` has `as_of >= fire.as_of`, `missed` when `resolve_as_of - fire.as_of > 10 minutes`, else `open`.
  - `summarize_rule_compliance(date, *, resolve_as_of=None) -> str` — markdown tally table for panels/review.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mes_journal.py`:

```python
# ---------------------------------------------------------------------------
# Standing rules (review -> session loop)
# ---------------------------------------------------------------------------


def _rule_dict(level="orb_top", tolerance=2.0, expires_on=None, note=""):
    return {
        "trigger": {"kind": "level_retest", "level": level,
                    "confirmation": "none", "tolerance_points": tolerance},
        "requirement": "log_check_or_skip",
        "note": note,
        "expires_on": expires_on,
    }


@pytest.mark.unit
def test_save_then_load_standing_rules_round_trip(journal):
    journal.save_standing_rules([_rule_dict()], reviewed_on="2026-03-30")
    active = journal.active_standing_rules("2026-03-31")
    assert [r["trigger"]["level"] for r in active] == ["orb_top"]
    assert active[0]["requirement"] == "log_check_or_skip"


@pytest.mark.unit
def test_expired_rules_are_filtered(journal):
    journal.save_standing_rules([
        _rule_dict(level="vwap", expires_on="2026-03-30"),   # expired by 03-31
        _rule_dict(level="orb_top", expires_on="2026-03-31"),
        _rule_dict(level="pdh", expires_on=None),
    ])
    active = journal.active_standing_rules("2026-03-31")
    assert [r["trigger"]["level"] for r in active] == ["orb_top", "pdh"]


@pytest.mark.unit
def test_new_review_supersedes_previous_rules(journal):
    journal.save_standing_rules([_rule_dict(level="vwap")], reviewed_on="2026-03-30")
    journal.save_standing_rules([_rule_dict(level="orb_top")], reviewed_on="2026-03-31")
    active = journal.active_standing_rules("2026-03-31")
    assert [r["trigger"]["level"] for r in active] == ["orb_top"]


@pytest.mark.unit
def test_malformed_rules_file_reads_as_empty(journal):
    journal.directory.mkdir(parents=True, exist_ok=True)
    (journal.directory / "standing_rules.json").write_text("{not json", encoding="utf-8")
    assert journal.active_standing_rules("2026-03-31") == []


@pytest.mark.unit
def test_no_rules_file_reads_as_empty(journal):
    assert journal.active_standing_rules("2026-03-31") == []


@pytest.mark.unit
def test_rule_fired_round_trip(journal):
    stamp = _dt(2026, 3, 30, 10, 35)
    journal.append_rule_fired(
        "2026-03-30", rule_id="orb_top", level="ORB high", confirmation="none",
        tolerance=2.0, last_price=101.25, distance=0.25, as_of=stamp,
    )
    fires = journal.load_rule_fires("2026-03-30")
    assert len(fires) == 1
    assert fires[0]["kind"] == "rule_fired"
    assert fires[0]["rule_id"] == "orb_top"
    assert fires[0]["distance"] == 0.25


@pytest.mark.unit
def test_rule_skip_round_trip(journal):
    journal.append_rule_skip(
        "2026-03-30", rule_id="orb_top", level="ORB high",
        reason="internals diverging", last_price=101.5,
        as_of=_dt(2026, 3, 30, 10, 38),
    )
    skips = journal.load_rule_skips("2026-03-30")
    assert len(skips) == 1
    assert skips[0]["reason"] == "internals diverging"
```

Compliance accounting tests (same file — the core of part C):

```python
def _fire(date, rule_id, hour, minute):
    stamp = _dt(2026, 3, 30, hour, minute)
    journal.append_rule_fired(
        date, rule_id=rule_id, level=rule_id, confirmation="none",
        tolerance=2.0, last_price=100.0, distance=0.0, as_of=stamp,
    )
    return stamp


@pytest.mark.unit
def test_compliance_counts_check_and_skip(journal):
    _fire("2026-03-30", "orb_top", 10, 0)
    journal.append_rule_skip(
        "2026-03-30", rule_id="orb_top", level="ORB high", reason="chop",
        as_of=_dt(2026, 3, 30, 10, 40),
    )
    _fire("2026-03-30", "vwap", 13, 0)
    snapshot = make_snapshot(as_of=_dt(2026, 3, 30, 13, 5))
    journal.append_check(snapshot=snapshot, result=evaluate(snapshot, "auto"))
    tally = journal.rule_compliance("2026-03-30")
    assert tally["triggered"] == 2
    assert tally["skipped"] == 1
    assert tally["checked"] == 1
    assert tally["missed"] == 0


@pytest.mark.unit
def test_unresolved_fire_is_open_then_missed_after_grace(journal):
    _fire("2026-03-30", "orb_top", 10, 35)
    tally = journal.rule_compliance("2026-03-30")   # no resolve time yet
    assert tally["triggered"] == 1 and tally["open"] and not tally["missed"]
    late = journal.rule_compliance(
        "2026-03-30", resolve_as_of=_dt(2026, 3, 30, 10, 46)  # > 10 min later
    )
    assert late["missed"] == 1 and late["open"] == []


@pytest.mark.unit
def test_check_before_fire_does_not_resolve_it(journal):
    earlier = _dt(2026, 3, 30, 10, 30)
    snapshot = make_snapshot(as_of=earlier)
    journal.append_check(snapshot=snapshot, result=evaluate(snapshot, "auto"))
    _fire("2026-03-30", "orb_top", 10, 35)
    late = journal.rule_compliance("2026-03-30", resolve_as_of=_dt(2026, 3, 30, 11, 0))
    # The 10:30 check predates the 10:35 fire; it cannot honor it.
    assert late["triggered"] == 1 and late["missed"] == 1


@pytest.mark.unit
def test_skip_by_other_rule_does_not_resolve(journal):
    _fire("2026-03-30", "orb_top", 10, 35)
    journal.append_rule_skip(
        "2026-03-30", rule_id="vwap", level="VWAP", reason="different level",
        as_of=_dt(2026, 3, 30, 10, 40),
    )
    late = journal.rule_compliance("2026-03-30", resolve_as_of=_dt(2026, 3, 30, 11, 0))
    assert late["missed"] == 1  # a skip for another rule does not cover this fire


@pytest.mark.unit
def test_summarize_rule_compliance_lines(journal):
    _fire("2026-03-30", "orb_top", 10, 35)
    journal.append_rule_skip("2026-03-30", rule_id="orb_top", level="ORB high",
                             reason="chop", as_of=_dt(2026, 3, 30, 10, 38))
    summary = journal.summarize_rule_compliance("2026-03-30")
    assert "orb_top" in summary
    assert "skipped" in summary
```

Note: `test_compliance_*` need a real check record for the `checked` path —
`journal.append_check(snapshot=..., result=evaluate(snapshot, "auto"))` with
`make_snapshot` from the factories is the established pattern in this file
(see `test_checks_are_appended_in_order`, test_mes_journal.py:65).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_journal.py -k "standing or rule or compliance" -v`
Expected: FAIL — `AttributeError: 'MesJournal' object has no attribute 'save_standing_rules'`.

- [ ] **Step 3: Implement the journal methods**

In `tradingagents/mes/journal.py`, extend the datetime import to
`from datetime import datetime, timedelta`, then add two module-level helpers
after `_first_line` (line ~38):

```python
_MISSED_GRACE_SECONDS = 600.0
"""A fire stays 'open' for its trigger bar (5m) plus one bar of grace."""


def _iso(value: Any) -> datetime | None:
    """Parse an ISO timestamp from a journal record; None when absent/mangled."""
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _resolve_fires(
    fires: list[dict],
    checks: list[dict],
    skips: list[dict],
    *,
    resolve_as_of: datetime | None = None,
) -> list[dict]:
    """Return each fire annotated with an outcome: checked / skipped / missed / open.

    A fire resolves when any later ``check`` record (any check counts — the
    trader ran the gatekeeper) or a same-``rule_id`` ``rule_skip`` lands at or
    after the fire's ``as_of``. Fires still unresolved at ``resolve_as_of``
    count as missed; without ``resolve_as_of`` (mid-session) they stay open so
    the copilot banner can keep prompting.
    """
    check_times = [t for t in (_iso(record.get("as_of")) for record in checks) if t]
    skips_by_rule: dict[str, list[datetime]] = {}
    for skip in skips:
        skip_time = _iso(skip.get("as_of"))
        if skip_time is not None:
            skips_by_rule.setdefault(str(skip.get("rule_id")), []).append(skip_time)

    resolved: list[dict] = []
    for fire in fires:
        fired_at = _iso(fire.get("as_of"))
        outcome = "open"
        if fired_at is not None:
            if any(t >= fired_at for t in check_times):
                outcome = "checked"
            elif any(t >= fired_at for t in skips_by_rule.get(str(fire.get("rule_id")), [])):
                outcome = "skipped"
            elif resolve_as_of is not None and (
                (resolve_as_of - fired_at).total_seconds() > _MISSED_GRACE_SECONDS
            ):
                outcome = "missed"
        resolved.append({**fire, "outcome": outcome})
    return resolved
```

Add the write-path methods after `save_hypothesis` (journal.py, before the
`# --- Read path ---` comment):

```python
    # --- Standing rules (review -> session loop) ---

    _RULES_FILE = "standing_rules.json"

    def save_standing_rules(self, rules: list[dict], *, reviewed_on: str) -> None:
        """Persist the latest review's standing rules, superseding earlier sets.

        Atomic write (tmp file + rename) so a mid-write crash cannot leave a
        truncated rule set behind.
        """
        payload = {"version": 1, "reviewed_on": reviewed_on, "rules": list(rules)}
        path = self._dir / self._RULES_FILE
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            logger.warning("MES standing rules write failed for %s: %s", path, exc)

    def active_standing_rules(self, session_date: str) -> list[dict]:
        """Rules still live for ``session_date`` (expired ones are filtered out)."""
        path = self._dir / self._RULES_FILE
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("MES standing rules read failed for %s: %s", path, exc)
            return []
        if not isinstance(payload, dict):
            return []
        return [
            rule
            for rule in (payload.get("rules") or [])
            if isinstance(rule, dict)
            and (rule.get("expires_on") is None or str(rule.get("expires_on")) >= session_date)
        ]

    def append_rule_fired(
        self, date: str, *, rule_id: str, level: str, confirmation: str,
        tolerance: float, last_price: float, distance: float, as_of: datetime,
    ) -> None:
        self._append(date, {
            "kind": "rule_fired",
            "logged_at": datetime.now().isoformat(),
            "as_of": as_of.isoformat(timespec="minutes"),
            "session_date": date,
            "rule_id": rule_id,
            "level": level,
            "confirmation": confirmation,
            "tolerance": _num(tolerance),
            "last_price": _num(last_price),
            "distance": _num(distance),
        })

    def append_rule_skip(
        self, date: str, *, rule_id: str, level: str, reason: str,
        last_price: float | None = None, as_of: datetime | None = None,
    ) -> None:
        stamp = as_of or datetime.now()
        self._append(date, {
            "kind": "rule_skip",
            "logged_at": datetime.now().isoformat(),
            "as_of": stamp.isoformat(timespec="minutes"),
            "session_date": date,
            "rule_id": rule_id,
            "level": level,
            "reason": reason,
            "last_price": _num(last_price),
        })
```

Add the read-path methods after `load_hypothesis` (journal.py):

```python
    def load_rule_fires(self, date: str) -> list[dict]:
        return [e for e in self.load_day(date) if e.get("kind") == "rule_fired"]

    def load_rule_skips(self, date: str) -> list[dict]:
        return [e for e in self.load_day(date) if e.get("kind") == "rule_skip"]

    def rule_compliance(self, date: str, *, resolve_as_of: datetime | None = None) -> dict:
        """Fire accounting for one session.

        Returns ``{"triggered", "checked", "skipped", "missed", "open",
        "detail"}`` — every unresolved fire sits in ``open`` until
        ``resolve_as_of`` pushes it past the missed grace (trigger bar + one bar).
        """
        fires = self.load_rule_fires(date)
        checks = self.load_checks(date)
        skips = self.load_rule_skips(date)
        resolved = _resolve_fires(fires, checks, skips, resolve_as_of=resolve_as_of)
        by_outcome: dict[str, int] = {"checked": 0, "skipped": 0, "missed": 0, "open": 0}
        for fire in resolved:
            by_outcome[fire["outcome"]] = by_outcome.get(fire["outcome"], 0) + 1
        return {
            "triggered": len(fires),
            "checked": by_outcome.get("checked", 0),
            "skipped": by_outcome.get("skipped", 0),
            "missed": by_outcome.get("missed", 0),
            "open": [f for f in resolved if f["outcome"] == "open"],
            "detail": resolved,
        }

    def summarize_rule_compliance(self, date: str, *, resolve_as_of: datetime | None = None) -> str:
        """Markdown fire tally: the compliance scoreboard for `mes review`."""
        tally = self.rule_compliance(date, resolve_as_of=resolve_as_of)
        if not tally["detail"]:
            return f"No standing-rule triggers recorded for {date}."
        lines = [
            f"{tally['triggered']} triggered / {tally['checked']} checked / "
            f"{tally['skipped']} skipped / {tally['missed']} MISSED",
            "",
            "| Rule | Fired | Outcome |",
            "| --- | --- | --- |",
        ]
        for fire in tally["detail"]:
            as_of = str(fire.get("as_of", ""))
            fired = as_of[11:16] if len(as_of) >= 16 else as_of
            lines.append(
                "| {rule} | {fired} | {outcome} |".format(
                    rule=fire.get("rule_id", "?"), fired=fired, outcome=fire["outcome"],
                )
            )
        return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_journal.py -v`
Expected: PASS — 13 new standing-rule tests plus all existing journal tests.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/mes/journal.py tests/test_mes_journal.py
git commit -m "feat: persist standing rules and rule fire/skip accounting in journal"
```

### Task 4: Review agent emits standing rules (+ structured-output hook)

**Files:**
- Modify: `tradingagents/agents/utils/structured.py:49-79` (add optional `on_model`)
- Modify: `tradingagents/agents/mes/review_agent.py` (prompt section + capture hook)
- Test: `tests/test_mes_agents.py` (append; `FakeLLM` at line 34, `_review()` at line 91 exist here)

**Interfaces:**
- Consumes: `StandingRule`/`SessionReview` from Task 1; `invoke_structured_or_freetext` (existing).
- Produces (consumed by Task 7): `invoke_structured_or_freetext(..., on_model: Callable[[T], None] | None = None)` — called with the parsed model exactly when the structured path succeeds (never on free-text fallback); `create_mes_review_agent(llm)` returned `run` gains keyword params `standing_rules_summary: str = ""` and `on_review: Callable[[SessionReview], None] | None = None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mes_agents.py` (review-agent section):

```python
@pytest.mark.unit
def test_invoke_structured_calls_on_model_with_parsed_result():
    from tradingagents.agents.utils.structured import invoke_structured_or_freetext

    review = _review()
    llm = FakeLLM({SessionReview: review})
    captured = []
    output = invoke_structured_or_freetext(
        llm.with_structured_output(SessionReview), llm, "prompt",
        render_session_review, "test", on_model=captured.append,
    )
    assert captured == [review]
    assert "**Hypothesis Grade**" in output


@pytest.mark.unit
def test_review_agent_prompt_includes_current_standing_rules():
    llm = FakeLLM()
    captured = []
    agent = create_mes_review_agent(llm)
    agent(
        hypothesis="h", checks_summary="c", outcome_summary="o",
        standing_rules_summary=(
            "Standing rules from yesterday: VWAP retest -> log a check or skip "
            "(2 triggered, 1 checked, 1 MISSED)."
        ),
        on_review=captured.append,
    )
    prompt = llm.prompts[0]
    assert "Standing Rules" in prompt
    assert "MISSED" in prompt
```

Also add `from tradingagents.agents.utils.structured import invoke_structured_or_freetext`
to the test module's imports (not present today).

Then the two review-agent behavior tests:

```python
@pytest.mark.unit
def test_review_agent_forwards_on_review_capture():
    llm = FakeLLM({SessionReview: _review()})
    seen = []
    create_mes_review_agent(llm)(
        hypothesis="h", checks_summary="c", outcome_summary="o", on_review=seen.append
    )
    assert len(seen) == 1 and seen[0].discipline_grade == "B"


@pytest.mark.unit
def test_standing_rules_rendered_in_review_markdown():
    review = _review()
    review.standing_rules = [_rule_for_agents()]
    llm = FakeLLM({SessionReview: review})
    output = create_mes_review_agent(llm)(
        hypothesis="h", checks_summary="c", outcome_summary="o",
    )
    assert "Standing Rules for the next session" in output


def _rule_for_agents() -> StandingRule:
    return StandingRule(
        trigger={"kind": "level_retest", "level": "orb_top"},
        note="Fade the first ORB-top retest.",
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_agents.py -k standing -v`
Expected: FAIL — `TypeError: invoke_structured_or_freetext() got an unexpected keyword argument 'on_model'` and `ImportError: cannot import name 'invoke_structured_or_freetext'`.

- [ ] **Step 3: Implement the hook and the review-agent prompt**

In `tradingagents/agents/utils/structured.py`, change `invoke_structured_or_freetext`
to accept and fire an optional callback (signature and structured branch only —
the free-text fallback is untouched):

```python
def invoke_structured_or_freetext(
    structured_llm: Any | None,
    plain_llm: Any,
    prompt: Any,
    render: Callable[[T], str],
    agent_name: str,
    on_model: Callable[[T], None] | None = None,
) -> str:
    """Run the structured call and render to markdown; fall back to free text on any failure.

    ``on_model``, when given, is invoked with the parsed model instance right
    after a successful structured call (and never on the free-text fallback) so
    callers can capture typed output while still receiving rendered markdown.
    """
    if structured_llm is not None:
        try:
            result = structured_llm.invoke(prompt)
            if result is None:
                # A thinking model can answer in plain text instead of calling
                # the tool, leaving the parser with nothing to return. Treat it
                # as a structured miss and fall back, with a clear reason.
                raise ValueError("structured output returned no parsed result")
            if on_model is not None:
                on_model(result)
            return render(result)
        except Exception as exc:
            logger.warning(
                "%s: structured-output invocation failed (%s); retrying once as free text",
                agent_name, exc,
            )

    response = plain_llm.invoke(prompt)
    return response.content
```

In `tradingagents/agents/mes/review_agent.py`, extend `run` and the prompt:

```python
def create_mes_review_agent(llm):
    structured_llm = bind_structured(llm, SessionReview, "MES Review Agent")

    def run(
        *,
        hypothesis: str,
        checks_summary: str,
        outcome_summary: str,
        trades_summary: str = "",
        standing_rules_summary: str = "",
        on_review=None,
    ) -> str:
```

After the trades-summary block and before `prompt += get_language_instruction()`,
insert:

```python
        if standing_rules_summary.strip():
            prompt += f"""

---

**Standing Rules Previously Prescribed (and their compliance):**
{standing_rules_summary}

These are the machine-checked rules the copilot watched during the session and
whether each trigger was honored (a check logged), consciously skipped, or
MISSED. Treat every MISSED trigger as a discipline failure: name it in
what_failed and let it pull the discipline grade down."""
```

and change the final `return` to pass the capture hook through:

```python
        return invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_session_review,
            "MES Review Agent",
            on_model=on_review,
        )
```

(The free-text fallback path never fires `on_review` — a fallback review
yields no standing rules, which is the correct conservative behavior: no rules
persist unless the structured parse succeeded.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_agents.py -v`
Expected: PASS — new tests plus all existing agent tests (the extra prompt
section is inert when `standing_rules_summary` is empty, so existing prompt
assertions stay valid).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/agents/utils/structured.py tradingagents/agents/mes/review_agent.py tests/test_mes_agents.py
git commit -m "feat: review agent accepts standing-rules feedback and reports emitted rules"
```

### Task 5: Copilot integration — banner, tick wiring, `mes skip`

**Files:**
- Modify: `cli/mes.py` — `_render_radar` (line 586) gains a rule banner; `_copilot_tick` (line 821) evaluates rules in the flat branch; new `@mes_app.command("skip")`; `mes radar --watch` loop passes hits when it holds a journal; `mes radar`'s `_fire_auto_check` is untouched
- Test: `tests/test_mes_cli_copilot.py` (append) and `tests/test_mes_cli_skip.py` (new)

**Interfaces:**
- Consumes: `evaluate_standing_rules`/`RuleHit` (Task 2); `journal.active_standing_rules`, `journal.append_rule_fired`, `journal.append_rule_skip`, `journal.rule_compliance` from Task 3.
- Produces: `_render_radar(report, as_of, rule_hits=None) -> Table` (existing callers stay valid — the parameter defaults to `None`); `_copilot_tick` keeps its exact signature (rules are loaded from the journal inside the tick, like `find_open_trade` already is); new CLI command `mes skip --reason "..." [--rule ID] [--as-of HH:MM] [--date] [--journal-dir]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mes_cli_skip.py`:

```python
"""Tests for `mes skip` — acknowledging a standing-rule trigger without a check."""

from __future__ import annotations

from datetime import datetime

import pytest
from typer.testing import CliRunner

from cli.mes import mes_app
from tradingagents.mes.journal import MesJournal

runner = CliRunner()

STAMP = datetime(2026, 3, 30, 10, 35)


def _open_fire(journal):
    journal.append_rule_fired(
        "2026-03-30", rule_id="orb_top", level="ORB high", confirmation="none",
        tolerance=2.0, last_price=101.25, distance=0.25, as_of=STAMP,
    )


@pytest.mark.unit
def test_skip_resolves_the_open_fire(tmp_path):
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    _open_fire(journal)
    result = runner.invoke(mes_app, [
        "skip", "--reason", "internals diverging", "--date", "2026-03-30",
        "--journal-dir", str(tmp_path),
    ])
    assert result.exit_code == 0, result.output
    assert "skipped" in result.output
    skips = MesJournal({"mes_journal_dir": str(tmp_path)}).load_rule_skips("2026-03-30")
    assert len(skips) == 1
    assert skips[0]["rule_id"] == "orb_top"
    assert skips[0]["reason"] == "internals diverging"


@pytest.mark.unit
def test_skip_targets_a_specific_rule_when_given(tmp_path):
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    journal.append_rule_fired("2026-03-30", rule_id="orb_top", level="ORB high",
                              confirmation="none", tolerance=2.0, last_price=101.0,
                              distance=0.0, as_of=STAMP)
    journal.append_rule_fired("2026-03-30", rule_id="vwap", level="VWAP",
                              confirmation="none", tolerance=2.0,
                              last_price=99.2, distance=0.2, as_of=STAMP)
    result = runner.invoke(mes_app, [
        "skip", "--reason", "chop", "--rule", "vwap", "--date", "2026-03-30",
        "--journal-dir", str(tmp_path),
    ])
    assert result.exit_code == 0, result.output
    skips = MesJournal({"mes_journal_dir": str(tmp_path)}).load_rule_skips("2026-03-30")
    assert [s["rule_id"] for s in skips] == ["vwap"]


@pytest.mark.unit
def test_skip_without_pending_trigger_fails(tmp_path):
    result = runner.invoke(mes_app, [
        "skip", "--reason", "nothing", "--date", "2026-03-30",
        "--journal-dir", str(tmp_path),
    ])
    assert result.exit_code == 1
    assert "No pending" in result.output
```

```python
# ---- Standing rules ----

RULE = {
    "trigger": {"kind": "level_retest", "level": "orb_top",
                "confirmation": "none", "tolerance_points": 2.0},
    "requirement": "log_check_or_skip",
    "note": "Fade the first ORB-top retest.",
    "expires_on": None,
}


@pytest.mark.unit
def test_flat_tick_fires_standing_rule_once(tmp_path, patched_snapshot, capsys):
    journal = mes_cli._trade_journal(mes_cli.load_mes_config(), tmp_path)
    journal.save_standing_rules([RULE], reviewed_on="2026-03-30")
    t0 = datetime(2026, 3, 30, 11, 0)
    _tick(journal, t0)  # patched snapshot closes 100.0; ORB high 101.0 -> hit
    out = capsys.readouterr().out
    assert "STANDING RULE" in out
    assert len(journal.load_rule_fires("2026-03-30")) == 1
    _tick(journal, t0 + timedelta(minutes=1))
    assert capsys.readouterr().out.count("STANDING RULE") == 0  # once per session
    assert len(journal.load_rule_fires("2026-03-30")) == 1


@pytest.mark.unit
def test_skip_resolves_the_banner(tmp_path, patched_snapshot, capsys):
    journal = mes_cli._trade_journal(mes_cli.load_mes_config(), tmp_path)
    journal.save_standing_rules([RULE], reviewed_on="2026-03-30")
    t0 = datetime(2026, 3, 30, 11, 0)
    _tick(journal, t0)
    journal.append_rule_skip("2026-03-30", rule_id="orb_top", level="ORB high",
                             reason="chop", as_of=t0)
    _tick(journal, t0 + timedelta(minutes=1))
    assert capsys.readouterr().out.count("LOG A CHECK NOW") == 0  # skip resolved it


@pytest.mark.unit
def test_radar_panel_renders_open_rule_banner():
    from tradingagents.mes.checklist import evaluate
    from tradingagents.mes.radar import build_proximity
    from tradingagents.mes.rules import RuleHit
    from tests.test_mes_radar import _render_table_to_text

    hit = RuleHit(rule_id="orb_top", level="ORB high", level_price=101.0,
                  distance=-1.0, tolerance=2.0, confirmation="none",
                  note="Fade the first ORB-top retest.")
    snap = make_snapshot(as_of=datetime(2026, 3, 30, 11, 0))
    snap = make_snapshot(as_of=datetime(2026, 3, 30, 11, 0))
    report = build_proximity(snap, evaluate(snap, "auto"))
    text = _render_table_to_text(mes_cli._render_radar(report, datetime(2026, 3, 30, 11, 0), rule_hits=[hit]))
    assert "LOG A CHECK NOW" in text
    assert "mes skip" in text
```

`make_snapshot` is already imported at the top of `test_mes_cli_copilot.py`
(line 11); add `from tradingagents.mes.checklist import evaluate` and
`from tradingagents.mes.radar import build_proximity` to its import block for
the last test. `_render_radar` takes only the report, so the banner rows come
from the `rule_hits` parameter.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_cli_skip.py tests/test_mes_cli_copilot.py -v`
Expected: FAIL — `ValueError: is not a valid Typer command name` (no `skip`
command) and `TypeError: _render_radar() got an unexpected keyword argument 'rule_hits'`.

- [ ] **Step 3: Implement**

**(a) Banner rows in `_render_radar`** (`cli/mes.py:586`) — add the keyword
parameter and render hits directly under the state banner:

```python
def _render_radar(
    report: ProximityReport, as_of: datetime, rule_hits: list[RuleHit] | None = None
) -> Table:
    """Build a compact Rich Table displaying the proximity report.

    ``rule_hits`` are the currently-open standing-rule triggers (see the tick
    wiring below); each renders as a persistent LOG A CHECK NOW banner until
    the trader runs `mes check` (any journal check) or `mes skip`.
    """
    state_style = _STATE_STYLE[report.state]
    state_label = _STATE_LABEL[report.state]
    tier_style = _TIER_STYLE.get(report.tier, "white")

    outer = Table.grid(padding=(0, 1))

    # ---- Header row ----
    header = (
        f"[bold]/MES {report.price:.2f}[/bold]  "
        f"{report.side.upper()}  "
        f"[{tier_style}]{report.tier}[/{tier_style}]  "
        f"Score [bold]{report.score}/{report.max_score}[/bold]  "
        f"Conf [bold]{report.confirmations}/{report.required}[/bold]  "
        f"SPY [bold]{report.spy_confirmations}/5[/bold]  "
        f"Band ±{report.proximity_band:.1f} pts  "
        f"[dim]{as_of.strftime('%H:%M')}[/dim]"
    )
    outer.add_row(Text.from_markup(header))

    # ---- State banner ----
    outer.add_row(Text.from_markup(f"[{state_style}]{state_label}[/{state_style}]"))

    # ---- Standing-rule banner (directly under the state, before internals) ----
    for hit in rule_hits or []:
        outer.add_row(Text.from_markup(
            f"[bold yellow]STANDING RULE — {hit.level} retest (±{hit.tolerance:g} pts)[/bold yellow]"
        ))
        outer.add_row(Text.from_markup(
            "[bold yellow]LOG A CHECK NOW[/bold yellow] — run [bold]`mes check`[/bold] "
            f"or [bold]`mes skip --rule {hit.rule_id} --reason …`[/bold]"
            + (f"  [dim]({hit.note})[/dim]" if hit.note else "")
        ))

    # ... everything below unchanged (internals, gates, missing items,
    # warnings, level tape): cli/mes.py:610-652 stays exactly as-is.
```

The imports at the top of `cli/mes.py` gain two lines with the other
`tradingagents.mes` imports:

```python
from tradingagents.agents.schemas import describe_level
from tradingagents.mes.rules import RuleHit, evaluate_standing_rules
```

**(b) Tick wiring in `_copilot_tick`** (`cli/mes.py:821-863`) — the flat branch
loads rules, evaluates hits, renders the banner, and fires once per rule per
session. Replace the current flat-branch panel update (lines 837-842) with:

```python
    if trade is None:
        report = build_proximity(snapshot, result, proximity_band=within)
        rules = journal.active_standing_rules(stamp_date)
        rule_hits = evaluate_standing_rules(snapshot, result, rules)
        # Fire once per rule per session — the journal's rule_fired records are
        # the dedup keys, so a copilot restart cannot double-fire a rule.
        fired_ids = {str(fire.get("rule_id")) for fire in journal.load_rule_fires(stamp_date)}
        new_hits = [hit for hit in rule_hits if hit.rule_id not in fired_ids]
        for hit in new_hits:
            if not no_log:
                journal.append_rule_fired(
                    stamp_date, rule_id=hit.rule_id, level=hit.level,
                    confirmation=hit.confirmation, tolerance=hit.tolerance,
                    last_price=snapshot.mes.close, distance=hit.distance,
                    as_of=stamp,
                )
            if alert:
                console.print("\a", end="")
            console.print(
                f"[bold yellow]STANDING RULE triggered — {hit.level} retest "
                f"({hit.distance:+.2f} pts):[/bold yellow] run [bold]mes check[/bold] "
                f"or [bold]mes skip --rule {hit.rule_id} --reason \"<why>\"[/bold]"
            )
        # Fires past the 10-min grace already count as MISSED and stop nagging;
        # fires resolved by a check/skip also leave the banner. Panel rows and
        # console reminders show only what is still open.
        tally = journal.rule_compliance(stamp_date, resolve_as_of=stamp)
        open_ids = {str(fire.get("rule_id")) for fire in tally["open"]}
        panel_hits = [hit for hit in rule_hits if hit.rule_id in open_ids or hit in new_hits]
        if live is not None:
            live.update(Panel(_render_radar(report, stamp, rule_hits=panel_hits),
                              title=f"MES Copilot — radar (flat)   {stamp:%H:%M:%S}",
                              border_style="blue"))
        else:
            console.print(Panel(_render_radar(report, stamp, rule_hits=panel_hits),
                                title="MES Radar", border_style="blue"))
        for rule_id in sorted(open_ids | {hit.rule_id for hit in new_hits}):
            console.print(
                f"[bold yellow]LOG A CHECK NOW[/bold yellow] — {describe_level(rule_id)} "
                f"retest still open: [bold]mes check[/bold] or "
                f"[bold]mes skip --rule {rule_id} --reason \"<why>\"[/bold]"
            )
        if tally["missed"]:
            console.print(f"[bold red]MISSED CHECKS TODAY: {tally['missed']}[/bold red]")
```

Notes for the implementer:

- `fired_ids` is the dedup key set (one fire per rule per session) rebuilt
  from journal records each tick — restart-safe. `--no-log` skips the fire
  write (the banner still shows for the session, but nothing persists).
- The panel banner renders only rules that are **open or newly fired** — a
  skipped/checked rule whose price is still in band stops nagging (this is what
  `test_skip_resolves_the_banner` asserts).
- `rule_compliance(..., resolve_as_of=stamp)` classifies fires older than the
  10-minute grace as `missed`, so the reminder nags for at most three 5-minute
  bars, then hands the failure to the review tally — the banner never
  deadlocks the panel for the rest of the day.
- The `mes radar --watch` loop (`cli/mes.py:784-803`) also renders
  `_render_radar` — pass `rule_hits` there too: compute
  `rule_hits = evaluate_standing_rules(snapshot, result, journal.active_standing_rules(stamp.strftime("%Y-%m-%d")))`
  when `journal is not None`, else pass `[]`. Radar without `--auto-check` has
  no journal (`journal is None`) and passes no hits.

**(c) New `mes skip` command** — insert after the `copilot` command
(`cli/mes.py`, after line 1032, before the Trade management section):

```python
@mes_app.command("skip")
def skip(
    reason: str = typer.Option(..., "--reason", help="Why this trigger is consciously skipped."),
    rule: str | None = typer.Option(None, "--rule", help="rule_id to skip when several are open."),
    as_of: str | None = typer.Option(None, "--as-of", help="Simulated timestamp, e.g. 12:35."),
    date: str | None = typer.Option(None, "--date", help="Trade date (defaults to today)."),
    journal_dir: Path | None = typer.Option(None, "--journal-dir", hidden=True),
):
    """Acknowledge a standing-rule trigger without logging a check.

    An honest skip is a scored pass: it resolves the open fire so `mes review`
    does not tally it as MISSED. Without --rule the most recent open fire is
    skipped.
    """
    cfg = load_mes_config()
    journal = _trade_journal(cfg, journal_dir)
    session_date = date or _market_now(cfg).strftime("%Y-%m-%d")
    open_fires = journal.rule_compliance(session_date)["open"]
    if not open_fires:
        console.print(f"[red]No pending rule trigger for {session_date} — nothing to skip.[/red]")
        raise typer.Exit(code=1)
    fire = next(
        (f for f in reversed(open_fires) if rule is None or f.get("rule_id") == rule), None
    )
    if fire is None:
        console.print(
            f"[red]No open fire for rule '{rule}'. Open fires: "
            f"{', '.join(str(f.get('rule_id')) for f in open_fires)}[/red]"
        )
        raise typer.Exit(code=1)
    journal.append_rule_skip(
        session_date,
        rule_id=str(fire.get("rule_id", "")),
        level=str(fire.get("level", "")),
        reason=reason,
        last_price=fire.get("last_price"),
        as_of=_parse_as_of(as_of, cfg, date=session_date) if as_of else _market_now(cfg),
    )
    console.print(
        f"[green]Skipped {fire.get('rule_id')} ({fire.get('level')}) — "
        f"reason recorded for the next review.[/green]"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_cli_skip.py tests/test_mes_cli_copilot.py tests/test_mes_radar.py -v`
Expected: PASS — new tests plus all existing copilot/radar tests.

- [ ] **Step 5: Commit**

```bash
git add cli/mes.py tests/test_mes_cli_skip.py tests/test_mes_cli_copilot.py
git commit -m "feat: standing-rule banner in copilot ticks and mes skip command"
```

### Task 6: Review wiring — save emitted rules, feed the compliance tally back

**Files:**
- Modify: `cli/mes.py` `review` command (lines 457-550): compliance panel, agent kwargs, rules persistence
- Test: `tests/test_mes_cli_skip.py` (append — it already owns the CLI+journal test scaffolding)

**Interfaces:**
- Consumes: `journal.summarize_rule_compliance` (Task 3), `journal.save_standing_rules` (Task 3), review agent `standing_rules_summary`/`on_review` params (Task 4), `SessionReview` from Task 1.
- Produces: end of `mes review` — (1) a "Rule Compliance" panel when the day had rule fires; (2) `standing_rules_summary` kwarg to the agent; (3) `standing_rules.json` written from the parsed review's `standing_rules` (superseding the previous set, expiry defaulting to the next weekday after the review date).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mes_cli_skip.py` (top-of-file imports:
`from tradingagents.agents.schemas import SessionReview, StandingRule`,
`from tradingagents.mes.checklist import evaluate`, and
`from tests.mes_factories import make_snapshot, make_mes_series`):

```python
@pytest.mark.unit
def test_review_saves_emitted_standing_rules(tmp_path, monkeypatch, capsys):

    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    snap = make_snapshot(as_of=datetime(2026, 3, 30, 16, 0))
    journal.append_check(snapshot=snap, result=evaluate(snap, "auto"))
    captured = {}

    class FakeReviewAgent:
        def __call__(self, **kwargs):
            captured.update(kwargs)
            kwargs["on_review"](SessionReview(
                hypothesis_grade="Correct", discipline_grade="B",
                what_worked="w", what_failed="f",
                one_improvement="Log a check at the ORB-top retest.",
                narrative="n",
                standing_rules=[StandingRule(
                    trigger={"kind": "level_retest", "level": "orb_top"},
                    note="Fade the first ORB-top retest.",
                )],
            ))
            return "**Hypothesis Grade**: Correct\n\nReview text."

    monkeypatch.setattr(mes_cli, "_make_llm", lambda cfg: object())
    monkeypatch.setattr(mes_cli, "create_mes_review_agent", lambda llm: FakeReviewAgent())
    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(mes_cli, "_market_now", lambda cfg: datetime(2026, 3, 30, 17, 0))

    result = runner.invoke(mes_app, [
        "review", "--date", "2026-03-30", "--journal-dir", str(tmp_path), "--no-memory",
    ])
    assert result.exit_code == 0, result.output
    assert "standing rule(s) saved" in result.output
    saved = MesJournal({"mes_journal_dir": str(tmp_path)}).active_standing_rules("2026-03-31")
    assert [r["trigger"]["level"] for r in saved] == ["orb_top"]
    assert "standing_rules_summary" in captured  # compliance tally reaches the prompt
```

Notes: the check appended to the journal is what keeps `mes review` from
exiting early ("No journal entries"); `--no-memory` skips the
`TradingMemoryLog` write, keeping the test hermetic. The fake asserts nothing
about `standing_rules_summary` content — its presence in `captured` is the
loop-closure assertion.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_cli_skip.py -k review -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'standing_rules_summary'`
(the CLI does not pass it yet, so the fake's kwargs assertion fails) and no
"standing rule(s) saved" output.

- [ ] **Step 3: Implement the review wiring**

In `cli/mes.py`'s `review` command — three insertions:

**(a)** After `checks_summary = journal.summarize_checks(session_date)` (line 480),
add the compliance tally and its panel — one tally, computed once, reused for
the panel and the agent prompt:

```python
    rule_compliance = journal.summarize_rule_compliance(session_date, resolve_as_of=close_stamp)
    if not rule_compliance.startswith("No standing-rule triggers"):
        console.print(Panel(Markdown(rule_compliance),
                            title="Rule Compliance", border_style="yellow"))
```

(`close_stamp` is already computed at line 502-503; resolving fires as of the
session close is what turns end-of-day-unresolved fires into MISSED — the
review grades the whole session.)

**(b)** Replace the agent invocation (lines 519-526) with:

```python
    hypothesis = (hypothesis_record or {}).get("hypothesis", "No hypothesis was recorded this morning.")
    emitted_rules: list[SessionReview] = []

    def _capture_review(review: SessionReview) -> None:
        emitted_rules.append(review)

    try:
        agent = create_mes_review_agent(_make_llm(config))
        review_markdown = agent(
            hypothesis=hypothesis,
            checks_summary=checks_summary,
            outcome_summary=outcome_summary,
            trades_summary=trades_summary,
            standing_rules_summary=rule_compliance,
            on_review=_capture_review,
        )
```

(The agent call signature change is backward-compatible — both new params have
defaults.)

**(c)** After `console.print(Panel(Markdown(review_markdown), ...))` (line 531),
before `if no_memory:` — persist the emitted rules:

```python
    if emitted_rules and emitted_rules[0].standing_rules:
        next_session = (
            datetime.strptime(session_date, "%Y-%m-%d").date() + timedelta(days=1)
        )
        while next_session.weekday() >= 5:  # Sat/Sun — holidays accepted (extra day of nagging is harmless)
            next_session += timedelta(days=1)
        saved_rules = []
        for rule in emitted_rules[0].standing_rules:
            rule_dict = rule.model_dump()
            if not rule_dict.get("expires_on"):
                rule_dict["expires_on"] = next_session.isoformat()
            saved_rules.append(rule_dict)
        journal.save_standing_rules(saved_rules, reviewed_on=session_date)
        console.print(
            f"[green]{len(saved_rules)} standing rule(s) saved for the next session "
            f"(expire {next_session.isoformat()}).[/green]"
        )
```

(`timedelta` must be importable in `cli/mes.py` — extend the existing
`from datetime import datetime, timedelta` import at the top of the file if
`timedelta` is not already imported.)

**(d)** In `tradingagents/agents/mes/review_agent.py`, add the prescription
instructions to the prompt — directly after the `standing_rules_summary`
section added in Task 4, before `get_language_instruction()`:

```python
        prompt += """

---

**Prescribing standing rules (machine-checked tomorrow):**
If your discipline findings include a chart-watchable miss — hesitation at a
level, an un-watched retest — prescribe it in `standing_rules` so the copilot
will prompt a check at that level tomorrow. Rules must be retests of one of:
vwap, orb_top, orb_bottom, pdh, pdl, prior_close, prior_vah, prior_val,
prior_poc, onh, onl. Zero to three rules; only ones you still believe in.
Omit `standing_rules` (leave it empty) when the improvement is not a
chart watch — do not force it."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_cli_skip.py tests/test_mes_agents.py -v`
Expected: PASS — skip tests, the review-wiring test, and all agent tests.

- [ ] **Step 5: Commit**

```bash
git add cli/mes.py tradingagents/agents/mes/review_agent.py tests/test_mes_cli_skip.py
git commit -m "feat: mes review saves standing rules and grades rule compliance"
```

### Task 7: Full-suite verification + usage note

**Files:**
- Modify: `plans/mes-session-handoff.md` or the repo README's MES section — one short usage block (fold into whichever file documents the copilot workflow today; `plans/mes-copilot-execution-guide.md` documents the session workflow)

**Interfaces:**
- Consumes: everything above.
- Produces: green suite + documented workflow.

- [ ] **Step 1: Run the full MES suite**

Run: `python -m pytest tests/ -k mes -q`
Expected: all pass (previously 1369 passed / 2 skipped plus the ~26 new tests).
If any pre-existing test asserts on the old `_render_radar` output byte-for-byte,
update only that assertion (banner rows are additive and sit after the state
line).

- [ ] **Step 2: Manual smoke (deterministic, no LLM)**

```bash
python - <<'PY'
from datetime import datetime
from tradingagents.mes.journal import MesJournal
journal = MesJournal({"mes_journal_dir": "/tmp/mes_rules_demo"})
journal.save_standing_rules([{
    "trigger": {"kind": "level_retest", "level": "orb_top",
                "confirmation": "none", "tolerance_points": 2.0},
    "requirement": "log_check_or_skip", "note": "Fade the first ORB-top retest.",
    "expires_on": None,
}], reviewed_on="2026-03-30")
journal.append_rule_fired("2026-03-31", rule_id="orb_top", level="ORB high",
                          confirmation="none", tolerance=2.0, last_price=101.5,
                          distance=0.5, as_of=datetime(2026, 3, 31, 10, 35))
print(journal.summarize_rule_compliance("2026-03-31"))
PY
```
Expected output: the `1 triggered / 0 checked / 0 skipped / 1 MISSED` tally —
the fire has no check/skip after it, so it resolves to missed. Then rerun with
`journal.append_rule_skip("2026-03-31", rule_id="orb_top", level="ORB high",
reason="test", as_of=datetime(2026, 3, 31, 10, 40))` inserted before the print
to see it flip to `skipped`. (Live `mes copilot` verification needs recorded
CSVs; the unit suite covers the CLI paths.)

- [ ] **Step 3: Commit**

```bash
git add cli/mes.py docs/ README.md
git commit -m "docs: standing rules usage for the mes copilot"
```

---

## Self-review (completed against the approved design)

1. **Spec coverage:** A → Tasks 1/3/4/6 (schema, persistence, review prompt + save). B → Tasks 2/5 (trigger engine, banner, per-session dedup via journal fire records). C → Tasks 3/5/6 (`mes skip`, missed tally, review feedback). The `one_improvement` prose path is untouched (memory log unchanged) — standing rules are the structured superset, as approved.
2. **Placeholder scan:** no TBD/TODO steps; every code step carries the exact code.
3. **Type consistency:** `RuleHit(rule_id, level, level_price, distance, tolerance, confirmation, note)` — Task 2 defines it, Task 5's banner test constructs it with those exact names; `journal.rule_compliance(...) -> {"triggered","checked","skipped","missed","open","detail"}` — Task 3 defines, Tasks 5/6 consume `["open"]`/`["missed"]`; `standing_rules_summary`/`on_review` — Task 4 defines, Task 6 consumes.

## Execution notes

- **Worktree:** this feature branch (`feat/mes-copilot`) is already checked out in the main working copy; `tradingagents/mes/config.py` carries unrelated local edits — never `git add` that file.
- **Test runner:** `python -m pytest tests/<file> -v` from `/Users/akundrock/sandbox/TradingAgents` (pytest 9.1, markers `unit`/`integration`/`smoke` configured in `pyproject.toml`).
- **Order is load-bearing:** Task 2's lazy `describe_level` import requires Task 1's schema; Task 5's CLI needs Tasks 2+3; Task 6 needs Tasks 3+4.
- **Deliberate scope limits (from the brainstorm):** no gatekeeper-prompt threading, no rule kinds beyond `level_retest`, no mid-trade rule evaluation (managing branch untouched), banner lives inside the existing radar panel (no separate modal).

























