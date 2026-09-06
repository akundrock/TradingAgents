import pytest

from tradingagents.mes.config import MesChecklistConfig, load_mes_config


@pytest.mark.unit
def test_defaults_match_documented_thresholds():
    cfg = load_mes_config()
    assert cfg.add_threshold == 250.0
    assert cfg.tick_threshold == 600.0
    assert cfg.use_dynamic_tick_threshold is True
    assert cfg.tick_lookback == 20
    assert cfg.tick_multiplier == 1.5
    assert cfg.tick_persistent_bars == 3
    assert cfg.tick_burst_multiplier == 1.5
    assert cfg.vold_zscore_lookback == 20
    assert cfg.vold_threshold == 0.0
    assert cfg.vold_use_trend is True
    assert cfg.add_trend_threshold == 1000.0
    assert cfg.add_chop_threshold == 500.0
    assert cfg.tick_extreme_threshold == 1000.0
    assert cfg.tick_sustain_bars == 3
    assert cfg.vold_slope_bars == 6
    assert (cfg.atr_multiplier, cfg.atr_length, cfg.sma_length, cfg.nfe) == (2.0, 14, 5, 13)
    assert (cfg.volume_lookback, cfg.volume_multiplier) == (20, 1.2)
    assert cfg.min_confirmations == 4
    assert cfg.enable_tier_min_confirmations is True
    assert (
        cfg.min_confirmations_marginal,
        cfg.min_confirmations_standard,
        cfg.min_confirmations_premium,
    ) == (3, 4, 5)
    assert cfg.momentum_score_weight == 2
    assert cfg.vwap_score_weight == cfg.atr_range_score_weight == 1
    assert cfg.pattern_score_weight == cfg.add_score_weight == 1
    assert cfg.tick_score_weight == cfg.vold_score_weight == 1
    assert cfg.volume_surge_score_weight == 1
    assert (cfg.rth_start, cfg.rth_end) == ("09:30", "16:00")
    assert cfg.block_morning_until == "09:45"
    assert cfg.allow_orb_window is False
    assert (cfg.last_entry_time, cfg.exit_time) == ("15:30", "15:50")
    assert cfg.contract_multiplier == 5.0
    assert cfg.default_risk_dollars == 200.0
    assert cfg.max_contracts == 10
    assert (cfg.spy_symbol, cfg.mes_symbol) == ("SPY", "/MES")
    assert cfg.session_timezone == "America/New_York"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        ("add_threshold", "400.5", 400.5),
        ("tick_sustain_bars", "5", 5),
        ("max_contracts", "3", 3),
        ("vold_use_trend", "false", False),
        ("vold_use_trend", "0", False),
        ("allow_orb_window", "yes", True),
        ("allow_orb_window", "TRUE", True),
        ("rth_start", "08:00", "08:00"),
    ],
)
def test_env_overrides_are_coerced_to_field_type(monkeypatch, field, raw, expected):
    monkeypatch.setenv(f"TRADINGAGENTS_MES_{field.upper()}", raw)
    cfg = load_mes_config()
    assert getattr(cfg, field) == expected
    assert type(getattr(cfg, field)) is type(expected)


@pytest.mark.unit
def test_blank_env_var_is_ignored(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_MES_ADD_THRESHOLD", "   ")
    assert load_mes_config().add_threshold == 250.0


@pytest.mark.unit
def test_overrides_win_over_env(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_MES_ADD_THRESHOLD", "400")
    cfg = load_mes_config({"add_threshold": 123.0})
    assert cfg.add_threshold == 123.0


@pytest.mark.unit
def test_unknown_override_keys_are_ignored():
    cfg = load_mes_config({"not_a_real_field": 1})
    assert not hasattr(cfg, "not_a_real_field")


@pytest.mark.unit
def test_tuner_alias_override_maps_to_snake_case_field():
    cfg = load_mes_config({"AddThreshold": 900.0, "EnableVOLD": False})
    assert cfg.add_threshold == 900.0
    assert cfg.enable_vold is False


@pytest.mark.unit
def test_to_dict_from_dict_round_trip():
    cfg = load_mes_config({"add_threshold": 321.0, "max_contracts": 4, "allow_orb_window": True})
    restored = MesChecklistConfig.from_dict(cfg.to_dict())
    assert restored == cfg


@pytest.mark.unit
def test_from_dict_ignores_unknown_keys():
    cfg = MesChecklistConfig.from_dict({"add_threshold": 10.0, "bogus": "x"})
    assert cfg.add_threshold == 10.0


@pytest.mark.unit
def test_management_ladder_defaults():
    cfg = MesChecklistConfig()
    assert cfg.breakeven_at_r == 1.0
    assert cfg.breakeven_cushion_ticks == 1.0
    assert cfg.partial_at_r == 1.5
    assert cfg.partial_fraction == 0.5
    assert cfg.trail_atr_multiple == 1.0
    assert cfg.exit_on_confluence_loss is False
    assert cfg.time_stop_buffer_minutes == 10


@pytest.mark.unit
def test_management_ladder_env_overrides(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_MES_BREAKEVEN_AT_R", "0.8")
    monkeypatch.setenv("TRADINGAGENTS_MES_PARTIAL_FRACTION", "0.3")
    cfg = load_mes_config()
    assert cfg.breakeven_at_r == 0.8
    assert cfg.partial_fraction == 0.3  # env override applied over the 0.5 default
