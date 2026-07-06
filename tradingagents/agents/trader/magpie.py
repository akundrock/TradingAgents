"""Deterministic Alpha-Zone-Pro (Magpie) state preparation.

This first implementation creates a typed, audit-friendly state payload and a
stable graph seam ahead of the Trader. The full AZP engine will replace the
placeholder scoring logic in a later phase once intraday and market-internals
data plumbing lands.
"""

from __future__ import annotations

from io import StringIO
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from collections.abc import Mapping

import pandas as pd

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.interface import route_to_vendor


_PLACEHOLDER_FACTORS = [
    "momentum",
    "vwap",
    "implied_move",
    "hammer_pattern",
    "$ADD",
    "$TICK",
    "$VOLD",
]

_LONG_FACTOR_NAMES = (
    "momentum",
    "vwap",
    "implied_move",
    "hammer_pattern",
    "$ADD",
    "$TICK",
    "$VOLD",
)

_SHORT_FACTOR_NAMES = (
    "momentum_short",
    "vwap_short",
    "implied_move",
    "inverse_hammer_pattern",
    "$ADD_short",
    "$TICK_short",
    "$VOLD_short",
)


def _coerce_factor_signal(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "bullish", "bearish", "active"}
    return False


def _parse_intraday_csv(csv_payload: str) -> pd.DataFrame:
    content = "\n".join(
        line for line in (csv_payload or "").splitlines() if not line.startswith("#")
    ).strip()
    if not content:
        return pd.DataFrame()
    data = pd.read_csv(StringIO(content))
    date_col = "Date" if "Date" in data.columns else ("Datetime" if "Datetime" in data.columns else None)
    if date_col is None:
        return pd.DataFrame()
    data["Date"] = pd.to_datetime(data[date_col], errors="coerce")
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in data.columns:
            data[col] = pd.to_numeric(data[col], errors="coerce")
    data = data.dropna(subset=["Date", "Open", "High", "Low", "Close"])
    if "Volume" not in data.columns:
        data["Volume"] = 0.0
    return data.sort_values("Date").reset_index(drop=True)


def _parse_market_internals_csv(csv_payload: str) -> pd.DataFrame:
    content = "\n".join(
        line for line in (csv_payload or "").splitlines() if not line.startswith("#")
    ).strip()
    if not content:
        return pd.DataFrame()
    data = pd.read_csv(StringIO(content))
    date_col = "Date" if "Date" in data.columns else ("Datetime" if "Datetime" in data.columns else None)
    if date_col is None:
        return pd.DataFrame()
    data["Date"] = pd.to_datetime(data[date_col], errors="coerce")
    for col in ("$ADD", "$TICK", "$VOLD"):
        if col in data.columns:
            data[col] = pd.to_numeric(data[col], errors="coerce")
        else:
            data[col] = pd.NA
    return data.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)


def _parse_implied_move_csv(csv_payload: str) -> pd.DataFrame:
    content = "\n".join(
        line for line in (csv_payload or "").splitlines() if not line.startswith("#")
    ).strip()
    if not content:
        return pd.DataFrame()
    data = pd.read_csv(StringIO(content))
    if "Date" not in data.columns:
        return pd.DataFrame()
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    for col in ("Upper", "Lower"):
        if col in data.columns:
            data[col] = pd.to_numeric(data[col], errors="coerce")
    return data.dropna(subset=["Date", "Upper", "Lower"]).sort_values("Date").reset_index(drop=True)


