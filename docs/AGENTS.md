# TradingAgents: Agent Types and Roles

## Overview

TradingAgents implements a multi-agent system that mirrors the structure of a real-world trading firm. Each agent has a specialized role, expertise domain, and reasoning pattern. Agents communicate through a shared state dictionary and collaborate to make trading decisions.

## Agent Taxonomy

### Tier 1: Analyst Team

Analysts gather data and produce initial assessments. They are independent and run in parallel (though sequentially in the current implementation).

#### 1. Market/Technical Analyst

**Role**: Technical analysis and price action patterns

**Location**: `tradingagents/agents/analysts/market_analyst.py`

**Inputs**:
- Historical OHLCV data
- Technical indicators (MACD, RSI, Bollinger Bands, etc.)
- Trading volume and price trends

**Reasoning Pattern**:
1. Fetch OHLCV data for relevant time windows
2. Calculate technical indicators
3. Identify chart patterns (support/resistance, breakouts, trends)
4. Assess momentum and mean reversion signals
5. Produce bullish, bearish, or neutral assessment

**Output**: Natural language assessment stored in `state["market_report"]`

**Key Tools**:
- `get_stock_data()` — OHLCV prices
- `get_indicators()` — Technical indicators

#### 2. Sentiment/Social Analyst

**Role**: Market sentiment from social media and retail investor discussions

**Location**: `tradingagents/agents/analysts/sentiment_analyst.py` (alias: social_media_analyst)

**Inputs**:
- Reddit discussions and sentiment
- StockTwits chatter and bullish/bearish counts
- Social media signal aggregation

**Reasoning Pattern**:
1. Fetch Reddit threads and StockTwits posts
2. Aggregate sentiment (bullish vs bearish counts)
3. Assess retail investor enthusiasm vs skepticism
4. Evaluate signal quality and extremes
5. Produce social sentiment summary

**Output**: Natural language sentiment analysis stored in `state["sentiment_report"]`

**Key Tools**:
- `get_social_sentiment()` — StockTwits data (via dataflows)
- Reddit integration (via dataflows)

#### 3. News Analyst

**Role**: News, macroeconomic events, and forward-looking catalysts

**Location**: `tradingagents/agents/analysts/news_analyst.py`

**Inputs**:
- Recent news headlines and articles
- Macroeconomic indicators (Fed rates, inflation, employment)
- Corporate events (earnings, guidance, M&A)
- Global events and geopolitical factors

**Reasoning Pattern**:
1. Fetch recent news for the company
2. Fetch macro indicators (FRED: rates, CPI, unemployment, GDP growth)
3. Assess news sentiment and event significance
4. Evaluate macro environment impact
5. Identify forward catalysts and event risk

**Output**: Natural language news and macro analysis stored in `state["news_report"]`

**Key Tools**:
- `get_news()` — Recent news articles
- `get_global_news()` — Broader market news
- `get_macro_indicators()` — Economic data (rates, inflation, etc.)

#### 4. Fundamentals Analyst

**Role**: Company financials, valuation, and intrinsic value

**Location**: `tradingagents/agents/analysts/fundamentals_analyst.py`

**Inputs**:
- Balance sheet (assets, liabilities, equity)
- Income statement (revenue, earnings, margins)
- Cash flow statement (operating, investing, financing)
- Key ratios (P/E, P/B, debt-to-equity)
- Insider transactions and changes in ownership

**Reasoning Pattern**:
1. Fetch financial statements (last 4 quarters, annual)
2. Calculate key metrics (earnings growth, margin trends, ROE)
3. Estimate intrinsic value (DCF, comparable multiples)
4. Assess balance sheet strength and cash position
5. Analyze insider activity (buying vs selling)
6. Evaluate financial health and growth prospects

**Output**: Natural language fundamentals assessment stored in `state["fundamentals_report"]`

