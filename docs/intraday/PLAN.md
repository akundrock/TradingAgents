# Intraday Watchlist System — Implementation Plan

Phased implementation plan for adding intraday watchlist trading to TradingAgents. Each phase is independently deliverable and testable. See [PROGRESS.md](PROGRESS.md) to track completion status.

---

## Phase 1 — Data Foundation

**Goal**: Extend the Schwab and stockstats data layers to support multi-timeframe (MTF) candle fetching and indicator computation. No new packages; no pipeline changes. Fully testable in isolation.

### Step 1 — `tradingagents/dataflows/schwab.py`

Add `get_candles_multi_timeframe()`:

```python
def get_candles_multi_timeframe(
    symbol: str,
    session_start: datetime,
    as_of: datetime,
    timeframes: list[int] = [5, 30],   # minutes
) -> dict[int, pd.DataFrame]:
    """
    Fetch intraday candles for multiple timeframes in parallel.
    Returns dict keyed by interval in minutes, e.g. {5: df_5min, 30: df_30min}.
    Reuses _fetch_price_history_range() internally.
    """
```

- Uses `concurrent.futures.ThreadPoolExecutor` to fire both requests simultaneously
- Each DataFrame has columns: `datetime`, `open`, `high`, `low`, `close`, `volume`
- `session_start` defines the intraday lookback start (typically 09:30 ET that day)
- `as_of` is the current bar-close timestamp (exclusive end for Schwab API)
- Raises `NoMarketDataError` if either timeframe returns no candles

### Step 2 — `tradingagents/dataflows/stockstats_utils.py`

Add `compute_mtf_indicators()`:

```python
def compute_mtf_indicators(
    candle_dfs: dict[int, pd.DataFrame],
) -> dict[int, pd.DataFrame]:
    """
    Apply stockstats.wrap() to each timeframe DataFrame and compute
    a standard intraday indicator set. Returns same-keyed dict with
    enriched DataFrames.
    """
```

Indicators computed per timeframe:
- `close_10_ema` — EMA(10), fast trend
- `close_20_sma` — SMA(20), slow trend
- `rsi` — RSI(14), momentum / overbought-oversold
- `macd`, `macds`, `macdh` — MACD line, signal, histogram
- `atr` — ATR(14), volatility / stop distance
- `boll`, `boll_ub`, `boll_lb` — Bollinger Bands(20, 2σ)
- `vwma` — Volume-Weighted MA

VWAP is computed separately (not available in stockstats natively):

```python
def compute_intraday_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Cumulative VWAP from session start.
    typical_price = (high + low + close) / 3
    vwap = cumsum(typical_price * volume) / cumsum(volume)
    """
```

### Step 3 — Tests

- `tests/test_schwab_mtf_fetch.py` — mock `_fetch_price_history_range`, assert both TFs returned, assert error handling
- `tests/test_stockstats_mtf.py` — pass synthetic OHLCV, assert all indicator columns present and non-null

---

## Phase 2 — Scanner Core

**Goal**: Build the `tradingagents/intraday/` package with session management, MTF validation, and the main scanner loop. Depends on Phase 1.

### Step 4 — `tradingagents/intraday/__init__.py`

Package scaffold; expose top-level symbols:

```python
from .scanner import WatchlistScanner
from .session import TradingSession
```

### Step 5 — `tradingagents/intraday/session.py`

`TradingSession` dataclass:

```python
@dataclass
class DailyBiasReport:
    symbol: str
    trade_date: str                   # "YYYY-MM-DD"
    direction: Literal["bullish", "bearish", "neutral"]
    key_levels: dict[str, float]      # {"support": x, "resistance": y, "pivot": z}
    summary: str                      # Full Research Manager output
    computed_at: datetime

@dataclass
class IntradaySignal:
    symbol: str
    bar_time: datetime
    action: Literal["BUY", "SELL", "HOLD"]
    direction: Literal["long", "short", "none"]
    entry_price: float | None
    stop_loss: float | None
    confidence: str                   # "Marginal" | "Strong" | "Premium"
    magpie_score: int
    gate_summary: str
    reasoning: str                    # From Portfolio Manager

@dataclass
class TradingSession:
    session_date: str                 # "YYYY-MM-DD"
    watchlist: list[str]
    status: Literal["pre_market", "active", "closed"]
    daily_bias_cache: dict[str, DailyBiasReport]   # symbol → report
    signal_log: list[IntradaySignal]
    last_scan_time: datetime | None
    scan_count: int
```

