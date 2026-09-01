from datetime import datetime, timedelta

import pytest

from tradingagents.mes.checklist import (
    ChecklistResult,
    detect_divergence,
    evaluate,
    max_achievable_score,
    required_confirmations,
    score_tier,
    tier_contract_band,
)
from tradingagents.mes.config import load_mes_config

from tests.mes_factories import (
    DEFAULT_AS_OF,
    find_item,
    make_mes_series,
    make_snapshot,
    make_spy_series,
)


def _internals(add=300.0, tick=100.0, vold=1000.0, count=6):
    return [(add, tick, vold)] * count


def _internals_last_tick_only(tick: float, count: int = 6):
    """History with neutral prior ticks so boundary tests are not skewed by streaks."""
    base = [(300.0, 0.0, 1000.0)] * (count - 1)
    return base + [(300.0, tick, 1000.0)]


# ---------------------------------------------------------------------------
# Scoring primitives
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("count", "tier"),
    [
        (0, "low"),
        (3, "low"),
        (4, "marginal"),
        (5, "marginal"),
        (6, "standard"),
        (7, "standard"),
        (8, "premium"),
        (9, "premium"),
    ],
)
def test_score_tier_boundaries(count, tier):
    assert score_tier(count) == tier


@pytest.mark.unit
@pytest.mark.parametrize(
    ("count", "expected"),
    [(0, 3), (3, 3), (4, 3), (5, 3), (6, 4), (7, 4), (8, 5)],
)
def test_required_confirmations_follows_the_tier(count, expected):
    assert required_confirmations(count, load_mes_config()) == expected


@pytest.mark.unit
def test_required_confirmations_falls_back_to_flat_minimum_when_tiers_disabled():
    cfg = load_mes_config({"enable_tier_min_confirmations": False, "min_confirmations": 7})
    assert required_confirmations(8, cfg) == 7
    assert required_confirmations(0, cfg) == 7


@pytest.mark.unit
def test_max_achievable_score_defaults_to_the_sum_of_all_weights():
    assert max_achievable_score(load_mes_config()) == 9


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"enable_momentum": False}, 7),
        ({"enable_vold": False, "enable_tick": False}, 7),
        ({"momentum_score_weight": 4}, 11),
        (
            {
                "enable_momentum": False,
                "enable_vwap": False,
                "enable_atr_range": False,
                "enable_pattern": False,
                "enable_add": False,
                "enable_tick": False,
                "enable_vold": False,
                "enable_volume_surge": False,
            },
            0,
        ),
    ],
)
def test_max_achievable_score_drops_disabled_signals(overrides, expected):
    assert max_achievable_score(load_mes_config(overrides)) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tier", "band"),
    [
        ("premium", (2, 3)),
        ("standard", (1, 2)),
        ("marginal", (1, 1)),
        ("low", (0, 0)),
        ("nonsense", (0, 0)),
    ],
)
def test_tier_contract_band(tier, band):
    assert tier_contract_band(tier) == band


# ---------------------------------------------------------------------------
# Individual MES signals
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("add", "side", "expected"),
    [
        (249.9, "long", False),
        (250.0, "long", False),
        (250.1, "long", True),
        (-249.9, "short", False),
        (-250.0, "short", False),
        (-250.1, "short", True),
        (250.1, "short", False),
    ],
)
def test_add_signal_boundary(add, side, expected):
    snapshot = make_snapshot(spy=make_spy_series(internals=_internals(add=add)))
    item = find_item(evaluate(snapshot, side).mes_items, "$ADD confirms")
    assert item.passed is expected


@pytest.mark.unit
def test_add_signal_reports_unavailable_when_internals_are_missing():
    snapshot = make_snapshot(spy=make_spy_series(internals=_internals(add=None)))
    item = find_item(evaluate(snapshot, "long").mes_items, "$ADD confirms")
    assert item.passed is False
    assert item.observed == "unavailable"


