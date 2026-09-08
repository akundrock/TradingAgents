from types import SimpleNamespace

import pytest

from tradingagents.agents.mes import (
    create_mes_gatekeeper_agent,
    create_mes_manager_agent,
    create_mes_morning_agent,
    create_mes_review_agent,
)
from tradingagents.agents.schemas import (
    DayType,
    HypothesisGrade,
    MarketBias,
    MorningHypothesis,
    SessionReview,
    TradeGoNoGo,
    TradeVerdict,
)


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
    # Structured attempt, then the free-text retry with the same prompt.
    assert len(llm.prompts) == 2
    assert llm.prompts[0] == llm.prompts[1]


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
