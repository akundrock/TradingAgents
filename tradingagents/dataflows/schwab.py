from __future__ import annotations

import base64
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import pandas as pd
import requests
from dateutil.relativedelta import relativedelta
from stockstats import wrap

from .config import get_config
from .errors import NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError
from .stockstats_utils import _assert_ohlcv_not_stale

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
SCHWAB_RATE_LIMIT_MAX_RETRIES = 3
SCHWAB_RATE_LIMIT_BASE_DELAY = 2.0
AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
PRICE_HISTORY_URL = "https://api.schwabapi.com/marketdata/v1/pricehistory"
OPTION_CHAINS_URL = "https://api.schwabapi.com/marketdata/v1/chains"
DEFAULT_REDIRECT_URI = "https://127.0.0.1"


class SchwabNotConfiguredError(VendorNotConfiguredError):
    """Raised when Schwab is selected but auth configuration is missing."""


class SchwabRateLimitError(VendorRateLimitError):
    """Raised when Schwab throttles requests."""


def _normalize_symbol(symbol: str) -> str:
    sym = (symbol or "").strip().upper()
    if not sym:
        return sym
    # Keep broker-native futures notation intact (e.g. /MESU26).
    if sym.startswith("/"):
        return sym
    return sym


def _get_setting(key: str, env_var: str) -> str | None:
    raw = os.getenv(env_var)
    if raw:
        return raw.strip()
    value = get_config().get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _tokens_path() -> Path:
    configured = _get_setting("schwab_tokens_path", "TRADINGAGENTS_SCHWAB_TOKENS_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    cache_dir = Path(get_config()["data_cache_dir"]).expanduser().resolve()
    return cache_dir / "schwab_tokens.json"


def get_schwab_redirect_uri() -> str:
    return _get_setting("schwab_redirect_uri", "TRADINGAGENTS_SCHWAB_REDIRECT_URI") or DEFAULT_REDIRECT_URI


def get_schwab_credentials() -> tuple[str, str]:
    client_id = _get_setting("schwab_client_id", "TRADINGAGENTS_SCHWAB_CLIENT_ID")
    client_secret = _get_setting("schwab_client_secret", "TRADINGAGENTS_SCHWAB_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SchwabNotConfiguredError(
            "Schwab credentials are missing. Set TRADINGAGENTS_SCHWAB_CLIENT_ID and "
            "TRADINGAGENTS_SCHWAB_CLIENT_SECRET."
        )
    return client_id, client_secret


def get_authorization_url(client_id: str, redirect_uri: str | None = None) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri or get_schwab_redirect_uri(),
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def parse_auth_code_from_redirect(redirect_url_or_code: str) -> str | None:
    raw = (redirect_url_or_code or "").strip()
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        params = parse_qs(parsed.query)
        if "code" not in params:
            return None
        return unquote(params["code"][0])
    # Allow pasting the code directly.
    return raw


def exchange_code_for_tokens(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str | None = None,
) -> dict:
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri or get_schwab_redirect_uri(),
        },
        headers=_token_headers(client_id, client_secret),
        timeout=30,
    )

    if response.status_code == 429:
        raise SchwabRateLimitError("Schwab token exchange was rate-limited")
    if response.status_code != 200:
        raise SchwabNotConfiguredError(
            "Schwab token exchange failed. Verify redirect URI and app credentials."
        )

    tokens = response.json()
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not access_token or not refresh_token:
        raise SchwabNotConfiguredError("Schwab token exchange did not return access/refresh tokens")
    _save_tokens(tokens)
    return tokens


def bootstrap_tokens_from_redirect(
    redirect_url_or_code: str,
    *,
    client_id: str | None = None,
    client_secret: str | None = None,
    redirect_uri: str | None = None,
) -> dict:
    code = parse_auth_code_from_redirect(redirect_url_or_code)
    if not code:
        raise SchwabNotConfiguredError(
            "Could not extract authorization code from input. Paste the full redirect URL or raw code."
        )

    if not client_id or not client_secret:
        client_id, client_secret = get_schwab_credentials()

    return exchange_code_for_tokens(code, client_id, client_secret, redirect_uri=redirect_uri)


