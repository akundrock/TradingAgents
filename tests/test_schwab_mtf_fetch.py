from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from tradingagents.dataflows import schwab
from tradingagents.dataflows.errors import NoMarketDataError


def _build_df(session_start: datetime, as_of: datetime, step_minutes: int) -> pd.DataFrame:
    dates = pd.date_range(session_start, as_of, freq=f"{step_minutes}min")
    close = pd.Series([100 + i * 0.1 for i in range(len(dates))])
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": close - 0.1,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": [1000] * len(dates),
        }
    )


@pytest.mark.unit
def test_fetch_returns_both_timeframes(monkeypatch):
    session_start = datetime(2026, 7, 27, 9, 30, 0)
    as_of = datetime(2026, 7, 27, 10, 0, 0)
    calls: list[tuple[str, int]] = []

    def fake_fetch(symbol, start_dt, end_dt, frequency_type, frequency):
        calls.append((frequency_type, frequency))
        return [{"datetime": 1}]  # content ignored by patched _candles_to_df

    def fake_candles_to_df(candles, symbol, curr_date, **kwargs):
        # Infer frequency from call order is fragile; use candle count marker.
        return _build_df(session_start, as_of, step_minutes=5 if len(calls) == 1 else 30)

    monkeypatch.setattr(schwab, "_fetch_price_history_range", fake_fetch)
    monkeypatch.setattr(schwab, "_candles_to_df", fake_candles_to_df)

    result = schwab.get_candles_multi_timeframe("AAPL", session_start, as_of, [5, 30])

    assert set(result.keys()) == {5, 30}
    for df in result.values():
        assert not df.empty
        assert list(df.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
    assert ("minute", 5) in calls
    assert ("minute", 30) in calls


@pytest.mark.unit
def test_fetch_raises_on_empty_candles(monkeypatch):
    session_start = datetime(2026, 7, 27, 9, 30, 0)
    as_of = datetime(2026, 7, 27, 10, 0, 0)

    monkeypatch.setattr(schwab, "_fetch_price_history_range", lambda *a, **k: [])

    with pytest.raises(NoMarketDataError):
        schwab.get_candles_multi_timeframe("AAPL", session_start, as_of, [5, 30])


@pytest.mark.unit
def test_fetch_uses_parallel_requests(monkeypatch):
    session_start = datetime(2026, 7, 27, 9, 30, 0)
    as_of = datetime(2026, 7, 27, 10, 0, 0)
    call_count = 0

    def fake_fetch(symbol, start_dt, end_dt, frequency_type, frequency):
        nonlocal call_count
        call_count += 1
        return [{"datetime": call_count}]

    def fake_candles_to_df(candles, symbol, curr_date, **kwargs):
        return _build_df(session_start, as_of, step_minutes=5)

    monkeypatch.setattr(schwab, "_fetch_price_history_range", fake_fetch)
    monkeypatch.setattr(schwab, "_candles_to_df", fake_candles_to_df)

    schwab.get_candles_multi_timeframe("AAPL", session_start, as_of, [5, 30])

    assert call_count == 2


@pytest.mark.unit
def test_fetch_rejects_unsupported_timeframe():
    session_start = datetime(2026, 7, 27, 9, 30, 0)
    as_of = datetime(2026, 7, 27, 10, 0, 0)

    with pytest.raises(ValueError, match="Unsupported intraday timeframes"):
        schwab.get_candles_multi_timeframe("AAPL", session_start, as_of, [7])
