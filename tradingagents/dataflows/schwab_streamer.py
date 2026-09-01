from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from tradingagents.dataflows.schwab import (
    _get_access_token,
    get_user_preference,
    refresh_access_token_if_possible,
)

logger = logging.getLogger(__name__)

SCREENER_SERVICE = "SCREENER_EQUITY"
DEFAULT_SCREENER_FIELDS = "0,1,2,3,4"

LEVELONE_EQUITIES_SERVICE = "LEVELONE_EQUITIES"
CHART_EQUITY_SERVICE = "CHART_EQUITY"
INTERNAL_LEVELONE_FIELDS = "0,3,17,29,33,34,35"
INTERNAL_CHART_EQUITY_FIELDS = "0,1,2,3,4,5,6,7"
INTERNAL_SYMBOLS = ("$ADD", "$TICK", "$VOLD")
INTERNAL_CHART_SYMBOLS = ("$ADD", "$VOLD")


@dataclass
class ScreenerCandidate:
    symbol: str
    last_price: float = 0.0
    net_change: float = 0.0
    net_percent_change: float = 0.0
    volume: int = 0
    total_volume: int = 0
    trades: int = 0
    market_share: float = 0.0
    screener_key: str = ""


@dataclass
class SchwabStreamerSession:
    socket_url: str
    customer_id: str
    correl_id: str
    channel: str
    function_id: str
    access_token: str


def _build_streamer_session(user_pref: dict, access_token: str) -> SchwabStreamerSession:
    streamer_info = user_pref.get("streamerInfo") or []
    if not streamer_info:
        raise RuntimeError("No streamerInfo in userPreference response")

    info = streamer_info[0]
    socket_url = info.get("streamerSocketUrl") or info.get("streamerSocketURL")
    if not socket_url:
        raise RuntimeError("Missing streamerSocketUrl in userPreference")

    customer_id = (
        info.get("schwabClientCustomerId")
        or user_pref.get("schwabClientCustomerId")
        or ""
    )
    channel = info.get("schwabClientChannel") or user_pref.get("schwabClientChannel") or ""
    function_id = (
        info.get("schwabClientFunctionId")
        or user_pref.get("schwabClientFunctionId")
        or ""
    )
    return SchwabStreamerSession(
        socket_url=socket_url,
        customer_id=customer_id,
        correl_id=str(uuid.uuid4()),
        channel=channel,
        function_id=function_id,
        access_token=access_token,
    )


def _build_request(
    request_id: int,
    service: str,
    command: str,
    session: SchwabStreamerSession,
    parameters: dict[str, str],
) -> dict[str, Any]:
    return {
        "requests": [
            {
                "requestid": str(request_id),
                "service": service,
                "command": command,
                "SchwabClientCustomerId": session.customer_id,
                "SchwabClientCorrelId": session.correl_id,
                "parameters": parameters,
            }
        ]
    }


def _field_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _field_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_internal_symbol(symbol: str) -> bool:
    return str(symbol or "").startswith("$")


def _content_symbol(item: dict[str, Any]) -> str:
    return str(item.get("key") or item.get("0") or "").strip().upper()


def _parse_level_one_internal(symbol: str, item: dict[str, Any]) -> float | None:
    """Parse LEVELONE_EQUITIES update for $ADD / $TICK / $VOLD.

    Index internals often stream 0 in field 3; field 29 is regular-market last.
    """
    fields = ["3", "29", "33", "17", "10"] if _is_internal_symbol(symbol) else ["3"]
    for key in fields:
        value = _field_float(item.get(key))
        if _is_internal_symbol(symbol) and value == 0:
            continue
        return value
    return None


def _parse_chart_equity_internal(symbol: str, item: dict[str, Any]) -> float | None:
    """Parse CHART_EQUITY candle close for internals symbols."""
    value = _field_float(item.get("4"))
    if _is_internal_symbol(symbol) and value == 0:
        return None
    if item.get("4") is None:
        return None
    return value


def _apply_internal_reading(readings: dict[str, float], symbol: str, value: float | None) -> None:
    if not symbol or value is None:
        return
    if _is_internal_symbol(symbol) and symbol in ("$ADD", "$VOLD") and value == 0:
        return
    readings[symbol] = value


