from datetime import datetime, timedelta

import pandas as pd
import pytest

from tradingagents.dataflows import schwab
from tradingagents.dataflows import schwab_quotes
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.schwab_quotes import EquityQuote


SESSION_START = datetime(2026, 3, 30, 9, 30)
AS_OF = datetime(2026, 3, 30, 10, 0)

_VALUES = {"$ADD": 1200.0, "$TICK": 650.0, "$VOLD": 4_500_000.0}
_VALUES["$ADVN"] = 2_500.0
_VALUES["$DECN"] = 1_100.0
_VALUES["$UVOL"] = 350_000.0
_VALUES["$DVOL"] = 450_000.0



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


class _FakeResponse:
    """Minimal stand-in for a successful price-history HTTP response."""

    def __init__(self, payload: dict):
        self.status_code = 200
        self._payload = payload

    def json(self) -> dict:
        return self._payload


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
def test_get_internals_frame_index_stays_unnamed_for_date_merges(recorded_calls):
    """Snapshot merges bars with internals on ``Date``; a named index that
    duplicates the ``Date`` column makes pandas raise an ambiguity ValueError."""
    frame = schwab.get_internals_frame(SESSION_START, AS_OF, "5m")
    assert frame.index.name is None
    merged = pd.DataFrame({"Date": frame["Date"]}).merge(
        frame, on="Date", how="left", suffixes=("", "_internal")
    )
    assert len(merged) == len(frame)


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
def test_internal_candle_value_prefers_close_then_two_sided_midpoint():
    # A real close always wins.
    assert schwab._internal_candle_value({"close": 120.0, "high": 200.0, "low": 100.0}) == 120.0
    # Both sides populated: the bar midpoint is an unbiased in-bar estimate.
    assert schwab._internal_candle_value({"close": 0.0, "high": 200.0, "low": 100.0}) == 150.0
    # Schwab's live defect: close=0 (or absent) with only ONE side populated.
    # Substituting the bar maximum is positive-biased for oscillating readings
    # like $TICK (a bar's high can never read below zero), so these are unknown.
    assert schwab._internal_candle_value({"close": 0.0, "high": 339.0, "low": 0.0}) is None
    assert schwab._internal_candle_value({"close": None, "high": 81.0, "low": 0.0}) is None
    assert schwab._internal_candle_value({"close": 0.0}) is None
    # A candle with no usable fields is unknown, never 0.0.
    assert schwab._internal_candle_value({"close": 0.0, "high": 0.0, "low": 0.0}) is None



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

    def fake_fetch(key, session_start, end_dt, frequency, source_frequency=None):
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


@pytest.mark.unit
def test_get_internals_frame_drops_unreadable_candles_and_backfills_last_bar(
    monkeypatch,
):
    """A defective $TICK candle feed must never fabricate a value from `high`."""

    def fake_fetch(*, symbol, start_dt, end_dt, frequency_type, frequency):
        candles = _candles(symbol, count=3)
        if symbol == "$TICK":
            # Schwab's live defect: close=0 and low=0 with only `high` populated.
            for candle in candles:
                candle.update({"close": 0.0, "high": 339.0, "low": 0.0})
        return candles

    monkeypatch.setattr(schwab, "_fetch_price_history_range", fake_fetch)
    monkeypatch.setattr(
        schwab,
        "_fetch_price_history_period",
        lambda **kwargs: (_ for _ in ()).throw(
            NoMarketDataError("$TICK", "$TICK", "period disabled")
        ),
    )

    def fake_get_quotes(symbols):
        return {
            symbol: EquityQuote(symbol=symbol, last_price=-183.0)
            for symbol in symbols
        }

    monkeypatch.setattr(schwab_quotes, "get_quotes", fake_get_quotes)
    monkeypatch.setattr(schwab, "_backfill_internals_from_streamer", lambda frame: frame)

    frame = schwab.get_internals_frame(SESSION_START, AS_OF, "5m")

    # Defective history is unknown (NaN) — never the fabricated candle highs.
    assert frame["tick"].iloc[:-1].isna().all()
    # The last bar carries the true live quote, matching the TOS panel,
    # and valid candle closes are never overwritten by quotes.
    assert frame["tick"].iloc[-1] == -183.0
    assert frame["add"].iloc[-1] == 1202.0
    assert frame["vold"].iloc[-1] == 4_500_002.0


