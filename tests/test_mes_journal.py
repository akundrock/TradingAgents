from pathlib import Path

import pytest

from tradingagents.mes.checklist import evaluate
from tradingagents.mes.journal import MesJournal

from tests.mes_factories import DEFAULT_AS_OF, make_snapshot


@pytest.fixture()
def journal(tmp_path):
    return MesJournal({"mes_journal_dir": str(tmp_path)})


@pytest.mark.unit
def test_directory_defaults_to_results_dir_subfolder(tmp_path):
    assert MesJournal({"results_dir": str(tmp_path)}).directory == tmp_path / "mes_journal"


@pytest.mark.unit
def test_explicit_directory_wins(tmp_path):
    assert MesJournal({"mes_journal_dir": str(tmp_path)}).directory == tmp_path


@pytest.mark.unit
def test_directory_falls_back_to_cwd_when_config_is_empty():
    assert MesJournal(None).directory == Path(".") / "mes_journal"


@pytest.mark.unit
def test_hypothesis_round_trip(journal):
    journal.save_hypothesis(
        date="2026-03-30",
        hypothesis_markdown="**Day Type**: Trend Up",
        market_context="$ADD +1200",
    )
    loaded = journal.load_hypothesis("2026-03-30")
    assert loaded["kind"] == "hypothesis"
    assert loaded["hypothesis"] == "**Day Type**: Trend Up"
    assert loaded["market_context"] == "$ADD +1200"
    assert loaded["session_date"] == "2026-03-30"


@pytest.mark.unit
def test_last_hypothesis_wins(journal):
    journal.save_hypothesis(date="2026-03-30", hypothesis_markdown="first")
    journal.save_hypothesis(date="2026-03-30", hypothesis_markdown="second")
    assert journal.load_hypothesis("2026-03-30")["hypothesis"] == "second"


@pytest.mark.unit
def test_missing_day_reads_as_empty(journal):
    assert journal.load_day("2020-01-01") == []
    assert journal.load_checks("2020-01-01") == []
    assert journal.load_hypothesis("2020-01-01") is None


@pytest.mark.unit
def test_checks_are_appended_in_order(journal):
    for minute in (0, 15, 30):
        snapshot = make_snapshot(as_of=DEFAULT_AS_OF.replace(minute=minute))
        journal.append_check(
            snapshot=snapshot,
            result=evaluate(snapshot, "long"),
            verdict_markdown=f"**Verdict**: Wait ({minute})",
        )

    checks = journal.load_checks("2026-03-30")
    assert [c["as_of"][11:16] for c in checks] == ["11:00", "11:15", "11:30"]
    assert all(c["kind"] == "check" for c in checks)


@pytest.mark.unit
def test_check_record_captures_the_result_and_internals(journal):
    snapshot = make_snapshot()
    result = evaluate(snapshot, "long")
    journal.append_check(
        snapshot=snapshot,
        result=result,
        verdict_markdown="**Verdict**: Stand Down",
        sizing={"contracts": 2},
    )

    record = journal.load_checks("2026-03-30")[0]
    assert record["side"] == result.side
    assert record["score"] == result.score
    assert record["tier"] == result.tier
    assert record["tradeable"] is result.tradeable
    assert record["internals"] == {
        "add": snapshot.add,
        "tick": snapshot.tick,
        "vold": snapshot.vold,
    }
    assert record["sizing"] == {"contracts": 2}
    assert len(record["items"]) == len(result.all_items)


@pytest.mark.unit
def test_load_day_returns_both_record_kinds(journal):
    snapshot = make_snapshot()
    journal.save_hypothesis(date="2026-03-30", hypothesis_markdown="plan")
    journal.append_check(snapshot=snapshot, result=evaluate(snapshot, "long"))

    entries = journal.load_day("2026-03-30")
    assert [e["kind"] for e in entries] == ["hypothesis", "check"]


@pytest.mark.unit
def test_available_dates_are_sorted(journal):
    for date in ("2026-03-31", "2026-03-30", "2026-04-01"):
        journal.save_hypothesis(date=date, hypothesis_markdown="x")
    assert journal.available_dates() == ["2026-03-30", "2026-03-31", "2026-04-01"]


@pytest.mark.unit
def test_available_dates_is_empty_before_anything_is_written(tmp_path):
    assert MesJournal({"mes_journal_dir": str(tmp_path / "nope")}).available_dates() == []


@pytest.mark.unit
def test_malformed_lines_are_skipped(journal):
    journal.save_hypothesis(date="2026-03-30", hypothesis_markdown="good")
    (journal.directory / "2026-03-30.jsonl").open("a").write("{not json}\n")
    assert len(journal.load_day("2026-03-30")) == 1


@pytest.mark.unit
def test_summarize_checks_contains_score_and_tier(journal):
    snapshot = make_snapshot()
    result = evaluate(snapshot, "long")
    journal.append_check(
        snapshot=snapshot,
        result=result,
        verdict_markdown="## Heading\n**Verdict**: Wait",
    )

    summary = journal.summarize_checks("2026-03-30")
    assert "| Time | Side | Score | Tier |" in summary
    assert f"{result.score}/{result.max_score}" in summary
    assert result.tier in summary
    assert "11:00" in summary
    assert "Heading" in summary


@pytest.mark.unit
def test_summarize_checks_with_no_checks(journal):
    assert journal.summarize_checks("2020-01-01") == "No checks logged for 2020-01-01."
