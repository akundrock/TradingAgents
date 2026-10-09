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


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return ""


_MISSED_GRACE_SECONDS = 600.0
"""A fire stays 'open' for its trigger bar (5m) plus one bar of grace."""


def _iso(value: Any) -> datetime | None:
    """Parse an ISO timestamp from a journal record; None when absent/mangled."""
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _resolve_fires(
    fires: list[dict],
    checks: list[dict],
    skips: list[dict],
    flips: list[dict] | None = None,
    *,
    resolve_as_of: datetime | None = None,
) -> list[dict]:
    """Return each fire annotated with an outcome: checked / skipped / flipped / missed / open.

    Level-retest fires resolve via the earliest ``check`` or same-``rule_id``
    ``rule_skip`` at or after ``as_of``. Invalidation fires resolve via a
    ``flip`` or same-``rule_id`` skip (never a check — the required action is
    a re-read). Fires still unresolved past ``resolve_as_of`` by more than the
    grace window count as ``missed``; without ``resolve_as_of`` everything
    unresolved stays ``open``.
    """
    check_times = [t for t in (_iso(record.get("as_of")) for record in checks) if t]
    flip_times = [t for t in (_iso(record.get("as_of")) for record in (flips or [])) if t]
    skips_by_rule: dict[str, list[datetime]] = {}
    for skip in skips:
        skip_time = _iso(skip.get("as_of"))
        if skip_time is not None:
            skips_by_rule.setdefault(str(skip.get("rule_id")), []).append(skip_time)

    resolved: list[dict] = []
    for fire in fires:
        fired_at = _iso(fire.get("as_of"))
        outcome = "open"
        trigger_kind = str(fire.get("trigger_kind") or "level_retest")
        if fired_at is not None:
            next_skip = min(
                (t for t in skips_by_rule.get(str(fire.get("rule_id")), []) if t >= fired_at),
                default=None,
            )
            if trigger_kind == "invalidation":
                next_flip = min((t for t in flip_times if t >= fired_at), default=None)
                if next_flip is not None and (next_skip is None or next_flip <= next_skip):
                    outcome = "flipped"
                elif next_skip is not None:
                    outcome = "skipped"
                elif resolve_as_of is not None and (
                    (resolve_as_of - fired_at).total_seconds() > _MISSED_GRACE_SECONDS
                ):
                    outcome = "missed"
            else:
                next_check = min((t for t in check_times if t >= fired_at), default=None)
                if next_check is not None and (next_skip is None or next_check <= next_skip):
                    outcome = "checked"
                elif next_skip is not None:
                    outcome = "skipped"
                elif resolve_as_of is not None and (
                    (resolve_as_of - fired_at).total_seconds() > _MISSED_GRACE_SECONDS
                ):
                    outcome = "missed"
        resolved.append({**fire, "outcome": outcome})
    return resolved


