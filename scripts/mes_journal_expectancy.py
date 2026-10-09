#!/usr/bin/env python3
"""Expectancy by checklist tier × side from live MES journal JSONL.

Joins each ``trade_closed`` event to the same-day ``trade_opened`` that shares
``entry_time`` (fallback: entry + side) and pulls ``entry_context.tier``.

Emits markdown (and optional CSV). Cells with ``n < SAMPLE_FLOOR`` are marked
not interpretable — matching ``tradingagents.mes.backtest.tier_table``.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median

SAMPLE_FLOOR = 5
_TIER_ORDER = ("premium", "standard", "marginal", "low")
_SIDE_ORDER = ("long", "short")


@dataclass(frozen=True)
class ClosedTrade:
    session_date: str
    side: str
    tier: str
    realized_r: float
    mfe_r: float
    mae_r: float
    reason: str
    entry: float
    exit_price: float


def _load_day(path: Path) -> list[dict]:
    events: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def join_closed_trades(events: list[dict], *, session_date: str) -> list[ClosedTrade]:
    """Pair closed trades to the matching open's entry_context.tier."""
    opens_by_key: dict[tuple, dict] = {}
    opens_by_entry: dict[tuple, dict] = {}
    for event in events:
        if event.get("kind") != "trade_opened":
            continue
        entry_time = event.get("entry_time")
        side = str(event.get("side", ""))
        entry = float(event.get("entry", 0.0))
        if entry_time:
            opens_by_key[(entry_time, side, entry)] = event
        opens_by_entry[(side, entry)] = event

    closed: list[ClosedTrade] = []
    for event in events:
        if event.get("kind") != "trade_closed":
            continue
        side = str(event.get("side", ""))
        entry = float(event.get("entry", 0.0))
        entry_time = event.get("entry_time")
        opened = None
        if entry_time:
            opened = opens_by_key.get((entry_time, side, entry))
        if opened is None:
            opened = opens_by_entry.get((side, entry))
        ctx = (opened or {}).get("entry_context") or {}
        tier = str(ctx.get("tier") or "unknown")
        closed.append(
            ClosedTrade(
                session_date=session_date,
                side=side or "unknown",
                tier=tier,
                realized_r=float(event.get("realized_r", 0.0)),
                mfe_r=float(event.get("mfe_r", 0.0)),
                mae_r=float(event.get("mae_r", 0.0)),
                reason=str(event.get("reason", "")),
                entry=entry,
                exit_price=float(event.get("exit_price", 0.0)),
            )
        )
    return closed


def load_journal(journal_dir: Path) -> list[ClosedTrade]:
    trades: list[ClosedTrade] = []
    for path in sorted(journal_dir.glob("*.jsonl")):
        if path.name == "standing_rules.json":
            continue
        session_date = path.stem
        trades.extend(join_closed_trades(_load_day(path), session_date=session_date))
    return trades


def aggregate(trades: list[ClosedTrade], *, sample_floor: int = SAMPLE_FLOOR) -> list[dict]:
    grouped: dict[tuple[str, str], list[ClosedTrade]] = defaultdict(list)
    for trade in trades:
        grouped[(trade.tier, trade.side)].append(trade)

    rows: list[dict] = []
    for (tier, side), bucket in grouped.items():
        rs = [t.realized_r for t in bucket]
        enough = len(bucket) >= sample_floor
        rows.append(
            {
                "tier": tier,
                "side": side,
                "n": len(bucket),
                "hit_rate": (sum(1 for r in rs if r > 0) / len(rs)) if enough else None,
                "expectancy_r": (sum(rs) / len(rs)) if enough else None,
                "median_mfe_r": median([t.mfe_r for t in bucket]) if enough else None,
                "median_mae_r": median([t.mae_r for t in bucket]) if enough else None,
                "total_r": round(sum(rs), 4),
            }
        )

    def _sort_key(row: dict) -> tuple[int, int]:
        tier_rank = _TIER_ORDER.index(row["tier"]) if row["tier"] in _TIER_ORDER else len(_TIER_ORDER)
        side_rank = _SIDE_ORDER.index(row["side"]) if row["side"] in _SIDE_ORDER else len(_SIDE_ORDER)
        return (tier_rank, side_rank)

    return sorted(rows, key=_sort_key)