def _hammer_flags(last_row: pd.Series) -> tuple[bool, bool]:
    body = abs(float(last_row["Close"]) - float(last_row["Open"]))
    total = float(last_row["High"]) - float(last_row["Low"])
    if total <= 0:
        return False, False
    upper_wick = float(last_row["High"]) - max(float(last_row["Open"]), float(last_row["Close"]))
    lower_wick = min(float(last_row["Open"]), float(last_row["Close"])) - float(last_row["Low"])
    max_body_ratio = 0.5
    wick_ratio = 1.5
    retracement = 0.5

    hammer = (
        lower_wick >= wick_ratio * body
        and (body / total) <= max_body_ratio
        and float(last_row["Close"]) >= (float(last_row["Low"]) + retracement * total)
    )
    inverse = (
        upper_wick >= wick_ratio * body
        and (body / total) <= max_body_ratio
        and float(last_row["Close"]) <= (float(last_row["High"]) - retracement * total)
    )
    return hammer, inverse


def build_magpie_factor_inputs_from_intraday(data: pd.DataFrame) -> dict | None:
    if data.empty or len(data) < 12:
        return None

    df = data.copy()
    close = df["Close"]
    volume = df["Volume"].replace(0, pd.NA).ffill().fillna(1.0)
    typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
    sma10 = close.rolling(10).mean()
    vwap = (typical * volume).cumsum() / volume.cumsum()

    if pd.isna(sma10.iloc[-2]) or pd.isna(sma10.iloc[-1]) or pd.isna(vwap.iloc[-2]) or pd.isna(vwap.iloc[-1]):
        return None

    trend_up = close.iloc[-1] >= close.iloc[-2]
    trend_down = close.iloc[-1] <= close.iloc[-2]
    momentum = bool(sma10.iloc[-2] < vwap.iloc[-2] and sma10.iloc[-1] > vwap.iloc[-1] and trend_up)
    momentum_short = bool(sma10.iloc[-2] > vwap.iloc[-2] and sma10.iloc[-1] < vwap.iloc[-1] and trend_down)

    hammer, inverse_hammer = _hammer_flags(df.iloc[-1])

    return {
        "momentum": momentum,
        "vwap": bool(close.iloc[-1] > vwap.iloc[-1]),
        "implied_move": False,
        "hammer_pattern": hammer,
        "$ADD": False,
        "$TICK": False,
        "$VOLD": False,
        "momentum_short": momentum_short,
        "vwap_short": bool(close.iloc[-1] < vwap.iloc[-1]),
        "inverse_hammer_pattern": inverse_hammer,
        "$ADD_short": False,
        "$TICK_short": False,
        "$VOLD_short": False,
    }


def _resolve_intraday_window(
    trade_date: str,
    *,
    lookback_minutes: int,
    session_mode: str,
    timezone_name: str,
    now: datetime | None = None,
) -> tuple[datetime | None, datetime | None, str]:
    try:
        trade_day = datetime.strptime(trade_date, "%Y-%m-%d").date()
    except ValueError:
        return None, None, "Invalid trade_date format; expected YYYY-MM-DD."

    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return None, None, f"Invalid timezone configured for Magpie: {timezone_name}"

    now_local = (now or datetime.now(tz)).astimezone(tz)
    today_local = now_local.date()
    if trade_day > today_local:
        return None, None, "future_date: trade_date is in the future for Magpie intraday evaluation."

    lookback = max(int(lookback_minutes), 60)
    mode = str(session_mode or "rth").strip().lower()
    if mode not in {"rth", "extended"}:
        mode = "rth"

    if mode == "extended":
        end_local = now_local if trade_day == today_local else datetime.combine(trade_day, datetime.max.time(), tzinfo=tz).replace(hour=23, minute=59, second=0, microsecond=0)
        start_local = end_local - timedelta(minutes=lookback)
    else:
        rth_open = datetime(trade_day.year, trade_day.month, trade_day.day, 9, 30, tzinfo=tz)
        rth_close = datetime(trade_day.year, trade_day.month, trade_day.day, 16, 0, tzinfo=tz)
        if trade_day == today_local:
            if now_local < rth_open:
                return None, None, "outside_session: market is not open yet (RTH starts at 09:30 ET)."
            end_local = min(now_local, rth_close)
        else:
            end_local = rth_close
        start_local = max(rth_open, end_local - timedelta(minutes=lookback))

    if end_local <= start_local:
        return None, None, "outside_session: no valid intraday window for Magpie evaluation."

    return start_local.replace(tzinfo=None), end_local.replace(tzinfo=None), ""


