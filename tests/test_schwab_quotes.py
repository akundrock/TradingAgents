from __future__ import annotations

from unittest.mock import patch

import pytest

from tradingagents.dataflows.schwab_quotes import (
    _parse_quote_entry,
    fetch_sp500_volume_candidates,
    get_quotes,
    get_quotes_batched,
)
from tradingagents.dataflows.schwab_streamer import (
    ScreenerCandidate,
    _merge_candidates,
    _parse_screener_items,
    _total_volume_anomaly,
)


def test_parse_quote_entry_nested():
    entry = {
        "assetMainType": "EQUITY",
        "quote": {
            "lastPrice": 120.5,
            "netChange": 1.2,
            "totalVolume": 45000000,
        },
    }
    quote = _parse_quote_entry("NVDA", entry)
    assert quote is not None
    assert quote.symbol == "NVDA"
    assert quote.last_price == 120.5
    assert quote.net_change == 1.2
    assert quote.total_volume == 45000000


def test_get_quotes_parses_batch_response():
    payload = {
        "NVDA": {
            "quote": {"lastPrice": 100.0, "totalVolume": 50000000, "netChange": 2.0},
        },
        "AAPL": {
            "quote": {"lastPrice": 190.0, "totalVolume": 40000000, "netChange": -1.0},
        },
    }
    with patch(
        "tradingagents.dataflows.schwab_quotes._authenticated_get",
        return_value=payload,
    ):
        quotes = get_quotes(["NVDA", "AAPL"])

    assert quotes["NVDA"].total_volume == 50000000
    assert quotes["AAPL"].total_volume == 40000000


def test_get_quotes_batched_chunks():
    symbols = [f"SYM{i}" for i in range(5)]
    calls: list[str] = []

    def fake_get(syms: list[str]):
        calls.append(",".join(syms))
        return {s: _parse_quote_entry(s, {"quote": {"totalVolume": 1000}}) for s in syms}

    with patch("tradingagents.dataflows.schwab_quotes.get_quotes", side_effect=fake_get):
        merged = get_quotes_batched(symbols, chunk_size=2, max_workers=2)

    assert len(merged) == 5
    assert len(calls) == 3


def test_fetch_sp500_quote_candidates_returns_all_without_rank():
    fake_quotes = {
        "NVDA": _parse_quote_entry("NVDA", {"quote": {"totalVolume": 90000000, "lastPrice": 120}}),
        "AAPL": _parse_quote_entry("AAPL", {"quote": {"totalVolume": 50000000, "lastPrice": 190}}),
    }

    with (
        patch(
            "tradingagents.intraday.indicators.sp500_constituents.load_sp500_constituents",
            return_value=frozenset(["NVDA", "AAPL"]),
        ),
        patch(
            "tradingagents.dataflows.schwab_quotes.get_quotes_batched",
            return_value=fake_quotes,
        ),
    ):
        from tradingagents.dataflows.schwab_quotes import fetch_sp500_quote_candidates

        candidates = fetch_sp500_quote_candidates()

    assert len(candidates) == 2
    symbols = {c.symbol for c in candidates}
    assert symbols == {"NVDA", "AAPL"}


def test_fetch_sp500_volume_candidates_ranks_by_volume():
    fake_quotes = {
        "NVDA": _parse_quote_entry("NVDA", {"quote": {"totalVolume": 90000000, "lastPrice": 120}}),
        "AAPL": _parse_quote_entry("AAPL", {"quote": {"totalVolume": 50000000, "lastPrice": 190}}),
        "F": _parse_quote_entry("F", {"quote": {"totalVolume": 10000000, "lastPrice": 12}}),
    }

    with (
        patch(
            "tradingagents.intraday.indicators.sp500_constituents.load_sp500_constituents",
            return_value=frozenset(["NVDA", "AAPL", "F"]),
        ),
        patch(
            "tradingagents.dataflows.schwab_quotes.get_quotes_batched",
            return_value=fake_quotes,
        ),
    ):
        candidates = fetch_sp500_volume_candidates(2)

    assert [c.symbol for c in candidates] == ["NVDA", "AAPL"]
    assert candidates[0].screener_key == "SP500_QUOTES"


@pytest.mark.unit
def test_total_volume_anomaly_detects_identical_values():
    batch = [
        ScreenerCandidate(symbol="A", total_volume=100, volume=10),
        ScreenerCandidate(symbol="B", total_volume=100, volume=20),
        ScreenerCandidate(symbol="C", total_volume=100, volume=30),
    ]
    assert _total_volume_anomaly(batch)


@pytest.mark.unit
def test_merge_candidates_uses_volume_when_total_volume_anomaly():
    batch = [
        ScreenerCandidate(symbol="A", total_volume=999, volume=10),
        ScreenerCandidate(symbol="B", total_volume=999, volume=50),
        ScreenerCandidate(symbol="C", total_volume=999, volume=30),
    ]
    merged = _merge_candidates([batch], limit=10)
    assert merged[0].symbol == "B"


@pytest.mark.unit
def test_parse_screener_items_numeric_keys():
    content = {
        "key": "NASDAQ_VOLUME_0",
        "4": [
            {
                "0": "NVDA",
                "3": 120.5,
                "6": 5000000,
                "7": 80000000,
            },
        ],
    }
    items = _parse_screener_items(content, "NASDAQ_VOLUME_0")
    assert len(items) == 1
    assert items[0].symbol == "NVDA"
    assert items[0].last_price == 120.5
    assert items[0].volume == 5000000
    assert items[0].total_volume == 80000000