@pytest.mark.unit
def test_quote_backfill_never_overrides_a_valid_candle_close(monkeypatch):
    monkeypatch.setattr(
        schwab_quotes,
        "get_quotes",
        lambda symbols: {
            symbol: EquityQuote(symbol=symbol, last_price=-999.0)
            for symbol in symbols
        },
    )
    frame = pd.DataFrame(
        {
            "Date": [
                pd.Timestamp(SESSION_START),
                pd.Timestamp(SESSION_START) + pd.Timedelta(minutes=5),
            ],
            "add": [1200.0, 1210.0],
            "tick": [650.0, 655.0],
            "vold": [4_500_000.0, 4_510_000.0],
        }
    )
    patched = schwab._backfill_internals_from_quotes(frame, session_start=SESSION_START)
    assert patched["tick"].iloc[-1] == 655.0
    assert patched["add"].iloc[-1] == 1210.0
    assert patched["vold"].iloc[-1] == 4_510_000.0


# --- price-history response cache -------------------------------------------


@pytest.mark.unit
def test_price_history_range_reuses_one_http_call_within_a_5m_bucket(monkeypatch):
    schwab.clear_price_history_cache()
    calls = {"n": 0}

    def fake_get(url, params, *, rate_limit_message):
        calls["n"] += 1
        return _FakeResponse({"candles": _candles("$TICK", 6)})

    monkeypatch.setattr(schwab, "_schwab_get_with_retry", fake_get)
    schwab._fetch_price_history_range("$TICK", SESSION_START, AS_OF, "minute", 5)
    schwab._fetch_price_history_range(
        "$TICK", SESSION_START, AS_OF + timedelta(seconds=90), "minute", 5
    )
    assert calls["n"] == 1  # same 5m bucket -> one HTTP call


@pytest.mark.unit
def test_price_history_range_refetches_in_the_next_bucket(monkeypatch):
    schwab.clear_price_history_cache()
    calls = {"n": 0}

    def fake_get(url, params, *, rate_limit_message):
        calls["n"] += 1
        return _FakeResponse({"candles": [], "empty": True})

    monkeypatch.setattr(schwab, "_schwab_get_with_retry", fake_get)
    with pytest.raises(NoMarketDataError):
        schwab._fetch_price_history_range("$TICK", SESSION_START, AS_OF, "minute", 5)
    with pytest.raises(NoMarketDataError):
        schwab._fetch_price_history_range(
            "$TICK", SESSION_START, AS_OF + timedelta(minutes=5, seconds=1), "minute", 5
        )
    assert calls["n"] == 2  # different buckets -> separate fetches


@pytest.mark.unit
def test_price_history_cache_expires_after_ttl(monkeypatch):
    schwab.clear_price_history_cache()
    clock = {"now": 1000.0}
    monkeypatch.setattr(schwab, "_cache_now", lambda: clock["now"])
    calls = {"n": 0}

    def fake_get(url, params, *, rate_limit_message):
        calls["n"] += 1
        return _FakeResponse({"candles": _candles("$TICK", 6)})

    monkeypatch.setattr(schwab, "_schwab_get_with_retry", fake_get)
    schwab._fetch_price_history_range("$TICK", SESSION_START, AS_OF, "minute", 5)
    schwab._fetch_price_history_range("$TICK", SESSION_START, AS_OF, "minute", 5)
    assert calls["n"] == 1  # inside the 120s TTL: served from cache
    clock["now"] += 121.0  # age past the default 120s TTL
    schwab._fetch_price_history_range("$TICK", SESSION_START, AS_OF, "minute", 5)
    assert calls["n"] == 2  # expired -> refetched


@pytest.mark.unit
def test_no_market_data_errors_are_not_cached(monkeypatch):
    schwab.clear_price_history_cache()
    calls = {"n": 0}

    def fake_get(url, params, *, rate_limit_message):
        calls["n"] += 1
        if calls["n"] == 1:
            raise NoMarketDataError("$TICK", "$TICK", "Schwab returned no candles")
        return _FakeResponse({"candles": _candles("$TICK", 6)})

    monkeypatch.setattr(schwab, "_schwab_get_with_retry", fake_get)
    with pytest.raises(NoMarketDataError):
        schwab._fetch_price_history_range("$TICK", SESSION_START, AS_OF, "minute", 5)
    candles = schwab._fetch_price_history_range("$TICK", SESSION_START, AS_OF, "minute", 5)
    assert len(candles) == 6
    assert calls["n"] == 2  # the failed fetch was not cached; the retry hit HTTP


