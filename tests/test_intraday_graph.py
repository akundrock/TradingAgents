from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from tradingagents.agents.schemas import SwingTradeProposal, TraderAction, render_swing_trade_proposal
from tradingagents.graph.intraday_graph import IntradayTradingGraph, _state_to_intraday_signal
from tradingagents.intraday.gating import GateResult
from tradingagents.intraday.mtf_validator import MTFValidationResult
from tradingagents.intraday.session import DailyBiasReport
from tradingagents.intraday.strategy import StrategyResult


@pytest.mark.unit
def test_propagate_intraday_returns_signal(monkeypatch):
    from tradingagents.graph.propagation import Propagator

    graph = IntradayTradingGraph.__new__(IntradayTradingGraph)
    graph.callbacks = []
    graph.propagator = Propagator()
    graph.graph = MagicMock()
    graph.graph.invoke.return_value = {
        "trader_investment_plan": "**Action**: Buy\n**Entry Price**: 100.5\n**Stop Loss**: 98.0",
        "final_trade_decision": "**Rating**: Buy",
    }

    mtf = MTFValidationResult(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        snapshot_5min={},
        snapshot_30min={},
        snapshot_daily={},
        trend_5min="up",
        trend_30min="up",
        daily_bias_direction="bullish",
        trends_aligned=True,
        vwap_5min=99.0,
        atr_5min=1.5,
    )
    gate = GateResult(
        passed=True,
        gate1_strategy=True,
        gate1_reason="ok",
        gate2_mtf_alignment=True,
        gate2_reason="ok",
        final_direction="long",
    )
    strategy_result = StrategyResult(
        passed=True,
        direction="long",
        reason="Long momentum setup confirmed.",
        factors_met=["close_above_vwap", "ema_above_sma"],
        factors_missing=[],
    )
    bias = DailyBiasReport(
        symbol="NVDA",
        trade_date="2026-07-27",
        direction="bullish",
        key_levels={},
        summary="buy",
        computed_at=datetime(2026, 7, 27, 9, 0),
    )

    signal = graph.propagate_intraday(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        daily_bias=bias,
        mtf=mtf,
        strategy_result=strategy_result,
        gate_result=gate,
        strategy_name="base_momentum",
    )

    assert signal.symbol == "NVDA"
    assert signal.action == "BUY"
    assert signal.entry_price == 100.5
    assert signal.stop_loss == 98.0
    assert signal.setup_score == 2


@pytest.mark.unit
def test_intraday_state_contains_context(monkeypatch):
    captured = {}

    class _Graph:
        def invoke(self, state, **kwargs):
            captured.update(state)
            return {}

    from tradingagents.graph.propagation import Propagator

    graph = IntradayTradingGraph.__new__(IntradayTradingGraph)
    graph.callbacks = []
    graph.propagator = Propagator()
    graph.graph = _Graph()

    mtf = MTFValidationResult(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        snapshot_5min={"Close": 1.0},
        snapshot_30min={"Close": 1.0},
        snapshot_daily={},
        trend_5min="up",
        trend_30min="up",
        daily_bias_direction="bullish",
        trends_aligned=True,
        vwap_5min=1.0,
        atr_5min=1.0,
    )
    gate = GateResult(
        passed=True,
        gate1_strategy=True,
        gate1_reason="ok",
        gate2_mtf_alignment=True,
        gate2_reason="ok",
        final_direction="long",
    )
    strategy_result = StrategyResult(
        passed=True,
        direction="long",
        reason="ok",
        factors_met=["close_above_vwap"],
        factors_missing=[],
    )
    bias = DailyBiasReport(
        symbol="NVDA",
        trade_date="2026-07-27",
        direction="bullish",
        key_levels={},
        summary="buy",
        computed_at=datetime(2026, 7, 27, 9, 0),
    )

    graph.propagate_intraday(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        daily_bias=bias,
        mtf=mtf,
        strategy_result=strategy_result,
        gate_result=gate,
        strategy_name="base_momentum",
    )

    assert captured.get("intraday_context") is not None
    assert captured["intraday_context"]["daily_bias"] == "bullish"
    assert captured["intraday_context"]["strategy"]["name"] == "base_momentum"
    assert captured["intraday_context"]["strategy"]["direction"] == "long"


