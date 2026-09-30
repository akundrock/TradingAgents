# Intraday RRS Screener — `both`-Direction, Consistency & Data-Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `intraday_screener_direction=both` produce two independently ranked long/short lists through the whole pipeline (filter → sort → merge → logs → dashboard), and harden data-quality edge cases (NaN RRS, zero-price quotes, daily-load asymmetry, unknown sector, direction hints).

**Architecture:** All changes live in the existing intraday pipeline: `RrsFilter.evaluate` (per-candidate scoring), `UniverseScreener` (sort + watchlist merge + price gate), `ProTraderDashboardStrategy` (direction hint + sector diagnostics), `WatchlistScanner` (snapshot plumbing), and `cli/intraday_display.py` (rendering). No new modules. NaN is the sentinel for "no reading"; every consumer treats NaN as non-aligned and sort-last.

**Tech Stack:** Python 3.10, pytest, pandas, rich (dashboard). Tests run via `.venv/bin/python -m pytest`.

## Global Constraints

- Python commands MUST use the project venv: `.venv/bin/python` (bare `python`/`python3` on this machine resolve to Pythons without pytest).
- All new tests use `@pytest.mark.unit` (repo convention).
- `DEFAULT_CONFIG` in `tradingagents/default_config.py` is the single source of defaults.
- `compute_rrs` returns `float("nan")` on insufficient data — never reintroduce a `0.0` sentinel.
- `compute_power_index` keeps its existing `0.0`-sentinel behavior (do not change).
- `count_aligned_rrs` semantics are unchanged: NaN compares False against both `> 0` and `< 0`, so NaN never counts as aligned. No NaN special-casing there.
- Public `ScreenedSymbol`/`FilterResult`/`StrategyResult` dataclass field order MUST NOT change; only append new optional fields at the end.
- Screener side-list log lines start with `Screener longs:` / `Screener shorts:` (keep greppable).

## Current State (verified 2026-09-08; full suite 1174 passed / 2 skipped)

The working tree already contains partial implementations from a previous session (all tests passing). Task 1 verifies and commits that work.

Already done in the working tree:
- `intraday_screener_rrs_timeframes` = `[5, 15, 30, 60]` (default_config.py:268)
- `compute_rrs` returns NaN on insufficient bars / zero ATR (relative_strength.py:110-141); `has_sufficient_rrs_bars` exists
- `both` tie-break `(aligned_count, abs(score))` in `RrsFilter.evaluate` (rrs_filter.py:198-201)
- Round-robin interleave in `_merge_watchlist` (universe_screener.py:437-451)
- NaN-safe `_safe_score` in `_sort_screened_results`
- `preferred_direction` param on `check_setup` (pro_trader_dashboard.py:118) + scanner threading (scanner.py:692-706)
- Two-list screener logging (scanner.py:464-480) + `Dir` column in dashboard (intraday_display.py:399, 416-421, 430)
- New tests: `tests/test_rrs_filter.py` (6), `tests/test_intraday_scanner.py::test_screener_refresh_logs_two_lists_for_both_direction`, `tests/test_pro_trader_indicators.py` NaN tests (3), `tests/test_pro_trader_strategy.py` hint tests (2, weak)

---

### Task 1: Verify and commit the already-implemented work

**Files:**
- Verify only; no code changes beyond the `import math` fix already applied to `tradingagents/intraday/universe_screener.py` (missing import crashed `_safe_score` at runtime).

**Interfaces:**
- Consumes: nothing.
- Produces: clean commit of the current working tree; baseline for Tasks 2–7.

- [ ] **Step 1: Run the targeted test files**

Run: `.venv/bin/python -m pytest tests/test_rrs_filter.py tests/test_universe_screener.py tests/test_intraday_scanner.py tests/test_pro_trader_strategy.py tests/test_pro_trader_indicators.py -q`
Expected: all PASS (76 tests as of plan time)

- [ ] **Step 2: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q --ignore=tests/test_memory_log.py`
Expected: all PASS, 2 skipped (`langchain_aws`, `DEEPSEEK_API_KEY`). Takes ~100s.

- [ ] **Step 3: Commit**

```bash
git add tradingagents/default_config.py \
  tradingagents/intraday/indicators/relative_strength.py \
  tradingagents/intraday/screener_filters/rrs_filter.py \
  tradingagents/intraday/universe_screener.py \
  tradingagents/intraday/strategies/pro_trader_dashboard.py \
  tradingagents/intraday/scanner.py \
  cli/intraday_display.py \
  tests/test_rrs_filter.py \
  tests/test_intraday_scanner.py \
  tests/test_pro_trader_indicators.py \
  tests/test_pro_trader_strategy.py
