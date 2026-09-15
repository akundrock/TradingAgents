"""W2.6 evidence report: the single grep-able markdown artifact.

:func:`build_evidence_report` renders everything the replay harness already
computed into ``out/backtest-evidence.md`` — the artifact Phase 3 sweeps and
the W0.4 scorecard re-score consume. Sections are grep-able headings:

    ## Coverage manifest
    ## Cross-check (live fills)
    ## Tier × side — baseline
    ## Ablation deltas
    ## Parity status (W2.5)
    ## Known limitations

Design rules (W2.6 plan):

* Pure formatter — no new computation: it formats the manifest and the per-run
  stats the CLI already holds, reusing ``format_tier_table`` for tier × side
  tables. No engine calls, no second walk.
* Deterministic: everything is sorted, and ``generated_at`` is a parameter
  (no ``datetime.now()`` inside), so identical inputs → byte-identical output.
* Honest by construction: an honest ``GATE FAILED`` is the headline finding,
  ``n<5`` cells never render a bare percentage, exclusion reasons are copied
  verbatim, and the W2.5 parity line is emitted verbatim.
* Plain markdown only — no Rich markup or ANSI escapes (Phase 3 sweeps grep
  and diff this file).
"""

from __future__ import annotations

import csv
import re
import warnings
from pathlib import Path
from typing import Any

from .ablations import BASELINE_NAME
from .tier_table import TierTable, format_tier_table

#: The W2.5 parity snapshot, emitted verbatim into every evidence report.
#: Numbers are the filed class totals over the frozen 5-session fixture
#: (647 bars) from ``plans/azp-phase2-w25-parity-spot-check-plan.md`` —
#: update only when W2.5-D1 is re-attributed and the gate goes green.
PARITY_STATUS: str = (
    "W2.5 parity spot-check over the frozen 5-session fixture (647 bars): "
    "verdict parity a=99, price-shift artifact b=15, replay-side c=0 — "
    "unexplained=3 (W2.5-D1, filed attribution in "
    "plans/azp-phase2-w25-parity-spot-check-plan.md § Drift). The parity gate "
    "is red-by-design until those three bars are re-attributed; backtest "
    "verdicts are consistent, not proven."
)

_ORDER_DATE_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})$")

#: Context line for the live-fills cross-check: the order log mixes
#: discretionary fills with checklist trades, so this is context only.
_CROSS_CHECK_NOTE = (
    "Context, not a pass/fail gate: the live order log mixes discretionary and "
    "checklist fills, while the replay count is verdict-tradeable bars from the "
    "baseline run. Cross-check totals for sanity only — never a pass/fail gate."
)


