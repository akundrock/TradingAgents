"""MES intraday trade copilot: deterministic checklist, sizing, and session journal.

The hard thresholds from the Alpha Zone Pro dual-chart workflow live here in code so
they stay auditable and testable; the LLM agents in ``tradingagents.agents.mes`` only
add judgement on top of an already-decided :class:`ChecklistResult`.

``$TICK`` / ``$VOLD`` scoring follows the Thinkorswim internals dashboard
(dynamic tick width, persistent streaks, bar-over-bar VOLD vs SPY). See
:mod:`tradingagents.mes.internals`.
"""

from .checklist import (
    CheckItem,
    ChecklistResult,
    detect_divergence,
    evaluate,
    max_achievable_score,
    required_confirmations,
    score_tier,
    tier_contract_band,
)
from .config import MesChecklistConfig, load_mes_config
from .config import MesChecklistConfig, load_mes_config
from .journal import MesJournal
from .levels import (
    TradeLevels,
    normalize_trade_gonogo,
    render_trade_levels_hint,
    suggest_trade_levels,
    suggest_trade_levels_from_snapshot,
)
from .management import MgmtEvent, MgmtReport, OpenTrade, evaluate_management
from .profile import VolumeProfile, volume_profile
from .radar import LevelDistance, ProximityReport, SetupState, build_proximity
from .render import render_checklist, render_market_context
from .sizing import SizingResult, size_position, suggest_stop_points
from .snapshot import (
    Bar,
    MesSnapshot,
    SeriesState,
    SessionLevels,
    build_snapshot,
    overnight_range,
    prior_session_levels,
    session_bounds,
    snapshot_from_csv,
)

__all__ = [
    "Bar",
    "CheckItem",
    "ChecklistResult",
    "LevelDistance",
    "MesChecklistConfig",
    "MesJournal",
    "MesSnapshot",
    "MgmtEvent",
    "MgmtReport",
    "OpenTrade",
    "ProximityReport",
    "SeriesState",
    "SessionLevels",
    "SetupState",
    "SizingResult",
    "TradeLevels",
    "VolumeProfile",
    "build_proximity",
    "build_snapshot",
    "detect_divergence",
    "evaluate",
    "evaluate_management",
    "load_mes_config",
    "max_achievable_score",
    "normalize_trade_gonogo",
    "overnight_range",
    "prior_session_levels",
    "render_checklist",
    "render_market_context",
    "render_trade_levels_hint",
    "required_confirmations",
    "score_tier",
    "session_bounds",
    "size_position",
    "snapshot_from_csv",
    "suggest_stop_points",
    "suggest_trade_levels",
    "suggest_trade_levels_from_snapshot",
    "tier_contract_band",
    "volume_profile",
]
