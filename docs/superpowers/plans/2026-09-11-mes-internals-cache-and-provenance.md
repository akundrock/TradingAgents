# MES Internals Option A: short response cache + synthetic provenance

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut Schwab price-history calls per poll ~10× with a short bucketed cache (radar is long-running), and stop printing unit-less synthetic `$VOLD` levels by surfacing data provenance in every render.

**Architecture:** Two independent changes in the internals data path. (1) A TTL + 5m-bucketed response cache inside the two low-level Schwab price-history fetchers, plus a 60s TTL cache for the streamer-internals backfill; because candles are 5m bars, every poll inside one 5m bucket can reuse one response. (2) `get_internals_frame` reports how each column was obtained (`candles` / `synthetic` / `none`), and the renders show a synthetic `$VOLD` as a baseline-relative delta with an explicit "(synthetic)" marker instead of a bare level.

**Tech Stack:** Python 3.11, pandas, requests, pytest (`unit`/`integration`/`smoke` markers), typer/rich CLI.

**Spec:** Session spikes of 2026-09-10/11 (throwaway scripts, findings recorded in NOTES.md):
- Synthetic `$VOLD` (`_fetch_synthetic_vold_series`) emits `Δ(UVOL−DVOL) since 09:30 ET × session-fitted scale` (`_VOLD_LARGE_BASELINE_CALIBRATION / baseline magnitude`, ≈1/2,042 on 2026-09-11) — **no stable unit** — while the small-baseline branch emits an absolute level. The branches are mutually inconsistent and not TOS-comparable (~98× gap on 2026-09-11). z-scores/slope/divergence are scale-invariant; only the printed absolute number is meaningless.
- Each `build_snapshot` poll re-fetches everything with zero caching: tick 2 price-history calls, add 2 (+4 synthetic components when $ADD fails), vold 2 (or +6 synthetic), 1 quotes call, 1 streamer session. `mes radar` polls every 60s, `mes go` every 15s; sustained fast polling trips Schwab 429s.
- Census 2026-09-11: $ADD candles intermittently return nothing mid-session (flaky); $TICK ~49% of candles defective (`close=0`, dropped); $UVOL/$DVOL clean at both 1m and 5m.

## Global Constraints

- Run tests only via `./.venv/bin/python -m pytest` **from the repo root** `/Users/akundrock/sandbox/TradingAgents` (plain `pytest` resolves the wrong interpreter).
- Work on branch `tick-internals-data-fidelity` (this plan extends its `internals_status` work).
- Env-var convention: `TRADINGAGENTS_*` uppercase (precedent: `TRADINGAGENTS_MES_ALLOW_MISSING_INTERNALS`).
- Never fabricate internals values: defective `close=0` candles are dropped, never substituted (`_internal_candle_value` docstring is the contract).
- Do not cache failures: a `NoMarketDataError` must never poison the cache; the next poll must retry.
- All new tests carry `@pytest.mark.unit`. No new third-party dependencies.

---

### Task 1: TTL-bucketed cache for price-history HTTP fetches

**Files:**
- Modify: `tradingagents/dataflows/schwab.py` (cache helpers after `_schwab_get_with_retry` ~line 297; wire into `_fetch_price_history_period` ~line 811 and `_fetch_price_history_range` ~line 312)
- Modify: `tests/conftest.py` (new autouse fixture)
- Test: `tests/test_schwab_internals.py`

**Interfaces:**
- Consumes: `_schwab_get_with_retry(url, params, *, rate_limit_message) -> requests.Response`, `_naive_market_datetime(dt) -> datetime`, `_normalize_symbol(symbol) -> str` (all exist in `schwab.py`).
- Produces (used by later tasks and the conftest fixture):
  - `clear_price_history_cache() -> None`
  - module attr `schwab._cache_now: Callable[[], float]` (tests monkeypatch it for TTL aging)
  - `_cache_ttl_seconds() -> float` from env `TRADINGAGENTS_SCHWAB_PRICE_HISTORY_TTL_SECONDS` (default `120.0`; `0` disables)
  - `_bucket_end(dt: datetime) -> datetime` (round up to the next 5m boundary; exact boundaries unchanged)
- Call-count contract: within one 5m bucket and the TTL window, repeated `_fetch_price_history_*` calls make **one** HTTP call; a new bucket refetches; errors are never cached.

- [ ] **Step 1: Write the failing tests**

In `tests/test_schwab_internals.py`: extend the first import line to `from datetime import datetime, timedelta`, then add this helper right after `_candles`:

```python
class _FakeResponse:
    """Minimal stand-in for a successful price-history HTTP response."""

    def __init__(self, payload: dict):
        self.status_code = 200
        self._payload = payload

    def json(self) -> dict:
        return self._payload
```

Append the following tests at the bottom of `tests/test_schwab_internals.py` (they appear here in full; do not abbreviate them when implementing):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_schwab_internals.py -k "cache or bucket" -v` (from repo root)
Expected: FAIL/ERROR — `clear_price_history_cache`, `_cache_now`, `_PRICE_HISTORY_CACHE` do not exist yet.

Continue the test block:

```python
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
```

Notes for the implementer:
- `_fetch_price_history_range` calls `_schwab_get_with_retry` with the **bucketed** end (see Step 3); the tests above exercise the real fetcher end-to-end, which is why they patch `_schwab_get_with_retry` rather than `_fetch_price_history_range`.
- `test_no_market_data_errors_are_not_cached` is what guarantees a flaky `$ADD` outage (census finding) cannot pin stale emptiness into the bucket.

### Task 1 (continued): implementation

- [ ] **Step 3: Implement the cache in `tradingagents/dataflows/schwab.py`**

Verify the module imports include `os` (add if missing) and `time` (already imported for the 429 backoff); add `from collections.abc import Callable`. Immediately after the `_schwab_get_with_retry` function (after line 296), insert:

```python
# --- price-history response cache -------------------------------------------
# radar/go re-fetch identical candles every 15-60s, but 5m candles only change
# at the bar boundary. Cache each successful response under a bucketed key so
# a long-running radar makes one price-history call per symbol per 5m bucket
# instead of one per poll. Quotes/streamer backfills stay live and keep the
# freshest bar honest. Failures are never cached. Disable with
# TRADINGAGENTS_SCHWAB_PRICE_HISTORY_TTL_SECONDS=0.


def _cache_ttl_seconds() -> float:
    raw = os.environ.get("TRADINGAGENTS_SCHWAB_PRICE_HISTORY_TTL_SECONDS", "120")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 120.0


_PRICE_HISTORY_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
_PRICE_HISTORY_CACHE_MAX_ENTRIES = 256
_cache_now: Callable[[], float] = time.monotonic