def _iso_date(value: str) -> str | None:
    """Normalize TOS ``M/D/YY`` (or ISO) dates to ``YYYY-MM-DD``, else None."""
    value = value.strip()
    match = _ORDER_DATE_RE.match(value)
    if not match:
        return None
    month, day, year = (int(part) for part in match.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    if year < 100:
        year += 2000
    return f"{year:04d}-{month:02d}-{day:02d}"


def load_order_history(path: str | Path) -> list[dict]:
    """Parse a TOS account-statement order-history CSV into fill dicts.

    keeps only rows that are both an MES contract and ``Filled`` (working or
    canceled orders, and non-MES contracts, are noise for the cross-check).
    Dates are normalized from TOS ``M/D/YY`` to ISO ``YYYY-MM-DD``. Malformed
    rows are tolerated with a counted ``UserWarning`` — never raised.
    """
    fills: list[dict] = []
    malformed = 0
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            side = (row.get("B/S") or "").strip()
            status = (row.get("Status") or "").strip()
            contract = (row.get("Contract") or "").strip()
            raw_date = (row.get("Date") or "").strip()
            date = _iso_date(raw_date)
            # Expected noise — unfilled/canceled orders and non-MES contracts —
            # is skipped quietly. Only rows that *claim* to be filled MES but
            # cannot be interpreted (e.g. unparseable date) count as malformed.
            if not (
                side in {"Buy", "Sell"}
                and status == "Filled"
                and contract.startswith("MES")
                and date
            ):
                # Expected noise — unfilled/canceled orders and non-MES
                # contracts — is skipped quietly. Only rows that cannot be
                # interpreted at all (missing essential fields or an
                # unparseable date on an otherwise-kept row) are malformed.
                interpretable = bool(side) and bool(status) and bool(contract) and date
                if not interpretable:
                    malformed += 1
                continue
            fills.append(
                {
                    "date": date,
                    "side": side.lower(),
                    "contract": contract,
                    "fill_time": (row.get("Fill Time") or "").strip(),
                }
            )
    if malformed:
        warnings.warn(
            f"order history: skipped {malformed} malformed row(s)",
            UserWarning,
            stacklevel=2,
        )
    return fills


# ---------------------------------------------------------------------------
# Emitter
# ---------------------------------------------------------------------------


def _num(value: Any) -> str:
    """Signed, stable number formatting (integers keep ``+``/``-``, no float wobble)."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:+d}" if value else "0"
    if isinstance(value, float):
        return f"{value:+.4f}" if value else "0"
    return str(value)


def _run_order(run_stats: dict) -> list[str]:
    """Baseline first, then every other run sorted — deterministic regardless
    of dict insertion order."""
    names = [name for name in run_stats if name != BASELINE_NAME]
    return [BASELINE_NAME, *sorted(names)] if BASELINE_NAME in run_stats else sorted(names)


def _coverage_section(manifest) -> list[str]:
    candidates = sorted(manifest, key=lambda candidate: candidate.date)
    walked = [candidate for candidate in candidates if not candidate.excluded]
    excluded = [candidate for candidate in candidates if candidate.excluded]
    lines = [
        "## Coverage manifest",
        "",
        "| date | MES bars | SPY bars | status |",
        "|---|---:|---:|---|",
    ]
    for candidate in candidates:
        status = "included" if not candidate.excluded else "excluded"
        lines.append(
            f"| {candidate.date} | {candidate.mes_bars} | {candidate.spy_bars} | {status} |"
        )
    lines += [
        "",
        f"sessions included: {len(walked)} / excluded: {len(excluded)} (discovered: {len(candidates)})",
    ]
    for candidate in excluded:
        lines.append(f"- {candidate.date}: {candidate.excluded}")
    return lines


def _cross_check_section(
    order_history: list[dict] | None, run_stats: dict
) -> list[str]:
    lines = ["## Cross-check (live fills)", ""]
    if order_history is None:
        return [*lines, "cross-check skipped — no order history provided.", ""]
    baseline = run_stats.get(BASELINE_NAME, {})
    tradeable_by_date: dict[str, int] = baseline.get("tradeable_by_date") or {}
    per_date: dict[str, dict[str, int]] = {}
    for fill in order_history:
        bucket = per_date.setdefault(fill["date"], {"buy": 0, "sell": 0, "total": 0})
        bucket[fill["side"]] += 1
        bucket["total"] += 1
    lines += [
        "| date | live buys | live sells | live fills | replay tradeable (baseline) |",
        "|---|---:|---:|---:|---:|",
    ]
    for date in sorted(set(per_date) | set(tradeable_by_date)):
        bucket = per_date.get(date, {"buy": 0, "sell": 0, "total": 0})
        lines.append(
            f"| {date} | {bucket['buy']} | {bucket['sell']} | {bucket['total']} "
            f"| {tradeable_by_date.get(date, 0)} |"
        )
    return [*lines, "", _CROSS_CHECK_NOTE, ""]


def _tier_section(name: str, stats: dict) -> list[str]:
    table: TierTable | None = stats.get("tier_table")
    lines = [f"## Tier × side — {name}", ""]
    if table is None:
        return [*lines, "(tier table unavailable for this run)", ""]
    lines += [
        "n = closed trades per tier × side. Below the sample floor a cell is",
        "marked n<5 — not interpretable and never renders a bare percentage.",
        "",
        format_tier_table(table),
        "",
    ]
    gate = stats.get("gate")
    if gate is not None:
        lines += [gate.message, ""]
    return lines


_DELTA_METRICS = ("records", "tradeable", "trades", "wins", "total_r", "avg_r")


def _ablation_section(run_stats: dict) -> list[str]:
    lines = ["## Ablation deltas", ""]
    baseline = run_stats.get(BASELINE_NAME, {})
    if not baseline:
        return [*lines, "(no baseline run stats — nothing to anchor on)", ""]
    lines += [
        "All deltas anchored on baseline (fingerprint "
        f"`{baseline.get('config_fingerprint', '?')}`).",
        "",
    ]
    for name in _run_order(run_stats):
        if name == BASELINE_NAME:
            continue
        stats = run_stats[name]
        lines.append(f"### {name}")
        lines += ["", f"- fingerprint `{stats.get('config_fingerprint', '?')}`"]
        delta = stats.get("config_delta") or {}
        rendered = ", ".join(
            f"{field} {change['from']}→{change['to']}"
            for field, change in sorted(delta.items())
        )
        lines.append(
            f"- config delta: {rendered or 'none — same config as baseline'}"
        )
        lines += [
            "",
            f"| metric | baseline | {name} | delta |",
            "|---|---:|---:|---:|",
        ]
        for metric in _DELTA_METRICS:
            base_value = baseline.get(metric, 0)
            run_value = stats.get(metric, 0)
            delta_value = run_value - base_value
            lines.append(
                f"| {metric} | {_num(base_value)} | {_num(run_value)} | {_num(delta_value)} |"
            )
        run_table: TierTable | None = stats.get("tier_table")
        if run_table is not None:
            lines += ["", "Tier shifts vs baseline (n per tier × side):", ""]
            lines += _tier_shifts(baseline.get("tier_table"), run_table)
        lines.append("")
    return lines


def _tier_shifts(baseline_table: TierTable | None, run_table: TierTable) -> list[str]:
    """Per tier × side cell-n shifts between two runs (never bare percentages)."""
    if baseline_table is None:
        return ["- (baseline tier table unavailable — shifts not comparable)"]
    cells: dict[tuple[str, str], int] = {}
    for cell in run_table.cells:
        cells[(cell.tier, cell.side)] = cell.n
    for cell in baseline_table.cells:
        cells.setdefault((cell.tier, cell.side), 0)
    shifts = []
    for (tier, side), run_n in sorted(cells.items()):
        base_n = next(
            (
                cell.n
                for cell in baseline_table.cells
                if cell.tier == tier and cell.side == side
            ),
            0,
        )
        if run_n != base_n:
            shifts.append((tier, side, base_n, run_n))
    if not shifts:
        return ["- none — identical tier × side sample sizes as baseline"]
    lines = ["| tier | side | baseline n | run n | delta |", "|---|---|---:|---:|---:|"]
    for tier, side, base_n, run_n in sorted(
        shifts, key=lambda item: (item[0], item[1])
    ):
        lines.append(f"| {tier} | {side} | {base_n} | {run_n} | {run_n - base_n:+d} |")
    return lines


def _limitations_section(manifest) -> list[str]:
    candidates = sorted(manifest, key=lambda candidate: candidate.date)
    discovered = len(candidates)
    spy_sessions = sum(1 for candidate in candidates if candidate.spy_path)
    return [
        "## Known limitations",
        "",
        "- Verdicts are bar-replayed on 5-minute bars; the live checklist runs on",
        "  1-minute snapshots from the TOS dashboard. Bar-level verdict parity is",
        "  approximate — the W2.5 spot-check quantified the gap (see parity status).",
        f"- SPY coverage is partial: {spy_sessions}/{discovered}",
        "  discovered sessions have same-date SPY recordings; sessions without SPY",
        "  are excluded, so the walked corpus understates the recorded corpus.",
        "- W2.5-D1: the 3 unexplained per-bar verdict diffs on the frozen fixture",
        "  are filed (attribution in plans/azp-phase2-w25-parity-spot-check-plan.md",
        "  § Drift); the parity gate stays red-by-design until they are re-attributed.",
        "",
    ]


def build_evidence_report(
    *,
    manifest,
    run_stats: dict[str, dict],
    parity_status: str,
    git_sha: str,
    order_history: list[dict] | None = None,
    generated_at: str = "",
    data_dir: str = "",
    wall_clock_seconds: float | None = None,
) -> str:
    """Render the evidence report as plain markdown (deterministic; no I/O).

    ``manifest`` is the walker's :class:`SessionCandidate` list (included and
    excluded); ``run_stats`` is the per-run dict the CLI already accumulates —
    extended with ``tier_table`` (TierTable), ``gate`` (GateResult) and
    ``tradeable_by_date`` (per-date verdict-tradeable bar counts). Everything is
    formatted from what the CLI already holds — no engine calls, no second walk.
    """
    candidates = sorted(manifest, key=lambda candidate: candidate.date)
    walked = [candidate for candidate in candidates if not candidate.excluded]
    excluded = [candidate for candidate in candidates if candidate.excluded]
    header_bits = [f"generated {generated_at}", f"git {git_sha}"]
    if data_dir:
        header_bits.append(f"data dir {data_dir}")
    if wall_clock_seconds is not None:
        header_bits.append(f"wall-clock {wall_clock_seconds:.1f}s")
    corpus = (
        f"corpus: sessions walked: {len(walked)} / excluded: {len(excluded)}"
    )
    lines = [
        "# MES backtest evidence — full corpus",
        "",
        " · ".join(header_bits),
        corpus,
        "",
    ]
    lines += _coverage_section(manifest)
    lines += [""] if not lines[-1] else [""]
    lines += _cross_check_section(order_history, run_stats)
    for name in _run_order(run_stats):
        lines += _tier_section(name, run_stats[name])
    lines += _ablation_section(run_stats)
    lines += ["## Parity status (W2.5)", "", parity_status, ""]
    lines += _limitations_section(manifest)
    return "\n".join(lines).rstrip("\n") + "\n"