def _ingest_internals_envelope(envelope: dict[str, Any], readings: dict[str, float]) -> None:
    for data in envelope.get("data", []):
        service = data.get("service")
        for content in data.get("content", []):
            if not isinstance(content, dict):
                continue
            symbol = _content_symbol(content)
            if service == LEVELONE_EQUITIES_SERVICE:
                _apply_internal_reading(readings, symbol, _parse_level_one_internal(symbol, content))
            elif service == CHART_EQUITY_SERVICE:
                _apply_internal_reading(readings, symbol, _parse_chart_equity_internal(symbol, content))


def _parse_screener_item(item: dict[str, Any]) -> ScreenerCandidate | None:
    """Parse one screener item; supports named fields and numeric string keys."""
    symbol = str(
        item.get("symbol")
        or item.get("Symbol")
        or item.get("0")
        or ""
    ).strip().upper()
    if not symbol:
        return None
    return ScreenerCandidate(
        symbol=symbol,
        last_price=_field_float(
            item.get("lastPrice") or item.get("LastPrice") or item.get("3")
        ),
        net_change=_field_float(
            item.get("netChange") or item.get("NetChange") or item.get("4")
        ),
        net_percent_change=_field_float(
            item.get("netPercentChange") or item.get("NetPercentChange") or item.get("5")
        ),
        volume=_field_int(item.get("volume") or item.get("Volume") or item.get("6")),
        total_volume=_field_int(
            item.get("totalVolume") or item.get("TotalVolume") or item.get("7")
        ),
        trades=_field_int(item.get("trades") or item.get("Trades") or item.get("8")),
        market_share=_field_float(
            item.get("marketShare") or item.get("MarketShare") or item.get("9")
        ),
    )


def _total_volume_anomaly(candidates: list[ScreenerCandidate]) -> bool:
    if len(candidates) < 2:
        return False
    totals = [c.total_volume for c in candidates if c.total_volume > 0]
    if len(totals) < 2:
        return False
    most_common = max(set(totals), key=totals.count)
    same_count = sum(1 for v in totals if v == most_common)
    return same_count / len(totals) > 0.8


def _effective_volume(candidate: ScreenerCandidate, *, use_volume_field: bool) -> int:
    if use_volume_field and candidate.volume > 0:
        return candidate.volume
    return candidate.total_volume or candidate.volume


def _format_candidate_volume(candidate: ScreenerCandidate) -> str:
    vol = candidate.volume or 0
    total = candidate.total_volume or 0
    if vol and total and vol != total:
        return f"{candidate.symbol}(vol={vol},total={total})"
    return f"{candidate.symbol}({total or vol})"


def _parse_screener_items(content: dict[str, Any], screener_key: str) -> list[ScreenerCandidate]:
    raw_items = content.get("4")
    if raw_items is None:
        raw_items = content.get("items")
    if raw_items is None and isinstance(content.get("Items"), list):
        raw_items = content.get("Items")
    if not isinstance(raw_items, list):
        return []

    candidates: list[ScreenerCandidate] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        parsed = _parse_screener_item(item)
        if parsed is None:
            continue
        candidates.append(parsed)
        candidates[-1].screener_key = screener_key
    return candidates


def _merge_candidates(
    batches: list[list[ScreenerCandidate]],
    limit: int,
) -> list[ScreenerCandidate]:
    merged: dict[str, ScreenerCandidate] = {}
    flat = [c for batch in batches for c in batch]
    use_volume_field = _total_volume_anomaly(flat)
    if use_volume_field:
        totals = [c.total_volume for c in flat if c.total_volume > 0]
        most_common = max(set(totals), key=totals.count)
        same_count = sum(1 for v in totals if v == most_common)
        logger.warning(
            "Streamer screener: identical totalVolume on %d/%d items — ranking by volume field",
            same_count,
            len(totals),
        )

    for batch in batches:
        for candidate in batch:
            existing = merged.get(candidate.symbol)
            if existing is None:
                merged[candidate.symbol] = candidate
                continue
            if _effective_volume(candidate, use_volume_field=use_volume_field) > _effective_volume(
                existing, use_volume_field=use_volume_field
            ):
                merged[candidate.symbol] = candidate

    ranked = sorted(
        merged.values(),
        key=lambda c: (
            _effective_volume(c, use_volume_field=use_volume_field),
            c.volume,
        ),
        reverse=True,
    )
    return ranked[:limit]


class StreamerTokenLoginError(RuntimeError):
    """Streamer ADMIN LOGIN rejected due to invalid or expired OAuth token."""

    def __init__(self, code: Any, msg: str) -> None:
        self.code = code
        self.msg = msg
        super().__init__(f"Streamer LOGIN failed: code={code} msg={msg}")


