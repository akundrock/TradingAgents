"""Tests for the `mes copilot` unified watch command."""

from __future__ import annotations

import threading
import time
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
    assert result.output.count("MES Radar") == 1  # radar panel printed exactly once


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
        manager_job=None,
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
    mode, _, _, _, last_fired, _, _ = _tick(journal, t0, auto_check=True)
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
    _, _, _, _, _, last_manager, _ = _tick(
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


# ---- Auto-check repaint safety (core fix) ----


class _StubLive:
    """Live stand-in recording every call; never touches a real console."""

    def __init__(self, *args, **kwargs):
        self.calls: list[str] = []
        self.panels: list = []
        self.console = kwargs.get("console")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self, renderable, *, refresh=False):
        self.calls.append("update")
        self.panels.append(renderable)

    def stop(self):
        self.calls.append("stop")

    def start(self):
        self.calls.append("start")


@pytest.mark.unit
def test_manager_does_not_block_live_mgmt_panel(tmp_path, patched_snapshot, capsys):
    """Live tick paints the mgmt panel even while manager LLM is still running."""
    journal = mes_cli._trade_journal(mes_cli.load_mes_config(), tmp_path)
    _enter(tmp_path)
    started = threading.Event()
    release = threading.Event()
    calls: list[dict] = []

    def slow_manager(**kwargs):
        calls.append(kwargs)
        started.set()
        assert release.wait(timeout=2.0), "test release timed out"
        return "Slow advisory text"

    live = _StubLive()
    job: dict = {}
    t0 = datetime(2026, 3, 30, 11, 0)
    try:
        t_start = time.monotonic()
        _tick(
            journal, t0, manager=slow_manager, manager_every=5.0,
            live=live, manager_job=job,
        )
        elapsed = time.monotonic() - t_start
        assert elapsed < 0.5, f"tick blocked on manager ({elapsed:.2f}s)"
        assert started.wait(timeout=1.0)
        assert len(live.panels) >= 1
        # Outer Panel title is the copilot managing chrome — never advisory-only.
        assert "managing" in str(getattr(live.panels[0], "title", "")).lower()
        assert "Slow advisory" not in capsys.readouterr().out
        release.set()
        if job.get("future") is not None:
            job["future"].result(timeout=1.0)
        # Next tick drains the finished future via console.print (not Live replace).
        _tick(
            journal, t0 + timedelta(minutes=1), manager=slow_manager, manager_every=5.0,
            live=live, manager_job=job, last_manager=t0,
        )
        out = capsys.readouterr().out
        assert "Slow advisory" in out
        assert "Manager Advisory" in out
        for upd in live.panels:
            assert "Slow advisory" not in str(getattr(upd, "renderable", upd))
    finally:
        release.set()


@pytest.mark.unit
def test_manager_in_flight_skips_overlap_and_tick_still_updates(
    tmp_path, patched_snapshot, monkeypatch, capsys,
):
    """A second tick while manager is in flight still updates the mgmt panel."""
    journal = mes_cli._trade_journal(mes_cli.load_mes_config(), tmp_path)
    _enter(tmp_path, target="110.00")
    started = threading.Event()
    release = threading.Event()
    calls: list[dict] = []

    def slow_manager(**kwargs):
        calls.append(kwargs)
        started.set()
        assert release.wait(timeout=2.0)
        return "Still thinking"

    live = _StubLive()
    job: dict = {}
    try:
        t0 = datetime(2026, 3, 30, 11, 0)
        _tick(
            journal, t0, manager=slow_manager, manager_every=5.0,
            live=live, manager_job=job,
        )
        assert started.wait(timeout=1.0)
        assert len(calls) == 1
        n_panels = len(live.panels)

        # Ladder event on tick 2 while manager still running — must not wait on LLM.
        snap = snap_at(97.0, high=97.75, low=97.0, as_of=datetime(2026, 3, 30, 11, 1))
        monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snap)
        seen: set[str] = set()
        t_start = time.monotonic()
        _tick(
            journal, datetime(2026, 3, 30, 11, 1),
            manager=slow_manager, manager_every=0.01,  # would re-fire if not in-flight
            live=live, manager_job=job, last_manager=None, seen=seen,
        )
        assert time.monotonic() - t_start < 0.5
        assert len(calls) == 1  # overlap skip
        assert len(live.panels) > n_panels
        assert "mes trade close" in capsys.readouterr().out
        # Ladder state persisted with remaining / realized_r (mirror trade-status).
        adjusted = [e for e in journal.load_day("2026-03-30") if e["kind"] == "trade_adjusted"]
        assert adjusted
        assert adjusted[-1]["remaining"] is not None
        assert adjusted[-1]["realized_r"] is not None
    finally:
        release.set()


@pytest.mark.unit
def test_copilot_auto_check_prints_without_stopping_live(tmp_path, patched_snapshot, capsys):
    """The flat-tick auto-check must not stop/restart the Live.

    Printing through the live console inserts the checklist/verdict above the
    live region (same mechanism as the standing-rule banner), so the next
    panel repaint can never erase the verdict tail.
    """
    from tests.test_mes_radar import _render_table_to_text

    journal = mes_cli._trade_journal(mes_cli.load_mes_config(), tmp_path)
    t0 = datetime(2026, 3, 30, 11, 0)
    live = _StubLive()
    mode, _, _, _, last_fired, _, last_check = _tick(journal, t0, auto_check=True, live=live)
    assert mode == "flat"
    assert capsys.readouterr().out.count("Deterministic Verdict") == 1
    assert "stop" not in live.calls
    assert "start" not in live.calls
    assert last_fired == t0
    # The tick returns the summary and surfaces it in the panel immediately.
    assert last_check is not None
    assert "STAND DOWN" in last_check  # flat factory snapshot is not tradeable
    assert "last check:" in _render_table_to_text(live.panels[-1])


@pytest.mark.unit
def test_copilot_auto_check_prints_through_live_console(tmp_path, patched_snapshot, monkeypatch):
    """The fire prints via the same console the Live was constructed on —
    Rich inserts those prints above the live region (never overwrites it)."""
    journal = mes_cli._trade_journal(mes_cli.load_mes_config(), tmp_path)
    t0 = datetime(2026, 3, 30, 11, 0)
    captured: dict = {}

    def stub_check_once(**kwargs):
        captured["console"] = mes_cli.console
        return {}, "note", ""

    monkeypatch.setattr(mes_cli, "_run_check_once", stub_check_once)
    live = _StubLive(console=mes_cli.console)  # mirrors Live(console=console, ...)
    _tick(journal, t0, auto_check=True, live=live)
    assert captured["console"] is live.console  # check output shares the live console


@pytest.mark.unit
def test_copilot_last_check_summary_persists_across_ticks(tmp_path, patched_snapshot, capsys):
    """A later flat tick (no fire) still renders the previous check summary."""
    from tests.test_mes_radar import _render_table_to_text

    journal = mes_cli._trade_journal(mes_cli.load_mes_config(), tmp_path)
    t0 = datetime(2026, 3, 30, 11, 0)
    live = _StubLive()
    _, _, _, _, _, _, last_check = _tick(journal, t0, auto_check=True, live=live)
    capsys.readouterr()
    # Next tick, cooldown active: repaint only — the row must persist.
    _tick(journal, t0 + timedelta(minutes=1), prev_state="flat", last_fired=t0,
          auto_check=True, live=live, last_check=last_check)
    out = capsys.readouterr().out
    assert "Deterministic Verdict" not in out  # cooldown: no second fire
    assert live.panels, "live panel was updated"
    assert "last check:" in _render_table_to_text(live.panels[-1])

