"""Pydantic schemas used by agents that produce structured output.

The framework's primary artifact is still prose: each agent's natural-language
reasoning is what users read in the saved markdown reports and what the
downstream agents read as context.  Structured output is layered onto the
three decision-making agents (Research Manager, Trader, Portfolio Manager)
so that:

- Their outputs follow consistent section headers across runs and providers
- Each provider's native structured-output mode is used (json_schema for
  OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic)
- Schema field descriptions become the model's output instructions, freeing
  the prompt body to focus on context and the rating-scale guidance
- A render helper turns the parsed Pydantic instance back into the same
  markdown shape the rest of the system already consumes, so display,
  memory log, and saved reports keep working unchanged
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# LLMs sometimes write a placeholder string ("None", "N/A", ...) into an optional
# numeric field instead of omitting it. Coerce those to None so the structured
# call validates instead of erroring (#1058). Pydantic still parses real numeric
# strings ("189.5") to float.
_NULLISH_FLOAT = {"", "none", "n/a", "na", "null", "nil", "-", "tbd", "unknown"}


def _coerce_optional_float(value):
    if isinstance(value, str) and value.strip().lower() in _NULLISH_FLOAT:
        return None
    return value


# ---------------------------------------------------------------------------
# Shared rating types
# ---------------------------------------------------------------------------


class PortfolioRating(str, Enum):
    """5-tier rating used by the Research Manager and Portfolio Manager."""

    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class TraderAction(str, Enum):
    """3-tier transaction direction used by the Trader.

    The Trader's job is to translate the Research Manager's investment plan
    into a concrete transaction proposal: should the desk execute a Buy, a
    Sell, or sit on Hold this round.  Position sizing and the nuanced
    Overweight / Underweight calls happen later at the Portfolio Manager.
    """

    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"


# ---------------------------------------------------------------------------
# Research Manager
# ---------------------------------------------------------------------------


class ResearchPlan(BaseModel):
    """Structured investment plan produced by the Research Manager.

    Hand-off to the Trader: the recommendation pins the directional view,
    the rationale captures which side of the bull/bear debate carried the
    argument, and the strategic actions translate that into concrete
    instructions the trader can execute against.
    """

    recommendation: PortfolioRating = Field(
        description=(
            "The investment recommendation. Exactly one of Buy / Overweight / "
            "Hold / Underweight / Sell. Reserve Hold for situations where the "
            "evidence on both sides is genuinely balanced; otherwise commit to "
            "the side with the stronger arguments."
        ),
    )
    rationale: str = Field(
        description=(
            "Conversational summary of the key points from both sides of the "
            "debate, ending with which arguments led to the recommendation. "
            "Speak naturally, as if to a teammate."
        ),
    )
    strategic_actions: str = Field(
        description=(
            "Concrete steps for the trader to implement the recommendation, "
            "including position sizing guidance consistent with the rating."
        ),
    )


def render_research_plan(plan: ResearchPlan) -> str:
    """Render a ResearchPlan to markdown for storage and the trader's prompt context."""
    return "\n".join([
        f"**Recommendation**: {plan.recommendation.value}",
        "",
        f"**Rationale**: {plan.rationale}",
        "",
        f"**Strategic Actions**: {plan.strategic_actions}",
    ])


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------


class TraderProposal(BaseModel):
    """Structured transaction proposal produced by the Trader.

    The trader reads the Research Manager's investment plan and the analyst
    reports, then turns them into a concrete transaction: what action to
    take, the reasoning that justifies it, and the practical levels for
    entry, stop-loss, and sizing.
    """

    action: TraderAction = Field(
        description="The transaction direction. Exactly one of Buy / Hold / Sell.",
    )
    reasoning: str = Field(
        description=(
            "The case for this action, anchored in the analysts' reports and "
            "the research plan. Two to four sentences."
        ),
    )
    entry_price: float | None = Field(
        default=None,
        description="Optional entry price target in the instrument's quote currency.",
    )
    stop_loss: float | None = Field(
        default=None,
        description="Optional stop-loss price in the instrument's quote currency.",
    )
    position_sizing: str | None = Field(
        default=None,
        description="Optional sizing guidance, e.g. '5% of portfolio'.",
    )

    @field_validator("entry_price", "stop_loss", mode="before")
    @classmethod
    def _nullish_float_to_none(cls, v):
        return _coerce_optional_float(v)


