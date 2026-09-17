"""Tests for tradingagents.mes.radar — ProximityReport, SetupState, level distances."""

from __future__ import annotations

import io
from datetime import datetime
import time

import pytest
from rich.console import Console
from typer.testing import CliRunner

from cli import mes as mes_cli
from tradingagents.mes.checklist import CheckItem, ChecklistResult
from tradingagents.mes.config import load_mes_config
from tradingagents.mes.journal import MesJournal
from tradingagents.mes.radar import (
    LevelDistance,
    ProximityReport,
    SetupState,
    _classify_state,
    _level_distances,
    _missing_items,
    build_proximity,
    should_auto_check,
)
from tradingagents.mes.render import format_internals_status, render_market_context
from tests.mes_factories import (
    DEFAULT_AS_OF,
    make_mes_series,
    make_snapshot,
    make_spy_series,
)

radar_runner = CliRunner()


def _render_table_to_text(table) -> str:
    """Render a Rich table/grid to plain text for substring assertions."""
    console = Console(file=io.StringIO(), width=160, legacy_windows=False)
    console.print(table)
    return console.file.getvalue()


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
        session_date = snap.session_date
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


# ---------------------------------------------------------------------------
# Internals status line (render.format_internals_status)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_internals_status_formats_all_three():
    snap = _make_snapshot()
    line = format_internals_status(snap)
    assert line is not None
    assert line.startswith("TICK +100 (thr ±")
    assert "ADD +300" in line
    assert "VOLD +1000 slope +0" in line


@pytest.mark.unit
def test_internals_status_none_when_all_missing():
    spy = make_spy_series(internals=[(None, None, None)] * 6)
    snap = make_snapshot(spy=spy)
    assert format_internals_status(snap) is None


@pytest.mark.unit
def test_internals_status_marks_unavailable_segments():
    spy = make_spy_series(internals=[(None, 250.0, None)] * 6)
    snap = make_snapshot(spy=spy)
    line = format_internals_status(snap)
    assert line is not None
    assert "TICK +250 (thr ±" in line
    assert "ADD unavailable" in line
    assert "VOLD unavailable" in line


@pytest.mark.unit
def test_internals_status_marks_synthetic_vold_as_a_delta():
    snap = _make_snapshot()
    snap.internals_provenance = {"add": "candles", "tick": "candles", "vold": "synthetic"}
    line = format_internals_status(snap)
    assert line is not None
    assert "VOLD Δ+1,000 (synthetic)" in line


@pytest.mark.unit
def test_internals_status_keeps_plain_vold_for_direct_candle_provenance():
    snap = _make_snapshot()
    snap.internals_provenance = {"add": "candles", "tick": "candles", "vold": "candles"}
    line = format_internals_status(snap)
    assert line is not None
    assert "VOLD +1000 slope" in line
    assert "synthetic" not in line


@pytest.mark.unit
def test_market_context_marks_synthetic_vold_value():
    snap = _make_snapshot()
    snap.internals_provenance = {"add": "candles", "tick": "candles", "vold": "synthetic"}
    body = render_market_context(snap)
    assert "Δ+1,000 (synthetic)" in body


# ---------------------------------------------------------------------------
# Snapshot warnings + internals status on ProximityReport
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_warnings_carried_from_snapshot():
    snap = _make_snapshot()
    snap.warnings = ["$TICK data sparse: only 2/78 5m bars readable"]
    report = build_proximity(snap, _make_result())
    assert report.warnings == ["$TICK data sparse: only 2/78 5m bars readable"]


@pytest.mark.unit
def test_warnings_is_a_copy_not_a_live_reference():
    snap = _make_snapshot()
    snap.warnings = ["first"]
    report = build_proximity(snap, _make_result())
    snap.warnings.append("appended later")
    assert report.warnings == ["first"]


@pytest.mark.unit
def test_warnings_empty_by_default():
    snap = _make_snapshot()
    report = build_proximity(snap, _make_result())
    assert report.warnings == []


