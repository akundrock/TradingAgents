from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cli.main import _expand_watchlist, _resolve_intraday_watchlist, app


@pytest.mark.unit
@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["NVDA", "AAPL"], ["NVDA", "AAPL"]),
        (["NVDA,AAPL,SPY"], ["NVDA", "AAPL", "SPY"]),
        (["NVDA AAPL SPY"], ["NVDA", "AAPL", "SPY"]),
        (["nvda", "aapl"], ["NVDA", "AAPL"]),
    ],
)
def test_expand_watchlist(values, expected):
    assert _expand_watchlist(values) == expected


@pytest.mark.unit
def test_resolve_intraday_watchlist_prefers_positional_symbols():
    assert _resolve_intraday_watchlist(["aapl"], ["NVDA"]) == ["NVDA", "AAPL"]


@pytest.mark.unit
def test_resolve_intraday_watchlist_merges_flag_and_positional_symbols():
    assert _resolve_intraday_watchlist(["AAPL", "SPY"], ["NVDA"]) == ["NVDA", "AAPL", "SPY"]


@pytest.mark.unit
@patch("cli.main.WatchlistScanner")
@patch("cli.main.TradingAgentsGraph")
def test_intraday_accepts_positional_symbols(mock_graph, mock_scanner):
    mock_scanner.return_value.run.return_value = MagicMock()
    runner = CliRunner()
    result = runner.invoke(app, ["intraday", "NVDA", "AAPL", "SPY", "--dry-run", "--no-premarket"])
    assert result.exit_code == 0, result.output
    config = mock_graph.call_args.kwargs["config"]
    assert config["watchlist"] == ["NVDA", "AAPL", "SPY"]


@pytest.mark.unit
@patch("cli.main.WatchlistScanner")
@patch("cli.main.TradingAgentsGraph")
def test_intraday_accepts_comma_separated_watchlist(mock_graph, mock_scanner):
    mock_scanner.return_value.run.return_value = MagicMock()
    runner = CliRunner()
    result = runner.invoke(app, ["intraday", "--watchlist", "NVDA,AAPL,SPY", "--dry-run", "--no-premarket"])
    assert result.exit_code == 0, result.output
    config = mock_graph.call_args.kwargs["config"]
    assert config["watchlist"] == ["NVDA", "AAPL", "SPY"]


@pytest.mark.unit
@patch("cli.main.WatchlistScanner")
@patch("cli.main.TradingAgentsGraph")
def test_intraday_merges_watchlist_flag_with_positional_symbols(mock_graph, mock_scanner):
    mock_scanner.return_value.run.return_value = MagicMock()
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["intraday", "--watchlist", "NVDA", "AAPL", "SPY", "--dry-run", "--no-premarket"],
    )
    assert result.exit_code == 0, result.output
    config = mock_graph.call_args.kwargs["config"]
    assert config["watchlist"] == ["NVDA", "AAPL", "SPY"]


@pytest.mark.unit
@patch("cli.main.WatchlistScanner")
@patch("cli.main.TradingAgentsGraph")
def test_intraday_accepts_repeated_watchlist_flags(mock_graph, mock_scanner):
    mock_scanner.return_value.run.return_value = MagicMock()
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "intraday",
            "--watchlist",
            "NVDA",
            "--watchlist",
            "AAPL",
            "--watchlist",
            "SPY",
            "--dry-run",
            "--no-premarket",
        ],
    )
    assert result.exit_code == 0, result.output
    config = mock_graph.call_args.kwargs["config"]
    assert config["watchlist"] == ["NVDA", "AAPL", "SPY"]


@pytest.mark.unit
@patch("cli.main.WatchlistScanner")
@patch("cli.main.TradingAgentsGraph")
def test_intraday_passes_selected_analysts_to_graph(mock_graph, mock_scanner):
    mock_scanner.return_value.run.return_value = MagicMock()
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "intraday",
            "NVDA",
            "--dry-run",
            "--analysts",
            "market,news,fundamentals",
        ],
    )
    assert result.exit_code == 0, result.output
    assert mock_graph.call_args.kwargs["selected_analysts"] == (
        "market",
        "news",
        "fundamentals",
    )
    assert "Premarket analysts" in result.output


@pytest.mark.unit
@patch("cli.main.WatchlistScanner")
@patch("cli.main.TradingAgentsGraph")
@patch("cli.main.Live")
def test_intraday_live_passes_callbacks_to_graph(mock_live, mock_graph, mock_scanner):
    mock_scanner.return_value.run.return_value = MagicMock()
    mock_live.return_value.__enter__ = MagicMock(return_value=None)
    mock_live.return_value.__exit__ = MagicMock(return_value=False)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["intraday", "NVDA", "--dry-run", "--no-premarket", "--live"],
    )
    assert result.exit_code == 0, result.output
    callbacks = mock_graph.call_args.kwargs.get("callbacks")
    assert callbacks is not None
    assert len(callbacks) == 1
    scanner_callbacks = mock_scanner.call_args.kwargs.get("callbacks")
    assert scanner_callbacks == callbacks


@pytest.mark.unit
@patch("cli.main.configure_logging", return_value="INFO")
@patch("cli.main.WatchlistScanner")
@patch("cli.main.TradingAgentsGraph")
def test_intraday_verbose_sets_info_logging(mock_graph, mock_scanner, mock_configure):
    mock_scanner.return_value.run.return_value = MagicMock()
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["intraday", "NVDA", "--dry-run", "--no-premarket", "--verbose"],
    )
    assert result.exit_code == 0, result.output
    mock_configure.assert_called_once_with("INFO")
    assert "Log level" in result.output
