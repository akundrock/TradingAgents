from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

import pandas as pd

from tradingagents.dataflows.schwab import get_intraday_5m_candles
from tradingagents.intraday.frame_enrichment import enriched_frames_from_5m
from tradingagents.intraday.indicators.relative_strength import (
    compute_rrs_multi_timeframe,
    count_aligned_rrs,
)
from tradingagents.intraday.indicators.relative_volume import compute_relative_volume
from tradingagents.intraday.screener_filters.base import FilterResult, ScanContext, ScreenerDirection

logger = logging.getLogger(__name__)

RANK_RRS_MINUTES = {"5m": 5, "30m": 30, "60m": 60}
ScreenerRankMode = Literal["pass_only", "rank_all"]


def resolve_rank_rrs_timeframe(config: dict) -> str:
    """Resolve screener sort key timeframe label (5m, 30m, or 60m)."""
    raw = str(config.get("intraday_screener_rank_rrs_timeframe", "5m")).strip().lower()
    if raw in RANK_RRS_MINUTES:
        return raw
    if raw.isdigit():
        minutes = int(raw)
        for label, tf_minutes in RANK_RRS_MINUTES.items():
            if tf_minutes == minutes:
                return label
    logger.warning(
        "Unknown intraday_screener_rank_rrs_timeframe=%s; using 5m",
        raw,
    )
    return "5m"


class RrsFilter:
    name = "rrs"

    def prepare(
        self,
        bar_time: datetime,
        config: dict,
        *,
        session_start: datetime,
        session: object | None = None,
    ) -> dict[int, pd.DataFrame]:
        timeframes = list(config.get("intraday_screener_rrs_timeframes") or [5, 30, 60])
        rank_rrs_tf = resolve_rank_rrs_timeframe(config)
        rank_tf_minutes = RANK_RRS_MINUTES[rank_rrs_tf]
        if rank_tf_minutes not in timeframes:
            timeframes = sorted(set(timeframes) | {rank_tf_minutes})
        benchmark = str(config.get("pro_trader_benchmark", "SPY"))
        df_5m = get_intraday_5m_candles(benchmark, session_start, bar_time)
        enriched = enriched_frames_from_5m(df_5m, timeframes)
        logger.info(
            "RrsFilter: cached benchmark %s 5m bars (%d rows)",
            benchmark,
            len(df_5m),
        )
        return enriched

    def evaluate(self, ctx: ScanContext) -> FilterResult:
        config = ctx.config
        timeframes = list(config.get("intraday_screener_rrs_timeframes") or [5, 30, 60])
        min_aligned = int(config.get("intraday_screener_min_rrs_aligned", 3))
        require_rvol = bool(config.get("intraday_screener_require_relative_volume", False))
        rank_mode = _resolve_rank_mode(config, min_aligned)
        rank_rrs_tf = resolve_rank_rrs_timeframe(config)
        rank_tf_minutes = RANK_RRS_MINUTES[rank_rrs_tf]
        if rank_tf_minutes not in timeframes:
            timeframes = sorted(set(timeframes) | {rank_tf_minutes})

        direction = str(config.get("intraday_screener_direction", "long"))
        directions = _resolve_directions(direction)
        bench_enriched = ctx.shared.get(self.name) or ctx.bench_enriched

        sym_enriched = enriched_frames_from_5m(ctx.df_5m, timeframes)
        sym_frames: dict[int | str, pd.DataFrame] = {}
        bench_frames: dict[int | str, pd.DataFrame] = {}
        for tf in timeframes:
            if tf in sym_enriched and not sym_enriched[tf].empty:
                sym_frames[tf] = sym_enriched[tf]
            if tf in bench_enriched and not bench_enriched[tf].empty:
                bench_frames[tf] = bench_enriched[tf]

        rrs_by_tf = compute_rrs_multi_timeframe(sym_frames, bench_frames)
        if not rrs_by_tf:
            return FilterResult(
                passed=False,
                direction="none",
                score=0.0,
                factors_met=[],
                factors_missing=["rrs_data"],
                metadata={"filter": self.name},
                reject_reason=f"no_data:{ctx.symbol} empty RRS",
            )

        relative_volume_5m = 0.0
        if 5 in sym_enriched and not sym_enriched[5].empty:
            relative_volume_5m = compute_relative_volume(sym_enriched[5], "5m")
        if require_rvol and relative_volume_5m <= 1.0:
            return FilterResult(
                passed=False,
                direction="none",
                score=0.0,
                factors_met=[],
                factors_missing=["relative_volume"],
                metadata={"filter": self.name, "relative_volume_5m": relative_volume_5m},
                reject_reason=f"rvol:{ctx.symbol} rvol5m={relative_volume_5m:.2f}<=1.0",
            )

        best_aligned = 0
        best: FilterResult | None = None
        for dir_choice in directions:
            aligned = count_aligned_rrs(rrs_by_tf, dir_choice)
            best_aligned = max(best_aligned, aligned)
            rank_score = float(rrs_by_tf.get(rank_rrs_tf, 0.0))
            if rank_mode == "pass_only" and aligned < min_aligned:
                continue
            result = FilterResult(
                passed=True,
                direction=dir_choice,
                score=rank_score,
                factors_met=[f"rs_aligned_{aligned}"],
                factors_missing=[],
                metadata={
                    "filter": self.name,
                    "rrs_by_tf": rrs_by_tf,
                    "aligned_count": aligned,
                    "relative_volume_5m": relative_volume_5m,
                    "rank_rrs_timeframe": rank_rrs_tf,
                },
            )
            if best is None:
                best = result
            elif "long" in directions and "short" not in directions:
                if result.score > best.score:
                    best = result
            elif "short" in directions and "long" not in directions:
                if result.score < best.score:
                    best = result
            elif abs(result.score) > abs(best.score):
                best = result

        if best is not None:
            logger.debug(
                "RRS pass %s: direction=%s aligned=%d rrs%s=%.2f rvol5m=%.2f",
                ctx.symbol,
                best.direction,
                best.metadata.get("aligned_count", 0),
                rank_rrs_tf.replace("m", ""),
                best.score,
                relative_volume_5m,
            )
            return best

        rank_rrs_val = float(rrs_by_tf.get(rank_rrs_tf, 0.0))
        return FilterResult(
            passed=False,
            direction="none",
            score=0.0,
            factors_met=[],
            factors_missing=[f"rs_aligned_{best_aligned}"],
            metadata={"filter": self.name, "rrs_by_tf": rrs_by_tf, "aligned_count": best_aligned},
            reject_reason=(
                f"rrs:{ctx.symbol} aligned={best_aligned}<{min_aligned} "
                f"rrs{rank_rrs_tf}={rank_rrs_val:.2f} rvol5m={relative_volume_5m:.2f}"
            ),
        )


def _resolve_rank_mode(config: dict, min_aligned: int) -> ScreenerRankMode:
    raw = str(config.get("intraday_screener_rank_mode", "pass_only")).strip().lower()
    if raw == "rank_all" or min_aligned <= 0:
        return "rank_all"
    return "pass_only"


def _resolve_directions(direction: str) -> list[ScreenerDirection]:
    if direction == "both":
        return ["long", "short"]
    if direction == "short":
        return ["short"]
    return ["long"]