@pytest.mark.unit
def test_internals_status_carried_on_report():
    snap = _make_snapshot()
    report = build_proximity(snap, _make_result())
    assert report.internals_status is not None
    assert report.internals_status.startswith("TICK +100 (thr ±")


@pytest.mark.unit
def test_report_internals_status_none_when_all_missing():
    spy = make_spy_series(internals=[(None, None, None)] * 6)
    snap = make_snapshot(spy=spy)
    report = build_proximity(snap, _make_result())
    assert report.internals_status is None


# ---------------------------------------------------------------------------
# Radar panel rendering: internals status + snapshot warnings
# ---------------------------------------------------------------------------


def _invoke_radar(snapshot, monkeypatch):
    from tests.mes_factories import DEFAULT_AS_OF

    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snapshot)
    monkeypatch.setattr(mes_cli, "_market_now", lambda cfg: DEFAULT_AS_OF)
    return radar_runner.invoke(mes_cli.mes_app, ["radar", "--no-watch"])


@pytest.mark.unit
def test_radar_panel_shows_internals_status(monkeypatch):
    snap = _make_snapshot()
    result = _invoke_radar(snap, monkeypatch)
    assert result.exit_code == 0
    assert "internals:" in result.output
    assert "TICK +100" in result.output
    assert "ADD +300" in result.output


@pytest.mark.unit
def test_radar_panel_shows_snapshot_warnings(monkeypatch):
    snap = _make_snapshot()
    snap.warnings = ["$TICK data sparse: only 2/78 5m bars readable"]
    result = _invoke_radar(snap, monkeypatch)
    assert result.exit_code == 0
    assert "warn:" in result.output
    assert "$TICK data sparse" in result.output


@pytest.mark.unit
def test_radar_panel_internals_unavailable_row(monkeypatch):
    spy = make_spy_series(internals=[(None, None, None)] * 6)
    snap = make_snapshot(mes=make_mes_series(), spy=spy)
    result = _invoke_radar(snap, monkeypatch)
    assert result.exit_code == 0
    assert "internals unavailable" in result.output
    assert "warn:" not in result.output


@pytest.mark.unit
def test_radar_panel_renders_report_without_warnings_cleanly(monkeypatch):
    snap = _make_snapshot()
    report = build_proximity(snap, _make_result())
    text = _render_table_to_text(mes_cli._render_radar(report, snap.as_of))
    assert "warn:" not in text


# ---------------------------------------------------------------------------
# _run_check_once — shared full-check pass for `mes check` and radar --auto-check
# ---------------------------------------------------------------------------


class _StubJournal:
    """Records append_check calls; stands in for MesJournal."""

    def __init__(self):
        self.calls = []

    def append_check(self, *, snapshot, result, verdict_markdown="", sizing=None):
        self.calls.append(
            {
                "snapshot": snapshot,
                "result": result,
                "verdict_markdown": verdict_markdown,
                "sizing": sizing,
            }
        )


def test_run_check_once_prints_deterministic_verdict_and_logs():
    snap = _make_snapshot()
    result = _make_result()
    stub = _StubJournal()

    sizing, sizing_note, verdict = mes_cli._run_check_once(
        snapshot=snap,
        result=result,
        cfg=load_mes_config(),
        journal=stub,
        gatekeeper=None,
        hypothesis="",
        past_context="",
        risk_dollars=1000.0,
        stop_points=8.0,
    )

    assert "contracts" in sizing
    assert "Risk $1,000" in sizing_note
    assert verdict == ""  # no gatekeeper -> deterministic verdict path
    assert len(stub.calls) == 1
    assert stub.calls[0]["snapshot"] is snap
    assert stub.calls[0]["result"] is result
    assert stub.calls[0]["verdict_markdown"] == ""


