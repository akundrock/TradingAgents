from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from tradingagents.dataflows.schwab_streamer import ScreenerCandidate
from tradingagents.intraday.screener_filters import (
    combine_filter_results,
    get_filter,
    resolve_filter_names,
    resolve_filter_pipeline,
)
from tradingagents.intraday.screener_filters.base import FilterResult, ScanContext
from tradingagents.intraday.screener_filters.orb_filter import OrbFilter


def _make_orb_bars() -> pd.DataFrame:
    start = datetime(2026, 7, 27, 9, 30)
    rows = []
    for i in range(6):
        rows.append(
            {
                "Date": start + pd.Timedelta(minutes=5 * i),
                "Open": 100.0,
                "High": 102.0,
                "Low": 100.0,
                "Close": 101.0,
                "Volume": 1000,
            }
        )
    rows.append(
        {
            "Date": start + pd.Timedelta(minutes=30),
            "Open": 101.5,
            "High": 103.0,
            "Low": 101.0,
            "Close": 102.5,
            "Volume": 2000,
        }
    )
    return pd.DataFrame(rows)


@pytest.mark.unit
def test_resolve_filter_names_defaults_to_rrs():
    assert resolve_filter_names({}) == ["rrs"]
    assert resolve_filter_names({"intraday_screener_filters": ["orb"]}) == ["orb"]
    assert resolve_filter_names({"intraday_screener_filters": "orb,rrs"}) == ["orb", "rrs"]


@pytest.mark.unit
def test_resolve_filter_pipeline():
    pipeline = resolve_filter_pipeline({"intraday_screener_filters": ["orb", "rrs"]})
    assert [f.name for f in pipeline] == ["orb", "rrs"]


@pytest.mark.unit
def test_combine_filter_results_any_mode():
    results = [
        FilterResult(passed=False, direction="none", score=0.0, factors_met=[], factors_missing=["orb"]),
        FilterResult(
            passed=True,
            direction="long",
            score=1.5,
            factors_met=["rs_aligned_3"],
            factors_missing=[],
            metadata={"filter": "rrs"},
        ),
    ]
    merged = combine_filter_results(results, mode="any", filter_names=["orb", "rrs"])
    assert merged is not None
    assert merged.passed is True
    assert merged.score == 1.5


@pytest.mark.unit
def test_combine_filter_results_all_mode_requires_every_filter():
    results = [
        FilterResult(passed=True, direction="long", score=0.5, factors_met=["orb"], factors_missing=[], metadata={"filter": "orb"}),
        FilterResult(passed=False, direction="none", score=0.0, factors_met=[], factors_missing=["rrs"], reject_reason="rrs fail"),
    ]
    merged = combine_filter_results(results, mode="all", filter_names=["orb", "rrs"])
    assert merged is not None
    assert merged.passed is False


@pytest.mark.unit
def test_orb_filter_detects_bullish_breakout():
    df = _make_orb_bars()
    bar_time = datetime(2026, 7, 27, 10, 30)
    session_start = datetime(2026, 7, 27, 9, 30)
    candidate = ScreenerCandidate(symbol="TEST", last_price=102.5, total_volume=1000)
    ctx = ScanContext(
        symbol="TEST",
        candidate=candidate,
        df_5m=df,
        bar_time=bar_time,
        session_start=session_start,
        config={
            "screener_orb_direction": "long",
            "screener_orb_require_price_beyond": True,
            "intraday_timezone": "America/New_York",
            "pro_trader_entry_mode": "wick_touch",
        },
        bench_enriched={},
        session=None,
        shared={},
    )
    filt = OrbFilter()
    result = filt.evaluate(ctx)
    assert result.passed is True
    assert result.direction == "long"
    assert result.score > 0
    assert result.metadata["orh"] == 102.0


@pytest.mark.unit
def test_orb_filter_rejects_before_entry_window():
    df = _make_orb_bars()
    bar_time = datetime(2026, 7, 27, 9, 45)
    session_start = datetime(2026, 7, 27, 9, 30)
    candidate = ScreenerCandidate(symbol="TEST", last_price=101.0, total_volume=1000)
    ctx = ScanContext(
        symbol="TEST",
        candidate=candidate,
        df_5m=df.iloc[:3],
        bar_time=bar_time,
        session_start=session_start,
        config={"screener_orb_direction": "long", "intraday_timezone": "America/New_York"},
        bench_enriched={},
        session=None,
        shared={},
    )
    result = OrbFilter().evaluate(ctx)
    assert result.passed is False


@pytest.mark.unit
def test_get_filter_unknown_raises():
    with pytest.raises(ValueError, match="Unknown screener filter"):
        get_filter("unknown")
