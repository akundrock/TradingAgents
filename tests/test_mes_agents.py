from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tradingagents.agents.mes import (
    create_mes_gatekeeper_agent,
    create_mes_manager_agent,
    create_mes_morning_agent,
    create_mes_review_agent,
)
from tradingagents.agents.schemas import (
    Confirmation,
    DayType,
    HypothesisGrade,
    MarketBias,
    MorningHypothesis,
    RuleLevel,
    SessionReview,
    StandingRule,
    TradeGoNoGo,
    TradeVerdict,
    render_session_review,
)
from tradingagents.agents.utils.structured import invoke_structured_or_freetext



class _StructuredStub:
    def __init__(self, owner, schema):
        self._owner = owner
        self._schema = schema

    def invoke(self, prompt):
        self._owner.prompts.append(prompt)
        if self._owner.structured_error is not None:
            raise self._owner.structured_error
        return self._owner.structured_results.get(self._schema)


class FakeLLM:
    """Chat-model stand-in that records every prompt it is handed."""

    def __init__(self, structured_results=None, structured_error=None, text="free text answer"):
        self.structured_results = structured_results or {}
        self.structured_error = structured_error
        self.text = text
        self.prompts: list[str] = []
        self.bound_schemas: list[type] = []

    def with_structured_output(self, schema):
        self.bound_schemas.append(schema)
        return _StructuredStub(self, schema)

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.text)


class LegacyLLM:
    """Provider without structured-output support."""

    def __init__(self, text="legacy free text"):
        self.text = text
        self.prompts: list[str] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.text)


def _hypothesis() -> MorningHypothesis:
    return MorningHypothesis(
        day_type=DayType.TREND_UP,
        bias=MarketBias.BULLISH,
        one_sentence_thesis="SPY holds VWAP with $ADD +1200, so /MES grinds up.",
        key_levels="VWAP 5000, ORB 4995-5010",
        invalidation="Sustained close below VWAP",
        confidence="medium",
        narrative="Internals lean bullish.",
    )


def _gonogo(verdict=TradeVerdict.TAKE) -> TradeGoNoGo:
    return TradeGoNoGo(
        verdict=verdict,
        direction="long" if verdict is TradeVerdict.TAKE else "none",
        confidence="high",
        reasoning="Checklist is premium tier with SPY confluence 5/5.",
        entry_zone="5000.00-5002.00",
        stop_level=4995.0,
        first_target=5010.0,
        suggested_contracts=2,
        what_would_change_my_mind="$ADD flipping negative and holding.",
    )


def _review() -> SessionReview:
    return SessionReview(
        hypothesis_grade=HypothesisGrade.PARTIAL,
        discipline_grade="B",
        what_worked="Waited for the 10:15 retest.",
        what_failed="Second entry ignored the chop gate.",
        one_improvement="No entries while $ADD sits inside +/-500.",
        narrative="Solid read, sloppy second half.",
    )


# ---------------------------------------------------------------------------
# Morning agent
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_morning_agent_renders_structured_hypothesis():
    llm = FakeLLM({MorningHypothesis: _hypothesis()})
    output = create_mes_morning_agent(llm)(market_context="## Market context\n$ADD +1200")

    assert llm.bound_schemas == [MorningHypothesis]
    assert "**Day Type**: Trend Up" in output
    assert "**Bias**: Bullish" in output
    assert "**Confidence**: Medium" in output
    assert "$ADD +1200" in llm.prompts[0]


@pytest.mark.unit
def test_morning_agent_includes_past_context_only_when_supplied():
    llm = FakeLLM({MorningHypothesis: _hypothesis()})
    run = create_mes_morning_agent(llm)

    run(market_context="ctx")
    assert "Lessons From Prior Sessions" not in llm.prompts[0]

    run(market_context="ctx", past_context="Stop chasing $TICK extremes.")
    assert "Lessons From Prior Sessions" in llm.prompts[1]
    assert "Stop chasing $TICK extremes." in llm.prompts[1]


@pytest.mark.unit
def test_morning_agent_pre_open_prompt_defers_internals():
    llm = FakeLLM({MorningHypothesis: _hypothesis()})
    create_mes_morning_agent(llm)(market_context="ctx", rth_started=False)

    prompt = llm.prompts[0]
    assert "pre-open hypothesis" in prompt
    assert "do not exist yet" in prompt
    assert "opening range, session VWAP" not in prompt


