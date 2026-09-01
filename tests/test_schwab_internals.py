from datetime import datetime

import pandas as pd
import pytest

from tradingagents.dataflows import schwab
from tradingagents.dataflows.errors import NoMarketDataError

SESSION_START = datetime(2026, 3, 30, 9, 30)
AS_OF = datetime(2026, 3, 30, 10, 0)

_VALUES = {"$ADD": 1200.0, "$TICK": 650.0, "$VOLD": 4_500_000.0}


def _epoch_ms(ts: datetime) -> int:
    return int(pd.Timestamp(ts).tz_localize("America/New_York").timestamp() * 1000)


def _candles(symbol: str, count: int = 6) -> list[dict]:
    base = _VALUES[symbol]
    return [
        {
            "datetime": _epoch_ms(SESSION_START + pd.Timedelta(minutes=5 * i)),
            "open": 0.0,
            "high": 0.0,
            "low": 0.0,
            "close": base + i,
            "volume": 0,
        }
        for i in range(count)
    ]


@pytest.fixture()
def recorded_calls(monkeypatch):
    calls = []

    def fake_fetch(*, symbol, start_dt, end_dt, frequency_type, frequency):
        calls.append(
            {
                "symbol": symbol,
                "start_dt": start_dt,
                "end_dt": end_dt,
                "frequency_type": frequency_type,
                "frequency": frequency,
            }
        )
        return _candles(symbol)

    def fake_period(*, symbol, period_days, frequency_type, frequency):
        raise NoMarketDataError(symbol, symbol, "period path disabled in test")

    monkeypatch.setattr(schwab, "_fetch_price_history_range", fake_fetch)
    monkeypatch.setattr(schwab, "_fetch_price_history_period", fake_period)
    monkeypatch.setattr(schwab, "_backfill_internals_from_quotes", lambda frame, session_start=None: frame)
    monkeypatch.setattr(schwab, "_backfill_internals_from_streamer", lambda frame: frame)
    return calls


@pytest.mark.unit
def test_internal_symbol_map():
    assert schwab.MARKET_INTERNAL_SYMBOLS == {"add": "$ADD", "tick": "$TICK", "vold": "$VOLD"}


@pytest.mark.unit
def test_get_internals_frame_requests_every_symbol_as_minute_candles(recorded_calls):
    schwab.get_internals_frame(SESSION_START, AS_OF, "5m")

    assert {c["symbol"] for c in recorded_calls} == {"$ADD", "$TICK", "$VOLD"}
    assert {c["frequency_type"] for c in recorded_calls} == {"minute"}
    assert {c["frequency"] for c in recorded_calls} == {5}
    assert {c["start_dt"] for c in recorded_calls} == {SESSION_START}
    assert {c["end_dt"] for c in recorded_calls} == {AS_OF}


@pytest.mark.unit
@pytest.mark.parametrize(("interval", "frequency"), [("1m", 1), ("5m", 5), ("30m", 30)])
def test_interval_maps_to_the_schwab_frequency(recorded_calls, interval, frequency):
    schwab.get_internals_frame(SESSION_START, AS_OF, interval)
    assert {c["frequency"] for c in recorded_calls} == {frequency}


@pytest.mark.unit
def test_get_internals_frame_shape_and_values(recorded_calls, monkeypatch):
    monkeypatch.setattr(schwab, "_backfill_internals_from_quotes", lambda frame, session_start=None: frame)
    frame = schwab.get_internals_frame(SESSION_START, AS_OF, "5m")

    assert list(frame.columns) == ["Date", "add", "tick", "vold"]
    assert len(frame) == 6
    assert frame["Date"].is_monotonic_increasing
    assert frame["Date"].iloc[0] == pd.Timestamp(SESSION_START)
    # Values come straight from each candle's close.
    assert frame["add"].tolist() == [1200.0 + i for i in range(6)]
    assert frame["tick"].tolist() == [650.0 + i for i in range(6)]
    assert frame["vold"].tolist() == [4_500_000.0 + i for i in range(6)]


@pytest.mark.unit
def test_get_internals_frame_rejects_an_unsupported_interval():
    with pytest.raises(ValueError, match="Unsupported internals interval"):
        schwab.get_internals_frame(SESSION_START, AS_OF, "7m")


@pytest.mark.unit
def test_get_internals_frame_rejects_a_non_advancing_window():
    with pytest.raises(ValueError, match="as_of must be after session_start"):
        schwab.get_internals_frame(AS_OF, SESSION_START, "5m")


