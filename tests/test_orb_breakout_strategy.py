from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tradingagents.dataflows.schwab_streamer import ScreenerCandidate
from tradingagents.intraday.strategies.orb_breakout import OrbBreakoutStrategy
from tradingagents.intraday.strategies import get_strategy, STRATEGY_REGISTRY
from tradingagents.intraday.mtf_validator import MTFValidationResult
from tradingagents.intraday.session import DailyBiasReport


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
def test_orb_breakout_strategy_registered():
    assert "orb_breakout" in STRATEGY_REGISTRY
    strategy = get_strategy("orb_breakout")
    assert strategy.name == "orb_breakout"


@pytest.mark.unit
def test_orb_breakout_strategy_passes_long_setup():
    df = _make_orb_bars()
    bar_time = datetime(2026, 7, 27, 10, 30)
    mtf = MTFValidationResult(
        symbol="TEST",
        bar_time=bar_time,
        snapshot_5min={"Close": 102.5, "High": 103.0, "Low": 101.0, "Volume": 2000},
        snapshot_30min={"Close": 102.5},
        snapshot_daily={"Close": 100.0},
        trend_5min="up",
        trend_30min="up",
        daily_bias_direction="neutral",
        trends_aligned=True,
        vwap_5min=101.5,
        atr_5min=1.0,
        df_5min=df,
    )
    bias = DailyBiasReport(
        symbol="TEST",
        trade_date="2026-07-27",
        direction="neutral",
        key_levels={},
        summary="neutral",
        computed_at=datetime(2026, 7, 27, 9, 0),
    )
    result = OrbBreakoutStrategy().check_setup("TEST", mtf, bias, config={"pro_trader_entry_mode": "wick_touch"})
    assert result.passed is True
    assert result.direction == "long"
    assert "orb_breakout" in result.factors_met
