# TradingAgents Architecture

## Overview

TradingAgents is a multi-agent LLM trading framework that simulates the dynamics of a real-world trading firm. The system decomposes complex trading decisions into specialized roles—analysts, researchers, traders, and risk managers—each powered by LLMs and working collaboratively through a LangGraph-based execution engine.

### Design Philosophy

- **Specialization**: Each agent has a specific expertise and role in the decision-making process
- **Collaboration**: Agents communicate through shared state (TradingMemoryLog) and structured debates
- **Determinism with LLM uncertainty**: Configuration-driven behavior with reproducible run signatures
- **Extensibility**: Pluggable data vendors, LLM providers, and agent implementations
- **Auditability**: All decisions and reflections are recorded in an append-only markdown log

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLI Entry Point (cli/main.py)            │
│  - Interactive configuration (provider, model, analysts)        │
│  - Display management with Rich                                 │
│  - Checkpoint resume support                                    │
└─────────────────┬───────────────────────────────────────────────┘
                  │
                  ↓
        ┌─────────────────────┐
        │ TradingAgentsGraph  │
        │  (trading_graph.py) │
        └──────────┬──────────┘
                   │
        ┌──────────┴──────────┐
        │                     │
        ↓                     ↓
    ┌─────────┐         ┌──────────┐
    │ LLMs    │         │ Tools &  │
    │ (Multi- │         │ Data     │
    │Provider)│         │ Sources  │
    └────┬────┘         └────┬─────┘
         │                   │
    ┌────┴───────────────────┴──┐
    │                           │
    ↓                           ↓
┌───────────────────┐   ┌────────────────┐
│  LLM Clients      │   │   Data Flows   │
│  (llm_clients/)   │   │  (dataflows/)  │
│                   │   │                │
│ - OpenAI          │   │ - Alpha Vantage│
│ - Anthropic       │   │ - Yahoo Finance│
│ - Google Gemini   │   │ - FRED         │
│ - Azure           │   │ - Polymarket   │
│ - Bedrock         │   │ - Reddit       │
└─────────────────┘   │ - StockTwits   │
                      │ - etc.         │
                      └────────────────┘
```

## Core Components

### 1. TradingAgentsGraph (Main Orchestrator)

**Location**: `tradingagents/graph/trading_graph.py`

The central class that initializes and manages the entire trading agent system.

```python
class TradingAgentsGraph:
    def __init__(
        self,
        selected_analysts=("market", "social", "news", "fundamentals"),
        debug=False,
        config: dict[str, Any] = None,
        callbacks: list | None = None,
    ):
        # Initializes:
        # - LLM clients (deep_thinking_llm, quick_thinking_llm)
        # - Memory log (TradingMemoryLog)
        # - Tool nodes (ToolNode for agent execution)
        # - Graph components (Propagator, Reflector, Propagator)
        # - LangGraph workflow and compiled graph
```

**Key Responsibilities**:
- LLM client initialization and configuration
- Graph setup and compilation
- Tool node creation (wraps agent utilities)
- Run orchestration via `propagate()` method

**Key Methods**:
- `propagate(ticker, date)` — Main execution: runs analysts, researchers, trader, and risk managers
- `reflect_and_remember(position_returns)` — Learning loop: reflects on outcomes and updates memory
- `resolve_instrument_context(ticker, date)` — Deterministic identity resolution for instruments

### 2. LangGraph Workflow

**Location**: `tradingagents/graph/setup.py`

The graph structure is built in `GraphSetup.setup_graph()` and represents the agent execution flow:

```
Input: (ticker, date, optional past_context)
  ↓
  ├─→ [Market Analyst] (if selected)
  ├─→ [Sentiment Analyst] (if selected)
  ├─→ [News Analyst] (if selected)
  ├─→ [Fundamentals Analyst] (if selected)
  ↓
  ├─→ [Bull Researcher] ⟷ [Bear Researcher] (debate loop)
  ├─→ [Research Manager] (synthesizes and recommends)
  ↓
  ├─→ [Trader] (translates recommendation to transaction)
  ↓
  ├─→ [Risk Manager Team] (Aggressive/Neutral/Conservative debate)
  ├─→ [Portfolio Manager] (final approval/rejection)
  ↓
Output: Trading decision with reasoning
```

**Graph Features**:
- **Conditional routing**: Analysts selected via config
- **Debate loops**: Bull/Bear and Aggressive/Neutral/Conservative debates with judge decisions
- **Tool availability**: Each agent has access to specific tools (data fetchers, calculations)
- **Checkpointing**: State saved at step boundaries for resumption

### 3. State Management

**Location**: `tradingagents/agents/utils/agent_states.py` and `tradingagents/agents/utils/memory.py`

The graph state includes:

```python
# Core message history
messages: list[tuple[str, str]]  # (role, content) pairs

# Execution context
company_of_interest: str
asset_type: str
trade_date: str
past_context: str
instrument_context: str

# Agent reports (outputs)
market_report: str
fundamentals_report: str
sentiment_report: str
news_report: str

