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
from .management import OpenTrade, _r_of
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

    # --- Trade lifecycle ---

    def append_trade_opened(self, trade: OpenTrade, *, entry_context: dict | None = None) -> None:
        date = trade.entry_time.strftime("%Y-%m-%d")
        self._append(date, {
            "kind": "trade_opened",
            "logged_at": datetime.now().isoformat(),
            "session_date": date,
            "side": trade.side,
            "contracts": trade.contracts,
            "remaining": trade.remaining,
            "entry": trade.entry,
            "stop": trade.stop,
            "initial_stop": trade.initial_stop,
            "target": trade.target,
            "entry_time": trade.entry_time.isoformat(timespec="minutes"),
            "initial_risk_points": trade.initial_risk_points,
            "fired": dict(trade.fired),
            "manual_events": list(trade.manual_events),
            "realized_r": trade.realized_r,
            "entry_context": entry_context or {},
        })

    def append_trade_adjusted(
        self,
        date: str,
        *,
        stop: float | None = None,
        note: str | None = None,
        as_of: str | None = None,
    ) -> None:
        self._append(date, {
            "kind": "trade_adjusted",
            "logged_at": datetime.now().isoformat(),
            "session_date": date,
            "as_of": as_of,
            "stop": stop,
            "note": note,
        })

    def append_trade_closed(
        self,
        trade: OpenTrade,
        *,
        exit_price: float,
        reason: str,
        as_of: datetime,
        mfe_r: float = 0.0,
        mae_r: float = 0.0,
    ) -> None:
        from .management import _r_of

        realized = round(trade.realized_r + trade.remaining * _r_of(exit_price, trade), 4)
        self._append(trade.entry_time.strftime("%Y-%m-%d"), {
            "kind": "trade_closed",
            "logged_at": datetime.now().isoformat(),
            "as_of": as_of.isoformat(timespec="minutes"),
            "side": trade.side,
            "entry": trade.entry,
            "stop": trade.stop,
            "exit_price": exit_price,
            "reason": reason,
            "realized_r": realized,
            "mfe_r": mfe_r,
            "mae_r": mae_r,
            "fired": dict(trade.fired),
            "manual_events": list(trade.manual_events),
            "entry_time": trade.entry_time.isoformat(timespec="minutes"),
        })

    def load_trades(self, date: str) -> list[dict]:
        kinds = {"trade_opened", "trade_adjusted", "trade_closed"}
        return [e for e in self.load_day(date) if e.get("kind") in kinds]

    def find_open_trade(self, date: str) -> OpenTrade | None:
        """Replay the day's trade records into the currently-open trade."""
        trade: OpenTrade | None = None
        for record in self.load_trades(date):
            kind = record.get("kind")
            if kind == "trade_opened":
                trade = OpenTrade(
                    side=record["side"],
                    contracts=int(record["contracts"]),
                    remaining=int(record["remaining"]),
                    entry=float(record["entry"]),
                    stop=float(record["stop"]),
                    initial_stop=float(record["initial_stop"]),
                    target=record["target"],
                    entry_time=datetime.fromisoformat(record["entry_time"]),
                    initial_risk_points=float(record["initial_risk_points"]),
                    fired=dict(record.get("fired", {})),
                    manual_events=list(record.get("manual_events", [])),
                    realized_r=float(record.get("realized_r", 0.0)),
                )
            elif kind == "trade_adjusted" and trade is not None:
                if record.get("stop") is not None:
                    trade.stop = float(record["stop"])
                if record.get("note"):
                    trade.manual_events.append(record["note"])
            elif kind == "trade_closed":
                return None
        return trade

    def summarize_trades(self, date: str) -> str:
        """Markdown table of closed trades for the review agent."""
        trades = self.load_trades(date)
        if not trades:
            return f"No trades logged for {date}."
        rows = [
            "| Side | Entry | Exit | Reason | Realized R |",
            "| --- | --- | --- | --- | --- |",
        ]
        for record in self.load_trades(date):
            if record.get("kind") != "trade_closed":
                continue
            rows.append(
                "| {side} | {entry:.2f} | {exit:.2f} | {reason} | {realized_r:+.2f} |".format(
                    side=record.get("side", "?"),
                    entry=float(record.get("entry", 0.0)),
                    exit=float(record.get("exit_price", 0.0)),
                    reason=record.get("reason", "?"),
                    realized_r=float(record.get("realized_r", 0.0)),
                )
            )
        if len(rows) == 2:
            return f"No closed trades for {date} (an open trade may still be running)."
        return "\n".join(rows)
