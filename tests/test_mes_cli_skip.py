"""Tests for `mes skip` — acknowledging a standing-rule trigger without a check."""

from __future__ import annotations

from datetime import datetime

import pytest
from typer.testing import CliRunner

from cli.mes import mes_app
from tests.mes_factories import make_mes_series, make_snapshot
from tradingagents.agents.schemas import SessionReview, StandingRule
from tradingagents.mes.checklist import evaluate
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
    assert "Skipped orb_top" in result.output
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


@pytest.mark.unit
def test_review_saves_emitted_standing_rules(tmp_path, monkeypatch):
    from cli import mes as mes_cli
    from tests.mes_factories import make_snapshot

    snap = make_snapshot(as_of=datetime(2026, 3, 30, 16, 0))
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
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