### Step 6 — `tradingagents/intraday/mtf_validator.py`

`MultiTimeframeValidator`:

```python
@dataclass
class MTFValidationResult:
    symbol: str
    bar_time: datetime
    # Per-timeframe indicator snapshots (last row of each enriched DF)
    snapshot_5min: dict[str, float]
    snapshot_30min: dict[str, float]
    snapshot_daily: dict[str, float]
    # Derived conclusions
    trend_5min: Literal["up", "down", "flat"]
    trend_30min: Literal["up", "down", "flat"]
    daily_bias_direction: str          # from DailyBiasReport
    trends_aligned: bool               # all three same direction
    vwap_5min: float
    atr_5min: float                    # for stop sizing

class MultiTimeframeValidator:
    def evaluate(
        self,
        symbol: str,
        as_of: datetime,
        session: TradingSession,
        config: dict,
    ) -> MTFValidationResult:
        """
        1. Fetch 5-min + 30-min candles via get_candles_multi_timeframe()
        2. Fetch daily candles (last 60 days) from Schwab or yfinance
        3. Compute indicators on each TF via compute_mtf_indicators()
        4. Determine trend direction per TF (EMA > SMA → up; EMA < SMA → down)
        5. Read daily bias from session.daily_bias_cache[symbol]
        6. Return MTFValidationResult
        """
```

Trend direction heuristic:
- `close > EMA(10) > SMA(20)` → **up**
- `close < EMA(10) < SMA(20)` → **down**
- Otherwise → **flat**

### Step 7 — `tradingagents/intraday/scanner.py`

`WatchlistScanner`:

```python
class WatchlistScanner:
    def __init__(self, config: dict, ta_graph: TradingAgentsGraph): ...

    def run(self) -> None:
        """Main entry: pre-market setup → session loop → EOD summary."""

    def _run_premarket_setup(self) -> None:
        """
        For each symbol in watchlist, call ta_graph.propagate_daily_bias().
        Store results in self.session.daily_bias_cache.
        Runs concurrently (ThreadPoolExecutor, max intraday_max_concurrent_symbols).
        """

    def _session_loop(self) -> None:
        """
        Schedules _on_bar_close() at each 5-min mark from session_start to session_end.
        Uses APScheduler BlockingScheduler (UTC cron, converted to ET bar times).
        Blocks until session_end.
        """

    def _on_bar_close(self, bar_time: datetime) -> None:
        """
        Evaluates all watchlist symbols in parallel at each bar close.
        Waits ~15 seconds after scheduled time before fetching (propagation delay).
        """

    def _evaluate_symbol(self, symbol: str, bar_time: datetime) -> IntradaySignal | None:
        """
        Full per-symbol evaluation pipeline:
        1. MultiTimeframeValidator.evaluate()
        2. Magpie scoring (pass MTF data, skip internal fetch)
        3. GatingLayer.evaluate()
        4. If gates pass → IntradayTradingGraph.propagate_intraday()
        5. Return IntradaySignal or None
        """

    def _emit_signal(self, signal: IntradaySignal) -> None:
        """Print to console (Rich) + append to CSV log."""

    def _eod_summary(self) -> None:
        """Print session summary: symbols scanned, signals generated, gate stats."""
```

---

## Phase 3 — Strategy & Gating Layer

**Goal**: Define the pluggable strategy interface and the three-gate validation logic. Independent of Phase 2 (can be built in parallel).

### Step 8 — `tradingagents/intraday/strategy.py`

