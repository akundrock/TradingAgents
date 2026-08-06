# Intraday Watchlist Trading System

An intraday trading mode for TradingAgents that monitors stocks on live Schwab data, validates setups via pluggable strategies, and surfaces actionable signals with LLM-quality reasoning — without re-running the full analyst pipeline on every bar.

**Signals only** — no automated order execution.

For a capability matrix and prioritized backlog, see [STATUS.md](STATUS.md).

## Design Goals

- **Live data from Schwab** — intraday candles via REST `pricehistory`; optional universe discovery via Streamer `SCREENER_EQUITY`
- **Multi-timeframe validation** — 5m entry context, 30m trend, daily bias; `pro_trader_dashboard` also uses 15m/60m RRS and ORB
- **Cost-efficient LLM usage** — daily bias once per symbol pre-market; intraday triggers only run Trader → Risk Debate → Portfolio Manager
- **Pluggable strategies** — `base_momentum` or `pro_trader_dashboard` (ThinkScript SMBD port)
- **Optional dynamic watchlist** — volume + RRS screener refreshes the watchlist during the session

---

## Pipeline Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│  Daily Pipeline (TradingAgentsGraph.propagate)                       │
│  Analysts → Researcher Debate → Research Manager → Magpie →         │
│  Trader → Risk Debate → Portfolio Manager                            │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│  Intraday Pipeline                                                   │
│                                                                      │
│  PRE-MARKET (once per static watchlist symbol):                      │
│    propagate_daily_bias() → DailyBiasReport → premarket_bias.json    │
│                                                                      │
│  OPTIONAL SCREENER (every N min after 10:00 ET):                     │
│    Schwab Streamer volume keys → RRS filter 5m/30m/60m → watchlist   │
│    New symbols: neutral daily bias (or optional LLM premarket)         │
│                                                                      │
│  EACH SCAN INTERVAL (default every 5 min, per symbol in parallel):   │
│    Schwab MTF fetch → MultiTimeframeValidator                        │
│    → IntradayStrategy.check_setup()                                  │
│    → GatingLayer (Gate 1 strategy, Gate 2 optional MTF/bias)         │
│    → [pass] IntradayTradingGraph → IntradaySignal                    │
└──────────────────────────────────────────────────────────────────────┘
```

---

## System Components

### Package: `tradingagents/intraday/`

| Module | Responsibility |
|--------|----------------|
| `session.py` | `TradingSession`, `DailyBiasReport`, `IntradaySignal`, screener metadata |
| `scanner.py` | `WatchlistScanner` — scheduler, pre-market, screener refresh, bar-close evaluation |
| `mtf_validator.py` | `MultiTimeframeValidator` — 5m fetch + local resample (default) or legacy multi-TF fetch; per-scan benchmark cache |
| `frame_enrichment.py` | Shared `enriched_frames_from_5m()` for screener and strategy paths |
| `premarket_cache.py` | Disk cache for daily bias (`premarket_bias.json`) |
| `universe_screener.py` | Volume universe + RRS pre-filter (ThinkScript scanner parity) |
| `gating.py` | Two-gate validation (strategy-first, not Magpie) |
| `strategy.py` | `IntradayStrategy` protocol, `StrategyResult` |
| `strategies/base_momentum.py` | VWAP + EMA/SMA + RSI momentum strategy |
| `strategies/pro_trader_dashboard.py` | ORB + SuperTrend + multi-TF RRS + relative volume + sector |
| `indicators/volume_pressure.py` | Buy/sell bar split, pre-market volume, 3-bar price+volume trend |
| `indicators/` | ORB, RRS, relative volume, SuperTrend, sector map, resample |

### Data layer

| Module | Responsibility |
|--------|----------------|
| `tradingagents/dataflows/schwab.py` | REST OHLCV, `get_candles_multi_timeframe()`, `get_user_preference()` |
| `tradingagents/dataflows/schwab_streamer.py` | WebSocket `SCREENER_EQUITY` for volume-ranked universe |
| `tradingagents/graph/intraday_graph.py` | Slim LLM graph: Trader → Risk → Portfolio Manager |
| `cli/intraday_display.py` | Rich live dashboard (watchlist, detail, signals, logs) |

---

## Strategies

Registered in `tradingagents/intraday/strategies/__init__.py`. Select via `--strategy` or `intraday_strategy`.

### `base_momentum` (default)

Simple momentum filter for experimentation:

- **Long**: price > VWAP, EMA(10) > SMA(20), RSI 40–70
- **Short**: inverse conditions
- Best when Gate 2 (`intraday_require_daily_bias_alignment`) encodes daily + 30m alignment

### `pro_trader_dashboard`

Port of ThinkOrSwim Pro Trader Dashboard ([spec](../pro-trader-dashboard-spec.md)):

- Opening Range Breakout (9:30–10:00 ET), SuperTrend, multi-TF RRS vs SPY
- 5m relative volume > 1, sector power alignment, daily key-level buffer
- Volume pressure (buy/sell bar split, pre-market volume) — computed every scan, shown on dashboard; optional gates default **off** (active day-trading entry timing)
- Auto-expands MTF fetch to 5/15/30/60m (60m resampled from 30m bars)

Use with the screener for liquid movers: `--screener --strategy pro_trader_dashboard`.

---

## Strategy-First Gating

Magpie is **not** run in the intraday scan loop (remains in the daily graph). Gating is two sequential gates:

**Gate 1 — Strategy:** `check_setup()` must pass with direction `long` or `short`. Factors logged at INFO.

**Gate 2 — MTF alignment (optional):** When `intraday_require_daily_bias_alignment` is `true`, 30m trend must align with pre-market daily bias. Disable when the strategy encodes bias/trend internally (typical for `pro_trader_dashboard`). For `orb_breakout` with `--screener`, Gate 2 is auto-disabled by default (`intraday_orb_breakout_screener_disable_gate2`) because screener-added symbols use neutral daily bias unless pre-market is run for new symbols.

When both gates pass, `IntradayTradingGraph.propagate_intraday()` runs.

---

## Dynamic Watchlist Screener

Three-layer pipeline (see [pro-trader-dashboard-spec.md](../pro-trader-dashboard-spec.md) §11):

1. **Universe discovery** — `intraday_screener_source`:
   - `auto` (default): `sp500_quotes` when `require_sp500=true` (volume-ranked top N); else Streamer actives
   - `sp500_quotes`: REST quotes over full SP500, rank by `totalVolume`, up to `candidate_limit` before RRS
   - `sp500_rrs`: REST quotes for **all** SP500 → min price gate → multi-TF RRS on full set → rank by 5m RRS → top `candidate_limit` to watchlist (screener picks prioritized over base symbols)
   - `streamer`: Schwab Streamer exchange actives (`NASDAQ_VOLUME_0`, `NYSE_VOLUME_0`)
2. **Price pre-filter** — optional min price + S&P 500 check (`sp500_constituents.json`; skipped for `sp500_quotes` / `sp500_rrs`; refresh with `tradingagents refresh-sp500 --write`)
3. **Pluggable filter pipeline** — configure via `intraday_screener_filters`:
   - `rrs` (default): 5m/30m/60m RRS vs SPY; benchmark 5m fetched once per refresh
   - `orb`: opening range breakout (9:30–10:00 ET OR, breakout latch after 10:00)
   - Combine with `intraday_screener_filter_mode`: `any` (union) or `all` (intersection)
4. **Bar-close strategy** — `pro_trader_dashboard`, `orb_breakout`, or `base_momentum`

Static symbols from CLI/config are always kept (`base_watchlist`). Screener adds/removes symbols after `intraday_screener_start_time` (default 10:00 ET).

Requires `pip install "tradingagents[intraday]"` (includes `websockets`).

---

## Configuration

Key settings in `tradingagents/default_config.py`. Full table: [CONFIGURATION.md](../CONFIGURATION.md#intraday).

```python
# Session
"watchlist": [],
"intraday_scan_interval_minutes": 5,
"intraday_bar_close_delay_seconds": 15,
"intraday_session_start": "09:30",
"intraday_session_end": "16:00",
"intraday_strategy": "base_momentum",  # or pro_trader_dashboard

