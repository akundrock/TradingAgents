# TradingAgents: Data Integration and Vendor Architecture

## Overview

TradingAgents uses a vendor-abstraction pattern to integrate multiple market data sources. This design allows:
- **Easy addition** of new data vendors (e.g., Schwab TOS)
- **Graceful fallback** when a vendor fails or lacks a feature
- **Consistent interface** across all data tools
- **Configurable priority** for vendor selection

## Data Vendor Abstraction

### Core Interface

**Location**: `tradingagents/dataflows/interface.py`

All data vendors implement a common set of methods:

```python
# Core stock/OHLCV data (required)
def get_stock_data(
    symbol: str,
    start_date: str,  # "YYYY-MM-DD"
    end_date: str,    # "YYYY-MM-DD"
) → pd.DataFrame:
    """Returns OHLCV data: columns [Open, High, Low, Close, Volume]"""

# Technical indicators
def get_indicators(
    symbol: str,
    indicators: list[str],  # e.g., ["MACD", "RSI", "BBANDS"]
    period: int = None,     # lookback period
) → dict[str, pd.Series]:
    """Returns indicator time series by name"""

# Fundamental data
def get_fundamentals(symbol: str) → dict[str, any]:
    """Quick fundamental snapshot: P/E, P/B, market cap, etc."""

def get_income_statement(symbol: str) → dict[str, list]:
    """Revenue, earnings, margins by period"""

def get_balance_sheet(symbol: str) → dict[str, list]:
    """Assets, liabilities, equity by period"""

def get_cashflow(symbol: str) → dict[str, list]:
    """Operating, investing, financing cash flows"""

# News and sentiment
def get_news(
    symbol: str,
    start_date: str,
    end_date: str,
) → list[dict]:
    """News articles for symbol in date range"""

def get_global_news(
    keywords: list[str],
    start_date: str,
    end_date: str,
) → list[dict]:
    """Broader market news by keyword"""

def get_insider_transactions(symbol: str) → list[dict]:
    """Insider buying/selling transactions"""

# Macroeconomic data
def get_macro_data(indicators: list[str]) → dict[str, any]:
    """Economic indicators: rates, inflation, unemployment, etc."""

# Prediction markets
def get_prediction_markets(event_description: str) → dict[str, float]:
    """Market-implied probabilities for events"""

# Social/sentiment data
def get_social_sentiment(symbol: str) → dict[str, any]:
    """Social media sentiment: Reddit, StockTwits"""
```

### Data Tool Categories

Tools are organized into logical categories for routing and error handling:

```python
TOOLS_CATEGORIES = {
    "core_stock_apis": {
        "description": "OHLCV stock price data",
        "tools": ["get_stock_data"],
        "required": True,  # Fail if all vendors fail
    },
    "technical_indicators": {
        "description": "Technical analysis indicators",
        "tools": ["get_indicators"],
        "required": True,
    },
    "fundamental_data": {
        "description": "Company financials",
        "tools": ["get_fundamentals", "get_balance_sheet", ...],
        "required": True,
    },
    "news_data": {
        "description": "News and insider data",
        "tools": ["get_news", "get_global_news", ...],
        "required": True,
    },
    "macro_data": {
        "description": "Macroeconomic indicators",
        "tools": ["get_macro_indicators"],
        "required": False,  # Degrade gracefully
    },
    "prediction_markets": {
        "description": "Market-implied probabilities",
        "tools": ["get_prediction_markets"],
        "required": False,  # Degrade gracefully
    },
}
```

### Vendor Router Logic

**Location**: `tradingagents/dataflows/interface.py` (router functions)

When an agent calls a tool, the router:

1. **Check configuration**: Is this vendor enabled? (API key present)
2. **Try vendors in priority order**:
   ```
   VENDOR_PRIORITY[tool] = [
       "yfinance",          # Try first
       "alpha_vantage",     # Fallback
       "..." 
   ]
   ```
3. **Capture errors**: Network errors, rate limits, API errors
4. **Route based on category**:
   - **Core category failure** → Raise error, abort run
   - **Optional category failure** → Log warning, return sentinel (empty dict)

### Vendor Implementations

### Current price-routing defaults

The default vendor routing is Schwab-first for price-centric categories:

- `core_stock_apis`: `schwab`
- `technical_indicators`: `schwab`
- `market_internals`: `schwab`
- `implied_move_data`: `schwab`

This keeps intraday OHLCV, indicators, internals, and implied-move context in
one source family and reduces cross-vendor drift in fast intraday workflows.

