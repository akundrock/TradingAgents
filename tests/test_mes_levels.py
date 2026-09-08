"""Tests for tradingagents.mes.levels — suggest_trade_levels and normalize_trade_gonogo."""

from __future__ import annotations

import pytest

from tradingagents.agents.schemas import TradeGoNoGo, TradeVerdict
from tradingagents.mes.levels import (
    _parse_entry_zone,
    normalize_trade_gonogo,
    render_trade_levels_hint,
    suggest_trade_levels,
)


# ---------------------------------------------------------------------------
# _parse_entry_zone
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_entry_zone_standard():
    assert _parse_entry_zone("5000.00-5002.00") == (5000.0, 5002.0)


@pytest.mark.unit
def test_parse_entry_zone_reversed():
    """High-first format should still produce (low, high)."""
    assert _parse_entry_zone("5002.00-5000.00") == (5000.0, 5002.0)


@pytest.mark.unit
def test_parse_entry_zone_with_spaces():
    assert _parse_entry_zone("5000.00 - 5002.00") == (5000.0, 5002.0)


@pytest.mark.unit
def test_parse_entry_zone_none():
    assert _parse_entry_zone(None) is None
    assert _parse_entry_zone("") is None
    assert _parse_entry_zone("not a price") is None


# ---------------------------------------------------------------------------
# suggest_trade_levels
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_long_target_above_price():
    """For a long, first_target must be strictly above last_price."""
    result = suggest_trade_levels(
        "long",
        last_price=7678.75,
        vwap=7665.42,
        atr=13.0,
        prior_high=7673.75,   # below current price — should NOT be the target
        prior_vah=7690.0,     # above current price — should be the target
        overnight_high=7695.0,
    )
    assert result is not None
    assert result.first_target > 7678.75, (
        f"first_target {result.first_target} should be above last_price 7678.75"
    )


@pytest.mark.unit
def test_long_stop_below_price():
    """For a long, stop must be strictly below last_price."""
    result = suggest_trade_levels(
        "long",
        last_price=7678.75,
        vwap=7665.42,
        atr=13.0,
        prior_high=7673.75,
        prior_vah=7690.0,
    )
    assert result is not None
    assert result.stop_level < 7678.75


@pytest.mark.unit
def test_short_target_below_price():
    """For a short, first_target must be strictly below last_price."""
    result = suggest_trade_levels(
        "short",
        last_price=5000.0,
        vwap=5010.0,
        atr=8.0,
        prior_low=4990.0,
        prior_val=4985.0,
    )
    assert result is not None
    assert result.first_target < 5000.0


@pytest.mark.unit
def test_short_stop_above_price():
    result = suggest_trade_levels(
        "short",
        last_price=5000.0,
        vwap=5010.0,
        atr=8.0,
        prior_low=4990.0,
    )
    assert result is not None
    assert result.stop_level > 5000.0


@pytest.mark.unit
def test_target_picks_nearest_above_not_already_cleared():
    """Specific regression: prior-day high already cleared should not be the target."""
    # Price 7678.75, prior_high 7673.75 is below price → must not be target.
    # next level above: overnight_high 7695
    result = suggest_trade_levels(
        "long",
        last_price=7678.75,
        vwap=7665.42,
        atr=13.0,
        prior_high=7673.75,
        overnight_high=7695.0,
    )
    assert result is not None
    assert result.first_target >= 7695.0 or result.first_target == pytest.approx(7700.0, abs=1)


@pytest.mark.unit
def test_returns_none_when_no_levels_above():
    """If no structural levels above price exist, return None rather than crash."""
    result = suggest_trade_levels(
        "long",
        last_price=99999.0,  # unrealistically high — only round numbers above
        vwap=99990.0,
        atr=5.0,
    )
    # Round numbers above 99999 always exist so result should still be non-None.
    # This just verifies no crash.
    assert result is not None