@pytest.mark.unit
def test_morning_agent_intraday_prompt_uses_live_internals():
    llm = FakeLLM({MorningHypothesis: _hypothesis()})
    create_mes_morning_agent(llm)(market_context="ctx", rth_started=True)

    prompt = llm.prompts[0]
    assert "internals ($ADD advance/decline, $TICK, $VOLD" in prompt
    assert "pre-open hypothesis" not in prompt


@pytest.mark.unit
def test_morning_agent_falls_back_to_free_text_when_structured_call_fails():
    llm = FakeLLM(structured_error=RuntimeError("bad json"), text="plain morning plan")
    output = create_mes_morning_agent(llm)(market_context="ctx")

    assert output == "plain morning plan"
    # Structured attempt, one structured retry, then free text — same prompt each time.
    assert len(llm.prompts) == 3
    assert llm.prompts[0] == llm.prompts[1] == llm.prompts[2]


@pytest.mark.unit
def test_morning_agent_falls_back_when_structured_output_returns_nothing():
    llm = FakeLLM({MorningHypothesis: None}, text="plain morning plan")
    assert create_mes_morning_agent(llm)(market_context="ctx") == "plain morning plan"


@pytest.mark.unit
def test_morning_agent_works_with_a_provider_without_structured_output():
    llm = LegacyLLM()
    assert create_mes_morning_agent(llm)(market_context="ctx") == "legacy free text"
    assert len(llm.prompts) == 1


# ---------------------------------------------------------------------------
# Gatekeeper agent
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_gatekeeper_renders_structured_verdict():
    llm = FakeLLM({TradeGoNoGo: _gonogo()})
    output = create_mes_gatekeeper_agent(llm)(
        checklist_markdown="## MES Entry Checklist",
        market_context="## Market context",
        tradeable=True,
    )

    assert "**Verdict**: Take" in output
    assert "**Direction**: Long" in output
    assert "**Entry Zone**: 5000.00-5002.00" in output
    assert "**Stop**: 4995.0" in output
    assert "**Suggested Contracts**: 2" in output
    assert "**What Would Change My Mind**" in output


@pytest.mark.unit
def test_gatekeeper_prompt_hard_constraint_when_not_tradeable():
    llm = FakeLLM({TradeGoNoGo: _gonogo(TradeVerdict.STAND_DOWN)})
    create_mes_gatekeeper_agent(llm)(
        checklist_markdown="## MES Entry Checklist",
        market_context="## Market context",
        tradeable=False,
    )

    prompt = llm.prompts[0]
    assert "HARD CONSTRAINT" in prompt
    assert "**NOT TRADEABLE**" in prompt
    assert 'your verdict MUST be "Stand Down"' in prompt
    assert 'Do not return\n"Take".' in prompt
    assert "daily loss" not in prompt.lower()
    assert "RTH/weekend session gates" in prompt


@pytest.mark.unit
def test_gatekeeper_prompt_reports_tradeable_state():
    llm = FakeLLM({TradeGoNoGo: _gonogo()})
    create_mes_gatekeeper_agent(llm)(
        checklist_markdown="checklist",
        market_context="context",
        tradeable=True,
    )
    assert "**TRADEABLE**" in llm.prompts[0]
    assert "**NOT TRADEABLE**" not in llm.prompts[0]


@pytest.mark.unit
def test_gatekeeper_optional_sections_are_conditional():
    llm = FakeLLM({TradeGoNoGo: _gonogo()})
    run = create_mes_gatekeeper_agent(llm)

    run(checklist_markdown="c", market_context="m", tradeable=True)
    bare = llm.prompts[0]
    assert "This Morning's Hypothesis" not in bare
    assert "Sizing Constraint" not in bare
    assert "Lessons From Prior Sessions" not in bare

    run(
        checklist_markdown="c",
        market_context="m",
        hypothesis="Trend up",
        past_context="Prior lesson",
        sizing_note="Max 2 contracts",
        tradeable=True,
    )
    full = llm.prompts[1]
    assert "This Morning's Hypothesis" in full
    assert "Max 2 contracts" in full
    assert "Prior lesson" in full


