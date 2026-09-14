# $TICK Internals Data Fidelity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `mes check` consume true $TICK/$ADD/$VOLD readings — never a fabricated one-sided candle value — and surface data-confidence when the session's tick history is sparse.

**Architecture:** Three surgical changes: (1) `_internal_candle_value` in the Schwab dataflow stops substituting a bar's `high`/`low` for a missing `close` (returns `None` → the bar drops out of the series and carries the last known reading forward instead); (2) the existing last-bar quote/streamer backfill becomes effective for unreadable bars (it was structurally defeated by the fabricated non-zero values); (3) `build_snapshot` appends a `$TICK data sparse` warning when session tick coverage drops below 50% of expected 5m bars. No changes to checklist signal logic (`internals.py`) — it was verified to be a faithful TOS port; the defect was upstream data.

**Tech Stack:** Python 3.11+, pandas, pytest (repo: `/Users/akundrock/sandbox/TradingAgents`, venv at `.venv/bin/python`, tests in `tests/`, marker `@pytest.mark.unit`).

**Spec:** `docs/superpowers/specs/2026-09-10-tick-internals-data-fidelity-design.md`

## Global Constraints

- Root cause verified live 2026-09-10: ~60% of 5m `$TICK` candles have `close=0` **and** `low=0`; only `high` is populated. `low=0` is a no-data sentinel on every bar (even bars with a real close), so the `(high+low)/2` midpoint branch is dead code and every defective bar was silently assigned the bar **maximum** — a value that can never read negative.
- Live streamer at 15:54 ET read `$TICK = −183` while the checklist consumed a fabricated `+440` (15:50 bar high). User's TOS bar 15:55: O −119 / H 179 / L −505 / C 179.
- Do NOT add a synthetic fallback for historical defective bars: unknown ≠ maximum. Unknown bars drop out; the snapshot's per-day ffill carries the last known reading.
- Keep `_backfill_internals_from_quotes` / `_backfill_internals_from_streamer` NaN/0-only guards: a valid candle close is never overwritten by a quote.
- Every task: run the named test, then the module's full test file, before committing.
- Commit style: repo uses plain imperative messages (see `git log`); no doc changes in code tasks.

---

### Task 1: `_internal_candle_value` — stop fabricating one-sided values

**Files:**
- Modify: `tradingagents/dataflows/schwab.py:786-806` (`_internal_candle_value`)
- Test: `tests/test_schwab_internals.py:155-159` (`test_internal_candle_value_prefers_close_then_bar_midpoint`)

**Interfaces:**
- Consumes: nothing (pure function over a Schwab candle dict).
- Produces: `_internal_candle_value(candle: dict) -> float | None` — unchanged signature. New contract: non-zero `close` → that close; else both `high` and `low` non-zero → their midpoint; **else `None`** (previously fell back to one-sided `high`/`low`, or `0.0`).

- [x] **Step 1: Rewrite the test to the new contract**

In `tests/test_schwab_internals.py`, replace lines 155-159:

```python
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
```

- [x] **Step 2: Run the test to verify it fails**

Run: `cd /Users/akundrock/sandbox/TradingAgents && .venv/bin/python -m pytest tests/test_schwab_internals.py::test_internal_candle_value_prefers_close_then_two_sided_midpoint -v`
Expected: FAIL (`assert 81.0 is None` — the old HIGH substitution).

- [x] **Step 3: Write the minimal implementation**

Replace `_internal_candle_value` in `tradingagents/dataflows/schwab.py:786-806` (keep name/location; docstring records the verified defect):

```python
def _internal_candle_value(candle: dict) -> float | None:
    """Best-effort internals reading from a pricehistory candle.

    Only a real ``close`` — or the bar midpoint when both ``high`` and ``low``
    are populated — is trusted. Schwab's live internals candles frequently
    publish ``close=0`` (and ``low=0``) while only ``high`` carries a value;
    substituting that single-sided extreme for an oscillating reading like
    $TICK is positive-biased (the bar maximum can never read below zero) and
    fabricated persistent-buy streaks that disagree with the TOS panel.
    Unknown bars are dropped here; the latest bar is recovered from live
    quotes/streamer by the backfills in ``get_internals_frame``, and earlier
    gaps carry the last known reading via the snapshot's per-day ffill.
    """
    close = candle.get("close")
    high = candle.get("high")
    low = candle.get("low")

    if close is not None and close != 0:
        return float(close)
    if high is not None and low is not None and high != 0 and low != 0:
        return float((high + low) / 2.0)
    return None
```

