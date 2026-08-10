# TradingAgents: Configuration System

## Overview

TradingAgents uses a **cascading configuration system** where settings are applied in order of precedence, allowing easy customization without code changes.

## Configuration Cascade (Order of Precedence)

Settings are applied in this order (later overrides earlier):

```
1. Code Defaults (in DEFAULT_CONFIG)
   ↓
2. Environment Variables (TRADINGAGENTS_*)
   ↓
3. CLI Prompts (interactive selection)
   ↓
4. Hard-Coded Overrides (in your script)
```

### Example: Setting the LLM Provider

```python
# 1. DEFAULT_CONFIG says:
DEFAULT_CONFIG["llm_provider"] = "openai"

# 2. User sets environment variable:
export TRADINGAGENTS_LLM_PROVIDER=anthropic

# 3. CLI prompts (if running from CLI):
# User selects: Anthropic

# 4. In your script, hard-code override:
config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "bedrock"
ta = TradingAgentsGraph(config=config)  # Uses "bedrock"
```

**Result**: `bedrock` (the latest override wins)

## Default Configuration

**Location**: `tradingagents/default_config.py`

```python
DEFAULT_CONFIG = {
    # ---- LLM Configuration ----
    "llm_provider": "openai",           # Provider: openai, anthropic, google, azure, bedrock
    "deep_think_llm": "gpt-5.5",        # Model for complex reasoning
    "quick_think_llm": "gpt-4o",        # Model for fast tasks
    "temperature": 0.7,                 # Sampling randomness (0.0-1.0)
    "llm_max_retries": 3,               # Retry budget for LLM failures
    
    # ---- Provider-Specific Reasoning ----
    "openai_reasoning_effort": None,    # o1/o3: low/medium/high or None
    "google_thinking_level": None,      # Gemini: low/medium/high or None
    "anthropic_effort": None,           # Claude: low/medium/high or None
    "backend_url": None,                # Custom endpoint for OpenAI-compatible
    
    # ---- Directories ----
    "project_dir": "/path/to/tradingagents",
    "data_cache_dir": "~/.tradingagents/cache",
    "results_dir": "~/.tradingagents/logs",
    
    # ---- Memory & Checkpointing ----
    "memory_log_path": "~/.tradingagents/memory/trading_memory.md",
    "memory_log_max_entries": None,     # None = unlimited
    "checkpoint_enabled": True,         # Enable/disable checkpointing
    
    # ---- Trading Parameters ----
    "max_debate_rounds": 2,             # Investment debate max rounds
    "max_risk_discuss_rounds": 2,       # Risk management debate max rounds
    "max_recur_limit": 100,             # LangGraph recursion limit
    "output_language": "en",            # i18n: en, zh, ja, de, es, fr, pt, ru, ko
    
    # ---- Data Vendors (Optional) ----
    "alpha_vantage_api_key": "",        # Alpha Vantage API key
    "fred_api_key": "",                 # FRED economic data API key
    "benchmark_ticker": "SPY",          # Benchmark for risk-adjusted returns
    
    # ---- Schwab Integration (Your Addition) ----
    "schwab_api_key": "",               # Schwab TOS API key
    "schwab_account_number": "",        # Schwab account ID
}
```

## Environment Variables (TRADINGAGENTS_*)

For every config key, there's a corresponding environment variable. Set these in your shell or `.env` file:

**Location**: Defined in `tradingagents/default_config.py` in `_ENV_OVERRIDES`:

```python
_ENV_OVERRIDES = {
    "TRADINGAGENTS_LLM_PROVIDER":         "llm_provider",
    "TRADINGAGENTS_DEEP_THINK_LLM":       "deep_think_llm",
    "TRADINGAGENTS_QUICK_THINK_LLM":      "quick_think_llm",
    "TRADINGAGENTS_LLM_BACKEND_URL":      "backend_url",
    "TRADINGAGENTS_OUTPUT_LANGUAGE":      "output_language",
    "TRADINGAGENTS_MAX_DEBATE_ROUNDS":    "max_debate_rounds",
    "TRADINGAGENTS_MAX_RISK_ROUNDS":      "max_risk_discuss_rounds",
    "TRADINGAGENTS_CHECKPOINT_ENABLED":   "checkpoint_enabled",
    "TRADINGAGENTS_BENCHMARK_TICKER":     "benchmark_ticker",
    "TRADINGAGENTS_TEMPERATURE":          "temperature",
    "TRADINGAGENTS_LLM_MAX_RETRIES":      "llm_max_retries",
    "TRADINGAGENTS_GOOGLE_THINKING_LEVEL":   "google_thinking_level",
    "TRADINGAGENTS_OPENAI_REASONING_EFFORT": "openai_reasoning_effort",
    "TRADINGAGENTS_ANTHROPIC_EFFORT":        "anthropic_effort",
   "TRADINGAGENTS_SCHWAB_CLIENT_ID":        "schwab_client_id",
   "TRADINGAGENTS_SCHWAB_CLIENT_SECRET":    "schwab_client_secret",
   "TRADINGAGENTS_SCHWAB_TOKENS_PATH":      "schwab_tokens_path",
   "TRADINGAGENTS_SCHWAB_REDIRECT_URI":     "schwab_redirect_uri",
}
```

