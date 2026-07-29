from __future__ import annotations

import logging

import pytest

from tradingagents.logging_config import configure_logging


@pytest.mark.unit
def test_configure_logging_defaults_to_warning(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_LOG_LEVEL", raising=False)
    assert configure_logging() == "WARNING"
    assert logging.getLogger().level == logging.WARNING
    assert logging.getLogger("tradingagents").level == logging.WARNING


@pytest.mark.unit
def test_configure_logging_respects_explicit_level():
    assert configure_logging("DEBUG") == "DEBUG"
    assert logging.getLogger().level == logging.DEBUG


@pytest.mark.unit
def test_configure_logging_respects_env(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_LOG_LEVEL", "INFO")
    assert configure_logging() == "INFO"


@pytest.mark.unit
def test_configure_logging_cli_overrides_env(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_LOG_LEVEL", "ERROR")
    assert configure_logging("DEBUG") == "DEBUG"


@pytest.mark.unit
def test_configure_logging_rejects_invalid_level():
    with pytest.raises(ValueError, match="invalid log level"):
        configure_logging("VERBOSE")
