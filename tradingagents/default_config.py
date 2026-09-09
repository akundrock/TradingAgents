import os

_TRADINGAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")

# Single source of truth for env-var → config-key overrides. To expose
# a new config key for environment-based override, add a row here — no
# entry-point script changes required. Coercion is driven by the type
# of the existing default, so users can keep writing plain strings in
# their .env file.
_ENV_OVERRIDES = {
    "TRADINGAGENTS_LLM_PROVIDER":         "llm_provider",
    "TRADINGAGENTS_DEEP_THINK_LLM":       "deep_think_llm",
    "TRADINGAGENTS_QUICK_THINK_LLM":      "quick_think_llm",
    "TRADINGAGENTS_LLM_BACKEND_URL":      "backend_url",
    "TRADINGAGENTS_OUTPUT_LANGUAGE":      "output_language",
    "TRADINGAGENTS_MAX_DEBATE_ROUNDS":    "max_debate_rounds",
    "TRADINGAGENTS_MAX_RISK_ROUNDS":      "max_risk_discuss_rounds",
    "TRADINGAGENTS_CHECKPOINT_ENABLED":   "checkpoint_enabled",
    "TRADINGAGENTS_BENCHMARK_TICKER":     "benchmark_ticker",
    "TRADINGAGENTS_TEMPERATURE":          "temperature",
    "TRADINGAGENTS_LLM_MAX_RETRIES":      "llm_max_retries",
    # Provider-specific reasoning/thinking knobs (None = each provider's own
    # default). Settable here for non-interactive runs; the CLI also offers an
    # interactive choice, which is skipped when the matching var is set.
    "TRADINGAGENTS_GOOGLE_THINKING_LEVEL":   "google_thinking_level",
    "TRADINGAGENTS_OPENAI_REASONING_EFFORT": "openai_reasoning_effort",
    "TRADINGAGENTS_ANTHROPIC_EFFORT":        "anthropic_effort",
    # Schwab/TOS market data configuration.
    "TRADINGAGENTS_SCHWAB_CLIENT_ID":        "schwab_client_id",
    "TRADINGAGENTS_SCHWAB_CLIENT_SECRET":    "schwab_client_secret",
    "TRADINGAGENTS_SCHWAB_TOKENS_PATH":      "schwab_tokens_path",
    "TRADINGAGENTS_SCHWAB_REDIRECT_URI":     "schwab_redirect_uri",
    "TRADINGAGENTS_MAGPIE_ENABLED":          "magpie_enabled",
    "TRADINGAGENTS_MAGPIE_INTRADAY_INTERVAL": "magpie_intraday_interval",
    "TRADINGAGENTS_MAGPIE_INTRADAY_LOOKBACK_MINUTES": "magpie_intraday_lookback_minutes",
    "TRADINGAGENTS_MAGPIE_SESSION_MODE":     "magpie_session_mode",
    "TRADINGAGENTS_MAGPIE_TIMEZONE":         "magpie_timezone",
    "TRADINGAGENTS_MAGPIE_IMPLIED_MOVE_LOCK_TIME": "magpie_implied_move_lock_time",
    "TRADINGAGENTS_MES_JOURNAL_DIR":         "mes_journal_dir",
    "TRADINGAGENTS_LOG_LEVEL":                  "log_level",
    "TRADINGAGENTS_INTRADAY_PREMARKET_ANALYSTS": "intraday_premarket_analysts",
    "TRADINGAGENTS_INTRADAY_RESTORE_PREMARKET_BIAS": "intraday_restore_premarket_bias",
    "TRADINGAGENTS_INTRADAY_LAZY_BIAS_ON_GATE1": "intraday_lazy_bias_on_gate1",
    # Pro Trader volume pressure (optional gates; default off in DEFAULT_CONFIG).
    "TRADINGAGENTS_PRO_TRADER_REQUIRE_BUY_PRESSURE": "pro_trader_require_buy_pressure",
    "TRADINGAGENTS_PRO_TRADER_REQUIRE_SELL_PRESSURE": "pro_trader_require_sell_pressure",
    "TRADINGAGENTS_PRO_TRADER_MIN_BUY_PERCENT": "pro_trader_min_buy_percent",
    "TRADINGAGENTS_PRO_TRADER_MIN_SELL_PERCENT": "pro_trader_min_sell_percent",
    "TRADINGAGENTS_PRO_TRADER_REQUIRE_PRICE_VOLUME_TREND": "pro_trader_require_price_volume_trend",
    "TRADINGAGENTS_PRO_TRADER_MIN_PREMARKET_VOLUME": "pro_trader_min_premarket_volume",
    # Swing trade profile injected into the pro-trader post-gate LLM chain
    # (docs/superpowers/specs/2026-09-09-swing-trade-profile-design.md).
    "TRADINGAGENTS_PRO_TRADER_SWING_PROFILE": "pro_trader_swing_profile_enabled",
    "TRADINGAGENTS_PRO_TRADER_DTE_WEEKS": "pro_trader_swing_dte_weeks",
    "TRADINGAGENTS_PRO_TRADER_TARGET_DELTA": "pro_trader_swing_target_delta",
    "TRADINGAGENTS_PRO_TRADER_HOLD_HORIZON_DAYS": "pro_trader_swing_hold_horizon_days",
    # Pro Trader Gate 1 thresholds.
    "TRADINGAGENTS_PRO_TRADER_MIN_RS_TIMEFRAMES": "pro_trader_min_rs_timeframes",
    "TRADINGAGENTS_PRO_TRADER_REQUIRE_DAILY_RRS": "pro_trader_require_daily_rrs",
    "TRADINGAGENTS_PRO_TRADER_REQUIRE_RELATIVE_VOLUME": "pro_trader_require_relative_volume",
    "TRADINGAGENTS_PRO_TRADER_MIN_RELATIVE_VOLUME": "pro_trader_min_relative_volume",
    "TRADINGAGENTS_PRO_TRADER_RELATIVE_VOLUME_ON_MISSING": "pro_trader_relative_volume_on_missing",
    "TRADINGAGENTS_PRO_TRADER_REQUIRE_SECTOR_ALIGNMENT": "pro_trader_require_sector_alignment",
    "TRADINGAGENTS_PRO_TRADER_SECTOR_ALIGNMENT_MODE": "pro_trader_sector_alignment_mode",
    "TRADINGAGENTS_PRO_TRADER_REQUIRE_PRICE_BEYOND_OR": "pro_trader_require_price_beyond_or",
    "TRADINGAGENTS_INTRADAY_SCREENER_MIN_PRICE": "intraday_screener_min_price",
    "TRADINGAGENTS_INTRADAY_SCREENER_REQUIRE_SP500": "intraday_screener_require_sp500",
    "TRADINGAGENTS_INTRADAY_SCREENER_SOURCE": "intraday_screener_source",
    "TRADINGAGENTS_INTRADAY_SCREENER_PREFILTER_LIMIT": "intraday_screener_prefilter_limit",
    "TRADINGAGENTS_INTRADAY_SCREENER_INCLUDE_DAILY_RRS": "intraday_screener_include_daily_rrs",
    "TRADINGAGENTS_INTRADAY_SCREENER_RANK_BY": "intraday_screener_rank_by",
    "TRADINGAGENTS_INTRADAY_SCREENER_DIRECTION": "intraday_screener_direction",
}