```python
@dataclass
class StrategyResult:
    passed: bool
    direction: Literal["long", "short", "none"]
    reason: str
    factors_met: list[str]
    factors_missing: list[str]

class IntradayStrategy(Protocol):
    name: str

    def check_setup(
        self,
        symbol: str,
        mtf: MTFValidationResult,
        daily_bias: DailyBiasReport,
        magpie: MagpieSignal,
    ) -> StrategyResult: ...

    def describe(self) -> str:
        """Human-readable description of the strategy conditions."""
        ...
```

### Step 9 — `tradingagents/intraday/strategies/base_momentum.py`

`BaseMomentumStrategy` — initial strategy, designed for trend-following momentum setups:

**Long conditions** (all must be true):
1. `close > vwap_5min` — price above VWAP (bullish intraday bias)
2. `ema_10 > sma_20` on 5-min chart — fast MA above slow MA (uptrend)
3. `40 <= rsi <= 70` on 5-min — not overbought, momentum intact
4. `trend_30min == "up"` — higher timeframe confirms direction
5. `daily_bias.direction == "bullish"` — daily analyst bias aligns

**Short conditions** — exact inverse of above.

Strategy is subclassable: override `_long_conditions()` and `_short_conditions()` to create variants without duplicating scaffolding.

Strategy registry:

```python
# tradingagents/intraday/strategies/__init__.py
STRATEGY_REGISTRY: dict[str, type[IntradayStrategy]] = {
    "base_momentum": BaseMomentumStrategy,
}

def get_strategy(name: str) -> IntradayStrategy:
    if name not in STRATEGY_REGISTRY:
        raise ValueError(f"Unknown strategy: {name}. Available: {list(STRATEGY_REGISTRY)}")
    return STRATEGY_REGISTRY[name]()
```

### Step 10 — `tradingagents/intraday/gating.py`

`GatingLayer` — three sequential gates, cheapest first:

```python
@dataclass
class GateResult:
    passed: bool
    gate1_magpie: bool
    gate1_reason: str
    gate2_mtf_alignment: bool
    gate2_reason: str
    gate3_strategy: bool | None        # None if gate2 failed (not evaluated)
    gate3_reason: str | None
    final_direction: Literal["long", "short", "none"]

class GatingLayer:
    def evaluate(
        self,
        mtf: MTFValidationResult,
        magpie: MagpieSignal,
        daily_bias: DailyBiasReport,
        strategy: IntradayStrategy,
        config: dict,
    ) -> GateResult:
        """
        Gate 1: magpie.long_score >= min_score OR short_score >= min_score
        Gate 2: mtf.trend_30min direction matches daily_bias.direction
        Gate 3: strategy.check_setup() passed
        Short-circuits: if Gate 1 fails, Gates 2+3 are not evaluated.
        """
```

---

## Phase 4 — Intraday LLM Pipeline

**Goal**: Wire the pre-computed MTF context and daily bias into a lightweight LLM pipeline that skips the expensive analyst nodes. Depends on Phases 2 and 3.

### Step 11 — `tradingagents/graph/trading_graph.py`

Add `propagate_daily_bias()`:

```python
def propagate_daily_bias(
    self,
    symbol: str,
    trade_date: str,        # "YYYY-MM-DD"
) -> DailyBiasReport:
    """
    Run the analyst + researcher debate sub-graph only.
    Stops after Research Manager (before Magpie and Trader).
    Returns a DailyBiasReport extracted from the Research Manager's structured output.
    Uses the existing graph infrastructure; a conditional route stops execution
    after the research_manager node.
    """
```

Implementation approach: pass a flag in state (`"stop_after_research": True`) and add a conditional edge after the `research_manager` node that routes to END when the flag is set.

### Step 12 — `tradingagents/graph/intraday_graph.py`

`IntradayTradingGraph`:

```python
class IntradayTradingGraph:
    """
    Slimmed LangGraph for intraday signal generation.
    Starts at the Trader node; injects pre-computed state.
    Reuses existing Trader, risk debater, and Portfolio Manager agents.
    """

    def __init__(self, config: dict): ...

    def propagate_intraday(
        self,
        symbol: str,
        bar_time: datetime,
        daily_bias: DailyBiasReport,
        mtf: MTFValidationResult,
        magpie_signal: MagpieSignal,
        gate_result: GateResult,
    ) -> IntradaySignal:
        """
        Build intraday state and run: Trader → Risk Debate → PM.
        Returns IntradaySignal with action, entry, stop, confidence, reasoning.
        """
```

