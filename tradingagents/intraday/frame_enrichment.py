from __future__ import annotations

from typing import Literal

import pandas as pd

from tradingagents.dataflows.schwab import SCHWAB_INTRADAY_MINUTES
from tradingagents.dataflows.stockstats_utils import compute_mtf_indicators, compute_tf_indicators
from tradingagents.intraday.indicators.resample import resample_ohlcv

MtfFetchMode = Literal["multi", "5m_resample"]


def resolve_mtf_fetch_mode(config: dict) -> MtfFetchMode:
    raw = str(config.get("intraday_mtf_fetch_mode", "5m_resample")).strip().lower()
    if raw == "multi":
        return "multi"
    if raw != "5m_resample":
        import logging

        logging.getLogger(__name__).warning(
            "Unknown intraday_mtf_fetch_mode=%s; using 5m_resample",
            raw,
        )
    return "5m_resample"


def effective_requested_timeframes(config: dict) -> tuple[list[int], bool]:
    """Return indicator timeframes to build and whether 60m synthesis is needed."""
    requested = list(config.get("intraday_mtf_timeframes", [5, 30]))
    strategy = str(config.get("intraday_strategy", "base_momentum"))
    if strategy == "pro_trader_dashboard":
        for tf in (5, 15, 30, 60):
            if tf not in requested:
                requested.append(tf)

    need_60m = 60 in requested
    fetch_tfs = sorted({tf for tf in requested if tf in SCHWAB_INTRADAY_MINUTES})
    if not fetch_tfs:
        fetch_tfs = [5, 30]
    return fetch_tfs, need_60m


def synthesize_60m(enriched: dict[int, pd.DataFrame]) -> dict[int, pd.DataFrame]:
    """Derive 60m indicators from 30m (preferred) or 5m bars."""
    if 60 in enriched and not enriched[60].empty:
        return enriched
    source_tf = 30 if 30 in enriched and not enriched[30].empty else 5
    if source_tf not in enriched or enriched[source_tf].empty:
        return enriched
    raw_60 = resample_ohlcv(enriched[source_tf], 60)
    if raw_60.empty:
        return enriched
    enriched = dict(enriched)
    enriched[60] = compute_tf_indicators(raw_60)
    return enriched


def enriched_frames_from_5m(
    df_5m: pd.DataFrame,
    timeframes: list[int],
) -> dict[int, pd.DataFrame]:
    """Build enriched intraday frames from a single 5m OHLCV series."""
    sym_dfs: dict[int, pd.DataFrame] = {5: df_5m}
    for tf in timeframes:
        if tf == 5:
            continue
        if tf in (15, 30):
            raw = resample_ohlcv(df_5m, tf)
            if not raw.empty:
                sym_dfs[tf] = raw
    enriched = compute_mtf_indicators(sym_dfs)
    if 60 in timeframes:
        enriched = synthesize_60m(enriched)
    return enriched
