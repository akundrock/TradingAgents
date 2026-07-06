# TradingAgents: State Management and Memory

## Overview

State management in TradingAgents involves three interconnected systems:

1. **Graph State** — LangGraph's runtime state dictionary passed between agents
2. **Memory Log** — Persistent append-only markdown log of decisions and reflections
3. **Checkpointing** — SQLite-backed resumption mechanism for long-running analyses

## Graph State Structure

### State Dictionary

The core graph state is a Python dictionary that flows through all agents:

```python
state = {
    # Core execution context
    "messages": [
        ("human", "NVDA"),           # Initial ticker input
        ("analyst", "Market report..."),
        ...
    ],
    "company_of_interest": "NVDA",   # Ticker or company name
    "asset_type": "stock",           # stock, crypto, etf, etc.
    "trade_date": "2024-05-10",      # Analysis date
    "past_context": "",              # Historical context for learning
    "instrument_context": "NVIDIA Corporation (NASDAQ: NVDA)",  # Full identifier
    
    # Agent reports (outputs)
    "market_report": "",             # Technical analyst output
    "fundamentals_report": "",       # Fundamentals analyst output
    "sentiment_report": "",          # Sentiment analyst output
    "news_report": "",               # News analyst output
    
    # Debate state machines
    "investment_debate_state": {
        "bull_history": "",          # All bull researcher arguments
        "bear_history": "",          # All bear researcher arguments
        "history": "",               # Combined debate record
        "current_response": "",      # Latest response from current speaker
        "judge_decision": "",        # Research Manager's synthesis
        "count": 0,                  # Debate round counter
    },
    "risk_debate_state": {
        "aggressive_history": "",    # Aggressive risk manager arguments
        "conservative_history": "",  # Conservative risk manager arguments
        "neutral_history": "",       # Neutral risk manager arguments
        "history": "",               # Combined debate record
        "latest_speaker": "",        # Who spoke last
        "current_aggressive_response": "",
        "current_conservative_response": "",
        "current_neutral_response": "",
        "judge_decision": "",        # Portfolio Manager ruling
        "count": 0,                  # Debate round counter
    },
    
    # Structured outputs
    "trader_proposal": None,         # TraderProposal Pydantic instance
    "portfolio_decision": None,      # PortfolioDecision Pydantic instance
}
```

### State Evolution

As the graph executes, agents update state:

```
Initial State
    ↓ [Market Analyst runs]
    ├→ state["market_report"] = "Technical analysis..."
    ├→ state["messages"].append(("analyst_market", "..."))
    ↓ [Fundamentals Analyst runs]
    ├→ state["fundamentals_report"] = "Valuation analysis..."
    ├→ state["messages"].append(("analyst_fundamentals", "..."))
    ↓ [Bull/Bear Debate]
    ├→ state["investment_debate_state"]["bull_history"] = "..."
    ├→ state["investment_debate_state"]["bear_history"] = "..."
    ↓ [Research Manager]
    ├→ state["investment_debate_state"]["judge_decision"] = "Buy"
    ├→ state["trader_proposal"] = TraderProposal(...)
    ↓ [Trader Agent]
    ├→ state["trader_proposal"] updated with entry/exit levels
    ↓ [Risk Debate]
    ├→ state["risk_debate_state"][...] updated with assessments
    ↓ [Portfolio Manager]
    ├→ state["portfolio_decision"] = PortfolioDecision(action="APPROVE", ...)
    ↓
Final State (persisted to checkpoint if enabled)
```

## Debate State Machines

### Investment Debate State

Manages Bull/Bear debate for investment recommendation:

```python
class InvestDebateState:
    """Structured state for investment recommendation debate."""
    
    bull_history: str = ""          # All bull arguments accumulated
    bear_history: str = ""          # All bear arguments accumulated
    history: str = ""               # Full debate transcript
    current_response: str = ""      # Latest speaker's argument
    judge_decision: str = ""        # Research Manager's synthesis
    count: int = 0                  # Round counter (0-based)
```

**Debate Flow**:
```
Round 0:
  Bull Researcher speaks → state["investment_debate_state"]["bull_history"] += argument
  Bear Researcher speaks → state["investment_debate_state"]["bear_history"] += argument

Round 1+:
  Bull reads bear's argument → rebuts
  Bear reads bull's argument → rebuts
  Judge (Research Manager) evaluates → sets judge_decision

Exit condition:
  count >= max_debate_rounds → Judge's decision becomes final
```

### Risk Debate State

Manages Aggressive/Neutral/Conservative debate for risk assessment:

