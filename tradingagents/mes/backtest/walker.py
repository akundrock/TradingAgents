"""Point-in-time session walker (AZP Phase 2, W2.1).

Loads a recorded session once and walks it forward bar by bar, evaluating the
real checklist at every MES bar close exactly the way ``mes check`` would have
in live trading:

* **Clock** — MES bars drive the replay. Bar ``i`` is evaluated at
  ``as_of = mes_bars[i].timestamp``; both series are sliced to ``<= as_of``
  (the same filter :func:`snapshot_from_csv` applies), so nothing from the
  future can reach the checklist.
* **No re-implementation** — every bar goes through the production
  :func:`~tradingagents.mes.checklist.evaluate` and
  :func:`~tradingagents.mes.radar.build_proximity` on a snapshot built by
  :func:`tradingagents.mes.snapshot.snapshot_from_bars`; the walker only
  records what they returned.
* **Cost** — ``O(bars^2 / 2)`` snapshot replays per session (~174 bars means
  ~15k bar-steps). Fine for one recorded session; chunk the corpus.
* **Skipped bars** — a bar before either series has any data cannot be
  evaluated (the live check would error the same way); the walker counts it
  as skipped instead of fabricating a verdict.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..checklist import ChecklistResult, evaluate
from ..config import MesChecklistConfig
from ..levels import TradeLevels, suggest_trade_levels_from_snapshot
from ..radar import ProximityReport, SetupState, build_proximity
from ..snapshot import Bar, load_csv_bars, snapshot_from_bars

from .ablations import BASELINE_NAME, config_fingerprint as fingerprint_config

__all__ = [
    "ReplayRecord",
    "ReplaySession",
    "SessionCandidate",
    "WalkResult",
    "discover_sessions",
    "load_session",
]

_MES_FILE = re.compile(r"^mes_(\d{4}-\d{2}-\d{2})\.csv$")
DEFAULT_MIN_BARS = 30


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------


@dataclass
class SessionCandidate:
    """One discovered ``mes_YYYY-MM-DD.csv`` and its same-date SPY recording."""

    date: str
    mes_path: Path
    spy_path: Path | None = None
    mes_bars: int = 0
    spy_bars: int = 0
    excluded: str | None = None
    """Why the session cannot be walked (``None`` when walkable). Never dropped silently."""


def _count_data_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def discover_sessions(
    data_dir: str | Path,
    *,
    start: str | None = None,
    end: str | None = None,
    min_bars: int = DEFAULT_MIN_BARS,
) -> list[SessionCandidate]:
    """Pair ``mes_YYYY-MM-DD.csv`` recordings with their same-date SPY CSVs.

    ``start`` / ``end`` are ``YYYY-MM-DD`` bounds on the session date. Sessions
    are excluded — with an explicit reason on the candidate, never silently —
    when the MES file is header-only or shorter than ``min_bars`` data rows, or
    when no same-date SPY recording exists: the checklist's SPY half cannot be
    answered without SPY bars.
    """
    data_dir = Path(data_dir)
    candidates: list[SessionCandidate] = []
    for mes_path in sorted(data_dir.glob("mes_*.csv")):
        match = _MES_FILE.match(mes_path.name)
        if not match:
            continue
        date = match.group(1)
        if start is not None and date < start:
            continue
        if end is not None and date > end:
            continue
        candidate = SessionCandidate(
            date=date,
            mes_path=mes_path,
            mes_bars=_count_data_rows(mes_path),
        )
        spy_path = data_dir / f"spy_{date}.csv"
        if spy_path.exists():
            candidate.spy_path = spy_path
            candidate.spy_bars = _count_data_rows(spy_path)
        else:
            candidate.excluded = (
                "no same-date spy_*.csv recording (SPY half of the checklist cannot run)"
            )
        if candidate.mes_bars < min_bars:
            candidate.excluded = (
                f"MES recording has {candidate.mes_bars} data bars (< min_bars={min_bars})"
            )
        candidates.append(candidate)
    return candidates


def load_session(
    mes_csv: str | Path,
    spy_csv: str | Path,
    cfg: MesChecklistConfig | None = None,
    *,
    ablation: str = BASELINE_NAME,
) -> "ReplaySession":
    """Load both recordings once and return a walkable :class:`ReplaySession`.

    ``ablation`` only labels the session (and every record it produces); the
    ablation overlay itself is applied by the caller via
    :func:`~tradingagents.mes.backtest.ablations.apply_ablation` before calling.
    """
    return ReplaySession(
        mes_bars=load_csv_bars(mes_csv),
        spy_bars=load_csv_bars(spy_csv),
        cfg=cfg or MesChecklistConfig(),
        ablation=ablation,
    )


# ---------------------------------------------------------------------------
# Walker
# ---------------------------------------------------------------------------


@dataclass
class ReplayRecord:
    """Everything the outcome joiner and tier table need about one MES bar.

    Every field is a copy of the production pipeline's own output for that bar
    close — the walker adds no judgement of its own.
    """

    index: int
    timestamp: datetime
    session_date: str

    side: str
    state: str
    tier: str
    tradeable: bool

    score: int
    max_score: int
    confirmations: int
    required: int

    gates_ok: bool
    gate_reasons: list[str]
    no_trade_reasons: list[str]

    spy_confirmations: int
    spy_confluence_ok: bool
    divergence: str

    last_price: float
    entry_zone: str | None = None
    stop: float | None = None
    first_target: float | None = None
    """Trade-template levels from the production levels helper (None when no trade)."""

    proximity_state: str = ""
    near_level: bool = False
    nearest_level_distance: float | None = None

    warnings: list[str] = field(default_factory=list)

    ablation: str = "baseline"
    """Named ablation this record was produced under ("baseline" = playbook defaults)."""
    config_fingerprint: str = ""
    """Short hash of the config values behind this record (W2.4 diffability)."""


@dataclass
class WalkResult:
    """Records for one walked session plus session-level diagnostics."""

    session_date: str
    records: list[ReplayRecord] = field(default_factory=list)
    skipped_bars: int = 0
    """Bars before both series had data — the live check would have errored there too."""
    warnings: list[str] = field(default_factory=list)
    """Session-level warnings, once each (e.g. a recording with no internals at all)."""


@dataclass
class ReplaySession:
    """A recorded MES/SPY session held in memory for bar-by-bar replay."""

    mes_bars: list[Bar]
    spy_bars: list[Bar]
    cfg: MesChecklistConfig = field(default_factory=MesChecklistConfig)
    #: Which named ablation overlay this session's cfg was built from. Stamped
    #: onto every record; ablations are applied by the caller (CLI/tests) via
    #: ``apply_ablation`` before constructing the session — the walker itself
    #: never knows ablation semantics, it only labels its output.
    ablation: str = "baseline"

    def walk(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> WalkResult:
        """Evaluate the checklist at every MES bar close, oldest first."""
        result = WalkResult(session_date=self.mes_bars[0].timestamp.strftime("%Y-%m-%d"))
        result.warnings.extend(_internals_gap_warnings(self.mes_bars, self.spy_bars))

        spy_stamps = [bar.timestamp for bar in self.spy_bars]
        for index, bar in enumerate(self.mes_bars):
            if (start is not None and bar.timestamp < start) or (
                end is not None and bar.timestamp > end
            ):
                continue
            as_of = bar.timestamp
            spy_visible = self.spy_bars[: bisect_right(spy_stamps, as_of)]
            try:
                snapshot = snapshot_from_bars(
                    self.mes_bars[: index + 1], spy_visible, as_of, self.cfg
                )
            except ValueError:
                # One of the series has no data at or before this bar yet; the
                # live checklist errors identically, so skip rather than fake.
                result.skipped_bars += 1
                continue

            checklist = evaluate(snapshot, "auto")
            proximity = build_proximity(snapshot, checklist)
            levels: TradeLevels | None = None
            if checklist.tradeable:
                levels = suggest_trade_levels_from_snapshot(
                    checklist.side, snapshot, checklist
                )
            result.records.append(
                _make_record(
                    index,
                    bar.timestamp,
                    checklist,
                    proximity,
                    levels,
                    ablation=self.ablation,
                    config_fingerprint=fingerprint_config(self.cfg),
                )
            )
        return result


def _state_text(state: SetupState | str) -> str:
    return state.value if isinstance(state, SetupState) else str(state)


def _nearest_level_distance(report: ProximityReport) -> float | None:
    distances = [abs(level.distance) for level in report.levels_above + report.levels_below]
    return min(distances) if distances else None


def _internals_gap_warnings(mes_bars: list[Bar], spy_bars: list[Bar]) -> list[str]:
    """One warning per series when the recording carries no internals at all."""
    warnings: list[str] = []
    if mes_bars and not _has_any_internals(mes_bars):
        warnings.append(
            "MES recording carries no $ADD/$TICK/$VOLD readings; internals scoring "
            "will fail on every bar exactly as it would live."
        )
    if spy_bars and not _has_any_internals(spy_bars):
        warnings.append(
            "SPY recording carries no $ADD/$TICK/$VOLD readings; breadth gates will "
            "block every bar, matching live behavior with missing internals."
        )
    return warnings


def _has_any_internals(bars: list[Bar]) -> bool:
    return any(
        bar.add is not None or bar.tick is not None or bar.vold is not None for bar in bars
    )


def _make_record(
    index: int,
    timestamp: datetime,
    result: ChecklistResult,
    proximity: ProximityReport,
    levels: TradeLevels | None,
    *,
    ablation: str = BASELINE_NAME,
    config_fingerprint: str = "",
) -> ReplayRecord:
    return ReplayRecord(
        index=index,
        timestamp=timestamp,
        session_date=timestamp.strftime("%Y-%m-%d"),
        side=str(result.side),
        state=_state_text(proximity.state),
        tier=result.tier,
        tradeable=result.tradeable,
        score=result.score,
        max_score=result.max_score,
        confirmations=result.confirmations,
        required=result.required,
        gates_ok=result.gates_ok,
        gate_reasons=list(result.gate_reasons),
        no_trade_reasons=list(result.no_trade_reasons),
        spy_confirmations=result.spy_confirmations,
        spy_confluence_ok=result.spy_confluence_ok,
        divergence=str(result.divergence),
        last_price=result.last_price,
        entry_zone=levels.entry_zone if levels else None,
        stop=levels.stop_level if levels else None,
        first_target=levels.first_target if levels else None,
        proximity_state=_state_text(proximity.state),
        near_level=proximity.near_level,
        nearest_level_distance=_nearest_level_distance(proximity),
        warnings=[],
        ablation=ablation,
        config_fingerprint=config_fingerprint,
    )
