from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tradingagents.intraday.gating import GateResult
from tradingagents.intraday.mtf_validator import MTFValidationResult
from tradingagents.intraday.scanner import WatchlistScanner
from tradingagents.intraday.session import DailyBiasReport, IntradaySignal
from tradingagents.intraday.strategy import StrategyResult


def _config() -> dict:
    return {
        "watchlist": ["NVDA", "AAPL"],
        "intraday_strategy": "base_momentum",
        "intraday_mtf_timeframes": [5, 30],
        "intraday_session_start": "00:00",
        "intraday_session_end": "23:59",
        "intraday_scan_interval_minutes": 5,
        "intraday_bar_close_delay_seconds": 0,
        "intraday_timezone": "America/New_York",
        "intraday_output_dir": "/tmp/tradingagents-intraday-test",
        "intraday_max_concurrent_symbols": 2,
        "intraday_premarket_analysts": ["market", "news", "fundamentals"],
    }


def _bias(symbol: str) -> DailyBiasReport:
    return DailyBiasReport(
        symbol=symbol,
        trade_date="2026-07-27",
        direction="bullish",
        key_levels={},
        summary="buy bias",
        computed_at=datetime(2026, 7, 27, 9, 0),
    )


def _mtf(symbol: str) -> MTFValidationResult:
    return MTFValidationResult(
        symbol=symbol,
        bar_time=datetime(2026, 7, 27, 10, 0),
        snapshot_5min={"Close": 100.0, "close_10_ema": 101.0, "close_20_sma": 99.0, "rsi": 55.0},
        snapshot_30min={"Close": 100.0, "close_10_ema": 101.0, "close_20_sma": 99.0},
        snapshot_daily={},
        trend_5min="up",
        trend_30min="up",
        daily_bias_direction="bullish",
        trends_aligned=True,
        vwap_5min=99.0,
        atr_5min=1.5,
        df_5min=pd.DataFrame(
            {
                "Date": pd.date_range("2026-07-27 09:30", periods=20, freq="5min"),
                "Open": [100.0] * 20,
                "High": [101.0] * 20,
                "Low": [99.0] * 20,
                "Close": [100.5] * 20,
                "Volume": [1000] * 20,
            }
        ),
    )


@pytest.mark.unit
def test_premarket_setup_called_per_symbol(monkeypatch, tmp_path):
    ta_graph = MagicMock()
    ta_graph.propagate_daily_bias.side_effect = lambda symbol, trade_date: _bias(symbol)

    config = _config()
    config["intraday_output_dir"] = str(tmp_path)
    scanner = WatchlistScanner(config, ta_graph, dry_run=True, skip_premarket=False)
    scanner.session.session_date = "2026-07-27"
    scanner._run_premarket_setup()

    assert ta_graph.propagate_daily_bias.call_count == 2
    assert "NVDA" in scanner.session.daily_bias_cache
    assert "AAPL" in scanner.session.daily_bias_cache


@pytest.mark.unit
def test_premarket_restores_cached_symbols(tmp_path):
    ta_graph = MagicMock()
    ta_graph.propagate_daily_bias.side_effect = lambda symbol, trade_date: _bias(symbol)

    config = _config()
    config["intraday_output_dir"] = str(tmp_path)
    config["watchlist"] = ["NVDA", "AAPL"]

    scanner1 = WatchlistScanner(config, ta_graph, dry_run=True, skip_premarket=False)
    scanner1.session.session_date = "2026-07-27"
    scanner1._run_premarket_setup()
    assert ta_graph.propagate_daily_bias.call_count == 2

    ta_graph.propagate_daily_bias.reset_mock()
    config["watchlist"] = ["NVDA", "AAPL", "SPY"]
    scanner2 = WatchlistScanner(config, ta_graph, dry_run=True, skip_premarket=False)
    scanner2.session.session_date = "2026-07-27"
    scanner2._run_premarket_setup()

    assert ta_graph.propagate_daily_bias.call_count == 1
    assert ta_graph.propagate_daily_bias.call_args[0][0] == "SPY"
    assert "SPY" in scanner2.session.daily_bias_cache


@pytest.mark.unit
def test_premarket_force_reruns_all(tmp_path):
    ta_graph = MagicMock()
    ta_graph.propagate_daily_bias.side_effect = lambda symbol, trade_date: _bias(symbol)

    config = _config()
    config["intraday_output_dir"] = str(tmp_path)

    scanner1 = WatchlistScanner(config, ta_graph, dry_run=True, skip_premarket=False)
    scanner1.session.session_date = "2026-07-27"
    scanner1._run_premarket_setup()

    ta_graph.propagate_daily_bias.reset_mock()
    scanner2 = WatchlistScanner(
        config, ta_graph, dry_run=True, skip_premarket=False, force_premarket=True
    )
    scanner2.session.session_date = "2026-07-27"
    scanner2._run_premarket_setup()

    assert ta_graph.propagate_daily_bias.call_count == 2


