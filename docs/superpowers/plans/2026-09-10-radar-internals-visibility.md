# Radar Internals Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make `mes radar` show the live $TICK/$ADD/$VOLD readings and the snapshot's data-quality warnings, so internals are visible while watching, not only evaluated.

**Architecture:** `build_proximity` gains two pass-through fields on `ProximityReport` (`warnings`, `internals_status`) computed from the snapshot it already receives; a new pure formatter `format_internals_status()` in `tradingagents/mes/render.py` turns snapshot internals into one plain-text line; `cli/mes.py::_render_radar` renders that line plus the warnings. No state-machine or data-flow changes — the internals already gate radar's state machine via `checklist.evaluate`; this only makes the inputs visible.

**Tech Stack:** Python 3.11, pytest (`@pytest.mark.unit` markers), Rich (panels/tables), Typer CliRunner for command-level tests.

**Spec:** Design approved in chat on 2026-09-10 (brainstorming session, user approved both rows). Approved scope: (1) snapshot warnings rendered in the radar panel, (2) an internals status row. Explicitly out of scope: checklist `result.warnings` duplication, any change to state-classification logic, internals integration into the pro-trader dashboard.

## Design (approved decisions)

- `ProximityReport` gains `warnings: list[str]` populated from `snapshot.warnings` (a copy — `build_snapshot` re-assigns `snapshot.warnings = warnings` after construction, so the report must not hold a live reference). Checklist-level `result.warnings` are NOT carried — radar's `gate:`/`blocked:`/`need:` rows already cover the checklist side.
- Internals status line (plain text, side-agnostic, mirrors what `mes check` prints):
  - `TICK {tick:+.0f} (thr ±{effective_threshold:.0f})` — effective threshold from `snapshot.tick_effective_threshold()`; `TICK unavailable` when None.
  - `ADD {add:+.0f}` / `ADD unavailable`.
  - `VOLD {vold:+.0f}` plus ` slope {slope:+.0f}` when `snapshot.vold_slope()` is not None / `VOLD unavailable`.
  - Segments joined with two spaces; returns `None` iff tick, add, and vold are all None.
- Panel rendering: one `internals:` row right after the state banner (`internals: unavailable` when the formatter returns None), and one `[yellow]warn:[/yellow] …` row per snapshot warning after the `need:` row — matching the existing `gate:`/`blocked:`/`need:` prefix style.
- `radar --json` picks up both fields automatically via `dataclasses.asdict`; no code change.

## Global Constraints

- Python 3.11; tests run with `python -m pytest` from the repo root (`/Users/akundrock/sandbox/TradingAgents`).
- Test naming/markers: functions prefixed `test_`, marked `@pytest.mark.unit`; tests live in `tests/test_mes_radar.py` for this feature (report-level and render-level), reusing the existing `_make_snapshot`/`_make_result` helpers and `tests/mes_factories.py` builders.
- Snapshot API surface (do not change): `snapshot.add`, `snapshot.tick`, `snapshot.vold` (`float | None`), `snapshot.tick_effective_threshold() -> float`, `snapshot.vold_slope() -> float | None`, `snapshot.warnings: list[str]`.
- Factory snapshot defaults (`make_snapshot()` without overrides): SPY series carries 6 bars of `(add=300.0, tick=100.0, vold=1000.0)`; config defaults are `tick_lookback=20`, `tick_multiplier=1.5`, `vold_slope_bars=6`, `tick_sustain_bars=3`.
- Formatting conventions: internals values formatted `{value:+.0f}` (matches checklist.py `_tick_observed` / `$ADD confirms` labels).
- Branch: `tick-internals-data-fidelity` in the main repo; no rebase/merge in this plan.
- Shell note: commit messages containing `$TICK` must use single quotes (double quotes trigger shell globbing).
- Full suite is green at plan time (1286 passed / 2 skipped); it must stay green after every task.

---

### Task 1: `format_internals_status` — pure formatter in render.py