@pytest.mark.unit
def test_price_history_cache_can_be_disabled(monkeypatch):
    schwab.clear_price_history_cache()
    monkeypatch.setenv("TRADINGAGENTS_SCHWAB_PRICE_HISTORY_TTL_SECONDS", "0")
    calls = {"n": 0}

    def fake_get(url, params, *, rate_limit_message):
        calls["n"] += 1
        return _FakeResponse({"candles": _candles("$TICK", 6)})

    monkeypatch.setattr(schwab, "_schwab_get_with_retry", fake_get)
    schwab._fetch_price_history_range("$TICK", SESSION_START, AS_OF, "minute", 5)
    schwab._fetch_price_history_range("$TICK", SESSION_START, AS_OF, "minute", 5)
    assert calls["n"] == 2  # TTL 0 disables the cache entirely


@pytest.mark.unit
@pytest.mark.parametrize(
    ("dt", "expected"),
    [
        (datetime(2026, 9, 11, 10, 0, 0), datetime(2026, 9, 11, 10, 0)),
        (datetime(2026, 9, 11, 10, 0, 1), datetime(2026, 9, 11, 10, 5)),
        (datetime(2026, 9, 11, 10, 4, 59), datetime(2026, 9, 11, 10, 5)),
    ],
)
def test_bucket_end_rounds_up_to_the_next_boundary(dt, expected):
    assert schwab._bucket_end(dt) == expected


# --- provenance metadata on the internals frame -------------------------------


@pytest.mark.unit
def test_direct_candle_values_report_candle_provenance(recorded_calls):
    frame = schwab.get_internals_frame(SESSION_START, AS_OF, "5m")
    assert frame.attrs["provenance"] == {"add": "candles", "tick": "candles", "vold": "candles"}
    assert dict(frame.attrs.get("backfilled", {})) == {}
    assert len(frame) == 6  # recorded_calls fixture still drives the same candles


@pytest.mark.unit
def test_synthetic_vold_reports_synthetic_provenance(monkeypatch):
    def fake_range(*, symbol, start_dt, end_dt, frequency_type, frequency):
        if symbol in ("$TICK", "$UVOL", "$DVOL"):
            return _candles(symbol)
        raise NoMarketDataError(symbol, symbol, "no candles")

    def fake_period(*, symbol, period_days, frequency_type, frequency):
        raise NoMarketDataError(symbol, symbol, "period path disabled in test")

    monkeypatch.setattr(schwab, "_fetch_price_history_range", fake_range)
    monkeypatch.setattr(schwab, "_fetch_price_history_period", fake_period)
    monkeypatch.setattr(schwab, "_backfill_internals_from_quotes", lambda frame, session_start=None: frame)
    monkeypatch.setattr(schwab, "_backfill_internals_from_streamer", lambda frame: frame)
    frame = schwab.get_internals_frame(SESSION_START, AS_OF, "5m")
    assert frame.attrs["provenance"]["vold"] == "synthetic"
    assert frame.attrs["provenance"]["tick"] == "candles"


@pytest.mark.unit
def test_quote_backfill_is_recorded_in_backfilled_attrs(monkeypatch):
    from types import SimpleNamespace

    quotes = {
        symbol: SimpleNamespace(last_price=6_000.0)
        for symbol in ("$ADD", "$TICK", "$VOLD")
    }
    monkeypatch.setattr(schwab_quotes, "get_quotes", lambda symbols: quotes)
    frame = pd.DataFrame(
        {
            "Date": [pd.Timestamp(SESSION_START), pd.Timestamp(AS_OF)],
            "add": [1200.0, float("nan")],
            "tick": [650.0, float("nan")],
            "vold": [4_500_000.0, float("nan")],
        }
    ).set_index("Date")

    frame = schwab._backfill_internals_from_quotes(frame)
    assert frame.attrs["backfilled"] == {"add": "quotes", "tick": "quotes", "vold": "quotes"}
    assert frame["add"].iloc[-1] == 6_000.0
    assert frame["tick"].iloc[-1] == 6_000.0
    assert frame["vold"].iloc[-1] == 6_000.0


# --- 1m -> 5m internals aggregation ------------------------------------------


