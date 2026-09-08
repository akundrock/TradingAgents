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


def _neutral_bias(symbol: str) -> DailyBiasReport:
    return DailyBiasReport(
        symbol=symbol,
        trade_date="2026-07-27",
        direction="neutral",
        key_levels={},
        summary="neutral bias",
        computed_at=datetime(2026, 7, 27, 9, 0),
    )


def _bearish_bias(symbol: str) -> DailyBiasReport:
    return DailyBiasReport(
        symbol=symbol,
        trade_date="2026-07-27",
        direction="bearish",
        key_levels={},
        summary="sell bias",
        computed_at=datetime(2026, 7, 27, 10, 5),
    )


def _mtf_short(symbol: str) -> MTFValidationResult:
    mtf = _mtf(symbol)
    mtf.trend_30min = "down"
    mtf.daily_bias_direction = "neutral"
    mtf.trends_aligned = False
    return mtf


def _lazy_bias_config() -> dict:
    config = _config()
    config["intraday_lazy_bias_on_gate1"] = True
    config["intraday_lazy_bias_analysts"] = ["market"]
    config["intraday_gate2_mode"] = "daily_bias"
    return config


@pytest.mark.unit
def test_get_lazy_bias_graph_defaults_to_fast_research(monkeypatch):
    ta_graph = MagicMock()
    scanner = WatchlistScanner(
        _lazy_bias_config(), ta_graph, dry_run=True, skip_premarket=True, restore_premarket=False
    )
    captured = {}
    monkeypatch.setattr(
        "tradingagents.graph.trading_graph.TradingAgentsGraph",
        lambda **kwargs: captured.update(kwargs) or MagicMock(),
    )

    scanner._get_lazy_bias_graph()

    assert captured["fast_research"] is True


@pytest.mark.unit
def test_get_lazy_bias_graph_respects_fast_mode_disabled(monkeypatch):
    ta_graph = MagicMock()
    config = _lazy_bias_config()
    config["intraday_lazy_bias_fast_mode"] = False
    scanner = WatchlistScanner(config, ta_graph, dry_run=True, skip_premarket=True, restore_premarket=False)
    captured = {}
    monkeypatch.setattr(
        "tradingagents.graph.trading_graph.TradingAgentsGraph",
        lambda **kwargs: captured.update(kwargs) or MagicMock(),
    )

    scanner._get_lazy_bias_graph()

    assert captured["fast_research"] is False


@pytest.mark.unit
def test_lazy_bias_not_called_on_g1_fail(monkeypatch):
    ta_graph = MagicMock()
    scanner = WatchlistScanner(
        _lazy_bias_config(), ta_graph, dry_run=True, skip_premarket=True, restore_premarket=False
    )
    scanner.session.daily_bias_cache["NVDA"] = _neutral_bias("NVDA")
    lazy_graph = MagicMock()
    scanner._lazy_bias_graph = lazy_graph

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

    scanner._evaluate_symbol("NVDA", datetime(2026, 7, 27, 10, 0))
    lazy_graph.propagate_daily_bias.assert_not_called()


@pytest.mark.unit
def test_lazy_bias_called_on_g1_pass_with_neutral_bias(monkeypatch):
    ta_graph = MagicMock()
    scanner = WatchlistScanner(
        _lazy_bias_config(), ta_graph, dry_run=True, skip_premarket=True, restore_premarket=False
    )
    # dry_run only forbids the LLM calls; force-enable to exercise the lazy-bias mechanism itself.
    monkeypatch.setattr(scanner, "_lazy_bias_enabled", lambda: True)
    scanner.session.session_date = "2026-07-27"
    scanner.session.daily_bias_cache["NVDA"] = _neutral_bias("NVDA")
    lazy_graph = MagicMock()
    lazy_graph.propagate_daily_bias.side_effect = (
        lambda symbol, trade_date: _bearish_bias(symbol)
    )
    scanner._lazy_bias_graph = lazy_graph

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
            factors_met=["close_above_vwap"],
            factors_missing=[],
        ),
    )

    scanner._evaluate_symbol("NVDA", datetime(2026, 7, 27, 10, 0))
    lazy_graph.propagate_daily_bias.assert_called_once_with("NVDA", "2026-07-27")