@pytest.mark.unit
def test_partial_failure_yields_nan_for_the_missing_column(monkeypatch):
    def fake_fetch(*, symbol, start_dt, end_dt, frequency_type, frequency):
        if symbol == "$TICK":
            raise RuntimeError("tick feed down")
        return _candles(symbol)

    monkeypatch.setattr(schwab, "_fetch_price_history_range", fake_fetch)
    monkeypatch.setattr(
        schwab,
        "_fetch_price_history_period",
        lambda **kwargs: (_ for _ in ()).throw(
            NoMarketDataError("$ADD", "$ADD", "period disabled")
        ),
    )
    monkeypatch.setattr(schwab, "_backfill_internals_from_quotes", lambda frame, session_start=None: frame)
    monkeypatch.setattr(schwab, "_backfill_internals_from_streamer", lambda frame: frame)
    frame = schwab.get_internals_frame(SESSION_START, AS_OF, "5m")

    assert list(frame.columns) == ["Date", "add", "tick", "vold"]
    assert frame["tick"].isna().all()
    assert frame["add"].notna().all()
    assert frame["vold"].notna().all()


@pytest.mark.unit
def test_total_failure_raises_no_market_data(monkeypatch):
    def fake_fetch(**kwargs):
        raise RuntimeError("schwab down")

    monkeypatch.setattr(schwab, "_fetch_price_history_range", fake_fetch)
    monkeypatch.setattr(schwab, "_fetch_price_history_period", fake_fetch)
    with pytest.raises(NoMarketDataError) as excinfo:
        schwab.get_internals_frame(SESSION_START, AS_OF, "5m")
    assert "no internals available" in str(excinfo.value)


@pytest.mark.unit
def test_empty_candles_raise_no_market_data(monkeypatch):
    monkeypatch.setattr(schwab, "_fetch_price_history_range", lambda **kwargs: [])
    monkeypatch.setattr(schwab, "_fetch_price_history_period", lambda **kwargs: [])
    with pytest.raises(NoMarketDataError):
        schwab.get_internals_frame(SESSION_START, AS_OF, "5m")


@pytest.mark.unit
def test_internal_candle_value_prefers_close_then_bar_midpoint():
    assert schwab._internal_candle_value({"close": 120.0, "high": 200.0, "low": 100.0}) == 120.0
    assert schwab._internal_candle_value({"close": 0.0, "high": 200.0, "low": 100.0}) == 150.0
    assert schwab._internal_candle_value({"close": 0.0, "high": 81.0, "low": 0.0}) == 81.0


@pytest.mark.unit
def test_market_epoch_ms_uses_eastern_wall_clock():
    # 09:30 ET should not shift when the host is outside Eastern.
    ts = datetime(2026, 9, 1, 9, 30)
    ms = schwab._market_epoch_ms(ts)
    parsed = pd.to_datetime(ms, unit="ms", utc=True).tz_convert("America/New_York")
    assert parsed.hour == 9 and parsed.minute == 30


@pytest.mark.unit
def test_fetch_internal_series_skips_incomplete_candles(monkeypatch):
    candles = _candles("$ADD", count=3)
    candles[1]["close"] = None
    candles[2]["datetime"] = None
    monkeypatch.setattr(schwab, "_fetch_price_history_range", lambda **kwargs: candles)
    monkeypatch.setattr(
        schwab,
        "_fetch_price_history_period",
        lambda **kwargs: (_ for _ in ()).throw(NoMarketDataError("$ADD", "$ADD", "nope")),
    )

    series = schwab._fetch_internal_series("$ADD", SESSION_START, AS_OF, 5)
    assert len(series) == 1
    assert series.iloc[0] == 1200.0


@pytest.mark.unit
def test_fetch_internal_series_trims_to_the_window(monkeypatch):
    monkeypatch.setattr(schwab, "_fetch_price_history_range", lambda **kwargs: _candles("$ADD", 12))
    monkeypatch.setattr(
        schwab,
        "_fetch_price_history_period",
        lambda **kwargs: (_ for _ in ()).throw(NoMarketDataError("$ADD", "$ADD", "nope")),
    )
    series = schwab._fetch_internal_series("$ADD", SESSION_START, AS_OF, 5)
    assert series.index.max() <= pd.Timestamp(AS_OF)
    assert series.index.min() >= pd.Timestamp(SESSION_START)


@pytest.mark.unit
def test_get_internals_frame_backfills_from_streamer(monkeypatch):
    def fake_fetch(*, symbol, start_dt, end_dt, frequency_type, frequency):
        if symbol == "$TICK":
            return _candles(symbol)
        raise NoMarketDataError(symbol, symbol, "empty")

    monkeypatch.setattr(schwab, "_fetch_price_history_range", fake_fetch)
    monkeypatch.setattr(
        schwab,
        "_fetch_price_history_period",
        lambda **kwargs: (_ for _ in ()).throw(NoMarketDataError("x", "x", "nope")),
    )
    monkeypatch.setattr(schwab, "_backfill_internals_from_quotes", lambda frame, session_start=None: frame)

    def fake_streamer(frame):
        frame = frame.copy()
        frame.loc[frame.index[-1], ["add", "vold"]] = [850.0, -458_000_000.0]
        return frame

    monkeypatch.setattr(schwab, "_backfill_internals_from_streamer", fake_streamer)
    frame = schwab.get_internals_frame(SESSION_START, AS_OF, "5m")
    assert frame["add"].iloc[-1] == 850.0
    assert frame["vold"].iloc[-1] == -458_000_000.0
    assert frame["tick"].iloc[-1] == 655.0


