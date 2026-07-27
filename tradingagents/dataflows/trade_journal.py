from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
import json
import logging
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
from stockstats import wrap

from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.dataflows.stockstats_utils import load_ohlcv
from tradingagents.dataflows.utils import safe_ticker_component


logger = logging.getLogger(__name__)


CORE_INDICATORS: tuple[str, ...] = (
    "close_10_ema",
    "close_50_sma",
    "close_200_sma",
    "macd",
    "macds",
    "macdh",
    "rsi",
    "boll",
    "boll_ub",
    "boll_lb",
    "atr",
)

_FUT_MONTH = r"[FGHJKMNQUVXZ]"
_FUT_CONTRACT_RE = re.compile(rf"^([A-Z]{{1,4}}){_FUT_MONTH}\d{{1,2}}$")


@dataclass
class TechnicalContext:
    source: str
    snapshot: dict[str, float | str | None]
    tags: list[str]
    summary: str
    diagnostics: list[str] | None = None


def _to_float(value) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.replace(",", "").strip()
            if cleaned == "":
                return None
            return float(cleaned)
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_timestamp(row: pd.Series) -> datetime | None:
    candidates = [row.get("Fill Time"), row.get("Timestamp")]
    for value in candidates:
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        parsed = pd.to_datetime(text, errors="coerce")
        if pd.isna(parsed):
            continue
        return parsed.to_pydatetime()
    return None