@pytest.mark.unit
def test_aggregate_series_takes_the_last_reading_per_5m_bucket():
    index = pd.to_datetime(
        [
            "2026-09-11 10:00", "2026-09-11 10:01", "2026-09-11 10:02", "2026-09-11 10:04",
            "2026-09-11 10:05", "2026-09-11 10:06",
        ]
    )
    series = pd.Series([10.0, 15.0, 20.0, 30.0, 45.0, 60.0], index=index)
    out = schwab._aggregate_series_to_bucket(series, source_minutes=1, target_minutes=5)
    assert out.index.tolist() == [pd.Timestamp("2026-09-11 10:00"), pd.Timestamp("2026-09-11 10:05")]
    assert out.loc[pd.Timestamp("2026-09-11 10:00")] == 30.0  # last 1m close inside the bucket (10:04)
    assert out.loc[pd.Timestamp("2026-09-11 10:05")] == 60.0


@pytest.mark.unit
def test_aggregate_series_takes_last_good_minute_when_defects_were_dropped():
    # 10:02 was defective (dropped upstream); 10:03 is the last good read.
    index = pd.to_datetime(["2026-09-11 10:00", "2026-09-11 10:01", "2026-09-11 10:03"])
    series = pd.Series([100.0, -120.0, 45.0], index=index)
    out = schwab._aggregate_series_to_bucket(series, source_minutes=1, target_minutes=5)
    assert out.index.tolist() == [pd.Timestamp("2026-09-11 10:00")]
    assert out.iloc[0] == 45.0


@pytest.mark.unit
def test_aggregate_series_returns_the_series_unchanged_when_source_is_not_finer():
    index = pd.to_datetime(["2026-09-11 10:00", "2026-09-11 10:05"])
    series = pd.Series([5.0, -7.0], index=index)
    assert schwab._aggregate_series_to_bucket(series, source_minutes=5, target_minutes=5) is series
    assert schwab._aggregate_series_to_bucket(series, source_minutes=15, target_minutes=5) is series
    assert schwab._aggregate_series_to_bucket(series, source_minutes=0, target_minutes=5) is series


@pytest.mark.unit
def test_get_internals_frame_downsamples_a_1m_source_into_5m_buckets(monkeypatch):
    def fake_range(*, symbol, start_dt, end_dt, frequency_type, frequency):
        assert frequency == 1, "the source granularity must reach the HTTP fetchers"
        base = _VALUES[symbol]
        return [
            {
                "datetime": _epoch_ms(SESSION_START + pd.Timedelta(minutes=minute)),
                "open": 0.0,
                "high": 0.0,
                "low": 0.0,
                "close": base + minute,
                "volume": 0,
            }
            for minute in range(30)
        ]

    def fake_period(*, symbol, period_days, frequency_type, frequency):
        raise NoMarketDataError(symbol, symbol, "period path disabled in test")

    monkeypatch.setattr(schwab, "_fetch_price_history_range", fake_range)
    monkeypatch.setattr(schwab, "_fetch_price_history_period", fake_period)
    monkeypatch.setattr(schwab, "_backfill_internals_from_quotes", lambda frame, session_start=None: frame)
    monkeypatch.setattr(schwab, "_backfill_internals_from_streamer", lambda frame: frame)

    frame = schwab.get_internals_frame(SESSION_START, AS_OF, "5m", source_interval="1m")

    # 30 one-minute candles -> 6 five-minute buckets; each keeps its last minute
    # (minutes 0-29 -> buckets 09:30..09:55, last-minute values +4, +9, ..., +29).
    assert len(frame) == 6
    assert frame["add"].tolist() == [1200.0 + 5 * i + 4 for i in range(6)]
    assert frame["tick"].tolist() == [650.0 + 5 * i + 4 for i in range(6)]
    assert frame["vold"].tolist() == [4_500_000.0 + 5 * i + 4 for i in range(6)]
    assert frame.attrs["provenance"] == {"add": "candles", "tick": "candles", "vold": "candles"}


@pytest.mark.unit
def test_source_interval_defaults_to_the_target_granularity(recorded_calls):
    schwab.get_internals_frame(SESSION_START, AS_OF, "5m")
    assert {c["frequency"] for c in recorded_calls} == {5}  # default unchanged


@pytest.mark.unit
def test_get_internals_frame_rejects_an_unsupported_source_interval():
    with pytest.raises(ValueError, match="Unsupported internals source interval"):
        schwab.get_internals_frame(SESSION_START, AS_OF, "5m", source_interval="7m")
