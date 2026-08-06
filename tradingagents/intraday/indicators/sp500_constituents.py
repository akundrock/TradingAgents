from __future__ import annotations

import json
import logging
from datetime import date
from functools import lru_cache
from io import StringIO
from pathlib import Path
from typing import Literal

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).resolve().parent / "data" / "sp500_constituents.json"
_WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_USER_AGENT = "TradingAgents/0.3.1 (S&P 500 constituent refresh)"

RefreshSource = Literal["wikipedia", "yfinance"]


@lru_cache(maxsize=1)
def _load_payload() -> dict:
    if not _DATA_PATH.exists():
        logger.warning("Missing bundled SP500 constituents file: %s", _DATA_PATH)
        return {"as_of": "", "source": "missing", "symbols": []}
    return json.loads(_DATA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_sp500_constituents() -> frozenset[str]:
    """Return uppercase S&P 500 symbols from the bundled JSON file."""
    payload = _load_payload()
    symbols = payload.get("symbols") or []
    return frozenset(str(s).strip().upper() for s in symbols if str(s).strip())


def sp500_metadata() -> dict[str, str | int]:
    """Return as_of, source, and count for logging."""
    payload = _load_payload()
    symbols = load_sp500_constituents()
    return {
        "as_of": str(payload.get("as_of") or ""),
        "source": str(payload.get("source") or ""),
        "count": len(symbols),
    }


def is_sp500_constituent(symbol: str) -> bool:
    """Return True if symbol is in the bundled S&P 500 (SPY index) constituent list."""
    return symbol.strip().upper() in load_sp500_constituents()


def bundled_data_path() -> Path:
    return _DATA_PATH


def _normalize_symbols(raw_symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in raw_symbols:
        sym = str(raw).strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        ordered.append(sym)
    return sorted(ordered)


def fetch_sp500_from_wikipedia() -> list[str]:
    """Fetch current S&P 500 symbols from Wikipedia."""
    response = requests.get(
        _WIKIPEDIA_URL,
        headers={"User-Agent": _USER_AGENT},
        timeout=45,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Wikipedia S&P 500 fetch failed (HTTP {response.status_code})"
        )
    tables = pd.read_html(StringIO(response.text))
    if not tables:
        raise RuntimeError("Wikipedia S&P 500 page had no tables")
    df = tables[0]
    if "Symbol" not in df.columns:
        raise RuntimeError("Wikipedia S&P 500 table missing Symbol column")
    return _normalize_symbols(df["Symbol"].astype(str).tolist())


def fetch_sp500_from_yfinance() -> list[str]:
    """Fetch SPY top holdings via yfinance (partial list — not full S&P 500)."""
    import yfinance as yf

    spy = yf.Ticker("SPY")
    holdings = spy.funds_data.top_holdings
    if holdings is None or holdings.empty:
        raise RuntimeError("yfinance returned no SPY top_holdings")
    symbols = _normalize_symbols(holdings.index.astype(str).tolist())
    if len(symbols) < 50:
        raise RuntimeError(
            f"yfinance SPY holdings only returned {len(symbols)} symbols; "
            "use wikipedia for the full S&P 500 list"
        )
    return symbols


def refresh_sp500_constituents(
    source: RefreshSource = "wikipedia",
) -> dict:
    """Fetch current constituents and return payload (does not write file)."""
    if source == "wikipedia":
        symbols = fetch_sp500_from_wikipedia()
        resolved_source = "wikipedia"
    elif source == "yfinance":
        symbols = fetch_sp500_from_yfinance()
        resolved_source = "yfinance"
    else:
        raise ValueError(f"Unknown source: {source}")

    return {
        "as_of": date.today().isoformat(),
        "source": resolved_source,
        "symbols": symbols,
    }


def write_sp500_constituents(payload: dict, path: Path | None = None) -> Path:
    """Write constituent payload to JSON and clear loader caches."""
    target = path or _DATA_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _load_payload.cache_clear()
    load_sp500_constituents.cache_clear()
    return target


def clear_sp500_cache() -> None:
    _load_payload.cache_clear()
    load_sp500_constituents.cache_clear()
