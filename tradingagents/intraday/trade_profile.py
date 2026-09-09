"""Swing trade profile: shared swing-option vocabulary for the post-gate LLM chain.

Single source of truth for the pro-trader-dashboard swing style. ``propagate_intraday``
attaches the profile to ``intraday_context`` and every post-gate agent renders it
into its prompt so Trader, risk debators, and PM reason in the same vocabulary.

See docs/superpowers/specs/2026-09-09-swing-trade-profile-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradeProfile:
    style: str = "swing"
    dte_weeks: str = "3-4"
    target_delta: str = "0.70"
    hold_horizon_days: str = "1"
    option_structure: str = (
        "long calls (long setups) / long puts (short setups), "
        "~3-4 weeks to expiration, ~0.70 delta (slightly ITM)"
    )


def build_trade_profile(config: dict | None = None) -> TradeProfile | None:
    """Build the swing TradeProfile, or None when the kill switch is off.

    Config keys (pre-coerced from env by default_config):
    - ``pro_trader_swing_profile_enabled`` (default True; False = kill switch)
    - ``pro_trader_swing_dte_weeks`` (default "3-4")
    - ``pro_trader_swing_target_delta`` (default "0.70")
    - ``pro_trader_swing_hold_horizon_days`` (default "1")
    """
    config = config or {}
    if not config.get("pro_trader_swing_profile_enabled", True):
        return None
    return TradeProfile(
        dte_weeks=str(config.get("pro_trader_swing_dte_weeks") or "3-4"),
        target_delta=str(config.get("pro_trader_swing_target_delta") or "0.70"),
        hold_horizon_days=str(config.get("pro_trader_swing_hold_horizon_days") or "1"),
    )


def render_trade_profile(profile: TradeProfile, symbol: str, direction: str) -> str:
    """Render the trade profile as a markdown block for agent prompts."""
    if direction == "short":
        structure = (
            f"long puts, ~{profile.dte_weeks} weeks to expiration, "
            f"~{profile.target_delta} delta (slightly ITM)"
        )
    else:
        structure = (
            f"long calls, ~{profile.dte_weeks} weeks to expiration, "
            f"~{profile.target_delta} delta (slightly ITM)"
        )
    return (
        "**Trade Profile: Swing Options**\n"
        f"- Symbol: {symbol}\n"
        f"- Style: {profile.style} trade expressed with long options\n"
        f"- Structure: {structure}\n"
        f"- Hold horizon: typically ~{profile.hold_horizon_days} trading day(s); "
        "closes within days, not weeks\n"
        "- Entry/stop/targets are **underlying-equity levels**; the executor maps "
        f"the underlying move to the ~{profile.target_delta}-delta contract.\n"
        "- Invalidation = stop on the underlying, not option premium.\n"
        '- Time matters: a setup that needs "wait a week" is a **HOLD**. '
        "A valid swing setup must act within "
        f"~{profile.hold_horizon_days} day{'s' if profile.hold_horizon_days != '1' else ''}."
    )
