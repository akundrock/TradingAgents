# Intraday System — Status & Backlog

Current capability snapshot for the intraday watchlist trading system. Updated with implementation state as of Phase 9 (pro-trader, screener, dashboard).

For usage examples see [README.md](README.md). For config keys see [CONFIGURATION.md](../CONFIGURATION.md#intraday).

---

## What Works Today

### Operating modes

| Mode | CLI example | Watchlist source | Typical strategy |
|------|-------------|------------------|------------------|
| Static watchlist | `tradingagents intraday NVDA AAPL --dry-run` | CLI / config only | `base_momentum` |
| Pro Trader static | `tradingagents intraday NVDA --strategy pro_trader_dashboard --dry-run` | CLI | `pro_trader_dashboard` |
| Screener only | `tradingagents intraday --screener --strategy pro_trader_dashboard --dry-run` | Schwab Streamer + RRS filter | `pro_trader_dashboard` |
| Combined | `tradingagents intraday SPY --screener --strategy pro_trader_dashboard` | SPY always + screener adds | `pro_trader_dashboard` |

### Session lifecycle

1. **Pre-market** — LLM daily bias for static watchlist symbols (cached to `premarket_bias.json`)
2. **Screener** (optional) — from `intraday_screener_start_time` (default 10:00 ET), refresh every N minutes
3. **Bar-close scans** — every `intraday_scan_interval_minutes` (default 5), parallel per symbol
4. **Signals** — console, Rich dashboard, `signals.csv` when gates pass (+ LLM unless `--dry-run`)

---

## Capability Matrix

| Capability | Status | Notes |
|------------|--------|-------|
| Schwab REST OHLCV (5/15/30m + daily) | Done | `get_candles_multi_timeframe()` |
| 60m bars (resampled from 30m) | Done | Minor drift vs TOS native hourly |
| Pre-market LLM bias + cache restore | Done | Same-day restart merges watchlist |
| Strategy: `base_momentum` | Done | VWAP + EMA/SMA + RSI |
| Strategy: `pro_trader_dashboard` | Done | ORB, SuperTrend, RRS, rvolume, sector |
| Two-gate gating (strategy-first) | Done | Not Magpie-based |
| Slim LLM pipeline (Trader → PM) | Done | `IntradayTradingGraph` |
| Rich live dashboard | Done | `--live/--no-live` |
| Dynamic volume screener (Streamer) | Done | `SCREENER_EQUITY` |
| RRS screener filter (5m/30m/60m) | Done | ThinkScript scanner parity |
| In-session watchlist updates | Done | With `--screener` |
| Signal cooldown | Done | `intraday_signal_cooldown_bars` |
| Bar-close delay | Done | 15s default |
| Volume pressure indicator | Optional gate + dashboard | Default off; see volume pressure notes below |
| Screener symbol cooldown enforcement | Config only | `removed_symbols` tracked, not enforced |
| Magpie intraday strategy | Not built | Daily graph only |
| Market internals gate (`$ADD`/`$TICK`) | Not built | yfinance fallback exists for other paths |
| REST screener fallback (S&P 500) | Not built | Phase 3 screener plan |
| Order execution | Not built | Signals only |
| Webhook / Slack alerts | Not built | Natural extension on `IntradaySignal` |

---

## Strategy Comparison

| | `base_momentum` | `pro_trader_dashboard` |
|--|-----------------|------------------------|
| **Best for** | Learning the intraday loop, simple momentum plays | ThinkOrSwim Pro Trader parity, liquid movers |
| **Entry logic** | VWAP, EMA/SMA, RSI band | ORB + SuperTrend + multi-TF RRS + rvolume + sector |
| **MTF data** | 5m + 30m (+ daily bias) | 5/15/30/60m + daily |
| **Gate 2** | Usually enable (`intraday_require_daily_bias_alignment`) | Often disable — strategy encodes bias/structure |
| **Screener pairing** | Optional | Recommended (`--screener`) for volume universe |

---

## Data Dependencies

| Need | Source | Auth |
|------|--------|------|
| Bar-close OHLCV | Schwab REST `pricehistory` | OAuth tokens (`tradingagents schwab-auth`) |
| Screener universe | Schwab Streamer `SCREENER_EQUITY` | Same OAuth + `userPreference` |
| Daily bias LLM | OpenAI/Anthropic/etc. | Provider API keys |
| Sector mapping | Local `sector_tickers.json` | None |
| Social pre-market (optional) | Reddit / StockTwits via analysts | Per vendor |

Install intraday extras: `pip install "tradingagents[intraday]"` (`apscheduler`, `websockets`).

---

## Known Limitations

- **60m RRS** — Python resamples 30m→60m; TOS `shared_long_1h_RS` uses native hourly bars (small value differences possible).
- **Screener-added symbols** — Default neutral daily bias; full LLM bias optional via `intraday_screener_run_premarket_for_new`.
- **Passing screener ≠ trade signal** — Layer 2 (RRS filter) is upstream; `pro_trader_dashboard` still gates at bar-close.
- **No REST screener** — If Streamer fails, no automatic fallback yet.
- **Watchlist static mode** — Without `--screener`, symbols are fixed until scanner restart (premarket cache still helps restarts).

### Volume pressure notes

- **Best for** active day trading (entry timing on the signal bar), not default for passive intraday monitoring.
- **Computed always** when using `pro_trader_dashboard`; optional gates (`pro_trader_require_buy_pressure`, etc.) default **off**.
- **PM / exit benefit** is not wired yet — Portfolio Manager does not receive bar-level indicators. Future work: pass bar-context into Trader/PM prompts or add a position-monitoring loop for scale-out/exit signals.

---

## Prioritized Backlog (current stack, no new vendors)

### Quick wins

1. **Wire volume pressure into LLM bar-context** — Trader/PM prompts + position monitor for exits (gates + dashboard done; default off).
2. **Enforce screener symbol cooldown** — use `intraday_screener_symbol_cooldown_minutes` before re-adding dropped symbols.
3. **Screener min price filter** — drop penny stocks via `ScreenerCandidate.last_price`.
4. **Sector universe filter** — restrict screener to `sector_tickers.json` for sector alignment.
5. **Document recommended screener defaults** — `intraday_screener_require_relative_volume=true` with `pro_trader_dashboard`.

### Medium effort

6. **REST screener fallback** — client-side RRS over ~509 S&P tickers when Streamer unavailable.
7. **Short screener mode** — `PERCENT_CHANGE_DOWN` keys + `intraday_screener_direction=both`.
8. **Magpie intraday strategy** — `strategies/magpie.py` using `compute_magpie_from_candles()`.
9. **Market internals gate** — Schwab Streamer `LEVELONE_EQUITIES` for `$ADD`/`$TICK`.
10. **Async LLM premarket for screener symbols** — non-blocking queue when `intraday_screener_run_premarket_for_new=true`.

### Larger features

11. **Historical dry-run / TOS parity replay** — cached OHLCV backtest for pro-trader + screener logic.
12. **Alert channels** — webhook/email/Slack on `IntradaySignal`.
13. **Trade journal correlation** — link signals to `trade_journal` executions.
14. **Order routing** — Schwab trade API (new phase; separate from market data).

### Deferred unless Streamer unreliable

- Third-party screeners (Polygon, Finnhub) — not needed if Schwab Streamer works in live testing.

---

## Test Coverage

| Area | Test files |
|------|------------|
| MTF fetch | `test_schwab_mtf_fetch.py`, `test_mtf_validator_60m.py` |
| Pro Trader | `test_pro_trader_indicators.py`, `test_pro_trader_strategy.py` |
| Screener | `test_schwab_streamer.py`, `test_universe_screener.py` |
| Scanner | `test_intraday_scanner.py` |
| Gating / strategy / graph | `test_intraday_gating.py`, `test_intraday_strategy.py`, `test_intraday_graph.py` |
| Dashboard / cache | `test_intraday_dashboard.py`, `test_premarket_cache.py` |
| CLI | `test_intraday_cli.py` |
