"""A-port: trader-side MR path (enable_mean_reversion).

Factory-local numbers (vwap=99.0, vwap_sigma=0.5): MR long zone needs
(99.0 - close)/0.5 >= mr_zone_sigma -- default 2.0 -> close <= 98.0; the deep
hammer (close 97.5) sits at z=+3.0. Mirror short: (close - 99.0)/0.5, the
factory inverse hammer at close 100.0 -> z=+2.0. Never reuse tuner numbers.
"""

import pytest

from tradingagents.mes.checklist import evaluate
from tradingagents.mes.config import MesChecklistConfig, load_mes_config

from tests.mes_factories import make_mes_series, make_snapshot, make_spy_series


def _mr_cfg(**overrides):
    return load_mes_config({"enable_mean_reversion": True, **overrides})


LONG_HAMMER_BAR = {"open_": 97.55, "high": 97.6, "low": 94.5, "close": 97.5}  # z=+3.0
INVERSE_HAMMER_BAR = {"open_": 100.05, "high": 103.0, "low": 99.95, "close": 100.0}  # z=+2.0 short


@pytest.mark.unit
def test_mr_defaults_off_inert():
    cfg = MesChecklistConfig()
    assert cfg.enable_mean_reversion is False
    assert cfg.mr_zone_sigma == 2.0
    assert cfg.mr_min_confirmations == 2
    assert cfg.mr_stop_atr_buffer == 1.0


@pytest.mark.unit
def test_mr_long_needs_confirmations():
    # Deep hammer at z=+3.0 (zone passes) but no volume surge and neutral
    # internals: 0 confirmations < mr_min_confirmations=2 -> no MR entry.
    mes = make_mes_series(bar_kwargs=LONG_HAMMER_BAR)
    result = evaluate(make_snapshot(cfg=_mr_cfg(), mes=mes), "long")
    assert result.mr_side == "long"
    assert result.mr_entry is False
    assert result.mr_confirmations == 0


@pytest.mark.unit
def test_mr_long_entry_with_volume_confirm() -> None:
    mes = make_mes_series(bar_kwargs={**LONG_HAMMER_BAR, "volume": 1500.0})
    cfg = _mr_cfg(mr_min_confirmations=1)
    result = evaluate(make_snapshot(cfg=cfg, mes=mes), "long")
    assert result.mr_side == "long"
    assert result.mr_entry is True
    assert result.mr_score >= 2  # zone+trigger point + volume confirm
    assert result.mr_stop is not None and result.mr_stop < result.last_price
    assert result.mr_target is not None and abs(result.mr_target - result.vwap) < 1e-9


@pytest.mark.unit
def test_mr_short_mirror():
    """Short mirror: (close - vwap)/sigma = (100.0 - 99.0)/0.5 = 2.0 >= the
    relaxed MRZoneSigma 1.5, and a +1200 $TICK reading clears the
    tick_extreme_threshold (1000) flipped into the fade-direction confirm."""
    spy = make_spy_series(internals=[(300.0, 1200.0, 1000.0)] * 6)
    mes = make_mes_series(bar_kwargs=INVERSE_HAMMER_BAR)
    cfg = _mr_cfg(mr_zone_sigma=1.5, mr_min_confirmations=1)
    result = evaluate(make_snapshot(cfg=cfg, mes=mes, spy=spy), "short")
    assert result.mr_side == "short"
    assert result.mr_zone is True  # mr_side is selected, so the zone truly held
    assert result.mr_entry is True
    assert result.mr_confirmations == 1  # tick exhaustion; volume + divergence silent
    assert result.mr_score == 2  # zone+trigger point + tick exhaustion confirm
    assert result.mr_stop == 104.0  # high 103.0 + mr_stop_atr_buffer 1.0 * atr 1.0
    assert result.mr_target is not None and abs(result.mr_target - result.vwap) < 1e-9


@pytest.mark.unit
def test_mr_zero_sigma_blocks():
    """Sigma 0 (no volume variance) never qualifies: no MR side at all. The
    zone-hold is geometric (close 97.5 vs vwap 99.0), but mr_zone reports for
    the SELECTED MR side only, so it reads False whenever mr_side is None."""
    mes = make_mes_series(bar_kwargs=LONG_HAMMER_BAR, vwap_sigma=0.0)
    cfg = _mr_cfg(mr_min_confirmations=1)
    result = evaluate(make_snapshot(cfg=cfg, mes=mes), "long")
    assert result.mr_side is None
    assert result.mr_entry is False
    assert result.mr_confirmations == 0 and result.mr_score == 0
    assert result.mr_zone is False and result.mr_trigger is False
    assert result.mr_stop is None and result.mr_target is None
    # The identical bar at the factory sigma 0.5 fires, so the block is the
    # zero-sigma guard, not the bar geometry.
    mes = make_mes_series(bar_kwargs=LONG_HAMMER_BAR)
    result = evaluate(make_snapshot(cfg=_mr_cfg(mr_min_confirmations=1), mes=mes), "long")
    assert result.mr_side == "long"


@pytest.mark.unit
def test_mr_additive_never_touches_trend_verdict():
    """Same bar with MR on vs off: MR may report an entry, yet every trend
    field stays identical (values probe-pinned against the merged engine)."""
    mes = make_mes_series(bar_kwargs={**LONG_HAMMER_BAR, "volume": 1500.0})
    on = evaluate(make_snapshot(cfg=_mr_cfg(mr_min_confirmations=1), mes=mes), "long")
    off = evaluate(make_snapshot(cfg=load_mes_config({}), mes=mes), "long")
    # MR fired on the flag-on run, so the invariance below is non-vacuous.
    assert on.mr_entry is True
    assert off.mr_entry is False and off.mr_side is None
    assert on.tradeable is False and off.tradeable is False
    assert on.score == off.score == 5
    assert on.confirmations == off.confirmations == 5
    assert on.side == off.side == "long"
    assert on.direction == off.direction == "none"
    assert on.tier == off.tier == "marginal"
