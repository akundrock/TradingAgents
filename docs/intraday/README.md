# Intraday Watchlist Trading System

An intraday trading mode for TradingAgents that monitors a small watchlist of stocks on live Schwab data, validates setups across a multi-timeframe technical stack, and surfaces actionable signals with LLM-quality reasoning — without re-running the full analyst pipeline on every bar.

## Design Goals

- **Live data from Schwab** — 5-min candles fetched on each bar close via the existing Schwab price history API
- **Multi-timeframe validation** — setups must align across 5-min (entry), 30-min (trend), and Daily (bias) timeframes before triggering the LLM pipeline
- **Cost-efficient LLM usage** — daily bias is computed once per symbol pre-market (full analysts + researcher debate); intraday triggers only run Trader → Risk Debate → Portfolio Manager
- **Pluggable strategy** — setup conditions are defined by a swappable `IntradayStrategy` class, making it easy to refine over time without touching the pipeline
- **Signals only** — output is alerts and CSV logs; no automated order execution

---

## How It Fits Into the Existing Pipeline

```
┌──────────────────────────────────────────────────────────────────────┐
│  Existing Daily Pipeline (TradingAgentsGraph.propagate)              │
│  Analysts → Researcher Debate → Research Manager → Magpie →         │
│  Trader → Risk Debate → Portfolio Manager                            │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│  NEW: Intraday Pipeline                                              │
│                                                                      │
│  PRE-MARKET (once/symbol):                                           │
│    TradingAgentsGraph.propagate_daily_bias()                         │
│    → All 4 Analysts + Bull/Bear/Research Manager                     │
│    → DailyBiasReport { direction, key_levels, summary }             │
│    → Cached in TradingSession                                        │
│                                                                      │
│  INTRADAY (each 5-min bar close, per symbol in parallel):            │
│    Schwab MTF fetch (5-min + 30-min candles)                         │
│    → MultiTimeframeValidator → MTFValidationResult                   │
│    → IntradayStrategy.check_setup() (default: base_momentum)         │
│    → GatingLayer (2 sequential gates)                                │
│         Gate 1: Strategy pass (direction + factors)                  │
│         Gate 2: 30-min trend aligns with daily bias (optional)       │
│    → [gates pass] IntradayTradingGraph.propagate_intraday()          │
│         Injects strategy result + MTF context into state             │
│         Trader → Risk Debate → Portfolio Manager                     │
│    → IntradaySignal emitted (console + CSV log)                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## System Components

### New Package: `tradingagents/intraday/`

| Module | Class | Responsibility |
|--------|-------|----------------|
| `session.py` | `TradingSession` | Session-level state: watchlist, daily bias cache, signal log, session status |
| `mtf_validator.py` | `MultiTimeframeValidator` | Fetch and compute indicators across all three timeframes |
| `scanner.py` | `WatchlistScanner` | Schedule bar-close callbacks, orchestrate per-symbol evaluation, manage session lifecycle |
| `strategy.py` | `IntradayStrategy` (Protocol) | Pluggable strategy interface; `StrategyResult` dataclass |
| `strategies/base_momentum.py` | `BaseMomentumStrategy` | First concrete strategy (momentum + VWAP + RSI) |
| `gating.py` | `GatingLayer` | Two-gate validation (strategy + optional MTF alignment) |

### New File: `tradingagents/graph/intraday_graph.py`

`IntradayTradingGraph` — a slimmed LangGraph that starts at the Trader node, injecting pre-computed daily bias and strategy result into state. Reuses existing Trader, risk debater, and Portfolio Manager agents unchanged.

### Modified Existing Files

| File | Change |
|------|--------|
| `tradingagents/dataflows/schwab.py` | Add `get_candles_multi_timeframe()` |
| `tradingagents/dataflows/stockstats_utils.py` | Add `compute_mtf_indicators()` |
| `tradingagents/agents/trader/magpie.py` | Accept pre-fetched MTF data to skip internal Schwab fetch |
| `tradingagents/graph/trading_graph.py` | Add `propagate_daily_bias()` method |
| `tradingagents/graph/propagation.py` | Extend state schema with `intraday_context` key |
| `tradingagents/default_config.py` | Add intraday config section |
| `cli/main.py` | Add `intraday` subcommand |

---

## Multi-Timeframe Stack

| Timeframe | Role | Key Indicators |
|-----------|------|----------------|
| **Daily** | Bias (direction for the day) | 50/200 SMA, prior close, relative position |
| **30-min** | Trend (trade in this direction) | EMA(10), SMA(20), MACD, RSI, VWAP position |
| **5-min** | Entry (timing and pattern) | EMA(10), SMA(20), VWAP, Bollinger Bands, ATR |

A setup requires all three timeframes to point the same direction.

---

## Strategy-First Gating

The intraday loop evaluates a **pluggable strategy** on each bar (default: `base_momentum`). Magpie is not run in the scan loop; it remains available in the daily `TradingAgentsGraph` pipeline and can be added later as a strategy implementation.

**Gate 1 — Strategy:** `IntradayStrategy.check_setup()` must pass with a tradeable direction (`long` or `short`). Per-ticker results are logged at INFO with `factors_met` and `factors_missing`.

**Gate 2 — MTF alignment (optional):** When `intraday_require_daily_bias_alignment` is `True`, the 30-min trend must align with the pre-market daily bias for the strategy direction. Disable this when your strategy fully encodes bias/trend checks.

When both gates pass, the slim LLM chain (`Trader → Risk Debate → Portfolio Manager`) runs with the strategy result injected into `intraday_context`.

---

## Strategy Interface

Strategies are swappable without modifying the pipeline:

```python
class IntradayStrategy(Protocol):
    name: str
    def check_setup(
        self,
        symbol: str,
        mtf: MultiTimeframeData,
        daily_bias: DailyBiasReport,
    ) -> StrategyResult: ...
