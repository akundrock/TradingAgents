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
    """Factory snapshot: MES vwap 99.0, ORB 98/101; SPY internals aligned by default."""
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


@pytest.mark.unit
def test_orb_top_rule_fires_within_tolerance():
    snapshot = _snapshot(100.5)
    hits = evaluate_standing_rules(snapshot, _evaluate(snapshot), [_rule_dict()])
    assert [hit.rule_id for hit in hits] == ["orb_top"]
    assert hits[0].level == "ORB high"
    assert hits[0].level_price == 101.0
    assert hits[0].distance == pytest.approx(-0.5)
    assert hits[0].tolerance == 2.0
    assert hits[0].note == "Fade the first ORB-top retest."


@pytest.mark.unit
def test_outside_tolerance_does_not_fire():
    snapshot = _snapshot(104.0)
    assert evaluate_standing_rules(snapshot, _evaluate(snapshot), [_rule_dict()]) == []


@pytest.mark.unit
def test_unavailable_level_never_fires():
    snapshot = _snapshot(100.0)  # factory snapshot has no prior/overnight levels
    assert evaluate_standing_rules(
        snapshot, _evaluate(snapshot),
        [{"trigger": {"kind": "level_retest", "level": "pdh",
                      "confirmation": "none", "tolerance_points": 50.0},
          "requirement": "log_check_or_skip", "note": "", "expires_on": None}],
    ) == []


@pytest.mark.unit
def test_unknown_trigger_kind_is_skipped():
    snapshot = _snapshot(100.0)
    rules = [{"trigger": {"kind": "time_of_day", "level": "vwap"}}]
    assert evaluate_standing_rules(snapshot, _evaluate(snapshot), rules) == []


@pytest.mark.unit
def test_prior_and_overnight_levels_resolve():
    prior = {"high": 102.0, "low": 95.0, "close": 99.0, "vah": 101.5,
             "val": 96.5, "poc": 100.0}
    snapshot = _snapshot(100.0, prior=prior, overnight=(103.0, 94.0))
    result = _evaluate(snapshot)
    # Wide tolerance: the point of this test is level resolution, not band math.
    assert evaluate_standing_rules(
        snapshot, result, [_rule_dict(level="pdh", tolerance_points=10.0)])[0].level_price == 102.0
    assert evaluate_standing_rules(
        snapshot, result, [_rule_dict(level="pdl", tolerance_points=10.0)])[0].level_price == 95.0
    assert evaluate_standing_rules(snapshot, result, [_rule_dict(level="onh", tolerance_points=10.0)])[0].level_price == 103.0


@pytest.mark.unit
def test_add_vold_aligned_confirmation_blocks_on_disagreement():
    snapshot = _snapshot(100.0, internals=[(300.0, 100.0, -1000.0)] * 6)
    assert evaluate_standing_rules(
        snapshot, _evaluate(snapshot), [_rule_dict(confirmation="add_vold_aligned")]) == []
    aligned = _snapshot(100.0, internals=[(300.0, 100.0, 1000.0)] * 6)
    hits = evaluate_standing_rules(
        aligned, _evaluate(aligned), [_rule_dict(confirmation="add_vold_aligned")])
    assert hits and hits[0].confirmation == "add_vold_aligned"


@pytest.mark.unit
def test_engine_is_stateless_same_input_same_hits():
    snapshot = _snapshot(101.0)  # exactly at the ORB high
    result = _evaluate(snapshot)
    first = evaluate_standing_rules(snapshot, result, [_rule_dict()])
    second = evaluate_standing_rules(snapshot, result, [_rule_dict()])
    assert [h.level_price for h in first] == [h.level_price for h in second]


@pytest.mark.unit
def test_vwap_rule_uses_checklist_vwap():
    snapshot = _snapshot(100.0)  # factory vwap = 99.0
    hits = evaluate_standing_rules(snapshot, _evaluate(snapshot), [_rule_dict(level="vwap")])
    assert [hit.rule_id for hit in hits] == ["vwap"]
    assert hits[0].level == "VWAP"
    assert hits[0].level_price == pytest.approx(99.0)