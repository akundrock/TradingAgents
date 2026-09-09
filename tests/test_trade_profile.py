# tests/test_trade_profile.py
from __future__ import annotations

import pytest

from tradingagents.intraday.trade_profile import (
    TradeProfile,
    build_trade_profile,
    render_trade_profile,
)


@pytest.mark.unit
def test_trade_profile_defaults_are_swing_options():
    profile = TradeProfile()
    assert profile.style == "swing"
    assert profile.dte_weeks == "3-4"
    assert profile.target_delta == "0.70"
    assert profile.hold_horizon_days == "1"


@pytest.mark.unit
def test_build_trade_profile_uses_defaults_without_config():
    profile = build_trade_profile(None)
    assert profile is not None
    assert profile.dte_weeks == "3-4"
    assert profile.target_delta == "0.70"
    assert profile.hold_horizon_days == "1"


@pytest.mark.unit
def test_build_trade_profile_applies_config_overrides():
    config = {
        "pro_trader_swing_dte_weeks": "5-6",
        "pro_trader_swing_target_delta": "0.65",
        "pro_trader_swing_hold_horizon_days": "2",
    }
    profile = build_trade_profile(config)
    assert profile.dte_weeks == "5-6"
    assert profile.target_delta == "0.65"
    assert profile.hold_horizon_days == "2"


@pytest.mark.unit
def test_build_trade_profile_returns_none_when_kill_switch_off():
    assert build_trade_profile({"pro_trader_swing_profile_enabled": False}) is None
    assert build_trade_profile({"pro_trader_swing_profile_enabled": True}) is not None
    assert build_trade_profile({}) is not None
    assert build_trade_profile(None) is not None


@pytest.mark.unit
def test_render_trade_profile_contains_swing_vocabulary():
    profile = TradeProfile()
    text = render_trade_profile(profile, symbol="NVDA", direction="long")
    assert "Trade Profile" in text
    assert "3-4" in text
    assert "0.70" in text
    assert "trading day" in text
    assert "underlying" in text.lower()
    assert "HOLD" in text


@pytest.mark.unit
def test_render_trade_profile_direction_phrasing():
    profile = TradeProfile()
    long_text = render_trade_profile(profile, symbol="NVDA", direction="long")
    short_text = render_trade_profile(profile, symbol="TSLA", direction="short")
    assert "long calls" in long_text
    assert "long puts" in short_text
    assert "NVDA" in long_text
