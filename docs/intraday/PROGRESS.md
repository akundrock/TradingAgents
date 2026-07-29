# Intraday Watchlist System — Progress Tracker

Track implementation status across coding sessions. Update checkboxes and notes as work is completed. See [PLAN.md](PLAN.md) for full implementation details on each step.

---

## Phase 1 — Data Foundation

**Objective**: Extend Schwab and stockstats data layers for multi-timeframe candle fetching and indicator computation.

- [x] **Step 1** — Add `get_candles_multi_timeframe()` to `tradingagents/dataflows/schwab.py`
  - [x] Parallel fetch of 5-min and 30-min candles via `ThreadPoolExecutor`
  - [x] Returns `dict[int, pd.DataFrame]` keyed by interval
  - [x] Raises `NoMarketDataError` if either timeframe is empty
  - [x] Session: 2026-07-27

- [x] **Step 2** — Add `compute_mtf_indicators()` to `tradingagents/dataflows/stockstats_utils.py`
  - [x] `stockstats.wrap()` on each timeframe DataFrame
  - [x] Indicators: EMA(10), SMA(20), RSI, MACD, ATR, Bollinger Bands, VWMA
  - [x] Add standalone `compute_intraday_vwap()` function
  - [x] Session: 2026-07-27

- [x] **Step 3** — Tests
  - [x] `tests/test_schwab_mtf_fetch.py`
  - [x] `tests/test_stockstats_mtf.py`
  - [x] Session: 2026-07-27

---

## Phase 2 — Scanner Core

**Objective**: Build `tradingagents/intraday/` package with session state, MTF validation, and the main watchlist scanner loop.

- [x] **Step 4** — Create `tradingagents/intraday/__init__.py`
  - [x] Package scaffold with top-level exports
  - [x] Session: 2026-07-27

- [x] **Step 5** — Create `tradingagents/intraday/session.py`
  - [x] `DailyBiasReport` dataclass
  - [x] `IntradaySignal` dataclass
  - [x] `TradingSession` dataclass
  - [x] Session: 2026-07-27

- [x] **Step 6** — Create `tradingagents/intraday/mtf_validator.py`
  - [x] `MTFValidationResult` dataclass
  - [x] `MultiTimeframeValidator.evaluate()` method
  - [x] Trend direction heuristic (EMA/SMA/close relationship)
  - [x] Session: 2026-07-27

- [x] **Step 7** — Create `tradingagents/intraday/scanner.py`
  - [x] `WatchlistScanner` class
  - [x] `run()` entry point
  - [x] `_run_premarket_setup()` (concurrent daily bias per symbol)
  - [x] `_session_loop()` with APScheduler bar-close scheduling
  - [x] `_on_bar_close()` with 15-second propagation delay
  - [x] `_evaluate_symbol()` pipeline orchestration
  - [x] `_emit_signal()` (console + CSV)
  - [x] `_eod_summary()`
  - [x] Session: 2026-07-27

---

## Phase 3 — Strategy & Gating Layer

**Objective**: Define the pluggable strategy interface and three-gate validation. Can be built concurrently with Phase 2.

- [x] **Step 8** — Create `tradingagents/intraday/strategy.py`
  - [x] `StrategyResult` dataclass
  - [x] `IntradayStrategy` Protocol
  - [x] Session: 2026-07-27

- [x] **Step 9** — Create strategy implementations
  - [x] `tradingagents/intraday/strategies/__init__.py` with `STRATEGY_REGISTRY` + `get_strategy()`
  - [x] `tradingagents/intraday/strategies/base_momentum.py` — `BaseMomentumStrategy`
    - [x] Long conditions: close > VWAP, EMA > SMA, 40 ≤ RSI ≤ 70, 30-min up, daily bullish
    - [x] Short conditions: inverse of long
    - [x] Subclassable via `_long_conditions()` / `_short_conditions()` overrides
  - [x] Session: 2026-07-27

- [x] **Step 10** — Create `tradingagents/intraday/gating.py`
  - [x] `GateResult` dataclass
  - [x] `GatingLayer.evaluate()` with three sequential gates
  - [x] Gate 1: Magpie score ≥ `intraday_min_magpie_score`
  - [x] Gate 2: 30-min trend aligns with `daily_bias.direction`
  - [x] Gate 3: `strategy.check_setup()` passed
  - [x] Short-circuit on Gate 1 failure
  - [x] Session: 2026-07-27

---

## Phase 4 — Intraday LLM Pipeline

**Objective**: Wire pre-computed context into a lightweight LLM pipeline that skips expensive analyst nodes. Depends on Phases 2 and 3.

- [x] **Step 11** — Add `propagate_daily_bias()` to `tradingagents/graph/trading_graph.py`
  - [x] Runs all 4 analysts + bull/bear researcher debate + Research Manager
  - [x] Stops before Magpie and Trader (conditional route on `stop_after_research` flag)
  - [x] Returns `DailyBiasReport` parsed from Research Manager structured output
  - [x] Session: 2026-07-27