def _fetch_intraday_factor_inputs(state: Mapping[str, object], config: Mapping[str, object]) -> tuple[dict | None, str]:
    symbol = str(state.get("company_of_interest", "")).strip()
    trade_date = str(state.get("trade_date", "")).strip()
    if not symbol or not trade_date:
        return None, "Missing symbol or trade_date for intraday Magpie evaluation."

    interval = str(config.get("magpie_intraday_interval", "5m"))
    lookback_minutes = int(config.get("magpie_intraday_lookback_minutes", 390))
    session_mode = str(config.get("magpie_session_mode", "rth"))
    timezone_name = str(config.get("magpie_timezone", "America/New_York"))
    implied_move_lock_time = str(config.get("magpie_implied_move_lock_time", "10:30"))

    start_dt, end_dt, window_reason = _resolve_intraday_window(
        trade_date,
        lookback_minutes=lookback_minutes,
        session_mode=session_mode,
        timezone_name=timezone_name,
    )
    if start_dt is None or end_dt is None:
        return None, window_reason or "Unable to resolve intraday window for Magpie evaluation."

    intraday_text = route_to_vendor(
        "get_intraday_stock_data",
        symbol,
        start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        end_dt.strftime("%Y-%m-%d %H:%M:%S"),
        interval,
    )
    if isinstance(intraday_text, str) and (
        intraday_text.startswith("NO_DATA_AVAILABLE") or intraday_text.startswith("DATA_UNAVAILABLE")
    ):
        return None, intraday_text

    data = _parse_intraday_csv(str(intraday_text))
    if data.empty:
        return None, "Intraday data response could not be parsed into OHLCV rows."

    factors = build_magpie_factor_inputs_from_intraday(data)
    if factors is None:
        return None, "Insufficient intraday bars to compute Magpie factors."

    try:
        lock_hour, lock_minute = [int(x) for x in implied_move_lock_time.split(":", maxsplit=1)]
    except ValueError:
        lock_hour, lock_minute = 10, 30

    if end_dt.hour > lock_hour or (end_dt.hour == lock_hour and end_dt.minute >= lock_minute):
        implied_text = route_to_vendor(
            "get_implied_move_data",
            symbol,
            trade_date,
            end_dt.strftime("%Y-%m-%d %H:%M:%S"),
        )
        if isinstance(implied_text, str) and not (
            implied_text.startswith("NO_DATA_AVAILABLE") or implied_text.startswith("DATA_UNAVAILABLE")
        ):
            implied_df = _parse_implied_move_csv(str(implied_text))
            if not implied_df.empty:
                last = implied_df.iloc[-1]
                upper = pd.to_numeric(last.get("Upper"), errors="coerce")
                lower = pd.to_numeric(last.get("Lower"), errors="coerce")
                close_val = pd.to_numeric(data["Close"].iloc[-1], errors="coerce")
                if pd.notna(upper) and pd.notna(lower) and pd.notna(close_val):
                    factors["implied_move"] = bool(lower < close_val < upper)

    internals_text = route_to_vendor(
        "get_market_internals",
        start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        end_dt.strftime("%Y-%m-%d %H:%M:%S"),
        interval,
    )
    if isinstance(internals_text, str) and (
        internals_text.startswith("NO_DATA_AVAILABLE") or internals_text.startswith("DATA_UNAVAILABLE")
    ):
        return factors, f"market internals unavailable: {internals_text}"

    internals = _parse_market_internals_csv(str(internals_text))
    if not internals.empty:
        latest = internals.iloc[-1]
        add_val = pd.to_numeric(latest.get("$ADD"), errors="coerce")
        tick_val = pd.to_numeric(latest.get("$TICK"), errors="coerce")
        vold_val = pd.to_numeric(latest.get("$VOLD"), errors="coerce")
        if pd.notna(add_val):
            factors["$ADD"] = bool(add_val > 250)
            factors["$ADD_short"] = bool(add_val < -250)
        if pd.notna(tick_val):
            factors["$TICK"] = bool(tick_val > 900)
            factors["$TICK_short"] = bool(tick_val < -900)
        if pd.notna(vold_val):
            factors["$VOLD"] = bool(vold_val > 0)
            factors["$VOLD_short"] = bool(vold_val < 0)
    return factors, ""


