from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Literal

from tradingagents.dataflows.schwab import get_intraday_5m_candles
from tradingagents.dataflows.schwab_streamer import SchwabEquityScreener, ScreenerCandidate
from tradingagents.intraday.indicators.sector_mapping import is_sp500_constituent
from tradingagents.intraday.screener_filters import (
    ScreenedSymbol,
    ScanContext,
    combine_filter_results,
    filter_result_to_screened,
    resolve_filter_mode,
    resolve_filter_names,
    resolve_filter_pipeline,
    uses_rrs_filter,
)
from tradingagents.intraday.screener_filters.rrs_filter import resolve_rank_rrs_timeframe

__all__ = [
    "RANK_RRS_LABELS",
    "RANK_RRS_MINUTES",
    "ScreenedSymbol",
    "UniverseScreener",
    "resolve_rank_rrs_timeframe",
    "resolve_screener_source",
]
from tradingagents.intraday.session import TradingSession

logger = logging.getLogger(__name__)

ScreenerDirection = Literal["long", "short", "both"]
ScreenerSource = Literal["auto", "streamer", "sp500_quotes", "sp500_rrs"]
RANK_RRS_LABELS = ("5m", "30m", "60m")
RANK_RRS_MINUTES = {"5m": 5, "30m": 30, "60m": 60}


def resolve_screener_source(config: dict) -> str:
    """Resolve screener universe source from config (auto uses SP500 quotes when required)."""
    raw = str(config.get("intraday_screener_source", "auto")).strip().lower()
    if raw == "auto":
        if bool(config.get("intraday_screener_require_sp500", True)):
            return "sp500_quotes"
        return "streamer"
    if raw in ("streamer", "sp500_quotes", "sp500_rrs"):
        return raw
    logger.warning("Unknown intraday_screener_source=%s; using streamer", raw)
    return "streamer"