@pytest.mark.unit
def test_add_signal_is_skipped_when_disabled():
    cfg = load_mes_config({"enable_add": False, "allow_missing_internals": True})
    snapshot = make_snapshot(cfg=cfg, spy=make_spy_series(internals=_internals(add=5000.0)))
    item = find_item(evaluate(snapshot, "long").mes_items, "$ADD confirms")
    assert item.passed is False
    assert item.note == "signal disabled"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tick", "side", "expected"),
    [
        (599.9, "long", False),
        (600.0, "long", False),
        (600.1, "long", True),
        (-600.1, "short", True),
        (-600.0, "short", False),
    ],
)
def test_tick_signal_boundary(tick, side, expected):
    cfg = load_mes_config({"use_dynamic_tick_threshold": False})
    snapshot = make_snapshot(
        cfg=cfg,
        spy=make_spy_series(internals=_internals_last_tick_only(tick)),
    )
    item = find_item(evaluate(snapshot, side).mes_items, "$TICK confirms")
    assert item.passed is expected


@pytest.mark.unit
def test_tick_persistent_streak_confirms_without_crossing_threshold():
    cfg = load_mes_config(
        {
            "use_dynamic_tick_threshold": True,
            "tick_lookback": 5,
            "tick_multiplier": 1.5,
            "tick_persistent_bars": 3,
        }
    )
    internals = [(300.0, 50.0, 1000.0)] * 5
    snapshot = make_snapshot(cfg=cfg, spy=make_spy_series(internals=internals))
    item = find_item(evaluate(snapshot, "long").mes_items, "$TICK confirms")
    assert item.passed is True
    assert "persistent buy" in item.observed


@pytest.mark.unit
@pytest.mark.parametrize(
    ("volds", "side", "expected"),
    [
        ([100.0, 200.0], "long", True),
        ([200.0, 200.0], "long", False),
        ([200.0, 100.0], "long", False),
        ([-100.0, -200.0], "short", True),
        ([-200.0, -200.0], "short", False),
        ([-200.0, -100.0], "short", False),
        ([100.0, -50.0], "long", False),
    ],
)
def test_vold_signal_requires_a_rising_or_falling_trend(volds, side, expected):
    internals = [(300.0, 100.0, v) for v in volds]
    snapshot = make_snapshot(spy=make_spy_series(internals=internals))
    item = find_item(evaluate(snapshot, side).mes_items, "$VOLD confirms")
    assert item.passed is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("vold", "side", "expected"),
    [
        (0.1, "long", True),
        (0.0, "long", False),
        (-0.1, "short", True),
        (0.0, "short", False),
    ],
)
def test_vold_signal_uses_level_only_when_trend_is_disabled(vold, side, expected):
    cfg = load_mes_config({"vold_use_trend": False})
    snapshot = make_snapshot(cfg=cfg, spy=make_spy_series(internals=_internals(vold=vold)))
    item = find_item(evaluate(snapshot, side).mes_items, "$VOLD confirms")
    assert item.passed is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("close", "vwap", "side", "expected"),
    [
        (100.0, 99.0, "long", True),
        (99.0, 99.0, "long", False),
        (98.0, 99.0, "long", False),
        (98.0, 99.0, "short", True),
        (99.0, 99.0, "short", False),
        (100.0, 99.0, "short", False),
    ],
)
def test_mes_vwap_side_signal(close, vwap, side, expected):
    mes = make_mes_series(close=close, vwap=vwap)
    item = find_item(evaluate(make_snapshot(mes=mes), side).mes_items, "correct side of VWAP")
    assert item.passed is expected


@pytest.mark.unit
def test_mes_vwap_signal_fails_before_vwap_is_ready():
    mes = make_mes_series(close=100.0, vwap=99.0, vwap_ready=False)
    item = find_item(evaluate(make_snapshot(mes=mes), "long").mes_items, "correct side of VWAP")
    assert item.passed is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("close", "expected"),
    [
        (100.0, True),
        (96.0, False),
        (104.0, False),
        (96.01, True),
        (105.0, False),
        (95.0, False),
    ],
)
def test_atr_band_exhaustion(close, expected):
    mes = make_mes_series(close=close, upper_atr_band=104.0, lower_atr_band=96.0)
    item = find_item(evaluate(make_snapshot(mes=mes), "long").mes_items, "ATR session bands")
    assert item.passed is expected


