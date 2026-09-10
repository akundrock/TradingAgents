# Design: $TICK / Market-Internals Data Fidelity for `mes check`

- **Date:** 2026-09-10
- **Status:** Approved design, ready for implementation planning
- **Scope:** Schwab internals dataflow (`tradingagents/dataflows/schwab.py`), MES snapshot ($TICK data-confidence warning), tests. No checklist/internals-math changes.

## 1. Problem

`tradingagents mes check` reported `$TICK +158 (persistent buy x25)` while the
user's thinkorswim `$TICK` panel (same 5m RTH series) showed a persistent
**sell**. The checklist logic itself is a faithful port of
`MES_TICK_Panel.tos` — the divergence is in the **data** the pipeline consumes.

Live diagnostic (2026-09-10, session data 13:50–15:54 ET, throwaway probe):

- **~60% of the day's 5m `$TICK` candles are defective**: `close=0` and
  `low=0` while only `high` carries a real reading (15 of the last 25 bars;
  `low` is 0 even on bars with a real close, so `low` carries no information
  at all in this feed).
- `_internal_candle_value` falls back `close → (high+low)/2 → high → low →
  close`. The midpoint branch is dead code for these symbols (low is always
  0), so every defective bar is silently assigned the bar **maximum** tick.
- That substitution is **positively biased**: a bar's high can never read
  below zero, so a real close of −300 on a bar with high +4 was recorded as
  `+4`, and streaks/zero-crossings computed on such a series can never turn
  negative.
- The fabricated values are non-zero, so `_backfill_internals_from_quotes` /
  `_backfill_internals_from_streamer` — which only patch bars whose value is
  NaN/0 — **never consult the live quote**: the true reading is discarded.
- Live proof at 15:54 ET: streamer `$TICK = −183`, REST quote `$TICK = −113`,
  while the checklist's series ended at `+440` (fabricated 15:50 bar high).
  The user's TOS bar (15:55: O −119 / H 179 / L −505 / C 179) shows the real
  tick oscillates deeply negative intrabar — consistent with the bias.

Consequences: persistent-streak counting, dynamic threshold (mean |tick| —
measured ±301 vs ±126 on known-real closes), whipsaw detection, exhaustion and
burst gates all ran on ~60% fabricated maxima.

## 2. Goal

`mes check` must consume true internals readings: never fabricate a value from
a one-sided candle, recover the latest bar from live quotes/streamer, and
surface data-confidence when the session's tick history is sparse.

Non-goals:

- No change to checklist gate/scoring logic (`internals.py` logic is a faithful
  TOS port and stays untouched).
- No attempt to recover historical defective closes from other sources
  (impossible from the Schwab REST feed); unknown bars are dropped, not
  invented.
- No change to the synthetic $ADD/$VOLD component paths (their `close` values
  are real; the same no-fabrication rule simply applies to them uniformly).

## 3. Architecture

### 3.1 `tradingagents/dataflows/schwab.py` — `_internal_candle_value`

Trust order becomes: real `close` → two-sided bar midpoint (only when **both**
`high` and `low` are non-zero) → **unknown (`None`)**. The single-sided
`high`/`low` substitutions and the all-zero `close` passthrough are removed.
`_fetch_internal_series` already skips `None` values, so defective bars drop
out of the series and the snapshot's per-day ffill carries the last known
reading — honest carry-forward instead of a biased fabrication.

### 3.2 Live backfill (no code change — becomes effective)

`_backfill_internals_from_quotes` / `_backfill_internals_from_streamer` keep
their `NaN/0`-only guards on the last bar. After fabrication is removed, an
unreadable forming bar is `NaN`, so the existing quote-then-streamer backfill
now actually patches it with the true live reading (exactly what the TOS panel
shows). Valid candle closes are never overwritten by quotes: completed bars
keep bar-close parity with TOS.

### 3.3 `tradingagents/mes/snapshot.py` — data-confidence warning

New helper `_tick_coverage_warning(internals, session_start, as_of)` compares
the count of readable `$TICK` bars in `[session_start, as_of]` against the
expected 5m bar count; below 50% coverage (≥ 4 bars elapsed) it appends a
warning to `snapshot.warnings`, which already flows into
`ChecklistResult.warnings`.

## 4. Acceptance criteria

1. A `close=0 / low=0` candle yields no internals value (no HIGH substitution);
   existing two-sided-midpoint behavior is preserved.
2. The last bar's unreadable internals are backfilled from quotes/streamer as
   before (NaN path) — a session whose candles are all-defective still gets a
   live last-bar reading instead of a fabricated one.
3. `build_snapshot` appends a `$TICK data sparse` warning when session tick
   coverage drops below 50% of expected bars.
4. Full test suite passes; `test_internal_candle_value_*` tests updated to the
   new contract.