#### 1. Yahoo Finance (yfinance)

**Location**: `tradingagents/dataflows/y_finance.py`

**Coverage**:
- OHLCV data (primary)
- Balance sheet, income statement, cash flow
- Insider transactions
- Fundamentals (P/E, P/B, dividend yield, etc.)

**Configuration**:
```python
# API key not required (public data)
# Optional environment variable:
TRADINGAGENTS_YFINANCE_TIMEOUT = "10"  # seconds
```

**Strengths**:
- Free, no authentication required
- Comprehensive US and international data
- Reliable for OHLCV data

**Limitations**:
- Rate limiting (respects backoff headers)
- Less frequent fundamental updates
- No technical indicators (use stockstats instead)

#### 2. Alpha Vantage

**Location**: `tradingagents/dataflows/alpha_vantage.py` (and related files)

**Coverage**:
- OHLCV data (intraday and daily)
- Technical indicators (MACD, RSI, BBANDS, etc.)
- Fundamentals (balance sheet, income, cash flow)
- News articles
- Insider transactions
- Global news (macro events)

**Configuration**:
```bash
TRADINGAGENTS_ALPHA_VANTAGE_API_KEY=<your-key>  # Required for this vendor
TRADINGAGENTS_ALPHA_VANTAGE_BASE_URL=...       # Optional: custom endpoint
```

**Strengths**:
- Technical indicator calculation (built-in)
- Good global coverage
- Fundamental data quality

**Limitations**:
- Rate limited (5 requests/min free tier)
- API key required
- Some international data gaps

#### 3. FRED (Federal Reserve Economic Data)

**Location**: `tradingagents/dataflows/fred.py`

**Coverage**:
- Macroeconomic indicators:
  - Interest rates (Fed funds rate, 10-year yield)
  - Inflation (CPI, PCE)
  - Employment (unemployment rate, nonfarm payrolls)
  - Growth (GDP, industrial production)

**Configuration**:
```bash
TRADINGAGENTS_FRED_API_KEY=<your-key>  # Required
```

**Strengths**:
- Official US Federal Reserve data
- High reliability and historical depth
- Comprehensive macro coverage

**Limitations**:
- US economic data only
- API key required
- Data typically released monthly/quarterly

#### 4. Polymarket

**Location**: `tradingagents/dataflows/polymarket.py`

**Coverage**:
- Prediction market probabilities for:
  - Economic outcomes (Fed decisions, inflation)
  - Corporate events (earnings surprises)
  - Geopolitical events

**Configuration**:
```bash
TRADINGAGENTS_POLYMARKET_API_KEY=...  # Optional
```

**Strengths**:
- Forward-looking market expectations
- Real-time probability updates
- Crowd intelligence

**Limitations**:
- Limited number of active markets
- Smaller liquidity than traditional markets
- Event-specific availability

#### 5. Reddit

**Location**: `tradingagents/dataflows/reddit.py`

**Coverage**:
- Subreddit discussions (r/stocks, r/investing, ticker-specific subreddits)
- Sentiment aggregation (bullish/bearish post counts)
- Discussion volume and trends

**Configuration**:
```bash
TRADINGAGENTS_REDDIT_CLIENT_ID=...     # Required
TRADINGAGENTS_REDDIT_CLIENT_SECRET=... # Required
TRADINGAGENTS_REDDIT_USER_AGENT=...    # Required
```

**Strengths**:
- Retail investor sentiment
- Real-time discussions
- Diverse perspectives

**Limitations**:
- Sentiment can be noisy
- Retail bias (not institutional)
- API authentication required

#### 6. StockTwits

**Location**: `tradingagents/dataflows/stocktwits.py`

**Coverage**:
- Stock-specific sentiment messages
- Bullish/bearish sentiment counts
- Discussion volume

**Configuration**:
```bash
TRADINGAGENTS_STOCKTWITS_API_KEY=...  # Optional (public API available)
```

**Strengths**:
- Retail trader specific platform
- Real-time sentiment scoring
- No authentication often needed

**Limitations**:
- Retail-focused (less institutional data)
- API rate limiting

#### 7. Schwab

**Location**: `tradingagents/dataflows/schwab.py`

**Coverage**:
- OHLCV retrieval for intraday and daily windows
- Indicator derivation from Schwab OHLCV
- Implied-move context from options chain
- Market internals retrieval attempts for `$ADD`, `$TICK`, and `$VOLD`

