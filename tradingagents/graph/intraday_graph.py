from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from langgraph.graph import END, START, StateGraph

from tradingagents.agents import (
    create_aggressive_debator,
    create_conservative_debator,
    create_neutral_debator,
    create_portfolio_manager,
    create_trader,
)
from tradingagents.agents.trader.magpie import build_default_magpie_signal
from tradingagents.agents.utils.agent_states import AgentState
from tradingagents.dataflows.config import set_config
from tradingagents.graph.conditional_logic import ConditionalLogic
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.setup import RISK_ANALYSIS_PATH_MAP
from tradingagents.intraday.gating import GateResult
from tradingagents.intraday.mtf_validator import MTFValidationResult
from tradingagents.intraday.session import DailyBiasReport, IntradaySignal
from tradingagents.intraday.strategy import StrategyResult
from tradingagents.intraday.trade_profile import build_trade_profile, render_trade_profile
from tradingagents.llm_clients import create_llm_client
from tradingagents.default_config import DEFAULT_CONFIG


class IntradayTradingGraph:
    """Slim LangGraph for intraday signal generation: Trader → Risk Debate → PM."""

    def __init__(self, config: dict | None = None, callbacks: list | None = None):
        self.config = config or DEFAULT_CONFIG
        self.callbacks = callbacks or []
        set_config(self.config)

        llm_kwargs: dict[str, Any] = {}
        temperature = self.config.get("temperature")
        if temperature is not None and temperature != "":
            llm_kwargs["temperature"] = float(temperature)
        if self.callbacks:
            llm_kwargs["callbacks"] = self.callbacks

        quick_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["quick_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )
        deep_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["deep_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )

        self.quick_thinking_llm = quick_client.get_llm()
        self.deep_thinking_llm = deep_client.get_llm()
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config["max_debate_rounds"],
            max_risk_discuss_rounds=self.config["max_risk_discuss_rounds"],
        )
        self.propagator = Propagator(max_recur_limit=self.config.get("max_recur_limit", 100))
        self.graph = self._build_graph().compile()

    def _build_graph(self) -> StateGraph:
        workflow = StateGraph(AgentState)
        workflow.add_node("Trader", create_trader(self.quick_thinking_llm))
        workflow.add_node("Aggressive Analyst", create_aggressive_debator(self.quick_thinking_llm))
        workflow.add_node("Neutral Analyst", create_neutral_debator(self.quick_thinking_llm))
        workflow.add_node("Conservative Analyst", create_conservative_debator(self.quick_thinking_llm))
        workflow.add_node("Portfolio Manager", create_portfolio_manager(self.deep_thinking_llm))

        workflow.add_edge(START, "Trader")
        workflow.add_edge("Trader", "Aggressive Analyst")
        for risk_node in ("Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"):
            workflow.add_conditional_edges(
                risk_node,
                self.conditional_logic.should_continue_risk_analysis,
                RISK_ANALYSIS_PATH_MAP,
            )
        workflow.add_edge("Portfolio Manager", END)
        return workflow

    def propagate_intraday(
        self,
        symbol: str,
        bar_time: datetime,
        daily_bias: DailyBiasReport,
        mtf: MTFValidationResult,
        strategy_result: StrategyResult,
        gate_result: GateResult,
        *,
        strategy_name: str,
    ) -> IntradaySignal:
        trade_date = bar_time.strftime("%Y-%m-%d")
        init_state = self.propagator.create_initial_state(symbol, trade_date)
        intraday_context = {
            "scan_time": bar_time.isoformat(),
            "mtf_5min_snapshot": mtf.snapshot_5min,
            "mtf_30min_snapshot": mtf.snapshot_30min,
            "daily_bias": daily_bias.direction,
            "daily_bias_report": daily_bias.summary,
            "trends_aligned": mtf.trends_aligned,
            "vwap": mtf.vwap_5min,
            "atr": mtf.atr_5min,
            "strategy": {
                "name": strategy_name,
                "direction": strategy_result.direction,
                "factors_met": strategy_result.factors_met,
                "factors_missing": strategy_result.factors_missing,
                "reason": strategy_result.reason,
            },
            "gate_results": {
                "gate1": gate_result.gate1_strategy,
                "gate2": gate_result.gate2_mtf_alignment,
            },
        }
        trade_profile = self._build_trade_profile_block(
            symbol=symbol,
            strategy_name=strategy_name,
            direction=strategy_result.direction,
        )
        if trade_profile is not None:
            intraday_context["trade_profile"] = trade_profile
        init_state.update(
            {
                "market_report": daily_bias.summary,
                "fundamentals_report": "",
                "sentiment_report": "",
                "news_report": "",
                "investment_plan": daily_bias.summary,
                "magpie_signal": build_default_magpie_signal(enabled=False),
                "intraday_context": intraday_context,
            }
        )

        graph_args = self.propagator.get_graph_args(
            callbacks=self.callbacks if self.callbacks else None
        )
        final_state = self.graph.invoke(init_state, **graph_args)
        return _state_to_intraday_signal(
            symbol=symbol,
            bar_time=bar_time,
            direction=gate_result.final_direction,
            strategy_result=strategy_result,
            gate_result=gate_result,
            final_state=final_state,
        )

    def _build_trade_profile_block(
        self, *, symbol: str, strategy_name: str, direction: str
    ) -> dict | None:
        """Swing profile block for pro-trader runs; None when off/other strategy."""
        if strategy_name != "pro_trader_dashboard":
            return None
        profile = build_trade_profile(self.config)
        if profile is None:
            return None
        return {
            "enabled": True,
            "block": render_trade_profile(profile, symbol=symbol, direction=direction),
        }


def _state_to_intraday_signal(
    *,
    symbol: str,
    bar_time: datetime,
    direction: str,
    strategy_result: StrategyResult,
    gate_result: GateResult,
    final_state: dict,
) -> IntradaySignal:
    trader_plan = str(final_state.get("trader_investment_plan", ""))
    pm_decision = str(final_state.get("final_trade_decision", ""))
    reasoning = pm_decision or trader_plan

    entry_price = _extract_float(r"\*\*Entry Price\*\*:\s*([0-9.]+)", trader_plan)
    stop_loss = _extract_float(r"\*\*Stop Loss\*\*:\s*([0-9.]+)", trader_plan)

    action = _resolve_action(trader_plan, pm_decision, direction)
    confidence = (
        "Strong"
        if strategy_result.passed and not strategy_result.factors_missing
        else "Marginal"
    )

    return IntradaySignal(
        symbol=symbol,
        bar_time=bar_time,
        action=action,
        direction=direction if direction in ("long", "short") else "none",
        entry_price=entry_price,
        stop_loss=stop_loss,
        confidence=confidence,
        setup_score=len(strategy_result.factors_met),
        gate_summary=(
            f"G1={gate_result.gate1_strategy} G2={gate_result.gate2_mtf_alignment}"
        ),
        reasoning=reasoning,
    )


def _extract_float(pattern: str, text: str) -> float | None:
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _resolve_action(trader_plan: str, pm_decision: str, direction: str) -> str:
    combined = f"{trader_plan}\n{pm_decision}".upper()
    if "SELL" in combined or direction == "short":
        return "SELL"
    if "BUY" in combined or direction == "long":
        return "BUY"
    return "HOLD"