def _validate_timezone_name(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid timezone: {name}") from exc


def _naive_local_to_utc_naive(ts: datetime, source_tz: str | None) -> datetime:
    tz = _validate_timezone_name(source_tz)
    if tz is None:
        return ts
    if ts.tzinfo is not None:
        return ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


def normalize_contract_root(contract: str) -> str:
    sym = (contract or "").strip().upper()
    if not sym:
        return sym
    matched = _FUT_CONTRACT_RE.match(sym)
    if matched:
        return matched.group(1)
    return sym


def market_symbol_for_root(root: str) -> str:
    normalized = normalize_contract_root(root)
    if normalized == "MES":
        return "MES=F"
    return normalized


def _futures_intraday_symbol_candidates(contract: str, ts: datetime) -> list[str]:
    """Return candidate Schwab futures symbols ordered by likelihood.

    Schwab market-data requests tend to require an explicit contract symbol
    (e.g. /MESM26), while broker exports may contain shorthand forms (MESM6).
    """
    raw = (contract or "").strip().upper().lstrip("/")
    if not raw:
        return []

    candidates: list[str] = []

    matched = _FUT_CONTRACT_RE.match(raw)
    if matched:
        root = matched.group(1)
        suffix = raw[len(root):]
        month_code = suffix[0]
        year_fragment = suffix[1:]

        def _add_sym(sym: str) -> None:
            candidates.append(sym)
            if sym.startswith("/"):
                candidates.append(sym[1:])

        # Preserve the exact contract notation first.
        _add_sym(f"/{raw}")

        year_2d = f"{ts.year % 100:02d}"
        year_1d = str(ts.year % 10)

        # Expand one-digit years to two digits and vice versa so we can probe
        # whichever variant Schwab accepts for the same expiry month.
        if len(year_fragment) == 1:
            _add_sym(f"/{root}{month_code}{year_2d}")
        elif len(year_fragment) == 2:
            _add_sym(f"/{root}{month_code}{year_fragment[-1]}")

        # If the CSV year fragment does not align with the fill timestamp year,
        # probe a candidate anchored to the trade timestamp as a fallback.
        if year_fragment not in {year_1d, year_2d}:
            _add_sym(f"/{root}{month_code}{year_2d}")

        # Final fallback to root-only symbol (least likely to work for futures).
        _add_sym(f"/{root}")
    else:
        # Non-futures or unknown format; preserve as-is and prefixed form.
        candidates.extend([f"/{raw}", raw])

    # Deduplicate while preserving order.
    deduped: list[str] = []
    seen: set[str] = set()
    for sym in candidates:
        if sym not in seen:
            deduped.append(sym)
            seen.add(sym)
    return deduped


def parse_schwab_order_history_csv(
    csv_path: str | Path,
    start_date: str | None = None,
    end_date: str | None = None,
    csv_timezone: str | None = None,
) -> list[dict]:
    logger.info("Parsing Schwab order-history CSV from %s", csv_path)
    df = pd.read_csv(csv_path)

    tz = _validate_timezone_name(csv_timezone)

    if "Status" not in df.columns:
        raise ValueError("Expected 'Status' column in order-history CSV")
    if "B/S" not in df.columns:
        raise ValueError("Expected 'B/S' column in order-history CSV")

    filled = df[df["Status"].astype(str).str.strip().str.lower() == "filled"].copy()
    logger.info("Loaded %d rows from CSV, %d rows marked Filled", len(df), len(filled))

    rows: list[dict] = []
    for _, row in filled.iterrows():
        side_raw = str(row.get("B/S", "")).strip().upper()
        side = "BUY" if side_raw.startswith("B") else "SELL"

        quantity = _to_float(row.get("filledQty"))
        if quantity is None:
            quantity = _to_float(row.get("Filled Qty"))
        price = _to_float(row.get("avgPrice"))
        if price is None:
            price = _to_float(row.get("Avg Fill Price"))

        timestamp = _parse_timestamp(row)
        if timestamp is None or quantity is None or price is None:
            logger.warning(
                "Skipping row with missing parsed values: timestamp=%r quantity=%r price=%r contract=%r",
                timestamp,
                quantity,
                price,
                row.get("Contract"),
            )
            continue

        contract = str(row.get("Contract", "")).strip().upper()
        ticker = normalize_contract_root(contract)
        trade_date = timestamp.strftime("%Y-%m-%d")

        rows.append(
            {
                "import_id": str(row.get("orderId") or row.get("Order ID") or "").strip() or None,
                "timestamp": timestamp,
                "trade_date": trade_date,
                "timezone": str(tz) if tz is not None else None,
                "ticker": ticker,
                "contract": contract,
                "side": side,
                "quantity": quantity,
                "price": price,
                "notional": _to_float(row.get("Notional Value")),
            }
        )

    rows.sort(key=lambda x: x["timestamp"])

    if start_date:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        rows = [r for r in rows if r["timestamp"] >= start_dt]
    if end_date:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        rows = [r for r in rows if r["timestamp"] < end_dt]

    logger.info("Parsed %d filled execution rows after date filters", len(rows))

    return rows


def _avg_price(fills: list[dict]) -> float:
    qty = sum(abs(f["signed_qty"]) for f in fills)
    if qty == 0:
        return 0.0
    notional = sum(f["price"] * abs(f["signed_qty"]) for f in fills)
    return notional / qty


def _finalize_round_trip(trade: dict, contract_multiplier: float) -> dict:
    direction = trade["direction"]
    fills = trade["fills"]
    entry_fills = [f for f in fills if f["signed_qty"] * direction > 0]
    exit_fills = [f for f in fills if f["signed_qty"] * direction < 0]

    qty = sum(abs(f["signed_qty"]) for f in entry_fills)
    entry_avg = _avg_price(entry_fills)
    exit_avg = _avg_price(exit_fills)

    points = (exit_avg - entry_avg) * direction
    dollars = points * qty * contract_multiplier
    ret = 0.0 if entry_avg == 0 else points / entry_avg

    opened_at = min(f["timestamp"] for f in fills)
    closed_at = max(f["timestamp"] for f in fills)
    import_fingerprint = (
        f"{trade['ticker']}|{opened_at.isoformat()}|{closed_at.isoformat()}|"
        f"{qty:.4f}|{entry_avg:.4f}|{exit_avg:.4f}"
    )

    return {
        "ticker": trade["ticker"],
        "contract": trade["contract"],
        "timezone": trade.get("timezone"),
        "direction": "LONG" if direction > 0 else "SHORT",
        "trade_date": opened_at.strftime("%Y-%m-%d"),
        "opened_at": opened_at,
        "closed_at": closed_at,
        "quantity": qty,
        "entry_avg": entry_avg,
        "exit_avg": exit_avg,
        "realized_points": points,
        "realized_dollars": dollars,
        "realized_return": ret,
        "fills": fills,
        "import_fingerprint": import_fingerprint,
    }


def reconstruct_round_trip_trades(
    fills: list[dict],
    contract_multiplier: float = 5.0,
) -> tuple[list[dict], list[dict]]:
    logger.info("Reconstructing round-trip trades from %d fills", len(fills))
    trades: list[dict] = []
    open_trade: dict | None = None
    net_qty = 0.0
    open_remainders: list[dict] = []

    def _start_trade(fill: dict, signed_qty: float) -> dict:
        return {
            "ticker": fill["ticker"],
            "contract": fill["contract"],
            "timezone": fill.get("timezone"),
            "direction": 1 if signed_qty > 0 else -1,
            "fills": [],
        }

    def _append_fill(trade: dict, fill: dict, signed_qty: float) -> None:
        trade["fills"].append(
            {
                "timestamp": fill["timestamp"],
                "price": fill["price"],
                "signed_qty": signed_qty,
                "side": "BUY" if signed_qty > 0 else "SELL",
            }
        )

    for fill in fills:
        signed_total = fill["quantity"] * (1 if fill["side"] == "BUY" else -1)
        remaining = abs(signed_total)
        unit_sign = 1 if signed_total > 0 else -1

        while remaining > 0:
            if open_trade is None:
                open_trade = _start_trade(fill, unit_sign)

            if net_qty == 0 or net_qty * unit_sign > 0:
                take = remaining
            else:
                take = min(abs(net_qty), remaining)

            signed_take = unit_sign * take
            _append_fill(open_trade, fill, signed_take)
            net_qty += signed_take
            remaining -= take

            if abs(net_qty) < 1e-12:
                trades.append(_finalize_round_trip(open_trade, contract_multiplier))
                open_trade = None

    if open_trade is not None:
        open_remainders.append(open_trade)

    logger.info(
        "Reconstructed %d closed trades with %d open remainders",
        len(trades),
        len(open_remainders),
    )

    return trades, open_remainders


def _clean_numeric(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f


def _rule_based_tags(current: pd.Series, previous: pd.Series | None) -> list[str]:
    tags: list[str] = []

    ema = _clean_numeric(current.get("close_10_ema"))
    sma50 = _clean_numeric(current.get("close_50_sma"))
    sma200 = _clean_numeric(current.get("close_200_sma"))
    prev_ema = _clean_numeric(previous.get("close_10_ema")) if previous is not None else None
    prev_sma50 = _clean_numeric(previous.get("close_50_sma")) if previous is not None else None

    if ema is not None and sma50 is not None and prev_ema is not None and prev_sma50 is not None:
        if prev_ema <= prev_sma50 and ema > sma50:
            tags.append("ema10_crossed_above_sma50")
        if prev_ema >= prev_sma50 and ema < sma50:
            tags.append("ema10_crossed_below_sma50")

    if ema is not None and sma50 is not None and sma200 is not None:
        if ema > sma50 > sma200:
            tags.append("moving_averages_aligned_bullish")
        if ema < sma50 < sma200:
            tags.append("moving_averages_aligned_bearish")

    close = _clean_numeric(current.get("Close"))
    boll_ub = _clean_numeric(current.get("boll_ub"))
    boll_lb = _clean_numeric(current.get("boll_lb"))
    if close is not None and boll_ub is not None and close > boll_ub:
        tags.append("price_above_bollinger_upper")
    if close is not None and boll_lb is not None and close < boll_lb:
        tags.append("price_below_bollinger_lower")

    macd = _clean_numeric(current.get("macd"))
    macds = _clean_numeric(current.get("macds"))
    macdh = _clean_numeric(current.get("macdh"))
    if macd is not None and macds is not None and macdh is not None:
        if macd > macds and macdh > 0:
            tags.append("macd_bullish_momentum")
        if macd < macds and macdh < 0:
            tags.append("macd_bearish_momentum")

    rsi = _clean_numeric(current.get("rsi"))
    if rsi is not None:
        if rsi >= 70:
            tags.append("rsi_overbought")
        elif rsi <= 30:
            tags.append("rsi_oversold")

    return tags


def _tags_to_summary(tags: list[str]) -> str:
    if not tags:
        return "Technical context was mixed with no strong crossover signal."

    phrases = {
        "ema10_crossed_above_sma50": "EMA10 crossed above SMA50",
        "ema10_crossed_below_sma50": "EMA10 crossed below SMA50",
        "moving_averages_aligned_bullish": "moving averages were aligned bullishly",
        "moving_averages_aligned_bearish": "moving averages were aligned bearishly",
        "price_above_bollinger_upper": "price was extended above the upper Bollinger band",
        "price_below_bollinger_lower": "price was compressed below the lower Bollinger band",
        "macd_bullish_momentum": "MACD momentum was bullish",
        "macd_bearish_momentum": "MACD momentum was bearish",
        "rsi_overbought": "RSI was overbought",
        "rsi_oversold": "RSI was oversold",
    }

    selected = [phrases[t] for t in tags if t in phrases][:3]
    if not selected:
        return "Technical context was available but did not produce a strong directional signal."
    return "At execution, " + ", ".join(selected) + "."


def _parse_data_csv(payload: str) -> pd.DataFrame:
    content = "\n".join(
        line for line in payload.splitlines() if not line.startswith("#")
    ).strip()
    if not content:
        return pd.DataFrame()
    df = pd.read_csv(StringIO(content))
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date")
    return df.reset_index(drop=True)


def _compute_snapshot(df: pd.DataFrame, ts: datetime, source: str) -> TechnicalContext:
    stock = wrap(df.copy())
    stock["Date"] = pd.to_datetime(stock["Date"], errors="coerce")
    for indicator in CORE_INDICATORS:
        stock[indicator]

    eligible = stock[stock["Date"] <= ts]
    if eligible.empty:
        eligible = stock
    idx = eligible.index[-1]
    row = stock.loc[idx]
    prev_row = stock.loc[idx - 1] if idx > 0 else None

    snapshot: dict[str, float | str | None] = {
        "Date": row.get("Date").strftime("%Y-%m-%d %H:%M:%S") if pd.notna(row.get("Date")) else None,
        "Close": _clean_numeric(row.get("Close")),
    }
    for indicator in CORE_INDICATORS:
        snapshot[indicator] = _clean_numeric(row.get(indicator))

    tags = _rule_based_tags(row, prev_row)
    return TechnicalContext(
        source=source,
        snapshot=snapshot,
        tags=tags,
        summary=_tags_to_summary(tags),
        diagnostics=[
            f"source={source}",
            f"snapshot_ts={snapshot.get('Date')}",
            f"tags={','.join(tags) if tags else 'none'}",
        ],
    )


def _intraday_context_for_timestamp(
    symbol: str,
    ts: datetime,
    source_timezone: str | None = None,
) -> TechnicalContext | None:
    from tradingagents.dataflows.schwab import get_intraday_stock

    query_ts = _naive_local_to_utc_naive(ts, source_timezone)
    start = (query_ts - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
    end = query_ts.strftime("%Y-%m-%d %H:%M:%S")
    if source_timezone:
        logger.info(
            "Converted local trade timestamp %s (%s) to UTC %s for Schwab intraday query",
            ts.strftime("%Y-%m-%d %H:%M:%S"),
            source_timezone,
            query_ts.strftime("%Y-%m-%d %H:%M:%S"),
        )
    logger.info(
        "Attempting intraday technical snapshot for %s from %s to %s via Schwab",
        symbol,
        start,
        end,
    )
    payload = get_intraday_stock(symbol, start, end, interval="5m")
    df = _parse_data_csv(payload)
    if df.empty:
        logger.warning("Intraday payload for %s returned no parsed rows", symbol)
        return None
    logger.info("Intraday snapshot for %s parsed %d OHLCV rows", symbol, len(df))
    context = _compute_snapshot(df, query_ts, source="intraday")
    context.diagnostics = (context.diagnostics or []) + [
        f"local_ts={ts.strftime('%Y-%m-%d %H:%M:%S')}",
        f"source_timezone={source_timezone or 'none'}",
        f"query_ts_utc={query_ts.strftime('%Y-%m-%d %H:%M:%S')}",
        f"query_window_utc={start}->{end}",
    ]
    return context


def _daily_context_for_timestamp(symbol_root: str, ts: datetime) -> TechnicalContext | None:
    symbol = market_symbol_for_root(symbol_root)
    logger.info(
        "Attempting daily technical snapshot fallback for %s using market symbol %s on %s",
        symbol_root,
        symbol,
        ts.strftime("%Y-%m-%d"),
    )
    df = load_ohlcv(symbol, ts.strftime("%Y-%m-%d"))
    if df is None or df.empty:
        logger.warning("Daily fallback for %s returned no OHLCV rows", symbol)
        return None
    base_df = df[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
    logger.info("Daily fallback for %s parsed %d OHLCV rows", symbol, len(base_df))
    return _compute_snapshot(base_df, ts, source="daily")


def technical_context_for_trade(trade: dict) -> dict:
    ticker = trade["ticker"]
    contract = trade.get("contract") or ticker
    trade_timezone = trade.get("timezone")
    entry_ts = trade["opened_at"]
    exit_ts = trade["closed_at"]

    entry_ctx = None
    exit_ctx = None
    entry_diagnostics: list[str] = []
    exit_diagnostics: list[str] = []

    logger.info(
        "Resolving technical context for %s/%s trade opened %s closed %s",
        ticker,
        contract,
        entry_ts.strftime("%Y-%m-%d %H:%M:%S"),
        exit_ts.strftime("%Y-%m-%d %H:%M:%S"),
    )

    entry_candidates = _futures_intraday_symbol_candidates(contract, entry_ts)
    exit_candidates = _futures_intraday_symbol_candidates(contract, exit_ts)

    if entry_candidates:
        logger.info("Entry intraday symbol candidates for %s: %s", contract, ", ".join(entry_candidates))
    if exit_candidates:
        logger.info("Exit intraday symbol candidates for %s: %s", contract, ", ".join(exit_candidates))

    for candidate in entry_candidates:
        try:
            entry_ctx = _intraday_context_for_timestamp(candidate, entry_ts, source_timezone=trade_timezone)
            if entry_ctx is not None:
                entry_diagnostics.append(f"intraday_entry_symbol={candidate}")
                break
        except Exception as exc:
            logger.warning("Intraday entry snapshot failed for %s at %s: %s", candidate, entry_ts, exc)
            entry_diagnostics.append(
                f"intraday_entry_error[{candidate}]={type(exc).__name__}: {exc}"
            )

    for candidate in exit_candidates:
        try:
            exit_ctx = _intraday_context_for_timestamp(candidate, exit_ts, source_timezone=trade_timezone)
            if exit_ctx is not None:
                exit_diagnostics.append(f"intraday_exit_symbol={candidate}")
                break
        except Exception as exc:
            logger.warning("Intraday exit snapshot failed for %s at %s: %s", candidate, exit_ts, exc)
            exit_diagnostics.append(
                f"intraday_exit_error[{candidate}]={type(exc).__name__}: {exc}"
            )

    if entry_ctx is None:
        try:
            entry_ctx = _daily_context_for_timestamp(ticker, entry_ts)
        except Exception as exc:
            logger.warning("Daily entry fallback failed for %s at %s: %s", ticker, entry_ts, exc)
            entry_diagnostics.append(f"daily_entry_error={type(exc).__name__}: {exc}")
            entry_ctx = None
    if exit_ctx is None:
        try:
            exit_ctx = _daily_context_for_timestamp(ticker, exit_ts)
        except Exception as exc:
            logger.warning("Daily exit fallback failed for %s at %s: %s", ticker, exit_ts, exc)
            exit_diagnostics.append(f"daily_exit_error={type(exc).__name__}: {exc}")
            exit_ctx = None

    if entry_ctx is None:
        entry_ctx = TechnicalContext(
            source="none",
            snapshot={},
            tags=[],
            summary="Technical context could not be retrieved for entry.",
            diagnostics=entry_diagnostics or ["entry_context_unavailable"],
        )
        logger.warning("No technical entry context available for %s trade at %s", ticker, entry_ts)
    else:
        entry_ctx.diagnostics = (entry_ctx.diagnostics or []) + entry_diagnostics
        logger.info(
            "Technical entry context for %s resolved from %s with tags=%s",
            ticker,
            entry_ctx.source,
            ",".join(entry_ctx.tags) if entry_ctx.tags else "none",
        )
    if exit_ctx is None:
        exit_ctx = TechnicalContext(
            source="none",
            snapshot={},
            tags=[],
            summary="Technical context could not be retrieved for exit.",
            diagnostics=exit_diagnostics or ["exit_context_unavailable"],
        )
        logger.warning("No technical exit context available for %s trade at %s", ticker, exit_ts)
    else:
        exit_ctx.diagnostics = (exit_ctx.diagnostics or []) + exit_diagnostics
        logger.info(
            "Technical exit context for %s resolved from %s with tags=%s",
            ticker,
            exit_ctx.source,
            ",".join(exit_ctx.tags) if exit_ctx.tags else "none",
        )

    return {
        "entry": {
            "source": entry_ctx.source,
            "snapshot": entry_ctx.snapshot,
            "tags": entry_ctx.tags,
            "summary": entry_ctx.summary,
            "diagnostics": entry_ctx.diagnostics or [],
        },
        "exit": {
            "source": exit_ctx.source,
            "snapshot": exit_ctx.snapshot,
            "tags": exit_ctx.tags,
            "summary": exit_ctx.summary,
            "diagnostics": exit_ctx.diagnostics or [],
        },
        "memory_tags": entry_ctx.tags,
        "memory_summary": entry_ctx.summary,
    }


def fundamentals_context_for_trade(trade: dict, config: dict) -> str:
    ticker = trade["ticker"].upper()
    trade_date = trade["trade_date"]
    results_dir = Path(config["results_dir"]).expanduser()
    safe_ticker = safe_ticker_component(ticker)
    state_log = results_dir / safe_ticker / "TradingAgentsStrategy_logs" / f"full_states_log_{trade_date}.json"

    if state_log.exists():
        try:
            payload = json.loads(state_log.read_text(encoding="utf-8"))
            report = payload.get("fundamentals_report", "")
            if isinstance(report, str) and report.strip():
                logger.info("Loaded fundamentals context for %s from saved state log %s", ticker, state_log)
                return report.strip()
        except Exception as exc:
            logger.warning("Could not read fundamentals context from %s: %s", state_log, exc)
            pass

    # MES is a futures contract without issuer financial statements.
    lookup_symbol = "SPY" if ticker == "MES" else ticker
    set_config(config)
    try:
        logger.info("Falling back to vendor fundamentals for %s on %s", lookup_symbol, trade_date)
        data = route_to_vendor("get_fundamentals", lookup_symbol, trade_date)
        return str(data).strip() if data else "Fundamentals context unavailable."
    except Exception as exc:
        logger.warning("Vendor fundamentals lookup failed for %s on %s: %s", lookup_symbol, trade_date, exc)
        return "Fundamentals context unavailable."


def render_trade_decision_summary(trade: dict, technical_context: dict) -> str:
    direction = trade["direction"]
    rating = "Buy" if trade["realized_dollars"] >= 0 else "Sell"
    return (
        f"Rating: {rating}\n"
        f"Imported round-trip trade for {trade['ticker']} ({direction}) from execution journal.\n"
        f"Opened: {trade['opened_at'].strftime('%Y-%m-%d %H:%M:%S')} at {trade['entry_avg']:.2f}, "
        f"closed: {trade['closed_at'].strftime('%Y-%m-%d %H:%M:%S')} at {trade['exit_avg']:.2f}, "
        f"size: {trade['quantity']:.0f}.\n"
        f"Realized points: {trade['realized_points']:+.2f}; realized return: {trade['realized_return']:+.2%}; "
        f"realized PnL: ${trade['realized_dollars']:+.2f}.\n"
        f"Technical memory: {technical_context['memory_summary']}"
    )