def _is_login_token_error(code: Any, msg: str | None) -> bool:
    if code == 3:
        return True
    text = (msg or "").lower()
    return "expired" in text or "invalid" in text


def _prepare_streamer_session(user_pref: dict) -> SchwabStreamerSession:
    """Build streamer session using access token after any REST-side refresh."""
    access_token, _ = _get_access_token()
    return _build_streamer_session(user_pref, access_token)


async def _streamer_login(
    ws: Any,
    session: SchwabStreamerSession,
    request_id: int,
) -> int:
    login = _build_request(
        request_id,
        "ADMIN",
        "LOGIN",
        session,
        {
            "Authorization": session.access_token,
            "SchwabClientChannel": session.channel,
            "SchwabClientFunctionId": session.function_id,
        },
    )
    await ws.send(json.dumps(login))

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
        envelope = json.loads(raw)
        for resp in envelope.get("response", []):
            if resp.get("service") == "ADMIN" and resp.get("command") == "LOGIN":
                content = resp.get("content", {}) or {}
                code = content.get("code")
                msg = str(content.get("msg") or "")
                if code == 0:
                    return request_id + 1
                if _is_login_token_error(code, msg):
                    raise StreamerTokenLoginError(code, msg)
                raise RuntimeError(
                    f"Streamer LOGIN failed: code={code} msg={msg}"
                )
    raise RuntimeError("Streamer LOGIN timed out")


async def _collect_screener_candidates(
    ws: Any,
    session: SchwabStreamerSession,
    keys: list[str],
    request_id: int,
    *,
    limit: int,
    timeout_seconds: float,
    debug_capture: list[dict[str, Any]] | None = None,
) -> list[ScreenerCandidate]:
    subscribe = _build_request(
        request_id,
        SCREENER_SERVICE,
        "SUBS",
        session,
        {
            "keys": ",".join(keys),
            "fields": DEFAULT_SCREENER_FIELDS,
        },
    )
    request_id += 1
    await ws.send(json.dumps(subscribe))

    collected: list[list[ScreenerCandidate]] = []
    listen_deadline = time.monotonic() + timeout_seconds
    seen_keys: set[str] = set()
    batch_sizes: dict[str, int] = {}

    while time.monotonic() < listen_deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
        except asyncio.TimeoutError:
            if len(seen_keys) >= len(keys):
                break
            continue

        envelope = json.loads(raw)
        for data in envelope.get("data", []):
            if data.get("service") != SCREENER_SERVICE:
                continue
            for content in data.get("content", []):
                if not isinstance(content, dict):
                    continue
                screener_key = str(content.get("key") or content.get("0") or "")
                if screener_key:
                    seen_keys.add(screener_key)
                    items = _parse_screener_items(content, screener_key)
                    if items:
                        collected.append(items)
                        batch_sizes[screener_key] = len(items)
                        if debug_capture is not None and not debug_capture:
                            raw_items = content.get("4") or content.get("items") or content.get("Items")
                            debug_capture.append(
                                {
                                    "screener_key": screener_key,
                                    "content": content,
                                    "first_item": raw_items[0] if isinstance(raw_items, list) and raw_items else None,
                                }
                            )

        if len(seen_keys) >= len(keys) and collected:
            break

    merged = _merge_candidates(collected, limit)
    logger.info(
        "Streamer screener: received keys=%s batches=%s merged_candidates=%d",
        sorted(seen_keys),
        batch_sizes,
        len(merged),
    )
    if merged:
        top = ", ".join(_format_candidate_volume(c) for c in merged[:8])
        logger.info("Streamer screener top volume: %s", top)
    return merged


async def _run_screener_session(
    session: SchwabStreamerSession,
    keys: list[str],
    *,
    limit: int,
    timeout_seconds: float,
    debug_capture: list[dict[str, Any]] | None = None,
) -> list[ScreenerCandidate]:
    import websockets

    logger.info(
        "Streamer connecting to %s keys=%s limit=%d",
        session.socket_url,
        keys,
        limit,
    )
    async with websockets.connect(session.socket_url, open_timeout=15) as ws:
        request_id = await _streamer_login(ws, session, 1)
        logger.info("Streamer LOGIN ok")
        return await _collect_screener_candidates(
            ws,
            session,
            keys,
            request_id,
            limit=limit,
            timeout_seconds=timeout_seconds,
            debug_capture=debug_capture,
        )


