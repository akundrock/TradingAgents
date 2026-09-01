import pytest

from tradingagents.mes.config import load_mes_config
from tradingagents.mes.internals import (
    tick_confirms_side,
    tick_effective_threshold,
    tick_signal,
    tick_streak,
    vold_bar_divergence,
    vold_confirms_side,
    vold_z_score,
)


@pytest.mark.unit
def test_tick_effective_threshold_uses_static_fallback_when_dynamic_disabled():
    cfg = load_mes_config({"use_dynamic_tick_threshold": False})
    assert tick_effective_threshold([100.0, 200.0, 300.0], cfg) == 600.0


@pytest.mark.unit
def test_tick_effective_threshold_scales_with_mean_absolute_tick():
    cfg = load_mes_config({"use_dynamic_tick_threshold": True, "tick_lookback": 4, "tick_multiplier": 1.5})
    ticks = [100.0, 200.0, 300.0, 400.0]
    assert tick_effective_threshold(ticks, cfg) == pytest.approx(375.0)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ticks", "positive", "expected"),
    [
        ([50.0, 60.0, 70.0], True, 3),
        ([50.0, -10.0, 70.0], True, 1),
        ([-50.0, -60.0, -70.0], False, 3),
        ([], True, 0),
    ],
)
def test_tick_streak_counts_consecutive_same_sign_bars(ticks, positive, expected):
    assert tick_streak(ticks, positive=positive) == expected


@pytest.mark.unit
def test_tick_signal_classifies_bull_bear_persistent_and_neutral():
    cfg = load_mes_config(
        {
            "use_dynamic_tick_threshold": True,
            "tick_lookback": 5,
            "tick_multiplier": 1.5,
            "tick_persistent_bars": 3,
        }
    )
    quiet = [40.0, 50.0, 45.0, 55.0, 50.0]
    assert tick_signal(120.0, quiet, cfg) == "bull"
    assert tick_signal(-120.0, quiet, cfg) == "bear"
    assert tick_signal(50.0, quiet, cfg) == "persistent_buy"
    assert tick_signal(-50.0, [-40.0, -45.0, -50.0, -55.0, -50.0], cfg) == "persistent_sell"
    assert tick_signal(10.0, [10.0, -10.0, 10.0, -10.0, 10.0], cfg) == "neutral"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("signal", "side", "expected"),
    [
        ("bull", "long", True),
        ("persistent_buy", "long", True),
        ("bear", "short", True),
        ("persistent_sell", "short", True),
        ("bull", "short", False),
        ("neutral", "long", False),
    ],
)
def test_tick_confirms_side(signal, side, expected):
    assert tick_confirms_side(signal, side) is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("vold", "close", "prev_close", "expected"),
    [
        (-100.0, 501.0, 500.0, "bearish"),
        (100.0, 499.0, 500.0, "bullish"),
        (100.0, 501.0, 500.0, "none"),
        (-100.0, 499.0, 500.0, "none"),
    ],
)
def test_vold_bar_divergence_matches_tos_logic(vold, close, prev_close, expected):
    assert vold_bar_divergence(vold, close, prev_close) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("vold", "close", "prev_close", "side", "expected"),
    [
        (100.0, 501.0, 500.0, "long", True),
        (-100.0, 499.0, 500.0, "short", True),
        (100.0, 499.0, 500.0, "long", False),
    ],
)
def test_vold_confirms_side(vold, close, prev_close, side, expected):
    assert vold_confirms_side(vold, close, prev_close, side) is expected


@pytest.mark.unit
def test_vold_z_score_returns_none_with_insufficient_history():
    assert vold_z_score([100.0], 20) is None


@pytest.mark.unit
def test_vold_z_score_computes_sample_z_score():
    values = [100.0] * 19 + [200.0]
    z = vold_z_score(values, 20)
    assert z is not None
    assert z > 0