@pytest.mark.unit
def test_premarket_reruns_when_analysts_change(tmp_path):
    ta_graph = MagicMock()
    ta_graph.propagate_daily_bias.side_effect = lambda symbol, trade_date: _bias(symbol)

    config = _config()
    config["intraday_output_dir"] = str(tmp_path)
    config["watchlist"] = ["NVDA"]

    scanner1 = WatchlistScanner(config, ta_graph, dry_run=True, skip_premarket=False)
    scanner1.session.session_date = "2026-07-27"
    scanner1._run_premarket_setup()
    assert ta_graph.propagate_daily_bias.call_count == 1

    ta_graph.propagate_daily_bias.reset_mock()
    config["intraday_premarket_analysts"] = ["market", "social"]
    scanner2 = WatchlistScanner(config, ta_graph, dry_run=True, skip_premarket=False)
    scanner2.session.session_date = "2026-07-27"
    scanner2._run_premarket_setup()

    assert ta_graph.propagate_daily_bias.call_count == 1


@pytest.mark.unit
def test_on_bar_close_skips_outside_session():
    ta_graph = MagicMock()
    config = _config()
    config["intraday_session_start"] = "09:30"
    config["intraday_session_end"] = "16:00"
    scanner = WatchlistScanner(config, ta_graph, dry_run=True, skip_premarket=True)
    scanner.session.daily_bias_cache["NVDA"] = _bias("NVDA")

    called = {"count": 0}

    def fake_eval(symbol, bar_time):
        called["count"] += 1
        return None

    scanner._evaluate_symbol = fake_eval
    scanner._on_bar_close(datetime(2026, 7, 27, 8, 0))
    assert called["count"] == 0


@pytest.mark.unit
def test_on_bar_close_logs_gate_result_at_info(caplog, monkeypatch):
    import logging

    ta_graph = MagicMock()
    scanner = WatchlistScanner(_config(), ta_graph, dry_run=True, skip_premarket=True)
    scanner.session.daily_bias_cache["NVDA"] = _bias("NVDA")

    monkeypatch.setattr(
        scanner.mtf_validator,
        "evaluate",
        lambda symbol, as_of, session, config: _mtf(symbol),
    )
    monkeypatch.setattr(
        scanner.strategy,
        "check_setup",
        lambda symbol, mtf, daily_bias: StrategyResult(
            passed=False,
            direction="none",
            reason="Long setup blocked: rsi_in_range",
            factors_met=[],
            factors_missing=["rsi_in_range"],
        ),
    )
    monkeypatch.setattr(
        scanner.gating,
        "evaluate",
        lambda mtf, strategy_result, daily_bias, config: GateResult(
            passed=False,
            gate1_strategy=False,
            gate1_reason="Long setup blocked: rsi_in_range",
            gate2_mtf_alignment=False,
            gate2_reason="Not evaluated (Gate 1 failed)",
            final_direction="none",
        ),
    )

    with caplog.at_level(logging.INFO, logger="tradingagents.intraday.scanner"):
        scanner._on_bar_close(datetime(2026, 7, 27, 10, 0))

    assert any("Scan #1" in record.message for record in caplog.records)
    assert any("NVDA gates=G1=fail" in record.message for record in caplog.records)
    assert any("strategy=base_momentum" in record.message for record in caplog.records)


@pytest.mark.unit
def test_signal_emitted_when_gates_pass(monkeypatch):
    ta_graph = MagicMock()
    scanner = WatchlistScanner(_config(), ta_graph, dry_run=True, skip_premarket=True)
    scanner.session.daily_bias_cache["NVDA"] = _bias("NVDA")
    scanner.session.daily_bias_cache["AAPL"] = _bias("AAPL")

    monkeypatch.setattr(
        scanner.mtf_validator,
        "evaluate",
        lambda symbol, as_of, session, config: _mtf(symbol),
    )
    monkeypatch.setattr(
        scanner.strategy,
        "check_setup",
        lambda symbol, mtf, daily_bias: StrategyResult(
            passed=True,
            direction="long",
            reason="Long momentum setup confirmed.",
            factors_met=["close_above_vwap", "ema_above_sma"],
            factors_missing=[],
        ),
    )
    monkeypatch.setattr(
        scanner.gating,
        "evaluate",
        lambda mtf, strategy_result, daily_bias, config: GateResult(
            passed=True,
            gate1_strategy=True,
            gate1_reason="ok",
            gate2_mtf_alignment=True,
            gate2_reason="ok",
            final_direction="long",
        ),
    )

    bar_time = datetime(2026, 7, 27, 10, 0)
    scanner._on_bar_close(bar_time)

    assert len(scanner.session.signal_log) == 2