async def _fetch_screener_candidates_async(
    keys: list[str],
    *,
    limit: int = 50,
    timeout_seconds: float = 30.0,
    debug_capture: list[dict[str, Any]] | None = None,
) -> list[ScreenerCandidate]:
    logger.info("Streamer screener fetch starting keys=%s limit=%d", keys, limit)
    user_pref = get_user_preference()
    session = _prepare_streamer_session(user_pref)

    try:
        return await _run_screener_session(
            session, keys, limit=limit, timeout_seconds=timeout_seconds, debug_capture=debug_capture
        )
    except StreamerTokenLoginError:
        logger.info(
            "Streamer LOGIN token rejected; refreshing OAuth token and retrying"
        )
        refresh_access_token_if_possible()
        session = _prepare_streamer_session(user_pref)
        return await _run_screener_session(
            session,
            keys,
            limit=limit,
            timeout_seconds=timeout_seconds,
            debug_capture=debug_capture,
        )


async def _collect_internals_quotes(
    ws: Any,
    session: SchwabStreamerSession,
    request_id: int,
    *,
    timeout_seconds: float,
) -> dict[str, float]:
    readings: dict[str, float] = {}

    levelone = _build_request(
        request_id,
        LEVELONE_EQUITIES_SERVICE,
        "SUBS",
        session,
        {
            "keys": ",".join(INTERNAL_SYMBOLS),
            "fields": INTERNAL_LEVELONE_FIELDS,
        },
    )
    request_id += 1
    chart = _build_request(
        request_id,
        CHART_EQUITY_SERVICE,
        "SUBS",
        session,
        {
            "keys": ",".join(INTERNAL_CHART_SYMBOLS),
            "fields": INTERNAL_CHART_EQUITY_FIELDS,
        },
    )
    await ws.send(json.dumps(levelone))
    await ws.send(json.dumps(chart))

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
        except asyncio.TimeoutError:
            if all(symbol in readings for symbol in INTERNAL_SYMBOLS):
                break
            continue

        _ingest_internals_envelope(json.loads(raw), readings)
        if all(symbol in readings for symbol in INTERNAL_SYMBOLS):
            break

    return readings


async def _run_internals_session(
    session: SchwabStreamerSession,
    *,
    timeout_seconds: float,
) -> dict[str, float]:
    import websockets

    async with websockets.connect(session.socket_url, open_timeout=15) as ws:
        request_id = await _streamer_login(ws, session, 1)
        return await _collect_internals_quotes(
            ws,
            session,
            request_id,
            timeout_seconds=timeout_seconds,
        )


async def _fetch_internals_quotes_async(
    *,
    timeout_seconds: float = 5.0,
) -> dict[str, float]:
    user_pref = get_user_preference()
    session = _prepare_streamer_session(user_pref)
    try:
        return await _run_internals_session(session, timeout_seconds=timeout_seconds)
    except StreamerTokenLoginError:
        logger.info("Streamer internals LOGIN rejected; refreshing OAuth token and retrying")
        refresh_access_token_if_possible()
        session = _prepare_streamer_session(user_pref)
        return await _run_internals_session(session, timeout_seconds=timeout_seconds)


def fetch_internals_quotes(*, timeout_seconds: float = 5.0) -> dict[str, float]:
    """Fetch live $ADD / $TICK / $VOLD readings from the Schwab streamer."""
    try:
        return asyncio.run(
            _fetch_internals_quotes_async(timeout_seconds=timeout_seconds)
        )
    except Exception as exc:
        logger.warning("Schwab streamer internals fetch failed: %s", exc)
        return {}


class SchwabEquityScreener:
    """Short-lived Schwab Streamer client for SCREENER_EQUITY universe discovery."""

    def fetch_top_symbols(
        self,
        keys: list[str],
        limit: int = 50,
        *,
        timeout_seconds: float = 30.0,
        debug_capture: list[dict[str, Any]] | None = None,
    ) -> list[ScreenerCandidate]:
        if not keys:
            return []
        try:
            return asyncio.run(
                _fetch_screener_candidates_async(
                    keys,
                    limit=limit,
                    timeout_seconds=timeout_seconds,
                    debug_capture=debug_capture,
                )
            )
        except Exception as exc:
            logger.warning("Schwab equity screener failed: %s", exc)
            return []
