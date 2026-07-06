from __future__ import annotations

import copy
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import tradingagents.default_config as default_config
from tradingagents.dataflows.config import set_config
from tradingagents.graph.conditional_logic import ConditionalLogic
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.setup import GraphSetup


@pytest.mark.unit
def test_workflow_computes_magpie_signal_from_routed_intraday(monkeypatch):
    # Keep at least one analyst selected, but use deterministic stubs so the
    # graph exercises the real Magpie node in a controlled path.
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_market_analyst",
        lambda _llm: (lambda state: {"messages": [AIMessage(content="market")], "market_report": "MKT"}),
    )
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_msg_delete",
        lambda: (lambda state: {"messages": [HumanMessage(content="cleared")]}),
    )
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_bull_researcher",
        lambda _llm: (
            lambda state: {
                "investment_debate_state": {
                    **state["investment_debate_state"],
                    "current_response": "Bull",
                    "count": state["investment_debate_state"]["count"] + 1,
                }
            }
        ),
    )
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_bear_researcher",
        lambda _llm: (lambda state: {"investment_debate_state": state["investment_debate_state"]}),
    )
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_research_manager",
        lambda _llm: (lambda state: {"investment_plan": "**Recommendation**: Buy"}),
    )
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_trader",
        lambda _llm: (lambda state: {"trader_investment_plan": "FINAL TRANSACTION PROPOSAL: **BUY**"}),
    )
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_aggressive_debator",
        lambda _llm: (
            lambda state: {
                "risk_debate_state": {
                    **state["risk_debate_state"],
                    "latest_speaker": "Aggressive",
                    "count": state["risk_debate_state"]["count"] + 1,
                }
            }
        ),
    )
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_neutral_debator",
        lambda _llm: (lambda state: {"risk_debate_state": state["risk_debate_state"]}),
    )
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_conservative_debator",
        lambda _llm: (lambda state: {"risk_debate_state": state["risk_debate_state"]}),
    )
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_portfolio_manager",
        lambda _llm: (
            lambda state: {
                "final_trade_decision": "Rating: Buy",
                "risk_debate_state": {
                    **state["risk_debate_state"],
                    "judge_decision": "Rating: Buy",
                },
            }
        ),
    )

    # Deterministic route stubs for the real Magpie node.
    def fake_route(method, *args):
        if method == "get_intraday_stock_data":
            return (
                "# Intraday stock data\n"
                "Date,Open,High,Low,Close,Volume\n"
                "2026-07-01 09:30:00,100,101,99,100.5,1000\n"
                "2026-07-01 09:35:00,100.5,101.5,100,101,1200\n"
                "2026-07-01 09:40:00,101,102,100.8,101.7,1300\n"
                "2026-07-01 09:45:00,101.7,102.1,101.5,101.8,1100\n"
                "2026-07-01 09:50:00,101.8,102.2,101.6,102,1250\n"
                "2026-07-01 09:55:00,102,102.4,101.9,102.2,1270\n"
                "2026-07-01 10:00:00,102.2,102.5,102,102.3,1320\n"
                "2026-07-01 10:05:00,102.3,102.6,102.1,102.4,1400\n"
                "2026-07-01 10:10:00,102.4,102.8,102.2,102.6,1380\n"
                "2026-07-01 10:15:00,102.6,103,102.4,102.8,1450\n"
                "2026-07-01 10:20:00,102.8,103.2,102.7,103,1480\n"
                "2026-07-01 10:25:00,103,103.4,102.9,103.1,1500\n"
            )
        if method == "get_implied_move_data":
            return (
                "# Implied move data\n"
                "Date,Symbol,UnderlyingPrice,ImpliedVolatility,DTE,Mean,Upper,Lower,Expiry\n"
                "2026-07-01 10:25:00,AAPL,103.1,0.2,3,103.0,106.0,100.0,2026-07-05\n"
            )
        if method == "get_market_internals":
            return (
                "# Market internals\n"
                "Date,$ADD,$TICK,$VOLD\n"
                "2026-07-01 10:25:00,350,1100,120000\n"
            )
        raise AssertionError(f"unexpected route method: {method}")

    monkeypatch.setattr("tradingagents.agents.trader.magpie.route_to_vendor", fake_route)

    cfg = copy.deepcopy(default_config.DEFAULT_CONFIG)
    cfg["magpie_enabled"] = True
    cfg["magpie_intraday_interval"] = "5m"
    cfg["magpie_intraday_lookback_minutes"] = 390
    set_config(cfg)

    class DeterministicConditionalLogic(ConditionalLogic):
        def should_continue_market(self, state):
            return "Msg Clear Market"

        def should_continue_debate(self, state):
            return "Research Manager"

        def should_continue_risk_analysis(self, state):
            return "Portfolio Manager"

    setup = GraphSetup(
        quick_thinking_llm=MagicMock(),
        deep_thinking_llm=MagicMock(),
        tool_nodes={"market": MagicMock(), "social": MagicMock(), "news": MagicMock(), "fundamentals": MagicMock()},
        conditional_logic=DeterministicConditionalLogic(max_debate_rounds=1, max_risk_discuss_rounds=1),
    )
    workflow = setup.setup_graph(selected_analysts=("market",))
    graph = workflow.compile()

    state = Propagator().create_initial_state("AAPL", "2026-07-01")
    out = graph.invoke(state)

    assert out["magpie_signal"]["status"] == "computed"
    assert out["magpie_signal"]["direction"] in {"Buy", "Hold", "Sell"}
    factors = {f["name"]: f for f in out["magpie_signal"].get("factors", [])}
    assert factors["$ADD"]["signal"] == "bullish"
    assert factors["$TICK"]["signal"] == "bullish"
    assert factors["$VOLD"]["signal"] == "bullish"