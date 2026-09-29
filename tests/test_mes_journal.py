from datetime import datetime as dt
from pathlib import Path

import pytest

from tradingagents.mes.checklist import evaluate
from tradingagents.mes.journal import MesJournal
from tradingagents.mes.management import OpenTrade

from tests.mes_factories import DEFAULT_AS_OF, make_mes_series, make_snapshot

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
def test_check_record_captures_opening_range_bounds(journal):
    snapshot = make_snapshot()
    journal.append_check(snapshot=snapshot, result=evaluate(snapshot, "long"))

    record = journal.load_checks("2026-03-30")[0]
    assert record["orb_high"] == 101.0
    assert record["orb_low"] == 98.0


@pytest.mark.unit
def test_check_record_tolerates_missing_opening_range(journal):
    snapshot = make_snapshot(mes=make_mes_series(opening_range_high=None, opening_range_low=None))
    journal.append_check(snapshot=snapshot, result=evaluate(snapshot, "long"))

    record = journal.load_checks("2026-03-30")[0]
    assert record["orb_high"] is None
    assert record["orb_low"] is None


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
    assert "| Time | Side | Frame | Score | Tier |" in summary
    assert f"{result.score}/{result.max_score}" in summary
    assert result.tier in summary
    assert "11:00" in summary
    assert "Heading" in summary


@pytest.mark.unit
def test_summarize_checks_with_no_checks(journal):
    assert journal.summarize_checks("2020-01-01") == "No checks logged for 2020-01-01."


@pytest.mark.unit
def test_summarize_checks_shows_location_and_frame(journal):
    journal.save_hypothesis(date="2026-03-30", hypothesis_markdown="Trend up")
    pre = make_snapshot(mes=make_mes_series(close=101.5))
    journal.append_check(snapshot=pre, result=evaluate(pre, "long"), verdict_markdown="**Verdict**: Wait")
    journal.append_flip(
        "2026-03-30",
        reason="VWAP lost on rolling internals",
        new_frame="",
        price=101.5,
        vwap=99.0,
    )
    post = make_snapshot(as_of=DEFAULT_AS_OF.replace(hour=13, minute=30), mes=make_mes_series(close=97.5))
    journal.append_check(snapshot=post, result=evaluate(post, "long"), verdict_markdown="**Verdict**: Wait")

    summary = journal.summarize_checks("2026-03-30")
    assert "| Time | Side | Frame | Score | Tier | Gates | Tradeable | Location | Verdict |" in summary
    assert "+2.50 vs VWAP · above OR-H" in summary
    assert "-1.50 vs VWAP · below OR-L" in summary
    assert "morning" in summary
    assert "flip 1" in summary


@pytest.mark.unit
def test_summarize_checks_location_handles_legacy_records(journal):
    # A record from before OR capture: no orb_high/orb_low keys at all.
    journal.directory.mkdir(parents=True, exist_ok=True)
    legacy = (
        '{"kind": "check", "logged_at": "2026-03-30T11:00:00", "as_of": "2026-03-30T11:00:00",'
        ' "session_date": "2026-03-30", "side": "long", "score": 6, "max_score": 10,'
        ' "tier": "standard", "gates_ok": true, "tradeable": true,'
        ' "last_price": 100.0, "vwap": 99.0, "verdict": "**Verdict**: Wait"}\n'
    )
    (journal.directory / "2026-03-30.jsonl").open("a", encoding="utf-8").write(legacy)

    summary = journal.summarize_checks("2026-03-30")
    assert "+1.00 vs VWAP" in summary  # VWAP context still renders
    assert "OR" not in summary  # OR context absent, not guessed


@pytest.mark.unit
def test_summarize_checks_frame_is_dash_before_any_frame_record(journal):
    snapshot = make_snapshot()
    journal.append_check(snapshot=snapshot, result=evaluate(snapshot, "long"))

    summary = journal.summarize_checks("2026-03-30")
    assert "| - |" in summary  # no hypothesis/flip journaled yet


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


def _fire(journal, date, rule_id, hour, minute):
    stamp = _dt(2026, 3, 30, hour, minute)
    journal.append_rule_fired(
        date, rule_id=rule_id, level=rule_id, confirmation="none",
        tolerance=2.0, last_price=100.0, distance=0.0, as_of=stamp,
    )
    return stamp


