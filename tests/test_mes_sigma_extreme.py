"""B-port: checklist-side sigma-extreme gate (MES_PatternDetector requireVwapExtreme).

Scenarios mirror tests/test_sigma_extreme.py in mes-tuner (same SessionVWAP
convention: offset = (vwap - close) for a hammer long, (close - vwap) for an
inverse hammer, divided by the volume-weighted deviation). The factory's
SeriesState ships vwap=99.0 with vwap_sigma=0.5, so a close of 99.5 sits at
z = (99.0 - 99.5) / 0.5 = -1.0 on the long side, and 97.5 at z = +3.0.
"""

import pytest

from tradingagents.mes.checklist import evaluate
from tradingagents.mes.config import MesChecklistConfig, load_mes_config

from tests.mes_factories import find_item, make_mes_series, make_snapshot


def _pattern_item(snapshot, side):
    return find_item(evaluate(snapshot, side).mes_items, "Reversal pattern")


@pytest.mark.unit
def test_sigma_extreme_flags_default_off_with_tuner_threshold():
    cfg = MesChecklistConfig()
    assert cfg.pattern_require_sigma_extreme is False
    assert cfg.pattern_extreme_sigma == 1.5


@pytest.mark.unit
def test_tuner_aliases_map_sigma_flags():
    cfg = load_mes_config(
        {"PatternRequireSigmaExtreme": True, "PatternExtremeSigma": 2.0}
    )
    assert cfg.pattern_require_sigma_extreme is True
    assert cfg.pattern_extreme_sigma == 2.0


@pytest.mark.unit
def test_sigma_extreme_blocks_hammer_near_vwap_when_flag_on():
    mes = make_mes_series(
        bar_kwargs={
            "open_": 99.55,
            "high": 99.6,
            "low": 96.5,
            "close": 99.5,
        }
    )
    on = load_mes_config({"pattern_require_sigma_extreme": True})
    off = load_mes_config({})

    # Flag on: hammer geometry is valid but the close sits only 1 sigma below
    # VWAP, so the extreme filter vetoes the pattern.
    assert _pattern_item(make_snapshot(cfg=on, mes=mes), "long").passed is False
    # Flag off (recorded baseline): the same bar confirms the hammer.
    assert _pattern_item(make_snapshot(cfg=off, mes=mes), "long").passed is True


@pytest.mark.unit
def test_sigma_extreme_threshold_sensitivity():
    """A 1-sigma hammer is only admitted when the threshold is relaxed below 1."""
    mes = make_mes_series(bar_kwargs={"open_": 98.55, "high": 98.6, "low": 95.5, "close": 98.5})
    relaxed = load_mes_config({"pattern_require_sigma_extreme": True, "pattern_extreme_sigma": 0.3})
    strict = load_mes_config({"pattern_require_sigma_extreme": True, "pattern_extreme_sigma": 1.5})
    assert _pattern_item(make_snapshot(cfg=relaxed, mes=mes), "long").passed is True
    assert _pattern_item(make_snapshot(cfg=strict, mes=mes), "long").passed is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("extreme_sigma", "expected"),
    [(1.5, True), (2.0, True), (3.0, True), (4.0, False)],
)
def test_sigma_extreme_deep_hammer_threshold_ladder(extreme_sigma, expected):
    """z = (99.0 - 97.5) / 0.5 = 3.0: passes 1.5/2.0 and the >= boundary at 3.0."""
    mes = make_mes_series(bar_kwargs={"open_": 97.55, "high": 97.6, "low": 94.5, "close": 97.5})
    cfg = load_mes_config({"pattern_require_sigma_extreme": True, "pattern_extreme_sigma": extreme_sigma})
    assert _pattern_item(make_snapshot(cfg=cfg, mes=mes), "long").passed is expected


@pytest.mark.unit
def test_sigma_extreme_short_side_symmetry():
    """The inverse-hammer path applies (close - vwap) / sigma symmetrically."""
    deep = make_mes_series(bar_kwargs={"open_": 100.05, "high": 103.0, "low": 99.95, "close": 100.0})
    shallow = make_mes_series(bar_kwargs={"open_": 99.55, "high": 102.5, "low": 99.5, "close": 99.5})
    on = load_mes_config({"pattern_require_sigma_extreme": True})
    # Deep inverse: close 1.0 above VWAP with sigma 0.5 -> z = 2.0 >= 1.5, passes.
    assert _pattern_item(make_snapshot(cfg=on, mes=deep), "short").passed is True
    # Shallow inverse: z = (99.5 - 99.0) / 0.5 = 1.0 < 1.5, blocked.
    assert _pattern_item(make_snapshot(cfg=on, mes=shallow), "short").passed is False
    # Flag off restores the plain inverse-hammer verdict on the same bars.
    off = load_mes_config({})
    assert _pattern_item(make_snapshot(cfg=off, mes=deep), "short").passed is True
    assert _pattern_item(make_snapshot(cfg=off, mes=shallow), "short").passed is True


@pytest.mark.unit
@pytest.mark.parametrize("sigma", [0.0, None])
def test_sigma_extreme_zero_sigma_blocks_when_flag_on(sigma):
    """Sigma 0 (no volume yet) or a not-ready VWAP never passes the gate."""
    overrides = {"vwap_ready": False} if sigma is None else {"vwap_sigma": 0.0}
    cfg = load_mes_config({"pattern_require_sigma_extreme": True})
    mes = make_mes_series(
        bar_kwargs={"open_": 97.55, "high": 97.6, "low": 94.5, "close": 97.5},
        **overrides,
    )
    snapshot = make_snapshot(cfg=cfg, mes=mes)
    assert _pattern_item(snapshot, "long").passed is False