# Debate state machines
investment_debate_state: InvestDebateState  # Bull/Bear debate
risk_debate_state: RiskDebateState           # Aggressive/Neutral/Conservative

# Final decisions
trader_proposal: TraderProposal  # Structured transaction proposal
portfolio_decision: PortfolioDecision  # Final approval/rejection
```

### 4. Memory Log (TradingMemoryLog)

**Location**: `tradingagents/agents/utils/memory.py`

An append-only markdown log that records:
- **Decisions**: Trading decisions and reasoning (appended during `propagate()`)
- **Reflections**: Outcome analysis and lessons learned (added during `reflect_and_remember()`)

Format:
```markdown
[2024-05-10 | NVDA | Buy | pending]

DECISION:
[agent reasoning and decision text here]

<!-- ENTRY_END -->

[After reflection phase]
[2024-05-10 | NVDA | Buy | resolved]

DECISION:
[original decision]

REFLECTION:
[outcome analysis and lessons learned]

<!-- ENTRY_END -->
```

**Key Operations**:
- `store_decision()` — Append new decision at propagation end
- `store_reflection()` — Update decision with reflection after outcome evaluation
- `load_entries()` — Parse all entries for reflection queries

### 5. Data Vendor Abstraction

**Location**: `tradingagents/dataflows/`

A vendor-agnostic interface abstracts over multiple data sources:

```python
# Core methods (from interface.py)
get_stock_data(symbol, start_date, end_date) → DataFrame
get_indicators(symbol, indicators, period) → Dict[str, Series]
get_fundamentals(symbol) → Dict[str, any]
get_news(symbol, date_range) → List[NewsItem]
get_macro_indicators(macro_indicators) → Dict[str, any]
get_prediction_markets(event_description) → Dict[str, Probability]
```

**Vendor Implementations**:
- **Alpha Vantage**: Stock prices, technical indicators, fundamentals, news
- **Yahoo Finance**: Stock prices, balance sheets, cash flows, insider transactions
- **FRED**: Macroeconomic indicators (rates, inflation, employment, growth)
- **Polymarket**: Prediction market probabilities for forward-looking events
- **Reddit/StockTwits**: Social media sentiment and discussions
- **yfinance/Alpha Vantage News**: Headline and article ingestion

**Vendor Selection Logic** (`dataflows/interface.py`):
- Vendors are tried in priority order
- Falls back to next vendor on network error or missing API key
- Raises error if core categories fail; degrades gracefully for optional categories

### 6. LLM Provider Abstraction

**Location**: `tradingagents/llm_clients/`

A factory pattern abstracts over multiple LLM providers:

```python
# Create LLM client
client = create_llm_client(
    provider="openai",  # or "anthropic", "google", "azure", "bedrock"
    model="gpt-5.5",
    base_url="https://...",  # optional, for custom endpoints
    temperature=0.7,
)

# Get LangChain LLM instance
llm = client.get_llm()
```

**Provider Features**:
- **Structured Output**: Each provider's native mode (json_schema, response_schema, tool-use)
- **Reasoning/Thinking**: Provider-specific depth control (OpenAI reasoning_effort, Google thinking_level, Anthropic effort)
- **Model Validation**: Warns if model is outside known list for provider
- **Custom Endpoints**: Support for OpenAI-compatible or self-hosted models

**Key Clients**:
- `OpenAIClient` — GPT-5.x, GPT-4.x, o1, o3 families
- `AnthropicClient` — Claude 4.x, 3.x families
- `GoogleClient` — Gemini 3.x with native thinking support
- `AzureClient` — Azure OpenAI deployment
- `BedrockClient` — AWS Bedrock with SigV4 auth

### 7. Propagation & Reflection

**Location**: `tradingagents/graph/propagation.py` and `tradingagents/graph/reflection.py`

**Propagator**: Initializes graph state and manages input data:
```python
def create_initial_state(
    company_name: str,
    trade_date: str,
    asset_type: str = "stock",
    past_context: str = "",
    instrument_context: str = "",
) → dict[str, Any]
```

**Reflector**: Implements the learning loop:
```python
def reflect(
    position_returns: float,  # realized P&L
    memory_log: TradingMemoryLog,
) → None
    # Queries memory log for past decisions
    # Uses LLM to analyze outcome
    # Updates log with reflections
```

### 8. Checkpointing

**Location**: `tradingagents/graph/checkpointer.py`

LangGraph checkpoint support for resumable runs:

```python
# Deterministic thread ID for ticker+date
thread_id = thread_id(ticker="NVDA", date="2024-05-10", signature="")

# Check if checkpoint exists
has_checkpoint(data_dir, "NVDA", "2024-05-10")

# Retrieve checkpoint step number
step = checkpoint_step(data_dir, "NVDA", "2024-05-10")