git commit -m "feat: both-direction screener lists, TF alignment, direction hint, NaN RRS"
```

---

### Task 2: Rank each side separately in `_sort_screened_results` when direction=both

`_sort_screened_results` (tradingagents/intraday/universe_screener.py:68) sorts both sides through one shared key, so in `rank_by=aligned` mode a short with 2 aligned TFs ranks below a long with 3, and the `_merge_watchlist` interleave consumes shorts out of rank order. Each side must be ranked within itself: longs best-first by `(aligned_count, rank_score)` desc, shorts best-first by `(aligned_count, -rank_score)` desc. Sides stay contiguous in the list (longs first, then shorts) so the round-robin interleave in `_merge_watchlist` (universe_screener.py:437-449) consumes each side's best-first.

**Files:**
- Modify: `tradingagents/intraday/universe_screener.py:68-92` (`_sort_screened_results`)
- Test: `tests/test_rrs_filter.py`

**Interfaces:**
- Consumes: `ScreenedSymbol` (fields `direction: str`, `rank_score: float`, `aligned_count: int`) from `tradingagents/intraday/screener_filters/base.py`.
- Produces: unchanged signature `_sort_screened_results(results: list[ScreenedSymbol], config: dict) -> None`. New ordering contract for `direction == "both"`: output list is `sorted_longs + sorted_shorts` where longs are ordered by `(aligned_count, rank_score)` desc and shorts by `(aligned_count, -rank_score)` desc. Callers unchanged: `_run_filter_pipeline` (universe_screener.py:362) and `_merge_watchlist` (universe_screener.py:433-453).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_rrs_filter.py` (module already imports `math`, `pd`, `pytest`, `patch` and has helpers `_make_ctx`, `_rrs_config`, `_screened`):

```python
@pytest.mark.unit
def test_sort_both_magnitude_keeps_sides_separate():
    """With rank_by=magnitude and direction=both, each side must be ranked
    within itself (longs desc by score, shorts desc by -score) — never one
    mixed abs(score) list."""
    from tradingagents.intraday.universe_screener import _sort_screened_results

    config = {"intraday_screener_direction": "both", "intraday_screener_rank_by": "magnitude"}
    results = [
        _screened("L1", "long", rank_score=1.0, aligned_count=2),
        _screened("S1", "short", rank_score=-3.0, aligned_count=2),
        _screened("L2", "long", rank_score=2.0, aligned_count=3),
        _screened("S2", "short", rank_score=-1.0, aligned_count=1),
    ]
    _sort_screened_results(results, config)

    long_symbols = [s.symbol for s in results if s.direction == "long"]
    short_symbols = [s.symbol for s in results if s.direction == "short"]
    # Longs desc by score: L2(2.0) before L1(1.0)
    assert long_symbols == ["L2", "L1"]
    # Shorts desc by -score: S1(-3.0, most negative = strongest short) first
    assert short_symbols == ["S1", "S2"]


@pytest.mark.unit
def test_sort_both_aligned_mode_ranks_within_sides():
    """rank_by=aligned + direction=both: longs ranked by (aligned, score) desc,
    shorts by (aligned, -score) desc, each within their own side."""
    from tradingagents.intraday.universe_screener import _sort_screened_results

    config = {"intraday_screener_direction": "both", "intraday_screener_rank_by": "aligned"}
    results = [
        _screened("L1", "long", rank_score=1.0, aligned_count=3),
        _screened("S1", "short", rank_score=-2.0, aligned_count=2),
        _screened("L2", "long", rank_score=0.5, aligned_count=3),
        _screened("S2", "short", rank_score=-1.0, aligned_count=2),
    ]
    _sort_screened_results(results, config)

    long_symbols = [s.symbol for s in results if s.direction == "long"]
    short_symbols = [s.symbol for s in results if s.direction == "short"]
    assert long_symbols == ["L1", "L2"]
    # Same aligned tier (2): stronger short (-2.0) first
    assert short_symbols == ["S1", "S2"]
```

Note: `_screened(symbol, direction, rank_score, aligned_count)` already exists in the test module (tests/test_rrs_filter.py:65-75) and returns a `ScreenedSymbol`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_rrs_filter.py -k "sort_both" -v`
Expected: FAIL — current `both` branch of `_sort_screened_results` sorts everything by one key (`abs(rank_score)` or `(aligned, abs(score))`), mixing sides; e.g. in the magnitude test S1(-3.0) lands before both longs, so the long-section assertion fails.

- [ ] **Step 3: Write the implementation**

Replace the ENTIRE `_sort_screened_results` function in `tradingagents/intraday/universe_screener.py` (currently lines 68-92) with:

```python
def _sort_screened_results(results: list[ScreenedSymbol], config: dict) -> None:
    direction = str(config.get("intraday_screener_direction", "long"))
    rank_by = str(config.get("intraday_screener_rank_by", "magnitude")).strip().lower()

    def _safe_score(value: float) -> float:
        # NaN rank scores (insufficient RRS data) must sort deterministically
        # last within their alignment tier, not poison tuple comparisons.
        return 0.0 if math.isnan(value) else value

    if direction == "both":
        # Rank each side independently: longs best-first (high score), shorts
        # best-first (most negative score). Longs keep their section before
        # shorts so the round-robin merge in _merge_watchlist consumes each
        # side's best-first. Non-long/short entries keep their relative order.
        longs = [s for s in results if s.direction == "long"]
        shorts = [s for s in results if s.direction == "short"]
        others = [s for s in results if s.direction not in ("long", "short")]
        if rank_by == "aligned":
            longs.sort(key=lambda s: (s.aligned_count, _safe_score(s.rank_score)), reverse=True)
            shorts.sort(key=lambda s: (s.aligned_count, -_safe_score(s.rank_score)), reverse=True)
        else:
            longs.sort(key=lambda s: (_safe_score(s.rank_score), s.aligned_count), reverse=True)
            shorts.sort(key=lambda s: (_safe_score(s.rank_score), s.aligned_count))
        results[:] = longs + shorts + others
        return

    if rank_by == "aligned":
        if direction == "short":
            results.sort(key=lambda s: (s.aligned_count, -_safe_score(s.rank_score)), reverse=True)
        else:
            results.sort(key=lambda s: (s.aligned_count, _safe_score(s.rank_score)), reverse=True)
        return

    if direction == "short":
        results.sort(key=lambda s: (_safe_score(s.rank_score), s.aligned_count))
    else:
        results.sort(key=lambda s: (_safe_score(s.rank_score), s.aligned_count), reverse=True)