def _load_tokens() -> dict | None:
    token_path = _tokens_path()
    if not token_path.exists():
        return None
    try:
        return json.loads(token_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_tokens(tokens: dict) -> None:
    token_path = _tokens_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")


def _token_headers(client_id: str, client_secret: str) -> dict[str, str]:
    encoded = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("utf-8")
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _refresh_access_token(refresh_token: str) -> str:
    client_id, client_secret = get_schwab_credentials()

    response = requests.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        headers=_token_headers(client_id, client_secret),
        timeout=30,
    )

    if response.status_code == 429:
        raise SchwabRateLimitError("Schwab token refresh was rate-limited")
    if response.status_code != 200:
        raise SchwabNotConfiguredError(
            "Schwab token refresh failed. Re-authorize and update the cached token file."
        )

    tokens = response.json()
    _save_tokens(tokens)
    access_token = tokens.get("access_token")
    if not access_token:
        raise SchwabNotConfiguredError("Schwab token refresh response had no access_token")
    return access_token


def _get_access_token() -> tuple[str, str | None]:
    inline_access = os.getenv("TRADINGAGENTS_SCHWAB_ACCESS_TOKEN")
    if inline_access:
        return inline_access.strip(), None

    tokens = _load_tokens()
    if not tokens:
        raise SchwabNotConfiguredError(
            "No Schwab tokens found. Generate tokens first and store them at "
            f"{_tokens_path()} or set TRADINGAGENTS_SCHWAB_ACCESS_TOKEN."
        )

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not access_token:
        raise SchwabNotConfiguredError("Cached Schwab token file has no access_token")
    return access_token, refresh_token


def refresh_access_token_if_possible() -> str:
    """Refresh OAuth access token using cached refresh_token; raises if unavailable."""
    _, refresh_token = _get_access_token()
    if not refresh_token:
        raise SchwabNotConfiguredError(
            "Cannot refresh Schwab access token (no refresh_token). "
            "Re-authorize or remove TRADINGAGENTS_SCHWAB_ACCESS_TOKEN."
        )
    return _refresh_access_token(refresh_token)


def _fetch_price_history(
    symbol: str,
    start_date: str,
    end_date: str,
    frequency_type: str,
    frequency: int,
) -> list[dict]:
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d") + relativedelta(days=1)

    return _fetch_price_history_range(
        symbol=symbol,
        start_dt=start_dt,
        end_dt=end_dt,
        frequency_type=frequency_type,
        frequency=frequency,
    )


def _schwab_get_with_retry(
    url: str,
    params: dict,
    *,
    rate_limit_message: str,
) -> requests.Response:
    """GET with token refresh on 401 and exponential backoff on 429."""
    access_token, refresh_token = _get_access_token()

    for attempt in range(SCHWAB_RATE_LIMIT_MAX_RETRIES + 1):
        response = requests.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=45,
        )
        if response.status_code == 401 and refresh_token:
            access_token = _refresh_access_token(refresh_token)
            response = requests.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
                timeout=45,
            )

        if response.status_code == 429:
            if attempt < SCHWAB_RATE_LIMIT_MAX_RETRIES:
                delay = SCHWAB_RATE_LIMIT_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "Schwab rate limited, retrying in %.0fs (attempt %d/%d)",
                    delay,
                    attempt + 1,
                    SCHWAB_RATE_LIMIT_MAX_RETRIES,
                )
                time.sleep(delay)
                continue
            raise SchwabRateLimitError(rate_limit_message)
        return response

    raise SchwabRateLimitError(rate_limit_message)


def _fetch_price_history_range(
    symbol: str,
    start_dt: datetime,
    end_dt: datetime,
    frequency_type: str,
    frequency: int,
) -> list[dict]:
    if end_dt <= start_dt:
        raise ValueError("end_dt must be after start_dt")

    params = {
        "symbol": _normalize_symbol(symbol),
        "startDate": int(start_dt.timestamp() * 1000),
        "endDate": int(end_dt.timestamp() * 1000),
        "frequencyType": frequency_type,
        "frequency": frequency,
        "needExtendedHoursData": True,
    }

    response = _schwab_get_with_retry(
        PRICE_HISTORY_URL,
        params,
        rate_limit_message="Schwab price history request was rate-limited",
    )

    if response.status_code == 401:
        raise SchwabNotConfiguredError("Schwab access token unauthorized; re-authorize and retry")
    if response.status_code >= 400:
        raise NoMarketDataError(symbol, _normalize_symbol(symbol), f"Schwab HTTP {response.status_code}")

    payload = response.json()
    candles = payload.get("candles") or []
    if payload.get("empty") or not candles:
        raise NoMarketDataError(symbol, _normalize_symbol(symbol), "Schwab returned no candles")
    return candles