```python
class RiskDebateState:
    """Structured state for risk management debate."""
    
    aggressive_history: str = ""         # Aggressive manager arguments
    conservative_history: str = ""       # Conservative manager arguments
    neutral_history: str = ""            # Neutral manager arguments
    history: str = ""                    # Full debate transcript
    latest_speaker: str = ""             # Last agent to speak
    current_aggressive_response: str = ""
    current_conservative_response: str = ""
    current_neutral_response: str = ""
    judge_decision: str = ""             # Portfolio Manager ruling
    count: int = 0                       # Debate round counter
```

**Debate Flow**:
```
Round 0:
  Aggressive Manager speaks
  Neutral Manager speaks
  Conservative Manager speaks

Round 1+:
  Each manager rebuts prior arguments

Exit condition:
  count >= max_risk_discuss_rounds → Judge's decision becomes final
```

## Memory Log (TradingMemoryLog)

### Purpose

An append-only markdown log that records:
1. **Decisions** — Trading decisions and reasoning (written during `propagate()`)
2. **Reflections** — Outcome analysis and learning (added during `reflect_and_remember()`)

Serves as:
- **Audit trail** — Record of all trading decisions
- **Learning history** — What worked, what didn't
- **Context for future decisions** — Agents can query log to learn from past

### Location

**File**: `tradingagents/agents/utils/memory.py`

### Log Format

Each entry has three components:

```markdown
[2024-05-10 | NVDA | Buy | pending]

DECISION:
The technical analysis shows a breakout above resistance at $120.50.
The fundamentals analyst reports strong earnings growth (15% YoY).
The Research Manager recommends Buy with conviction.
The Trader proposes entry at $121, stop-loss at $118, position size 5%.

<!-- ENTRY_END -->

[2024-05-15 | NVDA | Buy | resolved]

DECISION:
[original decision text, unchanged]

REFLECTION:
The trade was executed at $121 and exited at $127.50, realizing +$650 profit.
This validates the technical breakout signal and the conviction level.
For future references: breakouts combined with fundamental strength are reliable.
Consider increasing position size for similar setups by 1-2%.

<!-- ENTRY_END -->
```

### Entry Format Breakdown

**Header Tag**:
```
[DATE | TICKER | RATING | STATUS]
```
- `DATE` — Trade date (YYYY-MM-DD)
- `TICKER` — Instrument symbol
- `RATING` — Decision type (Buy, Overweight, Hold, Underweight, Sell)
- `STATUS` — pending (just decided) or resolved (outcome analyzed)

**DECISION Section**:
- Full reasoning and decision output from agents
- Includes analyst reports, debate summaries, trader/PM reasoning
- Immutable once written

**REFLECTION Section** (added later):
- Outcome analysis comparing prediction to actual results
- Lessons learned and pattern insights
- Only added when `reflect_and_remember()` is called