@pytest.mark.unit
def test_propagate_intraday_attaches_swing_profile_for_pro_trader():
    captured = {}

    class _Graph:
        def invoke(self, state, **kwargs):
            captured.update(state)
            return {"trader_investment_plan": "", "final_trade_decision": ""}

    from tradingagents.graph.propagation import Propagator

    graph = IntradayTradingGraph.__new__(IntradayTradingGraph)
    graph.config = {}
    graph.callbacks = []
    graph.propagator = Propagator()
    graph.graph = _Graph()

    mtf = MTFValidationResult(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        snapshot_5min={"Close": 1.0},
        snapshot_30min={"Close": 1.0},
        snapshot_daily={},
        trend_5min="up",
        trend_30min="up",
        daily_bias_direction="bullish",
        trends_aligned=True,
        vwap_5min=1.0,
        atr_5min=1.0,
    )
    gate = GateResult(
        passed=True,
        gate1_strategy=True,
        gate1_reason="ok",
        gate2_mtf_alignment=True,
        gate2_reason="ok",
        final_direction="long",
    )
    strategy_result = StrategyResult(
        passed=True,
        direction="long",
        reason="ok",
        factors_met=["orb_breakout"],
        factors_missing=[],
    )
    bias = DailyBiasReport(
        symbol="NVDA",
        trade_date="2026-07-27",
        direction="bullish",
        key_levels={},
        summary="buy",
        computed_at=datetime(2026, 7, 27, 9, 0),
    )

    graph.propagate_intraday(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        daily_bias=bias,
        mtf=mtf,
        strategy_result=strategy_result,
        gate_result=gate,
        strategy_name="pro_trader_dashboard",
    )

    ctx = captured["intraday_context"]
    assert "trade_profile" in ctx
    assert "Trade Profile" in ctx["trade_profile"]["block"]
    assert "0.70" in ctx["trade_profile"]["block"]


@pytest.mark.unit
def test_propagate_intraday_no_profile_when_kill_switch_off():
    captured = {}

    class _Graph:
        def invoke(self, state, **kwargs):
            captured.update(state)
            return {"trader_investment_plan": "", "final_trade_decision": ""}

    from tradingagents.graph.propagation import Propagator

    graph = IntradayTradingGraph.__new__(IntradayTradingGraph)
    graph.config = {"pro_trader_swing_profile_enabled": False}
    graph.callbacks = []
    graph.propagator = Propagator()
    graph.graph = _Graph()

    mtf = MTFValidationResult(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        snapshot_5min={"Close": 1.0},
        snapshot_30min={"Close": 1.0},
        snapshot_daily={},
        trend_5min="up",
        trend_30min="up",
        daily_bias_direction="bullish",
        trends_aligned=True,
        vwap_5min=1.0,
        atr_5min=1.0,
    )
    gate = GateResult(
        passed=True,
        gate1_strategy=True,
        gate1_reason="ok",
        gate2_mtf_alignment=True,
        gate2_reason="ok",
        final_direction="long",
    )
    strategy_result = StrategyResult(
        passed=True,
        direction="long",
        reason="ok",
        factors_met=["orb_breakout"],
        factors_missing=[],
    )
    bias = DailyBiasReport(
        symbol="NVDA",
        trade_date="2026-07-27",
        direction="bullish",
        key_levels={},
        summary="buy",
        computed_at=datetime(2026, 7, 27, 9, 0),
    )

    graph.propagate_intraday(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        daily_bias=bias,
        mtf=mtf,
        strategy_result=strategy_result,
        gate_result=gate,
        strategy_name="pro_trader_dashboard",
    )

    ctx = captured["intraday_context"]
    assert "trade_profile" not in ctx

@pytest.mark.unit
def test_propagate_intraday_no_profile_for_other_strategies():
    captured = {}

    class _Graph:
        def invoke(self, state, **kwargs):
            captured.update(state)
            return {"trader_investment_plan": "", "final_trade_decision": ""}

    from tradingagents.graph.propagation import Propagator

    graph = IntradayTradingGraph.__new__(IntradayTradingGraph)
    graph.config = {}
    graph.callbacks = []
    graph.propagator = Propagator()
    graph.graph = _Graph()

    mtf = MTFValidationResult(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        snapshot_5min={"Close": 1.0},
        snapshot_30min={"Close": 1.0},
        snapshot_daily={},
        trend_5min="up",
        trend_30min="up",
        daily_bias_direction="bullish",
        trends_aligned=True,
        vwap_5min=1.0,
        atr_5min=1.0,
    )
    gate = GateResult(
        passed=True,
        gate1_strategy=True,
        gate1_reason="ok",
        gate2_mtf_alignment=True,
        gate2_reason="ok",
        final_direction="long",
    )
    strategy_result = StrategyResult(
        passed=True,
        direction="long",
        reason="ok",
        factors_met=["orb_breakout"],
        factors_missing=[],
    )
    bias = DailyBiasReport(
        symbol="NVDA",
        trade_date="2026-07-27",
        direction="bullish",
        key_levels={},
        summary="buy",
        computed_at=datetime(2026, 7, 27, 9, 0),
    )

    graph.propagate_intraday(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        daily_bias=bias,
        mtf=mtf,
        strategy_result=strategy_result,
        gate_result=gate,
        strategy_name="orb_breakout",
    )

    assert captured["intraday_context"]["strategy"]["name"] == "orb_breakout"
    assert "trade_profile" not in captured["intraday_context"]


