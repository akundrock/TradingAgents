from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from tradingagents.dataflows.schwab_streamer import (
    ScreenerCandidate,
    StreamerTokenLoginError,
    _fetch_screener_candidates_async,
    _is_login_token_error,
    _merge_candidates,
    _parse_screener_items,
    _prepare_streamer_session,
)


def test_parse_screener_items():
    content = {
        "key": "NASDAQ_VOLUME_0",
        "4": [
            {
                "symbol": "NVDA",
                "lastPrice": 120.5,
                "volume": 5000000,
                "totalVolume": 80000000,
            },
            {
                "symbol": "AAPL",
                "lastPrice": 190.0,
                "volume": 3000000,
                "totalVolume": 60000000,
            },
        ],
    }
    items = _parse_screener_items(content, "NASDAQ_VOLUME_0")
    assert len(items) == 2
    assert items[0].symbol == "NVDA"
    assert items[0].total_volume == 80000000
    assert items[0].screener_key == "NASDAQ_VOLUME_0"


def test_merge_candidates_dedupes_and_ranks():
    batch_a = [
        ScreenerCandidate(symbol="NVDA", total_volume=100, volume=10),
        ScreenerCandidate(symbol="AAPL", total_volume=50, volume=5),
    ]
    batch_b = [
        ScreenerCandidate(symbol="NVDA", total_volume=80, volume=20),
        ScreenerCandidate(symbol="TSLA", total_volume=90, volume=15),
    ]
    merged = _merge_candidates([batch_a, batch_b], limit=10)
    symbols = [c.symbol for c in merged]
    assert symbols[0] == "NVDA"
    assert "TSLA" in symbols
    assert "AAPL" in symbols


def test_is_login_token_error():
    assert _is_login_token_error(3, "Login Denied.")
    assert _is_login_token_error(1, "token is invalid or has expired.")
    assert not _is_login_token_error(0, "OK")
    assert not _is_login_token_error(5, "service unavailable")


def test_prepare_streamer_session_uses_current_access_token():
    user_pref = {
        "streamerInfo": [
            {
                "streamerSocketUrl": "wss://streamer-api.schwab.com/ws",
                "schwabClientCustomerId": "CID",
                "schwabClientChannel": "N9",
                "schwabClientFunctionId": "APIAPP",
            }
        ]
    }
    with patch(
        "tradingagents.dataflows.schwab_streamer._get_access_token",
        return_value=("current-access-token", "refresh-token"),
    ):
        session = _prepare_streamer_session(user_pref)

    assert session.access_token == "current-access-token"


def test_fetch_screener_uses_token_after_user_preference_refresh():
    user_pref = {
        "streamerInfo": [
            {
                "streamerSocketUrl": "wss://streamer-api.schwab.com/ws",
                "schwabClientCustomerId": "CID",
                "schwabClientChannel": "N9",
                "schwabClientFunctionId": "APIAPP",
            }
        ]
    }
    token_state = {"access": "stale-token", "refresh": "refresh-token"}
    call_order: list[str] = []

    def fake_get_user_preference():
        call_order.append("user_pref")
        token_state["access"] = "fresh-token"
        return user_pref

    def fake_get_access_token():
        call_order.append("get_token")
        return token_state["access"], token_state["refresh"]

    candidates = [ScreenerCandidate(symbol="NVDA", total_volume=100)]

    with (
        patch(
            "tradingagents.dataflows.schwab_streamer.get_user_preference",
            side_effect=fake_get_user_preference,
        ),
        patch(
            "tradingagents.dataflows.schwab_streamer._get_access_token",
            side_effect=fake_get_access_token,
        ),
        patch(
            "tradingagents.dataflows.schwab_streamer._run_screener_session",
            new_callable=AsyncMock,
            return_value=candidates,
        ) as run_mock,
    ):
        result = asyncio.run(
            _fetch_screener_candidates_async(["NASDAQ_VOLUME_0"], limit=10)
        )

    assert result == candidates
    assert call_order == ["user_pref", "get_token"]
    session_arg = run_mock.await_args.args[0]
    assert session_arg.access_token == "fresh-token"


def test_fetch_screener_retries_after_token_login_error():
    user_pref = {"streamerInfo": [{"streamerSocketUrl": "wss://example/ws"}]}
    candidates = [ScreenerCandidate(symbol="NVDA", total_volume=100)]

    with (
        patch(
            "tradingagents.dataflows.schwab_streamer.get_user_preference",
            return_value=user_pref,
        ),
        patch(
            "tradingagents.dataflows.schwab_streamer._prepare_streamer_session",
            return_value=AsyncMock(),
        ),
        patch(
            "tradingagents.dataflows.schwab_streamer._run_screener_session",
            new_callable=AsyncMock,
            side_effect=[
                StreamerTokenLoginError(3, "token is invalid or has expired."),
                candidates,
            ],
        ) as run_mock,
        patch(
            "tradingagents.dataflows.schwab_streamer.refresh_access_token_if_possible",
            return_value="fresh-token",
        ) as refresh_mock,
    ):
        result = asyncio.run(
            _fetch_screener_candidates_async(["NASDAQ_VOLUME_0"], limit=10)
        )

    assert result == candidates
    assert run_mock.await_count == 2
    refresh_mock.assert_called_once()
