import pytest

from tradingagents.mes.config import load_mes_config
from tradingagents.mes.sizing import size_position, suggest_stop_distance_points


@pytest.mark.unit
def test_raw_contract_math_before_any_cap():
    cfg = load_mes_config({"max_contracts": 100})
    result = size_position(200.0, 4.0, "premium", cfg)
    assert result.risk_per_contract == 20.0
    # $200 / $20 = 10 raw, then clamped to the premium band's upper bound.
    assert result.tier_band == (2, 3)
    assert result.contracts == 3
    assert result.capped_by == "premium tier band"


@pytest.mark.unit
def test_max_contracts_caps_before_the_tier_band():
    cfg = load_mes_config({"max_contracts": 2})
    result = size_position(200.0, 4.0, "premium", cfg)
    assert result.contracts == 2
    assert result.capped_by == "max_contracts"


@pytest.mark.unit
def test_risk_is_the_limiter_when_it_lands_inside_the_band():
    cfg = load_mes_config()
    result = size_position(60.0, 4.0, "premium", cfg)
    assert result.contracts == 3
    assert result.capped_by == "risk"


@pytest.mark.unit
def test_risk_below_the_band_is_reported_as_such():
    cfg = load_mes_config()
    result = size_position(20.0, 4.0, "premium", cfg)
    assert result.contracts == 1
    assert result.capped_by == "risk (below tier band)"


@pytest.mark.unit
def test_tiers_below_marginal_are_sized_to_zero():
    result = size_position(200.0, 4.0, "low", load_mes_config())
    assert result.contracts == 0
    assert result.tier_band == (0, 0)
    assert result.capped_by == "tier below marginal"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tier", "expected"),
    [("premium", 3), ("standard", 2), ("marginal", 1), ("low", 0)],
)
def test_tier_band_ceilings(tier, expected):
    assert size_position(200.0, 4.0, tier, load_mes_config()).contracts == expected


@pytest.mark.unit
def test_result_echoes_its_inputs():
    cfg = load_mes_config()
    result = size_position(150.0, 3.0, "standard", cfg)
    assert result.risk_dollars == 150.0
    assert result.stop_points == 3.0
    assert result.tier == "standard"
    assert result.risk_per_contract == 3.0 * cfg.contract_multiplier


@pytest.mark.unit
@pytest.mark.parametrize("stop_points", [0.0, -1.0])
def test_non_positive_stop_points_raise(stop_points):
    with pytest.raises(ValueError, match="stop_points must be positive"):
        size_position(200.0, stop_points, "standard", load_mes_config())


@pytest.mark.unit
@pytest.mark.parametrize("side", ["long", "short"])
def test_suggest_stop_distance_points_is_always_positive(side):
    assert suggest_stop_distance_points(side, 5000.0, 5000.0, 0.0) > 0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("last_price", "vwap", "atr", "expected"),
    [
        (5010.0, 5000.0, 4.0, 10.0),   # VWAP distance dominates
        (5001.0, 5000.0, 4.0, 4.0),    # ATR dominates
        (5000.25, 5000.0, 0.1, 1.0),   # floored at 1 point
        (4990.0, 5000.0, 4.0, 10.0),   # distance is absolute, side-independent
    ],
)
def test_suggest_stop_points_takes_the_furthest_structure(last_price, vwap, atr, expected):
    assert suggest_stop_distance_points("long", last_price, vwap, atr) == expected
