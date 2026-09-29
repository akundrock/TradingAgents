# Design: Surface MR Setups + Flag-Gated Momentum Alignment Mode

- **Date:** 2026-09-29
- **Status:** Approved design, ready for implementation planning
- **Scope:** `tradingagents/mes/render.py`, `journal.py`, `levels.py`, `config.py`, `checklist.py` (momentum only), `tradingagents/mes/backtest/ablations.py`, `cli/mes.py` (hint wiring), tests. No gatekeeper-strictness, weight, or SPY changes.

## 1. Problem

1. **MR setups are invisible.** The mean-reversion path (`enable_mean_reversion`,
   default off) computes `mr_*` fields on every checklist result, but
   `render_checklist`, `journal.append_check`/`summarize_checks`, and
   `suggest_trade_levels`' hint all drop them. Even when a trader opts in, an
   MR candidate never reaches the gatekeeper prompt, the journal, or `mes review` —
   it only exists as a Rich panel in the live CLI.
2. **Midday trend pullbacks grade unfairly.** The momentum item demands a
   same-bar SMA/VWAP **cross**. A pullback in an established trend (price holds
   VWAP, SMA hovers near VWAP without crossing) can never re-cross every bar,
   so midday continuation setups permanently lose the 2-point momentum
   confirmation even with a rising Laguerre.

## 2. Decisions

- **Actionability:** Option 1 (surface only) now, designed as a prerequisite for
  Option 3 (a second MR gatekeeper pass) later. Option 2 (widening the single
  gatekeeper call's inputs to bless MR) is rejected: it breaks the "LLM may
  narrow but never widen" invariant.
- **Momentum scope:** flag-gated `momentum_mode` — `'cross'` default
  (mes-tuner parity), `'alignment'` opt-in, validated by a named backtest
  ablation before anyone opts in.
- **Sub-decision 1:** the Laguerre trend gate is **unchanged** in alignment
  mode (`laguerre > 0.2` + rising for longs; mirror shorts).
- **Sub-decision 2:** the A3 levels-hint MR block appears only when MR
  **fires** (`mr_entry` is true), not merely when price is in-zone.
- MR fix and MR actionability are independent axes: alignment helps midday
  trend pullbacks; MR surfacing serves counter-trend fades the trend path can
  never bless.

## 3. Part A — Surface MR (zero policy change)

**A1 `render_checklist` MR section (default-visible when MR is on).** When
`result.mr_side` is set, render a `### Mean-reversion candidate (informational)`
section (fade side, plan stop/target, confirmations x/y) plus a fixed prompt
paragraph clarifying MR is additive and the trend ruling governs. Output is
byte-identical when MR is off (`mr_side is None`).

**A2 Journal.** `append_check` records every `mr_*` field (`mr_side`,
`mr_entry`, `mr_zone`, `mr_trigger`, `mr_confirmations`, `mr_required`,
`mr_score`, `mr_stop`, `mr_target`); `summarize_checks` gains an `MR` column
(`long ENTRY` / `long watch` / `—`) so `mes review` can grade MR candidates.

**A3 Levels hint.** `levels.py` gains `render_mr_levels_hint(result) -> str | None`,
rendered only when MR **fires** (`mr_entry` true and a stop/target exist),
labeled informational and trend-verdict-governing; the CLI check command
appends it to the gatekeeper's `trade_levels_hint`.

**A4 Runtime opt-in.** Operators enable MR with
`TRADINGAGENTS_MES_ENABLE_MEAN_REVERSION=true` (the env map is already generic).
Module default stays off; thresholds untouched.

## 4. Part B — Flag-gated momentum alignment

`momentum_mode: str = "cross"` config field (alias `MomentumMode`):

- `'cross'` (default): byte-identical to today (parity tests stay green).
- `'alignment'`: replaces the same-bar cross requirement with SMA/VWAP
  **agreement** (long: `sma > vwap`; short mirror) + the unchanged Laguerre
  trend gate. The Laguerre gate is unchanged — only the cross requirement
  relaxes; every bar that passes in `'cross'` mode still passes in
  `'alignment'` mode (superset, never a downgrade).
- The momentum item's name/observed strings label the active mode.
- Opt-in is validated via a named ablation overlay `momentum-alignment`
  (`{"momentum_mode": "alignment"}`) over the frozen 5-session fixture
  before anyone opts in.

## 5. Out of scope (explicit)

- MR actionability (Option 3 second pass — needs outcome evidence first)
- MR threshold relaxation
- Gatekeeper strictness/weights
- SPY 3-of-5 rule
- MR outcome backfill

## 6. Tests

~25–35 new assertions across: render on/off/parity, journal fields + MR column,
hint block (fires vs watch vs off), prompt text, momentum parity ('cross'
byte-identical), alignment semantics (agreement passes without cross, Laguerre
gate unchanged, superset property), config round-trip/alias/env, ablation
overlay registration. Suite baseline: 1520 passed, 2 skipped.