@pytest.mark.unit
def test_atr_band_signal_fails_without_a_session_start():
    mes = make_mes_series(has_session_start=False)
    item = find_item(evaluate(make_snapshot(mes=mes), "long").mes_items, "ATR session bands")
    assert item.passed is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("volume", "expected"),
    [(1199.0, False), (1200.0, False), (1201.0, True), (5000.0, True)],
)
def test_volume_surge_boundary_at_the_multiplier(volume, expected):
    mes = make_mes_series(volume=volume, avg_volume=1000.0)
    item = find_item(evaluate(make_snapshot(mes=mes), "long").mes_items, "Volume surge")
    assert item.passed is expected


@pytest.mark.unit
def test_volume_surge_fails_before_the_average_is_ready():
    mes = make_mes_series(volume=5000.0, avg_volume=1000.0, volume_ready=False)
    item = find_item(evaluate(make_snapshot(mes=mes), "long").mes_items, "Volume surge")
    assert item.passed is False


@pytest.mark.unit
def test_momentum_signal_requires_a_cross_and_a_rising_laguerre():
    mes = make_mes_series(
        prev_sma=98.0,
        prev_vwap=99.0,
        sma=100.0,
        vwap=99.0,
        laguerre=0.6,
        prev_laguerre=0.5,
        laguerre_ready=True,
    )
    item = find_item(evaluate(make_snapshot(mes=mes), "long").mes_items, "Momentum")
    assert item.passed is True
    assert item.weight == 2


@pytest.mark.unit
def test_momentum_signal_fails_when_laguerre_is_turning_down():
    mes = make_mes_series(
        prev_sma=98.0,
        prev_vwap=99.0,
        sma=100.0,
        vwap=99.0,
        laguerre=0.4,
        prev_laguerre=0.5,
        laguerre_ready=True,
    )
    item = find_item(evaluate(make_snapshot(mes=mes), "long").mes_items, "Momentum")
    assert item.passed is False


@pytest.mark.unit
def test_pattern_signal_detects_a_hammer_for_a_long():
    mes = make_mes_series(bar_kwargs={"open_": 100.0, "high": 100.4, "low": 98.0, "close": 100.2})
    item = find_item(evaluate(make_snapshot(mes=mes), "long").mes_items, "Reversal pattern")
    assert item.passed is True


@pytest.mark.unit
def test_pattern_signal_detects_an_inverse_hammer_for_a_short():
    mes = make_mes_series(bar_kwargs={"open_": 100.0, "high": 102.5, "low": 99.8, "close": 100.1})
    item = find_item(evaluate(make_snapshot(mes=mes), "short").mes_items, "Reversal pattern")
    assert item.passed is True


@pytest.mark.unit
def test_pattern_signal_fails_on_a_zero_range_bar():
    mes = make_mes_series(bar_kwargs={"open_": 100.0, "high": 100.0, "low": 100.0, "close": 100.0})
    item = find_item(evaluate(make_snapshot(mes=mes), "long").mes_items, "Reversal pattern")
    assert item.passed is False


# ---------------------------------------------------------------------------
# SPY confluence and divergence
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("add", "expected"),
    [(1000.0, False), (1000.1, True), (999.9, False)],
)
def test_spy_add_trend_boundary(add, expected):
    snapshot = make_snapshot(spy=make_spy_series(internals=_internals(add=add)))
    item = find_item(evaluate(snapshot, "long").spy_items, "$ADD breadth trending")
    assert item.passed is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tick", "side", "exhausted"),
    [
        (1000.0, "long", True),
        (999.9, "long", False),
        (-1000.0, "short", True),
        (1000.0, "short", False),
    ],
)
def test_spy_room_to_target_flags_exhaustion_extremes(tick, side, exhausted):
    snapshot = make_snapshot(spy=make_spy_series(internals=_internals(tick=tick)))
    item = find_item(evaluate(snapshot, side).spy_items, "Room to target")
    assert item.passed is (not exhausted)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("prev_close", "close", "vold", "expected"),
    [
        (500.0, 501.0, -100.0, "bearish"),
        (500.0, 499.0, 100.0, "bullish"),
        (500.0, 501.0, 100.0, "none"),
        (500.0, 499.0, -100.0, "none"),
    ],
)
def test_detect_divergence(prev_close, close, vold, expected):
    from tests.mes_factories import make_bar

    bars = [
        make_bar(DEFAULT_AS_OF - timedelta(minutes=5), close=prev_close, vold=0.0, add=300.0, tick=100.0),
        make_bar(DEFAULT_AS_OF, close=close, vold=vold, add=300.0, tick=100.0),
    ]
    spy = make_spy_series(internals=[(300.0, 100.0, 0.0), (300.0, 100.0, vold)])
    spy.bars = bars
    spy.prev_vold = 0.0
    assert detect_divergence(make_snapshot(spy=spy)) == expected


