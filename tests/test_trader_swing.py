# tests/test_trader_swing.py
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.agents.schemas import SwingTradeProposal, TraderProposal
from tradingagents.agents.trader.trader import create_trader


def _swing_state() -> dict:
    return {
        "company_of_interest": "NVDA",
        "investment_plan": "buy the dip",
        "magpie_signal": None,
        "intraday_context": {
            "strategy": {"name": "pro_trader_dashboard", "direction": "long"},
            "trade_profile": {
                "enabled": True,
                "block": (
                    "**Trade Profile: Swing Options**\n"
                    "- Structure: long calls, ~3-4 weeks to expiration, ~0.70 delta"
                ),
            },
        },
    }


def _plain_state() -> dict:
    return {
        "company_of_interest": "NVDA",
        "investment_plan": "plan",
        "magpie_signal": None,
        "intraday_context": {
            "strategy": {"name": "base_momentum", "direction": "long"},
        },
    }


@pytest.mark.unit
def test_trader_binds_swing_schema_and_includes_profile():
    with (
        patch("tradingagents.agents.trader.trader.bind_structured") as mock_bind,
        patch(
            "tradingagents.agents.trader.trader.invoke_structured_or_freetext"
        ) as mock_invoke,
    ):
        mock_invoke.return_value = "FINAL TRANSACTION PROPOSAL: **BUY**"
        node = create_trader(MagicMock())
        node(_swing_state())

    # Second positional arg of bind_structured is the schema class.
    assert mock_bind.call_args[0][1] is SwingTradeProposal

    messages = mock_invoke.call_args[0][2]
    system_content = messages[0]["content"]
    user_content = messages[1]["content"]
    assert "swing option trade" in system_content
    assert "**Trade Profile: Swing Options**" in user_content


@pytest.mark.unit
def test_trader_binds_plain_schema_without_profile():
    with (
        patch("tradingagents.agents.trader.trader.bind_structured") as mock_bind,
        patch(
            "tradingagents.agents.trader.trader.invoke_structured_or_freetext"
        ) as mock_invoke,
    ):
        mock_invoke.return_value = "rendered"
        node = create_trader(MagicMock())
        node(_plain_state())

    assert mock_bind.call_args[0][1] is TraderProposal
    user_content = mock_invoke.call_args[0][2][1]["content"]
    assert "Trade Profile" not in user_content