- [x] **Step 4: Run the test to verify it passes**

Run: `cd /Users/akundrock/sandbox/TradingAgents && .venv/bin/python -m pytest tests/test_schwab_internals.py -v`
Expected: PASS for the whole module.

- [x] **Step 5: Commit**

```bash
cd /Users/akundrock/sandbox/TradingAgents && git add tradingagents/dataflows/schwab.py tests/test_schwab_internals.py
git commit -m "fix: stop fabricating internals values from one-sided candles"
```

### Task 2: End-to-end regression — defective feed yields NaN history plus live last-bar reading

**Files:**
- Test: `tests/test_schwab_internals.py` (append; no further production change expected — Task 1 makes `_fetch_internal_series` skip defective candles, and the existing NaN-guard in `_backfill_internals_from_quotes` then becomes effective)
- Read-only reference: `tradingagents/dataflows/schwab.py:1087-1117` (`_backfill_internals_from_quotes`), `tradingagents/dataflows/schwab_quotes.py:22-26` (`EquityQuote(symbol, last_price=...)`)

**Interfaces:**
- Consumes: `EquityQuote(symbol: str, last_price: float)`; `_normalize_symbol("$TICK") == "$TICK"` (verifiable via `schwab._normalize_symbol`).
- Produces: pinned regression contract for `get_internals_frame` on a defective feed.

- [x] **Step 1: Append the regression tests**

Extend the imports at the top of `tests/test_schwab_internals.py`:

```python
from tradingagents.dataflows import schwab_quotes
from tradingagents.dataflows.schwab_quotes import EquityQuote
```

Then append:

```python
@pytest.mark.unit
def test_get_internals_frame_drops_unreadable_candles_and_backfills_last_bar(
    monkeypatch,
):
    """A defective $TICK candle feed must never fabricate a value from `high`."""
    def fake_fetch(*, symbol, start_dt, end_dt, frequency_type, frequency):
        candles = _candles(symbol, count=3)
        if symbol == "$TICK":
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
            "$TICK": EquityQuote(symbol="$TICK", last_price=-183.0),
            "$ADD": EquityQuote(symbol="$ADD", last_price=1200.0),
            "$VOLD": EquityQuote(symbol="$VOLD", last_price=4_500_000.0),
        }

    monkeypatch.setattr(schwab_quotes, "get_quotes", fake_get_quotes)
    monkeypatch.setattr(schwab, "_backfill_internals_from_streamer", lambda frame: frame)
    frame = schwab.get_internals_frame(SESSION_START, AS_OF, "5m")

    # Defective history is unknown (NaN) — never the fabricated candle highs.
    assert frame["tick"].iloc[:-1].isna().all()
    # The last bar carries the true live reading, matching the TOS panel.
    assert frame["tick"].iloc[-1] == -183.0


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
            "Date": [pd.Timestamp(SESSION_START), pd.Timestamp(SESSION_START) + pd.Timedelta(minutes=5)],
            "add": [1200.0, 1210.0],
            "tick": [650.0, 655.0],
            "vold": [4_500_000.0, 4_510_000.0],
        }
    )
    patched = schwab._backfill_internals_from_quotes(frame, session_start=SESSION_START)
    assert patched["tick"].iloc[-1] == 655.0
    assert patched["add"].iloc[-1] == 1200.0
    assert patched["vold"].iloc[-1] == 4_500_000.0
```

- [x] **Step 2: Run the two new tests**

Run: `cd /Users/akundrock/sandbox/TradingAgents && .venv/bin/python -m pytest tests/test_schwab_internals.py::test_quote_backfill_never_overrides_a_valid_candle_close tests/test_schwab_internals.py::test_get_internals_frame_drops_unreadable_candles_and_backfills_last_bar_from_quotes -v`
Expected: PASS. (Pre-Task-1 the first would fail: the fabricated 339.0 passed the `!= 0` guard and blocked the live quote.)

- [x] **Step 3: Commit**

```bash
cd /Users/akundrock/sandbox/TradingAgents && git add tests/test_schwab_internals.py
git commit -m "test: defective internals candles drop out and live quotes fill the last bar"
```


