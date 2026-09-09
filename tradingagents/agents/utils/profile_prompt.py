"""Append the swing trade-profile block to an agent prompt when present."""
from __future__ import annotations

SWING_RISK_ROLE_LINE = (
    "Evaluate risk for a swing options position with the stated DTE, delta, "
    "and hold horizon from the trade profile below - focus on overnight gap "
    "risk, theta, and event risk within the hold window."
)

SWING_PM_ROLE_LINE = (
    "Weigh the swing thesis (DTE/delta/hold horizon in the profile below) "
    "against the risk debate for the final decision."
)


def append_trade_profile_block(
    base_prompt: str, profile_block: str | None, role_line: str
) -> str:
    """Append the rendered trade-profile block with a role-specific instruction.

    ``profile_block`` is the pre-rendered markdown block (the ``block`` value
    from ``intraday_context["trade_profile"]``), or None when absent or
    kill-switched; in that case ``base_prompt`` is returned unchanged.
    """
    if not profile_block:
        return base_prompt
    return f"{base_prompt}\n\n---\n\n{role_line}\n\n{profile_block}"