@pytest.mark.unit
def test_compliance_counts_check_and_skip(journal):
    _fire(journal, "2026-03-30", "orb_top", 10, 0)
    journal.append_rule_skip(
        "2026-03-30", rule_id="orb_top", level="ORB high", reason="chop",
        as_of=_dt(2026, 3, 30, 10, 40),
    )
    _fire(journal, "2026-03-30", "vwap", 13, 0)
    snapshot = make_snapshot(as_of=_dt(2026, 3, 30, 13, 5))
    journal.append_check(snapshot=snapshot, result=evaluate(snapshot, "auto"))
    tally = journal.rule_compliance("2026-03-30")
    assert tally["triggered"] == 2
    assert tally["skipped"] == 1
    assert tally["checked"] == 1
    assert tally["missed"] == 0


@pytest.mark.unit
def test_unresolved_fire_is_open_then_missed_after_grace(journal):
    _fire(journal, "2026-03-30", "orb_top", 10, 35)
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
    _fire(journal, "2026-03-30", "orb_top", 10, 35)
    late = journal.rule_compliance("2026-03-30", resolve_as_of=_dt(2026, 3, 30, 11, 0))
    # The 10:30 check predates the 10:35 fire; it cannot honor it.
    assert late["triggered"] == 1 and late["missed"] == 1


@pytest.mark.unit
def test_skip_by_other_rule_does_not_resolve(journal):
    _fire(journal, "2026-03-30", "orb_top", 10, 35)
    journal.append_rule_skip(
        "2026-03-30", rule_id="vwap", level="VWAP", reason="different level",
        as_of=_dt(2026, 3, 30, 10, 40),
    )
    late = journal.rule_compliance("2026-03-30", resolve_as_of=_dt(2026, 3, 30, 11, 0))
    assert late["missed"] == 1  # a skip for another rule does not cover this fire


@pytest.mark.unit
def test_summarize_rule_compliance_lines(journal):
    _fire(journal, "2026-03-30", "orb_top", 10, 35)
    journal.append_rule_skip("2026-03-30", rule_id="orb_top", level="ORB high",
                             reason="chop", as_of=_dt(2026, 3, 30, 10, 38))
    summary = journal.summarize_rule_compliance("2026-03-30")
    assert "orb_top" in summary
    assert "skipped" in summary


# ---------------------------------------------------------------------------
# Thesis flips (intraday re-reads)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_flip_round_trip_records_evidence_and_new_frame(journal):
    journal.append_flip(
        "2026-03-30",
        reason="15m under VWAP with $ADD rolling negative — trend frame is dead.",
        new_frame="**Day Type**: Range. Bias: neutral; fade extremes.",
        as_of=_dt(2026, 3, 30, 14, 9),
        price=6487.25,
        vwap=6491.5,
    )

    flips = journal.load_flips("2026-03-30")
    assert len(flips) == 1
    record = flips[0]
    assert record["kind"] == "flip"
    assert record["session_date"] == "2026-03-30"
    assert record["as_of"][11:16] == "14:09"
    assert "trend frame is dead" in record["reason"]
    assert record["new_frame"] == "**Day Type**: Range. Bias: neutral; fade extremes."
    assert record["price"] == pytest.approx(6487.25)
    assert record["vwap"] == pytest.approx(6491.5)


@pytest.mark.unit
def test_flip_defaults_work_without_optional_evidence(journal):
    journal.append_flip("2026-03-30", reason="SPY broke the opening range low.")
    record = journal.load_flips("2026-03-30")[0]
    assert record["new_frame"] == ""
    assert record["price"] is None
    assert record["vwap"] is None
    assert record["as_of"]  # stamps now when as_of omitted


@pytest.mark.unit
def test_active_frame_prefers_the_latest_flip(journal):
    journal.save_hypothesis(date="2026-03-30", hypothesis_markdown="**Day Type**: Trend Up")
    journal.append_flip(
        "2026-03-30", reason="VWAP lost and held.", new_frame="Range day; fade extremes.",
        as_of=_dt(2026, 3, 30, 14, 9),
    )
    frame = journal.active_frame("2026-03-30")
    assert frame["kind"] == "flip"
    assert frame["new_frame"].startswith("Range day")


