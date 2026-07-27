# Schwab API Spec Quick Reference for Coding Agents

## Overview
**File**: [`docs/schwab-api-spec.openapi.yaml`](schwab-api-spec.openapi.yaml)

A complete OpenAPI 3.0 specification for the Charles Schwab Market Data API endpoints used by TradingAgents.

## Key Endpoints

### 1. Price History (OHLCV Data)
**Endpoint**: `GET https://api.schwabapi.com/marketdata/v1/pricehistory`

**What it does**: Returns candlestick (Open, High, Low, Close, Volume) data.

**Common parameters**:
- `symbol`: `SPY`, `NVDA`, `/MES` (futures), etc.
- `startDate`: Unix timestamp in milliseconds (e.g., `1716192000000`)
- `endDate`: Unix timestamp in milliseconds
- `frequencyType`: `daily` or `minute`
- `frequency`: `1`, `5`, `10`, `15`, `30` (minutes if intraday)
- `needExtendedHoursData`: `true` (default, includes pre/after-hours)

**Example Response**:
```json
{
  "empty": false,
  "candles": [
    {
      "datetime": 1716192000000,
      "open": 417.85,
      "high": 419.92,
      "low": 417.50,
      "close": 419.75,
      "volume": 45230000
    }
  ]
}
```

**Use cases**:
- Get daily prices for technical indicators (50/200 SMA, EMA, MACD, RSI, Bollinger Bands, ATR, VWMA, MFI)
- Fetch intraday 5-minute bars for day trading strategies
- Support for futures symbols (e.g., `/MES` for Micro E-mini S&P 500)

### 2. Option Chains (IV/Implied Move)
**Endpoint**: `GET https://api.schwabapi.com/marketdata/v1/chains`

**What it does**: Returns options data (calls, puts) including implied volatility, Greeks, and underlying quote.

**Common parameters**:
- `symbol`: `SPY`, `QQQ`, `NVDA`, etc.
- `contractType`: `ALL` (calls + puts)
- `strategy`: `SINGLE` (standard analysis)
- `strikeCount`: `8` (balance detail vs. load)
- `includeUnderlyingQuote`: `TRUE`
- `fromDate`: `YYYY-MM-DD` (e.g., `2024-05-20`)
- `toDate`: `YYYY-MM-DD`

**Example Response**:
```json
{
  "status": "SUCCESS",
  "callExpDateMap": {
    "2024-05-31:1": {
      "420": [
        {
          "symbol": "SPY 053124C00420000",
          "bid": 2.15,
          "ask": 2.25,
          "volatility": 18.5,
          "delta": 0.65,
          "theta": -0.05
        }
      ]
    }
  },
  "putExpDateMap": {...},
  "underlyingQuote": {
    "symbol": "SPY",
    "bid": 422.50,
    "ask": 422.55,
    "last": 422.52
  }
}
```

**Use case**: Calculate implied move bounds for intraday trading context (Magpie signal).

### 3. OAuth2 Authorization
**Endpoints**:
- `GET https://api.schwabapi.com/v1/oauth/authorize` — Initiate login flow
- `POST https://api.schwabapi.com/v1/oauth/token` — Exchange code for tokens

**How TradingAgents uses it**:
1. Run `tradingagents schwab-auth` (calls authorize endpoint)
2. User approves access
3. Exchange code for tokens (calls token endpoint)
4. Automatic refresh when tokens expire (calls token endpoint with `grant_type: refresh_token`)

## Authentication

**Header for API requests**:
```
Authorization: Bearer {access_token}
```

**Header for token endpoint** (POST):
```
Authorization: Basic {base64(client_id:client_secret)}
```

## Technical Indicators (Client-Side Computed)

The OpenAPI spec documents the pricehistory endpoint. Indicators are computed client-side from the candles using the `stockstats` library:

- **Trend**: 50 SMA, 200 SMA, 10 EMA
- **Momentum**: MACD, MACD Signal, MACD Histogram, RSI
- **Volatility**: Bollinger Bands (upper/middle/lower), ATR
- **Volume**: VWMA, MFI

**Code reference**: [`tradingagents/dataflows/schwab.py`](../tradingagents/dataflows/schwab.py), `get_indicator()` function.

## Error Handling

| Status | Meaning | Action |
|--------|---------|--------|
| **400** | Invalid parameters | Check symbol, date format, parameters |
| **401** | Unauthorized (token expired) | TradingAgents auto-refreshes; if persists, re-authorize |
| **429** | Rate limited | Exponential backoff; typical limit ~5000 req/min |
| **5XX** | Server error | Retry with exponential backoff |

## Rate Limits

- Schwab enforces ~5000 requests per minute for market data
- TradingAgents handles rate limit retries automatically in `_authenticated_get()`

## Timestamp Format

All timestamps in requests are **Unix milliseconds** (ms since epoch, UTC):
- **Example**: 2024-05-20 00:00:00 UTC = `1716192000000` ms
- **Python**: `int(datetime.timestamp() * 1000)`

## Agent Integration

### For Prompt Engineering
Reference this spec when writing agent prompts:
```
The market data is fetched via Schwab API (see docs/schwab-api-spec.openapi.yaml).
Available endpoints:
- Price history: /marketdata/v1/pricehistory (daily + intraday)
- Option chains: /marketdata/v1/chains (IV for implied move)
```

### For Code Generation
Tools can use the OpenAPI spec to auto-generate client code:
```bash
# Example: Generate Python client (if using openapi-generator)
openapi-generator generate -i docs/schwab-api-spec.openapi.yaml -g python -o gen_client
```

### For Validation
Use the spec to validate requests/responses:
- Ensure symbol format is correct (e.g., `/MES` for futures, `SPY` for stocks)
- Verify timestamp is in milliseconds
- Check that `frequencyType` and `frequency` are valid combinations

## Files Referenced

- **OpenAPI Spec**: [`docs/schwab-api-spec.openapi.yaml`](schwab-api-spec.openapi.yaml)
- **Implementation**: [`tradingagents/dataflows/schwab.py`](../tradingagents/dataflows/schwab.py)
- **Config**: [`tradingagents/default_config.py`](../tradingagents/default_config.py) (Schwab vendor config)
- **Tests**: [`tests/test_schwab_*.py`](../../tests/) (Schwab integration tests)

## Next Steps

1. **Reference in prompts**: Agents can now include the endpoint documentation in their context
2. **Generate client code**: Use OpenAPI tools to generate type-safe clients
3. **Extend documentation**: Add endpoint-specific examples or tutorials as needed
4. **Link from docs**: Update [`docs/DATAFLOWS.md`](../DATAFLOWS.md) to reference this spec