**Files:**
- Modify: `tradingagents/mes/render.py` (append at end of file)
- Test: `tests/test_mes_radar.py` (append new import + test section)

**Interfaces:**
- Consumes: `MesSnapshot` (already imported in render.py) and the snapshot surface listed in Global Constraints.
- Produces: `format_internals_status(snapshot: MesSnapshot) -> str | None` — `None` iff all three of add/tick/vold are None; otherwise a two-space-joined status string. Task 2 stores this on `ProximityReport.internals_status`.

- [x] **Step 1: Write the failing tests**

In `tests/test_mes_radar.py`, extend the existing `from tradingagents.mes.render import ...` — there is none today, so add this import line next to the other `tradingagents.mes` imports:

```python
from tradingagents.mes.render import format_internals_status
```

Append to the end of the file:

```python
# ---------------------------------------------------------------------------
# Internals status line (render.format_internals_status)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_internals_status_formats_all_three():
    snap = _make_snapshot()
    line = format_internals_status(snap)
    assert line is not None
    assert line.startswith("TICK +100 (thr ±")
    assert "ADD +300" in line
    assert "VOLD +1000 slope +0" in line


@pytest.mark.unit
def test_internals_status_none_when_all_missing():
    spy = make_spy_series(internals=[(None, None, None)] * 6)
    snap = make_snapshot(spy=spy)
    assert format_internals_status(snap) is None


@pytest.mark.unit
def test_internals_status_marks_unavailable_segments():
    spy = make_spy_series(internals=[(None, 250.0, None)] * 6)
    snap = make_snapshot(spy=spy)
    line = format_internals_status(snap)
    assert line is not None
    assert "TICK +250 (thr ±" in line
    assert "ADD unavailable" in line
    assert "VOLD unavailable" in line
```

Rationale for the loose assertions: the effective threshold is config-dependent (factory default config yields `mean(abs(100)) * 1.5 = ±150`, but the test must not couple to config), so the prefix `TICK +100 (thr ±` is asserted without the number; `vold_slope()` on six identical `1000.0` bars is `0.0`, formatted `+0`.

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_radar.py -q -k "internals_status"`
Expected: FAIL with `ImportError: cannot import name 'format_internals_status'`.

- [x] **Step 3: Write the implementation**

Append to `tradingagents/mes/render.py`:

```python
def format_internals_status(snapshot: MesSnapshot) -> str | None:
    """Compact $ADD/$TICK/$VOLD status for the radar panel (plain text).

    Returns ``None`` when none of the three internals publish, which the CLI
    renders as "internals unavailable". Segments report "unavailable"
    individually so a partial feed stays visible.
    """
    add, tick, vold = snapshot.add, snapshot.tick, snapshot.vold
    if add is None and tick is None and vold is None:
        return None
    if tick is None:
        tick_seg = "TICK unavailable"
    else:
        tick_seg = f"TICK {tick:+.0f} (thr ±{snapshot.tick_effective_threshold():.0f})"
    add_seg = "ADD unavailable" if add is None else f"ADD {add:+.0f}"
    if vold is None:
        vold_seg = "VOLD unavailable"
    else:
        vold_seg = f"VOLD {vold:+.0f}"
        slope = snapshot.vold_slope()
        if slope is not None:
            vold_seg += f" slope {slope:+.0f}"
    return "  ".join([tick_seg, add_seg, vold_seg])
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_radar.py -q -k "internals_status"`
Expected: all pass, 0 failures.

- [x] **Step 5: Commit**

```bash
git add tradingagents/mes/render.py tests/test_mes_radar.py
git commit -m 'feat(mes): format_internals_status for radar internals row'
```


### Task 2: `ProximityReport` carries `warnings` and `internals_status`

**Files:**
- Modify: `tradingagents/mes/radar.py` — dataclass field additions after `proximity_band` (line 110-111) and the `build_proximity` return block (lines 258-278)
- Test: `tests/test_mes_radar.py`

**Interfaces:**
- Consumes: `format_internals_status(snapshot) -> str | None` from Task 1 (`from .render import format_internals_status`); `MesSnapshot.warnings: list[str]`.
- Produces: `ProximityReport.warnings: list[str]` (default empty list) and `ProximityReport.internals_status: str | None` (default None), populated by `build_proximity`. Task 3 reads exactly these two attribute names.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_mes_radar.py` (no new imports — `build_proximity`, `make_snapshot`, `make_spy_series` are already imported at the top of the file):

