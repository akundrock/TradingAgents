"""Tests for the `mes trade` CLI command group."""

from __future__ import annotations

from datetime import datetime

import pytest
from typer.testing import CliRunner

from cli.mes import mes_app, trade_app
from tests.mes_factories import make_mes_series, make_snapshot
from tradingagents.mes.journal import MesJournal

runner = CliRunner()


@pytest.fixture()
def patched_snapshot(monkeypatch):
    """Every snapshot build returns one flat bar closing at 100.0."""
    from cli import mes as mes_cli

    snap = make_snapshot(mes=make_mes_series(close=100.0))
    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(mes_cli, "_market_now", lambda cfg: datetime(2026, 3, 30, 11, 0))
    return snap


@pytest.fixture()
def journal(tmp_path, monkeypatch):
    return MesJournal({"mes_journal_dir": str(tmp_path)})


def _invoke(*args):
    return runner.invoke(trade_app, list(args))


@pytest.mark.unit
def test_enter_writes_opened_record(tmp_path, patched_snapshot):
    result = _invoke(
        "enter", "--side", "long", "--contracts", "1",
        "--entry", "100.00", "--stop", "98.00", "--target", "102.00",
        "--journal-dir", str(tmp_path),
    )
    assert result.exit_code == 0, result.output
    assert "trade opened" in result.output.lower()
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    records = [e for e in journal.load_day("2026-03-30") if e["kind"] == "trade_opened"]
    assert len(records) == 1
    assert records[0]["side"] == "long"
    assert records[0]["entry_context"]["tier"]  # checklist context captured


@pytest.mark.unit
def test_enter_refuses_second_trade(tmp_path, patched_snapshot):
    _invoke("enter", "--side", "long", "--entry", "100.00", "--stop", "98.00",
            "--journal-dir", str(tmp_path))
    second = _invoke("enter", "--side", "long", "--entry", "100.50", "--stop", "99.00",
                     "--journal-dir", str(tmp_path))
    assert second.exit_code != 0
    assert "already open" in second.output.lower()


@pytest.mark.unit
def test_status_reports_open_trade_without_llm(tmp_path, patched_snapshot):
    _invoke("enter", "--side", "long", "--entry", "100.00", "--stop", "98.00",
            "--journal-dir", str(tmp_path))
    status = _invoke("status", "--no-watch", "--no-llm", "--journal-dir", str(tmp_path))
    assert status.exit_code == 0
    assert "LONG" in status.output


@pytest.mark.unit
def test_status_json_emits_report_fields(tmp_path, patched_snapshot):
    _invoke("enter", "--side", "long", "--entry", "100.0", "--stop", "98.0",
            "--journal-dir", str(tmp_path))
    payload = _invoke("status", "--no-watch", "--json", "--journal-dir", str(tmp_path))
    assert payload.exit_code == 0
    assert '"recommendation"' in payload.output
    assert '"r_now"' in payload.output


@pytest.mark.unit
def test_close_records_exit_and_clears_open(tmp_path, patched_snapshot):
    _invoke("enter", "--side", "long", "--entry", "100.00", "--stop", "98.00",
            "--journal-dir", str(tmp_path))
    closed = _invoke("close", "--price", "101.50", "--reason", "manual",
                     "--journal-dir", str(tmp_path))
    assert closed.exit_code == 0
    assert "closed" in closed.output.lower()

    status = _invoke("status", "--no-watch", "--no-llm", "--journal-dir", str(tmp_path))
    assert "no open trade" in status.output.lower()


@pytest.mark.unit
def test_close_without_open_trade_fails(tmp_path, patched_snapshot):
    result = _invoke("close", "--price", "101.00", "--reason", "manual",
                     "--journal-dir", str(tmp_path))
    assert result.exit_code != 0