@pytest.mark.unit
def test_gatekeeper_falls_back_to_free_text():
    llm = FakeLLM(structured_error=ValueError("no tool call"), text="plain verdict")
    output = create_mes_gatekeeper_agent(llm)(
        checklist_markdown="c", market_context="m", tradeable=False
    )
    assert output == "plain verdict"
    assert "HARD CONSTRAINT" in llm.prompts[-1]


@pytest.mark.unit
def test_gatekeeper_prompt_includes_current_price():
    llm = FakeLLM({TradeGoNoGo: _gonogo()})
    create_mes_gatekeeper_agent(llm)(
        checklist_markdown="c",
        market_context="m",
        tradeable=True,
        current_price=7678.75,
    )
    assert "7678.75" in llm.prompts[0]


@pytest.mark.unit
def test_gatekeeper_prompt_includes_trade_levels_hint():
    llm = FakeLLM({TradeGoNoGo: _gonogo()})
    create_mes_gatekeeper_agent(llm)(
        checklist_markdown="c",
        market_context="m",
        tradeable=True,
        trade_levels_hint="- Entry zone: 5000.00-5000.25\n- Stop: 4990.0 (VWAP)\n- First target: 5010.0 (prior VAH)",
    )
    prompt = llm.prompts[0]
    assert "Suggested Trade Levels" in prompt
    assert "4990.0" in prompt
    assert "5010.0" in prompt


@pytest.mark.unit
def test_gatekeeper_prompt_contains_trade_level_rules():
    llm = FakeLLM({TradeGoNoGo: _gonogo()})
    create_mes_gatekeeper_agent(llm)(
        checklist_markdown="c",
        market_context="m",
        tradeable=True,
    )
    prompt = llm.prompts[0]
    assert "TRADE LEVEL RULES" in prompt
    assert "first_target" in prompt
    assert "strictly above" in prompt.lower()


@pytest.mark.unit
def test_gatekeeper_normalizes_wait_verdict_before_render():
    """A Wait verdict with populated trade levels must have them stripped by the gatekeeper."""
    inverted_wait = TradeGoNoGo(
        verdict=TradeVerdict.WAIT,
        direction="long",
        confidence="medium",
        reasoning="Marginal.",
        entry_zone="7678.75-7680.00",
        stop_level=7657.81,
        first_target=7673.75,  # inverted
        suggested_contracts=1,
        what_would_change_my_mind="Volume.",
    )
    llm = FakeLLM({TradeGoNoGo: inverted_wait})
    output = create_mes_gatekeeper_agent(llm)(
        checklist_markdown="c",
        market_context="m",
        tradeable=True,
    )
    assert "Entry Zone" not in output
    assert "Stop" not in output
    assert "First Target" not in output
    assert "**Direction**: None" in output


@pytest.mark.unit
def test_gatekeeper_normalizes_inverted_take_target():
    """A Take verdict with first_target below entry must have the target cleared."""
    inverted_take = TradeGoNoGo(
        verdict=TradeVerdict.TAKE,
        direction="long",
        confidence="medium",
        reasoning="Setup.",
        entry_zone="7678.75-7680.00",
        stop_level=7657.81,
        first_target=7673.75,  # below entry high — invalid for long
        suggested_contracts=1,
        what_would_change_my_mind="Break below VWAP.",
    )
    llm = FakeLLM({TradeGoNoGo: inverted_take})
    output = create_mes_gatekeeper_agent(llm)(
        checklist_markdown="c",
        market_context="m",
        tradeable=True,
    )
    assert "First Target" not in output
    assert "auto-corrected" in output


# ---------------------------------------------------------------------------
# Review agent
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_review_agent_renders_structured_review():
    llm = FakeLLM({SessionReview: _review()})
    output = create_mes_review_agent(llm)(
        hypothesis="Trend up",
        checks_summary="| Time | Side |",
        outcome_summary="-1 handle",
    )

    assert "**Hypothesis Grade**: Partial" in output
    assert "**Discipline Grade**: B" in output
    assert "**One Improvement**:" in output

    prompt = llm.prompts[0]
    assert "Trend up" in prompt
    assert "| Time | Side |" in prompt
    assert "-1 handle" in prompt


@pytest.mark.unit
def test_review_agent_falls_back_to_free_text():
    llm = FakeLLM(structured_error=RuntimeError("boom"), text="plain review")
    output = create_mes_review_agent(llm)(
        hypothesis="h", checks_summary="s", outcome_summary="o"
    )
    assert output == "plain review"