def render_trader_proposal(proposal: TraderProposal) -> str:
    """Render a TraderProposal to markdown.

    The trailing ``FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`` line is
    preserved for backward compatibility with the analyst stop-signal text
    and any external code that greps for it.
    """
    parts = [
        f"**Action**: {proposal.action.value}",
        "",
        f"**Reasoning**: {proposal.reasoning}",
    ]
    if proposal.entry_price is not None:
        parts.extend(["", f"**Entry Price**: {proposal.entry_price}"])
    if proposal.stop_loss is not None:
        parts.extend(["", f"**Stop Loss**: {proposal.stop_loss}"])
    if proposal.position_sizing:
        parts.extend(["", f"**Position Sizing**: {proposal.position_sizing}"])
    parts.extend([
        "",
        f"FINAL TRANSACTION PROPOSAL: **{proposal.action.value.upper()}**",
    ])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Portfolio Manager
# ---------------------------------------------------------------------------


class PortfolioDecision(BaseModel):
    """Structured output produced by the Portfolio Manager.

    The model fills every field as part of its primary LLM call; no separate
    extraction pass is required. Field descriptions double as the model's
    output instructions, so the prompt body only needs to convey context and
    the rating-scale guidance.
    """

    rating: PortfolioRating = Field(
        description=(
            "The final position rating. Exactly one of Buy / Overweight / Hold / "
            "Underweight / Sell, picked based on the analysts' debate."
        ),
    )
    executive_summary: str = Field(
        description=(
            "A concise action plan covering entry strategy, position sizing, "
            "key risk levels, and time horizon. Two to four sentences."
        ),
    )
    investment_thesis: str = Field(
        description=(
            "Detailed reasoning anchored in specific evidence from the analysts' "
            "debate. If prior lessons are referenced in the prompt context, "
            "incorporate them; otherwise rely solely on the current analysis."
        ),
    )
    price_target: float | None = Field(
        default=None,
        description="Optional target price in the instrument's quote currency.",
    )
    time_horizon: str | None = Field(
        default=None,
        description="Optional recommended holding period, e.g. '3-6 months'.",
    )

    @field_validator("price_target", mode="before")
    @classmethod
    def _nullish_float_to_none(cls, v):
        return _coerce_optional_float(v)


def render_pm_decision(decision: PortfolioDecision) -> str:
    """Render a PortfolioDecision back to the markdown shape the rest of the system expects.

    Memory log, CLI display, and saved report files all read this markdown,
    so the rendered output preserves the exact section headers (``**Rating**``,
    ``**Executive Summary**``, ``**Investment Thesis**``) that downstream
    parsers and the report writers already handle.
    """
    parts = [
        f"**Rating**: {decision.rating.value}",
        "",
        f"**Executive Summary**: {decision.executive_summary}",
        "",
        f"**Investment Thesis**: {decision.investment_thesis}",
    ]
    if decision.price_target is not None:
        parts.extend(["", f"**Price Target**: {decision.price_target}"])
    if decision.time_horizon:
        parts.extend(["", f"**Time Horizon**: {decision.time_horizon}"])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Sentiment Analyst
# ---------------------------------------------------------------------------


class SentimentBand(str, Enum):
    """Discrete sentiment direction produced by the Sentiment Analyst.

    Six tiers keep the signal granular enough to be actionable while remaining
    small enough for every provider to map reliably from its JSON output.
    """

    BULLISH = "Bullish"
    MILDLY_BULLISH = "Mildly Bullish"
    NEUTRAL = "Neutral"
    MIXED = "Mixed"
    MILDLY_BEARISH = "Mildly Bearish"
    BEARISH = "Bearish"