@pytest.mark.unit
def test_entry_zone_contains_last_price():
    result = suggest_trade_levels(
        "long",
        last_price=5814.00,
        vwap=5800.0,
        atr=10.0,
        prior_vah=5830.0,
    )
    assert result is not None
    low_str, high_str = result.entry_zone.split("-")
    low, high = float(low_str), float(high_str)
    assert low <= 5814.00 <= high


@pytest.mark.unit
def test_stop_floored_by_one_atr():
    """Stop must be at least 1 ATR below entry even if no structural level that far."""
    result = suggest_trade_levels(
        "long",
        last_price=5000.0,
        vwap=4999.0,   # only 1 pt below — atr enforcement kicks in
        atr=10.0,
        prior_vah=5015.0,
    )
    assert result is not None
    assert 5000.0 - result.stop_level >= 10.0 - 0.01  # at least 1 ATR


@pytest.mark.unit
def test_stop_floor_scales_with_atr_multiple():
    """The safety floor is stop_atr_multiple × ATR, not a fixed 1 ATR."""
    common = dict(
        side="long",
        last_price=5000.0,
        vwap=4999.0,   # only 1 pt below — floor enforcement kicks in
        atr=4.0,
        prior_vah=5015.0,
    )
    at_default = suggest_trade_levels(**common, stop_atr_multiple=1.0)
    at_double = suggest_trade_levels(**common, stop_atr_multiple=2.0)
    assert at_default is not None
    assert at_double is not None
    assert 5000.0 - at_default.stop_level == pytest.approx(4.0)  # 1 × ATR
    assert 5000.0 - at_double.stop_level == pytest.approx(8.0)   # 2 × ATR


# ---------------------------------------------------------------------------
# render_trade_levels_hint
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_render_hint_contains_key_lines():
    result = suggest_trade_levels(
        "long",
        last_price=5000.0,
        vwap=4990.0,
        atr=10.0,
        prior_vah=5020.0,
    )
    assert result is not None
    hint = render_trade_levels_hint(result)
    assert "Entry zone" in hint
    assert "Stop" in hint
    assert "First target" in hint


# ---------------------------------------------------------------------------
# normalize_trade_gonogo — Wait / Stand Down
# ---------------------------------------------------------------------------


def _wait_verdict_with_levels() -> TradeGoNoGo:
    return TradeGoNoGo(
        verdict=TradeVerdict.WAIT,
        direction="long",
        confidence="medium",
        reasoning="Marginal score.",
        entry_zone="7678.75-7680.00",
        stop_level=7657.81,
        first_target=7673.75,  # inverted — below entry but shouldn't matter for Wait
        suggested_contracts=1,
        what_would_change_my_mind="Volume surge and $TICK persistence.",
    )


@pytest.mark.unit
def test_normalize_strips_levels_on_wait():
    v = _wait_verdict_with_levels()
    normalize_trade_gonogo(v)

    assert v.entry_zone is None
    assert v.stop_level is None
    assert v.first_target is None
    assert v.suggested_contracts is None


@pytest.mark.unit
def test_normalize_forces_direction_none_on_wait():
    v = _wait_verdict_with_levels()
    normalize_trade_gonogo(v)
    assert v.direction == "none"


@pytest.mark.unit
def test_normalize_strips_levels_on_stand_down():
    v = TradeGoNoGo(
        verdict=TradeVerdict.STAND_DOWN,
        direction="long",
        confidence="low",
        reasoning="Gates blocked.",
        entry_zone="7678-7680",
        stop_level=7650.0,
        first_target=7673.0,
        what_would_change_my_mind="Nothing.",
    )
    normalize_trade_gonogo(v)
    assert v.entry_zone is None
    assert v.first_target is None
    assert v.direction == "none"


@pytest.mark.unit
def test_normalize_preserves_what_would_change_my_mind():
    """wait_change_my_mind field must survive normalization unchanged."""
    v = _wait_verdict_with_levels()
    normalize_trade_gonogo(v)
    assert "Volume surge" in v.what_would_change_my_mind