# Gating
"intraday_require_daily_bias_alignment": True,
"intraday_orb_breakout_screener_disable_gate2": True,  # auto-off Gate 2 for orb_breakout + screener

# Pro Trader (when strategy=pro_trader_dashboard)
"pro_trader_benchmark": "SPY",
"pro_trader_min_rs_timeframes": 4,
"pro_trader_require_relative_volume": True,
# Volume pressure (optional; default off — see pro-trader-dashboard-spec §6)
"pro_trader_require_buy_pressure": False,
"pro_trader_require_sell_pressure": False,
"pro_trader_min_buy_percent": 55.0,
"pro_trader_min_sell_percent": 55.0,
"pro_trader_require_price_volume_trend": False,
"pro_trader_min_premarket_volume": 0,

# Screener
"intraday_screener_enabled": False,
"intraday_screener_interval_minutes": 15,
"intraday_screener_keys": ["NASDAQ_VOLUME_0", "NYSE_VOLUME_0"],
"intraday_screener_source": "auto",  # or sp500_rrs for RRS-first SP500 scan
"intraday_screener_rank_mode": "pass_only",  # rank_all scores all symbols
"intraday_screener_rank_rrs_timeframe": "30m",  # sort key: 5m, 30m, or 60m
"intraday_screener_max_concurrent_symbols": None,  # optional screener throttle
"intraday_screener_min_rrs_aligned": 3,
"intraday_screener_filters": ["rrs"],  # or ["orb"], ["orb", "rrs"]
"intraday_screener_filter_mode": "any",
"intraday_screener_max_watchlist": 12,
```

---

## CLI Usage

```bash
# Static watchlist
tradingagents intraday NVDA AAPL SPY --dry-run