class SentimentReport(BaseModel):
    """Structured sentiment report produced by the Sentiment Analyst.

    Replaces the previous free-form prose output so downstream consumers
    (dashboards, audit logs, PDF renderers, other agents) can read
    ``overall_band`` and ``overall_score`` without maintaining fragile regex
    fallbacks that drift with every model release. ``narrative`` preserves the
    rich source-by-source analysis; ``render_sentiment_report`` prepends a
    deterministic header so the saved report stays human-readable.
    """

    overall_band: SentimentBand = Field(
        description=(
            "Overall sentiment direction. Exactly one of: "
            "Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish. "
            "Use Mixed when sources point in clearly different directions. "
            "Use Neutral only when all sources are genuinely silent or non-committal."
        ),
    )
    overall_score: float = Field(
        ge=0.0,
        le=10.0,
        description=(
            "Numeric sentiment intensity on a 0–10 scale. "
            "0 = maximally bearish, 5 = neutral, 10 = maximally bullish. "
            "Guideline for consistency with overall_band: "
            "Bullish ~6.5–10, Mildly Bullish ~5.5–6.4, Neutral/Mixed ~4.5–5.5, "
            "Mildly Bearish ~3.5–4.4, Bearish ~0–3.4. "
            "Only the 0–10 bounds are enforced."
        ),
    )
    confidence: Literal["low", "medium", "high"] = Field(
        description=(
            "Confidence in the assessment based on data quality and sample size. "
            "Use 'low' when one or more sources returned a placeholder or fewer "
            "than 5 data points; 'medium' when data is present but sparse; "
            "'high' when all three sources returned substantive data."
        ),
    )

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalise_confidence(cls, v: object) -> object:
        if isinstance(v, str):
            return v.lower()
        return v

    narrative: str = Field(
        description=(
            "Full sentiment report covering, in order: "
            "(1) source-by-source breakdown with specific evidence (cite message "
            "counts, ratios, notable posts); "
            "(2) cross-source divergences and alignments; "
            "(3) dominant narrative themes; "
            "(4) catalysts and risks surfaced by the data; "
            "(5) a markdown table summarising key sentiment signals, their "
            "direction, source, and supporting evidence. "
            "Keep it informative and substantive: develop each section thoroughly "
            "with concrete evidence so every point adds new signal for the trader."
        ),
    )


def render_sentiment_report(report: SentimentReport) -> str:
    """Render a SentimentReport to the markdown shape the rest of the system expects.

    The structured header (band + score + confidence) is prepended to the
    narrative so the saved report is both human-readable and machine-parseable
    without regex.
    """
    return "\n".join([
        f"**Overall Sentiment:** **{report.overall_band.value}** "
        f"(Score: {report.overall_score:.1f}/10)",
        f"**Confidence:** {report.confidence.capitalize()}",
        "",
        report.narrative,
    ])


# ---------------------------------------------------------------------------
# MES Morning Agent
# ---------------------------------------------------------------------------


class DayType(str, Enum):
    """Session character the morning hypothesis commits to for /MES."""

    TREND_UP = "Trend Up"
    TREND_DOWN = "Trend Down"
    RANGE = "Range"
    UNCLEAR = "Unclear"


class MarketBias(str, Enum):
    """Directional lean carried into the session."""

    BULLISH = "Bullish"
    BEARISH = "Bearish"
    NEUTRAL = "Neutral"


class MorningHypothesis(BaseModel):
    """Structured morning plan produced by the MES Morning Agent.

    The hypothesis is written before the session develops: it commits to a
    day type and a bias derived from the market internals ($ADD, $TICK,
    $VOLD) and the structural levels on /MES and SPY, and it states up front
    what price action would prove the plan wrong.
    """

    day_type: DayType = Field(
        description=(
            "The expected session character. Exactly one of Trend Up / Trend Down / "
            "Range / Unclear. Lean on $ADD breadth persistence and $TICK extremes: "
            "a $ADD that holds one side with $TICK pushing repeated same-side "
            "extremes argues for a trend day; a $ADD oscillating around zero with "
            "two-sided $TICK argues for a range day. Use Unclear only when the "
            "internals genuinely conflict."
        ),
    )
    bias: MarketBias = Field(
        description=(
            "Directional lean for /MES. Exactly one of Bullish / Bearish / Neutral. "
            "Must be consistent with the day_type and with the sign of $ADD and "
            "$VOLD in the supplied context."
        ),
    )
    one_sentence_thesis: str = Field(
        description=(
            "A single sentence stating what you expect /MES to do today and why, "
            "referencing SPY's direction and the internals that support it."
        ),
    )
    key_levels: str = Field(
        description=(
            "The price levels that matter today for /MES (and the SPY equivalents "
            "where useful): session VWAP, the opening-range high and low, and prior-"
            "day high/low/close. State each level numerically and say what it means "
            "if price accepts or rejects there."
        ),
    )
    invalidation: str = Field(
        description=(
            "The specific, observable condition that kills this hypothesis, e.g. a "
            "sustained /MES close back through VWAP against the bias, a failed "
            "opening-range break, or $ADD flipping sign and holding."
        ),
    )
    confidence: Literal["low", "medium", "high"] = Field(
        description=(
            "Confidence in the hypothesis. Use 'low' when internals conflict or the "
            "opening range has not resolved; 'medium' when the picture leans one way "
            "but confirmation is thin; 'high' when $ADD, $TICK, $VOLD, and price "
            "relative to VWAP all agree."
        ),
    )

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalise_confidence(cls, v: object) -> object:
        if isinstance(v, str):
            return v.lower()
        return v

    narrative: str = Field(
        description=(
            "Full morning plan covering, in order: "
            "(1) how the internals ($ADD, $TICK, $VOLD) are reading and what that "
            "implies about participation; "
            "(2) where /MES and SPY sit relative to VWAP, the opening range, and "
            "prior-day levels; "
            "(3) the primary scenario with the trade locations it would offer; "
            "(4) the alternate scenario if the primary fails; "
            "(5) what to avoid today. "
            "Be concrete and numeric; every claim should be traceable to the supplied "
            "market context."
        ),
    )


