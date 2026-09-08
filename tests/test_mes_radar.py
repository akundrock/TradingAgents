"""Tests for tradingagents.mes.radar — ProximityReport, SetupState, level distances."""

from __future__ import annotations

import pytest

from tradingagents.mes.checklist import CheckItem, ChecklistResult
from tradingagents.mes.radar import (
    LevelDistance,
    ProximityReport,
    SetupState,
    _classify_state,
    _level_distances,
    _missing_items,
    build_proximity,
)
from tests.mes_factories import (
    DEFAULT_AS_OF,
    make_mes_series,
    make_snapshot,
    make_spy_series,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    *,
    side: str = "long",
    last_price: float = 7678.75,
    vwap: float = 7665.0,
    atr: float = 12.0,
    upper_atr_band: float = 7700.0,
    lower_atr_band: float = 7620.0,
    gates_ok: bool = True,
    gate_reasons: list[str] | None = None,
    score: int = 6,
    max_score: int = 9,
    confirmations: int = 5,
    required: int = 4,
    tier: str = "standard",
    score_ok: bool = True,
    spy_confirmations: int = 4,
    spy_confluence_ok: bool = True,
    no_trade_reasons: list[str] | None = None,
    mes_items: list[CheckItem] | None = None,
    spy_items: list[CheckItem] | None = None,
    opening_range_high: float | None = 7680.0,
    opening_range_low: float | None = 7660.0,
) -> ChecklistResult:
    return ChecklistResult(
        as_of_label="11:00",
        side=side,
        direction=side,
        last_price=last_price,
        vwap=vwap,
        atr=atr,
        upper_atr_band=upper_atr_band,
        lower_atr_band=lower_atr_band,
        gates_ok=gates_ok,
        gate_reasons=gate_reasons or [],
        score=score,
        max_score=max_score,
        confirmations=confirmations,
        required=required,
        tier=tier,
        score_ok=score_ok,
        spy_confirmations=spy_confirmations,
        spy_confluence_ok=spy_confluence_ok,
        no_trade_reasons=no_trade_reasons or [],
        mes_items=mes_items or [],
        spy_items=spy_items or [],
        opening_range_high=opening_range_high,
        opening_range_low=opening_range_low,
    )


def _make_snapshot(*, prior_high: float | None = 7673.75, overnight_high: float | None = 7695.0):
    mes = make_mes_series(
        close=7678.75,
        vwap=7665.0,
        atr=12.0,
        upper_atr_band=7700.0,
        lower_atr_band=7620.0,
        opening_range_high=7680.0,
        opening_range_low=7660.0,
    )
    snap = make_snapshot(mes=mes)

    # Inject prior/overnight directly on snapshot for level collection
    class _Prior:
        high = prior_high
        low = 7640.0
        close = 7655.0
        vah = 7690.0
        val = 7650.0
        poc = 7665.0

    snap.prior_mes = _Prior() if prior_high is not None else None
    snap.overnight_mes = (overnight_high, 7645.0) if overnight_high is not None else None
    return snap


# ---------------------------------------------------------------------------
# SetupState classification
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_gates_closed_when_gates_not_ok():
    result = _make_result(gates_ok=False, gate_reasons=["outside RTH"])
    state = _classify_state(result, near_level=True)
    assert state == SetupState.GATES_CLOSED


@pytest.mark.unit
def test_blocked_when_no_trade_reasons_present():
    result = _make_result(no_trade_reasons=["TICK exhaustion on long"])
    state = _classify_state(result, near_level=True)
    assert state == SetupState.BLOCKED


@pytest.mark.unit
def test_ready_when_tradeable_and_near_level():
    result = _make_result()
    assert result.tradeable
    state = _classify_state(result, near_level=True)
    assert state == SetupState.READY


@pytest.mark.unit
def test_confluence_ok_waiting_location_when_tradeable_not_near():
    """tradeable=True but no level in band — valid patience, not hesitation."""
    result = _make_result()
    assert result.tradeable
    state = _classify_state(result, near_level=False)
    assert state == SetupState.CONFLUENCE_OK_WAITING_LOCATION