@pytest.mark.unit
def test_no_trade_when_vold_divergence_opposes_side():
    from tests.mes_factories import make_bar

    bars = [
        make_bar(DEFAULT_AS_OF - timedelta(minutes=5), close=500.0, vold=0.0, add=300.0, tick=100.0),
        make_bar(DEFAULT_AS_OF, close=501.0, vold=-100.0, add=300.0, tick=100.0),
    ]
    spy = make_spy_series(internals=[(300.0, 100.0, 0.0), (300.0, 100.0, -100.0)])
    spy.bars = bars
    result = evaluate(make_snapshot(spy=spy), "long")
    assert any("diverging bearishly" in reason for reason in result.no_trade_reasons)


@pytest.mark.unit
def test_spy_confluence_requires_three_of_five():
    weak = make_snapshot(spy=make_spy_series(internals=_internals(add=0.0, tick=0.0)))
    result = evaluate(weak, "long")
    assert result.spy_confirmations < 3
    assert result.spy_confluence_ok is False
    assert any("SPY confirmations" in r for r in result.no_trade_reasons)
    assert result.tradeable is False


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def _open_gate_snapshot(**kwargs):
    return make_snapshot(spy=make_spy_series(internals=_internals(add=1200.0, tick=700.0)), **kwargs)


@pytest.mark.unit
def test_gates_open_midsession():
    result = evaluate(_open_gate_snapshot(), "long")
    assert result.gates_ok is True
    assert result.gate_reasons == []


@pytest.mark.unit
def test_morning_block_before_0945():
    as_of = DEFAULT_AS_OF.replace(hour=9, minute=40)
    result = evaluate(_open_gate_snapshot(as_of=as_of), "long")
    assert result.gates_ok is False
    assert any("morning block" in r for r in result.gate_reasons)
    assert result.tradeable is False
    assert result.direction == "none"


@pytest.mark.unit
def test_morning_block_lifts_when_orb_window_allowed():
    as_of = DEFAULT_AS_OF.replace(hour=9, minute=40)
    cfg = load_mes_config({"allow_orb_window": True})
    result = evaluate(_open_gate_snapshot(as_of=as_of, cfg=cfg), "long")
    assert result.gates_ok is True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("hour", "minute", "fragment"),
    [
        (15, 30, "past last entry time"),
        (15, 45, "past last entry time"),
        (15, 55, "past EOD flatten"),
    ],
)
def test_late_session_gates(hour, minute, fragment):
    as_of = DEFAULT_AS_OF.replace(hour=hour, minute=minute)
    result = evaluate(_open_gate_snapshot(as_of=as_of), "long")
    assert result.gates_ok is False
    assert any(fragment in r for r in result.gate_reasons)
    assert result.tradeable is False


@pytest.mark.unit
@pytest.mark.parametrize(("hour", "minute"), [(8, 0), (17, 0)])
def test_outside_rth_is_blocked(hour, minute):
    as_of = DEFAULT_AS_OF.replace(hour=hour, minute=minute)
    result = evaluate(_open_gate_snapshot(as_of=as_of), "long")
    assert result.gates_ok is False
    assert any("outside RTH" in r for r in result.gate_reasons)
    assert result.tradeable is False