### Examples

```bash
# Set LLM provider
export TRADINGAGENTS_LLM_PROVIDER=anthropic

# Set deep thinking model
export TRADINGAGENTS_DEEP_THINK_LLM=claude-opus-4

# Set reasoning effort (for o1/o3)
export TRADINGAGENTS_OPENAI_REASONING_EFFORT=high

# Set API key
export TRADINGAGENTS_ALPHA_VANTAGE_API_KEY=ABC123DEF456

# Set output language
export TRADINGAGENTS_OUTPUT_LANGUAGE=ja

# Set debate rounds
export TRADINGAGENTS_MAX_DEBATE_ROUNDS=3

# Set cache directory
export TRADINGAGENTS_CACHE_DIR=/custom/cache/path

# Disable checkpointing
export TRADINGAGENTS_CHECKPOINT_ENABLED=false

# Set maximum LLM retries
export TRADINGAGENTS_LLM_MAX_RETRIES=5
```

### Type Coercion

Environment variables are automatically coerced to match the type of the default value:

```python
# Integer config
"max_debate_rounds": 2
export TRADINGAGENTS_MAX_DEBATE_ROUNDS=3  # Parsed as int: 3

# Boolean config
"checkpoint_enabled": True
export TRADINGAGENTS_CHECKPOINT_ENABLED=false  # Parsed as bool: False
# Accepted values: true/1/yes/on (→ True), false/0/no/off (→ False)

# Float config
"temperature": 0.7
export TRADINGAGENTS_TEMPERATURE=0.5  # Parsed as float: 0.5

# String config
"llm_provider": "openai"
export TRADINGAGENTS_LLM_PROVIDER=anthropic  # Kept as str: "anthropic"
```

**Invalid values raise errors at startup**:

```bash
export TRADINGAGENTS_TEMPERATURE=invalid
# → ValueError: expected a float, got 'invalid'

export TRADINGAGENTS_MAX_DEBATE_ROUNDS=-1
# → ValueError: max_debate_rounds must be >= 0, got -1

export TRADINGAGENTS_CHECKPOINT_ENABLED=yes_but_maybe
# → ValueError: expected a boolean (true/1/yes/on/false/0/no/off), got 'yes_but_maybe'
```

## Using .env Files

Load environment variables from a `.env` file in your project directory:

**File**: `.env` (root of TradingAgents repo or your script)

```bash
# .env file
TRADINGAGENTS_LLM_PROVIDER=anthropic
TRADINGAGENTS_DEEP_THINK_LLM=claude-opus-4
TRADINGAGENTS_ANTHROPIC_EFFORT=high

# API Keys
OPENAI_API_KEY=sk-proj-...
ANTHROPIC_API_KEY=sk-ant-...
TRADINGAGENTS_FRED_API_KEY=abc123...
TRADINGAGENTS_ALPHA_VANTAGE_API_KEY=def456...

# Schwab/TOS (OHLC + indicators)
TRADINGAGENTS_SCHWAB_CLIENT_ID=your_schwab_app_client_id
TRADINGAGENTS_SCHWAB_CLIENT_SECRET=your_schwab_app_client_secret
TRADINGAGENTS_SCHWAB_TOKENS_PATH=/Users/you/.tradingagents/cache/schwab_tokens.json
TRADINGAGENTS_SCHWAB_REDIRECT_URI=https://127.0.0.1

# Directories
TRADINGAGENTS_CACHE_DIR=/tmp/tradingagents_cache
TRADINGAGENTS_RESULTS_DIR=/tmp/tradingagents_results

# Schwab Integration
TRADINGAGENTS_SCHWAB_API_KEY=your_schwab_key
TRADINGAGENTS_SCHWAB_ACCOUNT=your_account_number
```

