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
