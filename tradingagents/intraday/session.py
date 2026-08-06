from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import pandas as pd

import pandas as pd


@dataclass
class DailyBiasReport:
    symbol: str
    trade_date: str
    direction: Literal["bullish", "bearish", "neutral"]
    key_levels: dict[str, float]
    summary: str
    computed_at: datetime
    analyst_reports: dict[str, str] = field(default_factory=dict)
    debate_summary: str = ""


@dataclass
class SymbolScanState:
    symbol: str
    bar_time: datetime
    daily_bias_direction: str
    strategy_name: str
    strategy_direction: str
    factors_met: list[str]
    factors_missing: list[str]
    gate1_passed: bool
    gate2_passed: bool
    gate_passed: bool
    final_direction: str | None
    setup_score: int
    indicator_snapshot: dict[str, float | str] | None = None


@dataclass
class IntradaySignal:
    symbol: str
    bar_time: datetime
    action: Literal["BUY", "SELL", "HOLD"]
    direction: Literal["long", "short", "none"]
    entry_price: float | None
    stop_loss: float | None
    confidence: str
    setup_score: int
    gate_summary: str
    reasoning: str


@dataclass
class TradingSession:
    session_date: str
    watchlist: list[str]
    status: Literal["pre_market", "active", "closed"] = "pre_market"
    daily_bias_cache: dict[str, DailyBiasReport] = field(default_factory=dict)
    signal_log: list[IntradaySignal] = field(default_factory=list)
    last_scan_time: datetime | None = None
    scan_count: int = 0
    last_signal_by_symbol: dict[str, IntradaySignal] = field(default_factory=dict)
    gate_stats: dict[str, int] = field(
        default_factory=lambda: {
            "gate1_fail": 0,
            "gate2_fail": 0,
            "all_pass": 0,
        }
    )
    latest_scan_by_symbol: dict[str, SymbolScanState] = field(default_factory=dict)
    selected_detail_symbol: str | None = None
    detail_follow_mode: bool = True
    base_watchlist: list[str] = field(default_factory=list)
    symbol_sources: dict[str, str] = field(default_factory=dict)
    removed_symbols: dict[str, datetime] = field(default_factory=dict)
    screener_last_refresh: datetime | None = None
    screener_last_candidate_count: int = 0
    screener_snapshots: dict[str, dict[str, float | str]] = field(default_factory=dict)
    intraday_scan_bar_time: datetime | None = None
    intraday_5m_cache: dict[str, pd.DataFrame] = field(default_factory=dict)
    benchmark_intraday_frames: dict[int, pd.DataFrame] = field(default_factory=dict)
    benchmark_daily_df: pd.DataFrame = field(default_factory=pd.DataFrame)