# Pro Trader strategy
tradingagents intraday NVDA --strategy pro_trader_dashboard --dry-run

# Dynamic screener only (no static symbols)
tradingagents intraday --screener --strategy pro_trader_dashboard --dry-run

# ORB screener + ORB strategy
tradingagents intraday --screener --screener-filters orb --strategy orb_breakout --dry-run

# RRS debug (compare values to TOS)
tradingagents rrs-debug AAPL --timeframes 5m,30m,60m

# Static base + screener additions
tradingagents intraday SPY --screener --screener-interval 15 --strategy pro_trader_dashboard

# Pre-market options
tradingagents intraday SPY --analysts market,news,fundamentals
tradingagents intraday SPY --no-premarket          # neutral bias for all
tradingagents intraday SPY --force-premarket       # re-run LLM bias
tradingagents intraday --watchlist SPY,COHR        # restore cache for existing symbols

# Logging
tradingagents intraday SPY --verbose
tradingagents intraday SPY --log-level DEBUG

# Validate screener volume discovery (Streamer vs SP500 REST quotes)
tradingagents screener-debug --source both --limit 20 --compare
tradingagents screener-debug --source sp500_quotes --limit 50
tradingagents screener-debug --source sp500_rrs --limit 20
```

### Live dashboard

Default on TTY (`--live/--no-live`):

- Watchlist: bias, source (`static`/`scre`), gates G1/G2, score, direction
- Detail: pre-market reports, strategy factors, screener RRS snapshot
- Recent signals + `tradingagents.*` log tail

**Symbol navigation** (while the scanner runs):

| Key | Action |
|-----|--------|
| `↑` / `k` | Previous symbol in watchlist |
| `↓` / `j` | Next symbol in watchlist |
| `Home` / `g` | Jump to first symbol |
| `End` | Jump to last symbol |
| `Enter` | Pin current symbol |
| `f` | Toggle follow mode (auto-follow latest scan/signal) |

Manual navigation pins the detail panel (`[pinned]` in the title). Press `f` to resume auto-follow (`[follow]`). The watchlist table still updates on every scan; only the detail panel selection is pinned.

### Outputs

| Path | Content |
|------|---------|
| `~/.tradingagents/intraday/YYYY-MM-DD/signals.csv` | Emitted signals |
| `~/.tradingagents/intraday/YYYY-MM-DD/premarket_bias.json` | Cached daily bias |

Pre-market cache: restart same day with new symbols — cached symbols restore instantly; only new symbols run LLM pre-market. With `--screener`, watchlist updates in-session without restart.

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Daily bias once pre-market | Cost + latency; full analyst debate not per bar |
| Strategy-first gating | Deterministic rules every bar; LLM only on pass |
| Two gates (not Magpie) | Strategy owns setup logic; Gate 2 optional bias check |
| Screener upstream of strategy | Volume discovery + RS filter; entry quality still at bar-close |
| Signals only | Order routing is a separate future phase |
| APScheduler + ThreadPoolExecutor | Reliable bar timing; parallel symbol evaluation |

---

## Resolved / Open Items

| Topic | Status |
|-------|--------|
| Bar-close delay | Implemented — `intraday_bar_close_delay_seconds` (default 15s) |
| Re-entry cooldown | Implemented — `intraday_signal_cooldown_bars` |
| Market internals (`$ADD`/`$TICK`) | Deferred for intraday; Magpie daily path degrades gracefully |
| Magpie intraday strategy | Not implemented — future `strategies/magpie.py` |
| Screener symbol cooldown config | Config exists; enforcement not yet wired |

---

## Related Documentation

- [STATUS.md](STATUS.md) — capability matrix and backlog
- [PLAN.md](PLAN.md) — original phased plan + post-MVP appendix
- [PROGRESS.md](PROGRESS.md) — implementation tracker
- [../pro-trader-dashboard-spec.md](../pro-trader-dashboard-spec.md) — Pro Trader / scanner parity
- [../CONFIGURATION.md](../CONFIGURATION.md) — all config keys
- [../SCHWAB_API_QUICK_REFERENCE.md](../SCHWAB_API_QUICK_REFERENCE.md) — REST + Streamer screener
- [../DATAFLOWS.md](../DATAFLOWS.md) — vendor routing
