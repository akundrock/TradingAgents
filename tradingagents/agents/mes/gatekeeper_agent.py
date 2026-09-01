"""MES Gatekeeper Agent: judgement layer on top of the deterministic pre-trade checklist."""

from __future__ import annotations

from tradingagents.agents.schemas import TradeGoNoGo, render_trade_gonogo
from tradingagents.agents.utils.agent_utils import get_language_instruction
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


def create_mes_gatekeeper_agent(llm):
    structured_llm = bind_structured(llm, TradeGoNoGo, "MES Gatekeeper Agent")

    def run(
        *,
        checklist_markdown: str,
        market_context: str,
        hypothesis: str = "",
        past_context: str = "",
        tradeable: bool,
        sizing_note: str = "",
    ) -> str:
        prompt = f"""You are the risk gatekeeper for a /MES (Micro E-mini S&P 500) discretionary desk. A trade has been proposed and it does not reach the market until you rule on it.

The deterministic checklist below has already been evaluated in code. **It is authoritative on every hard threshold** the engine enforces: RTH/weekend session gates, morning block until the ORB window (unless enabled), last-entry and EOD flatten times, minimum ATR, missing-internals blocks, checklist score and tier, SPY confluence (3/5), and no-trade conditions (chop, whipsaw, exhaustion). Do not re-derive, re-argue, or second-guess those results; they are facts, not opinions.

Your job is the judgement the checklist cannot make: is this actually a setup worth risking capital on, at this location, right now? Gates being open is permission to trade, not a reason to trade.

**HARD CONSTRAINT — READ THIS TWICE:**
The checklist reports this setup as: **{"TRADEABLE" if tradeable else "NOT TRADEABLE"}**.
If it is NOT TRADEABLE, your verdict MUST be "Stand Down". There is no market condition, no
quality of setup, and no argument in the context below that overrides this. Do not return
"Take". Do not return "Wait". Return "Stand Down" and explain which gate stopped the trade.

---

**Deterministic Checklist:**
{checklist_markdown}

---

**Market Context:**
{market_context}"""

        if hypothesis.strip():
            prompt += f"""

---

**This Morning's Hypothesis:**
{hypothesis}

A setup that agrees with the morning hypothesis is worth more than one that fights it. If this trade contradicts the hypothesis, say so and demand a correspondingly better location before allowing it."""

        if sizing_note.strip():
            prompt += f"""

---

**Sizing Constraint:**
{sizing_note}

Never suggest more contracts than this allows."""

        if past_context.strip():
            prompt += f"""

---

**Lessons From Prior Sessions:**
{past_context}

If this setup resembles a documented past mistake, weight heavily toward Wait or Stand Down."""

        prompt += get_language_instruction()

        return invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_trade_gonogo,
            "MES Gatekeeper Agent",
        )

    return run