@pytest.mark.unit
def test_at_level_missing_confluence_when_near_but_not_tradeable():
    result = _make_result(score_ok=False)
    assert not result.tradeable
    state = _classify_state(result, near_level=True)
    assert state == SetupState.AT_LEVEL_MISSING_CONFLUENCE


@pytest.mark.unit
def test_building_when_neither_near_nor_tradeable():
    result = _make_result(score_ok=False)
    assert not result.tradeable
    state = _classify_state(result, near_level=False)
    assert state == SetupState.BUILDING


@pytest.mark.unit
def test_gates_closed_takes_precedence_over_blocked():
    """Gates-closed must win over no_trade_reasons when both apply."""
    result = _make_result(
        gates_ok=False,
        gate_reasons=["weekend"],
        no_trade_reasons=["chop"],
    )
    state = _classify_state(result, near_level=True)
    assert state == SetupState.GATES_CLOSED


# ---------------------------------------------------------------------------
# Level distances — cleared vs active
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prior_day_high_below_price_appears_as_cleared():
    """Prior-day high 7673.75 is below current price 7678.75 — should be in 'below' list."""
    snap = _make_snapshot(prior_high=7673.75, overnight_high=7695.0)
    result = _make_result(last_price=7678.75, vwap=7665.0, atr=12.0)
    above, below, _ = _level_distances(7678.75, snap, result, proximity_band=4.0)

    below_prices = [ld.price for ld in below]
    assert 7673.75 in below_prices, "prior-day high should be in the 'below' tape"

    # Verify it is flagged as cleared
    cleared = [ld for ld in below if ld.price == 7673.75]
    assert cleared and cleared[0].cleared, "prior-day high below price must be cleared=True"


@pytest.mark.unit
def test_level_above_price_not_cleared():
    snap = _make_snapshot(prior_high=7673.75, overnight_high=7695.0)
    result = _make_result(last_price=7678.75, vwap=7665.0, atr=12.0)
    above, _, _ = _level_distances(7678.75, snap, result, proximity_band=4.0)

    # overnight high 7695 should be above and not cleared
    above_prices = [ld.price for ld in above]
    assert any(p >= 7695.0 for p in above_prices), "overnight high should appear above price"
    for ld in above:
        assert not ld.cleared, f"level {ld.price} is above price but cleared=True"


@pytest.mark.unit
def test_within_band_flag_on_nearby_level():
    snap = _make_snapshot(prior_high=7673.75, overnight_high=7682.0)
    result = _make_result(last_price=7678.75, vwap=7665.0, atr=12.0)
    above, _, near = _level_distances(7678.75, snap, result, proximity_band=4.0)

    # overnight_high 7682 is 3.25 pts above → within band of 4.0
    in_band = [ld for ld in above if ld.price == 7682.0]
    assert in_band and in_band[0].within_band, "level 3.25 pts away should be within_band"
    assert near


@pytest.mark.unit
def test_not_within_band_for_distant_level():
    snap = _make_snapshot(prior_high=7673.75, overnight_high=7695.0)
    result = _make_result(last_price=7678.75, vwap=7665.0, atr=12.0)
    _, _, near = _level_distances(7678.75, snap, result, proximity_band=2.0)

    # With band of 2 pts, overnight_high (16.25 pts away) and prior_high (5 pts away) are outside
    # VWAP 7665 is 13.75 pts away; ORB high 7680 is 1.25 pts away — within 2
    # ORB high IS within 2 pts, so near=True; we just check the distant overnight is not flagged
    above, below, _ = _level_distances(7678.75, snap, result, proximity_band=2.0)
    for ld in above:
        if abs(ld.distance) > 2.0:
            assert not ld.within_band, f"level {ld.price} is {ld.distance} pts away but within_band=True"


@pytest.mark.unit
def test_levels_sorted_nearest_first():
    snap = _make_snapshot(prior_high=7673.75, overnight_high=7695.0)
    result = _make_result(last_price=7678.75, vwap=7665.0, atr=12.0)
    above, below, _ = _level_distances(7678.75, snap, result, proximity_band=4.0)

    above_dists = [ld.distance for ld in above]
    assert above_dists == sorted(above_dists), "above levels must be sorted nearest first"

    below_dists = [ld.distance for ld in below]
    # below are negative distances; nearest first means closest to 0
    assert below_dists == sorted(below_dists, reverse=True), "below levels must be nearest first"