@pytest.mark.unit
def test_weekend_is_blocked():
    saturday = datetime(2026, 4, 4, 11, 0)
    result = evaluate(_open_gate_snapshot(as_of=saturday), "long")
    assert result.gates_ok is False
    assert any("weekend" in r for r in result.gate_reasons)


@pytest.mark.unit
def test_missing_internals_block_the_gates():
    snapshot = make_snapshot(spy=make_spy_series(internals=_internals(add=None, tick=None)))
    result = evaluate(snapshot, "long")
    assert result.gates_ok is False
    reason = next(r for r in result.gate_reasons if "internals missing" in r)
    assert "$ADD" in reason and "$TICK" in reason


@pytest.mark.unit
def test_missing_internals_allowed_when_configured():
    cfg = load_mes_config({"allow_missing_internals": True})
    snapshot = make_snapshot(
        cfg=cfg, spy=make_spy_series(internals=_internals(add=None, tick=None, vold=None))
    )
    assert evaluate(snapshot, "long").gates_ok is True


@pytest.mark.unit
def test_min_atr_points_gate():
    cfg = load_mes_config({"min_atr_points": 5.0})
    snapshot = _open_gate_snapshot(cfg=cfg, mes=make_mes_series(atr=1.0))
    result = evaluate(snapshot, "long")
    assert result.gates_ok is False
    assert any("below minimum" in r for r in result.gate_reasons)


@pytest.mark.unit
def test_session_filters_can_be_disabled():
    as_of = DEFAULT_AS_OF.replace(hour=9, minute=35)
    cfg = load_mes_config({"enforce_session_filters": False})
    assert evaluate(_open_gate_snapshot(as_of=as_of, cfg=cfg), "long").gates_ok is True


# ---------------------------------------------------------------------------
# No-trade conditions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_add_chop_zone_with_flat_vold_is_a_no_trade():
    internals = [(400.0, 700.0, 1000.0)] * 6
    snapshot = make_snapshot(spy=make_spy_series(internals=internals))
    result = evaluate(snapshot, "long")
    assert any("chop zone" in r for r in result.no_trade_reasons)
    assert result.tradeable is False


@pytest.mark.unit
def test_add_chop_zone_is_tolerated_when_vold_is_moving():
    internals = [(400.0, 700.0, float(1000 + 100 * i)) for i in range(6)]
    snapshot = make_snapshot(spy=make_spy_series(internals=internals))
    result = evaluate(snapshot, "long")
    assert not any("chop zone" in r for r in result.no_trade_reasons)


@pytest.mark.unit
def test_tick_whipsaw_is_a_no_trade():
    ticks = [100.0, -100.0, 120.0, -80.0, 90.0, -70.0]
    internals = [(1200.0, t, float(1000 + 100 * i)) for i, t in enumerate(ticks)]
    snapshot = make_snapshot(spy=make_spy_series(internals=internals))
    result = evaluate(snapshot, "long")
    assert any("whipsawing" in r for r in result.no_trade_reasons)
    assert result.tradeable is False


@pytest.mark.unit
def test_tick_exhaustion_extreme_is_a_no_trade_for_that_side():
    internals = [(1200.0, 1100.0, float(1000 + 100 * i)) for i in range(6)]
    snapshot = make_snapshot(spy=make_spy_series(internals=internals))
    long_result = evaluate(snapshot, "long")
    assert any("exhaustion extreme" in r for r in long_result.no_trade_reasons)
    assert long_result.tradeable is False

    short_result = evaluate(snapshot, "short")
    assert not any("exhaustion extreme" in r for r in short_result.no_trade_reasons)


# ---------------------------------------------------------------------------
# Whole-result behaviour
# ---------------------------------------------------------------------------


