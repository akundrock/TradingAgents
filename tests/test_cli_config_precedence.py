"""CLI config precedence (#976, #977).

An explicit environment override for the debate/risk round counts, or the
checkpoint flag, must win over the interactive research-depth selection — the CLI
must not clobber an env-configured value back to a prompt/flag default.
"""

from unittest import mock

import pytest

import cli.main as m

# Minimal selections dict shaped like get_user_selections()'s return value.
SELECTIONS = {
    "research_depth": 5,
    "shallow_thinker": "gpt-5.4-mini",
    "deep_thinker": "gpt-5.5",
    "backend_url": None,
    "llm_provider": "openai",
    "google_thinking_level": None,
    "openai_reasoning_effort": None,
    "anthropic_effort": None,
    "output_language": "English",
}


def test_research_depth_sets_both_rounds_without_env(monkeypatch):
    for var in ("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "TRADINGAGENTS_MAX_RISK_ROUNDS"):
        monkeypatch.delenv(var, raising=False)
    cfg = m._build_run_config(SELECTIONS, checkpoint=None)
    assert cfg["max_debate_rounds"] == 5
    assert cfg["max_risk_discuss_rounds"] == 5


def test_env_round_counts_win_over_selection(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "2")
    monkeypatch.setenv("TRADINGAGENTS_MAX_RISK_ROUNDS", "4")
    # DEFAULT_CONFIG already reflects the env (applied at import); emulate that.
    patched = dict(m.DEFAULT_CONFIG, max_debate_rounds=2, max_risk_discuss_rounds=4)
    with mock.patch.object(m, "DEFAULT_CONFIG", patched):
        cfg = m._build_run_config(SELECTIONS, checkpoint=None)
    assert cfg["max_debate_rounds"] == 2  # env value, not research_depth=5
    assert cfg["max_risk_discuss_rounds"] == 4


def test_partial_env_only_overrides_that_count(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "2")
    monkeypatch.delenv("TRADINGAGENTS_MAX_RISK_ROUNDS", raising=False)
    patched = dict(m.DEFAULT_CONFIG, max_debate_rounds=2)
    with mock.patch.object(m, "DEFAULT_CONFIG", patched):
        cfg = m._build_run_config(SELECTIONS, checkpoint=None)
    assert cfg["max_debate_rounds"] == 2  # env wins
    assert cfg["max_risk_discuss_rounds"] == 5  # falls through to research_depth


def test_checkpoint_none_preserves_env_default():
    patched = dict(m.DEFAULT_CONFIG, checkpoint_enabled=True)  # e.g. env-enabled
    with mock.patch.object(m, "DEFAULT_CONFIG", patched):
        cfg = m._build_run_config(SELECTIONS, checkpoint=None)
    assert cfg["checkpoint_enabled"] is True  # not clobbered back to False


def test_intraday_flags_override_run_config():
    cfg = m._build_run_config(
        SELECTIONS,
        checkpoint=None,
        intraday=True,
        intraday_interval_minutes=3,
        intraday_max_cycles=7,
        intraday_fast_path=True,
    )
    assert cfg["magpie_intraday_loop_enabled"] is True
    assert cfg["magpie_intraday_loop_interval_minutes"] == 3
    assert cfg["magpie_intraday_loop_max_cycles"] == 7
    assert cfg["magpie_intraday_fast_path_enabled"] is True


@pytest.mark.parametrize("flag", [True, False])
def test_checkpoint_flag_overrides_env(flag):
    patched = dict(m.DEFAULT_CONFIG, checkpoint_enabled=not flag)
    with mock.patch.object(m, "DEFAULT_CONFIG", patched):
        cfg = m._build_run_config(SELECTIONS, checkpoint=flag)
    assert cfg["checkpoint_enabled"] is flag


@pytest.mark.parametrize(
    "direction,expected",
    [("Buy", 1), ("Sell", -1), ("Hold", 0), ("Unavailable", 0)],
)
def test_direction_to_int(direction, expected):
    assert m._direction_to_int(direction) == expected


def test_seed_and_update_magpie_session_state_roundtrip():
    init_state = {}
    session_state = {
        "trade_date": "2026-07-06",
        "magpie_previous_direction": 1,
        "magpie_previous_long_entry": True,
        "magpie_previous_short_entry": False,
    }
    m._seed_magpie_session_state(init_state, session_state)
    assert init_state["magpie_previous_direction"] == 1
    assert init_state["magpie_previous_long_entry"] is True
    assert init_state["magpie_previous_short_entry"] is False

    final_state = {
        "magpie_signal": {
            "direction": "Sell",
            "long_entry": False,
            "short_entry": True,
        }
    }
    m._update_magpie_session_state(session_state, final_state, "2026-07-06")
    assert session_state["magpie_previous_direction"] == -1
    assert session_state["magpie_previous_long_entry"] is False
    assert session_state["magpie_previous_short_entry"] is True


def test_update_magpie_session_state_resets_on_trade_date_change():
    session_state = {
        "trade_date": "2026-07-05",
        "magpie_previous_direction": 1,
        "magpie_previous_long_entry": True,
        "magpie_previous_short_entry": False,
    }
    m._update_magpie_session_state(
        session_state,
        {"magpie_signal": {"direction": "Hold", "long_entry": False, "short_entry": False}},
        "2026-07-06",
    )
    assert session_state["trade_date"] == "2026-07-06"
    assert session_state["magpie_previous_direction"] == 0