# ---------------------------------------------------------------------------
# Standing rules (review -> session loop)
# ---------------------------------------------------------------------------


def _rule(**overrides) -> StandingRule:
    trigger = {"kind": "level_retest", "level": "orb_top",
               "confirmation": "none", "tolerance_points": 2.0}
    payload = {"trigger": trigger, "requirement": "log_check_or_skip",
               "note": "Fade the first ORB-top retest.", "expires_on": "2026-03-31"}
    payload.update(overrides)
    return StandingRule(**payload)


@pytest.mark.unit
def test_standing_rule_defaults():
    rule = StandingRule(trigger={"kind": "level_retest", "level": "vwap"})
    assert rule.trigger.level is RuleLevel.VWAP
    assert rule.trigger.confirmation is Confirmation.NONE
    assert rule.trigger.tolerance_points == 2.0
    assert rule.requirement == "log_check_or_skip"
    assert rule.expires_on is None


@pytest.mark.unit
def test_standing_rule_rejects_unknown_level():
    with pytest.raises(ValidationError):
        StandingRule(trigger={"kind": "level_retest", "level": "round_100"})


_FLATTENED_RULE = {
    "kind": "level_retest",
    "level": "vwap",
    "confirmation": "add_vold_aligned",
    "tolerance_points": 2.0,
    "note": "Fade the VWAP retest.",
    "expires_on": "2026-09-25",
}


@pytest.mark.unit
def test_standing_rule_lifts_flattened_trigger_fields():
    """Regression: providers without grammar enforcement emit tool args flat.

    DeepSeek-style tool calling emitted ``confirmation``/``tolerance_points``
    (and the trigger's ``level``/``kind``) at the rule level instead of nested
    under ``trigger``, which failed the whole review. The schema must lift the
    flattened fields into a ``trigger`` object.
    """
    rule = StandingRule(**_FLATTENED_RULE)
    assert rule.trigger.kind == "level_retest"
    assert rule.trigger.level is RuleLevel.VWAP
    assert rule.trigger.confirmation is Confirmation.ADD_VOLD_ALIGNED
    assert rule.trigger.tolerance_points == 2.0
    assert rule.note == "Fade the VWAP retest."
    assert rule.expires_on == "2026-09-25"


@pytest.mark.unit
def test_session_review_parses_flattened_standing_rules():
    """The exact 2026-09-24 failure: flat rule dicts inside a SessionReview tool call."""
    review = SessionReview.model_validate(
        {
            "hypothesis_grade": "Partial",
            "discipline_grade": "B",
            "what_worked": "waited",
            "what_failed": "hesitated at onl",
            "one_improvement": "watch the onl retest",
            "standing_rules": [
                {
                    "level": "onl",
                    "confirmation": "add_vold_aligned",
                    "tolerance_points": 2.0,
                    "note": "Fade the ONL retest.",
                }
            ],
            "narrative": "Solid read.",
        }
    )
    (rule,) = review.standing_rules
    assert rule.trigger.level is RuleLevel.ONL
    assert rule.trigger.confirmation is Confirmation.ADD_VOLD_ALIGNED
    assert rule.trigger.tolerance_points == 2.0


@pytest.mark.unit
def test_standing_rule_accepts_trigger_as_bare_level_string():
    rule = StandingRule(trigger="vwap")
    assert rule.trigger.level is RuleLevel.VWAP
    assert rule.trigger.confirmation is Confirmation.NONE


@pytest.mark.unit
def test_standing_rule_lifted_fields_do_not_clobber_nested_trigger():
    rule = StandingRule(
        trigger={"kind": "level_retest", "level": "orb_top", "tolerance_points": 3.0},
        tolerance_points=1.0,
    )
    assert rule.trigger.level is RuleLevel.ORB_TOP
    assert rule.trigger.tolerance_points == 3.0  # nested value wins over lifted


@pytest.mark.unit
def test_standing_rule_still_rejects_unknown_level_when_flattened():
    with pytest.raises(ValidationError):
        StandingRule(**{**_FLATTENED_RULE, "level": "round_100"})


@pytest.mark.unit
def test_session_review_standing_rules_default_empty():
    assert _review().standing_rules == []