```python
# ---------------------------------------------------------------------------
# Snapshot warnings + internals status on ProximityReport
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_warnings_carried_from_snapshot():
    snap = _make_snapshot()
    snap.warnings = ["$TICK data sparse: only 2/78 5m bars readable"]
    report = build_proximity(snap, _make_result())
    assert report.warnings == ["$TICK data sparse: only 2/78 5m bars readable"]


@pytest.mark.unit
def test_warnings_is_a_copy_not_a_live_reference():
    snap = _make_snapshot()
    snap.warnings = ["first"]
    report = build_proximity(snap, _make_result())
    snap.warnings.append("appended later")
    assert report.warnings == ["first"]


@pytest.mark.unit
def test_warnings_empty_by_default():
    snap = _make_snapshot()
    report = build_proximity(snap, _make_result())
    assert report.warnings == []


@pytest.mark.unit
def test_internals_status_carried_on_report():
    snap = _make_snapshot()
    report = build_proximity(snap, _make_result())
    assert report.internals_status is not None
    assert report.internals_status.startswith("TICK +100 (thr ±")


@pytest.mark.unit
def test_report_internals_status_none_when_all_missing():
    spy = make_spy_series(internals=[(None, None, None)] * 6)
    snap = make_snapshot(spy=spy)
    report = build_proximity(snap, _make_result())
    assert report.internals_status is None
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_radar.py -q -k "warnings_carried or warnings_is_a_copy or warnings_empty or internals_status_carried or status_none_when"`
Expected: FAIL — `AttributeError: 'ProximityReport' object has no attribute 'warnings'` (first test) and `... 'internals_status'` (fourth test). Note: `-k "status_none_when"` also matches Task 1's formatter test `test_internals_status_none_when_all_missing`, which already passes at this point — that is expected and harmless.

- [x] **Step 3: Write the implementation**

Edit 1 — in `tradingagents/mes/radar.py`, the `ProximityReport` dataclass currently ends with (lines 110-111):

```python
    proximity_band: float = 4.0
    """Band width in points used to classify near_level / SetupState."""
```

Replace that exact block with:

```python
    proximity_band: float = 4.0
    """Band width in points used to classify near_level / SetupState."""
    warnings: list[str] = field(default_factory=list)
    """Snapshot data-quality warnings (sparse $TICK coverage, missing internals, pre-open note)."""
    internals_status: str | None = None
    """Plain-text $ADD/$TICK/$VOLD status from render.format_internals_status; None when unavailable."""
```

Edit 2 — add the formatter import next to the existing relative imports at the top of `tradingagents/mes/radar.py` (after `from .snapshot import MesSnapshot`, line 24):

```python
from .render import format_internals_status
```

Edit 3 — in `build_proximity`, the return block currently ends (lines 276-278):

```python
        levels_above=above,
        levels_below=below,
        near_level=near,
        proximity_band=round(proximity_band, 2),
    )
```

Replace the last line of the keyword list with:

```python
        levels_above=above,
        levels_below=below,
        near_level=near,
        proximity_band=round(proximity_band, 2),
        warnings=list(snapshot.warnings),
        internals_status=format_internals_status(snapshot),
    )
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_radar.py -q`
Expected: all pass — the new fields have defaults, so existing report constructions and tests are unaffected.

- [x] **Step 5: Commit**