@pytest.mark.unit
def test_analyst_nodes_not_called(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.graph.intraday_graph.create_llm_client",
        lambda **kwargs: MagicMock(get_llm=lambda: MagicMock()),
    )
    graph = IntradayTradingGraph(
        config={
            "llm_provider": "openai",
            "quick_think_llm": "gpt-4o-mini",
            "deep_think_llm": "gpt-4o",
            "max_debate_rounds": 1,
            "max_risk_discuss_rounds": 1,
        }
    )
    node_names = set(graph.graph.get_graph().nodes.keys())
    assert "Market Analyst" not in node_names
    assert "Research Manager" not in node_names
    assert "Trader" in node_names
    assert "Portfolio Manager" in node_names


@pytest.mark.unit
def test_state_to_signal_extracts_swing_fields_from_markdown():
    final_state = {
        "trader_investment_plan": (
            "**Action**: Buy\n\n**Reasoning**: r\n\n**Entry Price**: 100.5\n\n"
            "**Stop Loss**: 98.0\n\n**Option Structure**: Long call, ~0.70 delta, 21 DTE\n\n"
            "**Hold Horizon**: 1\n\nFINAL TRANSACTION PROPOSAL: **BUY**"
        ),
        "final_trade_decision": "**Rating**: Buy",
    }
    signal = _state_to_intraday_signal(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        direction="long",
        strategy_result=StrategyResult(
            passed=True, direction="long", reason="ok", factors_met=["a"], factors_missing=[]
        ),
        gate_result=GateResult(
            passed=True,
            gate1_strategy=True,
            gate1_reason="ok",
            gate2_mtf_alignment=True,
            gate2_reason="ok",
            final_direction="long",
        ),
        final_state=final_state,
    )
    assert signal.option_structure == "Long call, ~0.70 delta, 21 DTE"
    assert signal.hold_horizon_days == "1"
    assert signal.entry_price == 100.5
    assert signal.stop_loss == 98.0


@pytest.mark.unit
def test_state_to_signal_round_trips_rendered_swing_proposal():
    """render_swing_trade_proposal output must be extractable by _state_to_intraday_signal."""
    proposal = SwingTradeProposal(
        action=TraderAction.BUY,
        reasoning="Momentum setup confirmed above VWAP.",
        entry_price=100.5,
        stop_loss=98.0,
        option_structure="Long call, ~0.70 delta, 21 DTE",
        hold_horizon_days="1",
        option_direction="long call",
    )
    signal = _state_to_intraday_signal(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        direction="long",
        strategy_result=StrategyResult(passed=True, direction="long", reason="ok", factors_met=["a"], factors_missing=[]),
        gate_result=GateResult(
            passed=True,
            gate1_strategy=True,
            gate1_reason="ok",
            gate2_mtf_alignment=True,
            gate2_reason="ok",
            final_direction="long",
        ),
        final_state={
            "trader_investment_plan": render_swing_trade_proposal(proposal),
            "final_trade_decision": "**Rating**: Buy",
        },
    )
    assert signal.option_structure == "Long call, ~0.70 delta, 21 DTE"
    assert signal.hold_horizon_days == "1"
    assert signal.entry_price == 100.5
    assert signal.stop_loss == 98.0


@pytest.mark.unit
def test_state_to_signal_swing_fields_none_without_markers():
    signal = _state_to_intraday_signal(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        direction="long",
        strategy_result=StrategyResult(passed=True, direction="long", reason="ok", factors_met=["a"], factors_missing=[]),
        gate_result=GateResult(
            passed=True,
            gate1_strategy=True,
            gate1_reason="ok",
            gate2_mtf_alignment=True,
            gate2_reason="ok",
            final_direction="long",
        ),
        final_state={
            "trader_investment_plan": "**Action**: Buy\n**Entry Price**: 100.5\n**Stop Loss**: 98.0",
            "final_trade_decision": "**Rating**: Buy",
        },
    )
    assert signal.option_structure is None
    assert signal.hold_horizon_days is None
