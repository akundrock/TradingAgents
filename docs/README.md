# TradingAgents Documentation

Welcome to the TradingAgents documentation. This directory contains comprehensive guides to understanding and extending the TradingAgents multi-agent LLM trading framework.

## Table of Contents

### Core Documentation

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — System design, agent orchestration, data flow, and key design patterns
  - Best starting point for understanding how the system works end-to-end
  - LangGraph integration and graph execution model
  - Key components and their responsibilities

- **[AGENTS.md](AGENTS.md)** — Agent types, roles, and structured output patterns
  - Analyst team (Market, Sentiment, News, Fundamentals)
  - Researcher team (Bull/Bear debaters and Research Manager)
  - Trader, Portfolio Manager, and Risk Management agents
  - Prompt templates and reasoning flows

- **[DATAFLOWS.md](DATAFLOWS.md)** — Data integration architecture and vendor patterns
  - Data vendor abstraction interface
  - Current integrations (Alpha Vantage, Yahoo Finance, FRED, Polymarket, Reddit, etc.)
  - How to add new data sources (e.g., Schwab TOS)
  - Instrument identity resolution and market data validation

- **[LLM_CLIENTS.md](LLM_CLIENTS.md)** — Multi-provider LLM support and abstraction
  - Provider implementations (OpenAI, Anthropic, Google, Azure, Bedrock)
  - Structured output patterns per provider
  - Model catalog and validation
  - Configuration and provider selection

- **[STATE_MANAGEMENT.md](STATE_MANAGEMENT.md)** — Graph state, memory management, and checkpointing
  - TradingMemoryLog: Append-only decision log with reflection support
  - LangGraph state structure and agent communication patterns
  - Checkpoint/resume mechanics for run resumption
  - Signal processing and conditional logic routing

- **[CONFIGURATION.md](CONFIGURATION.md)** — Configuration system and customization
  - Config cascade: defaults → environment variables → CLI → hard-coded overrides
  - All configurable parameters with their purposes
  - Environment variable mappings (TRADINGAGENTS_*)
  - How configuration flows through the system

## Quick Navigation by Task

### I want to understand...

- **How the system works overall** → Start with [ARCHITECTURE.md](ARCHITECTURE.md)
- **How agents think and make decisions** → See [AGENTS.md](AGENTS.md)
- **Where data comes from and how to add new sources** → Read [DATAFLOWS.md](DATAFLOWS.md)
- **How agents are configured to use different LLMs** → Check [LLM_CLIENTS.md](LLM_CLIENTS.md)
- **How state flows through the system and persists** → Study [STATE_MANAGEMENT.md](STATE_MANAGEMENT.md)
- **How to configure TradingAgents** → Refer to [CONFIGURATION.md](CONFIGURATION.md)

### I want to modify/add...

- **A new data vendor (e.g., Schwab TOS)** → [DATAFLOWS.md](DATAFLOWS.md) + [STATE_MANAGEMENT.md](STATE_MANAGEMENT.md)
- **A trading strategy (e.g., Magpie)** → [AGENTS.md](AGENTS.md) + [ARCHITECTURE.md](ARCHITECTURE.md)
- **Backtest/journal features** → [STATE_MANAGEMENT.md](STATE_MANAGEMENT.md) + [CONFIGURATION.md](CONFIGURATION.md)
- **LLM provider support** → [LLM_CLIENTS.md](LLM_CLIENTS.md)
- **Agent behavior or prompts** → [AGENTS.md](AGENTS.md)

## Codebase Structure Reference

```
tradingagents/
├── agents/              # Agent implementations
│   ├── analysts/        # Analyst agents (Market, Sentiment, News, Fundamentals)
│   ├── researchers/     # Researcher agents (Bull, Bear, Research Manager)
│   ├── trader/          # Trader agent
│   ├── managers/        # Risk Manager, Portfolio Manager
│   ├── utils/           # Shared utilities (agent_utils, memory, schemas)
│   └── schemas.py       # Pydantic structured output schemas
├── graph/               # LangGraph orchestration
│   ├── trading_graph.py # Main TradingAgentsGraph class
│   ├── setup.py         # Graph construction
│   ├── checkpointer.py  # Checkpoint/resume support
│   ├── propagation.py   # State initialization
│   ├── reflection.py    # Reflection/learning loop
│   ├── signal_processing.py  # Market signal analysis
│   └── conditional_logic.py  # Routing decisions
├── dataflows/           # Data vendor integrations
│   ├── interface.py      # Abstract vendor interface
│   ├── alpha_vantage.py  # Alpha Vantage provider
│   ├── y_finance.py      # Yahoo Finance provider
│   ├── fred.py           # FRED economic data
│   ├── polymarket.py     # Prediction markets
│   ├── reddit.py         # Reddit sentiment
│   ├── stocktwits.py     # StockTwits data
│   └── ...               # Other vendors
├── llm_clients/         # LLM provider implementations
│   ├── base_client.py    # Abstract LLM client
│   ├── factory.py        # Provider factory
│   ├── openai_client.py  # OpenAI implementation
│   ├── anthropic_client.py  # Anthropic implementation
│   ├── google_client.py   # Google Gemini implementation
│   └── ...               # Other providers
├── default_config.py    # Configuration system
├── reporting.py         # Markdown report generation
└── __init__.py          # Package initialization + .env loading

cli/
├── main.py              # CLI entry point (Typer app)
├── models.py            # CLI configuration models
├── config.py            # CLI configuration logic
└── utils.py             # CLI interaction helpers

tests/                   # Test suite
└── test_*.py            # Individual test files
```

## Key Concepts

### Agents
TradingAgents simulates a trading firm with specialized LLM-powered agents that collaborate to make trading decisions. Each agent has a specific role and reasoning pattern.

### Data Flows
The framework uses a vendor-abstraction pattern to integrate multiple data sources. Each vendor implements a standard interface, allowing easy addition of new sources.

### Graph-Based Orchestration
LangGraph manages the execution flow, state transitions, and tool availability. The graph supports conditional routing, debates between agents, and reflective learning loops.

### Structured Output
Newer agents (Research Manager, Trader, Portfolio Manager) produce structured Pydantic models that are then rendered to markdown for consistency with the rest of the system.

### Checkpointing
Runs can be resumed from saved checkpoints, useful for long-running analyses or backtesting iterations.

## Related Files

- **README.md** — Project overview and getting started
- **CHANGELOG.md** — Version history and feature releases
- **pyproject.toml** — Package dependencies and metadata
- **main.py** — Example usage script

## Version

This documentation corresponds to **TradingAgents v0.3.1**.

---

**Last Updated**: 2026-07-05
