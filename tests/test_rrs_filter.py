"""Tests for RrsFilter `both`-direction behavior and data-quality hardening."""

from __future__ import annotations

import math
from datetime import datetime
from unittest.mock import patch

import pandas as pd
import pytest
import math

from tradingagents.dataflows.schwab_streamer import ScreenerCandidate
from tradingagents.intraday.screener_filters.base import ScanContext, ScreenedSymbol
from tradingagents.intraday.screener_filters.rrs_filter import RrsFilter


def _make_5m_bars(rows: int = 30) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Date": pd.date_range("2026-07-27 09:30", periods=rows, freq="5min"),
            "Open": [100.0] * rows,
            "High": [101.0] * rows,
            "Low": [99.0] * rows,
            "Close": [100.0 + i * 0.1 for i in range(rows)],
            "Volume": [1000 + i * 10 for i in range(rows)],
        }
    )


def _make_ctx(
    symbol: str,
    config: dict,
    rrs_by_tf: dict[str, float],
) -> ScanContext:
    """Build a ScanContext with mocked RRS computation."""
    return ScanContext(
        symbol=symbol,
        candidate=ScreenerCandidate(symbol=symbol, last_price=100.0, total_volume=1000),
        df_5m=_make_5m_bars(),
        bar_time=datetime(2026, 7, 27, 10, 30),
        session_start=datetime(2026, 7, 27, 9, 30),
        config=config,
        bench_enriched={},
        session=None,
        shared={},
    )


def _rrs_config(**overrides) -> dict:
    config = {
        "intraday_screener_rrs_timeframes": [5, 30, 60],
        "intraday_screener_min_rrs_aligned": 1,
        "intraday_screener_direction": "both",
        "intraday_screener_rank_rrs_timeframe": "5m",
        "intraday_screener_include_daily_rrs": False,
        "pro_trader_benchmark": "SPY",
    }
    config.update(overrides)
    return config


def _screened(
    symbol: str,
    direction: str,
    rank_score: float,
    aligned_count: int,
) -> ScreenedSymbol:
    return ScreenedSymbol(
        symbol=symbol,
        direction=direction,
        rank_score=rank_score,
        aligned_count=aligned_count,
        rank_rrs_timeframe="5m",
    )


@pytest.mark.unit
def test_both_direction_tiebreak_prefers_higher_alignment():
    """When both long and short pass, the side with more aligned TFs should win
    even if its rank score magnitude is smaller."""
    config = _rrs_config()
    # long-aligned: 30m (0.5), 60m (0.4) = 2 TFs; short-aligned: 5m (-3.0) = 1 TF
    # Both sides pass min_aligned=1. Current code evaluates long first, and since
    # score is identical for both sides (rank TF value), long wins by accident of
    # ordering — even though short has the stronger 5m signal. Desired: the side
    # with MORE aligned timeframes wins -> long (2 aligned vs 1).
    rrs_by_tf = {"5m": -3.0, "30m": 0.5, "60m": 0.4}
    ctx = _make_ctx("TEST", config, rrs_by_tf)

    with patch(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_rrs_multi_timeframe",
        return_value=rrs_by_tf,
    ), patch(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_relative_volume",
        return_value=1.5,
    ):
        result = RrsFilter().evaluate(ctx)

    assert result.passed is True
    assert result.direction == "long"
    assert result.metadata["aligned_count"] == 2


@pytest.mark.unit
def test_merge_watchlist_both_interleaves_sides():
    """With direction=both, the merged watchlist should interleave longs and
    shorts so neither side is crowded out when the cap is hit."""
    from tradingagents.intraday.universe_screener import UniverseScreener

    config = {"intraday_screener_direction": "both", "intraday_screener_max_watchlist": 4}
    screener = UniverseScreener(config)
    screened = [
        _screened("L1", "long", 3.0, 3),
        _screened("L2", "long", 2.0, 3),
        _screened("L3", "long", 1.0, 2),
        _screened("L4", "long", 0.8, 2),
        _screened("S1", "short", -2.5, 2),
        _screened("S2", "short", -1.5, 1),
    ]
    merged = screener._merge_watchlist(
        screened,
        base_watchlist=[],
        max_watchlist=4,
        screener_first=True,
    )
    # Interleaved: best long, best short, next long, next short...
    assert merged[0] == "L1"
    assert "S1" in merged[:2]
    assert len(merged) == 4