@pytest.mark.unit
def test_active_frame_without_flips_is_the_morning_hypothesis(journal):
    journal.save_hypothesis(date="2026-03-30", hypothesis_markdown="**Day Type**: Trend Up")
    assert journal.active_frame("2026-03-30")["kind"] == "hypothesis"


@pytest.mark.unit
def test_active_frame_on_an_empty_day_is_none(journal):
    assert journal.active_frame("2020-01-01") is None


@pytest.mark.unit
def test_load_flips_on_an_empty_day_is_empty(journal):
    assert journal.load_flips("2020-01-01") == []


@pytest.mark.unit
def test_summarize_flips_lists_the_timeline(journal):
    journal.save_hypothesis(date="2026-03-30", hypothesis_markdown="**Day Type**: Trend Up")
    journal.append_flip(
        "2026-03-30",
        reason="15m under VWAP; $ADD rolled negative.",
        new_frame="## Range\nFade extremes.",
        as_of=_dt(2026, 3, 30, 14, 9),
    )
    summary = journal.summarize_flips("2026-03-30")
    assert "| Time | New Frame | What Killed The Old Frame |" in summary
    assert "14:09" in summary
    assert "VWAP lost" in summary or "under VWAP" in summary
    assert "Range" in summary


@pytest.mark.unit
def test_summarize_flips_is_empty_without_flips(journal):
    assert journal.summarize_flips("2020-01-01") == ""


# ---------------------------------------------------------------------------
# Ladder-state replay (find_open_trade)
# ---------------------------------------------------------------------------


def _opened_trade() -> OpenTrade:
    return OpenTrade(
        side="long", contracts=1, remaining=1,
        entry=100.0, stop=98.0, initial_stop=98.0, target=102.0,
        entry_time=_dt(2026, 3, 30, 10, 0), initial_risk_points=2.0,
    )


@pytest.mark.unit
def test_find_open_trade_replays_ladder_state(journal):
    """Ladder markers, remaining, and banked R persist through trade_adjusted records."""
    journal.append_trade_opened(_opened_trade(), entry_context={"score": 7, "tier": "standard"})
    # The ladder fires breakeven, then the target fill on a later tick.
    journal.append_trade_adjusted(
        "2026-03-30",
        stop=100.25,
        note="breakeven: stop 98.00 -> 100.25 (BE at +1.00R)",
        as_of="2026-03-30T10:30",
        ladder_fired={"breakeven": "2026-03-30T10:30"},
        remaining=1,
        realized_r=0.0,
    )
    journal.append_trade_adjusted(
        "2026-03-30",
        note="target: target filled at 102.00",
        as_of="2026-03-30T10:45",
        ladder_fired={"target": "2026-03-30T10:45"},
        remaining=0,
        realized_r=1.0,
    )

    reopened = journal.find_open_trade("2026-03-30")
    assert reopened is not None
    assert reopened.remaining == 0                     # ladder state survives rebuild
    assert reopened.fired.get("target") == "2026-03-30T10:45"
    assert reopened.fired.get("breakeven") == "2026-03-30T10:30"
    assert reopened.realized_r == pytest.approx(1.0)


@pytest.mark.unit
def test_terminal_fill_does_not_refire_on_replay(journal):
    """A journaled target fill must not re-fire when the trade is rebuilt."""
    journal.append_trade_opened(_opened_trade(), entry_context={"score": 7, "tier": "standard"})
    journal.append_trade_adjusted(
        "2026-03-30",
        note="target: target filled at 102.00",
        as_of="2026-03-30T10:45",
        ladder_fired={"target": "2026-03-30T10:45"},
        remaining=0,
        realized_r=1.0,
    )

    first = journal.find_open_trade("2026-03-30")
    assert first is not None and first.remaining == 0
    # Replaying again (the next tick's rebuild) yields the same persisted state.
    second = journal.find_open_trade("2026-03-30")
    assert second is not None
    assert second.remaining == 0
    assert second.realized_r == pytest.approx(1.0)
    assert first.fired == second.fired