```bash
git add tradingagents/mes/radar.py tests/test_mes_radar.py
git commit -m 'feat(mes): ProximityReport carries snapshot warnings and internals status'
```


### Task 3: Render the internals status row and warning rows in the radar panel

**Files:**
- Modify: `cli/mes.py` — direct import insertion after line 52, `_render_radar` (lines 516-578), `radar` command docstring (line 597)
- Test: `tests/test_mes_radar.py`

**Interfaces:**
- Consumes: `ProximityReport.warnings: list[str]` and `ProximityReport.internals_status: str | None` (Task 2).
- Produces: no new public API — only the Rich panel gains rows. The watch loop, `--json` path, and alert logic are untouched.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_mes_radar.py` — add these imports at the top alongside the existing ones (this import style matches `tests/test_mes_cli_trade.py`, which already imports `cli.mes` in tests):

```python
import io

from rich.console import Console
from typer.testing import CliRunner

from cli import mes as mes_cli

radar_runner = CliRunner()


def _render_table_to_text(table) -> str:
    """Render a Rich table/grid to plain text for substring assertions."""
    console = Console(file=io.StringIO(), width=160, legacy_windows=False)
    console.print(table)
    return console.file.getvalue()
```

Append the tests:

```python
# ---------------------------------------------------------------------------
# Radar panel rendering: internals status + snapshot warnings
# ---------------------------------------------------------------------------


def _invoke_radar(snapshot, monkeypatch):
    from tests.mes_factories import DEFAULT_AS_OF

    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snapshot)
    monkeypatch.setattr(mes_cli, "_market_now", lambda cfg: DEFAULT_AS_OF)
    return radar_runner.invoke(mes_cli.mes_app, ["radar", "--no-watch"])


@pytest.mark.unit
def test_radar_panel_shows_internals_status(monkeypatch):
    snap = _make_snapshot()
    result = _invoke_radar(snap, monkeypatch)
    assert result.exit_code == 0
    assert "internals:" in result.output
    assert "TICK +100" in result.output
    assert "ADD +300" in result.output


@pytest.mark.unit
def test_radar_panel_shows_snapshot_warnings(monkeypatch):
    snap = _make_snapshot()
    snap.warnings = ["$TICK data sparse: only 2/78 5m bars readable"]
    result = _invoke_radar(snap, monkeypatch)
    assert result.exit_code == 0
    assert "warn:" in result.output
    assert "$TICK data sparse" in result.output


@pytest.mark.unit
def test_radar_panel_internals_unavailable_row(monkeypatch):
    spy = make_spy_series(internals=[(None, None, None)] * 6)
    snap = make_snapshot(mes=make_mes_series(), spy=spy)
    result = _invoke_radar(snap, monkeypatch)
    assert result.exit_code == 0
    assert "internals unavailable" in result.output
    assert "warn:" not in result.output


@pytest.mark.unit
def test_radar_panel_renders_report_without_warnings_cleanly(monkeypatch):
    snap = _make_snapshot()
    report = build_proximity(snap, _make_result())
    text = _render_table_to_text(mes_cli._render_radar(report, snap.as_of))
    assert "warn:" not in text
```

Why substring assertions: Rich may wrap long rows at the terminal width; every asserted fragment (`internals:`, `TICK +100`, `ADD +300`, `warn:`, `$TICK data sparse`, `internals unavailable`) is short enough that Rich cannot split it at width 160.

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_radar.py -q -k "radar_panel or renders_report_without_warnings"`
Expected: FAIL — `"internals:"` not present in panel output (row not implemented yet); the fourth test may already pass (rendering is warning-free today) — that is fine, it pins the invariant.

- [x] **Step 3: Wire the rows into `_render_radar`**

In `cli/mes.py`, `_render_radar(report: ProximityReport, as_of: datetime) -> Table` builds rows in this order: header, state banner, gates, blocked, need, level tape. It receives only the report — the report now carries everything needed (Task 2).

