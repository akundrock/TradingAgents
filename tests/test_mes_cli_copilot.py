"""Tests for the `mes copilot` unified watch command."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from typer.testing import CliRunner

from cli.mes import mes_app
from tests.mes_factories import make_mes_series, make_snapshot

runner = CliRunner()


@pytest.fixture()
def patched_snapshot(monkeypatch):
    """Every snapshot build returns one flat bar closing at 100.0."""
    from cli import mes as mes_cli

    snap = make_snapshot(mes=make_mes_series(close=100.0))
    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(mes_cli, "_market_now", lambda cfg: datetime(2026, 3, 30, 11, 0))
    return snap


def _invoke(*args):
    return runner.invoke(mes_app, ["copilot", *args])


@pytest.mark.unit
def test_one_shot_flat_renders_radar(tmp_path, patched_snapshot):
    result = _invoke("--no-watch", "--no-llm", "--journal-dir", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert "MES Radar" in result.output  # radar panel title, same as `mes radar`


@pytest.mark.unit
def test_one_shot_open_trade_renders_mgmt_panel(tmp_path, patched_snapshot):
    trade_runner = CliRunner()
    trade_runner.invoke(
        mes_app, ["trade", "enter", "--side", "long", "--entry", "100.00",
                  "--stop", "98.00", "--journal-dir", str(tmp_path)]
    )
    result = _invoke("--no-watch", "--no-llm", "--journal-dir", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert "LONG" in result.output  # mgmt panel shows the trade side
    assert "MES Trade Manager" in result.output


@pytest.mark.unit
def test_one_shot_json_emits_mode(tmp_path, patched_snapshot):
    payload = _invoke("--no-watch", "--no-llm", "--journal-dir", str(tmp_path), "--json")
    assert payload.exit_code == 0
    assert '"mode"' in payload.output


# ---- Task 2 ----

from cli import mes as mes_cli  # after the existing imports
from tests.test_mes_management import snap_at  # controllable-OHLC snapshot helper


def _tick(journal, stamp, **overrides):
    kwargs = dict(
        stamp=stamp, cfg=mes_cli.load_mes_config(), journal=journal, side="auto",
        within=None, mes_csv=None, spy_csv=None, gatekeeper=None, past_context="",
        manager=None, manager_every=0.0, alert=False, no_log=False,
        auto_check=False, auto_check_cooldown=5.0,
        prev_state=None, last_fired=None, last_manager=None, seen=set(), live=None,
    )
    kwargs.update(overrides)
    return mes_cli._copilot_tick(**kwargs)


def _enter(tmp_path, side="long", entry="100.00", stop="98.00"):
    runner.invoke(mes_app, ["trade", "enter", "--side", side, "--entry", entry,
                            "--stop", stop, "--journal-dir", str(tmp_path)])


@pytest.mark.unit
def test_tick_flips_flat_to_managing_from_journal(tmp_path, patched_snapshot):
    journal = mes_cli._trade_journal(mes_cli.load_mes_config(), tmp_path)
    stamp = datetime(2026, 3, 30, 11, 0)
    assert _tick(journal, stamp)[0] == "flat"
    _enter(tmp_path)  # patched _market_now stamps the record 2026-03-30 11:00
    assert _tick(journal, stamp)[0] == "managing"


@pytest.mark.unit
def test_terminal_event_prints_close_hint_once(tmp_path, patched_snapshot, monkeypatch, capsys):
    journal = mes_cli._trade_journal(mes_cli.load_mes_config(), tmp_path)
    _enter(tmp_path)  # long 100.00, stop 98.00
    # Bar whose low touches the stop -> evaluate_management fires 'stopped_out'
    # (management.py _stop_fill: pure price logic, fills at min(stop, open)).
    snap = snap_at(97.0, high=97.75, low=97.0, as_of=datetime(2026, 3, 30, 11, 1))
    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snap)
    seen: set[str] = set()
    _tick(journal, datetime(2026, 3, 30, 11, 1), seen=seen, alert=True)
    out1 = capsys.readouterr().out
    assert "mes trade close" in out1
    assert "--reason stop" in out1
    _tick(journal, datetime(2026, 3, 30, 11, 2), seen=seen, alert=True)
    assert capsys.readouterr().out.count("mes trade close") == 0  # fire-once via seen


@pytest.mark.unit
def test_watch_loop_exits_cleanly_on_interrupt(tmp_path, patched_snapshot, monkeypatch):
    _enter(tmp_path)
    monkeypatch.setattr(
        mes_cli.time, "sleep", lambda _s: (_ for _ in ()).throw(KeyboardInterrupt)
    )
    result = _invoke("--no-llm", "--journal-dir", str(tmp_path), "--interval", "0")
    assert result.exit_code == 0, result.output


@pytest.mark.unit
def test_flat_auto_check_fires_once_per_cooldown(tmp_path, patched_snapshot, capsys):
    journal = mes_cli._trade_journal(mes_cli.load_mes_config(), tmp_path)
    t0 = datetime(2026, 3, 30, 11, 0)
    mode, _, _, _, last_fired, _ = _tick(journal, t0, auto_check=True)
    assert mode == "flat"
    assert capsys.readouterr().out.count("Deterministic Verdict") == 1  # first tick fires
    _tick(journal, t0 + timedelta(minutes=1), prev_state="flat", last_fired=t0,
          auto_check=True)
    assert capsys.readouterr().out.count("Deterministic Verdict") == 0  # inside cooldown
    _tick(journal, t0 + timedelta(minutes=5), prev_state="flat", last_fired=t0,
          auto_check=True)
    assert capsys.readouterr().out.count("Deterministic Verdict") == 1  # cooldown elapsed