```

`BaseMomentumStrategy` (initial implementation):
- **Long setup (Gate 1)**: price > VWAP, EMA(10) > SMA(20), RSI between 40–70
- **Short setup (Gate 1)**: mirror conditions
- **Gate 2** (when `intraday_require_daily_bias_alignment` is true): long requires bullish daily bias + 30-min uptrend; short requires bearish bias + 30-min downtrend

New strategies can be added in `tradingagents/intraday/strategies/` and selected via `intraday_strategy` config key.

---

## Configuration

All intraday settings live under a new block in `tradingagents/default_config.py`:

```python
"intraday_enabled": False,
"watchlist": [],                          # e.g. ["NVDA", "AAPL", "SPY"]
"intraday_scan_interval_minutes": 5,
"intraday_premarket_setup_time": "09:00", # ET — when to run daily bias
"intraday_session_start": "09:30",        # ET
"intraday_session_end": "16:00",          # ET
"intraday_timezone": "America/New_York",
"intraday_mtf_timeframes": [5, 30],       # minutes (daily always included)
"intraday_strategy": "base_momentum",
"intraday_require_daily_bias_alignment": True,
"intraday_output_dir": "~/.tradingagents/intraday",
"intraday_restore_premarket_bias": True,
"intraday_max_concurrent_symbols": 5,
```

---

## CLI Usage

```bash
# Run intraday scanner for a watchlist (positional symbols)
tradingagents intraday NVDA AAPL SPY

# Or comma-separated / repeated flags
tradingagents intraday --watchlist NVDA,AAPL,SPY
tradingagents intraday --watchlist NVDA --watchlist AAPL --watchlist SPY

# Use a specific strategy
tradingagents intraday NVDA --strategy base_momentum

# Dry-run: scan and evaluate gates but skip LLM pipeline
tradingagents intraday NVDA AAPL --dry-run

# Verbose scanner + dataflow logs
tradingagents intraday SPY --verbose
tradingagents intraday SPY --log-level DEBUG

# Pre-market bias without Sentiment Analyst (no Reddit/StockTwits fetches)
tradingagents intraday SPY --analysts market,news,fundamentals

# Restart later with added symbols — restores cached bias for the day
tradingagents intraday --watchlist SPY,COHR,TGT

# Force full pre-market re-run for all symbols
tradingagents intraday SPY COHR --force-premarket

# Ignore cache and re-run pre-market (still writes cache afterward)
tradingagents intraday SPY --no-restore-premarket

# Or via .env:
# TRADINGAGENTS_INTRADAY_PREMARKET_ANALYSTS=market,news,fundamentals
# TRADINGAGENTS_INTRADAY_RESTORE_PREMARKET_BIAS=true
# TRADINGAGENTS_LOG_LEVEL=INFO
```

Signals are logged to `~/.tradingagents/intraday/YYYY-MM-DD/signals.csv`.

Pre-market daily bias is cached to `~/.tradingagents/intraday/YYYY-MM-DD/premarket_bias.json`. When you restart the scanner the same day with an updated watchlist, symbols already analyzed are restored instantly; only new symbols run the LLM pre-market pipeline. If you change `--analysts`, affected symbols are re-analyzed automatically.

Watchlist changes require restarting the scanner (no hot-reload while the session loop is running).

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Daily bias computed once pre-market | Avoids running 4 analysts + researcher debate on every bar (cost + latency) |
| Strategy-first gating | Deterministic strategy evaluation on every bar; LLM only when gates pass |
| Two-gate structure | Strategy pass (Gate 1) then optional MTF/bias alignment (Gate 2) before LLM |
| Strategy as a Protocol | Swap strategy without touching pipeline; enables rapid iteration over time |
| Signals only, no order routing | Reduces system complexity; order execution is a future phase |
| APScheduler for bar-close timing | More reliable than stdlib `sched` for long-running intraday sessions |
| ThreadPoolExecutor for parallelism | Watchlist evaluation runs concurrently (max 5 symbols) without requiring full async refactor |

---

## Open Questions

1. **Bar-close delay**: Schwab candles propagate with a small lag after the bar closes. Should the scanner wait a fixed offset (e.g., 15 seconds) after each scheduled bar-close before fetching?
2. **Market Internals**: `$ADD`/`$TICK`/`$VOLD` are Magpie factors but `get_market_internals()` currently raises `NoMarketDataError`. The scanner should degrade gracefully (Magpie already has fallback logic). Defer full support or stub it?
3. **Re-entry suppression across bars**: The scanner tracks per-symbol cooldown at the session level (`intraday_signal_cooldown_bars`). Magpie transition logic is reserved for a future `magpie` strategy implementation.

---

## Related Documentation

- [PLAN.md](PLAN.md) — detailed phased implementation plan
- [PROGRESS.md](PROGRESS.md) — step-by-step progress tracker
- [../ARCHITECTURE.md](../ARCHITECTURE.md) — existing system architecture
- [../DATAFLOWS.md](../DATAFLOWS.md) — data source routing and vendor abstraction
- [../STATE_MANAGEMENT.md](../STATE_MANAGEMENT.md) — graph state schema
- [../SCHWAB_API_QUICK_REFERENCE.md](../SCHWAB_API_QUICK_REFERENCE.md) — Schwab endpoint reference