@pytest.mark.unit
def test_no_signal_when_gates_fail(monkeypatch):
    ta_graph = MagicMock()
    scanner = WatchlistScanner(_config(), ta_graph, dry_run=True, skip_premarket=True)
    scanner.session.daily_bias_cache["NVDA"] = _bias("NVDA")

    monkeypatch.setattr(
        scanner.mtf_validator,
        "evaluate",
        lambda symbol, as_of, session, config: _mtf(symbol),
    )
    monkeypatch.setattr(
        scanner.strategy,
        "check_setup",
        lambda symbol, mtf, daily_bias: StrategyResult(
            passed=False,
            direction="none",
            reason="blocked",
            factors_met=[],
            factors_missing=["rsi_in_range"],
        ),
    )
    monkeypatch.setattr(
        scanner.gating,
        "evaluate",
        lambda mtf, strategy_result, daily_bias, config: GateResult(
            passed=False,
            gate1_strategy=False,
            gate1_reason="blocked",
            gate2_mtf_alignment=False,
            gate2_reason="not evaluated",
            final_direction="none",
        ),
    )

    scanner._on_bar_close(datetime(2026, 7, 27, 10, 0))
    assert scanner.session.signal_log == []


@pytest.mark.unit
def test_scan_state_populated_on_gate_failure(monkeypatch):
    ta_graph = MagicMock()
    scanner = WatchlistScanner(_config(), ta_graph, dry_run=True, skip_premarket=True)
    scanner.session.daily_bias_cache["NVDA"] = _bias("NVDA")

    monkeypatch.setattr(
        scanner.mtf_validator,
        "evaluate",
        lambda symbol, as_of, session, config: _mtf(symbol),
    )
    monkeypatch.setattr(
        scanner.strategy,
        "check_setup",
        lambda symbol, mtf, daily_bias: StrategyResult(
            passed=False,
            direction="none",
            reason="blocked",
            factors_met=[],
            factors_missing=["rsi_in_range"],
        ),
    )
    monkeypatch.setattr(
        scanner.gating,
        "evaluate",
        lambda mtf, strategy_result, daily_bias, config: GateResult(
            passed=False,
            gate1_strategy=False,
            gate1_reason="blocked",
            gate2_mtf_alignment=False,
            gate2_reason="not evaluated",
            final_direction="none",
        ),
    )

    scanner._on_bar_close(datetime(2026, 7, 27, 10, 0))
    assert "NVDA" in scanner.session.latest_scan_by_symbol
    assert scanner.session.latest_scan_by_symbol["NVDA"].gate1_passed is False
    assert scanner.session.latest_scan_by_symbol["NVDA"].factors_missing == ["rsi_in_range"]


@pytest.mark.unit
def test_screener_refresh_updates_watchlist(monkeypatch):
    ta_graph = MagicMock()
    config = _config()
    config["intraday_screener_enabled"] = True
    config["intraday_screener_interval_minutes"] = 15
    config["intraday_screener_start_time"] = "09:30"
    config["watchlist"] = ["SPY"]
    scanner = WatchlistScanner(config, ta_graph, dry_run=True, skip_premarket=True)
    scanner.session.daily_bias_cache["SPY"] = _bias("SPY")
    scanner.session.daily_bias_cache["NVDA"] = _bias("NVDA")

    mock_universe = MagicMock()
    mock_universe.refresh_watchlist.return_value = (
        ["SPY", "NVDA"],
        [
            MagicMock(
                symbol="NVDA",
                rrs_by_tf={"5m": 1.2, "30m": 0.8, "60m": 0.5},
                aligned_count=3,
                relative_volume_5m=1.5,
                direction="long",
            ),
        ],
    )
    scanner.universe_screener = mock_universe

    bar_time = datetime(2026, 7, 27, 10, 0)
    scanner._maybe_refresh_watchlist(bar_time)

    assert "NVDA" in scanner.session.watchlist
    assert scanner.session.symbol_sources["NVDA"] == "screener"
    assert scanner.session.screener_last_candidate_count == 1
    assert "NVDA" in scanner.session.screener_snapshots