def format_markdown(
    trades: list[ClosedTrade],
    rows: list[dict],
    *,
    journal_dir: Path,
    sample_floor: int = SAMPLE_FLOOR,
) -> str:
    lines = [
        "# MES journal expectancy by setup (tier × side)",
        "",
        f"journal: `{journal_dir}`",
        f"closed trades: {len(trades)}",
        f"sample floor: n < {sample_floor} → not interpretable",
        "",
    ]
    interpretable = sum(1 for row in rows if row["n"] >= sample_floor)
    if len(trades) < sample_floor or interpretable == 0:
        lines.extend(
            [
                f"**Insufficient N:** {len(trades)} closed trade(s) across "
                f"{len(rows)} tier×side cell(s); none meet the sample floor "
                f"(n ≥ {sample_floor}). Treat as a sanity check only.",
                "",
            ]
        )

    lines.extend(
        [
            "| tier | side | n | hit rate | expectancy (R) | median MFE (R) | median MAE (R) | total R |",
            "|---|---|---:|---|---|---|---|---:|",
        ]
    )
    for row in rows:
        if row["n"] >= sample_floor:
            hit = f"{row['hit_rate']:.0%}"
            expect = f"{row['expectancy_r']:+.3f}"
            mfe = f"{row['median_mfe_r']:+.3f}"
            mae = f"{row['median_mae_r']:+.3f}"
        else:
            hit = expect = mfe = mae = f"n<{sample_floor} — not interpretable"
        lines.append(
            f"| {row['tier']} | {row['side']} | {row['n']} | {hit} | {expect} | {mfe} | {mae} | {row['total_r']:+.2f} |"
        )

    if trades:
        lines.extend(["", "## Closed trades", ""])
        lines.append("| date | tier | side | entry | exit | reason | realized R |")
        lines.append("|---|---|---|---:|---:|---|---:|")
        for t in trades:
            lines.append(
                f"| {t.session_date} | {t.tier} | {t.side} | {t.entry:.2f} | "
                f"{t.exit_price:.2f} | {t.reason} | {t.realized_r:+.4f} |"
            )
    return "\n".join(lines) + "\n"


def write_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = [
        "tier",
        "side",
        "n",
        "hit_rate",
        "expectancy_r",
        "median_mfe_r",
        "median_mae_r",
        "total_r",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--journal-dir",
        type=Path,
        default=Path.home() / ".tradingagents" / "logs" / "mes_journal",
        help="Directory of YYYY-MM-DD.jsonl journal files",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Write markdown report to PATH (default: stdout)",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Optional CSV twin of the tier × side table",
    )
    parser.add_argument(
        "--sample-floor",
        type=int,
        default=SAMPLE_FLOOR,
        help=f"Minimum n before rates are quoted (default {SAMPLE_FLOOR})",
    )
    args = parser.parse_args(argv)

    journal_dir = args.journal_dir.expanduser().resolve()
    if not journal_dir.is_dir():
        print(f"error: journal dir not found: {journal_dir}", file=sys.stderr)
        return 2

    trades = load_journal(journal_dir)
    rows = aggregate(trades, sample_floor=args.sample_floor)
    md = format_markdown(
        trades, rows, journal_dir=journal_dir, sample_floor=args.sample_floor
    )

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(md, encoding="utf-8")
        print(f"wrote {args.report}", file=sys.stderr)
    else:
        sys.stdout.write(md)

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        write_csv(args.csv, rows)
        print(f"wrote {args.csv}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