@pytest.mark.unit
def test_render_session_review_lists_standing_rules():
    review = _review()
    review.standing_rules = [_rule()]
    output = render_session_review(review)
    assert "**Standing Rules for the next session**:" in output
    assert "ORB high retest" in output
    assert "Fade the first ORB-top retest." in output


@pytest.mark.unit
def test_render_session_review_omits_rules_when_empty():
    assert "Standing Rules" not in render_session_review(_review())


@pytest.mark.unit
def test_render_session_review_omits_rules_when_empty():
    assert "Standing Rules" not in render_session_review(_review())


# ---------------------------------------------------------------------------
# Review agent standing-rules feedback (structured-output hook)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_invoke_structured_calls_on_model_with_parsed_result():
    review = _review()
    llm = FakeLLM({SessionReview: review})
    captured = []
    output = invoke_structured_or_freetext(
        llm.with_structured_output(SessionReview), llm, "prompt",
        render_session_review, "test", on_model=captured.append,
    )
    assert captured == [review]
    assert "**Hypothesis Grade**: Partial" in output


class _SequenceStructured:
    """Structured stub whose invoke returns a scripted sequence of outcomes.

    Each entry is either a result to return or an exception to raise; the
    last entry repeats for any further calls. Records every prompt.
    """

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls: list[str] = []

    def invoke(self, prompt):
        self.calls.append(prompt)
        outcome = self._outcomes.pop(0) if len(self._outcomes) > 1 else self._outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.unit
def test_invoke_structured_retries_once_when_first_call_returns_none():
    """Regression: weak OpenAI-compatible providers without forced tool_choice
    sometimes answer in prose, so the parser returns None and the review lost
    its standing rules to the free-text fallback. One retry parses on the
    second attempt; the fallback must not fire.
    """
    review = _review()
    structured = _SequenceStructured([None, review])
    llm = FakeLLM()
    captured = []
    output = invoke_structured_or_freetext(
        structured, llm, "prompt", render_session_review, "test",
        on_model=captured.append,
    )
    assert len(structured.calls) == 2
    assert captured == [review]
    assert "**Hypothesis Grade**: Partial" in output
    assert llm.prompts == []  # never degraded to free text


@pytest.mark.unit
def test_invoke_structured_retries_once_when_first_call_raises():
    """A transient parse/validation failure gets exactly one more structured
    attempt before the free-text fallback is considered."""
    review = _review()
    structured = _SequenceStructured([ValueError("structured output returned no parsed result"), review])
    llm = FakeLLM()
    output = invoke_structured_or_freetext(
        structured, llm, "prompt", render_session_review, "test",
    )
    assert len(structured.calls) == 2
    assert "**Hypothesis Grade**: Partial" in output
    assert llm.prompts == []


@pytest.mark.unit
def test_invoke_structured_falls_back_after_exhausting_structured_retry():
    """When both structured attempts fail, the free-text fallback still fires."""
    structured = _SequenceStructured([RuntimeError("boom")])
    llm = FakeLLM(text="plain review")
    output = invoke_structured_or_freetext(
        structured, llm, "prompt", render_session_review, "test",
    )
    assert len(structured.calls) == 2
    assert output == "plain review"


@pytest.mark.unit
def test_invoke_structured_single_attempt_on_success():
    """The happy path must not burn a second invocation."""
    review = _review()
    structured = _SequenceStructured([review])
    llm = FakeLLM()
    output = invoke_structured_or_freetext(
        structured, llm, "prompt", render_session_review, "test",
    )
    assert len(structured.calls) == 1
    assert llm.prompts == []
    assert "**Discipline Grade**: B" in output



@pytest.mark.unit
def test_review_agent_prompt_includes_current_standing_rules():
    llm = FakeLLM()
    create_mes_review_agent(llm)(
        hypothesis="h", checks_summary="c", outcome_summary="o",
        standing_rules_summary=(
            "1 triggered / 0 checked / 0 skipped / 1 MISSED"
        ),
    )
    prompt = llm.prompts[0]
    assert "Standing Rules" in prompt
    assert "MISSED" in prompt


@pytest.mark.unit
def test_review_agent_prompt_shows_nested_rule_shape():
    """Weak providers bind the schema without grammar enforcement, so the
    prompt itself must show the exact nested standing-rule shape."""
    llm = FakeLLM()
    create_mes_review_agent(llm)(hypothesis="h", checks_summary="c", outcome_summary="o")
    prompt = llm.prompts[0]
    assert '"standing_rules"' in prompt
    assert '"trigger"' in prompt
    assert '"level_retest"' in prompt
    assert '"confirmation"' in prompt
    assert '"tolerance_points"' in prompt