**Loading**:

The `tradingagents/__init__.py` automatically loads `.env` files at import time:

```python
from dotenv import find_dotenv, load_dotenv

# Load .env from current working directory
load_dotenv(find_dotenv(usecwd=True))

# Load .env.enterprise (optional enterprise config)
load_dotenv(find_dotenv(".env.enterprise", usecwd=True), override=False)
```

**Priority for .env loading**:
1. User's explicitly exported env vars (highest priority)
2. `.env` file in current working directory
3. `.env.enterprise` file (for enterprise deployments)

## Configuration in Code

### Using DEFAULT_CONFIG

```python
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

# DEFAULT_CONFIG already has env vars applied
config = DEFAULT_CONFIG.copy()  # Make a copy to avoid mutating global

ta = TradingAgentsGraph(config=config)
```

## Schwab Vendor (OHLC + Indicators)

Schwab can be selected for both `core_stock_apis` and `technical_indicators`.

```python
config = DEFAULT_CONFIG.copy()
config["data_vendors"]["core_stock_apis"] = "schwab"
config["data_vendors"]["technical_indicators"] = "schwab"
```

You can also use ordered fallback chains:

```python
config["data_vendors"]["core_stock_apis"] = "schwab,yfinance"
config["data_vendors"]["technical_indicators"] = "schwab,yfinance"
```

Expected setup for Schwab:
1. Set `TRADINGAGENTS_SCHWAB_CLIENT_ID` and `TRADINGAGENTS_SCHWAB_CLIENT_SECRET`.
2. Optionally set `TRADINGAGENTS_SCHWAB_TOKENS_PATH` and `TRADINGAGENTS_SCHWAB_REDIRECT_URI`.
3. Run in-repo bootstrap:

```bash
tradingagents schwab-auth
```

You can also pass the redirect URL directly:

```bash
tradingagents schwab-auth --redirect-url "https://127.0.0.1/?code=..."
```

### Overriding Configuration

```python
config = DEFAULT_CONFIG.copy()

# Override specific values
config["llm_provider"] = "bedrock"
config["deep_think_llm"] = "claude-opus-4"
config["max_debate_rounds"] = 3
config["temperature"] = 0.5

ta = TradingAgentsGraph(config=config)
```

### Selective Overrides

```python
config = {
    **DEFAULT_CONFIG,  # Start with all defaults
    "llm_provider": "google",  # Override specific keys
    "deep_think_llm": "gemini-2.0",
}

ta = TradingAgentsGraph(config=config)
```

## CLI Configuration

When using the CLI (`tradingagents` command), interactive prompts guide configuration:

**Location**: `cli/main.py` and `cli/utils.py`

### Configuration Flow

1. **Provider Selection** (unless `TRADINGAGENTS_LLM_PROVIDER` set):
   ```
   Select LLM Provider:
    1. OpenAI (GPT-5.x, o1, o3)
    2. Anthropic (Claude 4.x)
    3. Google Gemini (2.0, 1.5)
    4. Azure OpenAI
    5. AWS Bedrock
    6. OpenAI-Compatible Endpoint
   ```

2. **Model Selection** (unless `TRADINGAGENTS_DEEP_THINK_LLM` set):
   ```
   Select Deep Thinking Model:
    1. gpt-5.5 (default)
    2. gpt-5.4
    3. o1-preview
    ...
   ```

3. **Provider-Specific Options**:
   - **OpenAI o1/o3**: Reasoning effort (low/medium/high)
   - **Google Gemini**: Thinking level (off/low/medium/high)
   - **Anthropic Claude**: Extended thinking (low/medium/high)

4. **Analyst Selection** (unless `TRADINGAGENTS_ANALYSTS` set):
   ```
   Select Analysts:
    [x] Market/Technical Analyst
    [x] Sentiment Analyst
    [x] News Analyst
    [x] Fundamentals Analyst
   ```

5. **Research Depth**:
   ```
   Research Depth:
    1. Quick (fewer debate rounds, faster)
    2. Standard (balanced)
    3. Deep (more rounds, more thorough)
   ```

