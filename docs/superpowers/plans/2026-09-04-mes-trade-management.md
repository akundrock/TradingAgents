# MES Trade Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add post-entry trade management to the MES copilot: a deterministic exit ladder behind `mes trade enter/status/close/adjust`, a journal-tracked trade lifecycle (`trade_opened`/`trade_adjusted`/`trade_closed`), an advisory-only LLM manager, and per-trade grading in `mes review`.

**Architecture:** New pure module `tradingagents/mes/management.py` (no LLM, no I/O — same pattern as `checklist.py`/`radar.py`) evaluates a declared `OpenTrade` against a live `MesSnapshot` and returns `(updated_trade, MgmtReport)`. Lifecycle records live in the existing append-only `MesJournal` JSONL; open-trade state is reconstructed by replaying the day's file. A `mes trade` Typer sub-app drives it; a light LLM manager adds advisory text it can never turn into level changes; `mes review` gains a Trades section.

**Tech Stack:** Python 3.11+, dataclasses, Typer, Rich, pytest with `@pytest.mark.unit`, existing `tests/mes_factories.py` builders.

**Spec:** `docs/superpowers/specs/2026-09-04-mes-trade-management-design.md`. Two refinements made during planning: `evaluate_management` returns `(OpenTrade, MgmtReport)` so the CLI can persist the updated trade, and `OpenTrade` carries `realized_r` for partial-fill accounting.

## Global Constraints

- Work on branch `mes-trade-management` (already checked out).
- Several files carry **pre-existing uncommitted radar work** until Task 0 lands it (`cli/mes.py`, `tradingagents/mes/__init__.py`, `tests/test_mes_agents.py`, `tests/test_mes_levels.py`, `tests/test_mes_radar.py`, `tradingagents/agents/mes/gatekeeper_agent.py`, `tradingagents/agents/schemas.py`, `tradingagents/mes/levels.py`, `tradingagents/mes/radar.py`). Never `git add -A` or `git add .`; stage only the files listed in each task's commit step.
- Config fields and defaults (verbatim): `breakeven_at_r=1.0`, `breakeven_cushion_ticks=1.0`, `partial_at_r=1.5`, `partial_fraction=0.5`, `trail_atr_multiple=1.0`, `exit_on_confluence_loss=False`, `time_stop_buffer_minutes=10`.
- R is anchored at entry: `initial_risk_points = abs(entry - initial_stop)`; all R math uses it, never the current stop.
- Stops only tighten (long: stop never decreases; short: never increases) — enforced via `_tighter` in `evaluate_management`.
- Events are idempotent: the `fired` dict keys event names; a fired trigger never re-fires. The trail is the exception: it may improve the stop every bar (not `fired`-gated).
- A stop or target touched intrabar fills at the bar **open price** when it gaps through the level, otherwise at the level.
- Evaluation happens on the **latest bar close** only.
- 1 contract: the partial degrades to tightening the stop to lock `partial_fraction` of the open gain.
- One open trade at a time; `enter` refuses while one is open.
- The LLM trade manager produces advisory text only; it is never parsed into state.
- Tests reuse `tests/mes_factories.py` builders (SESSION_DATE 2026-03-30, DEFAULT_AS_OF 11:00 ET). Every test is marked `@pytest.mark.unit`.
- Run tests from the repo root: `python -m pytest <path> -v`.

## File Structure (locked decomposition)

| File | Responsibility |
|---|---|
| `tradingagents/mes/management.py` (new) | Pure ladder: `OpenTrade`, `MgmtEvent`, `MgmtReport`, `evaluate_management`, helpers. No I/O, no Rich. |
| `tradingagents/mes/config.py` (modify) | Ladder threshold fields on `MesChecklistConfig`. |
| `tradingagents/mes/journal.py` (modify) | Trade lifecycle records + replay (`append_trade_opened/adjusted/closed`, `load_trades`, `find_open_trade`, `summarize_trades`). |
| `tradingagents/agents/mes/manager_agent.py` (new) | Advisory-only LLM trade manager (mirrors `gatekeeper_agent.py`). |
| `tradingagents/agents/mes/review_agent.py` (modify) | Accept optional `trades_summary` context. |
| `cli/mes.py` (modify) | `mes trade enter/status/close/adjust` command group + Rich rendering. |
| `tradingagents/mes/__init__.py` (modify) | Re-export management types. |
| `tests/test_mes_management.py` (new), `tests/test_mes_journal.py` (modify), `tests/test_mes_agents.py` (modify), `tests/test_mes_cli_trade.py` (new) | Per-task coverage. |

---

### Task 0: Commit the pre-existing radar work

**Files:**
- Stage only (already modified/created, do not edit): `cli/mes.py`, `tests/test_mes_agents.py`, `tests/test_mes_levels.py`, `tests/test_mes_radar.py`, `tradingagents/agents/mes/gatekeeper_agent.py`, `tradingagents/agents/schemas.py`, `tradingagents/mes/__init__.py`, `tradingagents/mes/levels.py`, `tradingagents/mes/radar.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a clean working tree so every later task's `git add <file>` stages exactly the intended files.

- [ ] **Step 1: Verify the working tree matches the expected radar state**

Run: `git status --short`
Expected: modified `cli/mes.py`, `tests/test_mes_agents.py`, `tradingagents/agents/mes/gatekeeper_agent.py`, `tradingagents/agents/schemas.py`, `tradingagents/mes/__init__.py`; untracked `tests/test_mes_levels.py`, `tests/test_mes_radar.py`, `tradingagents/mes/levels.py`, `tradingagents/mes/radar.py`. If anything else appears, stop and ask.

- [ ] **Step 2: Run the MES test suite to confirm the radar work is green**

Run: `python -m pytest tests/ -k mes -q`
Expected: all pass.

- [ ] **Step 3: Commit (explicit paths only)**

```bash
git add cli/mes.py tests/test_mes_agents.py tests/test_mes_levels.py tests/test_mes_radar.py \
  tradingagents/agents/mes/gatekeeper_agent.py tradingagents/agents/schemas.py \
  tradingagents/mes/__init__.py tradingagents/mes/levels.py tradingagents/mes/radar.py
git commit -m "feat(mes): add proximity radar state machine and trade-level hints"
```

---

### Task 1: Management config fields

**Files:**
- Modify: `tradingagents/mes/config.py` (fields on `MesChecklistConfig`)
- Test: `tests/test_mes_config.py`

**Interfaces:**
- Consumes: existing `MesChecklistConfig` and the generic `load_mes_config` env-override loop (no machinery changes).
- Produces: `breakeven_at_r: float = 1.0`, `breakeven_cushion_ticks: float = 1.0`, `partial_at_r: float = 1.5`, `partial_fraction: float = 0.5`, `trail_atr_multiple: float = 1.0`, `exit_on_confluence_loss: bool = False`, `time_stop_buffer_minutes: int = 10`. All later tasks read these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mes_config.py` (add `import pytest` and `from tradingagents.mes.config import load_mes_config` to the imports if missing):

```python
@pytest.mark.unit
def test_management_ladder_defaults():
    cfg = MesChecklistConfig()
    assert cfg.breakeven_at_r == 1.0
    assert cfg.breakeven_cushion_ticks == 1.0
    assert cfg.partial_at_r == 1.5
    assert cfg.partial_fraction == 0.5
    assert cfg.trail_atr_multiple == 1.0
    assert cfg.exit_on_confluence_loss is False
    assert cfg.time_stop_buffer_minutes == 10


@pytest.mark.unit
def test_management_ladder_env_overrides(monkeypatch):
@pytest.mark.unit
def test_management_ladder_env_overrides(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_MES_BREAKEVEN_AT_R", "0.8")
    monkeypatch.setenv("TRADINGAGENTS_MES_PARTIAL_FRACTION", "0.3")
    cfg = load_mes_config()
    assert cfg.breakeven_at_r == 0.8
    assert cfg.partial_fraction == 0.3  # env override applied over the 0.5 default
```

