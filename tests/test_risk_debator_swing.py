from unittest.mock import MagicMock, patch

import pytest

from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.agents.risk_mgmt.aggressive_debator import create_aggressive_debator
from tradingagents.agents.risk_mgmt.conservative_debator import create_conservative_debator
from tradingagents.agents.risk_mgmt.neutral_debator import create_neutral_debator


def _risk_state(profile_block: str | None) -> dict:
    intraday_context = {}
    if profile_block:
        intraday_context["trade_profile"] = {"enabled": True, "block": profile_block}
    return {
        "risk_debate_state": {
            "history": "",
            "aggressive_history": "",
            "conservative_history": "",
            "neutral_history": "",
            "count": 0,
        },
        "instrument_context": "MES futures context",
        "market_report": "m",
        "sentiment_report": "s",
        "news_report": "n",
        "fundamentals_report": "f",
        "trader_investment_plan": "buy NVDA",
        "intraday_context": intraday_context,
    }


class _RecordingLLM:
    def __init__(self):
        self.prompts: list[str] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return MagicMock(content="ok")


@pytest.mark.unit
@pytest.mark.parametrize(
    "factory",
    [create_aggressive_debator, create_conservative_debator, create_neutral_debator],
)
def test_debator_prompt_includes_profile_when_present(factory):
    llm = _RecordingLLM()
    node = factory(llm)
    node(_risk_state("SWING-PROFILE-BLOCK"))
    assert "SWING-PROFILE-BLOCK" in llm.prompts[0]
    assert "overnight gap risk" in llm.prompts[0]
    # Role line must not contradict env-tunable profile numbers.
    assert "0.70" not in llm.prompts[0]
    assert "~1 day" not in llm.prompts[0]


@pytest.mark.unit
@pytest.mark.parametrize(
    "factory",
    [create_aggressive_debator, create_conservative_debator, create_neutral_debator],
)
def test_debator_prompt_unchanged_without_profile(factory):
    llm = _RecordingLLM()
    node = factory(llm)
    node(_risk_state(None))
    assert "Trade Profile" not in llm.prompts[0]


@pytest.mark.unit
def test_pm_prompt_includes_profile_when_present():
    with patch(
        "tradingagents.agents.managers.portfolio_manager.invoke_structured_or_freetext"
    ) as mock_invoke:
        mock_invoke.return_value = "PM decision"
        node = create_portfolio_manager(MagicMock())
        # PM node reads every key of risk_debate_state and "past_context".
        state = {
            "risk_debate_state": {
                "history": "debate",
                "aggressive_history": "",
                "conservative_history": "",
                "neutral_history": "",
                "current_aggressive_response": "",
                "current_conservative_response": "",
                "current_neutral_response": "",
                "count": 0,
            },
            "instrument_context": "MES futures context",
            "investment_plan": "plan",
            "trader_investment_plan": "buy",
            "past_context": "",
            "intraday_context": {
                "trade_profile": {"enabled": True, "block": "PM-PROFILE-BLOCK"},
            },
        }
        node(state)

    prompt_arg = _pm_prompt_arg(mock_invoke)
    assert "PM-PROFILE-BLOCK" in prompt_arg
    assert "swing thesis" in prompt_arg


@pytest.mark.unit
def test_pm_prompt_without_profile_has_no_block():
    with patch(
        "tradingagents.agents.managers.portfolio_manager.invoke_structured_or_freetext"
    ) as mock_invoke:
        mock_invoke.return_value = "PM decision"
        node = create_portfolio_manager(MagicMock())
        state = {
            "risk_debate_state": {
                "history": "debate",
                "aggressive_history": "",
                "conservative_history": "",
                "neutral_history": "",
                "current_aggressive_response": "",
                "current_conservative_response": "",
                "current_neutral_response": "",
                "count": 0,
            },
            "instrument_context": "MES futures context",
            "investment_plan": "plan",
            "trader_investment_plan": "buy",
            "past_context": "",
            "intraday_context": {},
        }
        node(state)

    prompt_arg = _pm_prompt_arg(mock_invoke)
    assert "PM-PROFILE-BLOCK" not in prompt_arg


# helpers


def _pm_prompt_arg(mock_invoke) -> str:
    """The PM passes a plain string prompt (not a messages list)."""
    args = mock_invoke.call_args[0]
    candidate = args[2] if len(args) > 2 else args[-1]
    assert isinstance(candidate, str)
    return candidate