def render_morning_hypothesis(hypothesis: MorningHypothesis) -> str:
    """Render a MorningHypothesis to the markdown the CLI displays and logs."""
    return "\n".join([
        f"**Day Type**: {hypothesis.day_type.value}",
        f"**Bias**: {hypothesis.bias.value}",
        f"**Thesis**: {hypothesis.one_sentence_thesis}",
        f"**Key Levels**: {hypothesis.key_levels}",
        f"**Invalidation**: {hypothesis.invalidation}",
        f"**Confidence**: {hypothesis.confidence.capitalize()}",
        "",
        hypothesis.narrative,
    ])


# ---------------------------------------------------------------------------
# MES Gatekeeper Agent
# ---------------------------------------------------------------------------


class TradeVerdict(str, Enum):
    """Gatekeeper's ruling on whether a trade may be placed right now."""

    TAKE = "Take"
    WAIT = "Wait"
    STAND_DOWN = "Stand Down"


class TradeGoNoGo(BaseModel):
    """Structured go/no-go ruling produced by the MES Gatekeeper Agent.

    The deterministic checklist owns the hard thresholds; this schema carries
    the judgement layer on top of it — whether the current setup is worth
    risking capital on, in which direction, and what would change the call.
    """

    verdict: TradeVerdict = Field(
        description=(
            "The ruling on this setup. Exactly one of Take / Wait / Stand Down. "
            "You may never return Take when the deterministic checklist reports "
            "gates BLOCKED or NOT TRADEABLE — in that case return Stand Down. "
            "Use Wait when the gates are open but the setup has not yet arrived at "
            "a location worth paying for."
        ),
    )
    direction: Literal["long", "short", "none"] = Field(
        description=(
            "Trade direction. Use 'none' whenever the verdict is Wait or Stand Down, "
            "and otherwise the side the setup favours."
        ),
    )
    confidence: Literal["low", "medium", "high"] = Field(
        description=(
            "Confidence in this ruling given the checklist state, the internals, and "
            "how cleanly the setup matches the morning hypothesis."
        ),
    )

    @field_validator("confidence", "direction", mode="before")
    @classmethod
    def _normalise_literals(cls, v: object) -> object:
        if isinstance(v, str):
            return v.lower()
        return v

    reasoning: str = Field(
        description=(
            "The case for the verdict in two to four sentences, citing the specific "
            "checklist lines and market-context readings that drove it."
        ),
    )
    entry_zone: str | None = Field(
        default=None,
        description=(
            "Optional /MES entry zone as a price range, e.g. '5812.50-5814.00'. Omit "
            "unless the verdict is Take."
        ),
    )
    stop_level: float | None = Field(
        default=None,
        description=(
            "Optional /MES stop price. Place it beyond the structure that would "
            "invalidate the trade, not at an arbitrary tick distance. Omit unless the "
            "verdict is Take."
        ),
    )
    first_target: float | None = Field(
        default=None,
        description=(
            "Optional /MES first-target price, typically the next structural level "
            "(VWAP, opening-range edge, prior-day level). Omit unless the verdict is Take."
        ),
    )
    suggested_contracts: int | None = Field(
        default=None,
        description=(
            "Optional /MES contract count, never exceeding the size allowed by the "
            "supplied sizing note. Omit unless the verdict is Take."
        ),
    )
    what_would_change_my_mind: str = Field(
        description=(
            "The specific, observable market development that would flip this verdict: "
            "what would turn a Wait into a Take, or a Take into a Stand Down."
        ),
    )

    @field_validator("stop_level", "first_target", mode="before")
    @classmethod
    def _nullish_float_to_none(cls, v):
        return _coerce_optional_float(v)