**Configuration**:
```bash
TRADINGAGENTS_SCHWAB_CLIENT_ID=...      # Required
TRADINGAGENTS_SCHWAB_CLIENT_SECRET=...  # Required
TRADINGAGENTS_SCHWAB_TOKENS_PATH=...    # Optional override
TRADINGAGENTS_SCHWAB_REDIRECT_URI=...   # Optional override
```

**Notes**:
- If internals symbols are unavailable for current account entitlements, the
    adapter raises `NoMarketDataError` for that internals call.
- Intraday loops can still proceed according to configured fallback chains, but
    signal quality can degrade when internals data is missing.

### Adding a New Data Vendor: Schwab TOS Example

To add Schwab Thinkorswim (TOS) streaming market data, follow this pattern:

#### Step 1: Create vendor module

**File**: `tradingagents/dataflows/schwab_tos.py`

```python
"""Schwab Thinkorswim market data integration."""

import os
from typing import dict, list
import pandas as pd

SCHWAB_TOS_API_KEY = os.getenv("TRADINGAGENTS_SCHWAB_API_KEY", "")
SCHWAB_TOS_ACCOUNT_NUMBER = os.getenv("TRADINGAGENTS_SCHWAB_ACCOUNT", "")

def _validate_credentials():
    """Ensure Schwab credentials are available."""
    if not SCHWAB_TOS_API_KEY:
        raise ValueError("TRADINGAGENTS_SCHWAB_API_KEY not set")
    if not SCHWAB_TOS_ACCOUNT_NUMBER:
        raise ValueError("TRADINGAGENTS_SCHWAB_ACCOUNT not set")

def get_stock_data(
    symbol: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """Fetch OHLCV data from Schwab TOS."""
    _validate_credentials()
    
    # Connect to Schwab API
    # (Use schwab-py library or REST API)
    from schwab import client  # Hypothetical
    
    c = client.Client(
        account_number=SCHWAB_TOS_ACCOUNT_NUMBER,
        token=SCHWAB_TOS_API_KEY,
    )
    
    # Fetch data
    data = c.price_history(
        symbol=symbol,
        period_type="month",
        period=6,  # 6 months
        frequency_type="daily",
    )
    
    # Convert to DataFrame with standard columns
    df = pd.DataFrame(data)
    df = df.rename(columns={
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    })
    df["Date"] = pd.to_datetime(df["datetime"])
    df = df.set_index("Date")
    
    # Filter to date range
    df = df[start_date:end_date]
    
    return df[["Open", "High", "Low", "Close", "Volume"]]

def get_streaming_data(symbol: str) -> dict:
    """Fetch real-time streaming data."""
    _validate_credentials()
    # Real-time price, bid/ask, volume
    ...
```

#### Step 2: Register vendor in interface

**File**: `tradingagents/dataflows/interface.py`

```python
from .schwab_tos import (
    get_stock_data as get_schwab_stock,
    get_streaming_data as get_schwab_streaming,
)

# Update VENDOR_METHODS
VENDOR_METHODS = {
    "get_stock_data": {
        "schwab_tos": get_schwab_stock,  # Add here
        "yfinance": get_YFin_data_online,
        "alpha_vantage": get_alpha_vantage_stock,
    },
    ...
}

# Update VENDOR_LIST
VENDOR_LIST = [
    "schwab_tos",       # Add with appropriate priority
    "yfinance",
    "alpha_vantage",
]
```

#### Step 3: Add configuration support

**File**: `tradingagents/default_config.py`

```python
_ENV_OVERRIDES = {
    ...
    "TRADINGAGENTS_SCHWAB_API_KEY":     "schwab_api_key",
    "TRADINGAGENTS_SCHWAB_ACCOUNT":     "schwab_account_number",
}

DEFAULT_CONFIG = _apply_env_overrides({
    ...
    "schwab_api_key": os.getenv("TRADINGAGENTS_SCHWAB_API_KEY", ""),
    "schwab_account_number": os.getenv("TRADINGAGENTS_SCHWAB_ACCOUNT", ""),
})
```

#### Step 4: Use in agents

Agents now automatically use Schwab TOS via the vendor router:

```python
# In any agent:
from tradingagents.agents.utils.agent_utils import get_stock_data

# This will try: schwab_tos → yfinance → alpha_vantage
df = get_stock_data("NVDA", "2024-05-01", "2024-05-10")
```

### Streaming Data Considerations for Schwab TOS

