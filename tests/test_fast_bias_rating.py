"""Tests for the Fast Bias Rating node (single-call debate substitute)."""

from unittest.mock import MagicMock

import pytest

from tradingagents.agents.managers.fast_bias_rating import create_fast_bias_rating
from tradingagents.agents.schemas import PortfolioRating, ResearchPlan


def _make_state(**overrides) -> dict:
    state = {
        "company_of_interest": "NVDA",
        "market_report": "Price above VWAP; RSI 62; uptrend on 30m.",
        "sentiment_report": "",
        "news_report": "",
        "fundamentals_report": "",
        "investment_debate_state": {
            "history": "",
            "bull_history": "",
            "bear_history": "",
            "current_response": "",
            "judge_decision": "",
            "count": 0,
        },
    }
    state.update(overrides)
    return state


def _structured_llm(captured: dict, plan: ResearchPlan | None = None):
    if plan is None:
        plan = ResearchPlan(
            recommendation=PortfolioRating.OVERWEIGHT,
            rationale="Momentum and trend both constructive.",
            strategic_actions="Take a long entry with tight risk.",
        )
    structured = MagicMock()
    structured.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt) or plan
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm


@pytest.mark.unit
class TestFastBiasRatingAgent:
    def test_structured_path_produces_rendered_markdown(self):
        captured = {}
        llm = _structured_llm(captured)
        node = create_fast_bias_rating(llm)
        result = node(_make_state())
        assert "**Recommendation**: Overweight" in result["investment_plan"]

    def test_only_populated_reports_are_included_in_prompt(self):
        captured = {}
        llm = _structured_llm(captured)
        node = create_fast_bias_rating(llm)
        node(_make_state())
        prompt = captured["prompt"]
        assert "Market analysis" in prompt
        assert "Social media sentiment" not in prompt
        assert "News" not in prompt
        assert "Fundamentals" not in prompt

    def test_prompt_uses_5_tier_rating_scale(self):
        captured = {}
        llm = _structured_llm(captured)
        node = create_fast_bias_rating(llm)
        node(_make_state())
        prompt = captured["prompt"]
        for tier in ("Buy", "Overweight", "Hold", "Underweight", "Sell"):
            assert f"**{tier}**" in prompt, f"missing {tier} in prompt"

    def test_debate_history_left_empty(self):
        """No debate ran, so bull/bear history must stay empty (not fabricated)."""
        captured = {}
        llm = _structured_llm(captured)
        node = create_fast_bias_rating(llm)
        result = node(_make_state())
        debate_state = result["investment_debate_state"]
        assert debate_state["history"] == ""
        assert debate_state["bull_history"] == ""
        assert debate_state["bear_history"] == ""

    def test_falls_back_to_freetext_when_structured_unavailable(self):
        plain_response = "**Recommendation**: Sell\n\n**Rationale**: ...\n\n**Strategic Actions**: ..."
        llm = MagicMock()
        llm.with_structured_output.side_effect = NotImplementedError("provider unsupported")
        llm.invoke.return_value = MagicMock(content=plain_response)
        node = create_fast_bias_rating(llm)
        result = node(_make_state())
        assert result["investment_plan"] == plain_response