6. **API Key Validation**:
   - Checks if required API keys are set
   - Prompts to set if missing

**Skipping Prompts**:
All interactive selections are skipped if the corresponding env var is set:

```bash
# Set all config via env vars → no CLI prompts
export TRADINGAGENTS_LLM_PROVIDER=anthropic
export TRADINGAGENTS_DEEP_THINK_LLM=claude-opus-4
export TRADINGAGENTS_ANTHROPIC_EFFORT=high
export TRADINGAGENTS_OUTPUT_LANGUAGE=en

# CLI runs without prompts:
tradingagents  # Fully configured, no interaction
```

## Configuration Parameters Reference

### LLM Settings

| Parameter | Type | Default | Env Var | Description |
|-----------|------|---------|---------|-------------|
| `llm_provider` | str | `"openai"` | `TRADINGAGENTS_LLM_PROVIDER` | LLM provider (openai, anthropic, google, azure, bedrock) |
| `deep_think_llm` | str | `"gpt-5.5"` | `TRADINGAGENTS_DEEP_THINK_LLM` | Model for complex reasoning tasks |
| `quick_think_llm` | str | `"gpt-4o"` | `TRADINGAGENTS_QUICK_THINK_LLM` | Model for quick tasks (memory log queries, signal extraction) |
| `temperature` | float | `0.7` | `TRADINGAGENTS_TEMPERATURE` | LLM sampling temperature (0.0-1.0) |
| `llm_max_retries` | int | `3` | `TRADINGAGENTS_LLM_MAX_RETRIES` | Retry count for LLM failures |
| `backend_url` | str | `None` | `TRADINGAGENTS_LLM_BACKEND_URL` | Custom endpoint (e.g., for local inference) |

### Provider-Specific Reasoning

| Parameter | Type | Default | Env Var | Description |
|-----------|------|---------|---------|-------------|
| `openai_reasoning_effort` | str | `None` | `TRADINGAGENTS_OPENAI_REASONING_EFFORT` | o1/o3 reasoning effort: low, medium, high |
| `google_thinking_level` | str | `None` | `TRADINGAGENTS_GOOGLE_THINKING_LEVEL` | Gemini thinking level: off, low, medium, high |
| `anthropic_effort` | str | `None` | `TRADINGAGENTS_ANTHROPIC_EFFORT` | Claude extended thinking: low, medium, high |

### Directories

| Parameter | Type | Default | Env Var | Description |
|-----------|------|---------|---------|-------------|
| `project_dir` | str | Auto-detected | N/A | TradingAgents package directory |
| `data_cache_dir` | str | `~/.tradingagents/cache` | `TRADINGAGENTS_CACHE_DIR` | Cache for OHLCV data, checkpoints |
| `results_dir` | str | `~/.tradingagents/logs` | `TRADINGAGENTS_RESULTS_DIR` | Output reports and logs |

### Memory & Persistence

| Parameter | Type | Default | Env Var | Description |
|-----------|------|---------|---------|-------------|
| `memory_log_path` | str | `~/.tradingagents/memory/trading_memory.md` | `TRADINGAGENTS_MEMORY_LOG_PATH` | Path to decision log |
| `memory_log_max_entries` | int | `None` | `TRADINGAGENTS_MEMORY_LOG_MAX_ENTRIES` | Max resolved entries (rotation limit) |
| `checkpoint_enabled` | bool | `True` | `TRADINGAGENTS_CHECKPOINT_ENABLED` | Enable graph state checkpointing |

### Trading Parameters

| Parameter | Type | Default | Env Var | Description |
|-----------|------|---------|---------|-------------|
| `max_debate_rounds` | int | `2` | `TRADINGAGENTS_MAX_DEBATE_ROUNDS` | Max investment debate rounds |
| `max_risk_discuss_rounds` | int | `2` | `TRADINGAGENTS_MAX_RISK_ROUNDS` | Max risk management debate rounds |
| `max_recur_limit` | int | `100` | N/A | LangGraph recursion limit |
| `output_language` | str | `"en"` | `TRADINGAGENTS_OUTPUT_LANGUAGE` | Output language (i18n): en, zh, ja, de, es, fr, pt, ru, ko |
| `benchmark_ticker` | str | `"SPY"` | `TRADINGAGENTS_BENCHMARK_TICKER` | Benchmark for risk-adjusted metrics |

### Logging

