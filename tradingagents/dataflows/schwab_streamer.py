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
)

logger = logging.getLogger(__name__)

SCREENER_SERVICE = "SCREENER_EQUITY"
DEFAULT_SCREENER_FIELDS = "0,1,2,3,4"


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
        symbol = str(item.get("symbol") or item.get("Symbol") or "").strip().upper()
        if not symbol:
            continue
        candidates.append(
            ScreenerCandidate(
                symbol=symbol,
                last_price=_field_float(item.get("lastPrice") or item.get("LastPrice")),
                net_change=_field_float(item.get("netChange") or item.get("NetChange")),
                net_percent_change=_field_float(
                    item.get("netPercentChange") or item.get("NetPercentChange")
                ),
                volume=_field_int(item.get("volume") or item.get("Volume")),
                total_volume=_field_int(item.get("totalVolume") or item.get("TotalVolume")),
                trades=_field_int(item.get("trades") or item.get("Trades")),
                market_share=_field_float(item.get("marketShare") or item.get("MarketShare")),
                screener_key=screener_key,
            )
        )
    return candidates


def _merge_candidates(
    batches: list[list[ScreenerCandidate]],
    limit: int,
) -> list[ScreenerCandidate]:
    merged: dict[str, ScreenerCandidate] = {}
    for batch in batches:
        for candidate in batch:
            existing = merged.get(candidate.symbol)
            if existing is None:
                merged[candidate.symbol] = candidate
                continue
            if candidate.total_volume > existing.total_volume:
                merged[candidate.symbol] = candidate

    ranked = sorted(
        merged.values(),
        key=lambda c: (c.total_volume, c.volume),
        reverse=True,
    )
    return ranked[:limit]


async def _fetch_screener_candidates_async(
    keys: list[str],
    *,
    limit: int = 50,
    timeout_seconds: float = 30.0,
) -> list[ScreenerCandidate]:
    import websockets

    access_token, refresh_token = _get_access_token()
    user_pref = get_user_preference()
    session = _build_streamer_session(user_pref, access_token)

    request_id = 1
    collected: list[list[ScreenerCandidate]] = []

    async with websockets.connect(session.socket_url, open_timeout=15) as ws:
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
        request_id += 1
        await ws.send(json.dumps(login))

        login_ok = False
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            envelope = json.loads(raw)
            for resp in envelope.get("response", []):
                if resp.get("service") == "ADMIN" and resp.get("command") == "LOGIN":
                    code = resp.get("content", {}).get("code")
                    if code == 0:
                        login_ok = True
                    else:
                        raise RuntimeError(
                            f"Streamer LOGIN failed: code={code} msg={resp.get('content', {}).get('msg')}"
                        )
            if login_ok:
                break
        if not login_ok:
            raise RuntimeError("Streamer LOGIN timed out")

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

        listen_deadline = time.monotonic() + timeout_seconds
        seen_keys: set[str] = set()

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

            if len(seen_keys) >= len(keys) and collected:
                break

    return _merge_candidates(collected, limit)


class SchwabEquityScreener:
    """Short-lived Schwab Streamer client for SCREENER_EQUITY universe discovery."""

    def fetch_top_symbols(
        self,
        keys: list[str],
        limit: int = 50,
        *,
        timeout_seconds: float = 30.0,
    ) -> list[ScreenerCandidate]:
        if not keys:
            return []
        try:
            return asyncio.run(
                _fetch_screener_candidates_async(
                    keys,
                    limit=limit,
                    timeout_seconds=timeout_seconds,
                )
            )
        except Exception as exc:
            logger.warning("Schwab equity screener failed: %s", exc)
            return []
