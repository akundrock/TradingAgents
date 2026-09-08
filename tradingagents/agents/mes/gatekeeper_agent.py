"""MES Gatekeeper Agent: judgement layer on top of the deterministic pre-trade checklist."""

from __future__ import annotations

from tradingagents.agents.schemas import TradeGoNoGo, render_trade_gonogo
from tradingagents.agents.utils.agent_utils import get_language_instruction
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)
from tradingagents.mes.levels import normalize_trade_gonogo


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
        current_price: float | None = None,
        trade_levels_hint: str = "",
    ) -> str:
        price_line = (
            f"**Current /MES price: {current_price:.2f}**\n\n" if current_price is not None else ""
        )

        prompt = f"""You are the risk gatekeeper for a /MES (Micro E-mini S&P 500) discretionary desk. A trade has been proposed and it does not reach the market until you rule on it.

The deterministic checklist below has already been evaluated in code. **It is authoritative on every hard threshold** the engine enforces: RTH/weekend session gates, morning block until the ORB window (unless enabled), last-entry and EOD flatten times, minimum ATR, missing-internals blocks, checklist score and tier, SPY confluence (3/5), and no-trade conditions (chop, whipsaw, exhaustion). Do not re-derive, re-argue, or second-guess those results; they are facts, not opinions.

Your job is the judgement the checklist cannot make: is this actually a setup worth risking capital on, at this location, right now? Gates being open is permission to trade, not a reason to trade.

**HARD CONSTRAINT — READ THIS TWICE:**
The checklist reports this setup as: **{"TRADEABLE" if tradeable else "NOT TRADEABLE"}**.
If it is NOT TRADEABLE, your verdict MUST be "Stand Down". There is no market condition, no
quality of setup, and no argument in the context below that overrides this. Do not return
"Take". Do not return "Wait". Return "Stand Down" and explain which gate stopped the trade.

**TRADE LEVEL RULES — MANDATORY:**
- When verdict is **Wait** or **Stand Down**: set `direction` to `none` and leave `entry_zone`, `stop_level`, `first_target`, and `suggested_contracts` all **null/omitted**. Watch levels and trigger conditions belong in `what_would_change_my_mind`, not in the trade level fields.
- When verdict is **Take** and direction is **long**: `first_target` MUST be strictly **above** the entry zone and strictly above current price. `stop_level` MUST be strictly **below** the entry zone. Do not cite a level that price has already cleared as a profit target.
- When verdict is **Take** and direction is **short**: `first_target` MUST be strictly **below** the entry zone and strictly below current price. `stop_level` MUST be strictly **above** the entry zone.

---

{price_line}**Deterministic Checklist:**
{checklist_markdown}

---

**Market Context:**
{market_context}"""

        if trade_levels_hint.strip():
            prompt += f"""

---

**Suggested Trade Levels (deterministically derived from structural data):**
{trade_levels_hint}

Use these levels as your starting point. You may adjust them for a better structural location, but any `first_target` you set must still satisfy the directional ordering rule above."""

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

        def _render_normalized(v: TradeGoNoGo) -> str:
            normalize_trade_gonogo(v)
            return render_trade_gonogo(v)

        return invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            _render_normalized,
            "MES Gatekeeper Agent",
        )

    return run
