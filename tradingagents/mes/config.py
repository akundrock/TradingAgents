"""Thresholds for the MES intraday checklist.

Defaults mirror ``mes-tuner``'s ``StrategyConfig`` (which is itself parity-tested
against ``MES_EntryScoreEngine.tos``) so scores produced here can be diffed
against the tuner's journal for the same bar. Times are US/Eastern wall clock,
matching the Schwab candle timestamps this package consumes.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, fields
from typing import Any

# Maps mes-tuner / ThinkScript PascalCase input names onto our snake_case fields.
_TUNER_FIELD_ALIASES: dict[str, str] = {
    "EnableMomentum": "enable_momentum",
    "EnableVWAP": "enable_vwap",
    "EnableATRRange": "enable_atr_range",
    "EnablePattern": "enable_pattern",
    "EnableADD": "enable_add",
    "EnableTICK": "enable_tick",
    "EnableVOLD": "enable_vold",
    "EnableVolumeSurge": "enable_volume_surge",
    "SMALength": "sma_length",
    "NFE": "nfe",
    "ATRLength": "atr_length",
    "ATRMultiplier": "atr_multiplier",
    "VolumeLookback": "volume_lookback",
    "VolumeMultiplier": "volume_multiplier",
    "WickToBodyRatio": "wick_to_body_ratio",
    "MaxBodyToRangeRatio": "max_body_to_range_ratio",
    "RetracementPercent": "retracement_percent",
    "PatternRequireVWAPSide": "pattern_require_vwap_side",
    "AddThreshold": "add_threshold",
    "TickThreshold": "tick_threshold",
    "UseDynamicThreshold": "use_dynamic_tick_threshold",
    "TickLookback": "tick_lookback",
    "TickMultiplier": "tick_multiplier",
    "StreakBars": "tick_persistent_bars",
    "TickBurstMultiplier": "tick_burst_multiplier",
    "VOLDThreshold": "vold_threshold",
    "VOLDUseTrend": "vold_use_trend",
    "VOLDZScoreLookback": "vold_zscore_lookback",
    "MinConfirmations": "min_confirmations",
    "EnableTierMinConfirmations": "enable_tier_min_confirmations",
    "MinConfirmationsMarginal": "min_confirmations_marginal",
    "MinConfirmationsStandard": "min_confirmations_standard",
    "MinConfirmationsPremium": "min_confirmations_premium",
    "MomentumScoreWeight": "momentum_score_weight",
    "VWAPScoreWeight": "vwap_score_weight",
    "ATRRangeScoreWeight": "atr_range_score_weight",
    "PatternScoreWeight": "pattern_score_weight",
    "ADDScoreWeight": "add_score_weight",
    "TICKScoreWeight": "tick_score_weight",
    "VOLDScoreWeight": "vold_score_weight",
    "VolumeSurgeScoreWeight": "volume_surge_score_weight",
    "MinATRPoints": "min_atr_points",
    "RequireRTH": "require_rth",
    "EnforceSessionFilters": "enforce_session_filters",
    "BlockMorningUntil": "block_morning_until",
    "AllowORBWindow": "allow_orb_window",
    "AllowMissingInternals": "allow_missing_internals",
}


@dataclass
class MesChecklistConfig:
    """Every threshold the deterministic checklist evaluates."""

    enable_momentum: bool = True
    enable_vwap: bool = True
    enable_atr_range: bool = True
    enable_pattern: bool = True
    enable_add: bool = True
    enable_tick: bool = True
    enable_vold: bool = True
    enable_volume_surge: bool = True

    sma_length: int = 5
    nfe: int = 13
    atr_length: int = 14
    atr_multiplier: float = 2.0
    volume_lookback: int = 20
    volume_multiplier: float = 1.2

    wick_to_body_ratio: float = 1.2
    max_body_to_range_ratio: float = 0.6
    retracement_percent: float = 40.0
    pattern_require_vwap_side: bool = False

    # Engine-level internals thresholds (mes-tuner parity when dynamic is off).
    add_threshold: float = 250.0
    tick_threshold: float = 600.0
    vold_threshold: float = 0.0
    vold_use_trend: bool = True

    # TOS internals-dashboard parity (MES_TICK_Panel / MES_VOLD_Histogram).
    use_dynamic_tick_threshold: bool = True
    tick_lookback: int = 20
    tick_multiplier: float = 1.5
    tick_persistent_bars: int = 3
    tick_burst_multiplier: float = 1.5
    vold_zscore_lookback: int = 20

    # Discretionary SPY-context thresholds from azp-dual-chart-workflow.md.
    add_trend_threshold: float = 1000.0
    add_chop_threshold: float = 500.0
    tick_extreme_threshold: float = 1000.0
    tick_sustain_bars: int = 3
    vold_slope_bars: int = 6

    min_confirmations: int = 4
    enable_tier_min_confirmations: bool = True
    min_confirmations_marginal: int = 3
    min_confirmations_standard: int = 4
    min_confirmations_premium: int = 5

    momentum_score_weight: int = 2
    vwap_score_weight: int = 1
    atr_range_score_weight: int = 1
    pattern_score_weight: int = 1
    add_score_weight: int = 1
    tick_score_weight: int = 1
    vold_score_weight: int = 1
    volume_surge_score_weight: int = 1

    min_atr_points: float = 0.0

    require_rth: bool = True
    enforce_session_filters: bool = True
    rth_start: str = "09:30"
    rth_end: str = "16:00"
    block_morning_until: str = "09:45"
    allow_orb_window: bool = False
    opening_range_end: str = "09:45"
    last_entry_time: str = "15:30"
    exit_time: str = "15:50"
    allow_missing_internals: bool = False

    # Execution / sizing.
    contract_multiplier: float = 5.0
    default_risk_dollars: float = 200.0
    max_contracts: int = 10

    spy_symbol: str = "SPY"
    mes_symbol: str = "/MES"
    session_timezone: str = "America/New_York"

    # Price increments used to bucket the prior-session volume profile.
    mes_tick_size: float = 0.25
    spy_tick_size: float = 0.01

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MesChecklistConfig:
        known = {f.name for f in fields(cls)}
        resolved: dict[str, Any] = {}
        for key, value in data.items():
            name = _TUNER_FIELD_ALIASES.get(key, key)
            if name in known:
                resolved[name] = value
        return cls(**resolved)


def _coerce(raw: str, current: Any) -> Any:
    if isinstance(current, bool):
        return raw.strip().lower() in {"1", "true", "yes", "y", "on"}
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    return raw


def load_mes_config(overrides: dict[str, Any] | None = None) -> MesChecklistConfig:
    """Build config from defaults, then ``TRADINGAGENTS_MES_*`` env vars, then overrides."""
    cfg = MesChecklistConfig()
    for field_def in fields(cfg):
        raw = os.getenv(f"TRADINGAGENTS_MES_{field_def.name.upper()}")
        if raw is None or not raw.strip():
            continue
        setattr(cfg, field_def.name, _coerce(raw, getattr(cfg, field_def.name)))
    if overrides:
        for key, value in overrides.items():
            name = _TUNER_FIELD_ALIASES.get(key, key)
            if hasattr(cfg, name):
                setattr(cfg, name, value)
    return cfg
