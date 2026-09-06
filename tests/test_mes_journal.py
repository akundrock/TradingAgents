from datetime import datetime as dt
from pathlib import Path

import pytest

from tradingagents.mes.checklist import evaluate
from tradingagents.mes.journal import MesJournal
from tradingagents.mes.management import OpenTrade

from tests.mes_factories import DEFAULT_AS_OF, make_snapshot

# The brief's test bodies call ``_dt(...)``; alias it to the datetime import.
_dt = dt


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


def _trade(**overrides) -> OpenTrade:
    fields = dict(
        side="long",
        contracts=1,
        remaining=1,
        entry=6500.0,
        stop=6498.0,
        initial_stop=6498.0,
        target=6504.0,
        entry_time=dt(2026, 3, 30, 10, 7),
        initial_risk_points=2.0,
        fired={},
        manual_events=[],
        realized_r=0.0,
    )
    fields.update(overrides)
    return OpenTrade(**fields)


@pytest.mark.unit
def test_trade_lifecycle_round_trip(journal):
    journal.append_trade_opened(_trade(), entry_context={"score": 7, "tier": "standard"})
    journal.append_trade_adjusted("2026-03-30", stop=6500.25, note="breakeven stop")

    open_trade = journal.find_open_trade("2026-03-30")
    assert open_trade is not None
    assert open_trade.entry == pytest.approx(6500.0)
    assert open_trade.side == "long"
    assert open_trade.stop == pytest.approx(6500.25)  # adjusted during replay
    assert open_trade.remaining == 1

    journal.append_trade_closed(
        _trade(stop=6500.25), exit_price=6503.0, reason="manual", as_of=_dt(2026, 3, 30, 11, 30)
    )
    assert journal.find_open_trade("2026-03-30") is None

    closed = [t for t in journal.load_trades("2026-03-30") if t["kind"] == "trade_closed"]
    assert closed[-1]["exit_price"] == 6503.0
    assert closed[-1]["reason"] == "manual"
    # realized: 3.0 pts / 2.0 risk = 1.5R
    assert closed[-1]["realized_r"] == pytest.approx(1.5)


@pytest.mark.unit
def test_trade_opened_captures_entry_context(journal):
    journal.append_trade_opened(_trade(), entry_context={"score": 7, "tier": "standard"})
    opened = [t for t in journal.load_trades("2026-03-30") if t["kind"] == "trade_opened"][0]
    assert opened["entry_context"] == {"score": 7, "tier": "standard"}
    assert opened["initial_risk_points"] == 2.0


@pytest.mark.unit
def test_find_open_trade_none_before_any_trade(journal):
    assert journal.find_open_trade("2020-01-01") is None


@pytest.mark.unit
def test_summarize_trades_lists_closed_results(journal):
    journal.append_trade_opened(_trade(), entry_context={"score": 7, "tier": "standard"})
    journal.append_trade_closed(_trade(realized_r=1.5), exit_price=6503.5, reason="manual", as_of=_dt(2026, 3, 30, 11, 0))

    summary = journal.summarize_trades("2026-03-30")
    assert "| Side | Entry | Exit | Reason | Realized R |" in summary
    assert "6503.50" in summary
    assert "manual" in summary


@pytest.mark.unit
def test_summarize_trades_empty(journal):
    assert journal.summarize_trades("2020-01-01") == "No trades logged for 2020-01-01."