Edit 1 — `cli/mes.py` already imports render helpers via the package import at lines 35-51; add a direct module import next to the other direct imports (after `from tradingagents.mes.stop_quality import build_stop_quality_report`, line 52):

```python
from tradingagents.mes.render import format_internals_status
```


Edit 2 — immediately after the state banner row (the line reading `outer.add_row(Text.from_markup(f"[{state_style}]{state_label}[/{state_style}]"))`), insert:

```python
    # ---- Internals status ----
    if report.internals_status is not None:
        outer.add_row(Text.from_markup(f"  [dim]internals:[/dim] {report.internals_status}"))
    else:
        outer.add_row(Text.from_markup("  [dim]internals: unavailable[/dim]"))
```

Edit 3 — immediately after the "Missing items" block (after the line `outer.add_row(Text.from_markup(f"  [yellow]need:[/yellow] {items_txt}{suffix}"))` closes its `if`), insert the warnings rows:

```python
    # ---- Snapshot data warnings ----
    for warning in report.warnings:
        outer.add_row(Text.from_markup(f"  [yellow]warn:[/yellow] {warning}"))
```

Edit 4 — update the `radar` command docstring (line ~597) first paragraph from:

```python
    """Compact live proximity view: how close is a valid MES trade entry?

    Runs without the LLM gatekeeper and does not write to the journal.
```

to:

```python
    """Compact live proximity view: how close is a valid MES trade entry?

    Shows the live $ADD/$TICK/$VOLD readings and any snapshot data warnings
    (e.g. sparse $TICK coverage) alongside the setup state. Runs without the
    LLM gatekeeper and does not write to the journal.
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_radar.py tests/test_mes_cli_trade.py -q`
Expected: all pass, 0 failures.

- [x] **Step 5: Commit**

```bash
git add cli/mes.py tests/test_mes_radar.py
git commit -m 'feat(mes): radar panel shows internals status and snapshot warnings'
```


### Task 4: Full-suite verification + plan bookkeeping

**Files:**
- Modify: `docs/superpowers/plans/2026-09-10-radar-internals-visibility.md` (this file — tick checkboxes)

- [x] **Step 1: Run the full test suite**

Run: `python -m pytest -q`
Expected: 12 new tests (3 formatter + 5 report + 4 render) on top of the existing suite → **1298 passed, 2 skipped, 0 failures**. If anything fails, fix before proceeding — do not weaken assertions.

- [x] **Step 2: Tick the checkboxes**

Mark every `- [x]` step in this plan as `- [x]` for the tasks completed.

- [x] **Step 3: Commit**

```bash
git add docs/superpowers/plans/2026-09-10-radar-internals-visibility.md
git commit -m 'docs: tick radar-internals-visibility plan checkboxes'
```

---

## Self-Review

**1. Spec coverage** — every approved design decision maps to a task:

| Design decision | Task |
| --- | --- |
| `warnings: list[str]` copy of `snapshot.warnings` on `ProximityReport` | Task 2 (field + `warnings=list(snapshot.warnings)`) |
| Internals status string (`TICK (thr ±)`, `ADD`, `VOLD slope`) | Task 1 (formatter) + Task 2 (field) |
| `internals: unavailable` collapse when all three are None | Task 1 (returns None) + Task 2 (field None) + Task 3 (dim row) |
| `warn:` rows rendered in panel | Task 3 Edit 3 |
| `--json` inherits both fields via `asdict` | No task needed — verified automatic; Task 3 notes it |
| No state-logic changes; checklist `result.warnings` not carried | Constraints + Tasks never touch them |

**2. Placeholder scan:** none — every code step contains complete code; every run step has the exact command.

**3. Type consistency:** `format_internals_status(snapshot: MesSnapshot) -> str | None` (Task 1) matches the import in Task 2 and the panel branch `if report.internals_status is not None` in Task 3. `ProximityReport.warnings: list[str]` declared in Task 2, iterated as `for warning in report.warnings` in Task 3. Both tasks use the identical field names.