@pytest.mark.unit
def test_adjust_refuses_to_loosen_stop(tmp_path, patched_snapshot):
    _invoke("enter", "--side", "long", "--entry", "100.00", "--stop", "98.00",
            "--journal-dir", str(tmp_path))
    loosened = _invoke("adjust", "--stop", "97.00", "--journal-dir", str(tmp_path))
    assert "refus" in loosened.output.lower() or "kept" in loosened.output.lower()


# --- Default-stop derivation (units bug regression tests) -------------------


def _opened_records(tmp_path) -> list[dict]:
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    return [e for e in journal.load_day("2026-03-30") if e["kind"] == "trade_opened"]


@pytest.mark.unit
def test_enter_default_stop_is_beyond_entry_for_short(tmp_path, patched_snapshot):
    """Without --stop, a short's default stop must be a PRICE above entry, not a distance."""
    result = _invoke(
        "enter", "--side", "short", "--contracts", "1",
        "--entry", "100.00",
        "--journal-dir", str(tmp_path),
    )
    assert result.exit_code == 0, result.output
    records = _opened_records(tmp_path)
    assert len(records) == 1
    opened = records[0]
    assert opened["stop"] > opened["entry"]
    assert opened["initial_risk_points"] < 50  # sane points, not |100 - 5.04|


@pytest.mark.unit
def test_enter_default_stop_is_beyond_entry_for_long(tmp_path, patched_snapshot):
    result = _invoke(
        "enter", "--side", "long", "--contracts", "1",
        "--entry", "100.00",
        "--journal-dir", str(tmp_path),
    )
    assert result.exit_code == 0, result.output
    records = _opened_records(tmp_path)
    assert len(records) == 1
    assert records[0]["stop"] < records[0]["entry"]
    assert records[0]["initial_risk_points"] < 50


@pytest.mark.unit
def test_enter_rejects_stop_on_wrong_side_of_entry(tmp_path, patched_snapshot):
    result = _invoke(
        "enter", "--side", "short", "--entry", "100.00", "--stop", "98.00",
        "--journal-dir", str(tmp_path),
    )
    assert result.exit_code != 0
    assert _opened_records(tmp_path) == []


@pytest.mark.unit
def test_enter_journals_stop_provenance(tmp_path, patched_snapshot):
    result = _invoke(
        "enter", "--side", "long", "--contracts", "1",
        "--entry", "100.00",
        "--journal-dir", str(tmp_path),
    )
    assert result.exit_code == 0, result.output
    records = _opened_records(tmp_path)
    assert len(records) == 1
    ctx = records[0]["entry_context"]
    for key in ("atr", "vwap_distance", "stop_distance_points", "stop_atr_multiple", "stop_anchor"):
        assert key in ctx, key
    assert ctx["atr"] > 0
    assert ctx["stop_distance_points"] > 0
    assert ctx["stop_atr_multiple"] > 0
    assert ctx["stop_anchor"]


@pytest.mark.unit
def test_review_flags_still_open_trade(tmp_path, patched_snapshot, monkeypatch):
    from datetime import datetime as _dt

    from cli import mes as mes_cli
    from tradingagents.mes.management import OpenTrade

    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    trade = OpenTrade(
        side="long", contracts=1, remaining=1,
        entry=100.0, stop=98.0, initial_stop=98.0, target=102.0,
        entry_time=datetime(2026, 3, 30, 10, 7), initial_risk_points=2.0,
    )
    journal.append_trade_opened(trade, entry_context={"score": 7, "tier": "standard"})

    # review builds its journal from DEFAULT_CONFIG; point it at tmp_path so
    # this test never touches the real journal.
    import tradingagents.default_config as dc
    from cli import mes as mes_cli

    monkeypatch.setattr(
        mes_cli, "DEFAULT_CONFIG",
        {**dc.DEFAULT_CONFIG, "mes_journal_dir": str(tmp_path)},
    )

    result = CliRunner().invoke(mes_app, ["review", "--date", "2026-03-30", "--no-llm"])
    assert result.exit_code == 0
    assert "still marked open" in result.output.lower()