def test_run_check_once_calls_gatekeeper_and_logs_verdict():
    snap = _make_snapshot()
    result = _make_result()
    stub = _StubJournal()

    def fake_gatekeeper(**kwargs):
        assert kwargs["tradeable"] is True
        assert kwargs["hypothesis"] == "fade extremes into VWAP"
        return "**Verdict**: Wait\n\nNot at level."

    sizing, sizing_note, verdict = mes_cli._run_check_once(
        snapshot=snap,
        result=result,
        cfg=load_mes_config(),
        journal=stub,
        gatekeeper=fake_gatekeeper,
        hypothesis="fade extremes into VWAP",
        past_context="",
        risk_dollars=1000.0,
        stop_points=8.0,
    )

    assert "Wait" in verdict
    assert len(stub.calls) == 1
    assert "Not at level" in stub.calls[0]["verdict_markdown"]
    assert "contracts" in stub.calls[0]["sizing"]


def test_run_check_once_no_log_skips_journal():
    snap = _make_snapshot()
    result = _make_result()
    stub = _StubJournal()

    mes_cli._run_check_once(
        snapshot=snap,
        result=result,
        cfg=load_mes_config(),
        journal=stub,
        gatekeeper=None,
        hypothesis="",
        past_context="",
        risk_dollars=1000.0,
        stop_points=8.0,
        no_log=True,
    )

    assert stub.calls == []


# ---------------------------------------------------------------------------
# should_auto_check — radar --auto-check trigger decision (pure function)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_should_auto_check_fires_on_transition_into_ready():
    now = datetime(2026, 3, 30, 11, 0)
    assert should_auto_check(
        SetupState.READY, SetupState.CONFLUENCE_OK_WAITING_LOCATION, None, now, 300.0
    )


@pytest.mark.unit
def test_should_auto_check_fires_when_radar_starts_already_ready():
    now = datetime(2026, 3, 30, 11, 0)
    assert should_auto_check(SetupState.READY, None, None, now, 300.0)


@pytest.mark.unit
def test_should_auto_check_never_fires_outside_ready():
    now = datetime(2026, 3, 30, 11, 0)
    non_ready = [
        SetupState.GATES_CLOSED,
        SetupState.BLOCKED,
        SetupState.BUILDING,
        SetupState.AT_LEVEL_MISSING_CONFLUENCE,
        SetupState.CONFLUENCE_OK_WAITING_LOCATION,
    ]
    for state in non_ready:
        assert not should_auto_check(state, None, None, now, 300.0)
        # even mid-cooldown with a fired history, non-READY never fires
        assert not should_auto_check(state, state, now, now, 300.0)


@pytest.mark.unit
def test_should_auto_check_suppressed_within_cooldown():
    now = datetime(2026, 3, 30, 11, 5)
    last_fired = datetime(2026, 3, 30, 11, 2)
    assert not should_auto_check(SetupState.READY, SetupState.READY, last_fired, now, 300.0)


@pytest.mark.unit
def test_should_auto_check_refires_after_cooldown():
    now = datetime(2026, 3, 30, 11, 5)
    last_fired = datetime(2026, 3, 30, 11, 0)
    assert should_auto_check(SetupState.READY, SetupState.READY, last_fired, now, 300.0)


@pytest.mark.unit
def test_should_auto_check_zero_cooldown_refires_every_ready_tick():
    now = datetime(2026, 3, 30, 11, 0)
    assert should_auto_check(SetupState.READY, SetupState.READY, now, now, 0.0)


@pytest.mark.unit
def test_should_auto_check_transition_refires_even_within_cooldown():
    """A fresh entry into READY is a new setup moment — it fires immediately."""
    now = datetime(2026, 3, 30, 11, 8)
    last_fired = datetime(2026, 3, 30, 11, 7)
    assert should_auto_check(
        SetupState.READY, SetupState.BLOCKED, last_fired, now, 300.0
    )


# ---------------------------------------------------------------------------
# radar --auto-check: fires the full check path on READY
# ---------------------------------------------------------------------------