def score_magpie_factors(
    factor_inputs: Mapping[str, object],
    *,
    min_confirmations: int = 3,
    transition_only: bool = True,
    require_direction_flip: bool = True,
    previous_direction: int = 0,
    previous_long_entry: bool = False,
    previous_short_entry: bool = False,
) -> dict:
    """Aggregate AZP-style factor booleans into a deterministic signal summary.

    This mirrors the ThinkScript model at the current abstraction boundary:
    factor truth values are already computed elsewhere, and this function
    handles score aggregation, direction resolution, quality tiers, and the
    transition gating that suppresses repeated entries.
    """
    long_hits = {
        name: _coerce_factor_signal(factor_inputs.get(name, False))
        for name in _LONG_FACTOR_NAMES
    }
    short_hits = {
        name: _coerce_factor_signal(factor_inputs.get(name, False))
        for name in _SHORT_FACTOR_NAMES
    }

    long_score = sum(long_hits.values())
    short_score = sum(short_hits.values())
    long_entry = long_score >= min_confirmations
    short_entry = short_score >= min_confirmations

    long_transition = long_entry and not previous_long_entry and not short_entry
    short_transition = short_entry and not previous_short_entry and not long_entry

    fresh_long = long_transition and (
        not require_direction_flip or previous_direction <= 0
    )
    fresh_short = short_transition and (
        not require_direction_flip or previous_direction >= 0
    )

    if transition_only:
        long_active = fresh_long
        short_active = fresh_short
    else:
        long_active = long_entry
        short_active = short_entry

    if long_active and not short_active:
        direction = "Buy"
    elif short_active and not long_active:
        direction = "Sell"
    elif long_entry or short_entry:
        direction = "Hold"
    else:
        direction = "Hold"

    dominant_score = max(long_score, short_score)
    if dominant_score >= 6:
        confidence = "Premium"
    elif dominant_score >= 5:
        confidence = "Standard"
    elif dominant_score >= min_confirmations:
        confidence = "Marginal"
    else:
        confidence = "No signal"

    if direction == "Buy":
        reasoning = (
            f"Long confluence reached {long_score}/{len(_LONG_FACTOR_NAMES)} confirmations "
            f"with short-side score at {short_score}."
        )
    elif direction == "Sell":
        reasoning = (
            f"Short confluence reached {short_score}/{len(_SHORT_FACTOR_NAMES)} confirmations "
            f"with long-side score at {long_score}."
        )
    elif long_entry or short_entry:
        reasoning = (
            "Confluence met the minimum threshold but transition or conflict rules "
            "suppressed a fresh directional entry."
        )
    else:
        reasoning = "Confluence did not reach the minimum confirmation threshold."

    factors = [
        {
            "name": name,
            "signal": "bullish" if active else "inactive",
            "value": f"long factor={'on' if active else 'off'}",
        }
        for name, active in long_hits.items()
    ] + [
        {
            "name": name,
            "signal": "bearish" if active else "inactive",
            "value": f"short factor={'on' if active else 'off'}",
        }
        for name, active in short_hits.items()
    ]

    return {
        "strategy": "Alpha-Zone-Pro (Magpie)",
        "status": "computed",
        "direction": direction,
        "confidence": confidence,
        "reasoning": reasoning,
        "long_score": long_score,
        "short_score": short_score,
        "factors": factors,
        "long_entry": long_entry,
        "short_entry": short_entry,
        "long_transition": long_transition,
        "short_transition": short_transition,
        "fresh_long": fresh_long,
        "fresh_short": fresh_short,
    }