```

The `others` bucket is defensive (the RRS filter only emits "long"/"short"); keep it so an unexpected direction value can't crash the sort.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_rrs_filter.py -v`
Expected: 8 PASS (6 existing + 2 new). Note the pre-existing `test_sort_screened_results_both_keeps_sides_separate` (tests/test_rrs_filter.py:161-183) also must pass — it asserts side-internal ordering in aligned mode, which this implementation satisfies.

- [ ] **Step 5: Run the screener + scanner suites**

Run: `.venv/bin/python -m pytest tests/test_universe_screener.py tests/test_intraday_scanner.py -q`
Expected: all PASS. The interleave in `_merge_watchlist` (universe_screener.py:437-448) consumes `screened` after `_sort_screened_results` ran at line 362, so side-contiguity keeps each side pre-ordered for the round-robin.

- [ ] **Step 6: Commit**

```bash
git add tradingagents/intraday/universe_screener.py tests/test_rrs_filter.py
git commit -m "feat: rank longs and shorts independently in both-direction screening"
```

---

### Task 3: Reject zero/missing-price quotes in the volume-candidate gate

`_filter_volume_candidates` (tradingagents/intraday/universe_screener.py:249-287) skips the min-price reject when `candidate.last_price == 0` (`candidate.last_price > 0 and candidate.last_price < min_price`), so a stale/missing quote flows through to the filter pipeline. A zero price is never a valid quote; reject it and count it in the summary.

**Files:**
- Modify: `tradingagents/intraday/universe_screener.py:249-287` (`_filter_volume_candidates`)
- Test: `tests/test_universe_screener.py`

**Interfaces:**
- Consumes: `ScreenerCandidate(symbol, last_price, volume, total_volume)` from `tradingagents.dataflows.schwab_streamer`.
- Produces: unchanged signature `(candidates: list[ScreenerCandidate], *, skip_sp500_filter: bool = False) -> tuple[list[ScreenerCandidate], str]`. New reject bucket `price_missing` appears in the summary string.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_universe_screener.py` (imports already present in that module: `pytest`, and `ScreenerCandidate`/`UniverseScreener` — check the header and only add imports that are missing):

```python
@pytest.mark.unit
def test_zero_price_quote_is_rejected():
    """A quote with last_price == 0 (missing quote) must be rejected, not
    silently passed through the min-price gate."""
    from tradingagents.dataflows.schwab_streamer import ScreenerCandidate
    from tradingagents.intraday.universe_screener import UniverseScreener

    config = {"intraday_screener_min_price": 10.0, "intraday_screener_require_sp500": False}
    screener = UniverseScreener(config)
    candidates = [
        ScreenerCandidate(symbol="GOOD", last_price=50.0, total_volume=1000),
        ScreenerCandidate(symbol="ZERO", last_price=0.0, total_volume=1000),
        ScreenerCandidate(symbol="CHEAP", last_price=5.0, total_volume=1000),
    ]
    filtered, summary = screener._filter_volume_candidates(candidates)

    assert [c.symbol for c in filtered] == ["GOOD"]
    assert "price_missing=1" in summary
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_universe_screener.py -k zero_price -v`
Expected: FAIL — `ZERO` currently passes the gate, so `filtered` contains both `GOOD` and `ZERO` and the list-equality assertion fails.

- [ ] **Step 3: Write the implementation**

In `_filter_volume_candidates` (tradingagents/intraday/universe_screener.py:249-287), three edits:

Edit 1 — add the missing-price counter (after `reject_price = 0`):

```python
        reject_price = 0
        reject_price_missing = 0
        reject_sp500 = 0
```

Edit 2 — replace the price check block:

```python
            if min_price > 0 and candidate.last_price > 0 and candidate.last_price < min_price:
                reject_price += 1
                if len(reject_samples) < 8:
                    reject_samples.append(
                        f"{symbol} price={candidate.last_price:.2f}<{min_price:.2f}"
                    )
                continue
            filtered.append(candidate)