**Key Tools**:
- `get_fundamentals()` — Quick fundamental snapshot
- `get_income_statement()` — Revenue, earnings, margins
- `get_balance_sheet()` — Assets, liabilities, equity
- `get_cashflow()` — Cash flow metrics
- `get_insider_transactions()` — Insider buying/selling

### Tier 2: Researcher Team (Debate & Synthesis)

Researchers take analyst reports and engage in structured debate to synthesize balanced views. Two independent researchers (Bull and Bear) present opposing perspectives, then a judge (Research Manager) synthesizes the debate.

#### 1. Bull Researcher

**Role**: Construct the most compelling bullish case

**Location**: `tradingagents/agents/researchers/bullish_researcher.py`

**Inputs**:
- All analyst reports (market, sentiment, news, fundamentals)
- Prior debate history (if in a debate loop)
- Bear researcher's previous argument (if debating)

**Reasoning Pattern**:
1. Read all analyst reports
2. Identify strongest bullish points from each analyst
3. Build a coherent bull case:
   - Technical: Breakout, trend, momentum confirmation
   - Sentiment: Retail enthusiasm, social signal strength
   - News: Positive catalysts, macro tailwinds, event upside
   - Fundamentals: Growth prospects, valuation discount, margin expansion
4. Anticipate and address bear counterarguments
5. Estimate upside target and timeline

**Output**: Bullish argument text, stored in debate state

#### 2. Bear Researcher

**Role**: Construct the most compelling bearish case

**Location**: `tradingagents/agents/researchers/bearish_researcher.py`

**Inputs**:
- All analyst reports (market, sentiment, news, fundamentals)
- Prior debate history
- Bull researcher's previous argument (if debating)

**Reasoning Pattern**:
1. Read all analyst reports
2. Identify strongest bearish points
3. Build a coherent bear case:
   - Technical: Resistance, reversal patterns, momentum divergence
   - Sentiment: Retail euphoria (contrarian signal), extremes
   - News: Negative catalysts, macro headwinds, event downside risk
   - Fundamentals: Valuation stretched, earnings slowing, debt rising
4. Anticipate and address bull counterarguments
5. Estimate downside risk and timeline

**Output**: Bearish argument text, stored in debate state

#### 3. Research Manager (Investment Decision)

**Role**: Judge the debate and synthesize an investment plan

**Location**: `tradingagents/agents/researchers/research_manager.py`

**Inputs**:
- All analyst reports
- Bull researcher argument
- Bear researcher argument
- Debate history (if multiple rounds)

**Reasoning Pattern**:
1. Read all four analyst reports
2. Evaluate both bull and bear arguments on merit
3. Assess which side has stronger evidence
4. Synthesize balanced view (not a simple average, but weighted by argument strength)
5. Produce structured investment recommendation:
   - **Rating** (Buy / Overweight / Hold / Underweight / Sell)
   - **Rationale** (explain which arguments prevailed)
   - **Strategic Actions** (concrete steps for trader)

**Output**: Structured `ResearchPlan` (Pydantic model) + rendered markdown

**Structured Output** (from `tradingagents/agents/schemas.py`):
```python
class ResearchPlan(BaseModel):
    recommendation: PortfolioRating  # Buy/Overweight/Hold/Underweight/Sell
    rationale: str                   # Summary of arguments + why this rating
    strategic_actions: str           # Concrete trader instructions
```

### Tier 3: Execution Agents

#### Trader Agent

**Role**: Convert research recommendation into concrete transaction

**Location**: `tradingagents/agents/trader/trader_agent.py`

**Inputs**:
- Research Manager's investment plan (rating + rationale + actions)
- All analyst reports (for context)
- Current market snapshot (price, volatility)

**Reasoning Pattern**:
1. Read research plan and analyst reports
2. Understand the bull/bear case and conviction level
3. Translate rating into action:
   - Buy/Overweight → BUY transaction
   - Hold → HOLD position
   - Underweight/Sell → SELL transaction
4. Determine practical entry/exit levels:
   - Entry price (buy/sell limit)
   - Stop-loss price (loss limit)
   - Position size (% of portfolio)