# Resume from checkpoint
graph.invoke(input_state, config={"configurable": {"thread_id": thread_id}})
```

**Benefits**:
- Resume long-running analyses without recomputing prior steps
- Useful for backtesting iterative runs
- Supports "graph-shape-aware" resumption (different analyst selections)

## Data Flow Example

### Step 1: Initialization
```python
ta = TradingAgentsGraph(
    selected_analysts=("market", "social", "news", "fundamentals"),
    config=DEFAULT_CONFIG
)
```

### Step 2: Propagate (Forward Pass)
```python
state, decision = ta.propagate("NVDA", "2024-05-10")
```

**What happens**:
1. **Instrument Resolution**: `resolve_instrument_context()` queries market data to confirm NVDA identity
2. **State Creation**: `Propagator.create_initial_state()` initializes messages and debate state
3. **Graph Execution**: LangGraph invokes the workflow:
   - Selected analysts fetch and analyze data
   - Researchers debate based on analyst reports
   - Trader makes transaction decision
   - Risk managers debate and Portfolio Manager approves/rejects
4. **Memory Log**: Decision stored as "pending" entry
5. **Return**: State dict and final decision string

### Step 3: Reflect (Learning Loop)
```python
ta.reflect_and_remember(position_returns=1250.50)  # realized P&L
```

**What happens**:
1. **Memory Load**: Loads pending decisions from log
2. **Reflection Query**: Uses LLM to analyze outcome against decision logic
3. **Log Update**: Updates entries from "pending" → "resolved" with reflection text
4. **Optional Rotation**: Prunes old entries if `memory_log_max_entries` exceeded

## Configuration Cascade

Configuration is applied in this order (later overrides earlier):

1. **Code Defaults** (`tradingagents/default_config.py`):
   ```python
   DEFAULT_CONFIG = {
       "llm_provider": "openai",
       "deep_think_llm": "gpt-5.5",
       "quick_think_llm": "gpt-4o",
       ...
   }
   ```

2. **Environment Variables** (TRADINGAGENTS_*):
   ```bash
   TRADINGAGENTS_LLM_PROVIDER=anthropic
   TRADINGAGENTS_DEEP_THINK_LLM=claude-opus-4
   ```

3. **CLI Prompts** (`cli/main.py`):
   - Interactive selection if env var not set

4. **Hard-Coded Overrides** (`main.py` or your script):
   ```python
   config = DEFAULT_CONFIG.copy()
   config["llm_provider"] = "bedrock"
   ta = TradingAgentsGraph(config=config)
   ```

## Key Design Patterns

### 1. Vendor Abstraction
Each data source implements a common interface. The router tries vendors in priority order and falls back gracefully.

**Relevant for**: Adding Schwab TOS → implement vendor interface

### 2. Agent State Machine
Debate loops use explicit state machines (`InvestDebateState`, `RiskDebateState`) with count limits and role-based routing.

**Relevant for**: Modifying agent reasoning, adding new agent types

### 3. Structured Output with Rendering
Pydantic schemas enforce output structure, but a `render_*()` function converts them back to markdown for compatibility with the rest of the system.

**Relevant for**: New agent types or output formats

### 4. Checkpointed Resumption
Graph steps are saved; resumption reuses saved state via LangGraph's `configurable["thread_id"]`.

**Relevant for**: Backtesting iterations, trade journal backfill

### 5. Append-Only Memory Log
Decisions are immutable once written; reflections augment them. Entries are tagged with date, ticker, rating, and status.

**Relevant for**: Trade journal backfill, learning loop enhancements

## Execution Modes

### Forward Propagation (Inference)
- **Trigger**: `ta.propagate(ticker, date)`
- **Output**: Trading decision string and final state
- **Use Case**: Daily trading decisions, strategy backtesting

### Reflection & Learning
- **Trigger**: `ta.reflect_and_remember(position_returns)`
- **Output**: Updated memory log with reflections
- **Use Case**: Post-trade learning, strategy refinement

## Error Handling & Resilience

### Data Vendor Failures
- Core categories (prices, fundamentals): Raise error, abort run
- Optional categories (macro, predictions): Log warning, degrade to sentinel

### LLM Failures
- Retry logic controlled by `llm_max_retries` config
- Fallback: Raise error or use cached response (depending on agent)

### Tool Execution Errors
- Wrapped in ToolNode error handling
- LLM can see error and try alternative tools

## Testing & Validation

The codebase includes extensive test coverage:

- `test_checkpoint_resume.py` — Checkpoint/resumption correctness
- `test_provider_registry.py` — Multi-provider configuration
- `test_market_data_validator.py` — Data quality validation
- `test_signal_processing.py` — Signal analysis correctness
- `test_structured_agents.py` — Structured output parsing
- And many more (see `tests/` directory)

---

**See Also**:
- [AGENTS.md](AGENTS.md) — Agent roles and reasoning
- [DATAFLOWS.md](DATAFLOWS.md) — Data integration details
- [LLM_CLIENTS.md](LLM_CLIENTS.md) — Provider configuration
- [STATE_MANAGEMENT.md](STATE_MANAGEMENT.md) — State and memory details