@pytest.mark.unit
def test_vold_component_delta_scale_uses_thousands_feed_for_small_baselines():
    assert schwab._vold_component_delta_scale(21_994.0, 29_970.0) == 1000.0


@pytest.mark.unit
def test_vold_component_delta_scale_scales_inflated_baselines():
    scale = schwab._vold_component_delta_scale(22_367_899_648.0, 458_672_608.0)
    assert scale == pytest.approx(schwab._VOLD_LARGE_BASELINE_CALIBRATION / 22_367_899_648.0)


@pytest.mark.unit
def test_fetch_synthetic_add_series_subtracts_components(monkeypatch):
    session = pd.Timestamp(SESSION_START)

    def fake_fetch(symbol, start_dt, end_dt, frequency):
        if symbol == "$ADVN":
            return pd.Series({session: 900.0, session + pd.Timedelta(minutes=5): 910.0})
        return pd.Series({session: 1_800.0, session + pd.Timedelta(minutes=5): 1_810.0})

    monkeypatch.setattr(schwab, "_fetch_internal_series", fake_fetch)
    series = schwab._fetch_synthetic_add_series(SESSION_START, AS_OF, 5)
    assert series.tolist() == [-900.0, -900.0]


@pytest.mark.unit
def test_fetch_synthetic_vold_series_uses_delta_on_inflated_feed(monkeypatch):
    session = pd.Timestamp(SESSION_START)

    def fake_fetch(symbol, start_dt, end_dt, frequency):
        if symbol == "$UVOL":
            return pd.Series(
                {
                    session: 22_367_899_648.0,
                    session + pd.Timedelta(minutes=5): 22_400_000_000.0,
                }
            )
        return pd.Series(
            {
                session: 458_672_608.0,
                session + pd.Timedelta(minutes=5): 460_000_000.0,
            }
        )

    monkeypatch.setattr(schwab, "_fetch_internal_series", fake_fetch)
    series = schwab._fetch_synthetic_vold_series(SESSION_START, AS_OF, 5)
    assert series.iloc[0] == pytest.approx(0.0)
    delta_diff = (22_400_000_000.0 - 460_000_000.0) - (22_367_899_648.0 - 458_672_608.0)
    scale = schwab._VOLD_LARGE_BASELINE_CALIBRATION / 22_367_899_648.0
    assert series.iloc[1] == pytest.approx(delta_diff * scale)


@pytest.mark.unit
def test_fetch_internal_key_series_falls_back_to_synthetic_add(monkeypatch):
    def fake_fetch(symbol, start_dt, end_dt, frequency):
        if symbol == "$ADD":
            raise NoMarketDataError(symbol, symbol, "empty")
        if symbol == "$ADVN":
            return pd.Series({pd.Timestamp(SESSION_START): 1_000.0})
        if symbol == "$DECN":
            return pd.Series({pd.Timestamp(SESSION_START): 1_900.0})
        raise AssertionError(symbol)

    monkeypatch.setattr(schwab, "_fetch_internal_series", fake_fetch)
    series = schwab._fetch_internal_key_series("add", SESSION_START, AS_OF, 5)
    assert series.iloc[0] == -900.0


@pytest.mark.unit
def test_get_internals_frame_fetches_tick_before_breadth(monkeypatch):
    call_order: list[str] = []

    def fake_fetch(key, session_start, end_dt, frequency):
        call_order.append(key)
        if key == "tick":
            return pd.Series({pd.Timestamp(SESSION_START): 650.0})
        if key == "add":
            return pd.Series({pd.Timestamp(SESSION_START): 1200.0})
        if key == "vold":
            return pd.Series({pd.Timestamp(SESSION_START): 4_500_000.0})
        raise AssertionError(key)

    monkeypatch.setattr(schwab, "_fetch_internal_key_series", fake_fetch)
    monkeypatch.setattr(schwab, "_backfill_internals_from_quotes", lambda frame, session_start=None: frame)
    monkeypatch.setattr(schwab, "_backfill_internals_from_streamer", lambda frame: frame)
    schwab.get_internals_frame(SESSION_START, AS_OF, "5m")
    assert call_order[0] == "tick"
    assert set(call_order[1:3]) == {"add", "vold"}


@pytest.mark.unit
def test_get_market_internals_returns_annotated_csv(recorded_calls, monkeypatch):
    monkeypatch.setattr(schwab, "_backfill_internals_from_quotes", lambda frame, session_start=None: frame)
    text = schwab.get_market_internals(
        "2026-03-30 09:30:00", "2026-03-30 10:00:00", "5m"
    )

    assert text.startswith("# Market internals ($ADD/$TICK/$VOLD)")
    assert "at interval 5m" in text
    assert "# Total records: 6" in text
    assert "Date,add,tick,vold" in text
    assert "1200.0" in text