| Parameter | Type | Default | Env Var | Description |
|-----------|------|---------|---------|-------------|
| `log_level` | str | `"WARNING"` | `TRADINGAGENTS_LOG_LEVEL` | Global log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### Intraday

| Parameter | Type | Default | Env Var | Description |
|-----------|------|---------|---------|-------------|
| `intraday_enabled` | bool | `False` | — | Master toggle (set by `tradingagents intraday` CLI) |
| `watchlist` | list[str] | `[]` | — | Static symbols; merged with screener results when enabled |
| `intraday_scan_interval_minutes` | int | `5` | — | Bar-close scan cadence |
| `intraday_bar_close_delay_seconds` | int | `15` | — | Wait after bar close before fetching Schwab candles |
| `intraday_premarket_setup_time` | str | `09:00` | — | ET hint for pre-market bias (scanner runs bias at startup) |
| `intraday_session_start` | str | `09:30` | — | ET session start for scans |
| `intraday_session_end` | str | `16:00` | — | ET session end |
| `intraday_timezone` | str | `America/New_York` | — | Scheduler timezone |
| `intraday_mtf_timeframes` | list[int] | `[5, 30]` | — | Schwab-fetchable intervals (minutes). `pro_trader_dashboard` auto-adds 15/30/60. |
| `intraday_mtf_fetch_mode` | str | `5m_resample` | — | `5m_resample` (one 5m call per symbol; 15/30/60 derived locally) or `multi` (legacy parallel fetch per TF) |
| `intraday_benchmark_cache_per_scan` | bool | `True` | — | Fetch SPY/benchmark intraday frames once per scan cycle (shared across watchlist) |
| `intraday_strategy` | str | `base_momentum` | `TRADINGAGENTS_INTRADAY_STRATEGY` | `base_momentum` or `pro_trader_dashboard` |
| `intraday_gate2_mode` | str \| null | `None` | — | Gate 2: `off`, `daily_bias`, or `supertrend`. `None` uses resolver (`orb_breakout` + screener → `supertrend`) |
| `intraday_require_daily_bias_alignment` | bool | `True` | `TRADINGAGENTS_INTRADAY_REQUIRE_DAILY_BIAS_ALIGNMENT` | Legacy; when `gate2_mode` unset, `false` → Gate 2 `off` |
| `intraday_lazy_bias_on_gate1` | bool | `True` | `TRADINGAGENTS_INTRADAY_LAZY_BIAS_ON_GATE1` | After Gate 1, run slim daily bias when bias is still neutral (`daily_bias` mode only) |
| `intraday_lazy_bias_analysts` | list[str] | `market` | — | Analyst wire keys for lazy Gate-1 bias |
| `intraday_orb_breakout_screener_disable_gate2` | bool | `False` | — | When `orb_breakout` + screener and `gate2_mode` unset, `true` skips Gate 2 |
| `intraday_output_dir` | str | `~/.tradingagents/intraday` | — | Signals CSV and premarket cache root |
| `intraday_max_concurrent_symbols` | int | `5` | — | Parallel symbol evaluation / screener RRS batch size |
| `intraday_signal_cooldown_bars` | int | `3` | — | Suppress duplicate same-direction signals within N scan intervals |
| `intraday_premarket_analysts` | list[str] | `market,social,news,fundamentals` | `TRADINGAGENTS_INTRADAY_PREMARKET_ANALYSTS` | Analyst wire keys for pre-market bias |
| `intraday_restore_premarket_bias` | bool | `True` | `TRADINGAGENTS_INTRADAY_RESTORE_PREMARKET_BIAS` | Restore `premarket_bias.json` on same-day restart |
| `intraday_screener_enabled` | bool | `False` | — | Dynamic volume + RRS watchlist screener |
| `intraday_screener_interval_minutes` | int | `15` | — | Screener refresh cadence |
| `intraday_screener_keys` | list[str] | `NASDAQ_VOLUME_0`, `NYSE_VOLUME_0` | — | Schwab Streamer volume rankings (exchange actives; not SPY index) |
| `intraday_screener_candidate_limit` | int | `50` | — | Max symbols before RRS (`sp500_quotes`/streamer) or **after** RRS ranking (`sp500_rrs` / `sp500_rs_quotes`) |
| `intraday_screener_prefilter_limit` | int | `100` | `TRADINGAGENTS_INTRADAY_SCREENER_PREFILTER_LIMIT` | Quote RS shortlist for `sp500_rs_quotes` (before 5m fetches) |
| `intraday_screener_rrs_timeframes` | list[int] | `[5, 60]` | — | Intraday RRS TFs (minutes); 60m resampled from 5m |
| `intraday_screener_include_daily_rrs` | bool | `True` | `TRADINGAGENTS_INTRADAY_SCREENER_INCLUDE_DAILY_RRS` | Include daily RRS in screener alignment |
| `intraday_screener_min_rrs_aligned` | int | `2` | — | Min aligned RRS TFs (`0` = pure rank with `rank_all`) |
| `intraday_screener_rank_mode` | str | `pass_only` | — | `pass_only` (reject below min_aligned) or `rank_all` (score all, sort by rank RRS TF) |
| `intraday_screener_rank_rrs_timeframe` | str | `5m` | — | RRS timeframe for screener sort key: `5m`, `30m`, or `60m` |
| `intraday_screener_rank_by` | str | `aligned` | `TRADINGAGENTS_INTRADAY_SCREENER_RANK_BY` | `aligned` (TF agreement first) or `magnitude` (RRS score first) |
| `intraday_screener_require_relative_volume` | bool | `False` | — | Optional 5m rvolume > 1 before RRS ranking |
| `intraday_screener_min_price` | float | `10.0` | `TRADINGAGENTS_INTRADAY_SCREENER_MIN_PRICE` | Min last price from streamer (0 = no filter) |
| `intraday_screener_require_sp500` | bool | `True` | `TRADINGAGENTS_INTRADAY_SCREENER_REQUIRE_SP500` | Require S&P 500 membership via `sp500_constituents.json` (not `intraday_screener_keys`) |
| `intraday_screener_source` | str | `auto` | `TRADINGAGENTS_INTRADAY_SCREENER_SOURCE` | `auto` (RRS → `sp500_rs_quotes`), `sp500_rs_quotes`, `sp500_rrs`, `sp500_quotes`, or `streamer` |
| `intraday_screener_max_concurrent_symbols` | int | `None` | — | Screener RRS batch parallelism (defaults to `intraday_max_concurrent_symbols`) |
| `intraday_screener_max_watchlist` | int | `12` | — | Cap merged watchlist size |
| `intraday_screener_direction` | str | `short` | `TRADINGAGENTS_INTRADAY_SCREENER_DIRECTION` | `long` (outperformers), `short` (underperformers), or `both` |
| `intraday_screener_start_time` | str | `10:00` | — | ET — no screener refresh before OR window ends |
| `intraday_screener_symbol_cooldown_minutes` | int | `30` | — | Cooldown after symbol removed (config only; enforcement pending) |
| `intraday_screener_run_premarket_for_new` | bool | `False` | — | Run LLM pre-market for screener-added symbols |
| `intraday_screener_filters` | list[str] | `["rrs"]` | — | Pluggable screener filter pipeline: `orb`, `rrs` (comma-separated or list) |
| `intraday_screener_filter_mode` | str | `any` | — | `any` (union — pass if any filter matches) or `all` (intersection) |
| `screener_orb_direction` | str | `long` | — | ORB filter direction: `long`, `short`, or `both` |
| `screener_orb_entry_mode` | str | `wick_touch` | — | ORB breakout mode: `wick_touch` or `close_above` |
| `screener_orb_require_price_beyond` | bool | `True` | — | Require current close beyond ORH/ORL at eval time |
| `screener_orb_min_range_width` | float | `0.0` | — | Minimum opening range width (0 = no filter) |
| `pro_trader_benchmark` | str | `SPY` | — | RRS benchmark (`pro_trader_dashboard`) |
| `pro_trader_entry_mode` | str | `wick_touch` | — | ORB entry: `wick_touch` or `close_above` |
| `pro_trader_min_rs_timeframes` | int | `4` | — | Min aligned RRS TFs for strategy pass |
| `pro_trader_require_sector_alignment` | bool | `True` | — | Sector power-index alignment |
| `pro_trader_require_relative_volume` | bool | `True` | — | 5m relative volume > 1 |
| `pro_trader_require_daily_rrs` | bool | `True` | — | Daily RRS direction filter |
| `pro_trader_key_level_atr_buffer` | float | `0.5` | — | ATR buffer near daily resistance/support |
| `pro_trader_require_buy_pressure` | bool | `False` | `TRADINGAGENTS_PRO_TRADER_REQUIRE_BUY_PRESSURE` | Long bar buy-pressure gate (default off) |
| `pro_trader_require_sell_pressure` | bool | `False` | `TRADINGAGENTS_PRO_TRADER_REQUIRE_SELL_PRESSURE` | Short bar sell-pressure gate (default off) |
| `pro_trader_min_buy_percent` | float | `55.0` | `TRADINGAGENTS_PRO_TRADER_MIN_BUY_PERCENT` | Min buy % for long pressure gate |
| `pro_trader_min_sell_percent` | float | `55.0` | `TRADINGAGENTS_PRO_TRADER_MIN_SELL_PERCENT` | Min sell % for short pressure gate |
| `pro_trader_require_price_volume_trend` | bool | `False` | `TRADINGAGENTS_PRO_TRADER_REQUIRE_PRICE_VOLUME_TREND` | 3-bar price+volume trend gate |
| `pro_trader_min_premarket_volume` | float | `0` | `TRADINGAGENTS_PRO_TRADER_MIN_PREMARKET_VOLUME` | Min pre-market volume (0 = no filter) |

