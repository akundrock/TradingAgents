from __future__ import annotations

import logging
import threading
from datetime import datetime

import pytest

from cli.intraday_display import (
    DashboardLogHandler,
    IntradayDashboardBuffer,
    attach_dashboard_logging,
    create_intraday_layout,
    detach_dashboard_logging,
    update_intraday_display,
)
from tradingagents.intraday.session import (
    DailyBiasReport,
    IntradaySignal,
    SymbolScanState,
    TradingSession,
)


def _session() -> TradingSession:
    return TradingSession(session_date="2026-07-31", watchlist=["NVDA", "AAPL"])


@pytest.mark.unit
def test_buffer_records_scan_state():
    session = _session()
    buffer = IntradayDashboardBuffer(session=session, strategy_name="base_momentum")
    scan = SymbolScanState(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 31, 10, 0),
        daily_bias_direction="bullish",
        strategy_name="base_momentum",
        strategy_direction="long",
        factors_met=["close_above_vwap"],
        factors_missing=["rsi_in_range"],
        gate1_passed=True,
        gate2_passed=False,
        gate_passed=False,
        final_direction=None,
        setup_score=1,
    )
    buffer.record_scan_state(scan)
    assert session.latest_scan_by_symbol["NVDA"].setup_score == 1
    assert session.selected_detail_symbol == "NVDA"


@pytest.mark.unit
def test_buffer_thread_safe_updates():
    session = _session()
    buffer = IntradayDashboardBuffer(session=session, strategy_name="base_momentum")

    def worker(symbol: str) -> None:
        buffer.record_scan_state(
            SymbolScanState(
                symbol=symbol,
                bar_time=datetime(2026, 7, 31, 10, 0),
                daily_bias_direction="bullish",
                strategy_name="base_momentum",
                strategy_direction="none",
                factors_met=[],
                factors_missing=[],
                gate1_passed=False,
                gate2_passed=False,
                gate_passed=False,
                final_direction=None,
                setup_score=0,
            )
        )

    threads = [threading.Thread(target=worker, args=(sym,)) for sym in ("NVDA", "AAPL")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert set(session.latest_scan_by_symbol) == {"NVDA", "AAPL"}


@pytest.mark.unit
def test_dashboard_log_handler_filters_non_tradingagents():
    session = _session()
    buffer = IntradayDashboardBuffer(session=session, strategy_name="base_momentum")
    handler = DashboardLogHandler(buffer)
    record = logging.LogRecord(
        name="other.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="ignored",
        args=(),
        exc_info=None,
    )
    handler.emit(record)
    assert len(buffer.log_lines) == 0

    record.name = "tradingagents.intraday.scanner"
    handler.emit(record)
    assert len(buffer.log_lines) == 1


@pytest.mark.unit
def test_premarket_chunk_updates_agent_status():
    session = _session()
    buffer = IntradayDashboardBuffer(session=session, strategy_name="base_momentum")
    buffer.init_premarket_symbol("NVDA", ["market", "news"])
    buffer.update_premarket_chunk("NVDA", {"market_report": "bullish technicals"})
    snap = buffer.snapshot()
    state = snap["premarket_by_symbol"]["NVDA"]
    assert state.agent_status["Market Analyst"] == "completed"
    assert state.report_sections["market_report"] == "bullish technicals"


@pytest.mark.unit
def test_layout_render_smoke():
    session = _session()
    session.daily_bias_cache["NVDA"] = DailyBiasReport(
        symbol="NVDA",
        trade_date="2026-07-31",
        direction="bullish",
        key_levels={},
        summary="buy",
        computed_at=datetime(2026, 7, 31, 9, 0),
        analyst_reports={"market_report": "strong trend"},
    )
    buffer = IntradayDashboardBuffer(session=session, strategy_name="base_momentum")
    buffer.add_signal(
        IntradaySignal(
            symbol="NVDA",
            bar_time=datetime(2026, 7, 31, 10, 0),
            action="BUY",
            direction="long",
            entry_price=100.0,
            stop_loss=98.0,
            confidence="Strong",
            setup_score=3,
            gate_summary="G1=True G2=True",
            reasoning="test",
        )
    )
    layout = create_intraday_layout()
    update_intraday_display(layout, buffer, start_time=datetime(2026, 7, 31, 9, 0))


@pytest.mark.unit
def test_attach_detach_dashboard_logging():
    session = _session()
    buffer = IntradayDashboardBuffer(session=session, strategy_name="base_momentum")
    handler = attach_dashboard_logging(buffer)
    logger = logging.getLogger("tradingagents.test.dashboard")
    logger.info("hello dashboard")
    detach_dashboard_logging(handler)
    assert any("hello dashboard" in line[2] for line in buffer.log_lines)