def _authenticated_get(url: str, params: dict, *, no_data_symbol: str, no_data_detail: str) -> dict:
    response = _schwab_get_with_retry(
        url,
        params,
        rate_limit_message="Schwab request was rate-limited",
    )

    if response.status_code == 401:
        raise SchwabNotConfiguredError("Schwab access token unauthorized; re-authorize and retry")
    if response.status_code >= 400:
        raise NoMarketDataError(no_data_symbol, _normalize_symbol(no_data_symbol), f"Schwab HTTP {response.status_code}")

    payload = response.json()
    if isinstance(payload, dict) and payload.get("status") == "FAILED":
        raise NoMarketDataError(no_data_symbol, _normalize_symbol(no_data_symbol), no_data_detail)
    return payload


def _extract_atm_iv(payload: dict, spot_price: float) -> tuple[float, datetime]:
    call_map = payload.get("callExpDateMap") or {}
    if not isinstance(call_map, dict) or not call_map:
        raise NoMarketDataError("IMPLIED_MOVE", "IMPLIED_MOVE", "Schwab option chain had no callExpDateMap")

    best_exp: datetime | None = None
    best_iv: float | None = None
    best_gap: float | None = None
    for exp_key, strikes in call_map.items():
        exp_raw = str(exp_key).split(":", maxsplit=1)[0]
        try:
            exp_dt = datetime.strptime(exp_raw, "%Y-%m-%d")
        except ValueError:
            continue
        if not isinstance(strikes, dict):
            continue
        for strike_key, contracts in strikes.items():
            try:
                strike = float(strike_key)
            except (TypeError, ValueError):
                continue
            if not isinstance(contracts, list) or not contracts:
                continue
            contract = contracts[0]
            iv = pd.to_numeric(contract.get("volatility"), errors="coerce")
            if pd.isna(iv):
                continue
            gap = abs(strike - spot_price)
            if best_exp is None or exp_dt < best_exp or (exp_dt == best_exp and (best_gap is None or gap < best_gap)):
                best_exp = exp_dt
                best_gap = gap
                best_iv = float(iv)

    if best_exp is None or best_iv is None:
        raise NoMarketDataError("IMPLIED_MOVE", "IMPLIED_MOVE", "Schwab option chain had no usable ATM IV")
    return best_iv, best_exp


