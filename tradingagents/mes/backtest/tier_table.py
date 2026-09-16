"""W2.3 tier→outcome table: the AZP Phase 2 deliverable.

Groups closed :class:`~tradingagents.mes.backtest.outcomes.TradeOutcome` rows
by **tier × side** and reports per-cell sample size, hit rate, expectancy (R),
median MFE/MAE (R), median hold bars, and total R.

The baseline defect being fixed is unsourced stats: cells with fewer than
``SAMPLE_FLOOR`` closed trades are marked ``n<5 — not interpretable`` and never
render a bare percentage. The premium-vs-standard gate (over >= 20 included
sessions) is a strategy finding either way — premium <= standard is recorded as
a headline failure, not a build failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from .outcomes import TradeOutcome

SAMPLE_FLOOR = 5
"""Minimum closed trades per tier × side cell before any rate is quoted."""

GATE_MIN_SESSIONS = 20
"""Minimum included sessions before the premium-vs-standard gate is evaluated."""

_TIER_ORDER = ("premium", "standard", "marginal", "low")
_SIDE_ORDER = ("long", "short")


@dataclass(frozen=True)
class TierCell:
    """Outcome aggregates for one tier × side bucket."""

    tier: str
    side: str
    n: int
    hit_rate: float | None
    """Share of closed trades with ``realized_r > 0``; None below the floor."""
    expectancy_r: float | None
    """Mean realized R; None below the floor."""
    median_mfe_r: float | None
    median_mae_r: float | None
    median_hold_bars: float | None
    total_r: float

    @property
    def interpretable(self) -> bool:
        return self.n >= SAMPLE_FLOOR


@dataclass
class TierTable:
    """The tier × side aggregate plus the per-tier pooled view for the gate."""

    cells: list[TierCell] = field(default_factory=list)
    sessions: int = 0

    def cell(self, tier: str, side: str) -> TierCell | None:
        for tier_cell in self.cells:
            if tier_cell.tier == tier and tier_cell.side == side:
                return tier_cell
        return None

    def expectancy(self, tier: str) -> float | None:
        """Pooled (both-side) expectancy for a tier, or None with no trades."""
        rows = [cell for cell in self.cells if cell.tier == tier and cell.n]
        if not rows:
            return None
        total_r = sum(cell.total_r for cell in rows)
        total_n = sum(cell.n for cell in rows)
        return total_r / total_n


def build_tier_table(
    outcomes,
    *,
    sessions: int = 1,
    sample_floor: int = SAMPLE_FLOOR,
) -> TierTable:
    """Aggregate closed-trade outcomes into tier × side cells."""
    grouped: dict[tuple[str, str], list] = {}
    for outcome in outcomes:
        grouped.setdefault((outcome.tier, outcome.side), []).append(outcome)

    cells: list[TierCell] = []
    for (tier, side), trades in grouped.items():
        rs = [trade.realized_r for trade in trades]
        enough = len(trades) >= sample_floor
        cells.append(
            TierCell(
                tier=tier,
                side=side,
                n=len(trades),
                hit_rate=(sum(1 for r in rs if r > 0) / len(rs)) if enough else None,
                expectancy_r=(sum(rs) / len(rs)) if enough else None,
                median_mfe_r=median([trade.mfe_r for trade in trades]) if enough else None,
                median_mae_r=median([trade.mae_r for trade in trades]) if enough else None,
                median_hold_bars=float(median(trade.hold_bars for trade in trades)) if enough else None,
                total_r=round(sum(rs), 4),
            )
        )

    def _sort_key(cell: TierCell) -> tuple[int, int]:
        tier_rank = _TIER_ORDER.index(cell.tier) if cell.tier in _TIER_ORDER else len(_TIER_ORDER)
        side_rank = _SIDE_ORDER.index(cell.side) if cell.side in _SIDE_ORDER else len(_SIDE_ORDER)
        return (tier_rank, side_rank)

    return TierTable(cells=sorted(cells, key=_sort_key), sessions=sessions)


@dataclass
class GateResult:
    """The W2.3 premium-vs-standard verdict (a finding either way)."""

    evaluated: bool
    met: bool | None
    """True when premium expectancy > standard expectancy; None when not evaluated."""
    message: str


def evaluate_gate(table: TierTable, *, min_sessions: int = GATE_MIN_SESSIONS) -> GateResult:
    """Compare pooled premium vs standard expectancy over >= min_sessions sessions."""
    if table.sessions < min_sessions:
        return GateResult(
            evaluated=False,
            met=None,
            message=(
                f"Gate not evaluated: {table.sessions} included sessions "
                f"(< {min_sessions})."
            ),
        )

    premium = table.expectancy("premium")
    standard = table.expectancy("standard")
    if premium is None or standard is None:
        return GateResult(
            evaluated=False,
            met=None,
            message=(
                "Gate not evaluable: premium or standard tier produced no closed "
                f"trades across {table.sessions} sessions."
            ),
        )

    if premium > standard:
        message = (
            f"GATE MET: premium expectancy {premium:+.3f}R > standard {standard:+.3f}R "
            f"over {table.sessions} sessions."
        )
    else:
        message = (
            f"GATE FAILED: premium expectancy {premium:+.3f}R <= standard "
            f"{standard:+.3f}R over {table.sessions} sessions. The ladder is "
            "falsified-or-flawed — recorded as a strategy finding, not a build failure."
        )
    return GateResult(evaluated=True, met=premium > standard, message=message)


def format_tier_table(table: TierTable, *, sample_floor: int = SAMPLE_FLOOR) -> str:
    """Render the tier × side table as grep-able markdown (no dashboard)."""
    header = (
        "| tier | side | n | hit rate | expectancy (R) | median MFE (R) |"
        " median MAE (R) | median hold bars | total R |"
    )
    divider = "|---|---|---|---|---|---|---|---|---:|"
    lines = [header, divider]
    for cell in table.cells:
        if cell.n >= sample_floor:
            hit = f"{cell.hit_rate:.0%}"
            expect = f"{cell.expectancy_r:+.3f}"
            mfe = f"{cell.median_mfe_r:+.3f}"
            mae = f"{cell.median_mae_r:+.3f}"
            hold = f"{cell.median_hold_bars:g}"
        else:
            hit = expect = mfe = mae = hold = f"n<{sample_floor} — not interpretable"
        lines.append(
            "| "
            + " | ".join(
                [
                    cell.tier,
                    cell.side,
                    str(cell.n),
                    hit,
                    expect,
                    mfe,
                    mae,
                    hold,
                    f"{cell.total_r:+.2f}",
                ]
            )
            + " |"
        )
    return "\n".join(lines)
