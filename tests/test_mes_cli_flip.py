"""Tests for `mes flip` — journaling an intraday thesis flip (re-read)."""

from __future__ import annotations

from datetime import datetime

import pytest
from typer.testing import CliRunner

from cli.mes import mes_app
from tradingagents.mes.journal import MesJournal

runner = CliRunner()


@pytest.mark.unit
def test_flip_records_the_re_read(tmp_path):
    result = runner.invoke(mes_app, [
        "flip", "--reason", "15m under VWAP; trend frame dead",
        "--frame", "**Day Type**: Range", "--price", "6487.25", "--vwap", "6491.5",
        "--as-of", "14:09", "--date", "2026-03-30",
        "--journal-dir", str(tmp_path),
    ])
    assert result.exit_code == 0, result.output
    assert "flip" in result.output.lower()
    flips = MesJournal({"mes_journal_dir": str(tmp_path)}).load_flips("2026-03-30")
    assert len(flips) == 1
    record = flips[0]
    assert record["kind"] == "flip"
    assert record["as_of"][11:16] == "14:09"
    assert "trend frame dead" in record["reason"]
    assert record["new_frame"] == "**Day Type**: Range"
    assert record["price"] == pytest.approx(6487.25)
    assert record["vwap"] == pytest.approx(6491.5)


@pytest.mark.unit
def test_flip_without_a_frame_still_records_the_invalidation(tmp_path):
    result = runner.invoke(mes_app, [
        "flip", "--reason", "SPY broke the opening range low",
        "--as-of", "14:09", "--date", "2026-03-30",
        "--journal-dir", str(tmp_path),
    ])
    assert result.exit_code == 0, result.output
    flips = MesJournal({"mes_journal_dir": str(tmp_path)}).load_flips("2026-03-30")
    assert flips[0]["reason"].startswith("SPY broke")
    assert flips[0]["new_frame"] == ""


@pytest.mark.unit
def test_flip_requires_a_reason(tmp_path):
    result = runner.invoke(mes_app, [
        "flip", "--date", "2026-03-30", "--journal-dir", str(tmp_path),
    ])
    assert result.exit_code != 0


@pytest.mark.unit
def test_check_after_a_flip_feeds_the_new_frame_to_the_gatekeeper(tmp_path, monkeypatch):
    """After a journaled flip, live checks must run against the new frame."""
    from cli import mes as mes_cli
    from tests.mes_factories import make_snapshot

    snap = make_snapshot(as_of=datetime(2026, 3, 30, 15, 0))
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    journal.save_hypothesis(date="2026-03-30", hypothesis_markdown="**Day Type**: Trend Up")
    journal.append_flip(
        "2026-03-30", reason="VWAP lost and held", new_frame="Range day; fade extremes.",
        as_of=datetime(2026, 3, 30, 14, 9),
    )

    captured = {}

    def fake_gatekeeper(**kwargs):
        captured.update(kwargs)
        return "**Verdict**: Wait\n\nNot at level."

    monkeypatch.setattr(mes_cli, "DEFAULT_CONFIG", {"mes_journal_dir": str(tmp_path)})
    monkeypatch.setattr(mes_cli, "_make_llm", lambda config: object())
    monkeypatch.setattr(mes_cli, "create_mes_gatekeeper_agent", lambda llm: fake_gatekeeper)
    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(mes_cli, "_market_now", lambda cfg: datetime(2026, 3, 30, 15, 0))
    monkeypatch.setattr(mes_cli, "_past_context", lambda config: "")

    result = runner.invoke(mes_app, ["check", "--date", "2026-03-30"])
    assert result.exit_code == 0, result.output
    assert captured["hypothesis"].startswith("Range day")
    # The dead morning frame must not reach the gatekeeper.
    assert "Trend Up" not in captured["hypothesis"]


@pytest.mark.unit
def test_review_passes_journaled_flips_to_the_reviewer(tmp_path, monkeypatch):
    from cli import mes as mes_cli
    from tests.mes_factories import make_snapshot

    snap = make_snapshot(as_of=datetime(2026, 3, 30, 16, 0))
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    journal.save_hypothesis(date="2026-03-30", hypothesis_markdown="**Day Type**: Trend Up")
    journal.append_flip(
        "2026-03-30", reason="VWAP lost and held", new_frame="Range day; fade extremes.",
        as_of=datetime(2026, 3, 30, 14, 9),
    )
    captured = {}

    class FakeReviewAgent:
        def __call__(self, **kwargs):
            captured.update(kwargs)
            return "**Hypothesis Grade**: Correct\n\nReview text."

    monkeypatch.setattr(mes_cli, "_make_llm", lambda cfg: object())
    monkeypatch.setattr(mes_cli, "create_mes_review_agent", lambda llm: FakeReviewAgent())
    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(mes_cli, "_market_now", lambda cfg: datetime(2026, 3, 30, 17, 0))

    result = runner.invoke(mes_app, [
        "review", "--date", "2026-03-30", "--journal-dir", str(tmp_path), "--no-memory",
    ])
    assert result.exit_code == 0, result.output
    assert "Thesis Flips" in result.output
    assert "Range" in result.output
    assert "VWAP lost" in captured["flips_summary"]
