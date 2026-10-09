"""Process-fidelity: standing-rules hygiene, invalidation (C), structured frame (D)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from typer.testing import CliRunner

from cli import mes as mes_cli
from cli.mes import mes_app
from tradingagents.agents.schemas import (
    InvalidationTrigger,
    MachineClause,
    MorningHypothesis,
    RuleLevel,
    StandingRule,
    DayType,
    MarketBias,
    render_morning_hypothesis,
)
from tradingagents.mes.checklist import evaluate
from tradingagents.mes.journal import MesJournal
from tradingagents.mes.rules import RuleHit, evaluate_standing_rules
from tests.mes_factories import (
    DEFAULT_AS_OF,
    make_mes_series,
    make_snapshot,
    make_spy_series,
)
from tests.test_mes_radar import _render_table_to_text

runner = CliRunner()


@pytest.fixture()
def journal(tmp_path):
    return MesJournal({"mes_journal_dir": str(tmp_path)})


def _inv_rule(level="vwap", break_side="below", tolerance=2.0, inv_id="inv_1", note=""):
    return {
        "trigger": {
            "kind": "invalidation",
            "id": inv_id,
            "level": level,
            "break_side": break_side,
            "confirmation": "none",
            "tolerance_points": tolerance,
        },
        "requirement": "flip_or_skip",
        "note": note or "Frame dies on breach.",
        "expires_on": None,
    }


def _retest_rule(level="orb_top", expires_on=None):
    return {
        "trigger": {
            "kind": "level_retest",
            "level": level,
            "confirmation": "none",
            "tolerance_points": 2.0,
        },
        "requirement": "log_check_or_skip",
        "note": "Fade retest.",
        "expires_on": expires_on,
    }


# ---- Phase 1: standing-rules hygiene ----


@pytest.mark.unit
def test_missing_rules_not_ready(journal):
    state = journal.standing_rules_readiness("2026-03-31")
    assert state["ready"] is False
    assert state["status"] == "missing"
    assert "RULES EXPIRED / NONE" in state["banner"]


@pytest.mark.unit
def test_expired_rules_not_ready(journal):
    journal.save_standing_rules(
        [_retest_rule(expires_on="2026-03-30")], reviewed_on="2026-03-29",
    )
    state = journal.standing_rules_readiness("2026-03-31")
    assert state["ready"] is False
    assert state["status"] == "expired"
    assert "RULES EXPIRED / NONE" in state["banner"]


@pytest.mark.unit
def test_empty_rules_without_ack_not_ready(journal):
    journal.save_standing_rules([], reviewed_on="2026-03-30", blank_day_ack=False)
    state = journal.standing_rules_readiness("2026-03-31")
    assert state["ready"] is False
    assert state["status"] == "needs_ack"


@pytest.mark.unit
def test_blank_day_ack_makes_ready(journal):
    journal.ack_blank_standing_rules(reviewed_on="2026-03-30", reason="range chop, no edge")
    state = journal.standing_rules_readiness("2026-03-31")
    assert state["ready"] is True
    assert state["blank_day_ack"] is True
    assert state["banner"] == ""


@pytest.mark.unit
def test_active_rules_ready(journal):
    journal.save_standing_rules(
        [_retest_rule(expires_on="2026-03-31")], reviewed_on="2026-03-30",
    )
    state = journal.standing_rules_readiness("2026-03-31")
    assert state["ready"] is True
    assert state["active_count"] == 1


@pytest.mark.unit
def test_rules_blank_cli(tmp_path):
    result = runner.invoke(
        mes_app,
        ["rules", "--blank", "--reason", "no chart watch",
         "--date", "2026-03-30", "--journal-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    assert journal.standing_rules_readiness("2026-03-31")["ready"] is True


@pytest.mark.unit
def test_copilot_surfaces_rules_expired_banner(tmp_path, monkeypatch, capsys):
    snap = make_snapshot(mes=make_mes_series(close=100.0))
    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snap)
    journal = mes_cli._trade_journal(mes_cli.load_mes_config(), tmp_path)
    stamp = datetime(2026, 3, 30, 11, 0)
    mes_cli._copilot_tick(
        stamp=stamp, cfg=mes_cli.load_mes_config(), journal=journal, side="auto",
        within=None, mes_csv=None, spy_csv=None, gatekeeper=None, past_context="",
        manager=None, manager_every=0.0, alert=False, no_log=False,
    )
    out = capsys.readouterr().out
    assert "RULES EXPIRED / NONE" in out


# ---- Phase 2: Option C invalidation ----


@pytest.mark.unit
def test_invalidation_fires_below_vwap():
    # factory vwap=99.0; price 98.5 is below → fire
    snap = make_snapshot(mes=make_mes_series(close=98.5), as_of=DEFAULT_AS_OF)
    hits = evaluate_standing_rules(snap, evaluate(snap, "auto"), [_inv_rule()])
    assert len(hits) == 1
    assert hits[0].kind == "invalidation"
    assert hits[0].rule_id == "inv_1"
    assert hits[0].level == "VWAP"


@pytest.mark.unit
def test_invalidation_does_not_fire_when_still_above():
    snap = make_snapshot(mes=make_mes_series(close=102.0), as_of=DEFAULT_AS_OF)
    assert evaluate_standing_rules(snap, evaluate(snap, "auto"), [_inv_rule()]) == []


@pytest.mark.unit
def test_invalidation_schema_and_describe():
    rule = StandingRule(
        trigger=InvalidationTrigger(
            id="inv_1", level=RuleLevel.VWAP, break_side="below",
        ),
        requirement="flip_or_skip",
        note="Bull dies below VWAP.",
    )
    assert rule.trigger.kind == "invalidation"
    assert rule.requirement == "flip_or_skip"
    from tradingagents.agents.schemas import describe_standing_rule
    text = describe_standing_rule(rule)
    assert "Invalidation" in text
    assert "mes flip" in text


@pytest.mark.unit
def test_radar_panel_renders_reread_banner():
    hit = RuleHit(
        rule_id="inv_1", level="VWAP", level_price=99.0, distance=-1.0,
        tolerance=2.0, confirmation="none", note="Frame dead.", kind="invalidation",
    )
    snap = make_snapshot(as_of=datetime(2026, 3, 30, 11, 0))
    from tradingagents.mes.radar import build_proximity
    report = build_proximity(snap, evaluate(snap, "auto"))
    text = _render_table_to_text(
        mes_cli._render_radar(report, datetime(2026, 3, 30, 11, 0), rule_hits=[hit])
    )
    assert "RE-READ REQUIRED" in text
    assert "mes flip" in text


@pytest.mark.unit
def test_invalidation_fire_cleared_by_flip(tmp_path, monkeypatch, capsys):
    snap = make_snapshot(mes=make_mes_series(close=98.5))
    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snap)
    journal = mes_cli._trade_journal(mes_cli.load_mes_config(), tmp_path)
    journal.save_standing_rules([_inv_rule()], reviewed_on="2026-03-29")
    t0 = datetime(2026, 3, 30, 11, 0)
    mes_cli._copilot_tick(
        stamp=t0, cfg=mes_cli.load_mes_config(), journal=journal, side="auto",
        within=None, mes_csv=None, spy_csv=None, gatekeeper=None, past_context="",
        manager=None, manager_every=0.0, alert=False, no_log=False,
    )
    out1 = capsys.readouterr().out
    assert "RE-READ REQUIRED" in out1
    assert "INVALIDATION" in out1
    journal.append_flip("2026-03-30", reason="lost VWAP", new_frame="", as_of=t0)
    mes_cli._copilot_tick(
        stamp=t0 + timedelta(minutes=1), cfg=mes_cli.load_mes_config(),
        journal=journal, side="auto", within=None, mes_csv=None, spy_csv=None,
        gatekeeper=None, past_context="", manager=None, manager_every=0.0,
        alert=False, no_log=False,
    )
    out2 = capsys.readouterr().out
    assert "RE-READ REQUIRED" not in out2
    tally = journal.rule_compliance("2026-03-30")
    assert tally["flipped"] == 1
    assert tally["open"] == []


@pytest.mark.unit
def test_invalidation_fire_cleared_by_skip(tmp_path, monkeypatch, capsys):
    snap = make_snapshot(mes=make_mes_series(close=98.5))
    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snap)
    journal = mes_cli._trade_journal(mes_cli.load_mes_config(), tmp_path)
    journal.save_standing_rules([_inv_rule()], reviewed_on="2026-03-29")
    t0 = datetime(2026, 3, 30, 11, 0)
    mes_cli._copilot_tick(
        stamp=t0, cfg=mes_cli.load_mes_config(), journal=journal, side="auto",
        within=None, mes_csv=None, spy_csv=None, gatekeeper=None, past_context="",
        manager=None, manager_every=0.0, alert=False, no_log=False,
    )
    capsys.readouterr()
    journal.append_rule_skip(
        "2026-03-30", rule_id="inv_1", level="VWAP",
        reason="false break, still bull", as_of=t0,
    )
    mes_cli._copilot_tick(
        stamp=t0 + timedelta(minutes=1), cfg=mes_cli.load_mes_config(),
        journal=journal, side="auto", within=None, mes_csv=None, spy_csv=None,
        gatekeeper=None, past_context="", manager=None, manager_every=0.0,
        alert=False, no_log=False,
    )
    out2 = capsys.readouterr().out
    assert "RE-READ REQUIRED" not in out2


@pytest.mark.unit
def test_open_invalidation_blocks_auto_check(tmp_path, monkeypatch, capsys):
    snap = make_snapshot(mes=make_mes_series(close=98.5))
    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snap)
    journal = mes_cli._trade_journal(mes_cli.load_mes_config(), tmp_path)
    journal.save_standing_rules([_inv_rule()], reviewed_on="2026-03-29")
    calls: list = []

    def stub_check(**kwargs):
        calls.append(kwargs)
        return None, None, "Wait"

    monkeypatch.setattr(mes_cli, "_run_check_once", stub_check)
    mes_cli._copilot_tick(
        stamp=datetime(2026, 3, 30, 11, 0), cfg=mes_cli.load_mes_config(),
        journal=journal, side="auto", within=None, mes_csv=None, spy_csv=None,
        gatekeeper=object(), past_context="", manager=None, manager_every=0.0,
        alert=False, no_log=False, auto_check=True,
    )
    out = capsys.readouterr().out
    assert "not quiet" in out.lower() or "invalidation open" in out.lower()
    assert calls == []


# ---- Phase 3: thin Option D ----


@pytest.mark.unit
def test_hypothesis_persists_structured_frame(journal):
    journal.save_hypothesis(
        date="2026-03-30",
        hypothesis_markdown="**Day Type**: Trend Up\n...",
        day_type="Trend Up",
        bias="Bullish",
        machine_clauses=[
            {"kind": "invalidation", "id": "inv_1", "level": "vwap",
             "break_side": "below", "note": "Bull dies below VWAP."},
        ],
    )
    frame = journal.active_frame("2026-03-30")
    assert frame["day_type"] == "Trend Up"
    assert frame["bias"] == "Bullish"
    assert frame["machine_clauses"][0]["id"] == "inv_1"


@pytest.mark.unit
def test_rules_for_session_seeds_invalidation_from_frame(journal):
    journal.ack_blank_standing_rules(reviewed_on="2026-03-29", reason="seed from frame")
    journal.save_hypothesis(
        date="2026-03-30",
        hypothesis_markdown="bull day",
        day_type="Trend Up",
        bias="Bullish",
        machine_clauses=[
            {"kind": "invalidation", "id": "inv_1", "level": "vwap",
             "break_side": "below"},
            {"kind": "key_level", "level": "orb_top", "note": "OR high"},
        ],
    )
    rules = journal.rules_for_session("2026-03-30")
    kinds = [r["trigger"]["kind"] for r in rules]
    assert kinds == ["invalidation"]
    assert rules[0]["trigger"]["id"] == "inv_1"


@pytest.mark.unit
def test_morning_hypothesis_renders_machine_clauses():
    hyp = MorningHypothesis(
        day_type=DayType.TREND_UP,
        bias=MarketBias.BULLISH,
        one_sentence_thesis="Bull trend if breadth holds.",
        key_levels="VWAP 99, ORH 101",
        invalidation="Sustained close below VWAP.",
        confidence="medium",
        machine_clauses=[
            MachineClause(
                kind="invalidation", id="inv_1", level=RuleLevel.VWAP,
                break_side="below", note="Bull dies below VWAP.",
            ),
        ],
        narrative="Full plan.",
    )
    text = render_morning_hypothesis(hyp)
    assert "Frame Clauses" in text
    assert "vwap" in text.lower() or "VWAP" in text or "break below" in text
