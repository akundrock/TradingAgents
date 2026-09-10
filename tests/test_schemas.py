# tests/test_schemas.py
from __future__ import annotations

import pytest

from tradingagents.agents.schemas import (
    SwingTradeProposal,
    render_swing_trade_proposal,
)


@pytest.mark.unit
def test_swing_trade_proposal_parses_all_fields():
    proposal = SwingTradeProposal(
        action="Buy",
        reasoning="ORB breakout with aligned RRS.",
        entry_price=100.5,
        stop_loss=98.0,
        option_structure="Long call, ~0.70 delta, 21 DTE",
        hold_horizon_days="1",
        option_direction="long call",
    )
    assert proposal.option_structure == "Long call, ~0.70 delta, 21 DTE"
    assert proposal.hold_horizon_days == "1"
    assert proposal.option_direction == "long call"


@pytest.mark.unit
def test_swing_trade_proposal_fields_optional():
    proposal = SwingTradeProposal(action="Hold", reasoning="wait")
    assert proposal.option_structure is None
    assert proposal.hold_horizon_days is None
    assert proposal.option_direction is None


@pytest.mark.unit
def test_render_swing_trade_proposal_includes_option_fields():
    proposal = SwingTradeProposal(
        action="Buy",
        reasoning="ORB breakout with aligned RRS.",
        entry_price=100.5,
        stop_loss=98.0,
        option_structure="Long call, ~0.70 delta, 21 DTE",
        hold_horizon_days="1",
        option_direction="long call",
    )
    text = render_swing_trade_proposal(proposal)
    assert "**Option Structure**: Long call, ~0.70 delta, 21 DTE" in text
    assert "**Hold Horizon**: 1" in text
    assert "**Option Direction**: long call" in text
    assert "FINAL TRANSACTION PROPOSAL: **BUY**" in text
    # FINAL line stays last (backward-compat for grep-based consumers)
    assert text.strip().endswith("FINAL TRANSACTION PROPOSAL: **BUY**")
