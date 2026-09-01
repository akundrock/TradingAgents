"""Append-only JSONL journal for the MES trading copilot.

One file per session date. Every checklist evaluation and every morning
hypothesis is appended as a single JSON line, so the day's record can be
replayed or fed back to a review LLM without mutating history.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .checklist import ChecklistResult
from .snapshot import MesSnapshot

logger = logging.getLogger(__name__)


def _num(value: Any) -> float | None:
    """Coerce numpy/pandas scalars to plain floats; None stays None."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return ""


class MesJournal:
    """Append-only JSONL log of MES checklist runs and daily hypotheses."""

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        directory = cfg.get("mes_journal_dir")
        if not directory:
            directory = Path(cfg.get("results_dir", ".")) / "mes_journal"
        self._dir = Path(directory).expanduser()

    @property
    def directory(self) -> Path:
        return self._dir

    def _path(self, date: str) -> Path:
        return self._dir / f"{date}.jsonl"

    # --- Write path ---

    def _append(self, date: str, record: dict[str, Any]) -> None:
        path = self._path(date)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
        except OSError as exc:
            logger.warning("MES journal write failed for %s: %s", path, exc)

    def append_check(
        self,
        *,
        snapshot: MesSnapshot,
        result: ChecklistResult,
        verdict_markdown: str = "",
        sizing: dict | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "kind": "check",
            "logged_at": datetime.now().isoformat(),
            "as_of": snapshot.as_of.isoformat(),
            "session_date": snapshot.session_date,
            "side": result.side,
            "direction": result.direction,
            "score": result.score,
            "max_score": result.max_score,
            "confirmations": result.confirmations,
            "required": result.required,
            "tier": result.tier,
            "gates_ok": bool(result.gates_ok),
            "tradeable": bool(result.tradeable),
            "spy_confirmations": result.spy_confirmations,
            "divergence": result.divergence,
            "last_price": _num(result.last_price),
            "vwap": _num(result.vwap),
            "atr": _num(result.atr),
            "internals": {
                "add": _num(snapshot.add),
                "tick": _num(snapshot.tick),
                "vold": _num(snapshot.vold),
            },
            "gate_reasons": list(result.gate_reasons),
            "no_trade_reasons": list(result.no_trade_reasons),
            "items": [
                {
                    "name": item.name,
                    "chart": item.chart,
                    "passed": bool(item.passed),
                    "observed": item.observed,
                    "threshold": item.threshold,
                    "weight": item.weight,
                }
                for item in result.all_items
            ],
            "verdict": verdict_markdown,
            "sizing": sizing,
        }
        self._append(snapshot.session_date, record)

    def save_hypothesis(
        self,
        *,
        date: str,
        hypothesis_markdown: str,
        market_context: str = "",
    ) -> None:
        self._append(
            date,
            {
                "kind": "hypothesis",
                "logged_at": datetime.now().isoformat(),
                "session_date": date,
                "hypothesis": hypothesis_markdown,
                "market_context": market_context,
            },
        )

    # --- Read path ---

    def load_day(self, date: str) -> list[dict]:
        path = self._path(date)
        if not path.exists():
            return []
        entries: list[dict] = []
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("MES journal read failed for %s: %s", path, exc)
            return []
        for lineno, line in enumerate(raw.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed MES journal line %s:%d", path, lineno)
                continue
            if isinstance(parsed, dict):
                entries.append(parsed)
        return entries

    def load_checks(self, date: str) -> list[dict]:
        return [e for e in self.load_day(date) if e.get("kind") == "check"]

    def load_hypothesis(self, date: str) -> dict | None:
        found = [e for e in self.load_day(date) if e.get("kind") == "hypothesis"]
        return found[-1] if found else None

    def available_dates(self) -> list[str]:
        if not self._dir.exists():
            return []
        try:
            return sorted(p.stem for p in self._dir.glob("*.jsonl"))
        except OSError as exc:
            logger.warning("MES journal listing failed for %s: %s", self._dir, exc)
            return []

    # --- Review helpers ---

    def summarize_checks(self, date: str) -> str:
        checks = self.load_checks(date)
        if not checks:
            return f"No checks logged for {date}."
        rows = [
            "| Time | Side | Score | Tier | Gates | Tradeable | Verdict |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for check in checks:
            as_of = str(check.get("as_of", ""))
            time_label = as_of[11:16] if len(as_of) >= 16 else as_of
            rows.append(
                "| {time} | {side} | {score}/{max_score} | {tier} | {gates} | {tradeable} | {verdict} |".format(
                    time=time_label,
                    side=check.get("side", "?"),
                    score=check.get("score", 0),
                    max_score=check.get("max_score", 0),
                    tier=check.get("tier", "?"),
                    gates="pass" if check.get("gates_ok") else "fail",
                    tradeable="yes" if check.get("tradeable") else "no",
                    verdict=_first_line(check.get("verdict", "")) or "-",
                )
            )
        return "\n".join(rows)
