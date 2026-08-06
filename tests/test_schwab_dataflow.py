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
def test_candles_to_df_converts_intraday_timestamps_to_eastern():
    # 9:30 AM ET on 2026-08-05 (EDT, UTC-4) = 13:30 UTC
    utc_open = datetime(2026, 8, 5, 13, 30, tzinfo=timezone.utc)
    candles = [
        {
            "datetime": int(utc_open.timestamp() * 1000),
            "open": 100.0,
            "high": 101.0,
            "low": 99.5,
            "close": 100.5,
            "volume": 1000,
        }
    ]
    df = schwab._candles_to_df(
        candles,
        "AAPL",
        "2026-08-05",
        session_timezone="America/New_York",
    )
    bar_time = pd.Timestamp(df["Date"].iloc[0])
    assert bar_time.hour == 9
    assert bar_time.minute == 30