def get_implied_move_data(symbol: str, trade_date: str, as_of_datetime: str) -> str:
    """Return IV/options-derived implied move bounds for Magpie in CSV text form."""
    canonical = _normalize_symbol(symbol)
    as_of_dt = datetime.strptime(as_of_datetime, "%Y-%m-%d %H:%M:%S")
    trade_dt = datetime.strptime(trade_date, "%Y-%m-%d")

    intraday_candles = _fetch_price_history_range(
        symbol=symbol,
        start_dt=datetime(trade_dt.year, trade_dt.month, trade_dt.day, 9, 30),
        end_dt=as_of_dt,
        frequency_type="minute",
        frequency=5,
    )
    intraday_df = _candles_to_df(
        intraday_candles, symbol, trade_date, session_timezone="America/New_York"
    )
    intraday_df = intraday_df[intraday_df["Date"] <= pd.to_datetime(as_of_datetime)]
    if intraday_df.empty:
        raise NoMarketDataError(symbol, canonical, "no intraday bars to compute implied move context")

    spot_price = float(intraday_df["Close"].iloc[-1])
    day_high = float(intraday_df["High"].max())
    day_low = float(intraday_df["Low"].min())

    daily_candles = _fetch_price_history(
        symbol=symbol,
        start_date=(trade_dt - relativedelta(days=7)).strftime("%Y-%m-%d"),
        end_date=trade_date,
        frequency_type="daily",
        frequency=1,
    )
    daily_df = _candles_to_df(daily_candles, symbol, trade_date)
    daily_df = daily_df.sort_values("Date")
    prev_rows = daily_df[daily_df["Date"] < pd.to_datetime(trade_date)]
    if prev_rows.empty:
        raise NoMarketDataError(symbol, canonical, "no prior close available for implied move baseline")
    prev_close = float(prev_rows["Close"].iloc[-1])

    option_payload = _authenticated_get(
        OPTION_CHAINS_URL,
        params={
            "symbol": canonical,
            "contractType": "ALL",
            "strategy": "SINGLE",
            "strikeCount": 8,
            "includeUnderlyingQuote": "TRUE",
            "fromDate": trade_date,
            "toDate": (trade_dt + relativedelta(days=14)).strftime("%Y-%m-%d"),
        },
        no_data_symbol=symbol,
        no_data_detail="Schwab options chain unavailable for implied-move calculation",
    )

    iv_percent, expiry = _extract_atm_iv(option_payload, spot_price)
    iv_decimal = iv_percent / 100.0
    dte_days = max((expiry.date() - trade_dt.date()).days, 1)
    mean_price = (max(day_high, prev_close) + min(day_low, prev_close)) / 2.0
    implied_move = (spot_price * iv_decimal * ((dte_days / 365.0) ** 0.5)) * 0.95
    upper = mean_price + implied_move
    lower = mean_price - implied_move

    out = pd.DataFrame(
        [
            {
                "Date": as_of_datetime,
                "Symbol": canonical,
                "UnderlyingPrice": round(spot_price, 4),
                "ImpliedVolatility": round(iv_decimal, 6),
                "DTE": int(dte_days),
                "Mean": round(mean_price, 4),
                "Upper": round(upper, 4),
                "Lower": round(lower, 4),
                "Expiry": expiry.strftime("%Y-%m-%d"),
            }
        ]
    )

    csv_string = out.to_csv(index=False)
    header = (
        f"# Implied move data for {canonical} on {trade_date} as of {as_of_datetime}\n"
        f"# Total records: {len(out)}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return header + csv_string


def _candles_to_df(
    candles: list[dict],
    symbol: str,
    curr_date: str,
    *,
    session_timezone: str | None = None,
) -> pd.DataFrame:
    rows = []
    for c in candles:
        ts = pd.to_datetime(c.get("datetime"), unit="ms", utc=True)
        if session_timezone:
            ts = ts.tz_convert(session_timezone).tz_localize(None)
        else:
            ts = ts.tz_localize(None)
        rows.append(
            {
                "Date": ts,
                "Open": c.get("open"),
                "High": c.get("high"),
                "Low": c.get("low"),
                "Close": c.get("close"),
                "Volume": c.get("volume"),
            }
        )

    data = pd.DataFrame(rows)
    data = data.dropna(subset=["Date", "Close"])
    data = data.sort_values("Date")
    # Include the full requested day regardless of candle intraday timestamp.
    cutoff_exclusive = pd.to_datetime(curr_date) + pd.Timedelta(days=1)
    data = data[data["Date"] < cutoff_exclusive]
    _assert_ohlcv_not_stale(data, curr_date, symbol, _normalize_symbol(symbol))
    if data.empty:
        raise NoMarketDataError(symbol, _normalize_symbol(symbol), "no rows on or before requested date")
    return data


def _load_ohlcv(symbol: str, curr_date: str, look_back_days: int = 400) -> pd.DataFrame:
    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = end_dt - relativedelta(days=max(look_back_days, 30))
    candles = _fetch_price_history(
        symbol=symbol,
        start_date=start_dt.strftime("%Y-%m-%d"),
        end_date=end_dt.strftime("%Y-%m-%d"),
        frequency_type="daily",
        frequency=1,
    )
    return _candles_to_df(candles, symbol, curr_date)


def get_stock(symbol: str, start_date: str, end_date: str) -> str:
    """Return Schwab OHLCV history as CSV text in the same format as other vendors."""
    candles = _fetch_price_history(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        frequency_type="daily",
        frequency=1,
    )
    data = _candles_to_df(candles, symbol, end_date)

    # Keep the date range strict and deterministic for downstream consumers.
    start_dt = pd.to_datetime(start_date)
    end_exclusive = pd.to_datetime(end_date) + pd.Timedelta(days=1)
    data = data[(data["Date"] >= start_dt) & (data["Date"] < end_exclusive)]
    if data.empty:
        raise NoMarketDataError(symbol, _normalize_symbol(symbol), "no rows in requested date range")

    data = data.copy()
    for col in ["Open", "High", "Low", "Close"]:
        data[col] = pd.to_numeric(data[col], errors="coerce").round(2)

    csv_string = data.to_csv(index=False)
    label = _normalize_symbol(symbol)
    header = f"# Stock data for {label} from {start_date} to {end_date}\n"
    header += f"# Total records: {len(data)}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    return header + csv_string


def get_intraday_stock(
    symbol: str,
    start_datetime: str,
    end_datetime: str,
    interval: str = "5m",
) -> str:
    """Return Schwab intraday OHLCV data as CSV text for deterministic strategies."""
    freq_map = {
        "1m": ("minute", 1),
        "5m": ("minute", 5),
        "10m": ("minute", 10),
        "15m": ("minute", 15),
        "30m": ("minute", 30),
    }
    if interval not in freq_map:
        raise ValueError(f"Unsupported intraday interval: {interval}")

    start_dt = datetime.strptime(start_datetime, "%Y-%m-%d %H:%M:%S")
    end_dt = datetime.strptime(end_datetime, "%Y-%m-%d %H:%M:%S")
    frequency_type, frequency = freq_map[interval]

    candles = _fetch_price_history_range(
        symbol=symbol,
        start_dt=start_dt,
        end_dt=end_dt,
        frequency_type=frequency_type,
        frequency=frequency,
    )
    data = _candles_to_df(candles, symbol, end_dt.strftime("%Y-%m-%d"))
    data = data[(data["Date"] >= pd.to_datetime(start_datetime)) & (data["Date"] <= pd.to_datetime(end_datetime))]
    if data.empty:
        raise NoMarketDataError(
            symbol,
            _normalize_symbol(symbol),
            f"no intraday rows between {start_datetime} and {end_datetime} at {interval}",
        )

    data = data.copy()
    for col in ["Open", "High", "Low", "Close"]:
        data[col] = pd.to_numeric(data[col], errors="coerce").round(4)

    csv_string = data.to_csv(index=False)
    label = _normalize_symbol(symbol)
    header = (
        f"# Intraday stock data for {label} from {start_datetime} to {end_datetime} "
        f"at interval {interval}\n"
    )
    header += f"# Total records: {len(data)}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    return header + csv_string


# Schwab pricehistory supports minute frequencies 1, 5, 10, 15, 30 only.
# Hourly (60m) bars must be derived locally via resample_ohlcv().
SCHWAB_INTRADAY_MINUTES: frozenset[int] = frozenset({1, 5, 10, 15, 30})

_INTRADAY_MINUTE_FREQ_MAP: dict[int, tuple[str, int]] = {
    1: ("minute", 1),
    5: ("minute", 5),
    10: ("minute", 10),
    15: ("minute", 15),
    30: ("minute", 30),
}


def get_candles_multi_timeframe(
    symbol: str,
    session_start: datetime,
    as_of: datetime,
    timeframes: list[int] | None = None,
) -> dict[int, pd.DataFrame]:
    """Fetch intraday candles for multiple timeframes in parallel.

    Returns a dict keyed by interval in minutes, e.g. ``{5: df_5min, 30: df_30min}``.
    Each DataFrame has columns: Date, Open, High, Low, Close, Volume.
    """
    if as_of <= session_start:
        raise ValueError("as_of must be after session_start")

    tfs = timeframes if timeframes is not None else [5, 30]
    unsupported = [tf for tf in tfs if tf not in _INTRADAY_MINUTE_FREQ_MAP]
    if unsupported:
        raise ValueError(
            f"Unsupported intraday timeframes: {unsupported}. "
            f"Supported: {sorted(_INTRADAY_MINUTE_FREQ_MAP)}"
        )

    curr_date = as_of.strftime("%Y-%m-%d")
    canonical = _normalize_symbol(symbol)

    def _fetch_tf(minutes: int) -> tuple[int, pd.DataFrame]:
        frequency_type, frequency = _INTRADAY_MINUTE_FREQ_MAP[minutes]
        try:
            candles = _fetch_price_history_range(
                symbol=symbol,
                start_dt=session_start,
                end_dt=as_of,
                frequency_type=frequency_type,
                frequency=frequency,
            )
        except NoMarketDataError as exc:
            raise NoMarketDataError(
                symbol,
                canonical,
                f"{exc.detail} at {minutes}m",
            ) from exc
        if not candles:
            return minutes, pd.DataFrame()
        data = _candles_to_df(
            candles, symbol, curr_date, session_timezone="America/New_York"
        )
        mask = (data["Date"] >= pd.to_datetime(session_start)) & (
            data["Date"] <= pd.to_datetime(as_of)
        )
        return minutes, data.loc[mask].copy()

    result: dict[int, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=len(tfs)) as executor:
        futures = {executor.submit(_fetch_tf, tf): tf for tf in tfs}
        for future in as_completed(futures):
            tf = futures[future]
            try:
                minutes, df = future.result()
            except NoMarketDataError:
                raise
            except Exception as exc:
                raise NoMarketDataError(
                    symbol,
                    canonical,
                    f"{exc} at {tf}m",
                ) from exc
            if df.empty:
                raise NoMarketDataError(
                    symbol,
                    canonical,
                    f"no intraday rows between {session_start} and {as_of} at {minutes}m",
                )
            result[minutes] = df

    return result


def _naive_market_datetime(dt: datetime, timezone: str = "America/New_York") -> datetime:
    """Convert to naive wall-clock time in the market timezone (matches candle Date column)."""
    if dt.tzinfo is None:
        return dt
    from zoneinfo import ZoneInfo

    return dt.astimezone(ZoneInfo(timezone)).replace(tzinfo=None)


def get_intraday_5m_candles(
    symbol: str,
    session_start: datetime,
    as_of: datetime,
    *,
    fetch_start: datetime | None = None,
) -> pd.DataFrame:
    """Fetch 5m OHLCV bars (single pricehistory call per symbol).

    By default fetches from ``session_start`` through ``as_of``. Pass ``fetch_start``
    earlier (e.g. from ``rrs_intraday_fetch_start``) when hourly RRS needs prior sessions.
    """
    session_start = _naive_market_datetime(session_start)
    as_of = _naive_market_datetime(as_of)
    start_dt = _naive_market_datetime(fetch_start) if fetch_start is not None else session_start
    if as_of <= start_dt:
        raise ValueError("as_of must be after fetch start")

    curr_date = as_of.strftime("%Y-%m-%d")
    canonical = _normalize_symbol(symbol)
    candles = _fetch_price_history_range(
        symbol=symbol,
        start_dt=start_dt,
        end_dt=as_of,
        frequency_type="minute",
        frequency=5,
    )
    if not candles:
        raise NoMarketDataError(symbol, canonical, "Schwab returned no 5m candles")
    data = _candles_to_df(candles, symbol, curr_date, session_timezone="America/New_York")
    mask = (data["Date"] >= pd.to_datetime(start_dt)) & (
        data["Date"] <= pd.to_datetime(as_of)
    )
    frame = data.loc[mask].copy()
    if frame.empty:
        raise NoMarketDataError(
            symbol,
            canonical,
            f"no 5m rows between {session_start} and {as_of}",
        )
    return frame


def get_market_internals(
    start_datetime: str,
    end_datetime: str,
    interval: str = "5m",
) -> str:
    """Schwab market internals are not currently supported by this adapter."""
    raise NoMarketDataError(
        "MARKET_INTERNALS",
        "MARKET_INTERNALS",
        "Schwab adapter does not provide $ADD/$TICK/$VOLD internals",
    )


def get_indicator(symbol: str, indicator: str, curr_date: str, look_back_days: int) -> str:
    """Return technical indicator values computed from Schwab OHLCV candles."""
    best_ind_params = {
        "close_50_sma": (
            "50 SMA: A medium-term trend indicator. "
            "Usage: Identify trend direction and serve as dynamic support/resistance. "
            "Tips: It lags price; combine with faster indicators for timely signals."
        ),
        "close_200_sma": (
            "200 SMA: A long-term trend benchmark. "
            "Usage: Confirm overall market trend and identify golden/death cross setups. "
            "Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries."
        ),
        "close_10_ema": (
            "10 EMA: A responsive short-term average. "
            "Usage: Capture quick shifts in momentum and potential entry points. "
            "Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals."
        ),
        "macd": (
            "MACD: Computes momentum via differences of EMAs. "
            "Usage: Look for crossovers and divergence as signals of trend changes. "
            "Tips: Confirm with other indicators in low-volatility or sideways markets."
        ),
        "macds": (
            "MACD Signal: An EMA smoothing of the MACD line. "
            "Usage: Use crossovers with the MACD line to trigger trades. "
            "Tips: Should be part of a broader strategy to avoid false positives."
        ),
        "macdh": (
            "MACD Histogram: Shows the gap between the MACD line and its signal. "
            "Usage: Visualize momentum strength and spot divergence early. "
            "Tips: Can be volatile; complement with additional filters in fast-moving markets."
        ),
        "rsi": (
            "RSI: Measures momentum to flag overbought/oversold conditions. "
            "Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. "
            "Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis."
        ),
        "boll": (
            "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. "
            "Usage: Acts as a dynamic benchmark for price movement. "
            "Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals."
        ),
        "boll_ub": (
            "Bollinger Upper Band: Typically 2 standard deviations above the middle line. "
            "Usage: Signals potential overbought conditions and breakout zones. "
            "Tips: Confirm signals with other tools; prices may ride the band in strong trends."
        ),
        "boll_lb": (
            "Bollinger Lower Band: Typically 2 standard deviations below the middle line. "
            "Usage: Indicates potential oversold conditions. "
            "Tips: Use additional analysis to avoid false reversal signals."
        ),
        "atr": (
            "ATR: Averages true range to measure volatility. "
            "Usage: Set stop-loss levels and adjust position sizes based on current market volatility. "
            "Tips: It's a reactive measure, so use it as part of a broader risk management strategy."
        ),
        "vwma": (
            "VWMA: A moving average weighted by volume. "
            "Usage: Confirm trends by integrating price action with volume data. "
            "Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses."
        ),
        "mfi": (
            "MFI: The Money Flow Index is a momentum indicator that uses both price and volume to measure buying and selling pressure. "
            "Usage: Identify overbought (>80) or oversold (<20) conditions and confirm the strength of trends or reversals. "
            "Tips: Use alongside RSI or MACD to confirm signals; divergence between price and MFI can indicate potential reversals."
        ),
    }

    if indicator not in best_ind_params:
        raise ValueError(
            f"Indicator {indicator} is not supported. Please choose from: {list(best_ind_params.keys())}"
        )

    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = curr_date_dt - relativedelta(days=look_back_days)
    data = _load_ohlcv(symbol, curr_date, look_back_days=max(look_back_days + 260, 400))
    df = wrap(data.copy())
    df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")

    df[indicator]
    result_dict: dict[str, str] = {}
    for _, row in df.iterrows():
        key = row["Date"]
        value = row[indicator]
        result_dict[key] = "N/A" if pd.isna(value) else str(value)

    current_dt = curr_date_dt
    lines = []
    while current_dt >= before:
        date_str = current_dt.strftime("%Y-%m-%d")
        lines.append(f"{date_str}: {result_dict.get(date_str, 'N/A: Not a trading day (weekend or holiday)')}")
        current_dt = current_dt - relativedelta(days=1)

    return (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {curr_date}:\n\n"
        + "\n".join(lines)
        + "\n\n"
        + best_ind_params[indicator]
    )


USER_PREFERENCE_URL = "https://api.schwabapi.com/trader/v1/userPreference"


def get_user_preference() -> dict:
    """Fetch Schwab user preference including streamer WebSocket connection info."""
    access_token, refresh_token = _get_access_token()

    def _request(token: str) -> requests.Response:
        return requests.get(
            USER_PREFERENCE_URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30,
        )

    response = _request(access_token)
    if response.status_code == 401 and refresh_token:
        access_token = _refresh_access_token(refresh_token)
        response = _request(access_token)
    if response.status_code == 429:
        raise SchwabRateLimitError("Schwab userPreference was rate-limited")
    if response.status_code != 200:
        raise SchwabNotConfiguredError(
            f"Schwab userPreference failed (status {response.status_code})"
        )
    return response.json()
