from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass
class DailyBiasReport:
    symbol: str
    trade_date: str
    direction: Literal["bullish", "bearish", "neutral"]
    key_levels: dict[str, float]
    summary: str
    computed_at: datetime


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
