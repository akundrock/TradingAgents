"""Tests for the shared alert escalation (`cli.mes._fire_alert`).

BEL is the only audible in-band signal, and terminals that mute it (VS Code
by default) make `--alert` inaudible — so `_fire_alert` escalates to a macOS
Notification Center alert, or a user-supplied `MES_ALERT_COMMAND`.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from cli import mes as mes_cli


class _Tty:
    """sys.stdout stand-in: isatty() -> True, writes captured for assertions."""

    def __init__(self, target) -> None:
        self._target = target  # pytest's captured stream, so output still shows
        self.chunks: list[str] = []

    def write(self, chunk: str) -> int:
        self.chunks.append(chunk)
        return self._target.write(chunk)

    def flush(self) -> None:  # pragma: no cover - rich may call it
        self._target.flush()

    def isatty(self) -> bool:
        return True

    @property
    def text(self) -> str:
        return "".join(self.chunks)


def _force_tty(monkeypatch) -> _Tty:
    """Swap sys.stdout for a tty stand-in *inside the test body*.

    A fixture-phase swap does not survive into the call phase: pytest
    re-installs its capture stream (EncodedFile) after fixtures run, so the
    swap has to happen here to be seen by `_fire_alert`.
    """
    tty = _Tty(sys.stdout)
    monkeypatch.setattr(mes_cli.sys, "stdout", tty)
    return tty


@pytest.mark.unit
def test_fire_alert_prints_bell_and_macos_notification(monkeypatch):
    tty = _force_tty(monkeypatch)
    calls: list = []
    monkeypatch.setattr(mes_cli.subprocess, "run", lambda args, **k: calls.append(args))
    mes_cli._fire_alert("Trade over")
    assert "\x07" in tty.chunks  # bell still emitted for bell-capable terminals
    assert calls, "macOS notification should fire on darwin with a tty stdout"
    args = calls[0]
    assert args[0] == "osascript"
    script = args[2]  # ['-e', <apple script>] -> the script is argv[2]
    assert "display notification" in script
    assert "MES copilot" in script
    assert 'sound name "Glass"' in script  # default sound
    assert "Trade over" in script


@pytest.mark.unit
def test_fire_alert_sanitizes_quotes_and_backslashes(monkeypatch):
    tty = _force_tty(monkeypatch)
    calls: list = []
    monkeypatch.setattr(mes_cli.subprocess, "run", lambda args, **k: calls.append(args))
    mes_cli._fire_alert('Rule "ORB top" \\ check')
    script = calls[0][2]
    body = script.split('display notification "', 1)[1].split('" with title', 1)[0]
    assert "ORB top" in body  # message survives
    assert '"' not in body and "\\" not in body  # AppleScript-safe


@pytest.mark.unit
def test_fire_alert_custom_command_overrides_notification(monkeypatch):
    spawn_log: list = []
    monkeypatch.setattr(
        mes_cli.subprocess, "run", lambda args, **k: spawn_log.append(args)
    )
    monkeypatch.setenv("MES_ALERT_COMMAND", 'say "check the MES"')
    mes_cli._fire_alert("hello")
    assert spawn_log == ['say "check the MES"']


@pytest.mark.unit
def test_fire_alert_no_alert_env_skips_shelling_out(monkeypatch):
    tty = _force_tty(monkeypatch)

    def _boom(*args, **kwargs):
        raise AssertionError("must not shell out when MES_NO_ALERT is set")

    monkeypatch.setattr(mes_cli.subprocess, "run", _boom)
    monkeypatch.setenv("MES_NO_ALERT", "1")
    mes_cli._fire_alert("quiet")
    assert "\x07" in tty.chunks


@pytest.mark.unit
def test_fire_alert_skips_notification_when_not_a_tty(monkeypatch):
    """Captured output (tests, CI, pipes): never shell out to osascript."""
    spawn_log: list = []
    monkeypatch.setattr(mes_cli.subprocess, "run", lambda args, **k: spawn_log.append(args))
    monkeypatch.delenv("MES_ALERT_COMMAND", raising=False)
    mes_cli._fire_alert("pipework")
    assert spawn_log == []  # no notification attempted outside a real terminal


@pytest.mark.unit
def test_fire_alert_swallows_subprocess_failures(monkeypatch):
    def _boom(args, **kwargs):
        raise OSError("osascript missing")

    monkeypatch.setattr(mes_cli.subprocess, "run", _boom)
    tty = _force_tty(monkeypatch)
    mes_cli._fire_alert("still fine")  # must not raise
    assert "\x07" in tty.chunks