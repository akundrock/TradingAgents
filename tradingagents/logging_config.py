"""Central logging configuration for TradingAgents CLI and library use."""

from __future__ import annotations

import logging
import os

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def configure_logging(level: str | int | None = None) -> str:
    """Configure root and ``tradingagents`` loggers.

    Resolution order when ``level`` is None: ``TRADINGAGENTS_LOG_LEVEL`` env,
    then ``WARNING``. Returns the resolved level name (e.g. ``"INFO"``).
    """
    if level is None:
        level = os.environ.get("TRADINGAGENTS_LOG_LEVEL", "WARNING")

    if isinstance(level, str):
        normalized = level.strip().upper()
        if normalized not in _LEVELS:
            raise ValueError(
                f"invalid log level {level!r}; expected one of "
                f"{', '.join(_LEVELS)}"
            )
        numeric = _LEVELS[normalized]
        label = normalized
    else:
        numeric = int(level)
        label = logging.getLevelName(numeric)

    logging.basicConfig(
        level=numeric,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("tradingagents").setLevel(numeric)
    return label