State injected at graph start:
- `company_of_interest`, `trade_date`, `asset_type`
- `market_report` = daily_bias.summary (pre-computed)
- `fundamentals_report` = "" (not needed for intraday)
- `sentiment_report` = "" (not needed for intraday)
- `news_report` = "" (not needed for intraday)
- `magpie_signal` = magpie_signal (pre-computed)
- `intraday_context` = serialized MTFValidationResult + GateResult

Graph shape:
```
START → trader_node → [risk debate loop] → portfolio_manager → END
```

### Step 13 — `tradingagents/graph/propagation.py`

Extend state schema with `intraday_context`:

```python
"intraday_context": {
    "scan_time": str,                  # ISO datetime of bar close
    "mtf_5min_snapshot": dict,         # Last row of 5-min indicators
    "mtf_30min_snapshot": dict,        # Last row of 30-min indicators
    "daily_bias": str,                 # "bullish" | "bearish" | "neutral"
    "daily_bias_report": str,          # Full Research Manager summary
    "trends_aligned": bool,
    "vwap": float,
    "atr": float,
    "gate_results": dict,              # Per-gate pass/fail + reasons
} | None
```

### Step 14 — `tradingagents/agents/trader/magpie.py`

Refactor `_fetch_intraday_factor_inputs()` to accept optional pre-fetched data:

```python
def _fetch_intraday_factor_inputs(
    self,
    symbol: str,
    as_of: datetime,
    prefetched_mtf: dict[int, pd.DataFrame] | None = None,
) -> dict:
    """
    If prefetched_mtf is provided, use it directly instead of calling Schwab.
    Falls back to internal fetch when called standalone (existing behavior preserved).
    """
```

This keeps Magpie fully functional in the existing daily pipeline while eliminating the redundant Schwab call when the scanner has already fetched the data.

---

## Phase 5 — Config & CLI

**Goal**: Surface all new functionality through configuration and a new CLI subcommand.

### Step 15 — `tradingagents/default_config.py`

Add intraday configuration block:

```python
# ── Intraday watchlist scanning ──────────────────────────────────────────────
"intraday_enabled": False,
"watchlist": [],                             # ["NVDA", "AAPL", "SPY"]
"intraday_scan_interval_minutes": 5,
"intraday_bar_close_delay_seconds": 15,      # Wait after bar close before fetching
"intraday_premarket_setup_time": "09:00",    # ET; when to run daily bias
"intraday_session_start": "09:30",           # ET
"intraday_session_end": "16:00",             # ET
"intraday_timezone": "America/New_York",
"intraday_mtf_timeframes": [5, 30],          # Intraday TFs (daily always included)
"intraday_strategy": "base_momentum",        # Strategy registry key
"intraday_min_magpie_score": 3,              # Gate 1 threshold
"intraday_require_daily_bias_alignment": True,
"intraday_output_dir": "~/.tradingagents/intraday",
"intraday_max_concurrent_symbols": 5,
```

### Step 16 — `cli/main.py`

Add `intraday` subcommand:

```
tradingagents intraday [OPTIONS]

Options:
  --watchlist SYMBOL...        Symbols to monitor (overrides config watchlist)
  --strategy TEXT              Strategy name (default: base_momentum)
  --dry-run                    Evaluate gates but skip LLM pipeline
  --no-premarket               Skip daily bias setup (use neutral bias)
  --output-dir PATH            Override signal log directory
  --help
```

Rich live display during session:
- Header: date, session status, strategy name
- Per-symbol table: symbol, last scan time, daily bias, last Magpie score, last signal
- Footer: total scans, signals triggered, gate stats

Signal CSV columns: `bar_time`, `symbol`, `action`, `direction`, `entry_price`, `stop_loss`, `confidence`, `magpie_score`, `gate_summary`, `reasoning`

---