def render_trade_gonogo(verdict: TradeGoNoGo) -> str:
    """Render a TradeGoNoGo to the markdown the CLI displays and logs."""
    parts = [
        f"**Verdict**: {verdict.verdict.value}",
        f"**Direction**: {verdict.direction.capitalize()}",
        f"**Confidence**: {verdict.confidence.capitalize()}",
        "",
        f"**Reasoning**: {verdict.reasoning}",
    ]
    if verdict.entry_zone:
        parts.extend(["", f"**Entry Zone**: {verdict.entry_zone}"])
    if verdict.stop_level is not None:
        parts.extend(["", f"**Stop**: {verdict.stop_level}"])
    if verdict.first_target is not None:
        parts.extend(["", f"**First Target**: {verdict.first_target}"])
    if verdict.suggested_contracts is not None:
        parts.extend(["", f"**Suggested Contracts**: {verdict.suggested_contracts}"])
    parts.extend([
        "",
        f"**What Would Change My Mind**: {verdict.what_would_change_my_mind}",
    ])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# MES Review Agent
# ---------------------------------------------------------------------------


class HypothesisGrade(str, Enum):
    """How the morning hypothesis held up against the session that followed."""

    CORRECT = "Correct"
    PARTIAL = "Partial"
    WRONG = "Wrong"


class SessionReview(BaseModel):
    """Structured end-of-day review produced by the MES Review Agent.

    Grades two separate things that are easy to confuse: whether the morning
    read of the market was right, and whether the process followed during the
    session was disciplined. A correct hypothesis traded badly still earns a
    poor discipline grade, and vice versa.
    """

    hypothesis_grade: HypothesisGrade = Field(
        description=(
            "How well the morning hypothesis described the session that actually "
            "happened. Exactly one of Correct / Partial / Wrong. Grade the day type "
            "and bias against the realised session, not against the P&L."
        ),
    )
    discipline_grade: Literal["A", "B", "C", "D", "F"] = Field(
        description=(
            "Letter grade for process discipline across the day's logged checks: "
            "whether gates were respected, sizing stayed within limits, and entries "
            "matched the plan. Grade the process independently of the outcome — a "
            "profitable rule-break is still a failing grade."
        ),
    )
    what_worked: str = Field(
        description=(
            "The specific reads, waits, and executions that went right, with the "
            "evidence from the logged checks and outcomes that shows it."
        ),
    )
    what_failed: str = Field(
        description=(
            "The specific misreads, rule-breaks, or hesitations that cost money or "
            "opportunity, again anchored in the logged checks and outcomes."
        ),
    )
    one_improvement: str = Field(
        description=(
            "Exactly one concrete, actionable change to apply to the next session. "
            "One change only — the most valuable one — stated as a rule that can be "
            "checked tomorrow."
        ),
    )
    narrative: str = Field(
        description=(
            "Full review covering, in order: "
            "(1) what the morning hypothesis predicted and what the session delivered; "
            "(2) a walk through the day's logged checks and how each decision held up; "
            "(3) the pattern behind the mistakes, if there is one; "
            "(4) what to carry into tomorrow's morning plan. "
            "Be direct and specific; a coach's honest debrief, not a summary."
        ),
    )


def render_session_review(review: SessionReview) -> str:
    """Render a SessionReview to the markdown the CLI displays and logs."""
    return "\n".join([
        f"**Hypothesis Grade**: {review.hypothesis_grade.value}",
        f"**Discipline Grade**: {review.discipline_grade}",
        "",
        f"**What Worked**: {review.what_worked}",
        "",
        f"**What Failed**: {review.what_failed}",
        "",
        f"**One Improvement**: {review.one_improvement}",
        "",
        review.narrative,
    ])
