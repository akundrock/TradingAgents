from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from cli.stats_handler import (
    extract_token_usage_from_llm_result,
    StatsCallbackHandler,
)


@pytest.mark.unit
def test_extract_token_usage_from_llm_output_token_usage():
    response = LLMResult(
        generations=[],
        llm_output={
            "token_usage": {
                "prompt_tokens": 42,
                "completion_tokens": 17,
            }
        },
    )
    assert extract_token_usage_from_llm_result(response) == (42, 17)


@pytest.mark.unit
def test_extract_token_usage_from_usage_metadata():
    message = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    generation = ChatGeneration(message=message)
    response = LLMResult(generations=[[generation]])
    assert extract_token_usage_from_llm_result(response) == (10, 5)


@pytest.mark.unit
def test_extract_token_usage_from_response_metadata():
    message = AIMessage(
        content="ok",
        response_metadata={
            "token_usage": {
                "prompt_tokens": 8,
                "completion_tokens": 3,
            }
        },
    )
    generation = ChatGeneration(message=message)
    response = LLMResult(generations=[[generation]])
    assert extract_token_usage_from_llm_result(response) == (8, 3)


@pytest.mark.unit
def test_stats_callback_handler_accumulates_llm_output_usage():
    handler = StatsCallbackHandler()
    response = LLMResult(
        generations=[],
        llm_output={
            "token_usage": {
                "prompt_tokens": 100,
                "completion_tokens": 25,
            }
        },
    )
    handler.on_llm_end(response)
    stats = handler.get_stats()
    assert stats["tokens_in"] == 100
    assert stats["tokens_out"] == 25


@pytest.mark.unit
@patch("tradingagents.graph.intraday_graph.IntradayTradingGraph")
def test_watchlist_scanner_passes_callbacks_to_intraday_graph(mock_intraday_graph):
    from tradingagents.intraday.scanner import WatchlistScanner

    ta_graph = MagicMock()
    config = {
        "watchlist": ["NVDA"],
        "intraday_strategy": "base_momentum",
        "intraday_output_dir": "/tmp",
    }
    callbacks = [MagicMock()]
    WatchlistScanner(config, ta_graph, callbacks=callbacks, dry_run=False)
    mock_intraday_graph.assert_called_once_with(config, callbacks=callbacks)
