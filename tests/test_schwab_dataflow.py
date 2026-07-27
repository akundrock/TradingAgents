from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from tradingagents.dataflows import schwab


def _ms(date_str: str) -> int:
    return int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000)


@pytest.mark.unit
def test_get_stock_filters_range_and_returns_csv(monkeypatch):
    candles = [
        {"datetime": _ms("2026-01-01"), "open": 100, "high": 105, "low": 99, "close": 104, "volume": 10},
        {"datetime": _ms("2026-01-02"), "open": 104, "high": 106, "low": 101, "close": 103, "volume": 11},
        {"datetime": _ms("2026-01-03"), "open": 103, "high": 107, "low": 102, "close": 106, "volume": 12},
    ]
    monkeypatch.setattr(schwab, "_fetch_price_history", lambda *a, **k: candles)

    out = schwab.get_stock("AAPL", "2026-01-02", "2026-01-03")
    assert "# Stock data for AAPL from 2026-01-02 to 2026-01-03" in out
    assert "# Total records: 2" in out
    assert "2026-01-01" not in out
    assert "2026-01-02" in out
    assert "2026-01-03" in out


@pytest.mark.unit
def test_get_access_token_missing_tokens_raises(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_SCHWAB_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(schwab, "_load_tokens", lambda: None)

    with pytest.raises(schwab.SchwabNotConfiguredError):
        schwab._get_access_token()


@pytest.mark.unit
def test_get_indicator_uses_schwab_ohlcv(monkeypatch):
    dates = pd.bdate_range("2026-05-01", "2026-05-20")
    data = pd.DataFrame(
        {
            "Date": dates,
            "Open": [100 + i for i in range(len(dates))],
            "High": [101 + i for i in range(len(dates))],
            "Low": [99 + i for i in range(len(dates))],
            "Close": [100 + i for i in range(len(dates))],
            "Volume": [1000 + i for i in range(len(dates))],
        }
    )
    monkeypatch.setattr(schwab, "_load_ohlcv", lambda *a, **k: data)

    out = schwab.get_indicator("AAPL", "close_10_ema", "2026-05-20", 3)
    assert "close_10_ema values from 2026-05-17 to 2026-05-20" in out
    assert "2026-05-20:" in out


@pytest.mark.unit
def test_get_market_internals_fetches_direct_vold_symbol(monkeypatch):
    def _candle(ts: str, close: float) -> dict:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        ms = int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        return {
            "datetime": ms,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1,
        }

    internal_data = {
        "$ADD": [_candle("2026-07-01 09:30:00", 300.0), _candle("2026-07-01 09:35:00", 250.0)],
        "$TICK": [_candle("2026-07-01 09:30:00", 1100.0), _candle("2026-07-01 09:35:00", 900.0)],
        "$VOLD": [_candle("2026-07-01 09:30:00", 800000.0), _candle("2026-07-01 09:35:00", 600000.0)],
    }

    def fake_fetch(symbol, *args, **kwargs):
        if symbol in internal_data:
            return internal_data[symbol]
        raise schwab.NoMarketDataError(symbol, symbol, "missing")

    monkeypatch.setattr(schwab, "_fetch_price_history_range", fake_fetch)

    out = schwab.get_market_internals(
        "2026-07-01 09:30:00",
        "2026-07-01 09:35:00",
        "5m",
    )
    assert "# Market internals" in out
    assert "$ADD" in out and "$TICK" in out and "$VOLD" in out
    assert "800000.0" in out


@pytest.mark.unit
def test_get_market_internals_raises_when_all_symbols_missing(monkeypatch):
    def fake_fetch(*args, **kwargs):
        raise schwab.NoMarketDataError("MARKET_INTERNALS", "MARKET_INTERNALS", "missing")

    monkeypatch.setattr(schwab, "_fetch_price_history_range", fake_fetch)

    with pytest.raises(schwab.NoMarketDataError):
        schwab.get_market_internals(
            "2026-07-01 09:30:00",
            "2026-07-01 09:35:00",
            "5m",
        )
