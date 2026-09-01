"""Fast Bias Rating: single-call substitute for the Bull/Bear/Research-Manager debate.

Used only when a graph is built with ``fast_research=True`` (currently the
intraday scanner's lazy-bias graph). Skips the multi-round debate entirely and
rates the analyst report(s) directly, on the caller-supplied (typically quick)
LLM, to cut latency for callers that need a daily-bias rating fast.
"""

from __future__ import annotations

from tradingagents.agents.schemas import ResearchPlan, render_research_plan
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


def create_fast_bias_rating(llm):
    structured_llm = bind_structured(llm, ResearchPlan, "Fast Bias Rating")

    def fast_bias_rating_node(state) -> dict:
        instrument_context = get_instrument_context_from_state(state)
        reports = [
            (label, state.get(key, ""))
            for key, label in (
                ("market_report", "Market analysis"),
                ("sentiment_report", "Social media sentiment"),
                ("news_report", "News"),
                ("fundamentals_report", "Fundamentals"),
            )
            if state.get(key)
        ]
        reports_block = "\n\n".join(f"**{label}**: {text}" for label, text in reports)

        prompt = f"""As a market analyst, rate the instrument below based solely on the analysis provided. No debate has been run; base your call only on this evidence.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong bullish conviction; recommend taking or growing the position
- **Overweight**: Constructive view; recommend gradually increasing exposure
- **Hold**: Balanced view; recommend maintaining the current position
- **Underweight**: Cautious view; recommend trimming exposure
- **Sell**: Strong bearish conviction; recommend exiting or avoiding the position

Reserve Hold for situations where the evidence is genuinely mixed or absent.

---

**Analysis:**
{reports_block}""" + get_language_instruction()

        investment_plan = invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_research_plan,
            "Fast Bias Rating",
        )

        new_investment_debate_state = {
            "judge_decision": investment_plan,
            "history": "",
            "bear_history": "",
            "bull_history": "",
            "current_response": investment_plan,
            "count": state["investment_debate_state"]["count"],
        }

        return {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": investment_plan,
        }

    return fast_bias_rating_node
