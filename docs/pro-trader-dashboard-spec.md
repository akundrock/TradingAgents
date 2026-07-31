# Pro Trader Dashboard — ThinkScript Migration Spec

Migration reference for porting the ThinkOrSwim Pro Trader Dashboard to TradingAgents intraday strategies.

**Source scripts:** `thinkorswim-scripts/thinkscript/pro-trader-dashboard/`

| Script | Purpose |
|--------|---------|
| `left/shared_SMBD.tos` | ORB, multi-TF RRS/volume labels, SuperTrend, entry arrows |
| `left/shared_ComparitiveRelativeStrength.tos` | RRS histogram vs SPY |
| `left/shared_SectorRS.tos` | Sector power-index rotation |
| `left/shared_VolumeComparison.tos` | Buy/sell volume pressure |

**TradingAgents modules:**

| TOS section | Python module | Test file |
|-------------|---------------|-----------|
| ORB lines 1–85 | `tradingagents/intraday/indicators/opening_range.py` | `tests/test_pro_trader_indicators.py` |
| RRS lines 87–386 | `tradingagents/intraday/indicators/relative_strength.py` | same |
| Relative volume 387–518 | `tradingagents/intraday/indicators/relative_volume.py` | same |
| SuperTrend 576–638 | `tradingagents/intraday/indicators/supertrend.py` | same |
| Sector RS | `tradingagents/intraday/indicators/sector_mapping.py` | same |
| Volume panel | `tradingagents/intraday/indicators/volume_pressure.py` | same |
| Strategy orchestration | `tradingagents/intraday/strategies/pro_trader_dashboard.py` | `tests/test_pro_trader_strategy.py` |

---

## 1. Timeframe model

- **Execution timeframe:** 5-minute bars
- **Higher-timeframe context:** Daily RRS (`RRSD`) for macro direction; hourly, 30m, 15m RRS for intraday persistence
- **Daily key levels:** Prior-day High/Low/Close from `DailyBiasReport.key_levels`; daily RRS from indicator module

---

## 2. Opening Range Breakout (ORB)

**Source:** `shared_SMBD.tos` lines 1–85

| Parameter | Default |
|-----------|---------|
| OR window | 9:30–10:00 ET |
| Entry window | 10:00–16:00 ET |
| Entry mode | `wick_touch` (high/low pierce OR) or `close_above` |

**State:**

- `opening_range_high` / `opening_range_low` — extrema during OR window, frozen after 10:00
- `bullish_orb` / `bearish_orb` — latched on first breakout in entry window
- `range = ORH - ORL`, `half_range = range / 2`, `mid_range = ORL + half_range`

**Measured-move targets:**

- Bull: `ORH + 0.5R`, `ORH + 1.0R`
- Bear: `ORL - 0.5R`, `ORL - 1.0R`

---

## 3. Real Relative Strength (RRS)

**Source:** `shared_SMBD.tos` lines 87–386, `shared_ComparitiveRelativeStrength.tos`

Benchmark: **SPY**, `length = 12`.

```
compared_move = close(benchmark) - close(benchmark)[length]
symbol_move   = close(symbol)   - close(symbol)[length]
symbol_atr    = WildersAverage(TrueRange, length)
benchmark_atr = WildersAverage(TrueRange(benchmark), length)

power_index   = compared_move / benchmark_atr
expected_move = power_index * symbol_atr
rrs           = (symbol_move - expected_move) / symbol_atr
```

**Interpretation:** `rrs > 0` = outperforming benchmark (ATR-normalized).

**Timeframes (TOS):** Daily, Hourly, 30m, 15m, 5m, 3m.

**TradingAgents note:** Schwab supports minute frequencies 1/5/10/15/30 only. The 60m (hourly) RRS label is computed by resampling 30m bars locally — see `tradingagents/intraday/indicators/resample.py`.

**Trader rule:** Prefer 4+ positive RRS timeframes for longs; 4+ negative for shorts. Daily RRS is the macro filter.

**Power index (sector study):** `rolling_move / atr` without benchmark adjustment.

---

## 4. Relative volume

**Source:** `shared_SMBD.tos` lines 387–518

`rvolume = current_volume / ((sum_of_historical_samples / n) * multiplier)`

| Timeframe | Samples | Multiplier | Bar offsets (TOS) |
|-----------|---------|------------|-----------------|
| Daily | 20 | 1.5 | 0..19 |
| Hourly | 20 | 1.2 | 0,7,14,...,140 |
| 30m | 20 | 1.2 | 0,13,26,...,273 |
| 15m | 20 | 1.2 | 0,26,52,...,520 |
| 5m | 20 | 1.2 | 0,78,156,...,1560 |
| 3m | 15 | 1.2 | 0,130,260,...,1950 |

**Rule:** `rvolume > 1` → confirmation (not in arrow logic).

---

## 5. Sector relative strength

**Source:** `shared_SectorRS.tos`

- Maps S&P tickers → sector ETF (XLC, XLY, XLP, XLE, XLF, XLV, XLI, XLB, XLRE, XLK, XLU, SMH)
- Power index for symbol, SPY baseline, and sector ETF
- Trade aligned with strong sector for longs, weak for shorts

Ticker map: `tradingagents/intraday/indicators/data/sector_tickers.json`

---

## 6. Volume pressure

**Source:** `shared_VolumeComparison.tos`

```
buying  = volume * (close - low)  / (high - low)
selling = volume * (high - close) / (high - low)
```

Pre-market accumulator: 4:00–9:29 ET. Visual confirmation only in TOS.

---

## 7. SuperTrend (modified)

**Source:** `shared_SMBD.tos` lines 576–638

| Parameter | Value |
|-----------|-------|
| ATR period | 2 |
| ATR factor | 1.5 |
| Smoothing | Wilders |
| Trend type | modified (HiLo/HRef/LRef) |

---

## 8. Entry signals (TOS arrows)

**Required (all must be true):**

| Factor | Long | Short |
|--------|------|-------|
| SuperTrend | long | short |
| ORB | bullish_orb latched | bearish_orb latched |
| Time | 10:00–16:00 ET | same |
| Price | above ORH | below ORL |

**Preferred (configurable in TradingAgents, default on):**

- Daily RRS direction aligned
- `min_rs_timeframes_aligned` (default 4 of 5: daily, 60m, 30m, 15m, 5m)
- Relative volume > 1 on 5m
- Sector power index aligned with trade direction
- Not entering into prior-day resistance (long) or support (short) within 0.5 ATR

---

## 9. Configuration keys

| Key | Default | Description |
|-----|---------|-------------|
| `intraday_strategy` | `pro_trader_dashboard` | Strategy registry key |
| `intraday_mtf_timeframes` | `[5, 30]` | Schwab-fetchable intervals; 60m derived via resample when using `pro_trader_dashboard` |
| `pro_trader_entry_mode` | `wick_touch` | ORB entry style |
| `pro_trader_benchmark` | `SPY` | RRS benchmark symbol |
| `pro_trader_min_rs_timeframes` | `4` | Min aligned RRS TFs |
| `pro_trader_require_sector_alignment` | `true` | Sector confirmation |
| `pro_trader_require_relative_volume` | `true` | 5m rvolume > 1 |
| `pro_trader_key_level_atr_buffer` | `0.5` | ATR buffer for daily level filter |

---

## 10. Parity checklist

- [ ] ORB high/low matches TOS for same 5m session bars
- [ ] RRS at 5m matches Comparative RS study (length=12, SPY)
- [ ] SuperTrend direction matches SMBD on same bars
- [ ] Sector ETF resolved for S&P symbols
- [ ] Entry factors match arrow logic on historical dry-run
