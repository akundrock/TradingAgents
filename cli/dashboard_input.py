from __future__ import annotations

import select
import sys
import termios
import threading
import tty
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cli.intraday_display import IntradayDashboardBuffer


def _read_key(stdin) -> str:
    """Read a single key or escape sequence from stdin in cbreak mode."""
    ch = stdin.read(1)
    if ch != "\x1b":
        return ch
    ch2 = stdin.read(1)
    if ch2 != "[":
        return ch
    ch3 = stdin.read(1)
    if ch3.isdigit():
        digits = ch3
        while True:
            next_ch = stdin.read(1)
            if next_ch.isdigit():
                digits += next_ch
                continue
            if next_ch == "~":
                return f"\x1b[{digits}~"
            return f"\x1b[{digits}"
    return f"\x1b[{ch3}"


class DashboardInputHandler:
    """Background keyboard handler for the intraday live dashboard."""

    def __init__(self, buffer: IntradayDashboardBuffer):
        self.buffer = buffer
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._old_termios: list | None = None

    def start(self) -> None:
        if not sys.stdin.isatty():
            return
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="dashboard-input",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _run(self) -> None:
        fd = sys.stdin.fileno()
        try:
            self._old_termios = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            stdin = sys.stdin
            while not self._stop.is_set():
                ready, _, _ = select.select([stdin], [], [], 0.2)
                if not ready:
                    continue
                key = _read_key(stdin)
                self._handle_key(key)
        finally:
            if self._old_termios is not None:
                termios.tcsetattr(fd, termios.TCSADRAIN, self._old_termios)
                self._old_termios = None

    def _handle_key(self, key: str) -> None:
        if key in ("j", "\x1b[B"):
            self.buffer.select_relative(1)
        elif key in ("k", "\x1b[A"):
            self.buffer.select_relative(-1)
        elif key in ("g", "\x1b[H", "\x1b[1~"):
            self.buffer.select_first()
        elif key in ("\x1b[F", "\x1b[4~"):
            self.buffer.select_last()
        elif key in ("\r", "\n"):
            self.buffer.pin_current_symbol()
        elif key == "f":
            self.buffer.toggle_follow_mode()