def build_default_magpie_signal(enabled: bool) -> dict:
    if not enabled:
        return {
            "strategy": "Alpha-Zone-Pro (Magpie)",
            "status": "disabled",
            "direction": "Unavailable",
            "confidence": "Not evaluated",
            "reasoning": "Magpie is disabled in configuration.",
            "long_score": 0,
            "short_score": 0,
            "factors": [],
        }

    return {
        "strategy": "Alpha-Zone-Pro (Magpie)",
        "status": "unavailable",
        "direction": "Unavailable",
        "confidence": "Pending AZP data integration",
        "reasoning": (
            "Magpie is enabled, but the full Alpha-Zone-Pro engine still needs "
            "intraday candles and market-internals inputs before it can compute "
            "a live confluence score."
        ),
        "long_score": 0,
        "short_score": 0,
        "factors": [
            {
                "name": factor,
                "signal": "unavailable",
                "value": "Awaiting first-class AZP data source",
            }
            for factor in _PLACEHOLDER_FACTORS
        ],
    }


def render_magpie_signal_summary(signal: dict | None) -> str:
    signal = signal or build_default_magpie_signal(enabled=False)
    lines = [
        f"**Strategy**: {signal.get('strategy', 'Alpha-Zone-Pro (Magpie)')}",
        f"**Status**: {signal.get('status', 'unavailable')}",
        f"**Direction**: {signal.get('direction', 'Unavailable')}",
        f"**Confidence**: {signal.get('confidence', 'Not evaluated')}",
        f"**Long Score**: {signal.get('long_score', 0)}",
        f"**Short Score**: {signal.get('short_score', 0)}",
        f"**Reasoning**: {signal.get('reasoning', '')}",
    ]
    factors = signal.get("factors") or []
    if factors:
        factor_bits = [
            f"{factor.get('name', 'factor')}: {factor.get('signal', 'unknown')} ({factor.get('value', '')})"
            for factor in factors
        ]
        lines.extend(["**Factors**:", "; ".join(factor_bits)])
    return "\n".join(lines)


def create_magpie_signal_node():
    def magpie_node(state) -> dict:
        config = get_config()
        enabled = bool(config.get("magpie_enabled", False))
        if not enabled:
            return {"magpie_signal": build_default_magpie_signal(enabled=False)}

        factor_inputs = state.get("magpie_factor_inputs")
        fallback_reason = ""
        if not isinstance(factor_inputs, Mapping):
            factor_inputs, fallback_reason = _fetch_intraday_factor_inputs(state, config)
        if isinstance(factor_inputs, Mapping):
            min_confirmations = int(config.get("magpie_min_confirmations", 3))
            transition_only = bool(config.get("magpie_transition_only", True))
            require_direction_flip = bool(config.get("magpie_require_direction_flip", True))
            previous_direction = int(state.get("magpie_previous_direction", 0) or 0)
            previous_long_entry = bool(state.get("magpie_previous_long_entry", False))
            previous_short_entry = bool(state.get("magpie_previous_short_entry", False))
            return {
                "magpie_signal": score_magpie_factors(
                    factor_inputs,
                    min_confirmations=min_confirmations,
                    transition_only=transition_only,
                    require_direction_flip=require_direction_flip,
                    previous_direction=previous_direction,
                    previous_long_entry=previous_long_entry,
                    previous_short_entry=previous_short_entry,
                )
            }

        signal = build_default_magpie_signal(enabled=True)
        if fallback_reason:
            signal["reasoning"] = (
                "Magpie intraday evaluation could not compute a live confluence score: "
                f"{fallback_reason}"
            )
        return {"magpie_signal": signal}

    return magpie_node