@pytest.mark.unit
def test_max_per_side_capped():
    snap = _make_snapshot(prior_high=7673.75, overnight_high=7695.0)
    result = _make_result(last_price=7678.75, vwap=7665.0, atr=12.0)
    above, below, _ = _level_distances(7678.75, snap, result, proximity_band=4.0, max_per_side=2)

    assert len(above) <= 2
    assert len(below) <= 2


# ---------------------------------------------------------------------------
# Missing items extraction
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_missing_items_returns_failing_mes_checks():
    mes_items = [
        CheckItem("Momentum", "MES", passed=False, observed="no cross", threshold="cross", weight=2),
        CheckItem("VWAP side", "MES", passed=True, observed="above", threshold="above", weight=1),
        CheckItem("Volume surge", "MES", passed=False, observed="low", threshold="1.2×", weight=1),
    ]
    result = _make_result(mes_items=mes_items, spy_confluence_ok=True)
    missing = _missing_items(result)

    assert "Momentum" in missing
    assert "Volume surge" in missing
    assert "VWAP side" not in missing


@pytest.mark.unit
def test_missing_items_weighted_first():
    """Heavier-weight failing items should appear before lower-weight ones."""
    mes_items = [
        CheckItem("Low weight", "MES", passed=False, observed="x", threshold="x", weight=1),
        CheckItem("High weight", "MES", passed=False, observed="x", threshold="x", weight=2),
    ]
    result = _make_result(mes_items=mes_items)
    missing = _missing_items(result)

    assert missing.index("High weight") < missing.index("Low weight")


@pytest.mark.unit
def test_missing_items_includes_spy_when_confluence_failing():
    spy_items = [
        CheckItem("SPY VWAP", "SPY", passed=False, observed="below", threshold="above", weight=0),
        CheckItem("$ADD breadth", "SPY", passed=True, observed="+500", threshold=">250", weight=0),
    ]
    result = _make_result(spy_items=spy_items, spy_confluence_ok=False)
    missing = _missing_items(result)

    assert "SPY VWAP" in missing
    assert "$ADD breadth" not in missing


@pytest.mark.unit
def test_missing_items_excludes_spy_when_confluence_ok():
    spy_items = [
        CheckItem("SPY VWAP", "SPY", passed=False, observed="below", threshold="above", weight=0),
    ]
    result = _make_result(spy_items=spy_items, spy_confluence_ok=True)
    missing = _missing_items(result)

    assert "SPY VWAP" not in missing


# ---------------------------------------------------------------------------
# build_proximity integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_proximity_returns_report():
    snap = _make_snapshot()
    result = _make_result()
    report = build_proximity(snap, result)

    assert isinstance(report, ProximityReport)
    assert isinstance(report.state, SetupState)
    assert report.price == pytest.approx(7678.75)
    assert report.side == "long"


@pytest.mark.unit
def test_build_proximity_ready_when_tradeable_and_near():
    snap = _make_snapshot(overnight_high=7681.0)  # 2.25 pts above → within default band
    result = _make_result(last_price=7678.75, atr=12.0)
    assert result.tradeable
    # Default band = min(4.0, 0.5 * 12) = 4.0 — overnight_high is 2.25 pts away → near
    report = build_proximity(snap, result)
    assert report.near_level
    assert report.state == SetupState.READY


@pytest.mark.unit
def test_build_proximity_waiting_location_when_tradeable_not_near():
    """No structural level within band → CONFLUENCE_OK_WAITING_LOCATION."""
    snap = _make_snapshot(prior_high=7600.0, overnight_high=7750.0)
    # Force ORB and VWAP also far from price
    mes = make_mes_series(
        close=7678.75,
        vwap=7500.0,  # very far
        atr=12.0,
        upper_atr_band=7800.0,
        lower_atr_band=7400.0,
        opening_range_high=7750.0,
        opening_range_low=7400.0,
    )
    snap2 = make_snapshot(mes=mes)
    snap2.prior_mes = None
    snap2.overnight_mes = None
    result = _make_result(
        last_price=7678.75,
        vwap=7500.0,
        atr=12.0,
        upper_atr_band=7800.0,
        lower_atr_band=7400.0,
        opening_range_high=7750.0,
        opening_range_low=7400.0,
    )
    # Use a tiny band so even round numbers may slip by
    report = build_proximity(snap2, result, proximity_band=0.1)
    # With a 0.1 band, only levels within 0.1 pts count; price=7678.75 so
    # round-50 at 7650 is 28.75 pts away, round-100 at 7700 is 21.25 pts away — all out of band
    assert not report.near_level
    assert report.state == SetupState.CONFLUENCE_OK_WAITING_LOCATION


