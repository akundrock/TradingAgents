"""Tests for MR surfacing in render_checklist (spec Part A1)."""

from __future__ import annotations

import dataclasses

import pytest

from tradingagents.mes.checklist import evaluate
from tradingagents.mes.config import load_mes_config
from tradingagents.mes.render import render_checklist

from tests.mes_factories import make_mes_series, make_snapshot

# Factory geometry (see tests/test_mes_mean_reversion.py): deep hammer close
# 97.5 vs vwap 99.0, sigma 0.5 -> z=+3.0; volume 1500 confirms; stop = low 94.5
# - 1.0 buffer * atr 1.0 = 93.5; target = vwap.
LONG_HAMMER_BAR = {"open_": 97.55, "high": 97.6, "low": 94.5, "close": 97.5, "volume": 1500.0}
MR_CFG = {"enable_mean_reversion": True, "mr_min_confirmations": 1}


@pytest.mark.unit
def test_default_render_has_no_mr_section():
    result = evaluate(make_snapshot(), "long")
    assert result.mr_side is None
    assert "Mean-reversion" not in render_checklist(result, live=True)


@pytest.mark.unit
def test_mr_candidate_renders_plan_and_additive_notice():
    snapshot = make_snapshot(cfg=load_mes_config(MR_CFG), mes=make_mes_series(bar_kwargs=LONG_HAMMER_BAR))
    result = evaluate(snapshot, "long")
    assert result.mr_entry is True  # non-vacuous

    out = render_checklist(result, live=True)
    assert "### Mean-reversion candidate (informational)" in out
    assert "Fade side: long (ENTRY)" in out
    assert "MR confirmations: 1/1" in out
    assert f"stop {result.mr_stop:.2f}" in out
    assert f"target {result.mr_target:.2f} (VWAP)" in out
    assert "never widens the deterministic verdict" in out
    assert "trend ruling governs" in out


@pytest.mark.unit
def test_mr_watch_state_renders_without_entry_label():
    # z=+3.0 zone holds but volume surge is absent -> mr_confirmations 0 < 1.
    snapshot = make_snapshot(
        cfg=load_mes_config({"enable_mean_reversion": True}),
        mes=make_mes_series(bar_kwargs={"open_": 97.55, "high": 97.6, "low": 94.5, "close": 97.5}),
    )
    result = evaluate(snapshot, "long")
    assert result.mr_side == "long" and result.mr_entry is False

    out = render_checklist(result, live=True)
    assert "Fade side: long (watch)" in out
    assert "ENTRY" not in out


@pytest.mark.unit
def test_mr_section_tolerates_unpriced_plan():
    result = evaluate(make_snapshot(cfg=load_mes_config(MR_CFG), mes=make_mes_series(bar_kwargs=LONG_HAMMER_BAR)), "long")
    unpriced = dataclasses.replace(result, mr_stop=None, mr_target=None)
    out = render_checklist(unpriced, live=True)
    section = out.split("### Mean-reversion candidate")[1]
    assert "Plan: stop" not in section  # unpriced plan is omitted, not guessed
    assert "Plan: target" not in section
