"""MES Review Agent: end-of-day coach grading the morning hypothesis and the day's discipline."""

from __future__ import annotations

from tradingagents.agents.schemas import SessionReview, render_session_review
from tradingagents.agents.utils.agent_utils import get_language_instruction
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


def create_mes_review_agent(llm):
    structured_llm = bind_structured(llm, SessionReview, "MES Review Agent")

    def run(
        *,
        hypothesis: str,
        checks_summary: str,
        outcome_summary: str,
        trades_summary: str = "",
    ) -> str:
        prompt = f"""You are an end-of-day trading coach debriefing a /MES (Micro E-mini S&P 500) discretionary trader. The session is closed and nothing can be changed — your only value is making tomorrow better.

Grade two things, and keep them strictly separate:

1. **The hypothesis.** Did this morning's read of the day type and bias describe the session that actually happened? Grade it against the realised market, not against the P&L. A correct read that made no money is still a correct read.

2. **The discipline.** Did the trader respect the gates, stay within sizing, and take entries that matched the plan? Grade the process independently of the outcome. A rule-break that made money is a failing grade, because it will not make money next time.

Be direct. Vague encouragement is worthless here; name the specific decisions and cite the checks that show them.

---

**This Morning's Hypothesis:**
{hypothesis}

---

**Logged Checks Across The Session:**
{checks_summary}

---

**Outcomes:**
{outcome_summary}"""

        if trades_summary.strip():
            prompt += f"""

---

**Trades Taken (from the journal):**
{trades_summary}

Grade the execution, not just the hypothesis: entry location quality vs. the
checklist tier at the time, stop management, whether the exit respected the
plan (time stop / target / manual), and what the realized R says about the
tier the entry was taken at."""

        prompt += get_language_instruction()

        return invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_session_review,
            "MES Review Agent",
        )

    return run