def _premium_long_snapshot():
    """Every MES signal true, every SPY confirmation true, gates open."""
    internals = [(1200.0, 100.0, float(1000 + 100 * i)) for i in range(17)]
    internals += [(1200.0, 800.0, float(1000 + 100 * i)) for i in range(17, 20)]
    mes = make_mes_series(
        volume=5000.0,
        bar_kwargs={"open_": 100.0, "high": 100.4, "low": 98.0, "close": 100.2},
        close=100.2,
        prev_sma=98.0,
        prev_vwap=99.0,
        sma=100.0,
        vwap=99.0,
        laguerre=0.6,
        prev_laguerre=0.5,
        laguerre_ready=True,
    )
    spy = make_spy_series(internals=internals, close=500.0, vwap=499.0)
    return make_snapshot(mes=mes, spy=spy)


@pytest.mark.unit
def test_full_confluence_long_is_tradeable():
    result = evaluate(_premium_long_snapshot(), "long")
    assert result.confirmations == 8
    assert result.score == 9
    assert result.max_score == 9
    assert result.tier == "premium"
    assert result.required == 5
    assert result.score_ok is True
    assert result.gates_ok is True
    assert result.spy_confirmations == 5
    assert result.no_trade_reasons == []
    assert result.tradeable is True
    assert result.direction == "long"


@pytest.mark.unit
def test_tradeable_is_false_whenever_gates_are_closed():
    snapshot = _premium_long_snapshot()
    snapshot.as_of = snapshot.as_of.replace(hour=9, minute=31)
    result = evaluate(snapshot, "long")
    assert result.gates_ok is False
    assert result.tradeable is False
    assert result.direction == "none"


@pytest.mark.unit
def test_low_tier_is_not_tradeable_even_when_every_other_condition_passes():
    """A tier that sizes to zero contracts must not report as a trade."""
    result = ChecklistResult(
        as_of_label="2026-03-30 11:00 ET",
        side="long",
        direction="long",
        score=3,
        max_score=9,
        confirmations=3,
        required=3,
        tier="low",
        score_ok=True,
        gates_ok=True,
        spy_confirmations=4,
        spy_confluence_ok=True,
    )
    assert tier_contract_band(result.tier) == (0, 0)
    assert result.tradeable is False

    result.tier = "marginal"
    assert result.tradeable is True


@pytest.mark.unit
def test_auto_side_picks_the_stronger_side():
    internals = [(-1200.0, -700.0, float(-1000 - 100 * i)) for i in range(6)]
    mes = make_mes_series(close=98.0, vwap=99.0)
    snapshot = make_snapshot(mes=mes, spy=make_spy_series(internals=internals, close=497.0))
    assert evaluate(snapshot, "auto").side == "short"


@pytest.mark.unit
def test_auto_side_defaults_to_long_on_a_tie():
    internals = [(0.0, 0.0, 0.0)] * 6
    # A doji with symmetric wicks scores identically for both sides.
    mes = make_mes_series(
        close=99.0,
        vwap=99.0,
        bar_kwargs={"open_": 99.0, "high": 99.5, "low": 98.5, "close": 99.0},
    )
    snapshot = make_snapshot(mes=mes, spy=make_spy_series(internals=internals))
    assert evaluate(snapshot, "auto").side == "long"


@pytest.mark.unit
def test_result_carries_levels_and_warnings_through():
    snapshot = make_snapshot(warnings=["internals unavailable: boom"])
    result = evaluate(snapshot, "long")
    assert result.vwap == snapshot.mes.vwap
    assert result.atr == snapshot.mes.atr
    assert result.upper_atr_band == snapshot.mes.upper_atr_band
    assert result.lower_atr_band == snapshot.mes.lower_atr_band
    assert result.opening_range_high == snapshot.mes.opening_range_high
    assert result.opening_range_low == snapshot.mes.opening_range_low
    assert result.last_price == snapshot.mes.close
    assert result.warnings == ["internals unavailable: boom"]
    assert result.as_of_label == "2026-03-30 11:00 ET"


@pytest.mark.unit
def test_all_items_is_spy_then_mes():
    result = evaluate(make_snapshot(), "long")
    assert len(result.spy_items) == 5
    assert len(result.mes_items) == 8
    assert result.all_items == result.spy_items + result.mes_items
    assert {i.chart for i in result.spy_items} == {"SPY"}
    assert {i.chart for i in result.mes_items} == {"MES"}
