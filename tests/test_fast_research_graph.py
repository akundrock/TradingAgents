"""Tests for GraphSetup's fast_research wiring (skips Bull/Bear/Research Manager)."""

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from tradingagents.graph.conditional_logic import ConditionalLogic
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.setup import GraphSetup


@pytest.mark.unit
def test_fast_research_skips_debate_and_uses_fast_bias_rating(monkeypatch):
    bull_mock = MagicMock(side_effect=AssertionError("Bull Researcher should not run in fast_research mode"))
    bear_mock = MagicMock(side_effect=AssertionError("Bear Researcher should not run in fast_research mode"))
    research_manager_mock = MagicMock(
        side_effect=AssertionError("Research Manager should not run in fast_research mode")
    )

    monkeypatch.setattr(
        "tradingagents.graph.setup.create_market_analyst",
        lambda _llm: (lambda state: {"messages": [AIMessage(content="market")], "market_report": "MKT"}),
    )
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_msg_delete",
        lambda: (lambda state: {"messages": [HumanMessage(content="cleared")]}),
    )
    monkeypatch.setattr("tradingagents.graph.setup.create_bull_researcher", lambda _llm: bull_mock)
    monkeypatch.setattr("tradingagents.graph.setup.create_bear_researcher", lambda _llm: bear_mock)
    monkeypatch.setattr("tradingagents.graph.setup.create_research_manager", lambda _llm: research_manager_mock)
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_fast_bias_rating",
        lambda _llm: (lambda state: {"investment_plan": "**Recommendation**: Buy"}),
    )

    class DeterministicConditionalLogic(ConditionalLogic):
        def should_continue_market(self, state):
            return "Msg Clear Market"

    setup = GraphSetup(
        quick_thinking_llm=MagicMock(),
        deep_thinking_llm=MagicMock(),
        tool_nodes={
            "market": MagicMock(),
            "social": MagicMock(),
            "news": MagicMock(),
            "fundamentals": MagicMock(),
        },
        conditional_logic=DeterministicConditionalLogic(max_debate_rounds=1, max_risk_discuss_rounds=1),
        fast_research=True,
    )
    workflow = setup.setup_graph(selected_analysts=("market",))
    graph = workflow.compile()

    state = Propagator().create_initial_state("AAPL", "2026-07-01")
    state["stop_after_research"] = True
    out = graph.invoke(state)

    assert out["investment_plan"] == "**Recommendation**: Buy"
    bull_mock.assert_not_called()
    bear_mock.assert_not_called()
    research_manager_mock.assert_not_called()


@pytest.mark.unit
def test_default_graph_still_uses_debate_when_fast_research_false(monkeypatch):
    """fast_research defaults to False, so existing graph wiring is unaffected."""
    fast_bias_mock = MagicMock(side_effect=AssertionError("Fast Bias Rating should not run by default"))

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
    monkeypatch.setattr("tradingagents.graph.setup.create_fast_bias_rating", lambda _llm: fast_bias_mock)

    class DeterministicConditionalLogic(ConditionalLogic):
        def should_continue_market(self, state):
            return "Msg Clear Market"

        def should_continue_debate(self, state):
            return "Research Manager"

    setup = GraphSetup(
        quick_thinking_llm=MagicMock(),
        deep_thinking_llm=MagicMock(),
        tool_nodes={
            "market": MagicMock(),
            "social": MagicMock(),
            "news": MagicMock(),
            "fundamentals": MagicMock(),
        },
        conditional_logic=DeterministicConditionalLogic(max_debate_rounds=1, max_risk_discuss_rounds=1),
    )
    workflow = setup.setup_graph(selected_analysts=("market",))
    graph = workflow.compile()

    state = Propagator().create_initial_state("AAPL", "2026-07-01")
    state["stop_after_research"] = True
    out = graph.invoke(state)

    assert out["investment_plan"] == "**Recommendation**: Buy"
    fast_bias_mock.assert_not_called()
