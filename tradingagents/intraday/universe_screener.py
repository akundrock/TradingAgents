from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import pandas as pd

from tradingagents.dataflows.schwab import get_candles_multi_timeframe
from tradingagents.dataflows.schwab_streamer import SchwabEquityScreener, ScreenerCandidate
from tradingagents.dataflows.stockstats_utils import compute_mtf_indicators
from tradingagents.intraday.indicators.relative_strength import (
    compute_rrs_multi_timeframe,
    count_aligned_rrs,
)
from tradingagents.intraday.indicators.relative_volume import compute_relative_volume
from tradingagents.intraday.indicators.resample import resample_ohlcv
from tradingagents.intraday.mtf_validator import _synthesize_60m

logger = logging.getLogger(__name__)

ScreenerDirection = Literal["long", "short", "both"]


@dataclass
class ScreenedSymbol:
    symbol: str
    rrs_by_tf: dict[str, float] = field(default_factory=dict)
    aligned_count: int = 0
    relative_volume_5m: float = 0.0
    direction: str = "long"
    rank_score: float = 0.0
    screener_volume: int = 0


class UniverseScreener:
    """Volume universe discovery + ThinkScript RS scanner parity filter (5m/30m/60m RRS)."""

    def __init__(self, config: dict, screener: SchwabEquityScreener | None = None):
        self.config = config
        self.screener = screener or SchwabEquityScreener()

    def refresh_watchlist(
        self,
        bar_time: datetime,
        base_watchlist: list[str],
    ) -> tuple[list[str], list[ScreenedSymbol]]:
        keys = list(self.config.get("intraday_screener_keys") or ["NASDAQ_VOLUME_0", "NYSE_VOLUME_0"])
        candidate_limit = int(self.config.get("intraday_screener_candidate_limit", 50))
        max_watchlist = int(self.config.get("intraday_screener_max_watchlist", 12))
        direction = str(self.config.get("intraday_screener_direction", "long"))

        candidates = self.screener.fetch_top_symbols(keys, limit=candidate_limit)
        if not candidates:
            logger.warning("Universe screener returned no volume candidates")
            merged = self._dedupe_symbols(base_watchlist)
            return merged[:max_watchlist], []

        screened = self._rrs_filter(candidates, bar_time, direction)
        screened_symbols = [s.symbol for s in screened]
        merged = self._dedupe_symbols(base_watchlist + screened_symbols)
        return merged[:max_watchlist], screened

    def _rrs_filter(
        self,
        candidates: list[ScreenerCandidate],
        bar_time: datetime,
        direction: str,
    ) -> list[ScreenedSymbol]:
        timeframes = list(self.config.get("intraday_screener_rrs_timeframes") or [5, 30, 60])
        min_aligned = int(self.config.get("intraday_screener_min_rrs_aligned", 3))
        require_rvol = bool(self.config.get("intraday_screener_require_relative_volume", False))
        benchmark = str(self.config.get("pro_trader_benchmark", "SPY"))
        max_workers = int(self.config.get("intraday_max_concurrent_symbols", 5))
        session_start = self._session_start(bar_time)

        directions: list[ScreenerDirection]
        if direction == "both":
            directions = ["long", "short"]
        elif direction == "short":
            directions = ["short"]
        else:
            directions = ["long"]

        results: list[ScreenedSymbol] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self._evaluate_candidate,
                    candidate,
                    bar_time,
                    session_start,
                    timeframes,
                    benchmark,
                    directions,
                    min_aligned,
                    require_rvol,
                ): candidate.symbol
                for candidate in candidates
            }
            for future in as_completed(futures):
                screened = future.result()
                if screened is not None:
                    results.append(screened)

        if "long" in directions and "short" not in directions:
            results.sort(key=lambda s: (s.rank_score, s.aligned_count), reverse=True)
        elif "short" in directions and "long" not in directions:
            results.sort(key=lambda s: (s.rank_score, s.aligned_count))
        else:
            results.sort(key=lambda s: abs(s.rank_score), reverse=True)
        return results

    def _evaluate_candidate(
        self,
        candidate: ScreenerCandidate,
        bar_time: datetime,
        session_start: datetime,
        timeframes: list[int],
        benchmark: str,
        directions: list[ScreenerDirection],
        min_aligned: int,
        require_rvol: bool,
    ) -> ScreenedSymbol | None:
        symbol = candidate.symbol
        try:
            fetch_tfs = sorted({tf for tf in timeframes if tf != 60})
            if not fetch_tfs:
                fetch_tfs = [5, 30]

            sym_dfs = get_candles_multi_timeframe(symbol, session_start, bar_time, timeframes=fetch_tfs)
            bench_dfs = get_candles_multi_timeframe(
                benchmark, session_start, bar_time, timeframes=fetch_tfs
            )
            sym_enriched = compute_mtf_indicators(sym_dfs)
            bench_enriched = compute_mtf_indicators(bench_dfs)

            if 60 in timeframes:
                sym_enriched = _synthesize_60m(sym_enriched)
                bench_enriched = _synthesize_60m(bench_enriched)

            sym_frames: dict[int | str, pd.DataFrame] = {}
            bench_frames: dict[int | str, pd.DataFrame] = {}
            for tf in timeframes:
                if tf in sym_enriched and not sym_enriched[tf].empty:
                    sym_frames[tf] = sym_enriched[tf]
                if tf in bench_enriched and not bench_enriched[tf].empty:
                    bench_frames[tf] = bench_enriched[tf]

            rrs_by_tf = compute_rrs_multi_timeframe(sym_frames, bench_frames)
            if not rrs_by_tf:
                return None

            relative_volume_5m = 0.0
            if 5 in sym_enriched and not sym_enriched[5].empty:
                relative_volume_5m = compute_relative_volume(sym_enriched[5], "5m")
            if require_rvol and relative_volume_5m <= 1.0:
                return None

            for dir_choice in directions:
                aligned = count_aligned_rrs(rrs_by_tf, dir_choice)
                if aligned < min_aligned:
                    continue
                rank_score = float(rrs_by_tf.get("5m", 0.0))
                return ScreenedSymbol(
                    symbol=symbol,
                    rrs_by_tf=rrs_by_tf,
                    aligned_count=aligned,
                    relative_volume_5m=relative_volume_5m,
                    direction=dir_choice,
                    rank_score=rank_score,
                    screener_volume=candidate.total_volume or candidate.volume,
                )
            return None
        except Exception as exc:
            logger.debug("RRS filter failed for %s: %s", symbol, exc)
            return None

    def _session_start(self, bar_time: datetime) -> datetime:
        start_str = str(self.config.get("intraday_session_start", "09:30"))
        hour, minute = [int(x) for x in start_str.split(":", maxsplit=1)]
        return bar_time.replace(hour=hour, minute=minute, second=0, microsecond=0)

    @staticmethod
    def _dedupe_symbols(symbols: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for symbol in symbols:
            sym = symbol.strip().upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            ordered.append(sym)
        return ordered