```

with:

```python
            if min_price > 0 and candidate.last_price <= 0:
                reject_price_missing += 1
                if len(reject_samples) < 8:
                    reject_samples.append(f"{symbol} price_missing")
                continue
            if min_price > 0 and candidate.last_price < min_price:
                reject_price += 1
                if len(reject_samples) < 8:
                    reject_samples.append(
                        f"{symbol} price={candidate.last_price:.2f}<{min_price:.2f}"
                    )
                continue
            filtered.append(candidate)
```

Edit 3 — replace the summary block:

```python
        if reject_price == 0 and reject_sp500 == 0:
            return filtered, ""

        summary = f"rejected price<{min_price:.2f}={reject_price} not_sp500={reject_sp500}"
```

with:

```python
        if reject_price == 0 and reject_price_missing == 0 and reject_sp500 == 0:
            return filtered, ""

        summary = (
            f"rejected price<{min_price:.2f}={reject_price} "
            f"price_missing={reject_price_missing} not_sp500={reject_sp500}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_universe_screener.py -q`
Expected: all PASS (no existing test exercises `last_price=0`, so no collisions).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/intraday/universe_screener.py tests/test_universe_screener.py
git commit -m "fix: reject zero-price quotes in screener price gate"
```

---

### Task 4: Record `daily_rrs_available` metadata in RRS filter

When `intraday_screener_include_daily_rrs` is True, `RrsFilter.evaluate` silently drops the daily TF whenever the benchmark daily didn't load (prepare failure) or the symbol's daily load fails (rrs_filter.py:127-139). The pass result is then indistinguishable from a full reading and `min_rrs_aligned` becomes easier to hit (design issue #8). Record availability in metadata.

**Files:**
- Modify: `tradingagents/intraday/screener_filters/rrs_filter.py` — `evaluate` daily block (~line 127-139) and pass-path metadata dict (~line 183)
- Test: `tests/test_rrs_filter.py`

**Interfaces:**
- Consumes: `ctx.shared["rrs_daily_cache"]` dict (existing), module `logger`, `load_ohlcv` import in `rrs_filter.py`.
- Produces: pass-path `FilterResult.metadata["daily_rrs_available"]: bool` whenever `intraday_screener_include_daily_rrs` is True. `True` = daily RRS present in `rrs_by_tf`; `False` = daily requested but unavailable. Not present when daily not requested.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rrs_filter.py`:

```python
@pytest.mark.unit
def test_daily_requested_but_unavailable_records_metadata():
    """When include_daily_rrs is on but no daily data made it into the frames
    (benchmark daily failed in prepare), the pass result must carry
    daily_rrs_available=False instead of silently evaluating intraday-only."""
    config = {
        "intraday_screener_rrs_timeframes": [5, 30],
        "intraday_screener_min_rrs_aligned": 1,
        "intraday_screener_direction": "long",
        "intraday_screener_rank_rrs_timeframe": "5m",
        "intraday_screener_include_daily_rrs": True,
        "pro_trader_benchmark": "SPY",
    }
    rrs_by_tf = {"5m": 0.5, "30m": 0.4}
    ctx = _make_ctx("TEST", config, rrs_by_tf)

    with patch(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_rrs_multi_timeframe",
        return_value={"5m": 0.5, "30m": 0.4},
    ), patch(
        "tradingagents.intraday.screener_filters.rrs_filter.compute_relative_volume",
        return_value=1.5,
    ):
        result = RrsFilter().evaluate(ctx)

    assert result.passed is True
    assert result.metadata.get("daily_rrs_available") is False
```

In this test `ctx.shared` is empty and `ctx.bench_enriched` is `{}`, so `bench_daily is None` — the exact "daily requested but unavailable" path. The metadata flag must be present and False. (Verified during planning: current code produces metadata without the key, so this test fails as required.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_rrs_filter.py -k daily_requested_but_unavailable -v`
Expected: FAIL — `daily_rrs_available` key absent from metadata.

- [ ] **Step 3: Write the implementation**

In `RrsFilter.evaluate` (tradingagents/intraday/screener_filters/rrs_filter.py), after the daily block (after line 139) and before `rrs_by_tf = compute_rrs_multi_timeframe(...)`, compute:

```python
        daily_available = "daily" in sym_frames and "daily" in bench_frames
```

Then in the pass-path metadata dict inside the `for dir_choice in directions:` loop (currently `"filter"`, `"rrs_by_tf"`, `"aligned_count"`, `"relative_volume_5m"`, `"rank_rrs_timeframe"`), add one entry:

```python
                    "daily_rrs_available": daily_available,
```

The fail-path metadata (the `factors_missing=[f"rs_aligned_{best_aligned}"]` result near line 218) needs the same flag so rejects also carry it: add `"daily_rrs_available": daily_available` to its metadata dict too.

Notes:
- The flag is informational — alignment logic is unchanged. `min_rrs_aligned` still counts only TFs present in `rrs_by_tf`; NaN daily values (NaN compares False in `count_aligned_rrs`) never inflate alignment.
- Do NOT log at INFO; the summary already surfaces `rs_aligned_N`. Add nothing to reject_reason (it already includes the aligned count).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_rrs_filter.py -q`
Expected: 7 PASS (6 existing + 1 new).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/intraday/screener_filters/rrs_filter.py tests/test_rrs_filter.py
git commit -m "feat: record daily_rrs_available metadata in RRS filter results"
```

---

### Task 5: Strengthen the direction-hint tests

`tests/test_pro_trader_strategy.py:401-429` (`test_pro_trader_direction_hint_prefers_short_side`) asserts `result.direction in ("short", "none")` — it passes even if the hint is ignored entirely (the in-test comment admits the scenario doesn't construct a passing short). Pin real behavior with two discriminating tests: (a) hint changes evaluation ORDER, (b) no-hint fallback returns `long` strictly.

**Files:**
- Modify: `tests/test_pro_trader_strategy.py:401-443` (replace both tests)
- Test only; no production changes.

**Interfaces:**
- Consumes: `ProTraderDashboardStrategy.check_setup(symbol, mtf, daily_bias, config=..., preferred_direction=...)`; fixtures `_pro_trader_mtf()` (bullish-leaning: OR 100–102, close 105, bullish ORB, up-trending intraday frames) and `_bias(direction)`; `_evaluate_long`/`_evaluate_short` methods.
- Produces: none (test-only).

- [ ] **Step 1: Rewrite `test_pro_trader_direction_hint_prefers_short_side` as an order test**

Replace the whole test (tests/test_pro_trader_strategy.py:401-429) with:

```python
@pytest.mark.unit
def test_pro_trader_hint_changes_evaluation_order():
    """preferred_direction='short' must evaluate _evaluate_short before
    _evaluate_long (observable via call order)."""
    strategy = ProTraderDashboardStrategy()
    call_order: list[str] = []

    def _track(side):
        def _inner(*args, **kwargs):
            call_order.append(side)
            return StrategyResult(
                passed=False,
                direction="none",
                reason=f"{side} blocked",
                factors_met=[],
                factors_missing=["x"],
            )

        return _inner

    with patch.object(strategy, "_evaluate_long", side_effect=_track("long")), \
         patch.object(strategy, "_evaluate_short", side_effect=_track("short")):
        strategy.check_setup(
            "NVDA", _pro_trader_mtf(), _bias("bearish"), config={}, preferred_direction="short"
        )
    assert call_order == ["short", "long"]
```

Check the file header for an existing `from unittest.mock import patch` import first: tests/test_pro_trader_strategy.py (lines 1-11) currently imports neither `patch` nor `StrategyResult` at module level — add BOTH to the imports at the top of the file:

```python
from unittest.mock import patch

from tradingagents.intraday.strategy import StrategyResult
```

(after the existing `from tradingagents.intraday.session import DailyBiasReport` line, before the strategies import — order per isort convention: `tradingagents.intraday.mtf_validator`, `tradingagents.intraday.session`, `tradingagents.intraday.strategy`, `tradingagents.intraday.strategies`, `tradingagents.intraday.strategies.pro_trader_dashboard`).

The patches replace the bound methods so `_build_context` is never reached (its result is only used as an argument the mocks discard), making the test fast and hermetic. Both sides are force-failed by the stub, so `check_setup` returns the "closer" fallback result with `factors_missing=["x"]` — that is fine; the assertion is on `call_order`.

Also replace `test_pro_trader_direction_hint_falls_back_without_hint` (tests/test_pro_trader_strategy.py:435-443) with a strict version:

```python
@pytest.mark.unit
def test_pro_trader_direction_hint_falls_back_without_hint():
    """Without a hint, long is evaluated first — a long-passing fixture must
    return direction='long'."""
    strategy = ProTraderDashboardStrategy()
    config = {
        "pro_trader_min_rs_timeframes": 1,
        "pro_trader_require_sector_alignment": False,
        "pro_trader_require_relative_volume": False,
        "pro_trader_require_daily_rrs": False,
    }
    result = strategy.check_setup("NVDA", _pro_trader_mtf(), _bias("bullish"), config=config)
    assert result.passed, f"expected long pass, got missing={result.factors_missing}"
    assert result.direction == "long"
```

The `_pro_trader_mtf()` fixture IS a passing long setup (verified: close=105 > or_high=102, supertrend up, RS frames trend up vs flat benchmark with min_rs=1). If some factor fails at execution time, print `result.factors_missing` in the assertion message (as shown) and fix the fixture — never loosen the assertion back to `in ("long", "none")`.

And add the diagnostics-ordering companion (pin the both-fail reporting path). This test uses mocks to give the two sides equal-length but different missing lists — the only scenario where the hint (not the "closer side" heuristic) decides what is reported. Verified during planning: with these stubs, hint=short reports `["short_only", ...]` and no hint reports `["long_only", ...]`:

```python
@pytest.mark.unit
def test_pro_trader_direction_hint_reports_preferred_side_diagnostics():
    """When both sides fail with equal-length missing lists, the hint decides
    which side's diagnostics are reported: hint=short reports the short side's
    factors_missing; no hint reports the long side's."""
    strategy = ProTraderDashboardStrategy()
    mtf = _pro_trader_mtf()

    def _long_cond(symbol, mtf, daily_bias, ctx, config):
        return ["f1"], ["long_only", "rs_timeframes_aligned"]

    def _short_cond(symbol, mtf, daily_bias, ctx, config):
        return ["f1"], ["short_only", "rs_timeframes_aligned"]

    with patch.object(strategy, "_build_context", return_value=None), \
         patch.object(strategy, "_long_conditions", side_effect=_long_cond), \
         patch.object(strategy, "_short_conditions", side_effect=_short_cond):
        hinted = strategy.check_setup(
            "NVDA", mtf, _bias("bearish"), config={}, preferred_direction="short"
        )
        no_hint = strategy.check_setup("NVDA", mtf, _bias("bearish"), config={})

    assert hinted.factors_missing == ["short_only", "rs_timeframes_aligned"]
    assert no_hint.factors_missing == ["long_only", "rs_timeframes_aligned"]
```

(Context on why the mock-based scenario: with the real fixture, `check_setup` reports whichever side has FEWER missing factors via `min(first, second, key=len)` — evaluation order only breaks ties. Stubbing both conditions methods gives equal-length, differently-named missing lists, making the hint's effect directly observable. Verified during planning: hinted → `["short_only", "rs_timeframes_aligned"]`, no-hint → `["long_only", "rs_timeframes_aligned"]`.)

- [ ] **Step 2: Run tests to verify they pass on current code (they pin behavior)**

Run: `.venv/bin/python -m pytest tests/test_pro_trader_strategy.py -k direction_hint -v`
Expected: PASS. Then verify the tests are discriminating by temporarily breaking the hint: comment out the `if preferred_direction == "short":` branch in `pro_trader_dashboard.py` (forcing long-first), re-run — `test_pro_trader_hint_changes_evaluation_order`'s `call_order == ["short", "long"]` and `test_pro_trader_direction_hint_reports_preferred_side_diagnostics`'s `short_only` assertion must FAIL. Restore the branch.

- [ ] **Step 3: Run the strategy tests**

Run: `.venv/bin/python -m pytest tests/test_pro_trader_strategy.py -q`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_pro_trader_strategy.py
git commit -m "test: pin preferred-direction ordering with discriminating assertions"
```

---

### Task 6: Screener-pass → strategy consistency guards

Two guard tests locking the design's Phase 2 acceptance: (1) the screener scores every TF the strategy needs, so a screener pass implies strategy `rs_timeframes_aligned` is evaluated on a superset of TFs; (2) the screener direction hint actually reaches `check_setup` as `preferred_direction`.

**Files:**
- Test: `tests/test_universe_screener.py` (add TF guard test)
- Test: `tests/test_intraday_scanner.py` (add hint-plumbing test)
- No production changes expected.

**Interfaces:**
- Consumes: `effective_requested_timeframes(config) -> tuple[list[int], bool]` (frame_enrichment.py:29-41; pro_trader forces `requested += [5,15,30,60]`, returns `(fetch_tfs, need_60m)`); `WatchlistScanner._evaluate_symbol(symbol, bar_time)`; `session.screener_snapshots: dict[str, dict[str, float | str]]`.
- Produces: regression guards only.

- [ ] **Step 1: Write the TF-set consistency test**

Append to `tests/test_universe_screener.py`:

```python
@pytest.mark.unit
def test_screener_tf_set_is_superset_of_strategy_effective_timeframes():
    """Screener RRS alignment must cover every TF the strategy checks, else a
    screener pass would not imply strategy rs_timeframes_aligned on the same
    data."""
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.intraday.frame_enrichment import effective_requested_timeframes

    fetch_tfs, need_60m = effective_requested_timeframes(DEFAULT_CONFIG)
    strategy_tfs = set(fetch_tfs) | ({60} if need_60m else set())
    screener_tfs = set(DEFAULT_CONFIG["intraday_screener_rrs_timeframes"])
    missing = strategy_tfs - screener_tfs
    assert not missing, (
        f"strategy needs TFs {sorted(missing)} that the screener doesn't score; "
        "a screener pass would not guarantee strategy rs_timeframes_aligned"
    )
```

(Verified against current defaults: strategy effective set is `{5, 30}` for pro_trader, screener is `[5, 15, 30, 60]` — passes. `intraday_mtf_timeframes` default is `[5, 30]` and pro_trader forces `(5, 15, 30, 60)` into `requested` before filtering to fetchable TFs, so the fetch list from DEFAULT_CONFIG is `[5, 30]` with `need_60m=True`.)

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/test_universe_screener.py -k tf_set_is_superset -v`
Expected: PASS. If it fails, config drift regressed — fix `default_config.py`, not the test.

- [ ] **Step 3: Write the direction-hint plumbing test**

Append to `tests/test_intraday_scanner.py` (module already imports `MagicMock`, `datetime`, `WatchlistScanner`, `_config` helper is named `_config()`, `_bias(symbol)` builder, `StrategyResult`):

```python
@pytest.mark.unit
def test_screener_snapshot_direction_flows_to_preferred_direction():
    """The screener direction hint must reach check_setup as
    preferred_direction for symbols present in screener_snapshots."""
    ta_graph = MagicMock()
    config = _config()
    config["intraday_strategy"] = "pro_trader_dashboard"
    config["intraday_screener_enabled"] = False  # no refresh; snapshot set directly
    scanner = WatchlistScanner(config, ta_graph, dry_run=True, skip_premarket=True)
    scanner.session.watchlist = ["NVDA"]
    scanner.session.daily_bias_cache["NVDA"] = _bias("NVDA")
    scanner.session.screener_snapshots["NVDA"] = {"direction": "short"}

    captured: dict = {}

    def _capture(symbol, mtf, daily_bias, preferred_direction=None, **kw):
        captured["preferred"] = preferred_direction
        return StrategyResult(
            passed=False, direction="none", reason="x",
            factors_met=[], factors_missing=[],
        )

    mock_strategy = MagicMock()
    mock_strategy.name = "pro_trader_dashboard"
    mock_strategy.check_setup.side_effect = _capture
    scanner.strategy = mock_strategy
    mtf = MagicMock()
    scanner.mtf_validator.evaluate = lambda *a, **k: mtf

    scanner._evaluate_symbol("NVDA", datetime(2026, 7, 27, 10, 0))
    assert captured["preferred"] == "short"
```

(Verified during planning against the real `WatchlistScanner`: `self.strategy` is assignable post-construction, `_evaluate_symbol` reads `screener_snapshots.get(symbol).get("direction")` and passes it as `preferred_direction=`, and the mock swap works. The test needs `StrategyResult` already imported in that test module — it is, at tests/test_intraday_scanner.py:10.)

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_universe_screener.py tests/test_intraday_scanner.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_universe_screener.py tests/test_intraday_scanner.py
git commit -m "test: screener-strategy TF consistency and direction-hint plumbing guards"
```

---

### Task 7: Surface unknown-sector state as `sector_unknown` diagnostic

Design item 10: symbols missing from `sector_tickers.json` silently skip the sector gate in lenient mode, so logs never explain why sector alignment was skipped. Surface `sector_unknown` in `factors_missing` when a setup FAILS and the symbol has no sector mapping — diagnostic-only, never blocks a pass.

Constraint discovered during planning: 6 existing tests unpack `_long_conditions` as a 2-tuple (`met, missing = ...`, e.g. tests/test_pro_trader_strategy.py:183), so the conditions methods MUST keep their `tuple[list[str], list[str]]` return signature. The diagnostic is therefore appended in `_evaluate_long`/`_evaluate_short` AFTER `passed` is computed.

**Files:**
- Modify: `tradingagents/intraday/strategies/pro_trader_dashboard.py` — `_evaluate_long` (~line 226-240) and `_evaluate_short` (~line 244-256)
- Test: `tests/test_pro_trader_strategy.py`

**Interfaces:**
- Consumes: `ctx.sector_etf: str | None` (None = symbol not in sector map), `_resolve_sector_mode(config)` (existing, pro_trader_dashboard.py:56-61).
- Produces: when `pro_trader_require_sector_alignment` is truthy (mode != "off") and `ctx.sector_etf is None` and the setup failed, the literal string `sector_unknown` appears in `StrategyResult.factors_missing`. It NEVER appears when the setup passes.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pro_trader_strategy.py`:

```python
@pytest.mark.unit
def test_unknown_sector_reports_sector_unknown_in_missing():
    """A symbol with no sector mapping must surface 'sector_unknown' in the
    missing-factors diagnostics when the setup fails, so logs explain why the
    sector check was skipped. It must never block a passing setup."""
    strategy = ProTraderDashboardStrategy()
    config = {
        "pro_trader_min_rs_timeframes": 5,  # unreachable: force both sides to fail
        "pro_trader_require_sector_alignment": True,
        "pro_trader_sector_alignment_mode": "lenient",
        "pro_trader_require_relative_volume": False,
        "pro_trader_require_daily_rrs": False,
    }
    mtf = _pro_trader_mtf()
    with patch(
        "tradingagents.intraday.strategies.pro_trader_dashboard.get_sector_etf",
        return_value=None,
    ):
        result = strategy.check_setup("NVDA", mtf, _bias("bullish"), config=config)
    assert not result.passed
    assert "sector_unknown" in result.factors_missing
```

(Requires `from unittest.mock import patch` at the top of tests/test_pro_trader_strategy.py — added in Task 5. Verified during planning: against current code this fails with `missing=['rs_timeframes_aligned']` and no `sector_unknown` — the sector check is silently skipped because `ctx.sector_etf` is None.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pro_trader_strategy.py -k unknown_sector -v`
Expected: FAIL — `sector_unknown` not in missing list.

- [ ] **Step 3: Write the implementation**

In `ProTraderDashboardStrategy._evaluate_long` (pro_trader_dashboard.py:225-241), replace the opening of the method:

```python
        met, missing = self._long_conditions(symbol, mtf, daily_bias, ctx, config)
        passed = len(missing) == 0
```

with:

```python
        met, missing = self._long_conditions(symbol, mtf, daily_bias, ctx, config)
        passed = len(missing) == 0
        if not passed and _resolve_sector_mode(config) != "off" and ctx.sector_etf is None:
            # Unknown sector (no ETF mapping): lenient mode skips the gate, but
            # say so in diagnostics. Appended after the pass check so it can
            # never block a setup on its own.
            missing.append("sector_unknown")
        return StrategyResult(
            passed=passed,
            direction="long" if passed else "none",
            reason="Long Pro Trader setup confirmed." if passed else f"Long setup blocked: {', '.join(missing)}",
            factors_met=met,
            factors_missing=missing,
        )
```

Identical change in `_evaluate_short` (~line 244-258): replace its `met, missing = self._short_conditions(symbol, mtf, daily_bias, ctx, config)` / `passed = len(missing) == 0` opening with the same two lines (`met, missing = ...` then `passed = len(missing) == 0` followed by the same `if not passed and _resolve_sector_mode(config) != "off" and ctx.sector_etf is None:` append) and reuse the existing `StrategyResult` return (changing only the reason string prefix from "Long" to "Short" — that line already exists, leave it as-is). The `ctx` parameter is in scope in both `_evaluate_*` methods, so `ctx.sector_etf is None` is directly checkable; `_resolve_sector_mode(config)` is recomputed here (same as `_long_conditions` does) rather than threaded through.

Edge behavior to confirm while executing: `test_pro_trader_lenient_sector_mode_skips_when_data_missing` (tests/test_pro_trader_strategy.py:220) uses NVDA which HAS a sector mapping, so `sector_etf` is not None there and `sector_unknown` is never appended — no collision.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_pro_trader_strategy.py tests/test_pro_trader_indicators.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/intraday/strategies/pro_trader_dashboard.py tests/test_pro_trader_strategy.py
git commit -m "feat: surface sector_unknown diagnostic in pro trader gate factors"
```

---

### Task 8: Full-suite verification and import sanity

**Files:**
- No changes. Verification only.

- [ ] **Step 1: Full test suite**

Run: `.venv/bin/python -m pytest tests/ -q --ignore=tests/test_memory_log.py`
Expected: all PASS (~1180 tests, ~100s), 2 pre-existing skips (`langchain_aws`, `DEEPSEEK_API_KEY`).

- [ ] **Step 2: Import sanity for every touched module**

Run: `.venv/bin/python -c "import tradingagents.intraday.universe_screener, tradingagents.intraday.screener_filters.rrs_filter, tradingagents.intraday.strategies.pro_trader_dashboard, tradingagents.intraday.scanner, cli.intraday_display; print('imports ok')"`
Expected: `imports ok` — this catches missing-import regressions like the `math` import bug found during planning.

- [ ] **Step 3: Manual smoke (market hours only — skip if closed)**

With `intraday_screener_direction=both` during market hours, confirm: two log lines (`Screener longs:` / `Screener shorts:`), the dashboard watchlist shows the `Dir` column, and screener-passed symbols reach strategy evaluation with `preferred_direction` set. Record results in the PR description.

---

## Self-Review

**1. Spec coverage** (all 10 design items + test plan):

| Design item | Task |
|---|---|
| 1. both tie-break by (aligned, abs(score)) | Task 1 (already implemented + tested, verified) |
| 2. per-side ranking in `_sort_screened_results` | Task 2 |
| 3. per-side watchlist merge (interleave) | Task 1 (done) + Task 2 (side-contiguous ordering it consumes) |
| 4. display both lists (logs + Dir column) | Task 1 (done, tested) |
| 5. TF alignment `[5,15,30,60]` | Task 1 (done) + Task 6 (regression test) |
| 6. direction hint consumption | Task 1 (done) + Task 6 (plumbing test) + Task 5 (order test) |
| 7. NaN semantics in compute_rrs | Task 1 (done + 3 tests) |
| 8. zero-price reject | Task 3 |
| 9. daily-RRS asymmetry metadata | Task 4 |
| 10. sector_unknown diagnostics | Task 7 |
| weak direction-hint tests strengthened | Task 5 |
| screener-pass→strategy consistency test | Task 6 |

**2. Placeholder scan:** Every code step contains final, verified code. Logic for Tasks 2, 4, 5, 6, 7 was executed against the real codebase during planning (red/green confirmed where noted). Task 4's note about the None-benchmark path and Task 7's probe results are verified facts, not TBDs.

**3. Type consistency:** `preferred_direction: str | None` matches scanner's `screener_snap.get("direction")` (str | None). `ScreenedSymbol.direction: str`; `FilterResult.direction: Literal["long","short","none"]` mapped to `str` by `filter_result_to_screened`. Task 4's `daily_rrs_available` key appears in both pass- and fail-path metadata; `combine_filter_results` merges via `dict.update` and only the rrs filter writes this key — no collisions. `_long_conditions`/`_short_conditions` keep their 2-tuple signature (6 existing call sites verified), so the sector diagnostic is appended in `_evaluate_*` only.

---

**Execution handoff:** Plan complete and saved to `docs/superpowers/plans/2026-09-08-rrs-screener-both-direction-hardening.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