class UniverseScreener:
    """Volume universe discovery + pluggable screener filter pipeline."""

    def __init__(self, config: dict, screener: SchwabEquityScreener | None = None):
        self.config = config
        self.screener = screener or SchwabEquityScreener()

    def refresh_watchlist(
        self,
        bar_time: datetime,
        base_watchlist: list[str],
        session: TradingSession | None = None,
    ) -> tuple[list[str], list[ScreenedSymbol]]:
        keys = list(self.config.get("intraday_screener_keys") or ["NASDAQ_VOLUME_0", "NYSE_VOLUME_0"])
        candidate_limit = int(self.config.get("intraday_screener_candidate_limit", 50))
        max_watchlist = int(self.config.get("intraday_screener_max_watchlist", 12))
        source = resolve_screener_source(self.config)
        filter_names = resolve_filter_names(self.config)
        rrs_first = source == "sp500_rrs"

        if source == "sp500_rrs" and not uses_rrs_filter(self.config):
            logger.warning(
                "intraday_screener_source=sp500_rrs but RRS filter not active; "
                "using sp500_quotes instead"
            )
            source = "sp500_quotes"
            rrs_first = False

        logger.info(
            "Universe screener: fetching candidates source=%s limit=%d filters=%s",
            source,
            candidate_limit,
            filter_names,
        )
        if source == "sp500_rrs":
            from tradingagents.dataflows.schwab_quotes import fetch_sp500_quote_candidates

            candidates = fetch_sp500_quote_candidates()
        elif source == "sp500_quotes":
            from tradingagents.dataflows.schwab_quotes import fetch_sp500_volume_candidates

            candidates = fetch_sp500_volume_candidates(candidate_limit)
        else:
            logger.info(
                "Universe screener: streamer keys=%s",
                keys,
            )
            candidates = self.screener.fetch_top_symbols(keys, limit=candidate_limit)
        if not candidates:
            logger.warning(
                "Universe screener: returned 0 candidates (source=%s); "
                "check Schwab auth or market hours",
                source,
            )
            merged = self._dedupe_symbols(base_watchlist)
            return merged[:max_watchlist], []

        if rrs_first:
            logger.info(
                "Universe screener: %d SP500 quote candidates (source=%s, RRS-first)",
                len(candidates),
                source,
            )
        else:
            top_volume = ", ".join(
                self._format_candidate_volume(c) for c in candidates[:8]
            )
            logger.info(
                "Universe screener: returned %d volume candidates (source=%s); top: %s",
                len(candidates),
                source,
                top_volume,
            )

        initial_count = len(candidates)
        skip_sp500_filter = source in ("sp500_quotes", "sp500_rrs")
        candidates, volume_filter_summary = self._filter_volume_candidates(
            candidates,
            skip_sp500_filter=skip_sp500_filter,
        )
        if volume_filter_summary:
            logger.info(
                "Universe screener: price pre-filter %d/%d remain — %s",
                len(candidates),
                initial_count,
                volume_filter_summary,
            )
        if not candidates:
            logger.info(
                "Universe screener: no candidates after price pre-filter (had %d)",
                initial_count,
            )
            merged = self._dedupe_symbols(base_watchlist)
            return merged[:max_watchlist], []

        screened, reject_summary = self._run_filter_pipeline(
            candidates, bar_time, session=session
        )
        if rrs_first and screened:
            screened = screened[:candidate_limit]
            logger.info(
                "Universe screener: filter-ranked top %d of %d scored candidates",
                len(screened),
                len(candidates),
            )

        if not screened and candidates:
            logger.info(
                "Universe screener: filters passed 0/%d candidates — %s",
                len(candidates),
                reject_summary,
            )
        elif screened:
            logger.info(
                "Universe screener: filters passed %d/%d — %s",
                len(screened),
                len(candidates),
                reject_summary,
            )

        merged = self._merge_watchlist(
            screened,
            base_watchlist,
            max_watchlist,
            screener_first=rrs_first,
        )
        logger.info(
            "Universe screener: merged watchlist %d symbols "
            "(base=%d screened=%d cap=%d screener_first=%s)",
            len(merged),
            len(base_watchlist),
            len(screened),
            max_watchlist,
            rrs_first,
        )
        return merged, screened

    def _filter_volume_candidates(
        self,
        candidates: list[ScreenerCandidate],
        *,
        skip_sp500_filter: bool = False,
    ) -> tuple[list[ScreenerCandidate], str]:
        min_price = float(self.config.get("intraday_screener_min_price", 10.0))
        require_sp500 = bool(self.config.get("intraday_screener_require_sp500", True))
        if skip_sp500_filter:
            require_sp500 = False

        if min_price <= 0 and not require_sp500:
            return candidates, ""

        filtered: list[ScreenerCandidate] = []
        reject_price = 0
        reject_sp500 = 0
        reject_samples: list[str] = []

        for candidate in candidates:
            symbol = candidate.symbol
            if require_sp500 and not is_sp500_constituent(symbol):
                reject_sp500 += 1
                if len(reject_samples) < 8:
                    reject_samples.append(f"{symbol} not_sp500")
                continue
            if min_price > 0 and candidate.last_price > 0 and candidate.last_price < min_price:
                reject_price += 1
                if len(reject_samples) < 8:
                    reject_samples.append(
                        f"{symbol} price={candidate.last_price:.2f}<{min_price:.2f}"
                    )
                continue
            filtered.append(candidate)

        if reject_price == 0 and reject_sp500 == 0:
            return filtered, ""

        summary = f"rejected price<{min_price:.2f}={reject_price} not_sp500={reject_sp500}"
        if reject_samples:
            summary += f"; samples: {', '.join(reject_samples)}"
        return filtered, summary

    def _screener_max_workers(self) -> int:
        screener_workers = self.config.get("intraday_screener_max_concurrent_symbols")
        if screener_workers is not None:
            return max(1, int(screener_workers))
        return max(1, int(self.config.get("intraday_max_concurrent_symbols", 5)))

    def _run_filter_pipeline(
        self,
        candidates: list[ScreenerCandidate],
        bar_time: datetime,
        session: TradingSession | None = None,
    ) -> tuple[list[ScreenedSymbol], str]:
        filters = resolve_filter_pipeline(self.config)
        filter_names = [f.name for f in filters]
        filter_mode = resolve_filter_mode(self.config)
        max_workers = self._screener_max_workers()
        session_start = self._session_start(bar_time)
        direction = str(self.config.get("intraday_screener_direction", "long"))

        shared: dict[str, object] = {}
        bench_enriched: dict = {}
        for filt in filters:
            prepared = filt.prepare(
                bar_time,
                self.config,
                session_start=session_start,
                session=session,
            )
            shared[filt.name] = prepared
            if filt.name == "rrs" and isinstance(prepared, dict):
                bench_enriched = prepared

        logger.info(
            "Universe screener filter pipeline: evaluating %d candidates "
            "(filters=%s mode=%s direction=%s workers=%d)",
            len(candidates),
            filter_names,
            filter_mode,
            direction,
            max_workers,
        )

        results: list[ScreenedSymbol] = []
        reject_counts: dict[str, int] = {}
        reject_samples: list[str] = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self._evaluate_candidate,
                    candidate,
                    bar_time,
                    session_start,
                    filters,
                    filter_names,
                    filter_mode,
                    shared,
                    bench_enriched,
                    session,
                ): candidate.symbol
                for candidate in candidates
            }
            for future in as_completed(futures):
                screened, reject_reason = future.result()
                if screened is not None:
                    results.append(screened)
                elif reject_reason:
                    bucket = reject_reason.split(":", maxsplit=1)[0]
                    reject_counts[bucket] = reject_counts.get(bucket, 0) + 1
                    if len(reject_samples) < 8:
                        reject_samples.append(reject_reason)

        if direction == "short":
            results.sort(key=lambda s: (s.rank_score, s.aligned_count))
        elif direction == "both":
            results.sort(key=lambda s: abs(s.rank_score), reverse=True)
        else:
            results.sort(key=lambda s: (s.rank_score, s.aligned_count), reverse=True)

        summary_parts = [f"{k}={v}" for k, v in sorted(reject_counts.items())]
        summary = f"rejected {' '.join(summary_parts)}" if summary_parts else "no rejections"
        if reject_samples:
            summary += f"; samples: {', '.join(reject_samples)}"
        return results, summary

    def _evaluate_candidate(
        self,
        candidate: ScreenerCandidate,
        bar_time: datetime,
        session_start: datetime,
        filters: list,
        filter_names: list[str],
        filter_mode: str,
        shared: dict[str, object],
        bench_enriched: dict,
        session: TradingSession | None = None,
    ) -> tuple[ScreenedSymbol | None, str | None]:
        symbol = candidate.symbol
        try:
            df_5m = get_intraday_5m_candles(symbol, session_start, bar_time)
            if session is not None:
                session.intraday_5m_cache[symbol] = df_5m

            ctx = ScanContext(
                symbol=symbol,
                candidate=candidate,
                df_5m=df_5m,
                bar_time=bar_time,
                session_start=session_start,
                config=self.config,
                bench_enriched=bench_enriched,
                session=session,
                shared=shared,
            )

            filter_results = [filt.evaluate(ctx) for filt in filters]
            merged = combine_filter_results(
                filter_results,
                mode=filter_mode,  # type: ignore[arg-type]
                filter_names=filter_names,
            )
            if merged is None or not merged.passed:
                reason = merged.reject_reason if merged else f"error:{symbol} no_result"
                return None, reason

            passed_filter = str(merged.metadata.get("filter", filter_names[0]))
            if "passed_filters" in merged.metadata:
                passed_names = merged.metadata["passed_filters"]
                if passed_names:
                    passed_filter = ",".join(str(n) for n in passed_names if n)

            screened = filter_result_to_screened(
                symbol,
                candidate,
                merged,
                passed_filter=passed_filter,
            )
            return screened, None
        except Exception as exc:
            logger.warning("Filter pipeline error for %s: %s", symbol, exc)
            return None, f"error:{symbol} {exc}"

    def _merge_watchlist(
        self,
        screened: list[ScreenedSymbol],
        base_watchlist: list[str],
        max_watchlist: int,
        *,
        screener_first: bool,
    ) -> list[str]:
        screened_symbols = [s.symbol for s in screened]
        if screener_first:
            merged = self._dedupe_symbols(screened_symbols + base_watchlist)
        else:
            merged = self._dedupe_symbols(base_watchlist + screened_symbols)
        return merged[:max_watchlist]

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

    @staticmethod
    def _format_candidate_volume(candidate: ScreenerCandidate) -> str:
        vol = candidate.volume or 0
        total = candidate.total_volume or 0
        if vol and total and vol != total:
            return f"{candidate.symbol}(vol={vol},total={total})"
        return f"{candidate.symbol}({total or vol})"