### Data Vendor API Keys

| Parameter | Type | Default | Env Var | Notes |
|-----------|------|---------|---------|-------|
| `alpha_vantage_api_key` | str | `""` | `TRADINGAGENTS_ALPHA_VANTAGE_API_KEY` | Alpha Vantage (required for that vendor) |
| `fred_api_key` | str | `""` | `TRADINGAGENTS_FRED_API_KEY` | FRED economic data (required for macro) |
| Standard providers (OpenAI, Anthropic, etc.) also use their own env vars (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.) |

### Your Additions (Schwab TOS)

| Parameter | Type | Default | Env Var | Notes |
|-----------|------|---------|---------|-------|
| `schwab_api_key` | str | `""` | `TRADINGAGENTS_SCHWAB_API_KEY` | Schwab TOS authentication |
| `schwab_account_number` | str | `""` | `TRADINGAGENTS_SCHWAB_ACCOUNT` | Schwab account ID |

## Configuration Examples

### Example 1: Using Claude with Extended Thinking

```python
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "anthropic"
config["deep_think_llm"] = "claude-opus-4"
config["quick_think_llm"] = "claude-sonnet-4"
config["anthropic_effort"] = "high"

ta = TradingAgentsGraph(config=config)
```

Or via environment:
```bash
export TRADINGAGENTS_LLM_PROVIDER=anthropic
export TRADINGAGENTS_DEEP_THINK_LLM=claude-opus-4
export TRADINGAGENTS_ANTHROPIC_EFFORT=high

# Run your script
python main.py
```

