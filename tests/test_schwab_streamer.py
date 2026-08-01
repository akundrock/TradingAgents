from __future__ import annotations

from tradingagents.dataflows.schwab_streamer import (
    ScreenerCandidate,
    _merge_candidates,
    _parse_screener_items,
)


def test_parse_screener_items():
    content = {
        "key": "NASDAQ_VOLUME_0",
        "4": [
            {
                "symbol": "NVDA",
                "lastPrice": 120.5,
                "volume": 5000000,
                "totalVolume": 80000000,
            },
            {
                "symbol": "AAPL",
                "lastPrice": 190.0,
                "volume": 3000000,
                "totalVolume": 60000000,
            },
        ],
    }
    items = _parse_screener_items(content, "NASDAQ_VOLUME_0")
    assert len(items) == 2
    assert items[0].symbol == "NVDA"
    assert items[0].total_volume == 80000000
    assert items[0].screener_key == "NASDAQ_VOLUME_0"


def test_merge_candidates_dedupes_and_ranks():
    batch_a = [
        ScreenerCandidate(symbol="NVDA", total_volume=100, volume=10),
        ScreenerCandidate(symbol="AAPL", total_volume=50, volume=5),
    ]
    batch_b = [
        ScreenerCandidate(symbol="NVDA", total_volume=80, volume=20),
        ScreenerCandidate(symbol="TSLA", total_volume=90, volume=15),
    ]
    merged = _merge_candidates([batch_a, batch_b], limit=10)
    symbols = [c.symbol for c in merged]
    assert symbols[0] == "NVDA"
    assert "TSLA" in symbols
    assert "AAPL" in symbols