(Both overrides must assert through: the env loop is generic and applies every
`TRADINGAGENTS_MES_*` var, so setting a var and asserting the default would
contradict `load_mes_config` itself.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mes_config.py -v -k management`
Expected: FAIL with `AttributeError: ... no attribute 'breakeven_at_r'`

- [ ] **Step 3: Write minimal implementation**

In `tradingagents/mes/config.py`, inside `MesChecklistConfig`, immediately after the `max_contracts: int = 10` line and before the blank line preceding `spy_symbol`, add:

```python
    # Trade management ladder (mes trade enter/status/close).
    breakeven_at_r: float = 1.0
    breakeven_cushion_ticks: float = 1.0
    partial_at_r: float = 1.5
    partial_fraction: float = 0.5
    trail_atr_multiple: float = 1.0
    exit_on_confluence_loss: bool = False
    time_stop_buffer_minutes: int = 10
```

No other changes: the env-override loop in `load_mes_config` is generic over dataclass fields.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mes_config.py tests/test_env_overrides.py -v`
Expected: PASS (all tests in both files).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/mes/config.py tests/test_mes_config.py
git commit -m "feat(mes): add trade-management ladder config fields"
```

---

### Task 2: `management.py` core — types, R math, fills, idempotency

**Files:**
- Create: `tradingagents/mes/management.py`
- Create: `tests/test_mes_management.py`

**Interfaces:**
- Consumes: `ChecklistResult` (`tradingagents/mes/checklist.py`), `MesSnapshot` (`tradingagents/mes/snapshot.py`; `.mes.close`, `.mes.last` bar with `.open/.high/.low/.close`), `tests/mes_factories.py` (`make_mes_series`, `make_snapshot`, `DEFAULT_AS_OF`).
- Produces (all later tasks rely on these exact names):

```python
@dataclass
class OpenTrade:
    side: str                     # "long" | "short"
    contracts: int
    remaining: int
    entry: float
    stop: float
    initial_stop: float
    target: float | None
    entry_time: datetime
    initial_risk_points: float
    fired: dict[str, str]         # event -> ISO timestamp first fired
    manual_events: list[str]      # freeform notes
    realized_r: float = 0.0       # R banked so far (partials / stop-outs)

@dataclass
class MgmtEvent:
    name: str                     # breakeven | partial | trail | time_stop | stopped_out | target
    as_of: datetime
    detail: str

@dataclass
class MgmtReport:
    r_now: float
    mfe_r: float
    mae_r: float
    stop: float
    target: float | None
    events: list[MgmtEvent]
    next_event: str
    recommendation: str           # HOLD | TIGHTEN | EXIT | FLATTEN | CLOSED
    reasons: list[str]

def evaluate_management(snapshot, result, trade, cfg=None) -> tuple[OpenTrade, MgmtReport]
def _sign(side: str) -> float
def _r_of(price: float, trade: OpenTrade) -> float
def _tighter(old: float, candidate: float, side: str) -> float
def _round_tick(price: float, tick: float) -> float
def _stop_fill(trade, bar) -> float | None
def _target_fill(trade, bar) -> float | None
def excursions_r(snapshot, trade) -> tuple[float, float]
```

This task delivers types, R accounting, gap-aware fill resolution, MFE/MAE, and the stop/target close branches. Task 3 replaces `evaluate_management` with the full trigger pipeline.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mes_management.py` with exactly this content:

```python
"""Tests for tradingagents.mes.management — the post-entry ladder."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from tradingagents.mes.checklist import ChecklistResult
from tradingagents.mes.management import (
    MgmtEvent,
    MgmtReport,
    OpenTrade,
    evaluate_management,
)
from tests.mes_factories import DEFAULT_AS_OF, make_mes_series, make_snapshot

# entry 100.00, initial stop 98.00 -> 1R = 2.0 points
ENTRY = 100.0
STOP = 98.0
TARGET = 102.0
RISK = 2.0


def make_trade(**overrides) -> OpenTrade:
    trade = OpenTrade(
        side="long",
        contracts=1,
        remaining=1,
        entry=100.0,
        stop=98.0,
        initial_stop=98.0,
        target=102.0,
        entry_time=datetime(2026, 3, 30, 10, 0),
        initial_risk_points=2.0,
        fired={},
        manual_events=[],
        realized_r=0.0,
    )
    return replace(trade, **overrides)


def snap_at(
    price: float,
    *,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    atr: float = 1.0,
    as_of=None,
):
    """Snapshot whose single MES bar closes at `price` with controllable OHLC."""
    open_ = price if open_ is None else open_
    high = max(open_, price) if high is None else high
    low = min(open_, price) if low is None else low
    mes = make_mes_series(
        close=price,
        atr=atr,
        bar_kwargs={"open_": open_, "high": high, "low": low, "close": price},
    )
    return make_snapshot(mes=mes, as_of=as_of)


def result_at(price: float, *, side: str = "long", atr: float = 1.0):
    return ChecklistResult(
        as_of_label="11:00",
        side=side,
        direction=side,
        last_price=price,
        vwap=99.0,
        atr=atr,
        gates_ok=True,
        score_ok=True,
        confirmations=5,
        required=4,
        tier="standard",
        spy_confirmations=4,
        spy_confluence_ok=True,
    )


@pytest.mark.unit
def test_r_now_long_positive_above_entry():
    _, report = evaluate_management(snap_at(101.0), result_at(101.0), make_trade())
    assert report.r_now == pytest.approx(0.5)  # +1.0 pts over a 2.0-pt risk


@pytest.mark.unit
def test_r_now_short_side_sign():
    # Short fixtures need mirrored levels: stop above entry, target below.
    trade = make_trade(side="short", stop=102.0, initial_stop=102.0, target=98.0)
    _, report = evaluate_management(snap_at(99.0), result_at(99.0), trade)
    assert report.r_now == pytest.approx(0.5)  # 1.0 favorable pt / 2.0


@pytest.mark.unit
def test_r_uses_initial_risk_not_current_stop():
    trade = make_trade(fired={"breakeven": "2026-03-30T10:30"}, stop=100.25)
    _, report = evaluate_management(snap_at(101.0), result_at(101.0), trade)
    assert report.r_now == pytest.approx(0.5)


@pytest.mark.unit
def test_hold_before_any_trigger():
    _, report = evaluate_management(snap_at(100.5), result_at(100.5), make_trade())
    assert report.recommendation == "HOLD"
    assert report.events == []
    assert report.next_event.startswith("BE stop")


@pytest.mark.unit
def test_input_trade_is_not_mutated():
    trade = make_trade()
    evaluate_management(snap_at(101.5), result_at(101.5), trade)
    assert trade.stop == 98.0
    assert trade.fired == {}


@pytest.mark.unit
def test_input_trade_fired_dict_not_mutated_on_fill():
    """replace() is shallow: fill paths must not write through to the input."""
    trade = make_trade()
    evaluate_management(
        snap_at(97.9, open_=99.5, high=100.2, low=97.9), result_at(97.9), make_trade()
    )
    assert trade.fired == {}
    trade2 = make_trade()
    evaluate_management(snap_at(102.0, high=102.4), result_at(102.0), trade2 := make_trade())
    assert trade2.fired == {}


@pytest.mark.unit
def test_stop_touched_closes_at_level_price():
    updated, report = evaluate_management(
        snap_at(97.9, open_=99.5, high=100.2, low=97.9), result_at(97.9), make_trade()
    )
    assert updated.remaining == 0
    assert updated.fired["stopped_out"].startswith("2026-03-30T11:00")
    assert report.recommendation == "CLOSED"
    assert updated.realized_r == pytest.approx(-1.0)  # filled at the 98.00 stop


@pytest.mark.unit
def test_stop_gap_fills_at_bar_open():
    """Open 97.00 gaps through the 98.00 stop: fill at 97.00, not 98.00."""
    updated, report = evaluate_management(
        snap_at(97.0, open_=97.0, low=96.8), result_at(97.0), make_trade()
    )
    assert updated.remaining == 0
    assert updated.realized_r == pytest.approx(-1.5)  # 97.0 vs entry 100.0 over 2.0 risk
    assert report.events[0].name == "stopped_out"
    assert "filled at 97.00" in report.events[0].detail


@pytest.mark.unit
def test_target_hit_closes_at_target_price():
    updated, report = evaluate_management(
        snap_at(102.0, high=102.4), result_at(102.0), make_trade()
    )
    assert updated.remaining == 0
    assert updated.realized_r == pytest.approx(1.0)  # 2.0 pts / 2.0-pt risk
    assert report.events[0].name == "target"
    assert report.recommendation == "CLOSED"


@pytest.mark.unit
def test_mfe_mae_cover_bars_since_entry():
    trade = make_trade()
    _, report = evaluate_management(snap_at(100.5), result_at(100.5), trade)
    assert report.mfe_r >= report.r_now
    assert report.mae_r <= 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_management.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingagents.mes.management'`

- [ ] **Step 3: Write the implementation**

Create `tradingagents/mes/management.py` with exactly this content:

```python
"""Post-entry trade management: the deterministic exit ladder for /MES.

Pure functions over an already-evaluated :class:`~.checklist.ChecklistResult`
and a declared :class:`OpenTrade`. The ladder decides every hard level move
(breakeven, partial, trail, time stop); the CLI and the LLM manager may
comment on the plan but never move a level. Stops only ever tighten; events
are idempotent via the trade's ``fired`` dict; a stop or target touched
intrabar fills at the bar's open when it gaps through the level.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from .checklist import ChecklistResult
from .config import MesChecklistConfig
from .snapshot import MesSnapshot


@dataclass
class OpenTrade:
    """A declared position being managed by the copilot."""

    side: str  # "long" | "short"
    contracts: int
    remaining: int
    entry: float
    stop: float
    initial_stop: float
    target: float | None
    entry_time: datetime
    initial_risk_points: float
    fired: dict[str, str] = field(default_factory=dict)
    """Event name -> ISO timestamp it first fired. Idempotency guard."""
    manual_events: list[str] = field(default_factory=list)
    realized_r: float = 0.0
    """R banked by partial closes / stop-outs so far."""

    @property
    def is_open(self) -> bool:
        return self.remaining > 0


@dataclass
class MgmtEvent:
    name: str
    as_of: datetime
    detail: str


@dataclass
class MgmtReport:
    r_now: float
    mfe_r: float
    mae_r: float
    stop: float
    target: float | None
    events: list[MgmtEvent] = field(default_factory=list)
    next_event: str = ""
    recommendation: str = "HOLD"
    reasons: list[str] = field(default_factory=list)


def _sign(side: str) -> float:
    return 1.0 if side == "long" else -1.0


def _tighter(old: float, candidate: float, side: str) -> float:
    """The more protective of two stop levels for the trade's side."""
    return max(old, candidate) if side == "long" else min(old, candidate)


def _round_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 4)


def _r_of(price: float, trade: OpenTrade) -> float:
    """Signed open R of ``price`` against the trade's initial risk."""
    risk = trade.initial_risk_points or 1.0
    return round(_sign(trade.side) * (price - trade.entry) / risk, 4)


def _stop_fill(trade: OpenTrade, bar) -> float | None:
    """Fill price when the bar touched the stop; gaps fill at the open."""
    if trade.side == "long":
        return min(trade.stop, bar.open) if bar.low <= trade.stop else None
    return max(trade.stop, bar.open) if bar.high >= trade.stop else None


def _target_fill(trade: OpenTrade, bar) -> float | None:
    if trade.target is None:
        return None
    if trade.side == "long":
        return max(trade.target, bar.open) if bar.high >= trade.target else None
    return min(trade.target, bar.open) if bar.low <= trade.target else None


def excursions_r(snapshot: MesSnapshot, trade: OpenTrade) -> tuple[float, float]:
    """(mfe_r, mae_r) over the bars since entry; MAE <= 0 by construction."""
    sign = _sign(trade.side)
    risk = trade.initial_risk_points or 1.0
    mfe = 0.0
    mae = 0.0
    for bar in snapshot.mes.bars:
        if bar.timestamp < trade.entry_time:
            continue
        if trade.side == "long":
            fav, adv = bar.high - trade.entry, bar.low - trade.entry
        else:
            fav, adv = trade.entry - bar.low, trade.entry - bar.high
        mfe = max(mfe, fav / risk)
        mae = min(mae, adv / risk)
    return round(mfe, 4), round(mae, 4)


def _next_event(cfg, trade: OpenTrade) -> str:
    if trade.remaining <= 0:
        return ""
    if "breakeven" not in trade.fired:
        return f"BE stop at +{cfg.breakeven_at_r:g}R"
    if "partial" not in trade.fired:
        return f"partial at +{cfg.partial_at_r:g}R"
    return f"trail {cfg.trail_atr_multiple:g}xATR"


def evaluate_management(
    snapshot: MesSnapshot,
    result: ChecklistResult,
    trade: OpenTrade,
    cfg: MesChecklistConfig | None = None,
) -> tuple[OpenTrade, MgmtReport]:
    """Evaluate the ladder on the latest bar close. Returns (updated_trade, report)."""
    cfg = cfg or snapshot.config
    bar = snapshot.mes.last
    updated = replace(trade)
    # replace() is a shallow copy: detach the nested mutables so fill-path
    # writes never leak into the caller's trade.
    updated.fired = dict(trade.fired)
    updated.manual_events = list(trade.manual_events)
    events: list[MgmtEvent] = []
    reasons: list[str] = []

    if updated.remaining <= 0:
        return updated, MgmtReport(
            r_now=0.0, mfe_r=0.0, mae_r=0.0, stop=updated.stop,
            target=updated.target, events=[], next_event="",
            recommendation="CLOSED", reasons=["position already closed"],
        )

    r_now = _r_of(snapshot.mes.close, updated)
    mfe_r, mae_r = excursions_r(snapshot, updated)
    stop_fill = _stop_fill(updated, bar)
    target_fill = _target_fill(updated, bar)

    if stop_fill is not None:
        updated.realized_r = round(
            updated.realized_r + updated.remaining * _r_of(stop_fill, updated), 4
        )
        updated.remaining = 0
        updated.fired.setdefault("stopped_out", snapshot.as_of.isoformat(timespec="minutes"))
        return updated, MgmtReport(
            r_now=0.0, mfe_r=mfe_r, mae_r=mae_r, stop=updated.stop,
            target=updated.target,
            events=[MgmtEvent("stopped_out", snapshot.as_of, f"stop filled at {stop_fill:.2f}")],
            next_event="",
            recommendation="CLOSED",
            reasons=[f"stop touched; filled at {stop_fill:.2f}"],
        )

    if target_fill is not None:
        updated.realized_r = round(
            updated.realized_r + updated.remaining * _r_of(target_fill, updated), 4
        )
        updated.remaining = 0
        updated.fired.setdefault("target", snapshot.as_of.isoformat(timespec="minutes"))
        return updated, MgmtReport(
            r_now=0.0, mfe_r=mfe_r, mae_r=mae_r, stop=updated.stop,
            target=updated.target,
            events=[MgmtEvent("target", snapshot.as_of, f"target filled at {target_fill:.2f}")],
            next_event="",
            recommendation="CLOSED",
            reasons=[f"target hit at {target_fill:.2f}"],
        )

    # No fill this bar: Task 3's ladder triggers run here. Task 2 reports HOLD.
    return updated, MgmtReport(
        r_now=r_now, mfe_r=mfe_r, mae_r=mae_r, stop=updated.stop,
        target=updated.target, events=events,
        next_event=_next_event(cfg, updated),
        recommendation="HOLD",
        reasons=reasons,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_management.py -v`
Expected: PASS (all 9 tests).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/mes/management.py tests/test_mes_management.py
git commit -m "feat(mes): add trade management core types, fills, and R accounting"
```

---

### Task 3: Ladder triggers — breakeven, partial, trail, time stop, confluence flip

**Files:**
- Modify: `tradingagents/mes/management.py` (extend `evaluate_management`)
- Modify: `tests/test_mes_management.py` (append tests)

**Interfaces:**
- Consumes: Task 2's skeleton (locals `updated`, `bar`, `price`, `r_now`, `mfe_r`, `mae_r`, `events`, `reasons`, `cfg`), helpers `_tighter` / `_round_tick`, Task 1 config fields.
- Produces: full ladder semantics — `breakeven`, `partial` (with 1-contract degradation), `trail`, `time_stop`, confluence-flip advisory/hard-exit; `recommendation` in `HOLD / EXIT / FLATTEN / CLOSED`; `next_event` progression.

Trigger semantics (normative — the tests are the arbiter):

1. **Breakeven** — when `r_now >= cfg.breakeven_at_r` and `"breakeven" not in fired`: candidate stop = `entry ± breakeven_cushion_ticks × mes_tick_size` (long: +, short: −); apply through `_tighter`; record event only if the stop actually moved; always set `fired["breakeven"]`.
2. **Partial** — when `"breakeven" in fired` and `"partial" not in fired` and `r_now >= cfg.partial_at_r`. Multi-contract: `closed = max(1, round(remaining * partial_fraction))`; `realized_r += closed * r_now`; `remaining -= closed`. Single contract: stop tightens to `entry ± r_now × partial_fraction × initial_risk_points` and the event detail says `1-contract degradation`. `"partial"` recorded in `fired` either way.
3. **Trail** (only when `"partial" in fired` and remaining > 0 and ATR ready; every bar, never looser): long candidate = `max(close since entry) - trail_atr_multiple × ATR`; short mirror; applied via `_tighter`, rounded to tick; event only when the stop moved.
4. **Time stop** — when `as_of >= exit_time - time_stop_buffer_minutes`: close remaining at `price` (credit `realized_r += remaining × r_now`), event `time_stop`, `recommendation == "FLATTEN"`.
5. **Confluence flip** — when `not result.spy_confluence_ok`, or (`not result.tradeable` and `result.gates_ok`), or (`result.tradeable` and `result.side != trade.side`): append reason, `recommendation == "EXIT"`; if `cfg.exit_on_confluence_loss`, close remaining at `price` (event name `confluence_exit`, `recommendation == "CLOSED"`).
6. Ordering on one bar: stop fill → target fill → breakeven → partial → trail → time stop → confluence. A `FLATTEN`/`CLOSED` return short-circuits everything after it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mes_management.py` (add `from tradingagents.mes.config import load_mes_config` to imports):

```python
# ---------------------------------------------------------------------------
# Ladder triggers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_breakeven_fires_at_1r():
    """+1.0R close moves the stop to entry + 1 tick cushion (0.25)."""
    trade = make_trade(target=None)  # target None: otherwise the fill branch closes first
    updated, report = evaluate_management(snap_at(102.0), result_at(102.0), trade)
    assert "breakeven" in updated.fired
    assert updated.stop == pytest.approx(100.25)  # entry + 1 tick
    assert report.events[0].name == "breakeven"
    assert "stop 98.00 -> 100.25" in report.events[0].detail


@pytest.mark.unit
def test_breakeven_does_not_refire():
    trade = make_trade(fired={"breakeven": "2026-03-30T10:30"}, stop=100.25, target=None)
    updated, report = evaluate_management(snap_at(102.0), result_at(102.0), trade)
    assert [e.name for e in report.events if e.name == "breakeven"] == []
    assert updated.stop == 100.25


@pytest.mark.unit
def test_stop_never_loosens_via_trail():
    """Trail candidate below the current stop never loosens it."""
    trade = make_trade(
        fired={"breakeven": "10:30", "partial": "10:35"},
        stop=101.5, target=None,
        entry_time=DEFAULT_AS_OF.replace(hour=9, minute=30),
    )
    # highest close 101.5 - 1.0 ATR = 100.5 -> candidate looser than current stop 101.5
    updated, _ = evaluate_management(snap_at(101.5), result_at(101.5), trade)
    assert updated.stop == pytest.approx(101.5)


@pytest.mark.unit
def test_partial_single_contract_degrades_to_stop_lock():
    trade = make_trade(fired={"breakeven": "10:30"}, stop=100.25, target=None)
    # +1.5R exactly: partial fires; 1 contract -> tighten stop, not sell
    updated, report = evaluate_management(snap_at(103.0), result_at(103.0), trade)
    assert "partial" in updated.fired
    assert updated.remaining == 1
    # lock = entry + r_now(1.5) * 0.5 * risk(2.0) = 101.50
    assert updated.stop == pytest.approx(101.5)
    assert any("1-contract" in e.detail for e in report.events)


@pytest.mark.unit
@pytest.mark.unit
def test_partial_multi_contract_reduces_remaining():
    trade = make_trade(contracts=2, remaining=2, target=None)
    updated, report = evaluate_management(snap_at(103.0), result_at(103.0), trade)
    assert updated.remaining == 1
    assert updated.realized_r == pytest.approx(1.5)  # 1 closed at +1.5R (per-contract credit)
    assert "partial" in [e.name for e in report.events]


@pytest.mark.unit
def test_trail_rides_atr_after_partial():
    trade = make_trade(
        fired={"breakeven": "10:30", "partial": "10:40"},
        stop=100.5, target=None,
        entry_time=DEFAULT_AS_OF.replace(hour=9, minute=30),
    )
    # highest close since entry = 103.25; trail = 103.25 - 1.0*1.0 = 102.25
    updated, _ = evaluate_management(snap_at(103.25), result_at(103.25), trade)
    assert updated.stop == pytest.approx(102.25)


@pytest.mark.unit
def test_time_stop_flattens_before_exit():
    cfg = load_mes_config({"exit_time": "11:15", "time_stop_buffer_minutes": 10})
    as_of = DEFAULT_AS_OF.replace(hour=11, minute=5)  # 11:15 - 10 min
    updated, report = evaluate_management(
        snap_at(100.2, as_of=as_of), result_at(100.2), make_trade(target=None), cfg
    )
    assert report.recommendation == "FLATTEN"
    assert report.events[0].name == "time_stop"
    assert updated.remaining == 0


@pytest.mark.unit
def test_confluence_flip_is_advisory_by_default():
    trade = make_trade(target=None)
    result = result_at(100.5)
    result.spy_confluence_ok = False
    _, report = evaluate_management(snap_at(100.5), result, trade)
    assert report.recommendation == "EXIT"
    assert any("confluence" in r.lower() for r in report.reasons)


@pytest.mark.unit
def test_exit_on_confluence_loss_hard_closes():
    cfg = load_mes_config({"exit_on_confluence_loss": True})
    result = result_at(100.5)
    result.spy_confluence_ok = False
    updated, report = evaluate_management(snap_at(100.5), result, make_trade(target=None), cfg)
    assert report.recommendation == "CLOSED"
    assert updated.remaining == 0
    assert "confluence_exit" in updated.fired
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_management.py -v`
Expected: the new ladder tests FAIL; the nine Task 2 tests still PASS.

- [ ] **Step 3: Implement the triggers**

In `tradingagents/mes/management.py`, replace the final block of `evaluate_management` (the `# No fill this bar:` comment through the trailing `return`) with:

```python
    # ---- Ladder triggers, evaluated in order on this bar close ----

    # 1. Breakeven: open profit at or past the configured R.
    if "breakeven" not in updated.fired and r_now >= cfg.breakeven_at_r:
        cushion = cfg.breakeven_cushion_ticks * cfg.mes_tick_size
        be_stop = updated.entry + cushion if updated.side == "long" else updated.entry - cushion
        new_stop = _tighter(updated.stop, _round_tick(be_stop, cfg.mes_tick_size), updated.side)
        if new_stop != updated.stop:
            events.append(MgmtEvent(
                "breakeven", snapshot.as_of,
                f"stop {updated.stop:.2f} -> {new_stop:.2f} (BE at +{r_now:.2f}R)",
            ))
            updated.stop = new_stop
        updated.fired["breakeven"] = snapshot.as_of.isoformat(timespec="minutes")

    # 2. Partial: bank a fraction of the position, or lock the gain with 1 contract.
    if (
        "breakeven" in updated.fired
        and "partial" not in updated.fired
        and r_now >= cfg.partial_at_r
    ):
        if updated.remaining > 1:
            closed = max(1, round(updated.remaining * cfg.partial_fraction))
            updated.realized_r = round(updated.realized_r + closed * r_now, 4)
            updated.remaining -= closed
            events.append(MgmtEvent(
                "partial", snapshot.as_of,
                f"closed {closed} contract(s) at +{r_now:g}R; {updated.remaining} remain",
            ))
        else:
            lock = updated.entry + _sign(updated.side) * (
                r_now * cfg.partial_fraction
            ) * updated.initial_risk_points
            new_stop = _tighter(
                updated.stop, _round_tick(lock, cfg.mes_tick_size), updated.side
            )
            if new_stop != updated.stop:
                events.append(MgmtEvent(
                    "partial", snapshot.as_of,
                    f"1-contract degradation: stop -> {new_stop:.2f} "
                    f"locking {cfg.partial_fraction:.0%} of the open gain",
                ))
            updated.stop = new_stop
        updated.fired["partial"] = snapshot.as_of.isoformat(timespec="minutes")

    # 3. Trail after the partial: every bar, never looser. Gated on the PRE-call
    # fired dict (trade, not updated) so the trail starts the bar AFTER the
    # partial fires, never on the same bar as a partial/BE move.
    if "partial" in trade.fired and updated.remaining > 0 and snapshot.mes.atr_ready:
        closes_since_entry = [
            b.close for b in snapshot.mes.bars if b.timestamp >= updated.entry_time
        ]
        if updated.side == "long":
            candidate = max(closes_since_entry) - snapshot.mes.atr * cfg.trail_atr_multiple
        else:
            candidate = min(closes_since_entry) + snapshot.mes.atr * cfg.trail_atr_multiple
        trail_stop = _tighter(updated.stop, _round_tick(candidate, cfg.mes_tick_size), updated.side)
        if trail_stop != updated.stop:
            events.append(MgmtEvent(
                "trail", snapshot.as_of,
                f"stop {updated.stop:.2f} -> {trail_stop:.2f} ({cfg.trail_atr_multiple:g}xATR)",
            ))
            updated.stop = trail_stop

    # 4. Time stop: flatten before session exit regardless of P&L.
    hour, minute = divmod(int(cfg.exit_time.replace(":", "")), 100)
    flatten = snapshot.as_of.replace(hour=hour, minute=minute)
    if snapshot.as_of >= flatten - timedelta(minutes=cfg.time_stop_buffer_minutes):
        updated.realized_r = round(
            updated.realized_r + updated.remaining * r_now, 4
        )
        updated.remaining = 0
        updated.fired.setdefault("time_stop", snapshot.as_of.isoformat(timespec="minutes"))
        return updated, MgmtReport(
            r_now=r_now, mfe_r=mfe_r, mae_r=mae_r, stop=updated.stop,
            target=updated.target, events=events + [
                MgmtEvent("time_stop", snapshot.as_of, f"flatten by {cfg.exit_time} ET")
            ],
            next_event="", recommendation="FLATTEN",
            reasons=[f"time stop {cfg.time_stop_buffer_minutes} min before {cfg.exit_time} ET"],
        )

    # 5. Confluence flip against the position: EXIT advisory (or hard exit).

    # 5. Confluence flip against the position: EXIT advisory (or hard exit).
    against = (
        not result.spy_confluence_ok
        or (not result.tradeable and result.gates_ok)
        or (result.tradeable and result.side != trade.side)
    )
    if against:
        reasons.append("checklist flipped against the position (SPY confluence lost)")
        if cfg.exit_on_confluence_loss:
            updated.realized_r = round(
                updated.realized_r + updated.remaining * r_now, 4
            )
            updated.remaining = 0
            updated.fired.setdefault(
                "confluence_exit", snapshot.as_of.isoformat(timespec="minutes")
            )
            return updated, MgmtReport(
                r_now=r_now, mfe_r=mfe_r, mae_r=mae_r, stop=updated.stop,
                target=updated.target, events=events,
                next_event="", recommendation="CLOSED",
                reasons=reasons + ["exit_on_confluence_loss=True"],
            )
        recommendation = "EXIT"
    else:
        recommendation = "HOLD"

    return updated, MgmtReport(
        r_now=r_now, mfe_r=mfe_r, mae_r=mae_r, stop=updated.stop,
        target=updated.target, events=events,
        next_event=_next_event(cfg, updated),
        recommendation=recommendation,
        reasons=reasons,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_management.py -v`
Expected: PASS (all tests including Task 2's nine).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/mes/management.py tests/test_mes_management.py
git commit -m "feat(mes): implement breakeven, partial, trail, and time-stop triggers"
```

---

### Task 4: Journal trade lifecycle

**Files:**
- Modify: `tradingagents/mes/journal.py` (new methods; existing ones untouched)
- Modify: `tests/test_mes_journal.py` (append tests)

**Interfaces:**
- Consumes: `MesJournal._append/_path/load_day`, Task 2's `OpenTrade` and `_r_of`.
- Produces (used by Tasks 5–7):
  - `MesJournal.append_trade_opened(trade: OpenTrade, *, entry_context: dict | None = None) -> None`
  - `MesJournal.append_trade_adjusted(date: str, *, stop: float | None = None, note: str | None = None, as_of: str | None = None) -> None`
  - `MesJournal.append_trade_closed(trade: OpenTrade, *, exit_price: float, reason: str, as_of: datetime, mfe_r: float = 0.0, mae_r: float = 0.0) -> None` — computes `realized_r = trade.realized_r + trade.remaining * _r_of(exit_price, trade)`
  - `MesJournal.load_trades(date: str) -> list[dict]` — only `trade_*` records, file order
  - `MesJournal.find_open_trade(date: str) -> OpenTrade | None` — replays lifecycle records into an `OpenTrade`; `None` once closed
  - `MesJournal.summarize_trades(date: str) -> str` — markdown table for the review agent

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mes_journal.py` (add imports `from datetime import datetime as dt` and `from tradingagents.mes.management import OpenTrade` at the top, `import pytest` if missing):

```python
def _trade(**overrides) -> OpenTrade:
    fields = dict(
        side="long",
        contracts=1,
        remaining=1,
        entry=6500.0,
        stop=6498.0,
        initial_stop=6498.0,
        target=6504.0,
        entry_time=dt(2026, 3, 30, 10, 7),
        initial_risk_points=2.0,
        fired={},
        manual_events=[],
        realized_r=0.0,
    )
    fields.update(overrides)
    return OpenTrade(**fields)


@pytest.mark.unit
def test_trade_lifecycle_round_trip(journal):
    journal.append_trade_opened(_trade(), entry_context={"score": 7, "tier": "standard"})
    journal.append_trade_adjusted("2026-03-30", stop=6500.25, note="breakeven stop")

    open_trade = journal.find_open_trade("2026-03-30")
    assert open_trade is not None
    assert open_trade.entry == pytest.approx(6500.0)
    assert open_trade.side == "long"
    assert open_trade.stop == pytest.approx(6500.25)  # adjusted during replay
    assert open_trade.remaining == 1

    journal.append_trade_closed(
        _trade(stop=6500.25), exit_price=6503.0, reason="manual", as_of=_dt(2026, 3, 30, 11, 30)
    )
    assert journal.find_open_trade("2026-03-30") is None

    closed = [t for t in journal.load_trades("2026-03-30") if t["kind"] == "trade_closed"]
    assert closed[-1]["exit_price"] == 6503.0
    assert closed[-1]["reason"] == "manual"
    # realized: 3.0 pts / 2.0 risk = 1.5R
    assert closed[-1]["realized_r"] == pytest.approx(1.5)


@pytest.mark.unit
def test_trade_opened_captures_entry_context(journal):
    journal.append_trade_opened(_trade(), entry_context={"score": 7, "tier": "standard"})
    opened = [t for t in journal.load_trades("2026-03-30") if t["kind"] == "trade_opened"][0]
    assert opened["entry_context"] == {"score": 7, "tier": "standard"}
    assert opened["initial_risk_points"] == 2.0


@pytest.mark.unit
def test_find_open_trade_none_before_any_trade(journal):
    assert journal.find_open_trade("2020-01-01") is None


@pytest.mark.unit
def test_summarize_trades_lists_closed_results(journal):
    journal.append_trade_opened(_trade(), entry_context={"score": 7, "tier": "standard"})
    journal.append_trade_closed(_trade(realized_r=1.5), exit_price=6503.5, reason="manual", as_of=_dt(2026, 3, 30, 11, 0))

    summary = journal.summarize_trades("2026-03-30")
    assert "| Side | Entry | Exit | Reason | Realized R |" in summary
    assert "6503.50" in summary
    assert "manual" in summary


@pytest.mark.unit
def test_summarize_trades_empty(journal):
    assert journal.summarize_trades("2020-01-01") == "No trades logged for 2020-01-01."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_journal.py -v -k trade`
Expected: FAIL — `AttributeError: 'MesJournal' object has no attribute 'append_trade_opened'`

- [ ] **Step 3: Implement**

In `tradingagents/mes/journal.py`, add this import next to the existing ones:

```python
from .management import OpenTrade, _r_of
```

Then append these methods to `MesJournal` (after `summarize_checks`):

```python
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
                "| {side} | {entry:.2f} | {exit:.2f} | {reason} | {r:+.2f} |".format(
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_journal.py -v`
Expected: PASS (existing journal tests unchanged and passing; new lifecycle tests pass).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/mes/journal.py tests/test_mes_journal.py
git commit -m "feat(mes): add trade lifecycle records to the session journal"
```

---

### Task 5: Manager agent + package exports + review-agent trades context

**Files:**
- Create: `tradingagents/agents/mes/manager_agent.py`
- Modify: `tradingagents/agents/mes/__init__.py`
- Modify: `tradingagents/agents/mes/review_agent.py`
- Modify: `tests/test_mes_agents.py` (append tests)

**Interfaces:**
- Consumes: `get_language_instruction` (`tradingagents/agents/utils/agent_utils.py`), the `FakeLLM` stub pattern in `tests/test_mes_agents.py`.
- Produces:
  - `tradingagents.agents.mes.create_mes_manager_agent(llm)` returning `run(*, mgmt_summary: str, market_context: str, hypothesis: str = "", current_price: float | None = None) -> str (plain advisory markdown, no schema).
  - `create_mes_review_agent(llm)` gains keyword-only param `trades_summary: str = ""`; when non-empty, an extra prompt section is appended.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mes_agents.py`:

```python
# ---------------------------------------------------------------------------
# Trade manager agent (advisory only)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_manager_agent_renders_prompt_with_report():
    llm = FakeLLM(text="Trend intact; $TICK still positive. Hold.")
    agent = create_mes_manager_agent(llm)
    agent(
        mgmt_summary="LONG 1 @ 100.00 | +0.46R | stop 98.00 | next: BE at +1.0R",
        market_context="SPY above VWAP, $ADD +1200",
        hypothesis="Trend-up day; SPY holding VWAP.",
        current_price=100.50,
    )
    assert llm.prompts, "manager must receive a prompt"
    assert "100.00" in llm.prompts[0]
    assert "SPY above VWAP" in llm.prompts[0]


@pytest.mark.unit
def test_manager_output_is_returned_verbatim():
    llm = FakeLLM(text="Internals flipped; consider tightening.")
    agent = create_mes_manager_agent(llm)
    text = agent(mgmt_summary="HOLD | +0.5R | stop 98.00", market_context="SPY above VWAP")
    assert text == "Internals flipped; consider tightening."


@pytest.mark.unit
def test_review_agent_accepts_trades_summary():
    llm = FakeLLM()
    agent = create_mes_review_agent(llm)
    agent(hypothesis="h", checks_summary="c", outcome_summary="o",
          trades_summary="| long | 6500.00 | 6503.50 | manual | +1.75 |")
    assert "Trades Taken" in llm.prompts[0]
```


- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_agents.py -v -k manager`
Expected: FAIL — `ImportError: cannot import name 'create_mes_manager_agent'`

- [ ] **Step 3: Implement**

Create `tradingagents/agents/mes/manager_agent.py`:

```python
"""MES Trade Manager Agent: advisory-only commentary on an open position.

Unlike the gatekeeper, this agent has no authority: the deterministic ladder
owns every level. The manager reads the mechanical report and adds judgement
about context the rules cannot see (internals shifts, news spikes, unusual
behavior). Its output is advisory text rendered under the mechanical report;
it is never parsed into state and never moves a level.
"""

from __future__ import annotations

from tradingagents.agents.utils.agent_utils import get_language_instruction


def create_mes_manager_agent(llm):
    def run(
        *,
        mgmt_summary: str,
        market_context: str,
        hypothesis: str = "",
        current_price: float | None = None,
    ) -> str:
        price_line = (
            f"**Current /MES price: {current_price:.2f}**\n\n"
            if current_price is not None
            else ""
        )

        prompt = f"""You are the trade manager for a /MES (Micro E-mini S&P 500) discretionary desk.
A position is open and the deterministic ladder below already owns every level
decision: breakeven, partial profit-taking, the trailing stop, and the EOD
time stop. Do not propose changing any level; do not issue a verdict.

Your job is one paragraph of advisory commentary the mechanical plan cannot
see: internal context shifts, follow-through quality, or risk the mechanical
report misses. Reference specific readings (e.g. "$TICK whipsawing",
"$VOLD diverging from SPY bar direction"). Never invent levels, never suggest
targets beyond the plan's, and never advise against the plan's stop.

---

**Position & Plan (deterministic — authoritative):**
{mgmt_summary}

---

**Market Context:**
{market_context}"""

        if hypothesis.strip():
            prompt += f"""

---

**This Morning's Hypothesis:**
{hypothesis}

Note when live price action contradicts the morning thesis; a thesis break is
worth flagging even when the mechanical ladder is quiet."""

        prompt += get_language_instruction()

        response = llm.invoke(prompt)
        return getattr(response, "content", "") or ""

    return run
```

Then in `tradingagents/agents/mes/__init__.py`, extend the imports and `__all__`:

```python
from .manager_agent import create_mes_manager_agent

__all__ = [
    "create_mes_gatekeeper_agent",
    "create_mes_manager_agent",
    "create_mes_morning_agent",
    "create_mes_review_agent",
]
```

For the review agent, change `review_agent.py`'s `run` signature and prompt tail:

```python
    def run(*, hypothesis: str, checks_summary: str, outcome_summary: str, trades_summary: str = "") -> str:
```

and after the `**Outcomes:**` section, append conditionally:

```python
        if trades_summary.strip():
            prompt += f"""

---

**Trades Taken (from the journal):**
{trades_summary}

Grade the execution, not just the hypothesis: entry location quality vs. the
checklist tier at the time, stop management, whether the exit respected the
plan (time stop / target / manual), and what the realized R says about the
tier the entry was taken at."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_agents.py -v`
Expected: PASS (existing agent tests unchanged; new manager tests pass).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/agents/mes/manager_agent.py tradingagents/agents/mes/__init__.py \
  tradingagents/agents/mes/review_agent.py tests/test_mes_agents.py
git commit -m "feat(mes): add advisory trade-manager agent and review trades context"
```

---

### Task 6: `mes trade` CLI — enter / status / close / adjust

**Files:**
- Modify: `cli/mes.py` (new `trade` sub-app + rendering)
- Create: `tests/test_mes_cli_trade.py`

**Interfaces:**
- Consumes: Tasks 1–5 outputs (`evaluate_management`, `OpenTrade`, `MgmtEvent`, `MgmtReport`, `excursions_r`, `_r_of`, `_tighter`, `MesJournal.append_trade_opened/adjusted/closed`, `find_open_trade`, `create_mes_manager_agent`, `suggest_stop_points`, `suggest_trade_levels_from_snapshot`, and existing `cli/mes.py` helpers `_load_snapshot`, `_market_now`, `_parse_as_of`, `_make_llm`, `console`, `mes_app`).
- Produces: `mes trade enter|status|close|adjust`; `trade_app` Typer sub-app; `status --no-watch --json` emits `{"as_of", "trade", "report"}`; `enter` refuses while a trade is open; `close` requires `--price`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mes_cli_trade.py` with exactly this content:

```python
"""Tests for the `mes trade` CLI command group."""

from __future__ import annotations

from datetime import datetime

import pytest
from typer.testing import CliRunner

from cli.mes import trade_app
from tradingagents.mes.journal import MesJournal
from tradingagents.mes.management import OpenTrade
from tests.mes_factories import DEFAULT_AS_OF, make_mes_series, make_snapshot

runner = CliRunner()


@pytest.fixture()
def patched_snapshot(monkeypatch):
    """Every snapshot build returns one flat bar closing at 100.0."""
    from cli import mes as mes_cli

    snap = make_snapshot(mes=make_mes_series(close=100.0))
    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(mes_cli, "_market_now", lambda cfg: datetime(2026, 3, 30, 11, 0))
    return snap


@pytest.fixture()
def journal(tmp_path, monkeypatch):
    return MesJournal({"mes_journal_dir": str(tmp_path)})


def _invoke(*args):
    return runner.invoke(trade_app, list(args))


@pytest.mark.unit
def test_enter_writes_opened_record(tmp_path, patched_snapshot):
    result = _invoke(
        "enter", "--side", "long", "--contracts", "1",
        "--entry", "100.00", "--stop", "98.00", "--target", "102.00",
        "--journal-dir", str(tmp_path),
    )
    assert result.exit_code == 0, result.output
    assert "trade opened" in result.output.lower()
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    records = [e for e in journal.load_day("2026-03-30") if e["kind"] == "trade_opened"]
    assert len(records) == 1
    assert records[0]["side"] == "long"
    assert records[0]["entry_context"]["tier"]  # checklist context captured


@pytest.mark.unit
def test_enter_refuses_second_trade(tmp_path, patched_snapshot):
    _invoke("enter", "--side", "long", "--entry", "100.00", "--stop", "98.00",
            "--journal-dir", str(tmp_path))
    second = _invoke("enter", "--side", "long", "--entry", "100.50", "--stop", "99.00",
                     "--journal-dir", str(tmp_path))
    assert second.exit_code != 0
    assert "already open" in second.output.lower()


@pytest.mark.unit
def test_status_reports_open_trade_without_llm(tmp_path, patched_snapshot):
    _invoke("enter", "--side", "long", "--entry", "100.00", "--stop", "98.00",
            "--journal-dir", str(tmp_path))
    status = _invoke("status", "--no-watch", "--no-llm", "--journal-dir", str(tmp_path))
    assert status.exit_code == 0
    assert "LONG" in status.output


@pytest.mark.unit
def test_status_json_emits_report_fields(tmp_path, patched_snapshot):
    _invoke("enter", "--side", "long", "--entry", "100.0", "--stop", "98.0",
            "--journal-dir", str(tmp_path))
@pytest.mark.unit
def test_status_json_emits_report_fields(tmp_path, patched_snapshot):
    _invoke("enter", "--side", "long", "--entry", "100.0", "--stop", "98.0",
            "--journal-dir", str(tmp_path))
    payload = _invoke("status", "--no-watch", "--json", "--journal-dir", str(tmp_path))
    assert payload.exit_code == 0
    assert '"recommendation"' in payload.output
    assert '"r_now"' in payload.output


@pytest.mark.unit
def test_close_records_exit_and_clears_open(tmp_path, patched_snapshot):
    _invoke("enter", "--side", "long", "--entry", "100.00", "--stop", "98.00",
            "--journal-dir", str(tmp_path))
    closed = _invoke("close", "--price", "101.50", "--reason", "manual",
                     "--journal-dir", str(tmp_path))
    assert closed.exit_code == 0
    assert "closed" in closed.output.lower()

    status = _invoke("status", "--no-watch", "--no-llm", "--journal-dir", str(tmp_path))
    assert "no open trade" in status.output.lower()


@pytest.mark.unit
def test_close_without_open_trade_fails(tmp_path, patched_snapshot):
    result = _invoke("close", "--price", "101.00", "--reason", "manual",
                     "--journal-dir", str(tmp_path))
    assert result.exit_code != 0


@pytest.mark.unit
def test_adjust_refuses_to_loosen_stop(tmp_path, patched_snapshot):
    _invoke("enter", "--side", "long", "--entry", "100.00", "--stop", "98.00",
            "--journal-dir", str(tmp_path))
    loosened = _invoke("adjust", "--stop", "97.00", "--journal-dir", str(tmp_path))
    assert "refus" in loosened.output.lower() or "kept" in loosened.output.lower()
```

**Harness contract (needed for the tests above to pass):** every `trade_*` command accepts a hidden `--journal-dir PATH` option (default `None` → plain `load_mes_config()`); when provided, `MesJournal({"mes_journal_dir": str(journal_dir)})` is used instead. This keeps tests hermetic without patching the config module.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_cli_trade.py -v`
Expected: FAIL — `ImportError: cannot import name 'trade_app' from 'cli.mes'`

- [ ] **Step 3: Implement the `mes trade` group in `cli/mes.py`**

Extend the `from tradingagents.mes import (...)` block with `OpenTrade` and add:

```python
from tradingagents.mes.management import MgmtEvent, MgmtReport, _r_of, evaluate_management
```

Insert the command group after the existing `radar` command, before the final line of the file:

```python
# ---------------------------------------------------------------------------
# Trade management
# ---------------------------------------------------------------------------

trade_app = typer.Typer(name="trade", help="Declare and manage an open /MES position.")
mes_app.add_typer(trade_app, name="trade")


def _trade_journal(cfg, journal_dir: Path | None):
    """Journal for trade commands; --journal-dir (hidden) keeps tests hermetic.

    The default branch MUST use DEFAULT_CONFIG: `MesChecklistConfig.to_dict()`
    carries neither `mes_journal_dir` nor `results_dir`, so a bare cfg.to_dict()
    would land in a CWD-relative ./mes_journal that `mes review` (built from
    DEFAULT_CONFIG) never reads — silently breaking the single-open guard and
    review integration across commands.
    """
    if journal_dir is not None:
        return MesJournal(cfg.to_dict() | {"mes_journal_dir": str(journal_dir)})
    return MesJournal(DEFAULT_CONFIG.copy())



def _render_mgmt_panel(trade: OpenTrade, report, price: float) -> Panel:
    grid = Table.grid(padding=(0, 1))
    grid.add_row(Text.from_markup(
        f"[bold]{trade.side.upper()} {trade.contracts} @ {trade.entry:.2f}[/bold]  "
        f"entry {trade.entry_time:%H:%M}"
    ))
    grid.add_row(Text.from_markup(
        f"Now [bold]{price:.2f}[/bold]  "
        f"[{'green' if report.r_now >= 0 else 'red'}]{report.r_now:+.2f}R[/'green']  "
        f"MFE {report.mfe_r:+.2f}R  MAE {report.mae_r:+.2f}R"
    ))
    grid.add_row(Text.from_markup(
        f"PLAN  stop {report.stop:.2f}  target {report.target if report.target is not None else '-'}"
    ))
    grid.add_row(Text.from_markup(f"NEXT  {report.next_event or '-'}"))
    body = Text("")
    for event in report.events:
        body.append(f"✓ {event.name}: {event.detail}\n", style="green")
    for reason in report.reasons:
        body.append(f"· {reason}\n", style="yellow")
    return Panel(Group(grid, body) if body.plain else grid,
                 title="MES Trade Manager", border_style="blue")
```

(The panel body is finalized during implementation; the required content lines are: side/contracts/entry header, current price + R + MFE/MAE line, PLAN stop/target line, NEXT-event line, then fired-event and reason lines. Keep it a single `Panel` titled `"MES Trade Manager"` with `border_style="blue"`.)

`enter`:

```python
@trade_app.command("enter")
def trade_enter(
    side: str = typer.Option(..., "--side", help="'long' or 'short'."),
    contracts: int = typer.Option(1, "--contracts", min=1),
    entry: float | None = typer.Option(None, "--entry", help="Fill price. Defaults to the latest close."),
    stop: float | None = typer.Option(None, "--stop", help="Initial stop. Defaults to the structural suggestion."),
    target: float | None = typer.Option(None, "--target"),
    as_of: str | None = typer.Option(None, "--as-of"),
    date: str | None = typer.Option(None, "--date"),
    journal_dir: Path | None = typer.Option(None, "--journal-dir", hidden=True),
    mes_csv: Path | None = typer.Option(None, "--mes-csv"),
    spy_csv: Path | None = typer.Option(None, "--spy-csv"),
    force: bool = typer.Option(False, "--force", help="Override the single-open-trade guard."),
):
    """Declare a filled entry; the copilot starts managing it."""
    if side not in {"long", "short"}:
        raise typer.BadParameter("--side must be 'long' or 'short'")
    cfg = load_mes_config()
    journal = _trade_journal(cfg, journal_dir)
    stamp = _parse_as_of(as_of, cfg, date=date) if as_of else _market_now(cfg)

    existing = journal.find_open_trade(stamp.strftime("%Y-%m-%d"))
    if existing is not None and not force:
        console.print("[red]A trade is already open for this date. Close it first.[/red]")
        raise typer.Exit(code=1)

    try:
        snapshot = _load_snapshot(stamp, cfg, mes_csv, spy_csv)
        result = evaluate(snapshot, side)
    except Exception as exc:
        console.print(f"[red]Could not build snapshot:[/red] {exc}")
        raise typer.Exit(code=1)

    entry_px = entry if entry is not None else snapshot.mes.close
    stop_px = stop if stop is not None else suggest_stop_points(side, entry_px, result.vwap, result.atr)
    levels = suggest_trade_levels_from_snapshot(side, snapshot, result)
    target_px = target if target is not None else (levels.first_target if levels else None)

    trade = OpenTrade(
        side=side,
        contracts=contracts,
        remaining=contracts,
        entry=round(entry_px, 2),
        stop=round(stop_px, 2),
        initial_stop=round(stop_px, 2),
        target=target_px,
        entry_time=snapshot.as_of,
        initial_risk_points=round(abs(entry_px - stop_px), 4),
    )
    if trade.initial_risk_points <= 0:
        raise typer.BadParameter("stop must be strictly beyond the entry price")

    journal.append_trade_opened(trade, entry_context={
        "score": result.score,
        "tier": result.tier,
        "confirmations": result.confirmations,
        "spy_confirmations": result.spy_confirmations,
        "side": side,
    })
    console.print(
        f"[bold green]Trade opened[/bold green]: {side} {contracts} @ {entry_px:.2f}, "
        f"stop {stop_px:.2f}, target {target if target is not None else '-'} "
        f"(1R = {trade.initial_risk_points:.2f} pts)"
    )
```

`status` (mirror the radar watch-loop; `--json` prints `{as_of, trade, report}` via `console.print_json`):

```python
@trade_app.command("status")
def trade_status(
    watch: int = typer.Option(15, "--watch"),
    no_watch: bool = typer.Option(False, "--no-watch"),
    no_llm: bool = typer.Option(False, "--no-llm"),
    as_json: bool = typer.Option(False, "--json"),
    alert: bool = typer.Option(False, "--alert"),
    date: str | None = typer.Option(None, "--date"),
    as_of: str | None = typer.Option(None, "--as-of"),
    journal_dir: Path | None = typer.Option(None, "--journal-dir", hidden=True),
    mes_csv: Path | None = typer.Option(None, "--mes-csv"),
    spy_csv: Path | None = typer.Option(None, "--spy-csv"),
):
    """Live management view of the open trade."""
    cfg = load_mes_config()
    journal = _trade_journal(cfg, journal_dir)
    manager = None
    if not no_llm:
        try:
            manager = create_mes_manager_agent(_make_llm(DEFAULT_CONFIG.copy()))
        except Exception as exc:
            console.print(f"[yellow]Manager unavailable, mechanical only:[/yellow] {exc}")
            manager = None

    def _one_shot(stamp):
        trade = journal.find_open_trade(stamp.strftime("%Y-%m-%d"))
        if trade is None:
            return None
        snapshot = _load_snapshot(stamp, cfg, mes_csv, spy_csv)
        result = evaluate(snapshot, trade.side)
        updated, report = evaluate_management(snapshot, result, trade, cfg)
        return snapshot, updated, report

    if no_watch or as_json:
        stamp = _parse_as_of(as_of, cfg, date=date) if as_of else _market_now(cfg)
        shot = _one_shot(stamp)
        if shot is None:
            console.print("[yellow]No open trade for this date. Run `mes trade enter` first.[/yellow]")
            raise typer.Exit(code=1)
        snapshot, updated, report = shot
        if as_json:
            console.print_json(json.dumps({
                "as_of": snapshot.as_of.isoformat(),
                "trade": dataclasses.asdict(updated),
                "report": dataclasses.asdict(report),
            }, default=str))
            return
        console.print(_render_mgmt_panel(updated, report, snapshot.mes.close))
        return

    # Watch loop (same skeleton as radar).
    with Live(console=console, refresh_per_second=1, screen=False) as live:
        seen: set[str] = set()
        while True:
            stamp = _market_now(cfg)
            try:
                shot = _one_shot(stamp)
                if shot is None:
                    live.update(Panel("[yellow]No open trade.[/yellow]", title="MES Trade Manager"))
                else:
                    snapshot, updated, report = shot
                    live.update(_render_mgmt_panel(updated, report, snapshot.mes.close))
                    # Persist each ladder event exactly once; advisory LLM text
                    # below never mutates state.
                    for event in report.events:
                        key = f"{event.name}@{event.as_of.isoformat(timespec='minutes')}"
                        if key in seen:
                            continue
                        seen.add(key)
                        journal.append_trade_adjusted(
                            stamp.strftime("%Y-%m-%d"),
                            stop=updated.stop,
                            note=f"{event.name}: {event.detail}",
                            as_of=event.as_of.isoformat(timespec="minutes"),
                        )
                        if alert and event.name in {"stopped_out", "target", "time_stop"}:
                            console.print("\a")
                    if manager is not None:
                        try:
                            advisory = manager(
                                mgmt_summary=(
                                    f"{updated.side} {updated.contracts} @ {updated.entry:.2f} | "
                                    f"{report.r_now:+.2f}R | stop {report.stop:.2f} | "
                                    f"next: {report.next_event or 'closed'}"
                                ),
                                market_context=render_market_context(snapshot),
                                current_price=snapshot.mes.close,
                            )
                            live.update(Panel(Markdown(advisory), title="Manager Advisory",
                                              border_style="dim"))
                        except Exception as exc:
                            console.print(f"[yellow]Manager call failed:[/yellow] {exc}")
            except Exception as exc:
                live.update(Panel(f"[red]Snapshot failed:[/red] {exc}", title="MES Trade Manager"))
            except Exception as exc:
                live.update(Panel(f"[red]Snapshot failed:[/red] {exc}", title="MES Trade Manager"))
            try:
                time.sleep(watch)
            except KeyboardInterrupt:
                break
```

The one-shot path under `--no-llm` skips the manager entirely; the LLM advisory text is never parsed into state and never moves a level. `stopped_out` / `target` / `time_stop` events ring the bell once when `--alert` is set.

`close`:

```python
@trade_app.command("close")
def trade_close(
    price: float = typer.Option(..., "--price"),
    reason: str = typer.Option("manual", "--reason", help="manual | stop | target | eod"),
    date: str | None = typer.Option(None, "--date"),
    as_of: str | None = typer.Option(None, "--as-of"),
    journal_dir: Path | None = typer.Option(None, "--journal-dir", hidden=True),
    mes_csv: Path | None = typer.Option(None, "--mes-csv"),
    spy_csv: Path | None = typer.Option(None, "--spy-csv"),
):
    """Record the exit for the open trade (execution stays in TOS)."""
    cfg = load_mes_config()
    journal = _trade_journal(cfg, journal_dir)
    stamp = _parse_as_of(as_of, cfg, date=date) if as_of else _market_now(cfg)
    session_date = stamp.strftime("%Y-%m-%d")
    trade = journal.find_open_trade(session_date)
    if trade is None:
        console.print("[yellow]No open trade for this date.[/yellow]")
        raise typer.Exit(code=1)

    snapshot = _load_snapshot(stamp, cfg, mes_csv, spy_csv)
    result = evaluate(snapshot, trade.side)
    updated, report = evaluate_management(snapshot, result, trade, cfg)
    if updated.remaining > 0:
        from tradingagents.mes.management import _r_of

        updated.realized_r = round(
            updated.realized_r + updated.remaining * _r_of(price, updated), 4
        )
        updated.remaining = 0

    journal.append_trade_closed(updated, exit_price=price, reason=reason, as_of=stamp)
    console.print(
        f"[bold green]Closed[/bold green] {updated.side} @ {price:.2f} ({reason}) — "
        f"realized {updated.realized_r:+.2f}R, MFE {report.mfe_r:+.2f}R, MAE {report.mae_r:+.2f}R"
    )
```

`adjust`:

```python
@trade_app.command("adjust")
def trade_adjust(
    stop: float | None = typer.Option(None, "--stop", help="New stop level (tightens only)."),
    note: str | None = typer.Option(None, "--note", help="Freeform manual note."),
    journal_dir: Path | None = typer.Option(None, "--journal-dir", hidden=True),
):
    """Record a manual adjustment you made at the broker."""
    cfg = load_mes_config()
    journal = _trade_journal(cfg, journal_dir)
    stamp = _market_now(cfg)
    trade = journal.find_open_trade(stamp.strftime("%Y-%m-%d"))
    if trade is None:
        console.print("[yellow]No open trade to adjust.[/yellow]")
        raise typer.Exit(code=1)
    if stop is not None:
        from tradingagents.mes.management import _tighter

        tightened = _tighter(trade.stop, stop, trade.side)
        if tightened != trade.stop:
            console.print("[yellow]Refusing to loosen the stop; kept the tighter level.[/yellow]")
        journal.append_trade_adjusted(
            stamp.strftime("%Y-%m-%d"), stop=tightened, note=note or "manual stop move"
        )
        console.print(f"[green]Stop adjusted to {tightened:.2f}.[/green]")
    elif note:
        journal.append_trade_adjusted(stamp.strftime("%Y-%m-%d"), note=note)
        console.print(f"[green]Noted:[/green] {note}")
```

Wiring notes: the `review` command gains the trades panel and `trades_summary` pass-through (Task 8 covers it); the sub-app must be registered **once** at module scope. The one-shot `status` path builds the same `mgmt_summary` string shown in the watch loop when the manager is available (skip entirely under `--no-llm`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_cli_trade.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cli/mes.py tests/test_mes_cli_trade.py
git commit -m "feat(cli): add mes trade enter/status/adjust/close commands"
```

---

### Task 8: Review integration — trades section in `mes review`

**Files:**
- Modify: `cli/mes.py` (`review` command only)
- Test: extend `tests/test_mes_cli_trade.py`

**Interfaces:**
- Consumes: Task 4's `MesJournal.summarize_trades` / `find_open_trade`; Task 5's review agent `trades_summary` parameter.
- Produces: `mes review` prints a Trades panel when closed trades exist, flags still-open trades, and passes `trades_summary` to the review agent.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mes_cli_trade.py`:

```python
@pytest.mark.unit
def test_review_flags_still_open_trade(tmp_path, patched_snapshot, monkeypatch):
    from datetime import datetime as _dt

    from cli import mes as mes_cli
    from tradingagents.mes.management import OpenTrade

    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    trade = OpenTrade(
        side="long", contracts=1, remaining=1,
        entry=100.0, stop=98.0, initial_stop=98.0, target=102.0,
        entry_time=datetime(2026, 3, 30, 10, 7), initial_risk_points=2.0,
    )
    journal.append_trade_opened(trade, entry_context={"score": 7, "tier": "standard"})

    # review builds its journal from DEFAULT_CONFIG; point it at tmp_path so
    # this test never touches the real journal.
    import tradingagents.default_config as dc
    from cli import mes as mes_cli

    monkeypatch.setattr(
        mes_cli, "DEFAULT_CONFIG",
        {**dc.DEFAULT_CONFIG, "mes_journal_dir": str(tmp_path)},
    )

    result = CliRunner().invoke(mes_app, ["review", "--date", "2026-03-30", "--no-llm"])
    assert result.exit_code == 0
    assert "still marked open" in result.output.lower()
```

**Harness note:** `review` builds `MesJournal(DEFAULT_CONFIG.copy())`, so the test
patches `mes_cli.DEFAULT_CONFIG` (the name `cli/mes.py` imported) rather than
relying on env vars — `DEFAULT_CONFIG` is computed once at import time, so
`monkeypatch.setenv` alone cannot reach it. The invocation must not touch the
real journal. Also note `review` exits early when the day has neither checks
nor a hypothesis; the test seeds the opened-trade record, which `load_day`
sees, but `load_checks`/`load_hypothesis` still return empty — so the
implementation must relax that guard to also proceed when trade records exist
(see Step 3).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mes_cli_trade.py -v -k review`
Expected: FAIL — "still marked open" never printed.

- [ ] **Step 3: Implement**

In `cli/mes.py`'s `review` command, first widen the "no entries" guard so a
trade-only day still reviews. Replace:

```python
    if not checks and not hypothesis_record:
```

with:

```python
    if not checks and not hypothesis_record and not journal.load_trades(session_date):
```

Then directly after `checks_summary = journal.summarize_checks(session_date)`:

```python
    trades_summary = journal.summarize_trades(session_date)
    open_trade = journal.find_open_trade(session_date)
    if open_trade is not None:
        console.print(
            "[yellow]Warning: the trade opened "
            f"{open_trade.entry_time:%H:%M} is still marked open for {session_date}; "
            "grading assumes an EOD flatten.[/yellow]"
        )
```

After the checks panel print, add:

```python
    if trades_summary and not trades_summary.startswith("No trades"):
        console.print(Panel(Markdown(trades_summary), title="Trades", border_style="green"))
```

and extend the agent call:

```python
        review_markdown = agent(
            hypothesis=hypothesis,
            checks_summary=checks_summary,
            outcome_summary=outcome_summary,
            trades_summary=trades_summary,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_cli_trade.py tests/test_mes_agents.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cli/mes.py tests/test_mes_cli_trade.py
git commit -m "feat(mes): surface trade results in the session review"
```

---

### Task 9: Package exports + full-suite verification

**Files:**
- Modify: `tradingagents/mes/__init__.py`
- Test: `tests/test_mes_management.py` (append), then full suite

**Interfaces:**
- Consumes: Tasks 2–3's `tradingagents/mes/management.py`.
- Produces: `from tradingagents.mes import OpenTrade, MgmtEvent, MgmtReport, evaluate_management` (Task 6's CLI imports these from the package root).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mes_management.py`:

```python
@pytest.mark.unit
def test_management_types_are_public():
    import tradingagents.mes as pkg

    for name in ("OpenTrade", "MgmtEvent", "MgmtReport", "evaluate_management"):
        assert hasattr(pkg, name), f"tradingagents.mes.{name} must be re-exported"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mes_management.py::test_management_types_are_public -v`
Expected: FAIL — `AttributeError: module 'tradingagents.mes' has no attribute 'OpenTrade'`

- [ ] **Step 3: Implement**

In `tradingagents/mes/__init__.py`, add next to the existing `from .journal import MesJournal` line:

```python
from .management import MgmtEvent, MgmtReport, OpenTrade, evaluate_management
```

and insert `"MgmtEvent"`, `"MgmtReport"`, `"OpenTrade"`, `"evaluate_management"` into `__all__` (alphabetical positions).

- [ ] **Step 4: Run the full MES suite**

Run: `python -m pytest tests/ -k mes -v`
Expected: PASS — every test, including all pre-existing checklist/journal/radar/agent tests.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/mes/__init__.py tests/test_mes_management.py
git commit -m "feat(mes): export trade management API from the mes package"
```

---

## Self-Review (completed during authoring; amended at execution pre-flight)

- **Spec coverage:** config fields (T1) → ladder core + fills + idempotency (T2) → triggers incl. 1-contract degradation, gaps, time stop, confluence flip advisory/hard (T3) → journal lifecycle + replay + summary (T4) → advisory manager + review-agent `trades_summary` (T5) → CLI enter/status/close/adjust incl. JSON + alert (T6) → review trades section (T8) → exports + regression (T9). No spec requirement lacks a task.
- **Placeholders:** none — every code step is complete code; every command step states the expected result.
- **Type consistency:** `evaluate_management` returns `(OpenTrade, MgmtReport)` in T2/T3 and all consumers; report field is `events` (not `events_fired`); `_r_of(price, trade)` used identically in management, journal close accounting, and CLI close; journal methods match across T4→T6→T8 (`append_trade_opened/adjusted/closed`, `load_trades`, `find_open_trade`, `summarize_trades`).

## Execution amendments (pre-flight, 2026-09-05)

Verified against the codebase before dispatching implementers; the task text
above already carries these amendments:

- **Task 3 tests** — all post-entry trigger tests pass `target=None` to
  `make_trade` (a live +1R target would close the position in the fill branch
  before any trigger runs) and set `entry_time` before the snapshot's bars
  where the trail/excursion windows matter. Trail is gated on the pre-call
  `trade.fired`, so it starts the bar after the partial, not the same bar.
- **Task 3 implementation** — partial's 1-contract branch assigns `new_stop`
  once (the draft tightened twice); `fired["partial"]` is set in both branches.
- **Task 6 CLI** — single `_trade_journal(cfg, journal_dir)` helper replaces
  the draft's ad-hoc `MesJournal(...)` constructions (some dropped
  `mes_journal_dir` and would write to the real `~/logs/mes_journal`);
  every CLI test invoke passes `--journal-dir`; `status --json` uses
  `dataclasses.asdict(updated)` (no walrus); watch loop is complete code with
  event persistence, bell, and manager advisory.
- **Task 8** — `review`'s "no entries" guard widens to include trade records,
  and the test patches `mes_cli.DEFAULT_CONFIG` (env vars cannot reach it at
  import time).
- **Test interpreter:** run tests with `.venv/bin/python -m pytest` (pyenv
  shim has no default `python` on this machine).