### Example 2: Using Bedrock in AWS Environment

```bash
export TRADINGAGENTS_LLM_PROVIDER=bedrock
export TRADINGAGENTS_DEEP_THINK_LLM=claude-opus-4
export AWS_REGION=us-west-2
```

### Example 3: Using Local Ollama Server

```bash
export TRADINGAGENTS_LLM_PROVIDER=ollama
export TRADINGAGENTS_DEEP_THINK_LLM=qwen3:latest
export TRADINGAGENTS_QUICK_THINK_LLM=qwen3:latest
# Optional remote host:
# export OLLAMA_BASE_URL=http://your-ollama-host:11434/v1
```

### Example 3b: Using llama.cpp (llama-server)

```bash
# Start llama-server (model path and --alias must match model IDs below):
# llama-server -m /path/to/model.gguf --alias qwen2.5-7b-instruct -ngl 99 -c 8192

export TRADINGAGENTS_LLM_PROVIDER=llama_cpp
export TRADINGAGENTS_DEEP_THINK_LLM=qwen2.5-7b-instruct
export TRADINGAGENTS_QUICK_THINK_LLM=qwen2.5-7b-instruct
# Optional remote host:
# export LLAMA_CPP_BASE_URL=http://your-llama-server:8080/v1
```

Docker profile:

```bash
# Place GGUF files in a volume mounted at /models, set LLAMA_ARG_MODEL in compose.
docker compose --profile llamacpp run --rm tradingagents-llamacpp
```

### Local inference: Ollama vs llama.cpp

Both backends use the same LangChain OpenAI-compatible client path. TradingAgents
also supports llama.cpp via `openai_compatible` + `TRADINGAGENTS_LLM_BACKEND_URL`
without the dedicated `llama_cpp` provider.