@pytest.mark.unit
def test_merge_watchlist_both_lopsided_day_still_yields_shorts():
    """On a strong-market day (many longs, few shorts), shorts must not be
    crowded out of the watchlist entirely."""
    from tradingagents.intraday.universe_screener import _sort_screened_results

    config = {"intraday_screener_direction": "both", "intraday_screener_rank_by": "aligned"}
    screened = [
        _screened(f"L{i}", "long", rank_score=1.0 + i * 0.1, aligned_count=3)
        for i in range(8)
    ] + [
        _screened("S1", "short", -2.0, 2),
        _screened("S2", "short", -1.0, 1),
    ]
    _sort_screened_results(results := screened, config)

    # Simulate merge with cap 4: interleave should include at least one short
    config2 = {"intraday_screener_direction": "both", "intraday_screener_max_watchlist": 4}
    from tradingagents.intraday.universe_screener import UniverseScreener

    screener = UniverseScreener(config)
    merged = screener._merge_watchlist(screened, base_watchlist=[], max_watchlist=4, screener_first=True)
    shorts_in_merged = [s for s in merged if s.startswith("S")]
    assert len(shorts_in_merged) >= 1, f"shorts crowded out: {merged}"


@pytest.mark.unit
def test_sort_screened_results_both_keeps_sides_separate():
    """With rank_by=aligned and direction=both, longs and shorts should each be
    ranked within their own side (longs desc by score, shorts asc), not mixed
    into one list ordered by abs(score)."""
    from tradingagents.intraday.universe_screener import _sort_screened_results

    config = {"intraday_screener_direction": "both", "intraday_screener_rank_by": "aligned"}
    results = [
        _screened("L1", "long", rank_score=1.0, aligned_count=3),
        _screened("S1", "short", rank_score=-2.0, aligned_count=2),
        _screened("L2", "long", rank_score=0.5, aligned_count=2),
        _screened("S2", "short", rank_score=-1.0, aligned_count=2),
    ]
    _sort_screened_results(results, config)

    # Longs ranked by (aligned, score) desc: L1(3) before L2(2)
    long_symbols = [s.symbol for s in results if s.direction == "long"]
    short_symbols = [s.symbol for s in results if s.direction == "short"]
    assert long_symbols == ["L1", "L2"]
    # Shorts ranked by (aligned, -score) desc: S1(-2.0, stronger) before S2
    assert short_symbols == ["S1", "S2"]


@pytest.mark.unit
def test_both_direction_tiebreak_short_wins_when_more_aligned():
    """Mirror case: short has more aligned TFs, so short must win even though
    the rank-TF score is positive (long-leaning)."""
    config = _rrs_config()
    # short-aligned: 30m (-0.5), 60m (-0.4) = 2 TFs; long-aligned: 5m (3.0) = 1 TF
    rrs_by_tf = {"5m": 3.0, "30m": -0.5, "60m": -0.4}
    ctx = _make_ctx("TEST", config, rrs_by_tf)

    with patch(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_rrs_multi_timeframe",
        return_value=rrs_by_tf,
    ), patch(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_relative_volume",
        return_value=1.5,
    ):
        result = RrsFilter().evaluate(ctx)

    assert result.passed is True
    assert result.direction == "short"
    assert result.metadata["aligned_count"] == 2


