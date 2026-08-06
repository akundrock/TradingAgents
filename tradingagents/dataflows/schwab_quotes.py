from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from tradingagents.dataflows.schwab import _authenticated_get, _normalize_symbol
from tradingagents.dataflows.schwab_streamer import ScreenerCandidate

logger = logging.getLogger(__name__)

QUOTES_URL = "https://api.schwabapi.com/marketdata/v1/quotes"
DEFAULT_CHUNK_SIZE = 100
SP500_QUOTES_KEY = "SP500_QUOTES"


@dataclass
class EquityQuote:
    symbol: str
    last_price: float = 0.0
    net_change: float = 0.0
    total_volume: int = 0


def _field_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _field_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_quote_entry(symbol: str, entry: dict[str, Any]) -> EquityQuote | None:
    if not isinstance(entry, dict):
        return None
    if entry.get("errors"):
        return None

    quote_block = entry.get("quote")
    if isinstance(quote_block, dict):
        data = quote_block
    else:
        data = entry

    sym = str(data.get("symbol") or entry.get("symbol") or symbol).strip().upper()
    if not sym:
        return None

    return EquityQuote(
        symbol=sym,
        last_price=_field_float(data.get("lastPrice")),
        net_change=_field_float(data.get("netChange")),
        total_volume=_field_int(data.get("totalVolume")),
    )


def get_quotes(symbols: list[str]) -> dict[str, EquityQuote]:
    """Fetch quotes for one or more symbols via REST batch endpoint."""
    normalized = [_normalize_symbol(s) for s in symbols if str(s).strip()]
    if not normalized:
        return {}

    params = {"symbols": ",".join(normalized)}
    payload = _authenticated_get(
        QUOTES_URL,
        params,
        no_data_symbol=normalized[0],
        no_data_detail="quotes request failed",
    )
    if not isinstance(payload, dict):
        return {}

    results: dict[str, EquityQuote] = {}
    for key, entry in payload.items():
        if not isinstance(entry, dict):
            continue
        parsed = _parse_quote_entry(str(key), entry)
        if parsed is not None:
            results[parsed.symbol] = parsed
    return results


def get_quotes_batched(
    symbols: list[str],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_workers: int = 5,
) -> dict[str, EquityQuote]:
    """Fetch quotes in parallel chunks."""
    normalized = sorted({_normalize_symbol(s) for s in symbols if str(s).strip()})
    if not normalized:
        return {}

    chunks: list[list[str]] = []
    for i in range(0, len(normalized), chunk_size):
        chunks.append(normalized[i : i + chunk_size])

    merged: dict[str, EquityQuote] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(chunks))) as executor:
        futures = {executor.submit(get_quotes, chunk): chunk for chunk in chunks}
        for future in as_completed(futures):
            merged.update(future.result())
    return merged


def _quote_to_candidate(quote: EquityQuote) -> ScreenerCandidate:
    return ScreenerCandidate(
        symbol=quote.symbol,
        last_price=quote.last_price,
        net_change=quote.net_change,
        total_volume=quote.total_volume,
        volume=quote.total_volume,
        screener_key=SP500_QUOTES_KEY,
    )


def fetch_sp500_quote_candidates(
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_workers: int = 5,
) -> list[ScreenerCandidate]:
    """Fetch REST quotes for all bundled S&P 500 symbols (no volume rank/limit)."""
    from tradingagents.intraday.indicators.sp500_constituents import load_sp500_constituents

    symbols = sorted(load_sp500_constituents())
    if not symbols:
        logger.warning("SP500 quotes screener: no symbols in bundled constituent list")
        return []

    quotes = get_quotes_batched(
        symbols,
        chunk_size=chunk_size,
        max_workers=max_workers,
    )
    candidates = [_quote_to_candidate(q) for q in quotes.values()]

    logger.info(
        "SP500 quotes screener: fetched %d/%d symbols (no volume pre-rank)",
        len(quotes),
        len(symbols),
    )
    return candidates


def fetch_sp500_volume_candidates(
    limit: int,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_workers: int = 5,
) -> list[ScreenerCandidate]:
    """Rank bundled S&P 500 symbols by REST quote totalVolume and return top N."""
    from tradingagents.intraday.indicators.sp500_constituents import load_sp500_constituents

    symbols = sorted(load_sp500_constituents())
    if not symbols:
        logger.warning("SP500 quotes screener: no symbols in bundled constituent list")
        return []

    quotes = get_quotes_batched(
        symbols,
        chunk_size=chunk_size,
        max_workers=max_workers,
    )
    candidates = [_quote_to_candidate(q) for q in quotes.values()]
    ranked = sorted(
        candidates,
        key=lambda c: c.total_volume,
        reverse=True,
    )
    top = ranked[:limit]

    if top:
        top_summary = ", ".join(
            f"{c.symbol}({c.total_volume})" for c in top[:8]
        )
        logger.info(
            "SP500 quotes screener: fetched %d/%d symbols, top: %s",
            len(quotes),
            len(symbols),
            top_summary,
        )
    else:
        logger.warning(
            "SP500 quotes screener: 0 quotes returned for %d symbols",
            len(symbols),
        )
    return top
