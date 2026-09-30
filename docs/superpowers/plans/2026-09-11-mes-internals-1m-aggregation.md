# MES Internals 1m → 5m aggregation add-on

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch internals candles at 1m granularity and aggregate them down to the 5m bar grid so more bars survive Schwab's candle defects, behind a config opt-in.

**Architecture:** One pure downsampling helper in `schwab.py`, one optional `source_interval` parameter threaded through `get_internals_frame` (fetch each internals key at 1m, then take the last reading inside each 5m bucket), and a `MesChecklistConfig.internals_source_interval` knob (`"5m"` default) that `build_snapshot` forwards. Single choke point: aggregation is applied inside `_fetch_internal_key_series`, so both the direct-candle path and the synthetic `$ADVN−$DECN` / `$UVOL−$DVOL` paths benefit.

**Tech Stack:** Python 3.11, pandas, pytest (`unit` markers).

**Spec:** Census of 2026-09-11 (throwaway scripts): at **1m** granularity the synthetic components (`$ADVN`/`$DECN`, `$UVOL`/`$DVOL`) were gapless where 5m bars flaked, and per-minute sampling recovers up to 5 of 6 readings per bar. `$TICK` is ~50% `close=0`-defective at **both** granularities, so aggregation helps it only by taking the last good minute inside a bucket — it cannot fix Schwab's defect. This add-on builds on Plan A (`docs/superpowers/plans/2026-09-11-mes-internals-cache-and-provenance.md`) Tasks 1-3; it does **not** change API-call counts (same period+range calls, different `frequency` param) — the Task 1 bucketed cache already absorbs that.

## Global Constraints

- Same as Plan 1: run tests via `./.venv/bin/python -m pytest` from `/Users/akundrock/sandbox/TradingAgents`; branch `tick-internals-data-fidelity`; `@pytest.mark.unit` everywhere; no new dependencies.
- Internals frames stay 5m-aligned: the returned frame index must always be 5m bucket starts (snapshot merges internals onto bars by `Date`), regardless of source interval.
- The provenance contract from Plan A Task 3 must survive: an aggregated series keeps the `provenance` attr of its source path (`candles` / `synthetic`).

---

### Task 1: 1m → 5m downsampling helper

**Files:**
- Modify: `tradingagents/dataflows/schwab.py` (helper right after `_merge_internal_candles`, ~line 842)
- Test: `tests/test_schwab_internals.py`