_BOOL_TRUE = ("true", "1", "yes", "on")
_BOOL_FALSE = ("false", "0", "no", "off")


def _coerce(value: str, reference):
    """Coerce env-var string to the type of the existing default value.

    Invalid values raise ``ValueError`` rather than silently falling back to a
    default — a misspelled boolean (e.g. ``treu``) or non-numeric int should fail
    loudly at startup, not quietly misconfigure an unattended run.
    """
    if isinstance(reference, bool):
        normalized = value.strip().lower()
        if normalized in _BOOL_TRUE:
            return True
        if normalized in _BOOL_FALSE:
            return False
        raise ValueError(
            f"expected a boolean ({'/'.join(_BOOL_TRUE + _BOOL_FALSE)}), got {value!r}"
        )
    if isinstance(reference, list):
        parts = [part.strip() for part in value.replace(",", " ").split() if part.strip()]
        if reference and isinstance(reference[0], int):
            return [int(part) for part in parts]
        return parts
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


def _apply_env_overrides(config: dict) -> dict:
    """Apply TRADINGAGENTS_* env vars to the config dict in-place."""
    for env_var, key in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            continue
        try:
            config[key] = _coerce(raw, config.get(key))
        except ValueError as exc:
            raise ValueError(f"Invalid value for {env_var}: {exc}") from exc
    return config