Currently, TradingAgents processes **historical** and **periodic** data. To add Schwab's **streaming** capabilities:

1. **Extend state dict** to include current prices:
   ```python
   state["current_market_snapshot"] = {
       "symbol": "NVDA",
       "bid": 123.45,
       "ask": 123.50,
       "last": 123.47,
       "volume": 1250000,
   }
   ```

2. **Create streaming hook** in graph setup:
   ```python
   # Before agents run, fetch current market snapshot
   from tradingagents.dataflows.schwab_tos import get_streaming_data
   state["current_market_snapshot"] = get_streaming_data(ticker)
   ```

3. **Agents reference current data** in prompts:
   ```
   Current market snapshot (as of run time):
   - Price: $123.47
   - Bid/Ask: $123.45 / $123.50
   - Daily volume so far: 1.25M
   ```

## Data Validation

**Location**: `tradingagents/dataflows/market_data_validator.py`

Data quality issues are caught before agents see them:

```python
class MarketDataValidator:
    def validate_ohlcv(self, df: pd.DataFrame) -> bool:
        """Check OHLCV data for:
        - Missing columns (Open, High, Low, Close, Volume)
        - Missing or stale timestamps
        - Invalid OHLCV relationships (High < Low, etc.)
        - Suspicious gaps or spikes
        """
        ...
    
    def validate_fundamentals(self, data: dict) -> bool:
        """Check fundamental data:
        - Required keys present
        - Values are numeric or expected types
        - No extreme outliers (e.g., P/E < 0)
        """
        ...
```

## Market Data Snapshot

Agents sometimes need a current market snapshot (current price, volatility, volume):

**Location**: `tradingagents/agents/utils/agent_utils.py`

```python
def get_verified_market_snapshot(
    symbol: str,
    cache_age_seconds: int = 60,
) → dict[str, any]:
    """Returns: {
        'price': float,
        'bid': float,
        'ask': float,
        'volume': int,
        'prev_close': float,
        '52_week_high': float,
        '52_week_low': float,
        'market_cap': float,
        'pe_ratio': float,
    }"""
```

## Instrument Identity Resolution

**Location**: `tradingagents/agents/utils/agent_utils.py`

Ticker symbols can be ambiguous (e.g., "BA" = Boeing or Bretton-Woods?). Resolution:

```python
def resolve_instrument_identity(
    company_name: str,
    trade_date: str,
) → str:
    """Returns: {
        'ticker': 'NVDA',
        'full_name': 'NVIDIA Corporation',
        'exchange': 'NASDAQ',
        'asset_type': 'stock',
        'currency': 'USD',
    }"""
    
    # Tries multiple lookup methods:
    # 1. Exact ticker if it's clearly a ticker
    # 2. Company name search (yfinance ticker_symbols)
    # 3. Manual disambiguation prompt (if interactive)
```

## Error Handling in Data Layer

**Location**: `tradingagents/dataflows/errors.py`

```python
class VendorRateLimitError(Exception):
    """Raised when vendor rate limit exceeded; triggers retry/fallback"""

class VendorNotConfiguredError(Exception):
    """Raised when required API key missing; triggers fallback"""

class NoMarketDataError(Exception):
    """Raised when no vendor can provide data; abort run"""
```

## Testing Vendor Integration

**Location**: `tests/` (see test files like `test_yfinance_stale_ohlcv_guard.py`)

Tests cover:
- Each vendor's API integration
- Fallback routing when vendor fails
- Data validation and sanitization
- Rate limit handling
- Missing API key scenarios

## Configuration for Your Modifications

### Backfilling Trade Journal
Extend dataflows to support historical trade data:

```python
# New vendor method
def get_execution_history(
    account_id: str,
    start_date: str,
    end_date: str,
) → list[dict]:
    """Returns: [{
        'symbol': 'NVDA',
        'date': '2024-05-10',
        'action': 'BUY' | 'SELL',
        'price': 123.45,
        'quantity': 100,
        'commission': 5.0,
    }, ...]"""
```

Then backfill state during graph initialization:
```python
state["execution_history"] = get_execution_history(
    account_id=config["schwab_account"],
    start_date=analysis_start_date,
    end_date=current_date,
)
```

---

**See Also**:
- [ARCHITECTURE.md](ARCHITECTURE.md) — How data flows through the system
- [AGENTS.md](AGENTS.md) — How agents use data tools
- [STATE_MANAGEMENT.md](STATE_MANAGEMENT.md) — State structure and memory