**Interfaces:**
- Produces: `_aggregate_series_to_bucket(series: pd.Series, *, source_minutes: int, target_minutes: int) -> pd.Series` — floors each timestamp to `target_minutes` buckets and keeps the **last** reading per bucket (internals candles are step readings, so the last minute inside a 5m bucket is that bucket's close-equivalent). Returns a series indexed by bucket-start timestamps, sorted. When `target_minutes <= source_minutes` or `source_minutes <= 0`, returns `series` unchanged.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_schwab_internals.py`:

```python
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
    assert out.loc[pd.Timestamp("2026-09-11 10:00")] == 20.0  # last 1m close inside the bucket
    assert out.loc[pd.Timestamp("2026-09-11 10:05")] == 30.0


@pytest.mark.unit
def test_aggregate_series_takes_last_good_minute_when_defects_were_dropped():
    # 10:02 and 10:03 were defective (dropped upstream); 10:03 is the last good read.
    index = pd.to_datetime(["2026-09-11 10:00", "2026-09-11 10:01", "2026-09-11 10:03"])
    series = pd.Series([100.0, -120.0, 45.0], index=index)
- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_schwab_internals.py -k aggregate -v` (repo root)
Expected: FAIL/ERROR — `_aggregate_series_to_bucket` does not exist.

- [ ] **Step 3: Implement the helper in `tradingagents/dataflows/schwab.py`** directly below `_merge_internal_candles`:

```python
def _aggregate_series_to_bucket(
    series: pd.Series, *, source_minutes: int, target_minutes: int
) -> pd.Series:
    """Downsample a finer internals series into target-frequency buckets.

    Internals candles are step readings, so the last reading inside a bucket is
    that bar's close-equivalent; defective candles are dropped upstream, so the
    bucket keeps its last good reading. Output is indexed by bucket-start time.
    """
    if target_minutes <= source_minutes or source_minutes <= 0:
        return series
    keys = pd.to_datetime(series.index).floor(f"{target_minutes}min")
    grouped = series.groupby(keys).last()
    grouped.index.name = series.index.name
    return grouped.sort_index()
```


- [ ] **Step 4: Run tests, then commit**

Run: `./.venv/bin/python -m pytest tests/test_schwab_internals.py -v` → ALL PASS.

### Task 2: Opt-in 1m source inside the internals fetch chain

**Files:**
- Modify: `tradingagents/dataflows/schwab.py` (`_fetch_internal_series` ~854, `_fetch_internal_key_series` ~990, `_fetch_internals_series_by_key` ~1122, `get_internals_frame` ~1167)
- Test: `tests/test_schwab_internals.py`

**Interfaces:**
- Consumes: `_aggregate_series_to_bucket` (Task 1).
- Produces: `get_internals_frame(session_start, as_of, interval, *, source_interval: str | None = None) -> pd.DataFrame` — when `source_interval` is set (e.g. `"1m"`), candles are fetched at that granularity and each key's series is downsampled into `interval` buckets (last reading per bucket wins). Frame shape/attrs contract unchanged (columns `Date/add/tick/vold`, `.attrs["provenance"]`). Internal chain: `_fetch_internals_series_by_key(session_start, as_of, frequency, source_frequency=...)`, `_fetch_internal_key_series(..., source_frequency=...)`, `_fetch_internal_series(..., source_frequency=None)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_schwab_internals.py`):

```python
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
```

- [ ] **Step 2: Run to verify the new tests fail** — `./.venv/bin/python -m pytest tests/test_schwab_internals.py -k source -v` → FAIL (`source_interval` unexpected kwarg).

### Task 3: Config knob + snapshot wiring (off by default)

**Files:**
- Modify: `tradingagents/mes/config.py` (`MesChecklistConfig`, line ~68)
- Modify: `tradingagents/mes/snapshot.py` (`build_snapshot` fetch_internals default, lines ~458-474)
- Test: `tests/test_mes_snapshot.py`

**Interfaces:**
- Consumes: `get_internals_frame(..., source_interval=...)` from Task 2.
- Produces: `MesChecklistConfig.internals_source_interval: str = "5m"` (loaded from the config key `internals_source_interval`); `build_snapshot`'s default internals fetch forwards it. Injected `fetch_internals` test doubles keep their existing 3-arg signature.

- [ ] **Step 1: Write the failing tests** — in `tests/test_mes_snapshot.py`:

```python
@pytest.mark.unit
def test_build_snapshot_forwards_the_configured_internals_source_interval(monkeypatch):
    cfg = load_mes_config({"internals_source_interval": "1m"})
    calls: list[dict] = []

    def fake_get_internals_frame(session_start, as_of, interval, *, source_interval=None):
        calls.append({"interval": interval, "source_interval": source_interval})
        return _internals_frame()

    import tradingagents.dataflows.schwab as schwab_module

    monkeypatch.setattr(schwab_module, "get_internals_frame", fake_get_internals_frame)
    build_snapshot(AS_OF, cfg, fetch_bars=lambda *a, **k: _bar_frame())
    assert calls and calls[0]["interval"] == "5m"
    assert calls[0]["source_interval"] == "1m"


@pytest.mark.unit
def test_build_snapshot_defaults_to_5m_source_when_not_configured(monkeypatch):
    cfg = load_mes_config()
    calls: list[dict] = []

    def fake_get_internals_frame(session_start, as_of, interval, *, source_interval=None):
        calls.append({"interval": interval, "source_interval": source_interval})
        return _internals_frame()

    import tradingagents.dataflows.schwab as schwab_module

    monkeypatch.setattr(schwab_module, "get_internals_frame", fake_get_internals_frame)
    build_snapshot(AS_OF, cfg, fetch_bars=lambda *a, **k: _bar_frame())
    assert calls[0]["source_interval"] == "5m"
```

- [ ] **Step 2: Run to verify failure** — `./.venv/bin/python -m pytest tests/test_mes_snapshot.py -k source_interval -v` → FAIL (unknown config key / `TypeError`).

- [ ] **Step 3: Implement the knob and wiring**

In `tradingagents/mes/config.py`, add to `MesChecklistConfig` next to the TOS internals-dashboard parity block (after `vold_zscore_lookback`, line ~104):

```python
    # Candle granularity the internals series is fetched at, before
    # aggregation into the 5m frame ("5m" = direct candles, the default).
    # 1m gives ~5 raw readings per bar, so a single defective candle no
    # longer blanks the bar. See NOTES.md data-fidelity census.
    internals_source_interval: str = "5m"
```

In `tradingagents/mes/snapshot.py` `build_snapshot` (line ~458), replace the bare default binding:

```python
    fetch_bars = fetch_bars or get_intraday_5m_candles
    fetch_internals = fetch_internals or get_internals_frame
```

with:

```python
    def _default_fetch_internals(
        session_start: datetime, as_of: datetime, interval: str
    ) -> pd.DataFrame:
        return get_internals_frame(
            session_start, as_of, interval, source_interval=cfg.internals_source_interval
        )

    fetch_bars = fetch_bars or get_intraday_5m_candles
    fetch_internals = fetch_internals or _default_fetch_internals
```

(`_default_fetch_internals` must be defined after `cfg` is available; the closure over `cfg` is the whole mechanism.)

- [ ] **Step 4: Run the snapshot suite, then commit**

Run: `./.venv/bin/python -m pytest tests/test_mes_snapshot.py tests/test_mes_radar.py -v` → ALL PASS.

```bash
git add tradingagents/mes/config.py tradingagents/mes/snapshot.py tests/test_mes_snapshot.py
git commit -m "feat(mes): internals_source_interval config knob (default 5m, 1m opt-in)"
```

### Task 4: Live smoke + docs

- [ ] Flip the knob on and smoke it live (during RTH, with Schwab auth). `load_mes_config` reads the config file it already uses for other keys — set `"internals_source_interval": "1m"` there, then:

```bash
cd /Users/akundrock/sandbox/TradingAgents
./.venv/bin/python -m tradingagents.cli.mes radar --no-watch
```

Verify, compared against the `"5m"` default on the same session:
- The internals row and status line still populate (add/tick/vold), now sourced from 1m reads.
- $TICK coverage warning (`TICK_COVERAGE_WARN_RATIO`) fires less often — the per-bucket last-good-minute rescues buckets whose 5m candle alone was defective.
- Watch the logs for Schwab 429s: a 1m source pulls ~5× more candles per request but the request COUNT is unchanged; if throttling appears, keep the default `"5m"` and note it.

- [ ] Update `NOTES.md` under the data-fidelity section:

```markdown
### Internals 1m-source aggregation (2026-09-11)
- `internals_source_interval: "1m"` (config) fetches internals at 1m and keeps
  each 5m bucket's last good reading; the frame stays 5m-aligned.
- Motivation (census): 1m candles carried no additional defects and rescue
  buckets whose 5m candle was defective; $TICK close=0 defects remain possible
  but a bucket only goes blank when ALL its 1m reads are defective.
- Provenance/backfill metadata from Plan A is unaffected.
```

- [ ] Commit:

```bash
git add NOTES.md
git commit -m "docs(mes): 1m internals-source census findings and knob"
```

## Expected outcome

- With `internals_source_interval: "1m"`: a defective 5m internals candle no longer blanks the bucket — up to 4 of 5 defective minutes are rescued by taking the bucket's last good 1m read. $TICK streaks stop going stale as often; $ADD/$VOLD get gapless coverage when the direct feed cooperates.
- z-scores/slope/streak semantics are unchanged (same 5m bars, same thresholds).
- Deliberately NOT solved: Schwab's close=0 candle defect (masked by quotes/streamer backfill, not fixed) and TOS-level $VOLD units (still synthetic/un-calibrated — labeled as such by Plan A).



- [ ] **Step 3: Implement the threading in `tradingagents/dataflows/schwab.py`**

Replace `_fetch_internal_key_series` (line ~990) with the source-aware version:

```python
def _fetch_internal_key_series(
    key: str,
    session_start: datetime,
    end_dt: datetime,
    frequency: int,
    source_frequency: int | None = None,
) -> pd.Series:
    symbol = MARKET_INTERNAL_SYMBOLS[key]
    fetch_frequency = source_frequency or frequency
    try:
        series = _fetch_internal_series(symbol, session_start, end_dt, fetch_frequency)
        series.attrs["provenance"] = "candles"
    except Exception as direct_exc:
        if key == "add":
            try:
                series = _fetch_synthetic_add_series(session_start, end_dt, fetch_frequency)
            except Exception:
                raise direct_exc
            series.attrs["provenance"] = "synthetic"
        elif key == "vold":
            try:
                series = _fetch_synthetic_vold_series(session_start, end_dt, fetch_frequency)
            except Exception:
                raise direct_exc
            series.attrs["provenance"] = "synthetic"
        else:
            raise
    provenance = series.attrs.get("provenance", "candles")
    if fetch_frequency != frequency:
        series = _aggregate_series_to_bucket(
            series, source_minutes=fetch_frequency, target_minutes=frequency
        )
        series.attrs["provenance"] = provenance
    return series
```

In `get_internals_frame`, validate and map the new kwarg next to the existing interval validation:

```python
    if interval not in _INTRADAY_MINUTE_FREQ_MAP_BY_LABEL:
        raise ValueError(f"Unsupported internals interval: {interval}")
    frequency = _INTRADAY_MINUTE_FREQ_MAP_BY_LABEL[interval]
    if source_interval is not None and source_interval not in _INTRADAY_MINUTE_FREQ_MAP_BY_LABEL:
        raise ValueError(f"Unsupported internals source interval: {source_interval}")
    source_frequency = (
        _INTRADAY_MINUTE_FREQ_MAP_BY_LABEL[source_interval] if source_interval else frequency
    )
```

change the signature to `def get_internals_frame(session_start: datetime, as_of: datetime, interval: str = "5m", *, source_interval: str | None = None) -> pd.DataFrame:` and pass it through the fetch:

```python
    series_by_key, failures, errors_by_key = _fetch_internals_series_by_key(
        session_start, as_of, frequency, source_frequency=source_frequency
    )
```

In `_fetch_internals_series_by_key(session_start, as_of, frequency, source_frequency=None)`, its `capture(key)` call becomes `_fetch_internal_key_series(key, session_start, as_of, frequency, source_frequency=source_frequency)`.

Notes for the implementer:
- The synthetic path benefits automatically: `_fetch_internal_key_series` fetches `$ADVN−$DECN` / `$UVOL−$DVOL` at the source granularity inside the code above; `_align_internal_component_pair` needs no change because the downsampling happens on the final key series, not the components.
- Provenance attrs are set on the series BEFORE downsampling; `_aggregate_series_to_bucket` runs `Series.groupby().last()`, which may drop `.attrs` — re-attach with `series.attrs["provenance"] = provenance` after the downsample (the code above already does this via the local variable).

- [ ] **Step 4: Run the internals suite, then commit**

Run: `./.venv/bin/python -m pytest tests/test_schwab_internals.py tests/test_mes_snapshot.py -v` (repo root) → ALL PASS.

```bash
git add tradingagents/dataflows/schwab.py tests/test_schwab_internals.py
git commit -m "feat(mes): opt-in 1m internals source downsampled to the frame interval"
```

---




