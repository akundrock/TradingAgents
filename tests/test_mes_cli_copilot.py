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
