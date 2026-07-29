from __future__ import annotations

import os

import pytest

from cli.models import AnalystType
from cli.utils import expand_analyst_keys, normalize_analyst_order, resolve_premarket_analysts


@pytest.mark.unit
@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["market", "news"], ["market", "news"]),
        (["market,news,fundamentals"], ["market", "news", "fundamentals"]),
        (["market news"], ["market", "news"]),
        (["SOCIAL"], ["social"]),
    ],
)
def test_expand_analyst_keys(values, expected):
    assert expand_analyst_keys(values) == expected


@pytest.mark.unit
def test_normalize_analyst_order():
    assert normalize_analyst_order(["fundamentals", "market", "news"]) == [
        "market",
        "news",
        "fundamentals",
    ]


@pytest.mark.unit
def test_resolve_premarket_analysts_from_cli():
    config = {"intraday_premarket_analysts": ["market", "social", "news", "fundamentals"]}
    assert resolve_premarket_analysts(
        cli_analysts=["news", "market"],
        config=config,
        interactive=False,
    ) == ["market", "news"]


@pytest.mark.unit
def test_resolve_premarket_analysts_from_env(monkeypatch):
    monkeypatch.setenv(
        "TRADINGAGENTS_INTRADAY_PREMARKET_ANALYSTS",
        "market,news,fundamentals",
    )
    config = {"intraday_premarket_analysts": ["market", "social", "news", "fundamentals"]}
    assert resolve_premarket_analysts(
        cli_analysts=[],
        config=config,
        interactive=False,
    ) == ["market", "news", "fundamentals"]


@pytest.mark.unit
def test_resolve_premarket_analysts_cli_overrides_env(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_INTRADAY_PREMARKET_ANALYSTS", "social")
    config = {"intraday_premarket_analysts": ["market", "social", "news", "fundamentals"]}
    assert resolve_premarket_analysts(
        cli_analysts=["market"],
        config=config,
        interactive=False,
    ) == ["market"]


@pytest.mark.unit
def test_resolve_premarket_analysts_interactive(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_INTRADAY_PREMARKET_ANALYSTS", raising=False)
    config = {"intraday_premarket_analysts": ["market", "social", "news", "fundamentals"]}

    def _fake_select(_asset_type):
        return [AnalystType.MARKET, AnalystType.NEWS]

    monkeypatch.setattr("cli.utils.select_analysts", _fake_select)
    assert resolve_premarket_analysts(
        cli_analysts=[],
        config=config,
        interactive=True,
    ) == ["market", "news"]


@pytest.mark.unit
def test_resolve_premarket_analysts_rejects_unknown_key():
    config = {"intraday_premarket_analysts": ["market"]}
    with pytest.raises(ValueError, match="unknown analyst key"):
        resolve_premarket_analysts(
            cli_analysts=["not-an-analyst"],
            config=config,
            interactive=False,
        )
