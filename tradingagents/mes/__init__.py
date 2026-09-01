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
from .journal import MesJournal
from .profile import VolumeProfile, volume_profile
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
    "MesChecklistConfig",
    "MesJournal",
    "MesSnapshot",
    "SeriesState",
    "SessionLevels",
    "SizingResult",
    "VolumeProfile",
    "build_snapshot",
    "detect_divergence",
    "evaluate",
    "load_mes_config",
    "max_achievable_score",
    "overnight_range",
    "prior_session_levels",
    "render_checklist",
    "render_market_context",
    "required_confirmations",
    "score_tier",
    "session_bounds",
    "size_position",
    "snapshot_from_csv",
    "suggest_stop_points",
    "tier_contract_band",
    "volume_profile",
]