DEFAULT_CONFIG = _apply_env_overrides({
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR", os.path.join(_TRADINGAGENTS_HOME, "logs")),
    "data_cache_dir": os.getenv("TRADINGAGENTS_CACHE_DIR", os.path.join(_TRADINGAGENTS_HOME, "cache")),
    "memory_log_path": os.getenv("TRADINGAGENTS_MEMORY_LOG_PATH", os.path.join(_TRADINGAGENTS_HOME, "memory", "trading_memory.md")),
    # Optional cap on the number of resolved memory log entries. When set,
    # the oldest resolved entries are pruned once this limit is exceeded.
    # Pending entries are never pruned. None disables rotation entirely.
    "memory_log_max_entries": None,
    # LLM settings
    "llm_provider": "openai",
    "deep_think_llm": "gpt-5.5",
    "quick_think_llm": "gpt-5.4-mini",
    # When None, each provider's client falls back to its own default endpoint
    # (api.openai.com for OpenAI, generativelanguage.googleapis.com for Gemini, ...).
    # The CLI overrides this per provider when the user picks one. Keeping a
    # provider-specific URL here would leak (e.g. OpenAI's /v1 was previously
    # being forwarded to Gemini, producing malformed request URLs).
    "backend_url": None,
    # Provider-specific thinking configuration
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    "anthropic_effort": None,           # "high", "medium", "low"
    # Sampling temperature, forwarded to every provider when set. None leaves
    # each provider at its own default. Lower values reduce run-to-run
    # variation on models that honor it; reasoning models largely ignore it
    # and no setting makes LLM output bit-identical across runs (see README).
    "temperature": None,
    # SDK retry budget forwarded to every provider chat client. None leaves each
    # provider/SDK at its own default (usually 2). Raise it to ride out bursty
    # 429 throttling on rate-limited deployments instead of aborting a run (#1091).
    "llm_max_retries": None,
    # Checkpoint/resume: when True, LangGraph saves state after each node
    # so a crashed run can resume from the last successful step.
    "checkpoint_enabled": False,
    # Output language for analyst reports and final decision
    # Internal agent debate stays in English for reasoning quality
    "output_language": "English",
    # Logging level name for configure_logging() (DEBUG | INFO | WARNING | ERROR)
    "log_level": "WARNING",
    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    # News / data fetching parameters
    # Increase for longer lookback strategies or to broaden macro coverage;
    # decrease to reduce token usage in agent prompts.
    "news_article_limit": 20,             # max articles per ticker (ticker-news)
    "global_news_article_limit": 10,      # max articles for global/macro news
    "global_news_lookback_days": 7,       # macro news lookback window
    # Search queries used by get_global_news for macro headlines. Extend or
    # replace to broaden geographic / sector coverage.
    "global_news_queries": [
        "Federal Reserve interest rates inflation",
        "S&P 500 earnings GDP economic outlook",
        "geopolitical risk trade war sanctions",
        "ECB Bank of England BOJ central bank policy",
        "oil commodities supply chain energy",
    ],
    # Data vendor configuration
    # Category-level configuration (default for all tools in category).
    # The configured value is the exact vendor chain — requests are NOT silently
    # routed to vendors you didn't choose. For ordered fallback, list several,
    # e.g. "yfinance,alpha_vantage". "default" uses all available vendors.
    "data_vendors": {
        "core_stock_apis": "yfinance",       # Options: alpha_vantage, yfinance, schwab
        "technical_indicators": "yfinance",  # Options: alpha_vantage, yfinance, schwab
        "market_internals": "yfinance",      # Options: yfinance, schwab
        "implied_move_data": "schwab",       # Options: schwab, yfinance
        "fundamental_data": "yfinance",      # Options: alpha_vantage, yfinance
        "news_data": "yfinance",             # Options: alpha_vantage, yfinance
        "macro_data": "fred",                # Options: fred (needs FRED_API_KEY)
        "prediction_markets": "polymarket",  # Options: polymarket (keyless)
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Example: "get_stock_data": "alpha_vantage",  # Override category default
    },
    # Schwab/TOS credentials and token cache location (used when vendor is
    # configured as "schwab" for OHLC and indicators).
    "schwab_client_id": None,
    "schwab_client_secret": None,
    "schwab_tokens_path": None,
    "schwab_redirect_uri": None,
    # Deterministic Alpha-Zone-Pro strategy integration. The current phase only
    # wires the graph/state seam; the full engine arrives with intraday and
    # market-internals data support.
    "magpie_enabled": False,
    "magpie_min_confirmations": 3,
    "magpie_transition_only": True,
    "magpie_require_direction_flip": True,
    "magpie_intraday_interval": "5m",
    "magpie_intraday_lookback_minutes": 390,
    "magpie_session_mode": "rth",  # Options: rth, extended
    "magpie_timezone": "America/New_York",
    "magpie_implied_move_lock_time": "10:30",
    # MES copilot journal location. None derives <results_dir>/mes_journal.
    "mes_journal_dir": None,
    # Intraday watchlist scanning
    "intraday_enabled": False,
    "watchlist": [],
    "intraday_scan_interval_minutes": 5,
    "intraday_bar_close_delay_seconds": 15,
    "intraday_premarket_setup_time": "09:00",
    "intraday_session_start": "09:30",
    "intraday_session_end": "16:00",
    "intraday_timezone": "America/New_York",
    "intraday_mtf_timeframes": [5, 30],
  # ``5m_resample``: one 5m pricehistory call per symbol; 15/30/60 derived locally.
  # ``multi``: legacy parallel fetch per Schwab-supported timeframe.
    "intraday_mtf_fetch_mode": "5m_resample",
    "intraday_benchmark_cache_per_scan": True,
    "intraday_strategy": "base_momentum",
    "intraday_require_daily_bias_alignment": True,
    "intraday_gate2_mode": None,
    "intraday_lazy_bias_on_gate1": True,
    "intraday_lazy_bias_analysts": ["market"],
    # Skip the Bull/Bear/Research-Manager debate for lazy bias and rate the
    # analyst report(s) directly in one quick-model call, to keep pace with
    # the scanner's refresh interval on slower LLM backends.
    "intraday_lazy_bias_fast_mode": True,
    # When orb_breakout runs with the dynamic screener, Gate 2 defaults to SuperTrend
    # (no LLM). Set true to skip Gate 2 entirely for that workflow.
    "intraday_orb_breakout_screener_disable_gate2": False,
    "pro_trader_benchmark": "SPY",
    "pro_trader_entry_mode": "wick_touch",
    "pro_trader_min_rs_timeframes": 2,
    "pro_trader_require_sector_alignment": True,
    # ``strict`` also requires the sector ETF itself to trend in the trade direction.
    "pro_trader_sector_alignment_mode": "lenient",
    "pro_trader_require_relative_volume": True,
    "pro_trader_min_relative_volume": 1.0,
    # 5m relative volume needs ~20 prior sessions; ``skip`` ignores the check when the
    # fetched history is too shallow rather than failing every symbol.
    "pro_trader_relative_volume_on_missing": "skip",
    # Daily RRS is a regime filter; off by default so intraday reversals are not blocked.
    "pro_trader_require_daily_rrs": False,
    "pro_trader_require_price_beyond_or": True,
    "pro_trader_key_level_atr_buffer": 0.5,
    "pro_trader_require_buy_pressure": False,
    "pro_trader_require_sell_pressure": False,
    "pro_trader_min_buy_percent": 55.0,
    "pro_trader_min_sell_percent": 55.0,
    "pro_trader_require_price_volume_trend": False,
    "pro_trader_min_premarket_volume": 0,
    # Swing trade profile injected into the pro-trader post-gate LLM chain
    # (docs/superpowers/specs/2026-09-09-swing-trade-profile-design.md).
    "pro_trader_swing_profile_enabled": True,
    "pro_trader_swing_dte_weeks": "3-4",
    "pro_trader_swing_target_delta": "0.70",
    "pro_trader_swing_hold_horizon_days": "1",
    "intraday_output_dir": os.path.join(_TRADINGAGENTS_HOME, "intraday"),
    "intraday_max_concurrent_symbols": 5,
    "intraday_signal_cooldown_bars": 3,
    "intraday_screener_enabled": False,
    "intraday_screener_interval_minutes": 5,
    "intraday_screener_keys": ["NASDAQ_VOLUME_0", "NYSE_VOLUME_0"],
    "intraday_screener_candidate_limit": 50,
    "intraday_screener_prefilter_limit": 100,
    "intraday_screener_rrs_timeframes": [5, 15, 30, 60],
    "intraday_screener_include_daily_rrs": True,
    "intraday_screener_min_rrs_aligned": 2,
    "intraday_screener_require_relative_volume": False,
    "intraday_screener_min_price": 10.0,
    "intraday_screener_require_sp500": True,
    "intraday_screener_source": "auto",
    "intraday_screener_rank_mode": "pass_only",
    "intraday_screener_rank_rrs_timeframe": "30m",
    "intraday_screener_rank_by": "aligned",
    "intraday_screener_max_concurrent_symbols": None,
    "intraday_screener_max_watchlist": 10,
    "intraday_screener_direction": "short",
    "intraday_screener_start_time": "10:00",
    "intraday_screener_symbol_cooldown_minutes": 30,
    "intraday_screener_run_premarket_for_new": False,
    "intraday_screener_filters": ["rrs"],
    "intraday_screener_filter_mode": "any",
    "screener_orb_direction": "long",
    "screener_orb_entry_mode": "wick_touch",
    "screener_orb_require_price_beyond": True,
    "screener_orb_min_range_width": 0.0,
    # Analyst wire keys for pre-market daily bias (market, social, news, fundamentals)
    "intraday_premarket_analysts": ["market", "social", "news", "fundamentals"],
    "intraday_restore_premarket_bias": True,
    # Benchmark for alpha calculation in the reflection layer.
    # ``benchmark_ticker`` (when set) overrides the suffix map for all
    # tickers; leave it None to use ``benchmark_map`` for auto-detection
    # based on the ticker's exchange suffix. SPY remains the US default
    # so the reflection label keeps reading "Alpha vs SPY" for US tickers
    # while non-US tickers get their regional index automatically.
    "benchmark_ticker": None,
    "benchmark_map": {
        ".NS":  "^NSEI",       # NSE India (Nifty 50)
        ".BO":  "^BSESN",      # BSE India (Sensex)
        ".T":   "^N225",       # Tokyo (Nikkei 225)
        ".HK":  "^HSI",        # Hong Kong (Hang Seng)
        ".L":   "^FTSE",       # London (FTSE 100)
        ".TO":  "^GSPTSE",     # Toronto (TSX Composite)
        ".AX":  "^AXJO",       # Australia (ASX 200)
        ".SS":  "000001.SS",   # Shanghai (SSE Composite)
        ".SZ":  "399001.SZ",   # Shenzhen (SZSE Component)
        "":     "SPY",         # default for US-listed tickers (no suffix)
    },
})