@pytest.mark.unit
def test_lazy_bias_not_called_with_supertrend_gate2_mode(monkeypatch):
    ta_graph = MagicMock()
    config = _lazy_bias_config()
    config["intraday_gate2_mode"] = "supertrend"
    scanner = WatchlistScanner(
        config, ta_graph, dry_run=True, skip_premarket=True, restore_premarket=False
    )
    scanner.session.daily_bias_cache["NVDA"] = _neutral_bias("NVDA")
    lazy_graph = MagicMock()
    scanner._lazy_bias_graph = lazy_graph

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
            factors_met=["close_above_vwap"],
            factors_missing=[],
        ),
    )

    scanner._evaluate_symbol("NVDA", datetime(2026, 7, 27, 10, 0))
    lazy_graph.propagate_daily_bias.assert_not_called()


@pytest.mark.unit
def test_lazy_bias_skipped_when_bias_already_set(monkeypatch):
    ta_graph = MagicMock()
    scanner = WatchlistScanner(
        _lazy_bias_config(), ta_graph, dry_run=True, skip_premarket=True, restore_premarket=False
    )
    scanner.session.daily_bias_cache["NVDA"] = _bias("NVDA")
    lazy_graph = MagicMock()
    scanner._lazy_bias_graph = lazy_graph

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
            factors_met=["close_above_vwap"],
            factors_missing=[],
        ),
    )

    scanner._evaluate_symbol("NVDA", datetime(2026, 7, 27, 10, 0))
    lazy_graph.propagate_daily_bias.assert_not_called()


@pytest.mark.unit
def test_lazy_bias_enables_g2_pass(monkeypatch):
    ta_graph = MagicMock()
    config = _lazy_bias_config()
    config["watchlist"] = ["NVDA"]
    scanner = WatchlistScanner(
        config, ta_graph, dry_run=True, skip_premarket=True, restore_premarket=False
    )
    # dry_run only forbids the LLM calls; force-enable to exercise the lazy-bias mechanism itself.
    monkeypatch.setattr(scanner, "_lazy_bias_enabled", lambda: True)
    scanner.session.session_date = "2026-07-27"
    scanner.session.daily_bias_cache["NVDA"] = _neutral_bias("NVDA")
    lazy_graph = MagicMock()
    lazy_graph.propagate_daily_bias.side_effect = (
        lambda symbol, trade_date: _bearish_bias(symbol)
    )
    scanner._lazy_bias_graph = lazy_graph

    monkeypatch.setattr(
        scanner.mtf_validator,
        "evaluate",
        lambda symbol, as_of, session, config: _mtf_short(symbol),
    )
    monkeypatch.setattr(
        scanner.strategy,
        "check_setup",
        lambda symbol, mtf, daily_bias: StrategyResult(
            passed=True,
            direction="short",
            reason="Short momentum setup confirmed.",
            factors_met=["close_below_vwap"],
            factors_missing=[],
        ),
    )

    bar_time = datetime(2026, 7, 27, 10, 0)
    scanner._on_bar_close(bar_time)

    lazy_graph.propagate_daily_bias.assert_called_once_with("NVDA", scanner.session.session_date)
    assert len(scanner.session.signal_log) == 1
    assert scanner.session.signal_log[0].direction == "short"
    assert scanner.session.latest_scan_by_symbol["NVDA"].gate2_passed is True


@pytest.mark.unit
def test_lazy_bias_disabled_when_dry_run(monkeypatch):
    ta_graph = MagicMock()
    scanner = WatchlistScanner(
        _lazy_bias_config(), ta_graph, dry_run=True, skip_premarket=True, restore_premarket=False
    )
    assert scanner._lazy_bias_enabled() is False


@pytest.mark.unit
def test_lazy_bias_not_called_on_g1_pass_when_dry_run(monkeypatch):
    ta_graph = MagicMock()
    scanner = WatchlistScanner(
        _lazy_bias_config(), ta_graph, dry_run=True, skip_premarket=True, restore_premarket=False
    )
    scanner.session.session_date = "2026-07-27"
    scanner.session.daily_bias_cache["NVDA"] = _neutral_bias("NVDA")
    lazy_graph = MagicMock()
    scanner._lazy_bias_graph = lazy_graph

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
            factors_met=["close_above_vwap"],
            factors_missing=[],
        ),
    )

    scanner._evaluate_symbol("NVDA", datetime(2026, 7, 27, 10, 0))
    lazy_graph.propagate_daily_bias.assert_not_called()