**Separator**:
- HTML comment `<!-- ENTRY_END -->` (safe delimiter, won't appear in LLM output)

### API

**Location**: `tradingagents/agents/utils/memory.py`

```python
class TradingMemoryLog:
    """Append-only markdown decision log."""
    
    def store_decision(
        self,
        ticker: str,
        trade_date: str,
        final_trade_decision: str,
    ) → None:
        """Append decision entry at end of propagate().
        Creates entry with status='pending'."""
    
    def store_reflection(
        self,
        ticker: str,
        trade_date: str,
        reflection_text: str,
    ) → None:
        """Update pending entry with reflection.
        Changes status to 'resolved'."""
    
    def load_entries(self) → list[dict]:
        """Parse all entries from log.
        Returns list of:
        {
            'date': '2024-05-10',
            'ticker': 'NVDA',
            'rating': 'Buy',
            'status': 'pending' or 'resolved',
            'decision': '...',
            'reflection': '...' (or None),
        }"""
    
    def query_for_reflection(
        self,
        position_returns: float,
    ) → str:
        """Prepare memory log entries for reflection LLM query.
        Formats recent pending entries for outcome analysis."""
```

### Configuration

**Location**: `tradingagents/default_config.py`

```python
DEFAULT_CONFIG = {
    "memory_log_path": os.path.join(
        _TRADINGAGENTS_HOME, "memory", "trading_memory.md"
    ),
    # Optional cap on resolved entries. When exceeded, oldest resolved
    # entries are pruned. Pending entries are never pruned. None disables.
    "memory_log_max_entries": None,
}
```

**Environment Variable**:
```bash
TRADINGAGENTS_MEMORY_LOG_PATH=~/.tradingagents/memory/trading_memory.md
TRADINGAGENTS_MEMORY_LOG_MAX_ENTRIES=1000  # Optional rotation limit
```

## Reflection and Learning Loop

The reflection system lets agents learn from outcomes and refine future decisions.

### Reflection Flow

1. **Decision Phase** (`propagate()`):
   - Agents make trading decision
   - Decision stored to memory log with status="pending"

2. **Outcome Evaluation** (external, post-trade):
   - Trade is executed in paper/live account
   - Actual price movement and P&L recorded

3. **Reflection Phase** (`reflect_and_remember()`):
   ```python
   ta.reflect_and_remember(position_returns=1250.50)  # Realized P&L
   ```
   - Memory log queries pending entries
   - Reflector LLM analyzes: Did decision logic hold up?
   - Reflection stored to log with status="resolved"

4. **Learning** (agents' future context):
   - Agents optionally read past_context containing summary of reflections
   - Patterns inform future decisions

### Reflection Implementation

**Location**: `tradingagents/graph/reflection.py`

```python
class Reflector:
    """Implements learning loop via reflection."""
    
    def reflect(
        self,
        position_returns: float,
        memory_log: TradingMemoryLog,
    ) → None:
        """Analyze outcome and update memory log.
        
        Args:
            position_returns: Realized profit/loss from executed trade
            memory_log: TradingMemoryLog instance to read/update
        """
        # Load pending entries from log
        entries = memory_log.load_entries()
        pending = [e for e in entries if e["status"] == "pending"]
        
        for entry in pending:
            # Format entry for reflection prompt
            reflection_prompt = f"""
            Trading Decision (from {entry['date']}):
            {entry['decision']}
            
            Outcome:
            Position returned: {position_returns}
            
            Reflection Questions:
            1. Did the analysis correctly identify the key drivers?
            2. Were there surprises in the outcome?
            3. What should we do differently next time?
            4. What patterns should we watch for?
            """
            
            # Use LLM to reflect
            reflection_text = self.quick_thinking_llm.invoke(reflection_prompt)
            
            # Update log with reflection
            memory_log.store_reflection(
                ticker=entry["ticker"],
                trade_date=entry["date"],
                reflection_text=reflection_text.content,
            )
```

### Using Reflections in Future Decisions

Agents can include past reflections in their context:

```python
# In any agent's prompt:
past_reflections = memory_log.query_for_reflection(...)

prompt_with_context = f"""
You are making a trading decision for {ticker}.

Past decisions and reflections on similar setups:
{past_reflections}

Current analysis:
[current analyst reports]

Based on what we've learned, recommend an action.
"""
```

## Checkpointing and Resumption

LangGraph supports saving and resuming graph execution via checkpoints.

### Checkpoint Architecture

**Location**: `tradingagents/graph/checkpointer.py`

Checkpoints are stored in **per-ticker SQLite databases** in the cache directory:

```
~/.tradingagents/cache/
├── checkpoints/
│   ├── NVDA.db         # Checkpoints for NVDA
│   ├── TSLA.db         # Checkpoints for TSLA
│   └── ...
```

Per-ticker databases prevent **concurrent ticker analysis from contending** on a single lock.

### Checkpoint Entry

Each checkpoint saves:
- **State dict** — Complete graph state at step N
- **Thread ID** — Deterministic identifier for ticker+date+graph-shape
- **Step number** — Which step was completed

### Thread ID

A deterministic, graph-shape-aware identifier:

```python
def thread_id(ticker: str, date: str, signature: str = "") -> str:
    """Deterministic thread ID for ticker+date pair.
    
    signature: Folds in graph-shape-affecting choices (selected analysts)
    so a resume under a different graph can't reuse the checkpoint.
    """
    base = f"{ticker.upper()}:{date}"
    if signature:
        base = f"{base}:{signature}"
    return hashlib.sha256(base.encode()).hexdigest()[:16]
```

**Example**:
```python
thread_id("NVDA", "2024-05-10")  # "a1b2c3d4e5f6g7h8"
# Same result on every call (deterministic)

# Different analysts selected → different signature → different thread ID
thread_id("NVDA", "2024-05-10", signature="market,news")  # "f9e8d7c6b5a4..."
```

### Checkpoint API

```python
# Check if checkpoint exists
has_checkpoint(data_dir, "NVDA", "2024-05-10")  # → True/False

# Get checkpoint step number
step = checkpoint_step(data_dir, "NVDA", "2024-05-10")  # → 3 or None

# Resume from checkpoint
config = {"configurable": {"thread_id": thread_id("NVDA", "2024-05-10")}}
state = graph.invoke(input_state, config=config)
# Graph starts from saved step, skipping completed steps

# Clear all checkpoints
clear_all_checkpoints(data_dir)  # → number of files deleted
```

### Resumption Example

```python
ta = TradingAgentsGraph(config=config)

# First run: analyze NVDA on 2024-05-10
state, decision = ta.propagate("NVDA", "2024-05-10")
# If interrupted at step 3 (after Market Analyst):
# Checkpoint saved with state at that step

# Later, resume the same analysis
# On second call, graph resumes from saved state
state, decision = ta.propagate("NVDA", "2024-05-10")
# Skips Market Analyst (already run), continues from Fundamentals Analyst
```

### Checkpoint Configuration

**Location**: `tradingagents/default_config.py`

```python
DEFAULT_CONFIG = {
    "checkpoint_enabled": True,  # Enable/disable checkpointing
    "data_cache_dir": ...,       # Where checkpoints are stored
}
```

**Environment Variable**:
```bash
TRADINGAGENTS_CHECKPOINT_ENABLED=true
TRADINGAGENTS_CACHE_DIR=~/.tradingagents/cache
```

### Graph-Shape-Aware Resumption

If the graph shape changes (different analysts selected), resumption is **disabled** for safety:

```python
# First run with selected_analysts=("market", "news")
ta1 = TradingAgentsGraph(selected_analysts=("market", "news"))
state1, _ = ta1.propagate("NVDA", "2024-05-10")

# Second run with different analysts
ta2 = TradingAgentsGraph(selected_analysts=("market", "sentiment", "news"))
state2, _ = ta2.propagate("NVDA", "2024-05-10")
# This creates a NEW checkpoint (different graph shape)
# Won't reuse old checkpoint from different analyst config
```

## Conditional Logic and Routing

**Location**: `tradingagents/graph/conditional_logic.py`

Conditional logic determines:
- When debate loops exit
- Which agents to route to
- When to move to next stage

```python
class ConditionalLogic:
    """Encapsulates routing and debate round logic."""
    
    def __init__(
        self,
        max_debate_rounds: int = 2,
        max_risk_discuss_rounds: int = 2,
    ):
        self.max_debate_rounds = max_debate_rounds
        self.max_risk_discuss_rounds = max_risk_discuss_rounds
    
    def should_continue_invest_debate(self, state: dict) -> bool:
        """Exit investment debate if: (judge has decided) or (reached max rounds)"""
        debate_state = state["investment_debate_state"]
        return (
            debate_state["count"] < self.max_debate_rounds
            and not debate_state["judge_decision"]
        )
    
    def should_continue_risk_debate(self, state: dict) -> bool:
        """Exit risk debate if: (PM has decided) or (reached max rounds)"""
        risk_state = state["risk_debate_state"]
        return (
            risk_state["count"] < self.max_risk_discuss_rounds
            and not risk_state["judge_decision"]
        )
```

## Signal Processing

**Location**: `tradingagents/graph/signal_processing.py`

Extracts trading signals from analyst reports and debate outcomes:

```python
class SignalProcessor:
    """Extracts and scores trading signals from agent outputs."""
    
    def process(self, state: dict) → dict:
        """Extract signals from all analyst reports and debate.
        
        Returns:
        {
            'technical_signal': 'bullish' | 'neutral' | 'bearish',
            'sentiment_signal': ...,
            'news_signal': ...,
            'fundamental_signal': ...,
            'consensus': 'strong_buy' | 'buy' | 'hold' | 'sell' | 'strong_sell',
            'signal_strength': 0.0-1.0,  # Confidence
        }
        """
```

## State for Your Modifications

### Trade Journal Backfill

Add to state:

```python
state["execution_history"] = [
    {
        "date": "2024-05-09",
        "ticker": "NVDA",
        "action": "BUY",
        "price": 121.00,
        "quantity": 100,
        "commission": 5.00,
    },
    ...
]
```

Agents reference journal:
```
Historical executions for this symbol:
- 2024-05-09: BUY 100 @ $121 (avg: $121.05 incl. commission)
- 2024-04-15: SELL 50 @ $118 (closed at loss)

Current position: 50 shares at avg cost $121.05
```

### Backtest Harness

Add to state:

```python
state["backtest_metrics"] = {
    "sharpe_ratio": 1.2,
    "max_drawdown": 0.12,
    "win_rate": 0.65,
    "avg_win": 250.0,
    "avg_loss": 150.0,
}
```

Risk managers factor in historical performance.

### Magpie Strategy Integration

Add to state:

```python
state["magpie_signals"] = {
    "signal": "BUY",
    "confidence": 0.8,
    "reasoning": "Pattern recognized...",
}
```

Trader weights Magpie signals alongside analyst reports.

---

**See Also**:
- [ARCHITECTURE.md](ARCHITECTURE.md) — System flow
- [AGENTS.md](AGENTS.md) — How agents update state
- [CONFIGURATION.md](CONFIGURATION.md) — Config for memory and checkpointing
