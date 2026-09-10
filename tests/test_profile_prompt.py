from __future__ import annotations

import pytest

from tradingagents.agents.utils.profile_prompt import (
    SWING_PM_ROLE_LINE,
    SWING_RISK_ROLE_LINE,
    append_trade_profile_block,
)


@pytest.mark.unit
def test_appends_profile_with_role_line_when_present():
    base = "BASE PROMPT"
    result = append_trade_profile_block(
        base,
        "**Trade Profile: Swing Options**\n- Structure: long calls",
        SWING_RISK_ROLE_LINE,
    )
    assert result.startswith("BASE PROMPT")
    assert "Trade Profile" in result
    assert SWING_RISK_ROLE_LINE in result
    assert "overnight gap risk" in result
    # Role lines carry no hardcoded tunables; the profile block owns the numbers.
    assert "0.70" not in SWING_RISK_ROLE_LINE
    assert "~1 day" not in SWING_RISK_ROLE_LINE


@pytest.mark.unit
def test_returns_base_unchanged_when_profile_absent():
    base = "BASE PROMPT"
    assert append_trade_profile_block(base, None, SWING_RISK_ROLE_LINE) == base
    assert append_trade_profile_block(base, "", SWING_RISK_ROLE_LINE) == base
