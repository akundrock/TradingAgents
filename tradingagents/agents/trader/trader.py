"""Trader: turns the Research Manager's investment plan into a concrete transaction proposal."""

from __future__ import annotations

import functools

from langchain_core.messages import AIMessage

from tradingagents.agents.schemas import TraderProposal, render_trader_proposal
from tradingagents.agents.trader.magpie import render_magpie_signal_summary
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)
from tradingagents.intraday.strategy import render_strategy_summary


def create_trader(llm):
    structured_llm = bind_structured(llm, TraderProposal, "Trader")

    def trader_node(state, name):
        company_name = state["company_of_interest"]
        instrument_context = get_instrument_context_from_state(state)
        investment_plan = state["investment_plan"]
        intraday_context = state.get("intraday_context") or {}
        strategy_block = intraday_context.get("strategy")

        if strategy_block:
            signal_label = "Intraday Strategy Signal"
            signal_summary = render_strategy_summary(strategy_block)
            system_extra = (
                "Anchor your reasoning in the analysts' reports, the research plan, and the "
                "deterministic intraday strategy signal when it is available."
            )
        else:
            signal_label = "Magpie Strategy Signal"
            signal_summary = render_magpie_signal_summary(state.get("magpie_signal"))
            system_extra = (
                "Anchor your reasoning in the analysts' reports, the research plan, and the "
                "deterministic Magpie strategy signal when it is available. If the Magpie signal "
                "is unavailable or disabled, say so briefly and rely on the rest of the evidence."
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a trading agent analyzing market data to make investment decisions. "
                    "Based on your analysis, provide a specific recommendation to buy, sell, or hold. "
                    + system_extra
                    + get_language_instruction()
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Based on a comprehensive analysis by a team of analysts, here is an investment "
                    f"plan tailored for {company_name}. {instrument_context} This plan incorporates "
                    f"insights from current technical market trends, macroeconomic indicators, and "
                    f"social media sentiment. Use this plan as a foundation for evaluating your next "
                    f"trading decision.\n\nProposed Investment Plan: {investment_plan}\n\n"
                    f"{signal_label}:\n{signal_summary}\n\n"
                    f"Leverage these insights to make an informed and strategic decision."
                ),
            },
        ]

        trader_plan = invoke_structured_or_freetext(
            structured_llm,
            llm,
            messages,
            render_trader_proposal,
            "Trader",
        )

        return {
            "messages": [AIMessage(content=trader_plan)],
            "trader_investment_plan": trader_plan,
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")
