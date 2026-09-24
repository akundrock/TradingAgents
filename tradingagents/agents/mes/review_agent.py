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
        standing_rules_summary: str = "",
        flips_summary: str = "",
        on_review=None,
    ) -> str:
        prompt = f"""You are an end-of-day trading coach debriefing a /MES (Micro E-mini S&P 500) discretionary trader. The session is closed and nothing can be changed — your only value is making tomorrow better.

Grade two things, and keep them strictly separate:

1. **The hypothesis.** Did this morning's read of the day type and bias describe the session that actually happened? Grade it against the realised market, not against the P&L. A correct read that made no money is still a correct read.

2. **The discipline.** Did the trader respect the gates, stay within sizing, and take entries that matched the plan? Grade the process independently of the outcome. A rule-break that made money is a failing grade, because it will not make money next time.

3. **The thesis flip.** The morning hypothesis states what would invalidate it. If the logged checks show that invalidation arriving — price sustained through the frame's anchor, internals rolling over, SPY breaking the level the read depends on — and no flip was journaled, the trader spent the rest of the session grading against a dead frame: name that missed flip as a discipline failure in what_failed and let it pull the discipline grade down. When a flip IS journaled, grade the flip itself: was it declared promptly once the invalidation was visible, and did the later checks follow the flipped frame?

Be direct. Vague encouragement is worthless here; name the specific decisions and cite the checks that show them.

---

**This Morning's Hypothesis:**
{hypothesis}

---

**Logged Checks Across The Session:**
{checks_summary}"""

        if flips_summary.strip():
            prompt += f"""

---

**Intraday Thesis Flips (journaled re-reads):**
{flips_summary}

The latest flip supersedes the morning hypothesis for the rest of the session:
grade checks logged after the flip against the flipped frame, and grade
whether the flip was declared promptly once the invalidation was visible."""

        prompt += f"""

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

        if standing_rules_summary.strip():
            prompt += f"""

---

**Standing Rules Previously Prescribed (and their compliance):**
{standing_rules_summary}

These are the machine-checked rules the copilot watched during the session and
whether each trigger was honored (a check logged), consciously skipped, or
MISSED. Treat every MISSED trigger as a discipline failure: name it in
what_failed and let it pull the discipline grade down."""

        prompt += """

---

**Prescribing standing rules (machine-checked tomorrow):**
If your discipline findings include a chart-watchable miss — hesitation at a
level, an un-watched retest — prescribe it in `standing_rules` so the copilot
will prompt a check at that level tomorrow. Rules must be retests of one of:
vwap, orb_top, orb_bottom, pdh, pdl, prior_close, prior_vah, prior_val,
poc, onh, onl. Zero to three rules; only ones you still believe in.
Omit `standing_rules` (leave it empty) when the improvement is not a
chart watch — do not force it.

Emit each rule in exactly this nested shape — the trigger fields MUST sit
inside the `trigger` object, never at the rule level:

{"standing_rules": [
  {"trigger": {"kind": "level_retest", "level": "vwap",
               "confirmation": "add_vold_aligned",
               "tolerance_points": 2},
   "note": "One-line why, shown in the radar banner."}]}

`level` is one of the levels listed above; `confirmation` is "none" or
"add_vold_aligned"; `tolerance_points` is between 0.25 and 10. Omit
`expires_on` — the pipeline stamps the next session's date for you. A rule
whose trigger fields are flattened to the rule level is invalid — nest them."""

        prompt += get_language_instruction()

        return invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_session_review,
            "MES Review Agent",
            on_model=on_review,
        )

    return run