@pytest.mark.unit
def test_review_agent_prompt_lists_only_schema_valid_levels():
    """Every level the prompt advertises must be a valid RuleLevel.

    The prompt advertised 'prior_poc' while the schema only knows 'poc', so
    the model faithfully echoed an invalid level and the whole review's parse
    failed (observed 2026-09-24). The prompt may not advertise a level the
    schema rejects.
    """
    llm = FakeLLM()
    create_mes_review_agent(llm)(hypothesis="h", checks_summary="c", outcome_summary="o")
    prompt = llm.prompts[0]
    advertised = prompt.split("Rules must be retests of one of:")[1].split(".")[0]
    levels = {token.strip() for token in advertised.replace("\n", " ").split(",")}
    assert levels
    for level in levels:
        RuleLevel(level)  # raises ValidationError for a level the schema rejects


@pytest.mark.unit
def test_review_agent_forwards_on_review_capture():
    llm = FakeLLM({SessionReview: _review()})
    captured = []
    create_mes_review_agent(llm)(
        hypothesis="h", checks_summary="c", outcome_summary="o", on_review=captured.append,
    )
    assert len(captured) == 1 and captured[0].discipline_grade == "B"


# ---------------------------------------------------------------------------
# Trade manager agent (advisory only)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_manager_agent_renders_prompt_with_report():
    llm = FakeLLM(text="Trend intact; $TICK still positive. Hold.")
    agent = create_mes_manager_agent(llm)
    agent(
        mgmt_summary="LONG 1 @ 100.00 | +0.46R | stop 98.00 | next: BE at +1.0R",
        market_context="SPY above VWAP, $ADD +1200",
        hypothesis="Trend-up day; SPY holding VWAP.",
        current_price=100.50,
    )
    assert llm.prompts, "manager must receive a prompt"
    assert "100.00" in llm.prompts[0]
    assert "SPY above VWAP" in llm.prompts[0]


@pytest.mark.unit
def test_manager_output_is_returned_verbatim():
    llm = FakeLLM(text="Internals flipped; consider tightening.")
    agent = create_mes_manager_agent(llm)
    text = agent(mgmt_summary="HOLD | +0.5R | stop 98.00", market_context="SPY above VWAP")
    assert text == "Internals flipped; consider tightening."


@pytest.mark.unit
def test_review_agent_accepts_trades_summary():
    llm = FakeLLM()
    agent = create_mes_review_agent(llm)
    agent(hypothesis="h", checks_summary="c", outcome_summary="o",
          trades_summary="| long | 6500.00 | 6503.50 | manual | +1.75 |")
    assert "Trades Taken" in llm.prompts[0]


@pytest.mark.unit
def test_review_agent_prompt_includes_journaled_flips():
    """A journaled flip must reach the coach: the morning frame was superseded."""
    llm = FakeLLM()
    create_mes_review_agent(llm)(
        hypothesis="h", checks_summary="c", outcome_summary="o",
        flips_summary="| 14:09 | Range | VWAP lost and held |",
    )
    prompt = llm.prompts[0]
    assert "Intraday Thesis Flips" in prompt
    assert "14:09" in prompt
    assert "supersedes" in prompt.lower()


@pytest.mark.unit
def test_review_agent_prompt_grades_an_unjournaled_flip_as_discipline_failure():
    """An invalidation visible in the checks but never journaled is a discipline
    failure the coach must name — the afternoon of 2/9–4/9 checked against a
    dead Trend Down frame because the flip was never journaled."""
    llm = FakeLLM()
    create_mes_review_agent(llm)(hypothesis="h", checks_summary="c", outcome_summary="o")
    prompt = llm.prompts[0]
    assert "flip" in prompt.lower()
    assert "discipline failure" in prompt.lower()


@pytest.mark.unit
def test_review_agent_prompt_omits_flip_section_without_flips():
    llm = FakeLLM()
    create_mes_review_agent(llm)(hypothesis="h", checks_summary="c", outcome_summary="o")
    prompt = llm.prompts[0]
    assert "Intraday Thesis Flips" not in prompt