5. Produce structured transaction proposal

**Output**: Structured `TraderProposal` (Pydantic model) + rendered markdown

**Structured Output**:
```python
class TraderProposal(BaseModel):
    action: TraderAction           # Buy / Hold / Sell
    reasoning: str                 # Why this action
    entry_price: float | None      # Suggested entry
    stop_loss: float | None        # Stop-loss level
    position_sizing: str | None    # E.g., "5% of portfolio"
```

### Tier 4: Risk Management & Portfolio Approval

#### Risk Management Agents (Debate)

Three agents debate risk from aggressive, neutral, and conservative perspectives:

1. **Aggressive Risk Manager**: "This trade has favorable risk/reward; execute it"
2. **Neutral Risk Manager**: "Balanced assessment; consider market conditions"
3. **Conservative Risk Manager**: "Manage downside risk; consider size limits"

**Location**: `tradingagents/agents/managers/risk_manager.py`

**Inputs**:
- Trader's proposal
- All analyst reports
- Market volatility and liquidity metrics
- Portfolio existing position (if any)

**Reasoning Pattern**:
- **Aggressive**: Emphasize upside opportunity, volatility as positive
- **Neutral**: Assess risk/reward objectively, check portfolio balance
- **Conservative**: Focus on downside protection, position sizing, hedging

**Output**: Risk assessment text, stored in debate state

#### Portfolio Manager (Final Approval)

**Role**: Final decision: approve or reject the proposed trade

**Location**: `tradingagents/agents/managers/portfolio_manager.py`

**Inputs**:
- Trader's proposal
- Risk management debate
- Current portfolio state

**Reasoning Pattern**:
1. Review proposed transaction
2. Weigh risks raised in risk debate
3. Check portfolio allocation and constraints
4. Make final decision: APPROVE or REJECT
5. Provide rationale and any conditions

**Output**: Structured `PortfolioDecision` (Pydantic model) + rendered markdown

**Structured Output**:
```python
class PortfolioDecision(BaseModel):
    action: Literal["APPROVE", "REJECT"]  # Final decision
    rationale: str                        # Why approve/reject
    conditions: str | None                # Any constraints or conditions
```

## Agent Communication Patterns

### 1. Direct Message History
Agents read all prior messages in `state["messages"]` to understand conversation history.

```python
state["messages"] = [
    ("human", "NVDA"),  # Initial ticker
    ("analyst_market", "Technical analysis shows..."),
    ("analyst_sentiment", "Retail sentiment is bullish..."),
    ...
]
```

### 2. Report Aggregation
Each analyst produces a report string stored in state:
```python
state["market_report"]       # Market analyst output
state["sentiment_report"]    # Sentiment analyst output
state["news_report"]         # News analyst output
state["fundamentals_report"] # Fundamentals analyst output
```

Downstream agents read ALL reports to maintain context.

### 3. Debate State Machines
Bull/Bear and Risk debates maintain explicit state:

```python
class InvestDebateState:
    bull_history: str                # All prior bull arguments
    bear_history: str                # All prior bear arguments
    history: str                     # Combined debate history
    current_response: str            # Latest judge ruling
    judge_decision: str              # Final research manager synthesis
    count: int                       # Debate round number

class RiskDebateState:
    aggressive_history: str          # All aggressive risk assessments
    conservative_history: str        # All conservative assessments
    neutral_history: str             # Neutral assessments
    history: str                     # Combined history
    latest_speaker: str              # Who spoke last
    judge_decision: str              # Portfolio manager ruling
    count: int                       # Debate round number
```

### 4. Conditional Routing
Graph routes to agents based on:
- `selected_analysts` config (which analysts to run)
- Debate round count limits (`max_debate_rounds`, `max_risk_discuss_rounds`)
- Explicit decisions (e.g., Research Manager ends investment debate)

## Agent Prompting Approach

### Persona-Based Prompts
Each agent receives a system prompt that establishes their role:

```
You are a [role] for a trading firm. Your expertise is [domain].

Your responsibilities:
1. Analyze [input data]
2. Identify [key patterns/signals]
3. Produce [output format]

Your tone should be [professional/conversational/confident].
```

### Few-Shot Examples
Some agents receive examples of high-quality output to guide reasoning.

### Context Injection
Agent prompts include:
- Company/instrument being analyzed
- Trading date and time window
- Relevant historical context
- Analyst reports (for researchers and decision-makers)

### Debate Structure
Debate prompts explicitly:
- Ask for strongest argument on one side
- Provide opposing argument as context
- Request balanced rebuttals
- Establish evaluation criteria (evidence quality, logical consistency)

## Structured Output Pattern

**Design Goal**: Enforce output consistency while maintaining prose-first philosophy.

### Implementation

1. **Pydantic Schema Definition** (`tradingagents/agents/schemas.py`):
   ```python
   class ResearchPlan(BaseModel):
       recommendation: PortfolioRating = Field(description="...")
       rationale: str = Field(description="...")
       strategic_actions: str = Field(description="...")
   ```

2. **Provider-Specific Structured Output Mode**:
   - **OpenAI**: `json_schema` (function_calling-style)
   - **Anthropic**: `tool_use` (tool_choice="auto")
   - **Google**: `response_schema` (native Gemini mode)

3. **Schema Field Descriptions as Instructions**:
   - Field descriptions are the LLM's actual output instructions
   - Prompt body focuses on context and reasoning guidance
   - Decouples prompt changes from schema changes

4. **Rendering Back to Markdown**:
   ```python
   def render_research_plan(plan: ResearchPlan) -> str:
       return "\n".join([
           f"**Recommendation**: {plan.recommendation.value}",
           f"**Rationale**: {plan.rationale}",
           f"**Strategic Actions**: {plan.strategic_actions}",
       ])
   ```

**Benefits**:
- Enforces consistent section headers across all runs
- Allows downstream agents to reliably parse sections
- Works with memory log and markdown reporting
- Each provider uses its native mode (no JSON parsing overhead)

## Agent Failures & Resilience

### Tool Execution Failures
If a tool (data fetcher) fails:
- Error is caught and returned to agent
- Agent can see error message and try alternative tools
- If all tools fail, agent continues with available data

### LLM Failures
If an LLM call fails:
- Retry logic (controlled by `llm_max_retries`)
- If retry budget exhausted, error bubbles up and aborts run

### Data Quality Issues
If data is invalid or missing:
- `MarketDataValidator` checks OHLCV data quality
- `market_data_validator.py` catches stale or malformed data
- Agent receives warning but can continue (graceful degradation)

## Customization Points for Your Modifications

### Adding Magpie Strategy (Trading Agent Modification)
1. Review `tradingagents/agents/trader/trader_agent.py`
2. Understand current decision logic (convert Research Manager plan → TraderProposal)
3. Extend Trader to:
   - Evaluate Magpie signals alongside analyst reports
   - Weight Magpie signals per configuration
   - Produce transaction from combined signal+research plan
4. Optionally create new agent layer for strategy-specific logic

### Backtest Harness (New Reasoning Layer)
1. Extend Risk Management agents to:
   - Evaluate backtesting metrics (Sharpe, drawdown, win rate)
   - Adjust position sizing based on historical performance
2. Create new agent for backtesting analysis
3. Modify memory log to store backtest-specific metrics

### Trade Journal Integration (State Extension)
1. Extend state dict with `execution_history` field
2. Backfill execution_history from journal
3. Modify agents to reference journal when making decisions
4. Update memory log to correlate decisions with actual executions

---

**See Also**:
- [ARCHITECTURE.md](ARCHITECTURE.md) — System flow and graph structure
- [STATE_MANAGEMENT.md](STATE_MANAGEMENT.md) — State handling and memory log details
- [DATAFLOWS.md](DATAFLOWS.md) — Data tools available to agents
