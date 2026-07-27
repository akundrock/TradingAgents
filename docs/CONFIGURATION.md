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

### Magpie Intraday Controls

| Parameter | Type | Default | Env Var | Description |
|-----------|------|---------|---------|-------------|
| `magpie_intraday_loop_enabled` | bool | `False` | `TRADINGAGENTS_MAGPIE_INTRADAY_LOOP_ENABLED` | Enable recurring intraday analysis loop (RTH-guarded) |
| `magpie_intraday_loop_interval_minutes` | int | `5` | `TRADINGAGENTS_MAGPIE_INTRADAY_LOOP_INTERVAL_MINUTES` | Sleep interval between loop cycles |
| `magpie_intraday_loop_max_cycles` | int | `12` | `TRADINGAGENTS_MAGPIE_INTRADAY_LOOP_MAX_CYCLES` | Maximum loop cycles in one run |
| `magpie_intraday_fast_path_enabled` | bool | `False` | `TRADINGAGENTS_MAGPIE_INTRADAY_FAST_PATH_ENABLED` | Use full graph on cycle 1 and Magpie+Trader-only cycles afterward |

CLI overrides (when explicitly passed) take precedence over environment values:

- `--intraday/--no-intraday`
- `--intraday-interval-minutes`
- `--intraday-max-cycles`
- `--intraday-fast-path/--no-intraday-fast-path`

Related Magpie signal fields exposed in state/reporting:

- `invalidation_level`: deterministic invalidation reference from latest bar structure.
- `signal_ttl_minutes`: signal time-to-live, defaulting to one intraday bar interval.

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
# Ollama running on localhost:11434
export TRADINGAGENTS_LLM_PROVIDER=openai
export TRADINGAGENTS_DEEP_THINK_LLM=llama2
export TRADINGAGENTS_LLM_BACKEND_URL=http://localhost:11434/v1
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
