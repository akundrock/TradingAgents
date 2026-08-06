from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

import pandas as pd

from tradingagents.dataflows.schwab_streamer import ScreenerCandidate
from tradingagents.intraday.session import TradingSession

ScreenerDirection = Literal["long", "short", "both"]
FilterMode = Literal["any", "all"]


@dataclass
class ScreenedSymbol:
    symbol: str
    direction: str = "long"
    rank_score: float = 0.0
    screener_volume: int = 0
    passed_filter: str = ""
    filter_metadata: dict[str, Any] = field(default_factory=dict)
    # RRS-specific (populated when RRS filter passes)
    rrs_by_tf: dict[str, float] = field(default_factory=dict)
    aligned_count: int = 0
    relative_volume_5m: float = 0.0
    rank_rrs_timeframe: str = "5m"


@dataclass
class ScanContext:
    symbol: str
    candidate: ScreenerCandidate
    df_5m: pd.DataFrame
    bar_time: datetime
    session_start: datetime
    config: dict
    bench_enriched: dict[int, pd.DataFrame]
    session: TradingSession | None
    shared: dict[str, Any]


@dataclass
class FilterResult:
    passed: bool
    direction: Literal["long", "short", "none"]
    score: float
    factors_met: list[str]
    factors_missing: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)
    reject_reason: str | None = None


class ScanFilter(Protocol):
    name: str

    def prepare(
        self,
        bar_time: datetime,
        config: dict,
        *,
        session_start: datetime,
        session: TradingSession | None = None,
    ) -> Any: ...

    def evaluate(self, ctx: ScanContext) -> FilterResult: ...


def combine_filter_results(
    results: list[FilterResult],
    *,
    mode: FilterMode,
    filter_names: list[str],
) -> FilterResult | None:
    """Merge per-filter results for one candidate."""
    if not results:
        return None

    passing = [r for r in results if r.passed]
    if mode == "all":
        if len(passing) != len(filter_names):
            missing_filters = [
                name
                for name, result in zip(filter_names, results)
                if not result.passed
            ]
            reasons = [r.reject_reason for r in results if r.reject_reason]
            return FilterResult(
                passed=False,
                direction="none",
                score=0.0,
                factors_met=[],
                factors_missing=missing_filters,
                metadata={},
                reject_reason="; ".join(r for r in reasons if r),
            )
        best = max(passing, key=lambda r: abs(r.score))
        merged_meta: dict[str, Any] = {}
        merged_met: list[str] = []
        merged_missing: list[str] = []
        for r in passing:
            merged_meta.update(r.metadata)
            merged_met.extend(r.factors_met)
            merged_missing.extend(r.factors_missing)
        return FilterResult(
            passed=True,
            direction=best.direction,
            score=best.score,
            factors_met=merged_met,
            factors_missing=merged_missing,
            metadata=merged_meta,
        )

    if not passing:
        reasons = [r.reject_reason for r in results if r.reject_reason]
        return FilterResult(
            passed=False,
            direction="none",
            score=0.0,
            factors_met=[],
            factors_missing=[],
            metadata={},
            reject_reason="; ".join(r for r in reasons if r) or "no_filter_passed",
        )

    best = max(passing, key=lambda r: abs(r.score))
    merged_meta = {}
    merged_met = []
    for r in passing:
        merged_meta.update(r.metadata)
        merged_met.extend(r.factors_met)
    return FilterResult(
        passed=True,
        direction=best.direction,
        score=best.score,
        factors_met=merged_met,
        factors_missing=[],
        metadata={**merged_meta, "passed_filters": [r.metadata.get("filter") for r in passing]},
    )


def filter_result_to_screened(
    symbol: str,
    candidate: ScreenerCandidate,
    merged: FilterResult,
    *,
    passed_filter: str,
) -> ScreenedSymbol:
    meta = merged.metadata
    screened = ScreenedSymbol(
        symbol=symbol,
        direction=merged.direction if merged.direction != "none" else "long",
        rank_score=merged.score,
        screener_volume=candidate.total_volume or candidate.volume,
        passed_filter=passed_filter,
        filter_metadata=meta,
    )
    if "rrs_by_tf" in meta:
        screened.rrs_by_tf = dict(meta["rrs_by_tf"])
        screened.aligned_count = int(meta.get("aligned_count", 0))
        screened.relative_volume_5m = float(meta.get("relative_volume_5m", 0.0))
        screened.rank_rrs_timeframe = str(meta.get("rank_rrs_timeframe", "5m"))
    return screened