@pytest.mark.unit
def test_screener_new_symbol_premarket_skipped_when_dry_run(tmp_path):
    ta_graph = MagicMock()
    config = _config()
    config["intraday_output_dir"] = str(tmp_path)
    config["intraday_screener_run_premarket_for_new"] = True
    scanner = WatchlistScanner(config, ta_graph, dry_run=True, skip_premarket=True)
    scanner.session.session_date = "2026-07-27"

    scanner._add_screener_symbol("TSLA")

    ta_graph.propagate_daily_bias.assert_not_called()
    assert scanner.session.daily_bias_cache["TSLA"].direction == "neutral"


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
                rank_score=1.2,
                rank_rrs_timeframe="5m",
                passed_filter="rrs",
                filter_metadata={},
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


@pytest.mark.unit
def test_screener_refresh_logs_two_lists_for_both_direction(caplog):
    """With direction=both, the screener log should show separate long and short
    ranked lists instead of one mixed top-5, so neither side is invisible."""
    import logging

    ta_graph = MagicMock()
    config = _config()
    config["intraday_screener_enabled"] = True
    config["intraday_screener_interval_minutes"] = 15
    config["intraday_screener_start_time"] = "09:30"
    config["watchlist"] = ["SPY"]
    scanner = WatchlistScanner(config, ta_graph, dry_run=True, skip_premarket=True)
    scanner.session.daily_bias_cache["SPY"] = _bias("SPY")
    scanner.session.daily_bias_cache["NVDA"] = _bias("NVDA")

    def _item(symbol, direction, rank_score, aligned):
        return MagicMock(
            symbol=symbol,
            rrs_by_tf={"5m": rank_score, "30m": 0.0, "60m": 0.0},
            aligned_count=aligned,
            relative_volume_5m=1.5,
            direction=direction,
            rank_score=rank_score,
            rank_rrs_timeframe="5m",
            passed_filter="rrs",
            filter_metadata={},
        )

    mock_universe = MagicMock()
    mock_universe.refresh_watchlist.return_value = (
        ["SPY", "L1", "S1"],
        [
            _item("L1", "long", 1.5, 3),
            _item("L2", "long", 0.8, 2),
            _item("S1", "short", -1.2, 2),
            _item("S2", "short", -0.5, 1),
        ],
    )
    scanner.universe_screener = mock_universe

    bar_time = datetime(2026, 7, 27, 10, 0)
    with caplog.at_level(logging.INFO, logger="tradingagents.intraday.scanner"):
        scanner._maybe_refresh_watchlist(bar_time)

    messages = [r.message for r in caplog.records]
    longs_line = next((m for m in messages if "longs" in m), None)
    shorts_line = next((m for m in messages if "shorts" in m), None)
    assert longs_line is not None, f"no longs list logged: {messages}"
    assert "L1" in longs_line and "L2" in longs_line
    assert shorts_line is not None
    assert "S1" in shorts_line


@pytest.mark.unit
def test_screener_snapshot_direction_flows_to_preferred_direction():
    """The screener direction hint must reach check_setup as
    preferred_direction for symbols present in screener_snapshots."""
    ta_graph = MagicMock()
    config = _config()
    config["intraday_strategy"] = "pro_trader_dashboard"
    config["intraday_screener_enabled"] = False  # no refresh; snapshot set directly
    scanner = WatchlistScanner(config, ta_graph, dry_run=True, skip_premarket=True)
    scanner.session.watchlist = ["NVDA"]
    scanner.session.daily_bias_cache["NVDA"] = _bias("NVDA")
    scanner.session.screener_snapshots["NVDA"] = {"direction": "short"}

    captured: dict = {}

    def _capture(symbol, mtf, daily_bias, preferred_direction=None, **kw):
        captured["preferred"] = preferred_direction
        return StrategyResult(
            passed=False, direction="none", reason="x",
            factors_met=[], factors_missing=[],
        )

    mock_strategy = MagicMock()
    mock_strategy.name = "pro_trader_dashboard"
    mock_strategy.check_setup.side_effect = _capture
    scanner.strategy = mock_strategy
    mtf = MagicMock()
    scanner.mtf_validator.evaluate = lambda *a, **k: mtf

    scanner._evaluate_symbol("NVDA", datetime(2026, 7, 27, 10, 0))
    assert captured["preferred"] == "short"
