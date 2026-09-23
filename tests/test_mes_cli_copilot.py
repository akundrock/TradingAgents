"""Tests for the `mes copilot` unified watch command."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from typer.testing import CliRunner

from cli.mes import mes_app
from tests.mes_factories import make_mes_series, make_snapshot, make_spy_series

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
from tradingagents.mes.checklist import evaluate
from tradingagents.mes.radar import build_proximity
from tradingagents.mes.rules import RuleHit


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


def _enter(tmp_path, side="long", entry="100.00", stop="98.00", target=None):
    args = ["trade", "enter", "--side", side, "--entry", entry,
            "--stop", stop, "--journal-dir", str(tmp_path)]
    if target is not None:
        args += ["--target", target]
    runner.invoke(mes_app, args)


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
def test_multi_event_close_hint_prints_once_per_terminal_tick(
    tmp_path, patched_snapshot, monkeypatch, capsys,
):
    """F1: a terminal tick that fires breakeven + partial prints the close hint
    once, not once per ladder event (cli/mes.py per-event loop)."""
    journal = mes_cli._trade_journal(mes_cli.load_mes_config(), tmp_path)
    _enter(tmp_path, target="110.00")  # far target so the fill check can't preempt
    # +2.75R bar (entry 100 / stop 98 / close 103.5) fires breakeven AND partial on
    # one tick; weak SPY internals (add=0.0, tick=0.0) flip confluence, so with
    # exit_on_confluence_loss=True the tick ends CLOSED carrying both ladder events
    # (management.py confluence-exit returns the ladder events it already fired).
    snap = make_snapshot(
        mes=make_mes_series(
            close=103.5, bar_kwargs={"open_": 100.5, "high": 103.75, "low": 100.25},
        ),
        spy=make_spy_series(internals=[(0.0, 0.0, 1000.0)] * 6),
        as_of=datetime(2026, 3, 30, 11, 1),
    )
    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snap)
    seen: set[str] = set()
    cfg = mes_cli.load_mes_config({"exit_on_confluence_loss": True})
    _tick(journal, datetime(2026, 3, 30, 11, 1), cfg=cfg, seen=seen, alert=True)
    out1 = capsys.readouterr().out
    assert "mes trade close" in out1
    assert out1.count("mes trade close") == 1  # RED: hint prints once per event today
    assert "--reason manual" in out1  # reason comes from the LAST event (partial)
    assert "--reason stop" not in out1
    # Next tick: journal replay restores the degradation-locked stop (fired resets),
    # and BE/partial re-fire to the same rounded stop -> zero new events, no hint.
    quiet = make_snapshot(
        mes=make_mes_series(
            close=103.5, atr=1.0,
            bar_kwargs={"open_": 103.5, "high": 103.75, "low": 103.25, "close": 103.5},
        ),
        as_of=datetime(2026, 3, 30, 11, 2),
    )
    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: quiet)
    _tick(journal, datetime(2026, 3, 30, 11, 2), cfg=cfg, seen=seen, alert=True)
    assert capsys.readouterr().out.count("mes trade close") == 0


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

@pytest.mark.unit
def test_manager_advice_throttled_by_interval(tmp_path, patched_snapshot, capsys):
    """Manager agent consults at most once per --manager-every minutes."""
    journal = mes_cli._trade_journal(mes_cli.load_mes_config(), tmp_path)
    _enter(tmp_path)  # long 100.00, stop 98.00
    calls: list[dict] = []

    def stub_manager(**kwargs):
        calls.append(kwargs)
        return "Hold the runner; nothing has changed."

    t0 = datetime(2026, 3, 30, 11, 0)
    # Tick 1: last_manager is None -> consult fires.
    _tick(journal, t0, manager=stub_manager, manager_every=5.0)
    assert len(calls) == 1 and calls[0]["current_price"] is not None
    # Tick 2 one minute later: 60s < 300s -> throttled.
    _, _, _, _, _, last_manager = _tick(
        journal, t0 + timedelta(minutes=1),
        manager=stub_manager, manager_every=5.0, last_manager=t0,
    )
    assert len(calls) == 1
    assert "Hold the runner" in capsys.readouterr().out  # tick 1's panel
    assert last_manager == t0  # throttle timestamp carries through unchanged


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
    out1 = capsys.readouterr().out
    assert out1.count("STANDING RULE triggered") == 1
    assert len(journal.load_rule_fires("2026-03-30")) == 1
    _tick(journal, t0 + timedelta(minutes=1), prev_state="flat", last_fired=t0)
    # Tick 2: same hit, but the per-session dedup prevents a second trigger
    # (only the open reminder prints, never a second trigger line).
    out2 = capsys.readouterr().out
    assert "STANDING RULE triggered" not in out2
    assert "LOG A CHECK NOW" in out2  # open banner still reminds
    assert len(journal.load_rule_fires("2026-03-30")) == 1


@pytest.mark.unit
def test_skip_resolves_the_banner(tmp_path, patched_snapshot, capsys):
    journal = mes_cli._trade_journal(mes_cli.load_mes_config(), tmp_path)
    journal.save_standing_rules([RULE], reviewed_on="2026-03-30")
    t0 = datetime(2026, 3, 30, 11, 0)
    _tick(journal, t0)
    capsys.readouterr()  # drain tick 1
    journal.append_rule_skip("2026-03-30", rule_id="orb_top", level="ORB high",
                             reason="chop", as_of=t0)
    _tick(journal, t0 + timedelta(minutes=1))
    out2 = capsys.readouterr().out
    assert "LOG A CHECK NOW" not in out2  # skip resolved the fire
    assert "STANDING RULE triggered" not in out2  # and no re-fire


@pytest.mark.unit
def test_radar_panel_renders_open_rule_banner():
    from tests.test_mes_radar import _render_table_to_text

    hit = RuleHit(rule_id="orb_top", level="ORB high", level_price=101.0,
                  distance=-1.0, tolerance=2.0, confirmation="none",
                  note="Fade the first ORB-top retest.")
    snap = make_snapshot(as_of=datetime(2026, 3, 30, 11, 0))
    report = build_proximity(snap, evaluate(snap, "auto"))
    text = _render_table_to_text(
        mes_cli._render_radar(report, datetime(2026, 3, 30, 11, 0), rule_hits=[hit])
    )
    assert "LOG A CHECK NOW" in text
    assert "mes skip" in text