### Task 3: `$TICK` data-confidence warning in `build_snapshot`

**Files:**
- Modify: `tradingagents/mes/snapshot.py` (new helper + wire into `build_snapshot`)
- Test: `tests/test_mes_snapshot.py`

**Interfaces:**
- Consumes: `internals` DataFrame with columns `Date, add, tick, vold` (as returned by `get_internals_frame` / the injected `fetch_internals`).
- Produces: module constants `TICK_COVERAGE_MIN_BARS = 4`, `TICK_COVERAGE_WARN_RATIO = 0.5`, module constant `_TICK_BAR_SPACING = pd.Timedelta(minutes=5)`, and helper `_tick_coverage_warning(internals: pd.DataFrame | None, session_start: datetime, as_of: datetime) -> str | None`. Warning text starts with `$TICK data sparse:` and flows `snapshot.warnings` → `ChecklistResult.warnings`.

- [x] **Step 1: Write the failing tests**

In `tests/test_mes_snapshot.py`, after `test_build_snapshot_records_a_warning_when_internals_fail`, add (reuses that module's `_internals_frame`, `_bar_frame`, `AS_OF`, `build_snapshot`, `load_mes_config` imports and `@pytest.mark.unit` convention):

```python
@pytest.mark.unit
def test_build_snapshot_warns_when_tick_coverage_is_sparse():
    cfg = load_mes_config()
    frame = _internals_frame()
    # 16 of 20 session bars have no readable $TICK (Schwab close=0 candles).
    frame.loc[frame.index[4:], "tick"] = pd.NA

    snapshot = build_snapshot(
        AS_OF,
        cfg,
        fetch_bars=lambda *a, **k: _bar_frame(),
        fetch_internals=lambda *a, **k: frame,
    )

    assert snapshot.tick is None
    assert any("$TICK data sparse" in w for w in snapshot.warnings)


@pytest.mark.unit
def test_tick_coverage_warning_tolerates_healthy_coverage():
    from tradingagents.mes.snapshot import _tick_coverage_warning

    start = datetime(2026, 3, 30, 9, 30)
    assert _tick_coverage_warning(_internals_frame(), start, AS_OF) is None
```

- [x] **Step 2: Run to verify failure**

Run: `cd /Users/akundrock/sandbox/TradingAgents && .venv/bin/python -m pytest tests/test_mes_snapshot.py::test_build_snapshot_warns_when_tick_coverage_is_sparse -v`
Expected: FAIL — no `$TICK data sparse` warning emitted yet.


- [x] **Step 3: Implement in `tradingagents/mes/snapshot.py`**

Below the `INTERNALS_FIRST_BAR = timedelta(minutes=5)` constant (snapshot.py:405), add:

```python
# Schwab frequently publishes internals candles with close=0; those bars are
# dropped (never fabricated), so measure how much of the session's $TICK
# history is actually readable and warn when streak/threshold math runs on a
# sparse, mostly-carried-forward series.
_TICK_BAR_SPACING = pd.Timedelta(minutes=5)
TICK_COVERAGE_MIN_BARS = 4
TICK_COVERAGE_WARN_RATIO = 0.5


def _tick_coverage_warning(
    internals: pd.DataFrame | None,
    session_start: datetime,
    as_of: datetime,
) -> str | None:
    """Warn when too few of the session's $TICK bars were readable from Schwab."""
    if internals is None or internals.empty or "tick" not in internals.columns:
        return None
    start = pd.Timestamp(session_start)
    end = pd.Timestamp(as_of)
    expected = int((end - start) / _TICK_BAR_SPACING) + 1
    if expected < TICK_COVERAGE_MIN_BARS:
        return None
    known = int(
        (
            (internals["Date"] >= start)
            & (internals["Date"] <= end)
            & internals["tick"].notna()
        ).sum()
    )
    if known >= expected * TICK_COVERAGE_WARN_RATIO:
        return None
    return (
        f"$TICK data sparse: only {known}/{expected} 5m bars readable from Schwab "
        "candles (defective close=0 bars are dropped, never fabricated). Tick "
        "streaks and thresholds may be stale — cross-check the TOS $TICK panel."
    )
```

- [x] **Step 4: Wire into `build_snapshot`**

In `build_snapshot` (snapshot.py:460-477), inside the existing `if snapshot.rth_started and as_of >= session_start + INTERNALS_FIRST_BAR and internals is not None:` branch, directly after the `missing`-warning append, add the same append pattern:

```python
        sparse = _tick_coverage_warning(internals, session_start, as_of)
        if sparse:
            warnings.append(sparse)
            snapshot.warnings = warnings
```

- [x] **Step 5: Run the snapshot test module**

Run: `cd /Users/akundrock/sandbox/TradingAgents && .venv/bin/python -m pytest tests/test_mes_snapshot.py -v`
Expected: PASS — including existing `test_build_snapshot_uses_injected_fetchers` (full-coverage frame ⇒ `snapshot.warnings == []` still holds).

- [x] **Step 6: Commit**

```bash
cd /Users/akundrock/sandbox/TradingAgents && git add tradingagents/mes/snapshot.py tests/test_mes_snapshot.py
git commit -m "feat: warn when session $TICK candle coverage is too sparse to trust"
```

### Task 4: Full-suite verification + probe re-run

**Files:** none (verification only)

**Interfaces:**
- Consumes: `/tmp/tick_parity_probe.py` (throwaway diagnostic from the investigation; stays in /tmp, never committed).

- [x] **Step 1: Run the full test suite**

Run: `cd /Users/akundrock/sandbox/TradingAgents && .venv/bin/python -m pytest tests/ -x -q`
Expected: all PASS, no new failures vs. the pre-change baseline.

- [ ] **Step 2: Live smoke (skip while market is closed)**

Run: `cd /Users/akundrock/sandbox/TradingAgents && .venv/bin/python /tmp/tick_parity_probe.py`
Expected: the `used` column shows `-` (NaN) for defective bars instead of `candle:HIGH`; `CHECK as run` and `TOS-parity` verdicts agree on direction; no defective bar carries a positive fabricated value. Requires live market data — skip when closed.

- [x] **Step 3: Commit the docs**

```bash
cd /Users/akundrock/sandbox/TradingAgents && git add docs/superpowers/specs/2026-09-10-tick-internals-data-fidelity-design.md docs/superpowers/plans/2026-09-10-tick-internals-data-fidelity.md
git commit -m "docs: $TICK internals data-fidelity design and implementation plan"
```

## Risk Notes

- **$ADD/$VOLD unaffected negatively:** their candle closes are real (synthetic $ADD = `$ADVN − $DECN` closes), and the no-fabrication rule applies uniformly; the two-sided midpoint branch stays for symbols where both sides populate.
- **Streak semantics after the fix:** with a mostly-defective feed, history carries the last known reading forward (ffill) and the live quote anchors the forming bar — matching TOS's "persistent" verdict more closely, never better than truth.
- **Scope guard:** `internals.py` checklist math is untouched; if verdicts still diverge from TOS after data fidelity is fixed, that is a separate (currently unevidenced) issue.

## Execution Notes (2026-09-10)

Executed on branch `tick-internals-data-fidelity` (worktree setup was replaced by a plain branch at the user's request). Commits: `edb3839` (Task 1), `2768289` (Task 2), `b548549` (Task 3). Full suite: 1286 passed / 2 skipped / 0 failures (baseline 1282 passed — the +4 are this plan's new tests). Two plan corrections made during execution, both documented here so future readers match reality:

1. **Task 2 guard test:** the fixture frame's last-row values are `1210.0`/`4_510_000.0` (add/vold), not the first-row values asserted in the original snippet; assertions corrected accordingly. Also, with only `$TICK` candles corrupted, `$ADD`/`$VOLD` succeed via their direct candle paths (`1202.0`/`4_500_002.0`), so the end-to-end test asserts those valid closes are *not* overwritten by the quotes.
2. **Task 3 sparse test:** `build_snapshot` intentionally forward-fills the last known tick onto unreadable bars, so `snapshot.tick` is `653.0` (carried), not `None`. The test pins that honest carry-forward plus the `$TICK data sparse` warning (Task 3's acceptance criterion is the warning itself).

Live smoke (Task 4 Step 2) deferred: market closed at execution time. Re-run `/tmp/tick_parity_probe.py` during a live session to confirm the `used` column shows NaN instead of `candle:HIGH` on defective bars and that `CHECK as run` matches `TOS-parity`.


