from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATA_PATH = Path(__file__).resolve().parent / "data" / "sector_tickers.json"

SECTOR_ETF_SYMBOLS: dict[str, str] = {
    "comms": "XLC",
    "cdisc": "XLY",
    "cstap": "XLP",
    "energy": "XLE",
    "fin": "XLF",
    "health": "XLV",
    "indus": "XLI",
    "mats": "XLB",
    "real": "XLRE",
    "tech": "XLK",
    "utils": "XLU",
    "semis": "SMH",
}


@lru_cache(maxsize=1)
def _load_sector_data() -> tuple[dict[str, str], dict[str, str]]:
    if not _DATA_PATH.exists():
        return SECTOR_ETF_SYMBOLS.copy(), {}
    payload = json.loads(_DATA_PATH.read_text())
    etfs = payload.get("sector_etfs", SECTOR_ETF_SYMBOLS)
    tickers = payload.get("ticker_to_sector", {})
    return etfs, tickers


def get_sector_key(symbol: str) -> str | None:
    """Return sector key (e.g. 'tech') for a ticker, or None."""
    _, ticker_map = _load_sector_data()
    return ticker_map.get(symbol.upper())


def get_sector_etf(symbol: str) -> str | None:
    """Return sector ETF symbol for a stock ticker."""
    sector = get_sector_key(symbol)
    if sector is None:
        return None
    etfs, _ = _load_sector_data()
    return etfs.get(sector)


def is_sp500_constituent(symbol: str) -> bool:
    """Return True if symbol is in the S&P 500 (SPY index) constituent list."""
    from tradingagents.intraday.indicators.sp500_constituents import is_sp500_constituent as _is_sp500

    return _is_sp500(symbol)


def sector_aligned_for_direction(
    symbol_power: float,
    sector_power: float,
    direction: str,
) -> bool:
    """Sector confirmation: long needs positive sector momentum, short needs negative."""
    if direction == "long":
        return sector_power > 0 and symbol_power >= sector_power
    if direction == "short":
        return sector_power < 0 and symbol_power <= sector_power
    return False