@pytest.mark.unit
def test_both_direction_nan_rank_tf_falls_back_to_aligned_side():
    """When the rank TF RRS is NaN (insufficient bars), the tie-break should not
    crash or misorder — alignment count still decides."""
    config = _rrs_config()
    # 30m and 60m aligned long; 5m (rank TF) is NaN
    rrs_by_tf = {"5m": float("nan"), "30m": 0.5, "60m": 0.4}
    ctx = _make_ctx("TEST", config, rrs_by_tf)

    with patch(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_rrs_multi_timeframe",
        return_value=rrs_by_tf,
    ), patch(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_relative_volume",
        return_value=1.5,
    ):
        result = RrsFilter().evaluate(ctx)

    assert result.passed is True
    assert result.direction == "long"
    assert result.metadata["aligned_count"] == 2
    assert math.isnan(result.score)


@pytest.mark.unit
def test_sort_both_magnitude_keeps_sides_separate():
    """With rank_by=magnitude and direction=both, each side must be ranked
    within itself (longs desc by score, shorts desc by -score) — never one
    mixed abs(score) list."""
    from tradingagents.intraday.universe_screener import _sort_screened_results

    config = {"intraday_screener_direction": "both", "intraday_screener_rank_by": "magnitude"}
    results = [
        _screened("L1", "long", rank_score=1.0, aligned_count=2),
        _screened("S1", "short", rank_score=-3.0, aligned_count=2),
        _screened("L2", "long", rank_score=2.0, aligned_count=3),
        _screened("S2", "short", rank_score=-1.0, aligned_count=1),
    ]
    _sort_screened_results(results, config)

    long_symbols = [s.symbol for s in results if s.direction == "long"]
    short_symbols = [s.symbol for s in results if s.direction == "short"]
    # Longs desc by score: L2(2.0) before L1(1.0)
    assert long_symbols == ["L2", "L1"]
    # Shorts desc by -score: S1(-3.0, most negative = strongest short) first
    assert short_symbols == ["S1", "S2"]


@pytest.mark.unit
def test_sort_both_aligned_mode_ranks_within_sides():
    """rank_by=aligned + direction=both: longs ranked by (aligned, score) desc,
    shorts by (aligned, -score) desc, each within their own side."""
    from tradingagents.intraday.universe_screener import _sort_screened_results

    config = {"intraday_screener_direction": "both", "intraday_screener_rank_by": "aligned"}
    results = [
        _screened("L1", "long", rank_score=1.0, aligned_count=3),
        _screened("S1", "short", rank_score=-2.0, aligned_count=2),
        _screened("L2", "long", rank_score=0.5, aligned_count=3),
        _screened("S2", "short", rank_score=-1.0, aligned_count=2),
    ]
    _sort_screened_results(results, config)

    long_symbols = [s.symbol for s in results if s.direction == "long"]
    short_symbols = [s.symbol for s in results if s.direction == "short"]
    assert long_symbols == ["L1", "L2"]
    # Same aligned tier (2): stronger short (-2.0) first
    assert short_symbols == ["S1", "S2"]


@pytest.mark.unit
def test_daily_requested_but_unavailable_records_metadata():
    """When include_daily_rrs is on but no daily data made it into the frames
    (benchmark daily failed in prepare), the pass result must carry
    daily_rrs_available=False instead of silently evaluating intraday-only."""
    config = {
        "intraday_screener_rrs_timeframes": [5, 30],
        "intraday_screener_min_rrs_aligned": 1,
        "intraday_screener_direction": "long",
        "intraday_screener_rank_rrs_timeframe": "5m",
        "intraday_screener_include_daily_rrs": True,
        "pro_trader_benchmark": "SPY",
    }
    rrs_by_tf = {"5m": 0.5, "30m": 0.4}
    ctx = _make_ctx("TEST", config, rrs_by_tf)

    with patch(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_rrs_multi_timeframe",
        return_value={"5m": 0.5, "30m": 0.4},
    ), patch(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_relative_volume",
        return_value=1.5,
    ):
        result = RrsFilter().evaluate(ctx)

    assert result.passed is True
    assert result.metadata.get("daily_rrs_available") is False