@pytest.mark.unit
def test_build_proximity_custom_band():
    snap = _make_snapshot(prior_high=7673.75, overnight_high=7695.0)
    result = _make_result(last_price=7678.75, atr=12.0)
    # With a 1-pt band nothing is within 1 pt of 7678.75 (VWAP 7665=13.75, ORB low 7660=18.75 etc.)
    # unless ORB high 7680 is 1.25 pts away — above band of 1.0
    report = build_proximity(snap, result, proximity_band=1.0)
    # Regardless of exact result, check the report stores the custom band
    assert report.proximity_band == pytest.approx(1.0)


@pytest.mark.unit
def test_build_proximity_default_band_uses_half_atr():
    snap = _make_snapshot()
    # atr = 12.0 → 0.5*12 = 6.0, but capped at 4.0
    result = _make_result(atr=12.0)
    report = build_proximity(snap, result)
    assert report.proximity_band == pytest.approx(4.0)

    # atr = 4.0 → 0.5*4 = 2.0, which is < 4.0
    result2 = _make_result(atr=4.0)
    report2 = build_proximity(snap, result2)
    assert report2.proximity_band == pytest.approx(2.0)


@pytest.mark.unit
def test_build_proximity_gates_closed():
    snap = _make_snapshot()
    result = _make_result(gates_ok=False, gate_reasons=["outside RTH"])
    report = build_proximity(snap, result)
    assert report.state == SetupState.GATES_CLOSED
    assert "outside RTH" in report.gate_reasons


@pytest.mark.unit
def test_build_proximity_missing_items_populated():
    mes_items = [
        CheckItem("Momentum", "MES", passed=False, observed="no cross", threshold="cross", weight=2),
        CheckItem("VWAP side", "MES", passed=True, observed="above", threshold="above", weight=1),
    ]
    snap = _make_snapshot()
    result = _make_result(mes_items=mes_items)
    report = build_proximity(snap, result)

    assert "Momentum" in report.missing_items
    assert "VWAP side" not in report.missing_items


@pytest.mark.unit
def test_cleared_prior_day_high_not_shown_as_target_level():
    """Regression: prior-day high 7673.75 should appear in 'below' tape, never 'above'."""
    snap = _make_snapshot(prior_high=7673.75, overnight_high=7695.0)
    result = _make_result(last_price=7678.75)
    report = build_proximity(snap, result)

    above_prices = [ld.price for ld in report.levels_above]
    assert 7673.75 not in above_prices, (
        "prior-day high 7673.75 is below current price and must not appear as a target level"
    )

    below_prices = [ld.price for ld in report.levels_below]
    # It may or may not be in the top-3 below, but if it is, it must be cleared
    if 7673.75 in below_prices:
        cleared = [ld for ld in report.levels_below if ld.price == 7673.75]
        assert cleared[0].cleared


@pytest.mark.unit
def test_near_level_true_when_price_close_to_vwap():
    """VWAP as structural level should trigger near_level when price is within band."""
    mes = make_mes_series(
        close=7667.0,
        vwap=7665.0,  # 2 pts away
        atr=12.0,
        upper_atr_band=7700.0,
        lower_atr_band=7620.0,
        opening_range_high=7700.0,
        opening_range_low=7600.0,
    )
    snap = make_snapshot(mes=mes)
    snap.prior_mes = None
    snap.overnight_mes = None
    result = _make_result(
        last_price=7667.0,
        vwap=7665.0,
        atr=12.0,
        upper_atr_band=7700.0,
        lower_atr_band=7620.0,
        opening_range_high=7700.0,
        opening_range_low=7600.0,
    )
    # Default band: min(4.0, 6.0) = 4.0; VWAP is 2.0 pts away → within band
    report = build_proximity(snap, result)
    assert report.near_level
