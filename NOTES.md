# Pro Trader Dashboard — Training Notes

Opening range and automatic levels set from ORB
Searching for strong or weak relative strength compared to the SPY
Confirm key levels on other chart timeframes (supertrend?) 5m 60m daily
volume profile compared over last 20 days (relative volume tracker) to spot institutional buying
Scanner should align on 2 of these timeframes (RSS 5m,1h,daily)

## Example tickers
Longs: FAST (8/7/26)
![alt text](image.png)
Shorts: names underperforming SPY with negative RRS on 2+ of 5m / 60m / daily

## RS screener implementation plan

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | `sp500_rs_quotes` — quote pre-rank vs SPY before 5m candle fetches (fixes volume_miss) | done |
| 2 | Daily RRS in screener; `min_rrs_aligned=2` on 5m / 60m / daily | done |
| 3 | Direction-aware pre-rank (`long` / `short` / `both`) | done |
| 4 | Alignment-first ranking (`aligned_count` then RRS magnitude) | done |
| 5 | Optional 20-day relative volume pre-filter | deferred |
| 6 | Config, docs, CLI (`--screener-direction`, `screener-debug`) | done |

### Session direction

One direction per run — set via env or CLI:

```bash
# Long day — outperformers vs SPY
TRADINGAGENTS_INTRADAY_SCREENER_DIRECTION=long

# Short day — underperformers vs SPY
TRADINGAGENTS_INTRADAY_SCREENER_DIRECTION=short
```

### Target config (after implementation)

```bash
TRADINGAGENTS_INTRADAY_SCREENER_SOURCE=sp500_rs_quotes
TRADINGAGENTS_INTRADAY_SCREENER_PREFILTER_LIMIT=100
TRADINGAGENTS_INTRADAY_SCREENER_FILTERS=rrs
TRADINGAGENTS_INTRADAY_SCREENER_DIRECTION=long   # or short
TRADINGAGENTS_INTRADAY_SCREENER_MIN_RRS_ALIGNED=2
TRADINGAGENTS_INTRADAY_SCREENER_INCLUDE_DAILY_RRS=true
TRADINGAGENTS_INTRADAY_SCREENER_RANK_BY=aligned
TRADINGAGENTS_INTRADAY_SCREENER_CANDIDATE_LIMIT=50
TRADINGAGENTS_INTRADAY_SCREENER_MAX_WATCHLIST=15
```

Full plan: `.cursor/plans/rs-focused_screener_637b9df9.plan.md`

# Timestamp 11:30 (radar vs actual)
# VIX is not represented in internals
# $VOLD value is too short 540095 vs 61940120
# VWAP 7660.95 vs 7656.18
# +2 ATR 7691.17 vs ? (MES_ATR_Range_ length 14)
# MES_Vwap_deviations_

### Internals cache + provenance (2026-09-11)
- Price-history responses are cached per 5m bucket with a 120s TTL
  (`TRADINGAGENTS_SCHWAB_PRICE_HISTORY_TTL_SECONDS`; 0 disables). Within a 5m
  bucket every poll reuses one response per (symbol, window); closed bars never
  change, and the live tail is corrected by the quotes/streamer backfills.
  Expected: radar drops from ~8-15 price-history calls/poll to 1 cold + 0 warm.
- Streamer-internals backfill readings are reused for 60s
  (`TRADINGAGENTS_SCHWAB_STREAMER_TTL_SECONDS`).
- `get_internals_frame` reports `.attrs["provenance"]`
  (`candles`/`synthetic`/`none`) and `.attrs["backfilled"]`; snapshot renders
  show synthetic $VOLD as `Δ… (synthetic)` and warn that its absolute level is
  not TOS-comparable — trust the delta/z-score, never the level.

### Internals 1m-source aggregation (2026-09-11)
- `internals_source_interval: "1m"` (config) fetches internals at 1m and keeps
  each 5m bucket's last good reading; the frame stays 5m-aligned.
- Motivation (census): 1m candles carried no additional defects and rescue
  buckets whose 5m candle was defective; $TICK close=0 defects remain possible
  but a bucket only goes blank when ALL its 1m reads are defective.
- Provenance/backfill metadata from Plan A is unaffected.