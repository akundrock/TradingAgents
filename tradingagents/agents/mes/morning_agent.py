"""MES Morning Agent: turns the pre-session market context into a day-type hypothesis."""

from __future__ import annotations

from tradingagents.agents.schemas import MorningHypothesis, render_morning_hypothesis
from tradingagents.agents.utils.agent_utils import get_language_instruction
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


def _morning_task_instructions(*, rth_started: bool) -> str:
    if rth_started:
        return """Your job right now is to commit to a hypothesis for the session before it develops further. Build it from the market context below — the internals ($ADD advance/decline, $TICK, $VOLD up/down volume), the opening range, session VWAP, and the prior-day levels. Do not invent numbers: every level and reading you cite must appear in the context."""
    return """Regular trading hours have not started yet. Build a **pre-open hypothesis** from the prior completed session levels (POC, VAH, VAL, high/low/close), the overnight range, and where last price sits relative to those marks. Do not invent numbers: every level you cite must appear in the context.

$ADD, $TICK, $VOLD, today's session VWAP, and the opening range **do not exist yet** — they publish after the 09:30 ET open. Frame your day-type expectation conditionally (for example: "if $ADD expands above +1000 and holds…" or "if price rejects prior VAH and breadth stays weak…"). Do not state current internals readings as facts."""


def create_mes_morning_agent(llm):
    structured_llm = bind_structured(llm, MorningHypothesis, "MES Morning Agent")

    def run(*, market_context: str, past_context: str = "", rth_started: bool = False) -> str:
        task = _morning_task_instructions(rth_started=rth_started)
        prompt = f"""You are a futures desk strategist preparing the morning plan for /MES (Micro E-mini S&P 500). SPY is your directional reference: it trades the same underlying index with cleaner internals, so read SPY for direction and express the trade in /MES.

{task}

A hypothesis is only useful if it can be wrong. State plainly what price action would invalidate it, so that later in the session you can tell the difference between a plan that is working and one that has already failed.

---

**Market Context:**
{market_context}"""

        if past_context.strip():
            prompt += f"""

---

**Lessons From Prior Sessions:**
{past_context}

Weigh these lessons against today's context. They describe recurring mistakes, not rules about today's market — if today's internals disagree with a prior lesson, follow today's internals and say so."""

        prompt += get_language_instruction()

        return invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_morning_hypothesis,
            "MES Morning Agent",
        )

    return run