def _ready_fixture(monkeypatch, tmp_path):
    """Patches the CLI so radar evaluates a genuinely READY setup.

    overnight_high=7681.0 is 2.25 pts above price 7678.75 -> within the default
    band, and _make_result() is tradeable -> build_proximity reports READY.
    Journal writes go to tmp_path via a patched DEFAULT_CONFIG.
    """
    snap = _make_snapshot(overnight_high=7681.0)
    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(mes_cli, "_market_now", lambda cfg: DEFAULT_AS_OF)
    monkeypatch.setattr(mes_cli, "DEFAULT_CONFIG", {"mes_journal_dir": str(tmp_path)})
    # Pin the checklist result so the test exercises the auto-check trigger,
    # not the real checklist thresholds on factory data.
    monkeypatch.setattr(mes_cli, "evaluate", lambda snapshot, side: _make_result())
    return snap


@pytest.mark.unit
def test_radar_auto_check_fires_on_ready_one_shot(monkeypatch, tmp_path):
    _ready_fixture(monkeypatch, tmp_path)
    result = radar_runner.invoke(
        mes_cli.mes_app, ["radar", "--no-watch", "--auto-check", "--no-llm"]
    )
    assert result.exit_code == 0, result.output
    assert "Deterministic Verdict" in result.output
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    records = [e for e in journal.load_day("2026-03-30") if e["kind"] == "check"]
    assert len(records) == 1
    assert records[0]["tradeable"] is True


@pytest.mark.unit
def test_radar_without_auto_check_still_never_writes_journal(monkeypatch, tmp_path):
    _ready_fixture(monkeypatch, tmp_path)
    result = radar_runner.invoke(mes_cli.mes_app, ["radar", "--no-watch"])
    assert result.exit_code == 0
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    assert journal.load_day("2026-03-30") == []


@pytest.mark.unit
def test_radar_auto_check_skipped_when_not_ready(monkeypatch, tmp_path):
    """Gates closed -> never READY -> no check run even with --auto-check."""
    snap = _make_snapshot()
    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(mes_cli, "_market_now", lambda cfg: DEFAULT_AS_OF)
    monkeypatch.setattr(mes_cli, "DEFAULT_CONFIG", {"mes_journal_dir": str(tmp_path)})
    monkeypatch.setattr(
        mes_cli, "evaluate",
        lambda snapshot, side: _make_result(gates_ok=False, gate_reasons=["outside RTH"]),
    )
    result = radar_runner.invoke(
        mes_cli.mes_app, ["radar", "--no-watch", "--auto-check", "--no-llm"]
    )
    assert result.exit_code == 0
    assert "Deterministic Verdict" not in result.output
    result = radar_runner.invoke(
        mes_cli.mes_app, ["radar", "--no-watch", "--auto-check", "--no-llm"]
    )
    assert result.exit_code == 0
    assert "Deterministic Verdict" not in result.output
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    assert journal.load_day("2026-03-30") == []


@pytest.mark.unit
def test_radar_auto_check_no_llm_never_builds_gatekeeper(monkeypatch, tmp_path):
    def _boom(*args, **kwargs):
        raise AssertionError("gatekeeper must not be created with --no-llm")

    _ready_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(mes_cli, "create_mes_gatekeeper_agent", _boom)
    result = radar_runner.invoke(
        mes_cli.mes_app, ["radar", "--no-watch", "--auto-check", "--no-llm"]
    )
    assert result.exit_code == 0, result.output
    assert "Deterministic Verdict" in result.output


@pytest.mark.unit
def test_radar_auto_check_calls_gatekeeper_and_logs_verdict(monkeypatch, tmp_path):
    _ready_fixture(monkeypatch, tmp_path)

    def fake_gatekeeper(**kwargs):
        return "**Verdict**: Wait\n\nSizing unclear."

    monkeypatch.setattr(mes_cli, "_make_llm", lambda config, deep=False: object())
    monkeypatch.setattr(mes_cli, "create_mes_gatekeeper_agent", lambda llm: fake_gatekeeper)
    result = radar_runner.invoke(
        mes_cli.mes_app, ["radar", "--no-watch", "--auto-check"]
    )
    assert result.exit_code == 0, result.output
    assert "Verdict: Wait" in result.output
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    records = [e for e in journal.load_day("2026-03-30") if e["kind"] == "check"]
    assert len(records) == 1
    assert "Wait" in records[0]["verdict"]