@pytest.mark.unit
def test_normalize_preserves_reasoning_on_clean_wait():
    v = _wait_verdict_with_levels()
    normalize_trade_gonogo(v)
    # reasoning is not modified when no extra note is needed for the direction
    # fix (the direction was already being overridden silently, no issue appended).
    assert "Marginal score" in v.reasoning


# ---------------------------------------------------------------------------
# normalize_trade_gonogo — Take / long with inverted target
# ---------------------------------------------------------------------------


def _take_long_inverted() -> TradeGoNoGo:
    """Reproduces the exact failure from the reported output."""
    return TradeGoNoGo(
        verdict=TradeVerdict.TAKE,
        direction="long",
        confidence="medium",
        reasoning="Bullish setup.",
        entry_zone="7678.75-7680.00",
        stop_level=7657.81,
        first_target=7673.75,   # below entry high 7680.00 — invalid for long
        suggested_contracts=1,
        what_would_change_my_mind="Break below VWAP.",
    )


@pytest.mark.unit
def test_normalize_clears_inverted_target_on_long_take():
    v = _take_long_inverted()
    normalize_trade_gonogo(v)
    assert v.first_target is None


@pytest.mark.unit
def test_normalize_appends_note_to_reasoning_on_target_clear():
    v = _take_long_inverted()
    normalize_trade_gonogo(v)
    assert "auto-corrected" in v.reasoning


@pytest.mark.unit
def test_normalize_preserves_valid_long_take():
    v = TradeGoNoGo(
        verdict=TradeVerdict.TAKE,
        direction="long",
        confidence="high",
        reasoning="Premium setup.",
        entry_zone="5000.00-5002.00",
        stop_level=4995.0,   # below entry low 5000.00 ✓
        first_target=5010.0,  # above entry high 5002.00 ✓
        suggested_contracts=2,
        what_would_change_my_mind="$ADD flipping negative.",
    )
    normalize_trade_gonogo(v)
    assert v.first_target == 5010.0
    assert v.stop_level == 4995.0
    assert "auto-corrected" not in v.reasoning


@pytest.mark.unit
def test_normalize_clears_inverted_stop_on_long_take():
    """Stop above or equal to entry low should be cleared."""
    v = TradeGoNoGo(
        verdict=TradeVerdict.TAKE,
        direction="long",
        confidence="medium",
        reasoning="Setup.",
        entry_zone="5000.00-5002.00",
        stop_level=5001.0,   # inside entry zone — invalid
        first_target=5010.0,
        what_would_change_my_mind="Nothing.",
    )
    normalize_trade_gonogo(v)
    assert v.stop_level is None
    assert "auto-corrected" in v.reasoning


@pytest.mark.unit
def test_normalize_preserves_valid_short_take():
    v = TradeGoNoGo(
        verdict=TradeVerdict.TAKE,
        direction="short",
        confidence="high",
        reasoning="Bearish setup.",
        entry_zone="5000.00-5002.00",
        stop_level=5005.0,   # above entry high ✓
        first_target=4990.0,  # below entry low ✓
        suggested_contracts=1,
        what_would_change_my_mind="$ADD reverting positive.",
    )
    normalize_trade_gonogo(v)
    assert v.first_target == 4990.0
    assert v.stop_level == 5005.0
    assert "auto-corrected" not in v.reasoning


@pytest.mark.unit
def test_normalize_clears_inverted_target_on_short_take():
    v = TradeGoNoGo(
        verdict=TradeVerdict.TAKE,
        direction="short",
        confidence="medium",
        reasoning="Bearish.",
        entry_zone="5000.00-5002.00",
        stop_level=5005.0,
        first_target=5001.0,   # inside entry zone — invalid for short
        what_would_change_my_mind="Nothing.",
    )
    normalize_trade_gonogo(v)
    assert v.first_target is None