**Tune Ollama first (no code changes):**

```bash
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KV_CACHE_TYPE=q8_0
# restart ollama serve
```

**Config-only llama.cpp trial:**

1. Run `llama-server` with Metal offload on Apple Silicon (`-ngl 99`).
2. Set `TRADINGAGENTS_LLM_PROVIDER=openai_compatible` and
   `TRADINGAGENTS_LLM_BACKEND_URL=http://localhost:8080/v1`, or use
   `TRADINGAGENTS_LLM_PROVIDER=llama_cpp`.
3. Run the same intraday dry-run or premarket pass on one symbol with each backend.
4. Compare:
   - **Throughput:** llama-server logs tok/s; Ollama `ollama run` or server metrics.
   - **Wall-clock:** intraday live footer elapsed time and LLM call counts.
   - **Tokens:** live footer `Tokens:` line (wired via `StatsCallbackHandler`).

On Apple Silicon GPU, expect ~10–18% higher throughput from llama.cpp vs default
Ollama; Ollama can win on warm time-to-first-token when prompt caching helps.

**Alternative without dedicated provider:**

```bash
export TRADINGAGENTS_LLM_PROVIDER=openai_compatible
export TRADINGAGENTS_LLM_BACKEND_URL=http://localhost:8080/v1
export TRADINGAGENTS_QUICK_THINK_LLM=your-model-alias
export TRADINGAGENTS_DEEP_THINK_LLM=your-model-alias
```

### Example 4: Multi-Analyst Deep Analysis

```python
config = DEFAULT_CONFIG.copy()
config["max_debate_rounds"] = 4  # More debate rounds
config["max_risk_discuss_rounds"] = 3
config["temperature"] = 0.5  # Lower temperature = more focused

ta = TradingAgentsGraph(
    selected_analysts=("market", "sentiment", "news", "fundamentals"),
    config=config
)
state, decision = ta.propagate("NVDA", "2024-05-10")
```

### Example 5: Minimal Configuration (Fastest)

```python
config = DEFAULT_CONFIG.copy()
config["quick_think_llm"] = "gpt-4o-mini"  # Faster, cheaper
config["max_debate_rounds"] = 1  # No debate
config["max_risk_discuss_rounds"] = 0  # Skip risk discussion

ta = TradingAgentsGraph(
    selected_analysts=("market",),  # Only technical analyst
    config=config
)
```

## Adding New Configuration Parameters

To add a new configuration option (e.g., for Schwab TOS):

### Step 1: Add to DEFAULT_CONFIG

**File**: `tradingagents/default_config.py`

```python
DEFAULT_CONFIG = _apply_env_overrides({
    ...
    "schwab_api_key": os.getenv("TRADINGAGENTS_SCHWAB_API_KEY", ""),
    "schwab_account_number": os.getenv("TRADINGAGENTS_SCHWAB_ACCOUNT", ""),
})
```

### Step 2: Add Environment Variable Mapping

**File**: `tradingagents/default_config.py`

```python
_ENV_OVERRIDES = {
    ...
    "TRADINGAGENTS_SCHWAB_API_KEY":      "schwab_api_key",
    "TRADINGAGENTS_SCHWAB_ACCOUNT":      "schwab_account_number",
}
```

### Step 3: Use in Code

```python
# In your dataflow/vendor code
api_key = config["schwab_api_key"]
account = config["schwab_account_number"]

# Or access via TradingAgentsGraph
ta = TradingAgentsGraph(config=config)
schwab_key = ta.config["schwab_api_key"]
```

## Configuration Validation

The system validates configuration on startup:

1. **Type Checking**: Env vars coerced to config value types
2. **Value Range Checking**: max_debate_rounds must be >= 0
3. **API Key Checks**: CLI prompts to set missing keys
4. **Model Validation**: Warns if model unknown for provider

Errors raise at initialization, preventing silent misconfiguration.

---

**See Also**:
- [ARCHITECTURE.md](ARCHITECTURE.md) — How config flows through system
- [LLM_CLIENTS.md](LLM_CLIENTS.md) — Provider-specific configuration
- [STATE_MANAGEMENT.md](STATE_MANAGEMENT.md) — Memory log and checkpoint config
- [DATAFLOWS.md](DATAFLOWS.md) — Data vendor configuration
