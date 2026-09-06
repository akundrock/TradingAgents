"""MES Trade Manager Agent: advisory-only commentary on an open position.

Unlike the gatekeeper, this agent has no authority: the deterministic ladder
owns every level. The manager reads the mechanical report and adds judgement
about context the rules cannot see (internals shifts, news spikes, unusual
behavior). Its output is advisory text rendered under the mechanical report;
it is never parsed into state and never moves a level.
"""

from __future__ import annotations

from tradingagents.agents.utils.agent_utils import get_language_instruction


def create_mes_manager_agent(llm):
    def run(
        *,
        mgmt_summary: str,
        market_context: str,
        hypothesis: str = "",
        current_price: float | None = None,
    ) -> str:
        price_line = (
            f"**Current /MES price: {current_price:.2f}**\n\n"
            if current_price is not None
            else ""
        )

        prompt = f"""You are the trade manager for a /MES (Micro E-mini S&P 500) discretionary desk.
A position is open and the deterministic ladder below already owns every level
decision: breakeven, partial profit-taking, the trailing stop, and the EOD
time stop. Do not propose changing any level; do not issue a verdict.

Your job is one paragraph of advisory commentary the mechanical plan cannot
see: internal context shifts, follow-through quality, or risk the mechanical
report misses. Reference specific readings (e.g. "$TICK whipsawing",
"$VOLD diverging from SPY bar direction"). Never invent levels, never suggest
targets beyond the plan's, and never advise against the plan's stop.

---

{price_line}**Position & Plan (deterministic — authoritative):**
{mgmt_summary}

---

**Market Context:**
{market_context}"""

        if hypothesis.strip():
            prompt += f"""

---

**This Morning's Hypothesis:**
{hypothesis}

Note when live price action contradicts the morning thesis; a thesis break is
worth flagging even when the mechanical ladder is quiet."""

        prompt += get_language_instruction()

        response = llm.invoke(prompt)
        return getattr(response, "content", "") or ""

    return run