def _num(value: Any) -> float | None:
    """Coerce numpy/pandas scalars to plain floats; None stays None."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


        return None


def _location_label(check: dict) -> str:
    """One compact cell: signed distance to VWAP, then position vs the opening range.

    Records written before OR capture carry no ``orb_high``/``orb_low`` keys and
    render without the OR clause; records with no usable numbers render ``-``.
    """
    price = check.get("last_price")
    vwap = check.get("vwap")
    parts: list[str] = []
    if price is not None and vwap is not None:
        delta = round(float(price) - float(vwap), 2)
        parts.append(f"{delta:+.2f} vs VWAP")
    orb_high = check.get("orb_high")
    orb_low = check.get("orb_low")
    if price is not None and orb_high is not None and orb_low is not None:
        if float(price) > float(orb_high):
            parts.append("above OR-H")
        elif float(price) < float(orb_low):
            parts.append("below OR-L")
        else:
            parts.append("in OR")
    return " · ".join(parts) if parts else "-"


def _mr_label(check: dict) -> str:
    """One compact MR cell: 'long ENTRY' when the fade plan fired, 'long watch'
    for an in-zone candidate, '—' otherwise. Records written before MR capture
    carry no ``mr_side`` key and read as '—', never a guess."""
    side = check.get("mr_side")
    if not side:
        return "—"
    return f"{side} ENTRY" if check.get("mr_entry") else f"{side} watch"


def _frame_timeline(day: list[dict]) -> list[str]:
    """Frame label in force at each check, aligned with the day's check order.

    Mirrors :meth:`active_frame`: ``hypothesis`` and ``flip`` records define
    frames in append order (chronological), a later hypothesis re-commit
    supersedes a flip, and all other record kinds are transparent. Flips get
    numbered labels so repeated flips stay distinguishable. Checks logged
    before any frame record get ``-``.
    """
    labels: list[str] = []
    current = "-"
    flips_seen = 0
    for record in day:
        kind = record.get("kind")
        if kind == "hypothesis":
            current = "morning"
        elif kind == "flip":
            flips_seen += 1
            current = f"flip {flips_seen}"
        elif kind == "check":
            labels.append(current)
    return labels


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
            "orb_high": _num(snapshot.mes.opening_range_high),
            "orb_low": _num(snapshot.mes.opening_range_low),
            "mr_side": result.mr_side,
            "mr_entry": bool(result.mr_entry),
            "mr_zone": bool(result.mr_zone),
            "mr_trigger": bool(result.mr_trigger),
            "mr_confirmations": result.mr_confirmations,
            "mr_required": result.mr_required,
            "mr_score": result.mr_score,
            "mr_stop": _num(result.mr_stop),
            "mr_target": _num(result.mr_target),
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
        day_type: str = "",
        bias: str = "",
        machine_clauses: list[dict] | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "kind": "hypothesis",
            "logged_at": datetime.now().isoformat(),
            "session_date": date,
            "hypothesis": hypothesis_markdown,
            "market_context": market_context,
        }
        if day_type:
            record["day_type"] = day_type
        if bias:
            record["bias"] = bias
        if machine_clauses:
            record["machine_clauses"] = list(machine_clauses)
        self._append(date, record)

    def append_flip(
        self,
        date: str,
        *,
        reason: str,
        new_frame: str = "",
        price: float | None = None,
        vwap: float | None = None,
        as_of: datetime | None = None,
    ) -> None:
        """Journal a thesis flip: the morning frame died; record the re-read.

        ``reason`` is the invalidation evidence (what killed the old frame);
        ``new_frame`` is the replacement day-type/bias thesis (markdown, may be
        empty when flipping to "no frame — stand down"). Subsequent checks read
        this frame via :meth:`active_frame`, and ``mes review`` grades both the
        flip's timeliness and any checks that kept arguing the dead frame.
        """
        stamp = as_of or datetime.now()
        self._append(date, {
            "kind": "flip",
            "logged_at": datetime.now().isoformat(),
            "as_of": stamp.isoformat(timespec="minutes"),
            "session_date": date,
            "reason": reason,
            "new_frame": new_frame,
            "price": _num(price),
            "vwap": _num(vwap),
        })

    # --- Standing rules (review -> session loop) ---

    _RULES_FILE = "standing_rules.json"

    def save_standing_rules(
        self,
        rules: list[dict],
        *,
        reviewed_on: str = "",
        blank_day_ack: bool = False,
        blank_day_reason: str = "",
    ) -> None:
        """Persist the latest review's standing rules, superseding earlier sets.

        Atomic write (tmp file + rename) so a mid-write crash cannot leave a
        truncated rule set behind. ``blank_day_ack`` marks an explicit
        "no rules today" acknowledgement so the next session can start ready
        without machine-checkable prescriptions.
        """
        payload = {
            "version": 1,
            "reviewed_on": reviewed_on,
            "rules": list(rules),
            "blank_day_ack": bool(blank_day_ack),
            "blank_day_reason": blank_day_reason or "",
        }
        path = self._dir / self._RULES_FILE
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            logger.warning("MES standing rules write failed for %s: %s", path, exc)

    def ack_blank_standing_rules(self, *, reviewed_on: str, reason: str = "") -> None:
        """Explicit no-rules / blank-day ack — next session is process-ready."""
        self.save_standing_rules(
            [], reviewed_on=reviewed_on, blank_day_ack=True, blank_day_reason=reason,
        )

    def _load_standing_rules_payload(self) -> dict | None:
        path = self._dir / self._RULES_FILE
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("MES standing rules read failed for %s: %s", path, exc)
            return None
        return payload if isinstance(payload, dict) else None

    def active_standing_rules(self, session_date: str) -> list[dict]:
        """Rules still live for ``session_date`` (expired ones are filtered out)."""
        payload = self._load_standing_rules_payload()
        if payload is None:
            return []
        return [
            rule
            for rule in (payload.get("rules") or [])
            if isinstance(rule, dict)
            and (rule.get("expires_on") is None or str(rule.get("expires_on")) >= session_date)
        ]

    def standing_rules_readiness(self, session_date: str) -> dict[str, Any]:
        """Session-start hygiene for standing rules.

        Returns a dict with ``ready`` (bool), ``status``
        (``ready`` / ``missing`` / ``expired`` / ``needs_ack``), ``banner``
        (loud copilot line when not ready), and metadata for panels/tests.
        """
        payload = self._load_standing_rules_payload()
        if payload is None:
            return {
                "ready": False,
                "status": "missing",
                "banner": "RULES EXPIRED / NONE — standing_rules.json missing; "
                          "run `mes review` or `mes rules blank --reason …`",
                "active_count": 0,
                "reviewed_on": "",
                "blank_day_ack": False,
            }
        active = self.active_standing_rules(session_date)
        reviewed_on = str(payload.get("reviewed_on") or "")
        blank_ack = bool(payload.get("blank_day_ack"))
        raw_rules = [r for r in (payload.get("rules") or []) if isinstance(r, dict)]
        if active:
            return {
                "ready": True,
                "status": "ready",
                "banner": "",
                "active_count": len(active),
                "reviewed_on": reviewed_on,
                "blank_day_ack": False,
            }
        if blank_ack and not raw_rules:
            return {
                "ready": True,
                "status": "ready",
                "banner": "",
                "active_count": 0,
                "reviewed_on": reviewed_on,
                "blank_day_ack": True,
            }
        if raw_rules:
            return {
                "ready": False,
                "status": "expired",
                "banner": "RULES EXPIRED / NONE — all standing rules past expires; "
                          "run `mes review` or `mes rules blank --reason …`",
                "active_count": 0,
                "reviewed_on": reviewed_on,
                "blank_day_ack": blank_ack,
            }
        return {
            "ready": False,
            "status": "needs_ack",
            "banner": "RULES EXPIRED / NONE — review left no rules; "
                      "ack with `mes rules blank --reason …` before the session",
            "active_count": 0,
            "reviewed_on": reviewed_on,
            "blank_day_ack": False,
        }

    def frame_machine_clauses(self, date: str) -> list[dict]:
        """Machine clauses from the active frame (morning hypothesis or flip)."""
        frame = self.active_frame(date) or {}
        clauses = frame.get("machine_clauses") or []
        return [c for c in clauses if isinstance(c, dict)]

    def rules_for_session(self, session_date: str) -> list[dict]:
        """Standing rules plus invalidation clauses seeded from today's frame."""
        rules = list(self.active_standing_rules(session_date))
        seen_ids = {
            str((r.get("trigger") or {}).get("id") or (r.get("trigger") or {}).get("level") or "")
            for r in rules
        }
        for index, clause in enumerate(self.frame_machine_clauses(session_date), start=1):
            kind = str(clause.get("kind") or "invalidation")
            if kind != "invalidation":
                continue
            clause_id = str(clause.get("id") or f"inv_{index}")
            if clause_id in seen_ids:
                continue
            level = clause.get("level")
            if not level:
                continue
            break_side = clause.get("break_side") or "below"
            rules.append({
                "trigger": {
                    "kind": "invalidation",
                    "id": clause_id,
                    "level": level,
                    "break_side": break_side,
                    "confirmation": clause.get("confirmation") or "none",
                    "tolerance_points": float(clause.get("tolerance_points") or 2.0),
                },
                "requirement": "flip_or_skip",
                "note": str(clause.get("note") or ""),
                "expires_on": None,
            })
            seen_ids.add(clause_id)
        return rules

    def append_rule_fired(
        self, date: str, *, rule_id: str, level: str, confirmation: str,
        tolerance: float, last_price: float, distance: float, as_of: datetime,
        trigger_kind: str = "level_retest",
    ) -> None:
        self._append(date, {
            "kind": "rule_fired",
            "logged_at": datetime.now().isoformat(),
            "as_of": as_of.isoformat(timespec="minutes"),
            "session_date": date,
            "rule_id": rule_id,
            "level": level,
            "confirmation": confirmation,
            "tolerance": _num(tolerance),
            "last_price": _num(last_price),
            "distance": _num(distance),
            "trigger_kind": trigger_kind,
        })

    def append_rule_skip(
        self, date: str, *, rule_id: str, level: str, reason: str,
        last_price: float | None = None, as_of: datetime | None = None,
    ) -> None:
        stamp = as_of or datetime.now()
        self._append(date, {
            "kind": "rule_skip",
            "logged_at": datetime.now().isoformat(),
            "as_of": stamp.isoformat(timespec="minutes"),
            "session_date": date,
            "rule_id": rule_id,
            "level": level,
            "reason": reason,
            "last_price": _num(last_price),
        })

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

    def load_flips(self, date: str) -> list[dict]:
        return [e for e in self.load_day(date) if e.get("kind") == "flip"]

    def active_frame(self, date: str) -> dict | None:
        """The thesis currently in force for a session.

        The last frame-defining record wins: a journaled ``flip`` supersedes the
        morning ``hypothesis``, and a hypothesis re-saved later (a mid-session
        re-commit) supersedes the flip. Records are read in append order, which
        is chronological, so the last frame on the day wins.
        """
        frames = [
            e for e in self.load_day(date) if e.get("kind") in ("hypothesis", "flip")
        ]
        return frames[-1] if frames else None

    def load_rule_fires(self, date: str) -> list[dict]:
        return [e for e in self.load_day(date) if e.get("kind") == "rule_fired"]

    def load_rule_skips(self, date: str) -> list[dict]:
        return [e for e in self.load_day(date) if e.get("kind") == "rule_skip"]

    def rule_compliance(self, date: str, *, resolve_as_of: datetime | None = None) -> dict:
        """Fire accounting for one session.

        Returns ``{"triggered": int, "checked": int, "skipped": int,
        "flipped": int, "missed": int, "open": list[dict], "detail": list[dict]}``
        — every unresolved fire sits in ``open`` until ``resolve_as_of`` pushes
        it past the missed grace (trigger bar + one bar).
        """
        fires = self.load_rule_fires(date)
        checks = self.load_checks(date)
        skips = self.load_rule_skips(date)
        flips = self.load_flips(date)
        resolved = _resolve_fires(
            fires, checks, skips, flips, resolve_as_of=resolve_as_of,
        )
        by_outcome: dict[str, int] = {
            "checked": 0, "skipped": 0, "flipped": 0, "missed": 0, "open": 0,
        }
        for fire in resolved:
            by_outcome[fire["outcome"]] = by_outcome.get(fire["outcome"], 0) + 1
        return {
            "triggered": len(fires),
            "checked": by_outcome.get("checked", 0),
            "skipped": by_outcome.get("skipped", 0),
            "flipped": by_outcome.get("flipped", 0),
            "missed": by_outcome.get("missed", 0),
            "open": [f for f in resolved if f["outcome"] == "open"],
            "detail": resolved,
        }

    def summarize_rule_compliance(self, date: str, *, resolve_as_of: datetime | None = None) -> str:
        """Markdown fire tally: the compliance scoreboard for `mes review`."""
        tally = self.rule_compliance(date, resolve_as_of=resolve_as_of)
        if not tally["detail"]:
            return f"No standing-rule triggers recorded for {date}."
        lines = [
            f"{tally['triggered']} triggered / {tally['checked']} checked / "
            f"{tally['skipped']} skipped / {tally.get('flipped', 0)} flipped / "
            f"{tally['missed']} MISSED",
            "",
            "| Rule | Fired | Outcome |",
            "| --- | --- | --- |",
        ]
        for fire in tally["detail"]:
            as_of = str(fire.get("as_of", ""))
            fired = as_of[11:16] if len(as_of) >= 16 else as_of
            lines.append(
                "| {rule} | {fired} | {outcome} |".format(
                    rule=fire.get("rule_id", "?"), fired=fired, outcome=fire["outcome"],
                )
            )
        return "\n".join(lines)

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
        day = self.load_day(date)
        if not any(r.get("kind") == "check" for r in day):
            return f"No checks logged for {date}."
        frames = iter(_frame_timeline(day))
        rows = [
            "| Time | Side | Frame | Score | Tier | Gates | Tradeable | Location | MR | Verdict |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for check in (r for r in day if r.get("kind") == "check"):
            as_of = str(check.get("as_of", ""))
            time_label = as_of[11:16] if len(as_of) >= 16 else as_of
            rows.append(
                "| {time} | {side} | {frame} | {score}/{max_score} | {tier} | {gates} | {tradeable} | {location} | {mr} | {verdict} |".format(
                    time=time_label,
                    side=check.get("side", "?"),
                    frame=next(frames),
                    score=check.get("score", 0),
                    max_score=check.get("max_score", 0),
                    tier=check.get("tier", "?"),
                    gates="pass" if check.get("gates_ok") else "fail",
                    tradeable="yes" if check.get("tradeable") else "no",
                    location=_location_label(check),
                    mr=_mr_label(check),
                    verdict=_first_line(check.get("verdict", "")) or "-",
                )
            )
        return "\n".join(rows)

    def summarize_flips(self, date: str) -> str:
        """Markdown flip timeline: what superseded the morning frame, and why.

        Empty string on a flip-free day so callers can omit the section
        instead of printing a placeholder table.
        """
        flips = self.load_flips(date)
        if not flips:
            return ""
        lines = [
            "| Time | New Frame | What Killed The Old Frame |",
            "| --- | --- | --- |",
        ]
        for flip in flips:
            as_of = str(flip.get("as_of", ""))
            time_label = as_of[11:16] if len(as_of) >= 16 else as_of
            lines.append(
                "| {time} | {frame} | {reason} |".format(
                    time=time_label,
                    frame=_first_line(str(flip.get("new_frame", ""))) or "-",
                    reason=flip.get("reason", "") or "-",
                )
            )
        return "\n".join(lines)

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
        ladder_fired: dict[str, str] | None = None,
        remaining: int | None = None,
        realized_r: float | None = None,
    ) -> None:
        """Record a ladder event or manual adjustment.

        ``ladder_fired`` / ``remaining`` / ``realized_r`` carry the manager's
        state transitions (BE/partial/trail markers, fills, banked R) so that
        :meth:`find_open_trade` can replay them and the ladder never re-fires
        an event it already processed — across ticks *and* restarts.
        """
        self._append(date, {
            "kind": "trade_adjusted",
            "logged_at": datetime.now().isoformat(),
            "session_date": date,
            "as_of": as_of,
            "stop": stop,
            "note": note,
            "fired": dict(ladder_fired) if ladder_fired else None,
            "remaining": remaining,
            "realized_r": realized_r,
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
                # Ladder state transitions (BE/partial/trail markers, fills,
                # banked R) persist here so replay matches the ladder: a fill
                # already journaled this session must not re-fire on rebuild.
                for name, ts in (record.get("fired") or {}).items():
                    trade.fired.setdefault(str(name), str(ts))
                if record.get("remaining") is not None:
                    trade.remaining = int(record["remaining"])
                if record.get("realized_r") is not None:
                    trade.realized_r = float(record["realized_r"])
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