- [x] **Step 12** — Create `tradingagents/graph/intraday_graph.py`
  - [x] `IntradayTradingGraph` class
  - [x] LangGraph: START → Trader → Risk Debate → PM → END
  - [x] `propagate_intraday()` method (injects daily bias + MTF context + Magpie signal)
  - [x] Reuses existing Trader, risk debaters, Portfolio Manager agents unchanged
  - [x] Session: 2026-07-27

- [x] **Step 13** — Extend state schema in `tradingagents/graph/propagation.py`
  - [x] Add `intraday_context` key with sub-fields: `scan_time`, `mtf_5min_snapshot`, `mtf_30min_snapshot`, `daily_bias`, `daily_bias_report`, `trends_aligned`, `vwap`, `atr`, `gate_results`
  - [x] Session: 2026-07-27

- [x] **Step 14** — Refactor `tradingagents/agents/trader/magpie.py`
  - [x] Added `compute_magpie_from_candles()` helper for scanner (pre-fetched DataFrame path)
  - [x] Scanner uses pre-fetched 5-min data directly; existing graph path unchanged
  - [x] Preserve existing behavior when called standalone (no regression)
  - [x] Session: 2026-07-27

---

## Phase 5 — Config & CLI

**Objective**: Expose all new functionality through configuration and the `tradingagents intraday` CLI subcommand.

- [x] **Step 15** — Add intraday config block to `tradingagents/default_config.py`
  - [x] All keys listed in PLAN.md Step 15
  - [x] Document each key with inline comment
  - [x] Session: 2026-07-27

- [x] **Step 16** — Add `intraday` subcommand to `cli/main.py`
  - [x] `--watchlist`, `--strategy`, `--dry-run`, `--no-premarket`, `--output-dir` options
  - [x] Rich live display: per-symbol status table
  - [x] Signal CSV output with all required columns
  - [x] Session: 2026-07-27

---

## Phase 6 — Tests

**Objective**: Verify each layer in isolation and verify integration.

- [x] **Step 17** — `tests/test_schwab_mtf_fetch.py`
  - [x] `test_fetch_returns_both_timeframes`
  - [x] `test_fetch_raises_on_empty_candles`
  - [x] `test_fetch_uses_parallel_requests`
  - [x] Session: 2026-07-27

- [x] **Step 18** — `tests/test_stockstats_mtf.py`
  - [x] `test_all_indicators_computed`
  - [x] `test_vwap_correct_formula`
  - [x] `test_handles_single_row`
  - [x] Session: 2026-07-27

- [x] **Step 19** — `tests/test_intraday_gating.py`
  - [x] `test_gate1_blocks_on_low_magpie_score`
  - [x] `test_gate2_blocks_on_misaligned_trend`
  - [x] `test_gate3_blocks_on_strategy_fail`
  - [x] `test_all_gates_pass`
  - [x] `test_gate_result_direction_from_magpie`
  - [x] Session: 2026-07-27

- [x] **Step 20** — `tests/test_intraday_strategy.py`
  - [x] `test_base_momentum_long_setup`
  - [x] `test_base_momentum_blocks_on_rsi_overbought`
  - [x] `test_base_momentum_blocks_on_bearish_daily_bias`
  - [x] `test_base_momentum_short_setup`
  - [x] Session: 2026-07-27

- [x] **Step 21** — `tests/test_intraday_scanner.py`
  - [x] `test_premarket_setup_called_per_symbol`
  - [x] `test_on_bar_close_skips_outside_session`
  - [x] `test_signal_emitted_when_gates_pass`
  - [x] `test_no_signal_when_gates_fail`
  - [x] Session: 2026-07-27

- [x] **Step 22** — `tests/test_intraday_graph.py`
  - [x] `test_propagate_intraday_returns_signal`
  - [x] `test_intraday_state_contains_context`
  - [x] `test_analyst_nodes_not_called`
  - [x] Session: 2026-07-27

---

## Open Questions

Track decisions that still need an answer before or during implementation.

- [x] **Bar-close delay**: Default 15 seconds in `intraday_bar_close_delay_seconds` config. Validate against live Schwab data during first live session.
- [x] **Market Internals**: Deferred — Magpie degrades gracefully without `$ADD/$TICK/$VOLD`.
- [x] **Re-entry suppression**: Implemented via `last_signal_by_symbol` + `intraday_signal_cooldown_bars` in `TradingSession`.
- [x] **Daily bias for neutral markets**: `BaseMomentumStrategy` blocks both long and short when `daily_bias.direction == "neutral"`.

---

## Notes & Session Log

Use this section to record observations, blockers, and decisions made during implementation.

| Date | Session Notes |
|------|---------------|
| 2026-07-27 | Initial plan created. All phases defined. Open questions identified. |
| 2026-07-27 | Full implementation complete (Phases 1–6). 619 tests passing. |