## Phase 6 — Tests

**Goal**: Verify each layer in isolation and as an integrated whole.

### Step 17 — `tests/test_schwab_mtf_fetch.py`
- `test_fetch_returns_both_timeframes` — mock `_fetch_price_history_range`, assert dict keys `{5, 30}`
- `test_fetch_raises_on_empty_candles` — mock returns `{"empty": True}`, assert `NoMarketDataError`
- `test_fetch_uses_parallel_requests` — assert both requests fired (mock call count)

### Step 18 — `tests/test_stockstats_mtf.py`
- `test_all_indicators_computed` — synthetic OHLCV, assert all columns present
- `test_vwap_correct_formula` — known inputs, assert VWAP value matches hand-calculation
- `test_handles_single_row` — edge case: one bar of data, no NaN crash

### Step 19 — `tests/test_intraday_gating.py`
- `test_gate1_blocks_on_low_magpie_score` — score=2, gate1 fails, gate2+3 not evaluated
- `test_gate2_blocks_on_misaligned_trend` — 30-min down, daily bullish, gate2 fails
- `test_gate3_blocks_on_strategy_fail` — gates 1+2 pass, strategy returns `passed=False`
- `test_all_gates_pass` — happy path, `GateResult.passed == True`
- `test_gate_result_direction_from_magpie` — assert final direction matches dominant side

### Step 20 — `tests/test_intraday_strategy.py`
- `test_base_momentum_long_setup` — all conditions met, `StrategyResult.direction == "long"`
- `test_base_momentum_blocks_on_rsi_overbought` — RSI=75, `passed=False`, RSI in `factors_missing`
- `test_base_momentum_blocks_on_bearish_daily_bias` — daily bearish, long setup fails
- `test_base_momentum_short_setup` — inverse conditions, `direction == "short"`

### Step 21 — `tests/test_intraday_scanner.py`
- `test_premarket_setup_called_per_symbol` — assert `propagate_daily_bias` called N times
- `test_on_bar_close_skips_outside_session` — bar_time before 09:30, no evaluation
- `test_signal_emitted_when_gates_pass` — mock all dependencies, assert `signal_log` populated
- `test_no_signal_when_gates_fail` — gate1 fails, `signal_log` empty

### Step 22 — `tests/test_intraday_graph.py`
- `test_propagate_intraday_returns_signal` — mocked Trader+PM, assert `IntradaySignal` returned
- `test_intraday_state_contains_context` — assert `intraday_context` key present in graph state
- `test_analyst_nodes_not_called` — verify analyst nodes are not invoked in intraday graph

---

## Dependencies

No new external packages are strictly required. Optional improvement:

```
apscheduler>=3.10    # Bar-close scheduling (fallback: stdlib sched)
```

Add to `requirements.txt` and `pyproject.toml` under `[project.optional-dependencies]`:
```toml
[project.optional-dependencies]
intraday = ["apscheduler>=3.10"]
```

---

## Implementation Order

Phases are ordered by dependency but Phase 3 (strategy/gating) can be built concurrently with Phase 2 (scanner core) since they share no internal dependencies.

```
Phase 1 (Data) → Phase 2 (Scanner) ┐
                                     ├→ Phase 4 (Pipeline) → Phase 5 (Config/CLI) → Phase 6 (Tests)
Phase 3 (Strategy/Gating) ──────────┘
```

---

## Post-MVP Extensions (implemented)

Work beyond the original Phases 1–6 plan:

| Area | Docs | Code |
|------|------|------|
| Pro Trader Dashboard strategy | [pro-trader-dashboard-spec.md](../pro-trader-dashboard-spec.md) | `strategies/pro_trader_dashboard.py`, `indicators/` |
| Dynamic volume + RRS screener | [README.md](README.md), spec §11 | `universe_screener.py`, `schwab_streamer.py` |
| Live dashboard + premarket cache | [README.md](README.md), [STATUS.md](STATUS.md) | `cli/intraday_display.py`, `premarket_cache.py` |

See [PROGRESS.md](PROGRESS.md) Phases 7–9 for completion status.