def _bucket_end(dt: datetime, *, minutes: int = 5) -> datetime:
    """Round ``dt`` up to the next bucket boundary (exact boundaries unchanged).

    Naive market wall-clock in, naive market wall-clock out (same convention as
    ``_naive_market_datetime``).
    """
    dt = _naive_market_datetime(dt)
    floored = dt.replace(minute=(dt.minute // minutes) * minutes, second=0, microsecond=0)
    if floored < dt:
        floored = floored + timedelta(minutes=minutes)
    return floored


def clear_price_history_cache() -> None:
    """Drop every cached price-history response (tests, replays, manual refresh)."""
    _PRICE_HISTORY_CACHE.clear()


def _price_history_cache_get(key: tuple) -> list[dict] | None:
    entry = _PRICE_HISTORY_CACHE.get(key)
    if entry is None:
        return None
    stored_at, candles = entry
    if _cache_now() - stored_at > _cache_ttl_seconds():
        _PRICE_HISTORY_CACHE.pop(key, None)
        return None
    return candles


def _price_history_cache_put(key: tuple, candles: list[dict]) -> None:
    if len(_PRICE_HISTORY_CACHE) >= _PRICE_HISTORY_CACHE_MAX_ENTRIES:
        oldest_key = min(_PRICE_HISTORY_CACHE, key=lambda k: _PRICE_HISTORY_CACHE[k][0])
        _PRICE_HISTORY_CACHE.pop(oldest_key, None)
    _PRICE_HISTORY_CACHE[key] = (_cache_now(), candles)
```

Add `from collections.abc import Callable` to the imports if missing.

Now wire the cache into both fetchers.

**In `_fetch_price_history_range` (currently lines 312-336):** after the existing guard (`end_dt <= start_dt` → `ValueError`) and the two `_naive_market_datetime` normalizations, insert the bucketed key + cache lookup, and change the request to use the bucketed end:

```python
    start_dt = _naive_market_datetime(start_dt)
    end_dt = _naive_market_datetime(end_dt)
    # Bucket the requested end to the next 5m boundary so every poll inside the
    # same bar shares one cache entry and one HTTP call. The response contains
    # only bars that existed at fetch time; downstream as_of filtering and the
    # quote/streamer backfills keep the live tail honest.
    bucketed_end = _bucket_end(end_dt)
    cache_key = (
        "range",
        _normalize_symbol(symbol),
        start_dt,
        bucketed_end,
        frequency_type,
        frequency,
    )
    cached = _price_history_cache_get(cache_key)
    if cached is not None:
        logger.debug("price-history cache hit (range): %s", cache_key)
        return cached
```

In `params`, use `"endDate": _market_epoch_ms(bucketed_end)` instead of `_market_epoch_ms(end_dt)`, and change the success exit:

```python
    payload = response.json()
    candles = payload.get("candles") or []
    if payload.get("empty") or not candles:
        raise NoMarketDataError(symbol, _normalize_symbol(symbol), "Schwab returned no candles")
    _price_history_cache_put(cache_key, candles)
    return candles
```

(The `end_dt <= start_dt` guard runs on the caller's raw datetimes BEFORE bucketing — bucketing only moves `end_dt` later, so the guard stays correct.)

**In `_fetch_price_history_period` (currently lines 811-839):** after building `params`, add:

```python
    from zoneinfo import ZoneInfo

    et_today = datetime.now(ZoneInfo("America/New_York")).date()
    cache_key = (
        "period",
        params["symbol"],
        params["period"],
        frequency_type,
        frequency,
        et_today,
    )
    cached = _price_history_cache_get(cache_key)
    if cached is not None:
        logger.debug("price-history cache hit (period): %s", cache_key)
        return cached
```

and before the final `return candles`:

```python
    _price_history_cache_put(cache_key, candles)
    return candles
```

(The ET date in the key prevents a midnight rollover from serving yesterday's "latest N days" response; the 120s TTL keeps it fresh within the day. `zoneinfo` is imported lazily elsewhere in this module — follow whatever pattern `_market_epoch_ms` uses at line 306.)

- [ ] **Step 3b: Add test isolation in `tests/conftest.py`**

Existing tests monkeypatch `_fetch_price_history_range`/`_fetch_price_history_period` themselves and bypass the cache, but tests that patch `_schwab_get_with_retry` (like the new ones) DO populate the real module cache. Append this autouse fixture next to `_isolate_config` so no cached state leaks between tests:

```python
@pytest.fixture(autouse=True)
def _clear_schwab_price_history_cache():
    """Keep the Schwab price-history TTL cache from leaking between tests."""
    from tradingagents.dataflows import schwab as schwab_module

    schwab_module.clear_price_history_cache()
    yield
    schwab_module.clear_price_history_cache()
```

- [ ] **Step 4: Run the cache tests plus the fetcher suites**

Run: `./.venv/bin/python -m pytest tests/test_schwab_internals.py tests/test_schwab_dataflow.py tests/test_schwab_mtf_fetch.py tests/test_mes_snapshot.py -v` (repo root)
Expected: ALL PASS — the six new cache/bucket tests and every existing fetcher/snapshot test.

If `test_get_internals_frame_requests_every_symbol_as_minute_candles` fails on the `end_dt` assertion: that test monkeypatches `_fetch_price_history_range` (the wrapper), so it never sees the bucketed request — if it fails, the bucketing leaked ABOVE the fetcher; check you modified `_fetch_price_history_range` itself, not `_fetch_internal_series`.

### Task 2: Streamer-internals TTL cache

The streamer backfill opens a fresh asyncio streamer session each time the last bar needs patching — an expensive call that radar currently repeats every poll. Cache its reading for 60s (shorter than one 5m bar, longer than the poll interval).

**Files:**
- Modify: `tradingagents/dataflows/schwab_streamer.py` (add the cached wrapper near `fetch_internals_quotes`)
- Modify: `tradingagents/dataflows/schwab.py:1058-1086` (`_backfill_internals_from_streamer` call site)
- Modify: `tests/conftest.py` (extend the autouse fixture)
- Test: `tests/test_schwab_streamer.py`

**Interfaces:**
- Consumes: `schwab_streamer.fetch_internals_quotes(timeout_seconds: float = 5.0) -> dict[str, float]` (unchanged).
- Produces: `schwab_streamer.fetch_internals_quotes_cached(*, timeout_seconds: float = 5.0) -> dict[str, float]`; `schwab_streamer.clear_internals_backfill_cache() -> None`; env `TRADINGAGENTS_SCHWAB_STREAMER_TTL_SECONDS` (default `60.0`, `0` disables).

- [ ] **Step 1: Write the failing test** — append to `tests/test_schwab_streamer.py`:

```python
@pytest.mark.unit
def test_fetch_internals_quotes_cached_reuses_readings_within_ttl(monkeypatch):
    import pandas as pd

    from tradingagents.dataflows import schwab, schwab_streamer

    schwab_streamer.clear_internals_backfill_cache()
    session_start = datetime(2026, 3, 30, 9, 30)
    as_of = datetime(2026, 3, 30, 10, 0)
    calls = {"n": 0}

    def fake_streamer(*, timeout_seconds):
        calls["n"] += 1
        return {"$TICK": 120.0, "$ADD": 900.0, "$VOLD": 400_000.0}

    monkeypatch.setattr(schwab_streamer, "fetch_internals_quotes", fake_streamer)
    frame = pd.DataFrame(
        {
            "Date": [pd.Timestamp(session_start), pd.Timestamp(as_of)],
            "add": [1200.0, float("nan")],
            "tick": [650.0, float("nan")],
            "vold": [4_500_000.0, 4_500_100.0],
        }
    ).set_index("Date")

    out1 = schwab._backfill_internals_from_streamer(frame)
    out2 = schwab._backfill_internals_from_streamer(frame)
    assert calls["n"] == 1  # second call inside the TTL reuses the reading
    assert out1["tick"].iloc[-1] == 120.0
    assert out2["tick"].iloc[-1] == 120.0




@pytest.mark.unit
def test_streamer_backfill_cache_expires_and_ignores_empty_readings(monkeypatch):
    from tradingagents.dataflows import schwab_streamer

    schwab_streamer.clear_internals_backfill_cache()
    calls = {"n": 0}

    def fake_streamer(*, timeout_seconds):
        calls["n"] += 1
        return {}  # streamer found nothing

    monkeypatch.setattr(schwab_streamer, "fetch_internals_quotes", fake_streamer)
    schwab_streamer.fetch_internals_quotes_cached(timeout_seconds=5.0)
    schwab_streamer.fetch_internals_quotes_cached(timeout_seconds=5.0)
    assert calls["n"] == 2  # empty readings are never cached; every poll retries
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/test_schwab_streamer.py -k "cached" -v`
Expected: FAIL/ERROR — `clear_internals_backfill_cache` / `fetch_internals_quotes_cached` do not exist.

- [ ] **Step 3: Implement in `tradingagents/dataflows/schwab_streamer.py`**

Ensure the module imports `os` and `time` (add either if missing). Directly below `fetch_internals_quotes`, add:

```python
_STREAMER_INTERNALS_TTL_SECONDS = 60.0
_streamer_internals_cache: tuple[float, dict[str, float]] | None = None


def _streamer_backfill_ttl_seconds() -> float:
    raw = os.environ.get("TRADINGAGENTS_SCHWAB_STREAMER_TTL_SECONDS", "60")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 60.0


def fetch_internals_quotes_cached(*, timeout_seconds: float = 5.0) -> dict[str, float]:
    """``fetch_internals_quotes`` with a short TTL; reuse the last reading.

    Long-running radar loops re-request internals every poll; a fresh-enough
    streamer reading beats re-running the full auth+login streaming handshake
    every 60s. Empty readings are never cached so the next poll retries.
    """
    global _streamer_internals_cache
    now = time.monotonic()
    if _streamer_internals_cache is not None:
        cached_at, cached_readings = _streamer_internals_cache
        if now - cached_at < _streamer_backfill_ttl_seconds():
            return cached_readings
    readings = fetch_internals_quotes(timeout_seconds=timeout_seconds)
    if readings:
        _streamer_internals_cache = (now, readings)
    return readings


def clear_internals_backfill_cache() -> None:
    global _streamer_internals_cache
    _streamer_internals_cache = None
```

If `os`/`time` are not yet imported in `schwab_streamer.py`, add them at the top. Then change the call site in `tradingagents/dataflows/schwab.py` `_backfill_internals_from_streamer` (line ~1071):

```python
    from .schwab_streamer import fetch_internals_quotes_cached

    readings = fetch_internals_quotes_cached(timeout_seconds=5.0)
```

Finally extend the conftest fixture from Task 1 Step 3b to also clear the streamer cache:

```python
@pytest.fixture(autouse=True)
def _clear_schwab_price_history_cache():
    """Keep the Schwab price-history/backfill TTL caches from leaking between tests."""
    from tradingagents.dataflows import schwab as schwab_module
    from tradingagents.dataflows import schwab_streamer as schwab_streamer_module

    schwab_module.clear_price_history_cache()
    schwab_streamer_module.clear_internals_backfill_cache()
    yield
    schwab_module.clear_price_history_cache()
    schwab_streamer_module.clear_internals_backfill_cache()
```

- [ ] **Step 4: Run tests**

Run: `./.venv/bin/python -m pytest tests/test_schwab_streamer.py tests/test_schwab_internals.py -v`
Expected: ALL PASS.

### Task 3: Provenance metadata on the internals frame

**Files:**
- Modify: `tradingagents/dataflows/schwab.py` (`_fetch_internal_key_series` ~990, `_backfill_internals_from_quotes` ~1089, `_backfill_internals_from_streamer` ~1058, `get_internals_frame` ~1167)
- Test: `tests/test_schwab_internals.py`

**Interfaces:**
- Consumes: `_fetch_internal_key_series`, `_fetch_synthetic_add_series`, `_fetch_synthetic_vold_series`, `_backfill_internals_from_quotes`, `_backfill_internals_from_streamer` (all existing).
- Produces (Tasks 4-5 depend on these exact names):
  - `frame.attrs["provenance"]: dict[str, str]` — one entry per internals key (`"add"`, `"tick"`, `"vold"`), value `"candles"` (real $ADD/$TICK/$VOLD candles), `"synthetic"` (computed from components), or `"none"` (no data).
  - `frame.attrs["backfilled"]: dict[str, str]` — key → `"quotes" | "streamer" | "synthetic_quote"` for last-bar patches only; absent key means nothing was patched.
  - `schwab.INTERNALS_PROVENANCE_NOTES: dict[str, str]` — static display hints: `{"candles": "", "synthetic": "synthetic (built from component symbols; not TOS-comparable)", "none": "no data"}`.

- [ ] **Step 1: Write the failing tests** — first extend `_VALUES` at the top of `tests/test_schwab_internals.py`:

```python
_VALUES = {
    "$ADD": 1200.0,
    "$TICK": 650.0,
    "$VOLD": 4_500_000.0,
    "$ADVN": 3000.0,
    "$DECN": 1800.0,
    "$UVOL": 2_000_000.0,
    "$DVOL": 1_000_000.0,
}
```

Then append:

```python
@pytest.mark.unit
def test_direct_candle_values_report_candle_provenance(recorded_calls):
    frame = schwab.get_internals_frame(SESSION_START, AS_OF, "5m")
    assert frame.attrs["provenance"] == {"add": "candles", "tick": "candles", "vold": "candles"}
    assert frame.attrs["backfilled"] == {}


@pytest.mark.unit
def test_synthetic_vold_reports_synthetic_provenance(monkeypatch):
    def fake_range(*, symbol, start_dt, end_dt, frequency_type, frequency):
        if symbol in ("$UVOL", "$DVOL"):
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

- [ ] **Step 2: Run to verify the tests fail**

Run: `./.venv/bin/python -m pytest tests/test_schwab_internals.py -k provenance -v`
Expected: FAIL — `KeyError: 'provenance'` (and the synthetic test fails on `KeyError: 'vold'` in the provenance dict).

- [ ] **Step 3: Implement provenance in `tradingagents/dataflows/schwab.py`**

Add the display-hint constant below `MARKET_INTERNAL_SYMBOLS` (line ~773):

```python
# How each internals column was obtained; renderers use this to avoid printing
# unit-less synthetic levels as if they were real feed readings.
INTERNALS_PROVENANCE_NOTES: dict[str, str] = {
    "candles": "",
    "synthetic": "synthetic (built from component symbols; not TOS-comparable)",
    "none": "no data",
}
```

In `_fetch_internal_key_series` (line ~990), tag each return:

```python
def _fetch_internal_key_series(
    key: str,
    session_start: datetime,
    end_dt: datetime,
    frequency: int,
) -> pd.Series:
    symbol = MARKET_INTERNAL_SYMBOLS[key]
    try:
        series = _fetch_internal_series(symbol, session_start, end_dt, frequency)
        series.attrs["provenance"] = "candles"
        return series
    except Exception as direct_exc:
        if key == "add":
            try:
                synthetic = _fetch_synthetic_add_series(session_start, end_dt, frequency)
            except Exception:
                raise direct_exc
            synthetic.attrs["provenance"] = "synthetic"
            return synthetic
        if key == "vold":
            try:
                synthetic = _fetch_synthetic_vold_series(session_start, end_dt, frequency)
            except Exception:
                raise direct_exc
            synthetic.attrs["provenance"] = "synthetic"
            return synthetic
        raise
```

In `get_internals_frame`, after `frame = pd.DataFrame(series_by_key)` (the column-fill lines follow), add provenance collection; then after the two backfill calls, re-attach attrs to the returned frame:

```python
    provenance = {key: "none" for key in MARKET_INTERNAL_SYMBOLS}
    for key, series in series_by_key.items():
        provenance[key] = str(series.attrs.get("provenance", "candles"))
```

and at the end, replacing `frame.index.name = "Date"; return frame.reset_index()`:

```python
    frame.index.name = "Date"
    final = frame.reset_index()
    final.attrs["provenance"] = provenance
    final.attrs["backfilled"] = dict(frame.attrs.get("backfilled", {}))
    return final
```

(`.attrs` propagation through `reset_index` is not guaranteed by pandas, so the frame's own attrs are copied explicitly.)

In `_backfill_internals_from_quotes`, record every patch — right after each value write inside the loop:

```python
        if quote is not None and quote.last_price != 0:
            frame.at[last_idx, key] = float(quote.last_price)
            frame.attrs.setdefault("backfilled", {})[key] = "quotes"
            continue
        if session_start is None or key not in {"add", "vold"}:
            continue
        synthetic = _synthetic_internal_quote(key, session_start)
        if synthetic is not None:
            frame.at[last_idx, key] = float(synthetic)
            frame.attrs.setdefault("backfilled", {})[key] = "synthetic_quote"
```

In `_backfill_internals_from_streamer`, inside the symbol loop after `frame.at[last_idx, key] = float(value)`:

```python
        frame.attrs.setdefault("backfilled", {})[key] = "streamer"
```

- [ ] **Step 4: Run the internals suite**

Run: `./.venv/bin/python -m pytest tests/test_schwab_internals.py -v` (repo root)
Expected: ALL PASS — provenance is frame metadata (`.attrs`), so the pre-existing frame-shape/value tests are untouched, and the new provenance/backfill tests pass.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/schwab.py tests/test_schwab_internals.py
git commit -m "feat(mes): carry internals provenance and backfill source on the frame"
```

### Task 4: Snapshot carries provenance; renders show synthetic VOLD as a delta

**Files:**
- Modify: `tradingagents/mes/snapshot.py` (`MesSnapshot` fields ~line 235-245; `build_snapshot` ~line 469-505)
- Modify: `tradingagents/mes/render.py` (`render_market_context` internals rows at 109-111; new `_fmt_vold` near `_fmt` at 170; `format_internals_status` VOLD segment at 256-262)
- Test: `tests/test_mes_snapshot.py`, `tests/test_mes_radar.py`

**Interfaces:**
- Consumes: `frame.attrs["provenance"]`, `frame.attrs["backfilled"]` from Task 3.
- Produces: `MesSnapshot.internals_provenance: dict[str, str] = field(default_factory=dict)`; `MesSnapshot.vold_is_synthetic -> bool`; new render helper `render._fmt_vold(snapshot) -> str`; new warning text `"$VOLD is synthetic (Δ$UVOL−$DVOL since open, session-fitted scale); its absolute level is not TOS-comparable — read the delta or z-score"` appended to `build_snapshot` warnings when the final `$VOLD` reading is synthetic.

- [ ] **Step 1: Write the failing tests**

In `tests/test_mes_snapshot.py` (uses the file's existing `_bar_frame`/`_internals_frame`/`load_mes_config` helpers; `AS_OF` is already module-level):

```python
@pytest.mark.unit
def test_build_snapshot_exposes_internals_provenance_and_flags_synthetic_vold():
    cfg = load_mes_config()
    frame = _internals_frame()
    frame.attrs["provenance"] = {"add": "candles", "tick": "candles", "vold": "synthetic"}
    frame.attrs["backfilled"] = {"vold": "synthetic_quote"}

    def fetch_internals(session_start, as_of, interval):
        return frame

    snapshot = build_snapshot(
        AS_OF,
        cfg,
        fetch_bars=lambda *a, **k: _bar_frame(),
        fetch_internals=fetch_internals,
    )

    assert snapshot.internals_provenance["vold"] == "synthetic"
    assert snapshot.internals_provenance["tick"] == "candles"
    assert snapshot.vold_is_synthetic is True
    assert any("$VOLD is synthetic" in w for w in snapshot.warnings)


@pytest.mark.unit
def test_direct_vold_provenance_adds_no_warning():
    cfg = load_mes_config()

    snapshot = build_snapshot(
        AS_OF,
        cfg,
        fetch_bars=lambda *a, **k: _bar_frame(),
        fetch_internals=lambda *a, **k: _internals_frame(),
    )

    assert snapshot.vold_is_synthetic is False
    assert snapshot.warnings == []
```

In `tests/test_mes_radar.py` (reuse `_make_snapshot`, line 96):

```python
@pytest.mark.unit
def test_internals_status_marks_synthetic_vold_as_a_delta():
    snap = _make_snapshot()
    snap.internals_provenance = {"add": "candles", "tick": "candles", "vold": "synthetic"}
    line = format_internals_status(snap)
    assert line is not None
    assert "VOLD Δ+1,000 (synthetic)" in line


@pytest.mark.unit
def test_internals_status_keeps_plain_vold_for_direct_candle_provenance():
    snap = _make_snapshot()
    snap.internals_provenance = {"add": "candles", "tick": "candles", "vold": "candles"}
    line = format_internals_status(snap)
    assert line is not None
    assert "VOLD +1000 slope" in line
    assert "synthetic" not in line


@pytest.mark.unit
def test_market_context_marks_synthetic_vold_value():
    snap = _make_snapshot()
    snap.internals_provenance = {"add": "candles", "tick": "candles", "vold": "synthetic"}
    body = render_market_context(snap)
    assert "Δ+1,000 (synthetic)" in body
```

- [ ] **Step 2: Run to verify the tests fail**

Run: `./.venv/bin/python -m pytest tests/test_mes_snapshot.py tests/test_mes_radar.py -k "provenance or synthetic_vold or flags_synthetic" -v`
Expected: FAIL — `MesSnapshot` has no `internals_provenance`/`vold_is_synthetic`.

- [ ] **Step 3: Plumb provenance into MesSnapshot (`tradingagents/mes/snapshot.py`)**

In `build_snapshot`, capture the attrs right after the internals fetch (the `else:` branch that currently does `internals = fetch_internals(session_start, as_of, "5m")`):

```python
    else:
        try:
            internals = fetch_internals(session_start, as_of, "5m")
            internals_provenance = {
                "provenance": dict(getattr(internals, "attrs", {}).get("provenance", {})),
                "backfilled": dict(getattr(internals, "attrs", {}).get("backfilled", {})),
            }
        except Exception as exc:
            warnings.append(f"market internals unavailable: {exc}")
            logger.warning("MES snapshot: internals unavailable: %s", exc)
```

with `internals_provenance: dict[str, dict[str, str]] = {}` initialized next to `warnings: list[str] = []` above the `if`. Then extend the `MesSnapshot` dataclass (after `overnight_mes`):

```python
    internals_provenance: dict[str, str] = field(default_factory=dict)
    """How each internals reading was obtained: ``candles`` / ``synthetic`` / ``none``."""
```

and pass it in the constructor call:

```python
        internals_provenance=internals_provenance.get("provenance", {}),
```

Add the derived flag next to the `vold` property (line ~275):

```python
    @property
    def vold_is_synthetic(self) -> bool:
        return self.internals_provenance.get("vold") == "synthetic"
```

Append the synthetic warning inside the existing `if snapshot.rth_started and ... and internals is not None:` block, after the `missing` check:

```python
        if snapshot.vold_is_synthetic and snapshot.vold is not None:
            warnings.append(
                "$VOLD is synthetic (Δ$UVOL−$DVOL since open, session-fitted scale); "
                "its absolute level is not TOS-comparable — read the delta or z-score"
            )
```

- [ ] **Step 3b: Update the renders (`tradingagents/mes/render.py`)**

Add a provenance-aware formatter next to `_fmt` (line 170):

```python
def _fmt_vold(snapshot: MesSnapshot) -> str:
    """Synthetic $VOLD is a baseline-relative delta, not a TOS-comparable level."""
    vold = snapshot.vold
    if vold is None:
        return "unavailable"
    if snapshot.internals_provenance.get("vold") == "synthetic":
        return f"Δ{vold:+,.0f} (synthetic)"
    return _fmt(vold)
```

In `render_market_context`, swap the VOLD row (line 111) to use it:

```python
            f"| $VOLD | {_fmt_vold(snapshot)} | {_vold_read(snapshot)} |",
```

In `format_internals_status` (lines 256-262), branch the VOLD segment:

```python
    if vold is None:
        vold_seg = "VOLD unavailable"
    elif snapshot.internals_provenance.get("vold") == "synthetic":
        vold_seg = f"VOLD Δ{vold:+,.0f} (synthetic)"
        slope = snapshot.vold_slope()
        if slope is not None:
            vold_seg += f" slope {slope:+.0f}"
    else:
        vold_seg = f"VOLD {vold:+.0f}"
        slope = snapshot.vold_slope()
        if slope is not None:
            vold_seg += f" slope {slope:+.0f}"
```

Add `render_market_context` to the render imports at the top of `tests/test_mes_radar.py` (it already imports `format_internals_status` from `.render`).

- [ ] **Step 4: Run the affected suites**

Run: `./.venv/bin/python -m pytest tests/test_mes_snapshot.py tests/test_mes_radar.py tests/test_schwab_internals.py -v` (repo root)
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/mes/snapshot.py tradingagents/mes/render.py tests/test_mes_snapshot.py tests/test_mes_radar.py
git commit -m "feat(mes): label synthetic VOLD as an uncalibrated delta in every render"
```

### Task 5: Full-suite gate, live smoke, docs

**Files:**
- Modify: `NOTES.md` (data-fidelity notes — find the internals section created during the spike)
- No new test file; this task is verification + docs.

- [ ] **Step 1: Run the complete unit suite**

Run: `./.venv/bin/python -m pytest -m unit -q` (repo root)
Expected: ALL PASS, no new failures vs. the pre-plan baseline.

- [ ] **Step 2: Live smoke during RTH** (requires Schwab auth; skip outside 09:30-16:00 ET)

```bash
cd /Users/akundrock/sandbox/TradingAgents
TRADINGAGENTS_LOG_LEVEL=DEBUG ./.venv/bin/tradingagents mes radar --no-watch
```

Verify:
- DEBUG log shows `price-history cache hit (range|period)` lines on a second invocation within the same 5m bucket.
- When Schwab withholds direct `$VOLD`, the internals row reads `VOLD Δ… (synthetic)` and a snapshot warning `$VOLD is synthetic …` appears.
- `internals_status` still prints plain `VOLD +…` when the direct feed is healthy.
- Count HTTP calls in the log: a second run in the same 5m bucket should show zero price-history GETs (quotes + optional streamer only).

- [ ] **Step 3: Update `NOTES.md`** — append a short section:

```markdown
### Internals cache + provenance (2026-09-11)
- Price-history responses are cached per 5m bucket with a 120s TTL
  (`TRADINGAGENTS_SCHWAB_PRICE_HISTORY_TTL_SECONDS`; 0 disables). Within a 5m
  bucket every poll reuses one response per (symbol, window); closed bars never
  change, and the live tail is corrected by the quotes/streamer backfills.
  Expected: radar drops from ~8-15 price-history calls/poll to 1 cold + 0 warm.
- Streamer-internals backfill readings are reused for 60s
  (`TRADINGAGENTS_SCHWAB_STREAMER_TTL_SECONDS`).
- `get_internals_frame` reports `.attrs["provenance"]`
  (`candles`/`synthetic`/`none`) and `.attrs["backfilled"]`; snapshot renders
  show synthetic $VOLD as `Δ… (synthetic)` and warn that its absolute level is
  not TOS-comparable — trust the delta/z-score, never the level.
```

- [ ] **Step 4: Commit**

```bash
git add NOTES.md
git commit -m "docs(mes): internals cache semantics and provenance notes"
```

---

## Expected outcome after this plan

- A warm `mes radar` poll makes **1 quotes REST call and zero price-history calls** (cold poll per 5m bucket: the usual ~8-15); streamer sessions drop from one per poll to at most one per 60s. Sustained rate on `go --watch 15` falls from ~2,400-4,000 calls/hr toward ~300-600, comfortably inside Schwab's ~120/min.
- Renders never show a unit-less synthetic `$VOLD` level again: synthetic readings print as `Δ… (synthetic)` with a snapshot warning; `$ADD`/`$TICK` provenance is visible via `internals_status` warnings.
- `$TICK` consistency improves **indirectly only**: fewer 429s and no cache-poisoned flaky fetches, but the `close=0` candle defect remains (that is Schwab's data, not ours) — the quotes/streamer backfill stays the live mask.

## Out of scope (deferred)

- 1m → 5m aggregation add-on: planned separately in
  `docs/superpowers/plans/2026-09-11-mes-internals-1m-aggregation.md` (builds on Tasks 1-3 of this plan).
- TOS-streamer parity; z-score-only display; integrating this branch into `main`.