@pytest.mark.unit
def test_radar_auto_check_json_mode_ignores_flag(monkeypatch, tmp_path):
    _ready_fixture(monkeypatch, tmp_path)
    result = radar_runner.invoke(
        mes_cli.mes_app, ["radar", "--no-watch", "--json", "--auto-check", "--no-llm"]
    )
    assert result.exit_code == 0, result.output
    assert '"state"' in result.output
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    assert journal.load_day("2026-03-30") == []


@pytest.mark.unit
def test_radar_auto_check_no_log_writes_nothing(monkeypatch, tmp_path):
    _ready_fixture(monkeypatch, tmp_path)
    result = radar_runner.invoke(
        mes_cli.mes_app,
        ["radar", "--no-watch", "--auto-check", "--no-llm", "--no-log"],
    )
    assert result.exit_code == 0
    assert "Deterministic Verdict" in result.output
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    assert journal.load_day("2026-03-30") == []


# ---------------------------------------------------------------------------
# radar --auto-check in watch mode: transition fire + cooldown re-fire
# ---------------------------------------------------------------------------


def _invoke_radar_watch(monkeypatch, tmp_path, ticks: int, extra_args=None):
    """Run `radar --watch 1 --auto-check` for exactly `ticks` ticks.

    The first `ticks - 1` sleeps pass, then sleep raises KeyboardInterrupt so
    the loop exits after `ticks` iterations. All ticks use the same stamp.
    """
    _ready_fixture(monkeypatch, tmp_path)
    sleeps: list[int] = []

    def fake_sleep(seconds):
        if len(sleeps) >= ticks - 1:
            raise KeyboardInterrupt
        sleeps.append(seconds)

    monkeypatch.setattr(time, "sleep", fake_sleep)
    return radar_runner.invoke(mes_cli.mes_app, ["radar", "--watch", "1", "--auto-check", "--no-llm"] + (extra_args or []))


@pytest.mark.unit
def test_radar_watch_auto_check_fires_once_and_cooldown_suppresses_refire(monkeypatch, tmp_path):
    result = _invoke_radar_watch(monkeypatch, tmp_path, ticks=2, extra_args=["--auto-check-cooldown", "5"])
    assert result.exit_code == 0, result.output
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    records = [e for e in journal.load_day("2026-03-30") if e["kind"] == "check"]
    # Tick 1: transition into READY fires. Tick 2: still READY within the
    # 5-minute cooldown on the same stamp -> suppressed.
    assert len(records) == 1


@pytest.mark.unit
def test_radar_watch_auto_check_refires_after_cooldown(monkeypatch, tmp_path):
    result = _invoke_radar_watch(monkeypatch, tmp_path, ticks=2, extra_args=["--auto-check-cooldown", "0"])
    assert result.exit_code == 0, result.output
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    records = [e for e in journal.load_day("2026-03-30") if e["kind"] == "check"]
    # Cooldown 0: the second READY tick is already at/past the cooldown -> refire.
    assert len(records) == 2


@pytest.mark.unit
def test_radar_watch_without_auto_check_writes_no_journal(monkeypatch, tmp_path):
    snap = _make_snapshot(overnight_high=7681.0)
    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(mes_cli, "_market_now", lambda cfg: DEFAULT_AS_OF)
    monkeypatch.setattr(mes_cli, "DEFAULT_CONFIG", {"mes_journal_dir": str(tmp_path)})
    monkeypatch.setattr(mes_cli, "evaluate", lambda s, side: _make_result())
    sleeps: list[int] = []

    def fake_sleep(seconds):
        if sleeps:
            raise KeyboardInterrupt
        sleeps.append(seconds)

    monkeypatch.setattr(time, "sleep", fake_sleep)
    result = radar_runner.invoke(mes_cli.mes_app, ["radar", "--watch", "1"])
    assert result.exit_code == 0, result.output
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    assert journal.load_day("2026-03-30") == []

