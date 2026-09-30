# MES MR Surfacing + Momentum Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the checklist's mean-reversion (MR) path in the gatekeeper prompt, journal, and levels hint (zero policy change), and add a flag-gated `momentum_mode` so midday trend pullbacks can earn the momentum item without a same-bar SMA/VWAP cross.

**Architecture:** Part A is pure surfacing — `ChecklistResult.mr_*` fields already exist and are computed; we render them (`render.py`), journal them (`journal.py`), and hint them (`levels.py` + `cli/mes.py` + `gatekeeper_agent.py`), always labeled informational so the "LLM may narrow but never widen" invariant is untouched. Part B adds a `momentum_mode` config axis: `'cross'` (default) is byte-identical to today; `'alignment'` relaxes only the same-bar SMA/VWAP cross into agreement behind the unchanged Laguerre gate, and is validated by the named `momentum-alignment` backtest ablation.

**Tech Stack:** Python 3.11 dataclasses, pytest (`@pytest.mark.unit`), Typer CLI, existing `tests/mes_factories.py` builders.

**Spec:** `docs/superpowers/specs/2026-09-29-mes-mr-surfacing-momentum-alignment-design.md` — read it first; this plan argues from it.

## Global Constraints

- The LLM gatekeeper may **narrow but never widen** the deterministic verdict; MR content is informational only. `ChecklistResult.tradeable` is untouched by every task.
- `momentum_mode` defaults to `"cross"`; every default-path verdict stays byte-identical (full suite stays green; ablation registry tests enforce pure overlays).
- `'alignment'` is a strict **superset** of `'cross'` for the momentum item (cross ⇒ alignment, never the reverse); the Laguerre trend gate is unchanged in both modes.
- MR thresholds (`mr_zone_sigma`, `mr_min_confirmations`, `mr_stop_atr_buffer`), gatekeeper strictness/weights, SPY 3-of-5, and MR outcome backfill are **out of scope** — no task touches them.
- Tests use factory numbers from `tests/mes_factories.py` (vwap 99.0, vwap_sigma 0.5 → MR long hammer close 97.5 = z=+3.0); never reuse tuner numbers.
- Run tests as `python -m pytest <paths> -v`; suite baseline is 1520 passed, 2 skipped.
- Backtest invariants hold: overlays are pure `dataclasses.replace` copies; configs are never mutated.
- Precondition: the working tree's in-flight mes-trade-management changes (`journal.py`, `levels.py`, `cli/mes.py`, `config.py`, `management.py` + tests) are committed before Task 1; execute in a worktree created via `superpowers:using-git-worktrees`.

## File Structure

- `tradingagents/mes/render.py` — LLM/CLI markdown; gains MR section (A1).
- `tradingagents/mes/journal.py` — `append_check` gains `mr_*` keys; `summarize_checks` gains MR column (A2).
- `tradingagents/mes/levels.py` — gains `render_mr_levels_hint` + `render_gatekeeper_levels_hint` (A3).
- `tradingagents/agents/mes/gatekeeper_agent.py` — conditional MR interpretation paragraph (A1 prompt half).
- `tradingagents/mes/config.py` — `momentum_mode: str = "cross"` + `MomentumMode` alias (B1).
- `tradingagents/mes/checklist.py` — `_momentum` alignment mode + labeled item (B).
- `tradingagents/mes/backtest/ablations.py` — named `momentum-alignment` overlay (B validation).
- Tests: `tests/test_mes_render.py` (new), `tests/test_mes_journal.py`, `tests/test_mes_levels.py`, `tests/test_mes_agents.py`, `tests/test_mes_config.py`, `tests/test_mes_checklist.py`, `tests/test_mes_ablations.py`.

### Task 1: Journal — MR fields on check records + MR column in summarize_checks

**Files:**
- Modify: `tradingagents/mes/journal.py` (helper `_mr_label` after `_location_label` ~line 137; `append_check` record ~line 194; `summarize_checks` ~line 458)
- Test: `tests/test_mes_journal.py`

**Interfaces:**
- Consumes: `ChecklistResult.mr_side/mr_entry/mr_zone/mr_trigger/mr_confirmations/mr_required/mr_score/mr_stop/mr_target` (already exist), journal helpers `_num()` and `_first_line()`.
- Produces: check records carry keys `mr_side` (str|None), `mr_entry`/`mr_zone`/`mr_trigger` (bool), `mr_confirmations`/`mr_required`/`mr_score` (int), `mr_stop`/`mr_target` (float|None); module-level `_mr_label(check: dict) -> str` returning `"long ENTRY"`, `"long watch"`, or `"—"`; summary header `| Time | Side | Frame | Score | Tier | Gates | Tradeable | Location | MR | Verdict |`.

- [x] **Step 1: Write the failing tests**

In `tests/test_mes_journal.py` (imports already include `evaluate`, `make_mes_series`, `make_snapshot`), append three tests. First add `load_mes_config` to the `tradingagents.mes.config` import line (new import in this file):

```python
from tradingagents.mes.config import load_mes_config
```

```python
@pytest.mark.unit
def test_check_record_captures_mr_fields_when_mr_fires(journal):
    mes = make_mes_series(
        bar_kwargs={"open_": 97.55, "high": 97.6, "low": 94.5, "close": 97.5, "volume": 1500.0}
    )
    snapshot = make_snapshot(
        cfg=load_mes_config({"enable_mean_reversion": True, "mr_min_confirmations": 1}),
        mes=mes,
    )
    result = evaluate(snapshot, "long")
    assert result.mr_entry is True  # non-vacuous: the record must carry a fired MR
    journal.append_check(snapshot=snapshot, result=result)

    record = journal.load_checks("2026-03-30")[0]
    assert record["mr_side"] == "long"
    assert record["mr_entry"] is True
    assert record["mr_zone"] is True
    assert record["mr_trigger"] is True
    assert record["mr_confirmations"] == 1
    assert record["mr_required"] == 1
    assert record["mr_score"] == result.mr_score
    assert record["mr_stop"] == result.mr_stop
    assert record["mr_target"] == result.mr_target


@pytest.mark.unit
def test_check_record_mr_fields_default_when_mr_is_off(journal):
    snapshot = make_snapshot()
    journal.append_check(snapshot=snapshot, result=evaluate(snapshot, "long"))

    record = journal.load_checks("2026-03-30")[0]
    assert record["mr_side"] is None
    assert record["mr_entry"] is False
    assert record["mr_stop"] is None
    assert record["mr_target"] is None


@pytest.mark.unit
def test_summarize_checks_mr_column(journal):
    fired = make_snapshot(
        cfg=load_mes_config({"enable_mean_reversion": True, "mr_min_confirmations": 1}),
        mes=make_mes_series(
            bar_kwargs={"open_": 97.55, "high": 97.6, "low": 94.5, "close": 97.5, "volume": 1500.0}
        ),
    )
    journal.append_check(snapshot=fired, result=evaluate(fired, "long"), verdict_markdown="**Verdict**: Wait")
    plain = make_snapshot()
    journal.append_check(snapshot=plain, result=evaluate(plain, "long"), verdict_markdown="**Verdict**: Wait")

    summary = journal.summarize_checks("2026-03-30")
    assert "| Time | Side | Frame | Score | Tier | Gates | Tradeable | Location | MR | Verdict |" in summary
    assert "long ENTRY" in summary
    assert "— |" in summary
```

Also update the existing header assertion in `test_summarize_checks_shows_location_and_frame` (tests/test_mes_journal.py:196) to the same new header string with `| MR |` inserted before `| Verdict |`.

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_journal.py -v -k "mr or MR or summarize"`
Expected: FAIL with `KeyError: 'mr_side'` (append_check does not write `mr_*` keys yet).

- [x] **Step 3: Implement**

In `tradingagents/mes/journal.py`:

(a) After the `_location_label` function (~line 137), add the label helper:

```python
def _mr_label(check: dict) -> str:
    """One compact MR cell: 'long ENTRY' when the fade plan fired, 'long watch'
    for an in-zone candidate, '—' otherwise. Records written before MR capture
    carry no ``mr_side`` key and read as '—', never a guess."""
    side = check.get("mr_side")
    if not side:
        return "—"
    return f"{side} ENTRY" if check.get("mr_entry") else f"{side} watch"
```

(b) In `append_check`, right after the `"orb_low"` line of the record dict, add:

```python
            "mr_side": result.mr_side,
            "mr_entry": bool(result.mr_entry),
            "mr_zone": bool(result.mr_zone),
            "mr_trigger": bool(result.mr_trigger),
            "mr_confirmations": result.mr_confirmations,
            "mr_required": result.mr_required,
            "mr_score": result.mr_score,
            "mr_stop": _num(result.mr_stop),
            "mr_target": _num(result.mr_target),
```

(c) In `summarize_checks`, replace the header rows with:

```python
        rows = [
            "| Time | Side | Frame | Score | Tier | Gates | Tradeable | Location | MR | Verdict |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
```

and replace the `rows.append(...)` `.format(...)` row with:

```python
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
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_journal.py -v`
Expected: PASS — all new MR tests plus the updated location/frame header test.

- [x] **Step 5: Commit**

```bash
git add tradingagents/mes/journal.py tests/test_mes_journal.py
git commit -m "feat(mes-journal): capture mr_* fields and add MR column to summarize_checks"
```

### Task 2: render_checklist — informational MR candidate section

**Files:**
- Modify: `tradingagents/mes/render.py` (insert after the opening-range block, ~lines 60-63, before the `if result.gate_reasons:` block)
- Create: `tests/test_mes_render.py`

**Interfaces:**
- Consumes: `ChecklistResult.mr_side/mr_entry/mr_required/mr_confirmations/mr_score/mr_stop/mr_target/vwap` (existing fields).
- Produces: when `result.mr_side` is set, `render_checklist` output contains a `### Mean-reversion candidate (informational)` section plus the fixed prompt paragraph; when `mr_side is None`, output is unchanged (byte-identical default behavior). Later tasks (3, gatekeeper paragraph) rely on the stable header string `### Mean-reversion candidate (informational)`.

- [x] **Step 1: Write the failing tests**

Create `tests/test_mes_render.py`:

```python
"""Tests for MR surfacing in render_checklist (spec Part A1)."""

from __future__ import annotations

import dataclasses

import pytest

from tradingagents.mes.checklist import evaluate
from tradingagents.mes.config import load_mes_config
from tradingagents.mes.render import render_checklist

from tests.mes_factories import make_mes_series, make_snapshot

# Factory geometry (see tests/test_mes_mean_reversion.py): deep hammer close
# 97.5 vs vwap 99.0, sigma 0.5 -> z=+3.0; volume 1500 confirms; stop = low 94.5
# - 1.0 buffer * atr 1.0 = 93.5; target = vwap.
LONG_HAMMER_BAR = {"open_": 97.55, "high": 97.6, "low": 94.5, "close": 97.5, "volume": 1500.0}
MR_CFG = {"enable_mean_reversion": True, "mr_min_confirmations": 1}


@pytest.mark.unit
def test_default_render_has_no_mr_section():
    result = evaluate(make_snapshot(), "long")
    assert result.mr_side is None
    assert "Mean-reversion" not in render_checklist(result, live=True)


@pytest.mark.unit
def test_mr_candidate_renders_plan_and_additive_notice():
    snapshot = make_snapshot(cfg=load_mes_config(MR_CFG), mes=make_mes_series(bar_kwargs=LONG_HAMMER_BAR))
    result = evaluate(snapshot, "long")
    assert result.mr_entry is True  # non-vacuous

    out = render_checklist(result, live=True)
    assert "### Mean-reversion candidate (informational)" in out
    assert "Fade side: long (ENTRY)" in out
    assert "MR confirmations: 1/1" in out
    assert f"stop {result.mr_stop:.2f}" in out
    assert f"target {result.mr_target:.2f} (VWAP)" in out
    assert "never widens the deterministic verdict" in out
    assert "trend ruling governs" in out


@pytest.mark.unit
def test_mr_watch_state_renders_without_entry_label():
    # z=+3.0 zone holds but volume surge is absent -> mr_confirmations 0 < 1.
    snapshot = make_snapshot(
        cfg=load_mes_config({"enable_mean_reversion": True}),
        mes=make_mes_series(bar_kwargs={"open_": 97.55, "high": 97.6, "low": 94.5, "close": 97.5}),
    )
    result = evaluate(snapshot, "long")
    assert result.mr_side == "long" and result.mr_entry is False

    out = render_checklist(result, live=True)
    assert "Fade side: long (watch)" in out
    assert "ENTRY" not in out


@pytest.mark.unit
def test_mr_section_tolerates_unpriced_plan():
    result = evaluate(make_snapshot(cfg=load_mes_config(MR_CFG), mes=make_mes_series(bar_kwargs=LONG_HAMMER_BAR)), "long")
    unpriced = dataclasses.replace(result, mr_stop=None, mr_target=None)
    out = render_checklist(unpriced, live=True)
    section = out.split("### Mean-reversion candidate")[1]
    assert "Plan: stop" not in section  # unpriced plan is omitted, not guessed
    assert "Plan: target" not in section
```

No extra imports are needed beyond those already in the block.

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_render.py -v`
Expected: FAIL — `AssertionError: 'Mean-reversion' not in ...` for the first three tests; the last test fails on split key (no section).

- [x] **Step 3: Implement**

In `tradingagents/mes/render.py`, inside `render_checklist`, between the opening-range `if` block (lines 60-63) and `if result.gate_reasons:` (line 64), insert:

```python
    if result.mr_side is not None:
        mr_state = "ENTRY" if result.mr_entry else "watch"
        parts.extend(
            [
                "",
                "### Mean-reversion candidate (informational)",
                "",
                f"- Fade side: {result.mr_side} ({mr_state})",
                f"- MR confirmations: {result.mr_confirmations}/{result.mr_required}",
            ]
        )
        if result.mr_stop is not None:
            parts.append(
                f"- Plan: stop {result.mr_stop:.2f}, target {result.mr_target:.2f} (VWAP)"
            )
        parts.extend(
            [
                "",
                "**MR notice:** this fade candidate is additive evidence computed in parallel "
                "to the trend checklist. It never widens the deterministic verdict above: a "
                "NOT TRADEABLE ruling still forces Stand Down, and the trend ruling governs "
                "any Take. Treat the MR plan (fade stop, VWAP target) as context, not as the "
                "trade's level set.",
            ]
        )
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_render.py tests/test_mes_prior_session.py tests/test_mes_mean_reversion.py -v`
Expected: PASS — new tests green, and the existing prior-session render tests stay green (no default-path change).

- [x] **Step 5: Commit**

```bash
git add tradingagents/mes/render.py tests/test_mes_render.py
git commit -m "feat(mes-render): informational MR candidate section in render_checklist"
```

### Task 3: Levels hint — informational MR block when MR fires + CLI wiring

**Files:**
- Modify: `tradingagents/mes/levels.py` (add two functions after `render_trade_levels_hint`, ~line 251)
- Modify: `cli/mes.py:350-363` (hint assembly in the `check` command's gatekeeper call)
- Test: `tests/test_mes_levels.py`

**Interfaces:**
- Consumes: `TradeLevels`, `render_trade_levels_hint(levels)` (existing), `ChecklistResult.mr_side/mr_entry/mr_stop/mr_target` (existing).
- Produces:
  - `render_mr_levels_hint(result) -> str | None` — multi-line block, `None` unless MR fires (`mr_entry` true, `mr_side` in `{"long","short"}`, both levels priced).
  - `render_gatekeeper_levels_hint(levels: TradeLevels | None, result) -> str` — structural hint plus the MR block appended after a newline when it exists; `""` when neither exists.
  - The CLI passes `render_gatekeeper_levels_hint(levels_hint, result)` as `trade_levels_hint`.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_mes_levels.py` (extend its existing `from tradingagents.mes.levels import (...)` with `render_gatekeeper_levels_hint, render_mr_levels_hint`):

```python
# render_mr_levels_hint — informational MR block (spec A3)


class _FakeMR:
    def __init__(self, mr_entry=True, mr_side="long", mr_stop=94.5, mr_target=99.0):
        self.mr_entry = mr_entry
        self.mr_side = mr_side
        self.mr_stop = mr_stop
        self.mr_target = mr_target


def test_mr_hint_renders_when_mr_fires():
    hint = render_mr_levels_hint(_FakeMR())
    assert hint is not None
    assert "MR fade long: stop 94.50, target 99.00 (VWAP)" in hint
    assert "Informational only" in hint
    assert "the trend checklist's verdict governs" in hint


@pytest.mark.parametrize("kwargs", [
    {"mr_entry": False},           # in-zone/watch -> not fired -> stays out
    {"mr_side": None},
    {"mr_side": "bogus"},
    {"mr_stop": None},
    {"mr_target": None},
])
def test_mr_hint_is_none_unless_mr_fires_with_a_priced_plan(kwargs):
    assert render_mr_levels_hint(_FakeMR(**kwargs)) is None


def test_gatekeeper_hint_appends_mr_block_after_structural_hint():
    levels = suggest_trade_levels("long", 100.0, 99.0, 1.0, orb_high=101.0, orb_low=96.0)
    text = render_gatekeeper_levels_hint(levels, _FakeMR())
    assert "Entry zone:" in text  # structural hint still first
    assert "MR fade long" in text
    assert text.index("Entry zone:") < text.index("MR fade long")


def test_gatekeeper_hint_renders_mr_block_alone_without_structural_levels():
    text = render_gatekeeper_levels_hint(None, _FakeMR())
    assert text.startswith("- MR fade long: stop 94.50")


def test_gatekeeper_hint_is_empty_without_levels_or_mr():
    assert render_gatekeeper_levels_hint(None, _FakeMR(mr_entry=False)) == ""
```

The imports must match the module's existing import style (`from tradingagents.mes.levels import ...`).

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_levels.py -v -k mr`
Expected: FAIL — `ImportError: cannot import name 'render_mr_levels_hint'`.

- [x] **Step 3: Implement**

In `tradingagents/mes/levels.py`, immediately after `render_trade_levels_hint`, add:

```python
def render_mr_levels_hint(result) -> str | None:
    """Informational MR block for the gatekeeper prompt; None unless MR fires.

    ``mr_entry`` means zone + reversal trigger + confirmations all held (and the
    session had started) — a merely in-zone "watch" candidate never reaches the
    prompt, so the gatekeeper is never handed trade levels the trend verdict
    cannot bless (spec A3, user sub-decision 2).
    """
    if not getattr(result, "mr_entry", False) or result.mr_side not in ("long", "short"):
        return None
    if result.mr_stop is None or result.mr_target is None:
        return None
    return "\n".join(
        [
            f"- MR fade {result.mr_side}: stop {result.mr_stop:.2f}, "
            f"target {result.mr_target:.2f} (VWAP)",
            "- Informational only — the trend checklist's verdict governs; this block never "
            "licenses a counter-trend Take and its levels must not replace the trend trade's.",
        ]
    )


def render_gatekeeper_levels_hint(levels: TradeLevels | None, result) -> str:
    """Assemble the gatekeeper's full levels hint.

    The structural suggestion (when one exists) stays first and authoritative;
    a fired MR candidate is appended as informational context. Empty string
    when there is nothing to show (the CLI omits the section entirely).
    """
    lines = render_trade_levels_hint(levels) if levels else ""
    mr_block = render_mr_levels_hint(result)
    if mr_block:
        lines = f"{lines}\n{mr_block}" if lines else mr_block
    return lines
```

In `cli/mes.py`, update the levels import block (currently `from tradingagents.mes.levels import (render_trade_levels_hint, suggest_trade_levels_from_snapshot, ...)` at lines 79-80) to also import `render_gatekeeper_levels_hint`, then in the `check` command's gatekeeper call (~lines 352-363) replace:

```python
            levels_hint = suggest_trade_levels_from_snapshot(result.side, snapshot, result)
```

with the same line, and replace

```python
                trade_levels_hint=render_trade_levels_hint(levels_hint) if levels_hint else "",
```

with

```python
                trade_levels_hint=render_gatekeeper_levels_hint(levels_hint, result),
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_levels.py tests/test_mes_cli_trade.py -v`
Expected: PASS — new hint tests green; existing levels and CLI-trade suites unaffected (the gatekeeper call path is only exercised with `gatekeeper is not None`, so live CLI behavior changes only when an LLM gatekeeper is configured).

- [x] **Step 5: Commit**

```bash
git add tradingagents/mes/levels.py cli/mes.py tests/test_mes_levels.py
git commit -m "feat(mes-levels): informational MR block in the gatekeeper levels hint when MR fires"
```

### Task 4: Gatekeeper prompt — MR interpretation paragraph

**Files:**
- Modify: `tradingagents/agents/mes/gatekeeper_agent.py` (prompt construction, after the TRADE LEVEL RULES block, ~line 48)
- Test: `tests/test_mes_agents.py`

**Interfaces:**
- Consumes: the stable section header `### Mean-reversion candidate (informational)` produced by Task 2's `render_checklist`.
- Produces: when `checklist_markdown` contains `"Mean-reversion candidate"`, the prompt gains a fixed `**MEAN-REVERSION NOTE**` paragraph; without it, the prompt is unchanged (tests `test_gatekeeper_prompt_*` must stay green).

- [x] **Step 1: Write the failing test**

Append to `tests/test_mes_agents.py` (next to the other gatekeeper prompt tests; reuse `FakeLLM` and `_gonogo()` already defined there):

```python
@pytest.mark.unit
def test_gatekeeper_prompt_carries_mr_notice_when_candidate_present():
    llm = FakeLLM({TradeGoNoGo: _gonogo()})
    create_mes_gatekeeper_agent(llm)(
        checklist_markdown="### Mean-reversion candidate (informational)\n- Fade side: long (watch)",
        market_context="context",
        tradeable=False,
    )
    prompt = llm.prompts[0]
    assert "**MEAN-REVERSION NOTE:**" in prompt
    assert "never widens the deterministic verdict" in prompt
    assert "trend ruling governs" in prompt


@pytest.mark.unit
def test_gatekeeper_prompt_has_no_mr_notice_without_candidate():
    llm = FakeLLM({TradeGoNoGo: _gonogo()})
    create_mes_gatekeeper_agent(llm)(
        checklist_markdown="## MES Entry Checklist",
        market_context="context",
        tradeable=True,
    )
    assert "MEAN-REVERSION" not in llm.prompts[0]
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_agents.py -v -k "gatekeeper"`
Expected: the new MR test FAILS (`'MEAN-REVERSION' not found`); all existing gatekeeper tests stay green.

- [x] **Step 3: Implement**

In `tradingagents/agents/mes/gatekeeper_agent.py`, inside `run`, immediately after the block that appends the `trade_levels_hint` section (`if trade_levels_hint.strip():` … ends at line 67) and before `if hypothesis.strip():` (line 69), insert:

```python
        if "Mean-reversion candidate" in checklist_markdown:
            prompt += """

---

**MEAN-REVERSION NOTE:**
The checklist may also report a parallel mean-reversion (MR) candidate — a
counter-trend fade at the ±sigma zone with its own stop and a VWAP target. It is
additive evidence and never widens the deterministic verdict above: a NOT
TRADEABLE ruling still forces "Stand Down", and the trend ruling governs any
Take. Use the MR plan only as fade context (location, stop, VWAP target) in your
reasoning or in `what_would_change_my_mind`; never copy its levels into the
trade level fields."""
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_agents.py -v`
Expected: PASS — the new paragraph appears only when the checklist markdown contains the MR section; all existing gatekeeper prompt tests stay green.

- [x] **Step 5: Commit**

```bash
git add tradingagents/agents/mes/gatekeeper_agent.py tests/test_mes_agents.py
git commit -m "feat(mes-gatekeeper): interpret MR candidates as additive context in the prompt"
```

### Task 5: Config — `momentum_mode` field with `MomentumMode` alias

**Files:**
- Modify: `tradingagents/mes/config.py` (dataclass field + alias map entry)
- Test: `tests/test_mes_config.py`

**Interfaces:**
- Consumes: nothing (leaf change).
- Produces: `MesChecklistConfig.momentum_mode: str = "cross"` (all later tasks read `cfg.momentum_mode`); alias `"MomentumMode" -> "momentum_mode"` in `_TUNER_FIELD_ALIASES` so `from_dict`/`load_mes_config(overrides)`/env (`TRADINGAGENTS_MES_MOMENTUM_MODE`) all resolve it; ablations/`to_dict` round-trip pick it up automatically because it is a dataclass field.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_mes_config.py`:

```python
@pytest.mark.unit
def test_momentum_mode_defaults_to_cross():
    """'cross' is the mes-tuner-parity default: byte-identical to today."""
    assert MesChecklistConfig().momentum_mode == "cross"


@pytest.mark.unit
def test_momentum_mode_alias_round_trips_through_from_dict():
    cfg = MesChecklistConfig.from_dict({"MomentumMode": "alignment"})
    assert cfg.momentum_mode == "alignment"
    assert MesChecklistConfig.from_dict(cfg.to_dict()) == cfg


@pytest.mark.unit
def test_momentum_mode_env_override(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_MES_MOMENTUM_MODE", "alignment")
    assert load_mes_config().momentum_mode == "alignment"


@pytest.mark.unit
def test_unknown_momentum_mode_string_round_trips_untouched():
    # No enum validation: unknown strings degrade to historic cross behavior
    # in checklist._momentum (pinned by test_mes_checklist.py), so a typo can
    # never silently change scoring into an unreviewed third mode.
    assert MesChecklistConfig.from_dict({"MomentumMode": "alginment"}).momentum_mode == "alginment"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_config.py -v -k momentum`
Expected: FAIL with `AttributeError: 'MesChecklistConfig' object has no attribute 'momentum_mode'` (and `TypeError` on `from_dict` round-trip equality before the field exists).

- [x] **Step 3: Implement**

In `tradingagents/mes/config.py`:

1. In `_TUNER_FIELD_ALIASES`, directly under `"EnableMomentum": "enable_momentum",` (line 17), add:

```python
    "MomentumMode": "momentum_mode",
```

2. In the dataclass, directly after `divergence_veto: bool = True` (line 161), add:

```python
    # B-port: momentum evaluation mode. "cross" (default) is the historic
    # mes-tuner-parity behavior: the momentum item requires a same-bar SMA/VWAP
    # cross plus the Laguerre trend gate. "alignment" relaxes ONLY the same-bar
    # cross into SMA/VWAP agreement; the Laguerre trend gate is unchanged, so
    # alignment is a strict superset — it can pass where cross fails and never
    # the reverse. Default keeps every default-path verdict byte-identical
    # (mes-tuner parity); validate via the "momentum-alignment" backtest
    # ablation before opting in live.
    momentum_mode: str = "cross"
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_config.py tests/test_mes_ablations.py -v`
Expected: PASS — field exists, alias maps, `from_dict(cfg.to_dict()) == cfg` round-trips (config equality is field-wise, so the new default field round-trips cleanly).

- [x] **Step 5: Commit**

```bash
git add tradingagents/mes/config.py tests/test_mes_config.py
git commit -m "feat(mes-config): momentum_mode flag (cross default) with MomentumMode alias"
```

### Task 6: Checklist — `alignment` momentum semantics + mode-labeled item

**Files:**
- Modify: `tradingagents/mes/checklist.py` (`_momentum` at lines 137-147; the momentum signal tuple in `_evaluate_mes` ~line 388)
- Test: `tests/test_mes_checklist.py`

**Interfaces:**
- Consumes: `cfg.momentum_mode: str` (Task 5); `SeriesState` flags `sma/prev_sma/vwap/prev_vwap/laguerre/prev_laguerre` + `*_ready` (existing).
- Produces: `_momentum(state, cfg, side) -> bool` (unchanged signature) honoring `cfg.momentum_mode`; the momentum item `name`/`threshold` strings label the active mode. Cross mode keeps the exact historic strings `"Momentum (SMA/VWAP cross + Laguerre)"` / `"cross with trend"` (mes-tuner diffing stays stable).

- [x] **Step 1: Write the failing tests**

Append to `tests/test_mes_checklist.py` (the file already imports `find_item`, `evaluate`, `make_snapshot`, `make_mes_series`; add `load_mes_config` to its config import if missing):

```python
@pytest.mark.unit
def test_momentum_alignment_passes_a_pullback_without_a_fresh_cross():
    """Midday pullback: SMA held above VWAP on both bars (no fresh cross) and
    the Laguerre gate is healthy — cross mode denies, alignment grants."""
    kwargs = dict(
        prev_sma=99.6, prev_vwap=99.0, sma=99.6, vwap=99.0,
        laguerre=0.6, prev_laguerre=0.5, laguerre_ready=True,
    )
    cross = find_item(evaluate(make_snapshot(mes=make_mes_series(**kwargs)), "long").mes_items, "Momentum")
    aligned = find_item(
        evaluate(
            make_snapshot(mes=make_mes_series(**kwargs), cfg=load_mes_config({"momentum_mode": "alignment"})),
            "long",
        ).mes_items,
        "Momentum",
    )
    assert cross.passed is False   # today's behavior: no fresh cross -> deny
    assert aligned.passed is True  # SMA/VWAP agreement + Laguerre gate -> grant


@pytest.mark.unit
def test_momentum_alignment_still_requires_the_laguerre_trend_gate():
    """The Laguerre gate is unchanged in alignment mode: turning-down vetoes."""
    kwargs = dict(
        prev_sma=99.6, prev_vwap=99.0, sma=100.0, vwap=99.0,
        laguerre=0.4, prev_laguerre=0.5, laguerre_ready=True,
    )
    aligned = find_item(
        evaluate(make_snapshot(mes=make_mes_series(**kwargs), cfg=load_mes_config({"momentum_mode": "alignment"})), "long").mes_items,
        "Momentum",
    )
    assert aligned.passed is False


@pytest.mark.unit
def test_momentum_alignment_short_mirror():
    """Short mirror of the pullback grant: SMA held below VWAP both bars, no
    fresh cross, Laguerre still falling-under-0.8 and non-increasing."""
    kwargs = dict(prev_sma=98.6, prev_vwap=99.0, sma=98.6, vwap=99.0,
                  laguerre=0.4, prev_laguerre=0.5, laguerre_ready=True)
    item = find_item(
        evaluate(make_snapshot(mes=make_mes_series(**kwargs), cfg=load_mes_config({"momentum_mode": "alignment"})), "short").mes_items,
        "Momentum",
    )
    assert item.passed is True


@pytest.mark.unit
def test_alignment_is_a_superset_of_cross_never_a_downgrade():
    """Any bar cross-mode passes must also pass alignment (same Laguerre gate)."""
    kwargs = dict(prev_sma=98.0, prev_vwap=99.0, sma=100.0, vwap=99.0,
                  laguerre=0.6, prev_laguerre=0.5, laguerre_ready=True)
    cross = find_item(evaluate(make_snapshot(mes=make_mes_series(**kwargs)), "long").mes_items, "Momentum")
    aligned = find_item(
        evaluate(make_snapshot(mes=make_mes_series(**kwargs), cfg=load_mes_config({"momentum_mode": "alignment"})), "long").mes_items,
        "Momentum",
    )
    assert cross.passed is True
    assert aligned.passed is True


@pytest.mark.unit
def test_unknown_momentum_mode_degrades_to_cross():
    """A typo'd mode must behave exactly like the historic cross mode."""
    kwargs = dict(prev_sma=99.6, prev_vwap=99.0, sma=99.6, vwap=99.0, laguerre_ready=True)
    item = find_item(
        evaluate(make_snapshot(cfg=load_mes_config({"momentum_mode": "alginment"}), mes=make_mes_series(**kwargs)), "long").mes_items,
        "Momentum",
    )
    assert item.passed is False  # no cross -> no momentum, identical to today


@pytest.mark.unit
def test_momentum_item_name_labels_the_active_mode():
    kwargs = dict(prev_sma=98.0, prev_vwap=99.0, sma=100.0, vwap=99.0, laguerre_ready=True)
    aligned = find_item(
        evaluate(make_snapshot(mes=make_mes_series(**kwargs), cfg=load_mes_config({"momentum_mode": "alignment"})), "long").mes_items,
        "Momentum",
    )
    cross = find_item(evaluate(make_snapshot(mes=make_mes_series(**kwargs)), "long").mes_items, "Momentum")
    assert aligned.name == "Momentum (SMA/VWAP alignment + Laguerre)"
    assert cross.name == "Momentum (SMA/VWAP cross + Laguerre)"  # historic, pinned
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_checklist.py -v -k alignment`
Expected: FAIL — `test_momentum_alignment_passes_a_pullback_without_a_fresh_cross` asserts `passed is True` but gets False (alignment semantics do not exist yet); the item-name test fails on the historic name.

- [x] **Step 3: Implement**

In `tradingagents/mes/checklist.py`, replace `_momentum` (lines 137-147) with (note the mode check on **both** side branches — alignment relaxes the cross on either side):

```python
def _momentum(state: SeriesState, cfg: MesChecklistConfig, side: Side) -> bool:
    if not cfg.enable_momentum or not state.laguerre_ready:
        return False
    if not (state.prev_sma_ready and state.prev_vwap_ready and state.sma_ready and state.vwap_ready):
        return False
    alignment = cfg.momentum_mode == "alignment"
    if side == "long":
        trend = state.laguerre > 0.2 and state.laguerre >= state.prev_laguerre
        if alignment:
            # Alignment mode: SMA/VWAP agreement replaces the same-bar cross.
            # The Laguerre gate is unchanged, so alignment is a strict
            # superset of cross — it can grant, never downgrade.
            return state.sma > state.vwap and trend
        return state.prev_sma < state.prev_vwap and state.sma > state.vwap and trend
    trend = state.laguerre < 0.8 and state.laguerre <= state.prev_laguerre
    if alignment:
        return state.sma < state.vwap and trend
    return state.prev_sma > state.prev_vwap and state.sma < state.vwap and trend
```

Then in `_evaluate_mes`, replace the momentum signal tuple so the item labels the active mode — change:

```python
        (
            "Momentum (SMA/VWAP cross + Laguerre)",
            momentum,
            cfg.momentum_score_weight,
            cfg.enable_momentum,
            f"SMA {mes.sma:.2f} vs VWAP {mes.vwap:.2f}, LagRSI {mes.laguerre:.2f}",
            "cross with trend" if long_side else "cross down with trend",
        ),
```

to a mode-aware tuple (compute the label before the tuple so the ternary stays readable — add these two locals just above the `signals: list[...] = [` list, next to the existing `momentum = _momentum(...)` assignment):

```python
    if cfg.momentum_mode == "alignment":
        momentum_name = "Momentum (SMA/VWAP alignment + Laguerre)"
        momentum_threshold = "SMA/VWAP aligned with trend"
    else:
        momentum_name = "Momentum (SMA/VWAP cross + Laguerre)"
        momentum_threshold = "cross with trend" if long_side else "cross down with trend"
```

and change the momentum tuple to:

```python
        (
            momentum_name,
            momentum,
            cfg.momentum_score_weight,
            cfg.enable_momentum,
            f"SMA {mes.sma:.2f} vs VWAP {mes.vwap:.2f}, LagRSI {mes.laguerre:.2f}",
            momentum_threshold,
        ),
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_checklist.py tests/test_mes_config.py tests/test_mes_ablations.py -v`
Expected: PASS — new alignment tests green; every existing momentum test (cross behavior, weights, max-score) stays green proving cross-mode byte-parity.

- [x] **Step 5: Commit**

```bash
git add tradingagents/mes/checklist.py tests/test_mes_checklist.py
git commit -m "feat(mes-checklist): flag-gated momentum alignment mode (Laguerre gate unchanged)"
```

### Task 7: Named ablation overlay `momentum-alignment`

**Files:**
- Modify: `tradingagents/mes/backtest/ablations.py` (module docstring list + `ABLATIONS` dict)
- Test: `tests/test_mes_ablations.py`

**Interfaces:**
- Consumes: `Ablation`, `apply_ablation`, `config_delta`, `PLAN_ORDER` (existing registry/tests).
- Produces: `get_ablation("momentum-alignment")` with `base="checklist"` and `overlay={"momentum_mode": "alignment"}`. The existing registry tests (`test_registry_lists_all_plan_ablations_in_order`, `test_ablation_overlay_changes_exactly_its_declared_fields` parametrized over `PLAN_ORDER`) automatically cover the new entry once `PLAN_ORDER` gains the name — that is why this task updates the test file's `PLAN_ORDER` too.

- [x] **Step 1: Write the failing tests**

In `tests/test_mes_ablations.py`:

1. Append `"momentum-alignment",` to the `PLAN_ORDER` list (after `"dynamic-threshold-off",`).
2. Add the ablation's checklist-level behavior test (pattern: the fixture session replayed with the overlay cfg; assert the alignment overlay can pass momentum where cross cannot, and that the overlay moves only `momentum_mode`):

```python
@pytest.mark.unit
def test_momentum_alignment_overlay_reaches_the_momentum_item():
    """Named ablation 'momentum-alignment' overlays exactly momentum_mode and
    flips a same-bar-cross-denied bar into a pass — the cross item never
    downgrades (superset semantics)."""
    base = MesChecklistConfig()
    assert base.momentum_mode == "cross"
    overlaid = apply_ablation(base, "momentum-alignment")
    assert overlaid.momentum_mode == "alignment"
    assert config_delta(base, overlaid) == {
        "momentum_mode": {"from": "cross", "to": "alignment"}
    }
    # Factory bar: SMA 99.5 above VWAP 99.0 on both bars (no fresh cross) —
    # cross mode fails the item, alignment passes it (Laguerre rising).
    kwargs = dict(prev_sma=99.6, prev_vwap=99.0, sma=99.6, vwap=99.0,
                  laguerre=0.6, prev_laguerre=0.5, laguerre_ready=True)
    base_item = find_item(evaluate(make_snapshot(mes=make_mes_series(**kwargs)), "long").mes_items, "Momentum")
    aligned_cfg = apply_ablation(load_mes_config(), "momentum-alignment")
    aligned_item = find_item(evaluate(make_snapshot(mes=make_mes_series(**kwargs), cfg=aligned_cfg), "long").mes_items, "Momentum")
    assert base_item.passed is False
    assert aligned_item.passed is True
```

This file already imports `find_item, make_snapshot, make_spy_series` from `tests.mes_factories`; add `make_mes_series` to that import.

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_ablations.py -v`
Expected: FAIL — `test_registry_lists_all_plan_ablations_in_order` errors with `unknown ablation 'momentum-alignment'` (the registry lacks the entry).

- [x] **Step 3: Implement the overlay**

In `tradingagents/mes/backtest/ablations.py`:

1. Extend the module docstring's ablation list (after the ``dynamic-threshold-off`` entry) with:

```python
- ``momentum-alignment`` — do midday trend pullbacks grade fairly when the
  momentum item accepts SMA/VWAP agreement instead of a same-bar cross,
  with the Laguerre trend gate unchanged? (base: checklist)
```

2. Add the registry entry as the last member of the `ABLATIONS` tuple (after `dynamic-threshold-off`):

```python
        Ablation(
            name="momentum-alignment",
            question=(
                "do midday trend pullbacks grade fairly when the momentum item "
                "accepts SMA/VWAP agreement instead of a same-bar cross, with the "
                "unchanged Laguerre trend gate as the only guardrail?"
            ),
            base="checklist",
            # Relaxation is a superset: alignment passes wherever cross passes
            # (same Laguerre gate), so no verdict can improve by keeping cross.
            overlay={"momentum_mode": "alignment"},
        ),
```

3. Add one line to the module docstring's bullet list: ``momentum-alignment`` needs no new toggles beyond ``momentum_mode`` itself.

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_ablations.py tests/test_mes_backtest_walker.py tests/test_mes_backtest_tier_table.py -v`
Expected: PASS — the registry shape test now finds `momentum-alignment` in plan order, `config_delta` equals the single declared field, the no-mutation test passes, and the walker/outcome stamping tests stay green.

- [x] **Step 5: Commit**

```bash
git add tradingagents/mes/backtest/ablations.py tests/test_mes_ablations.py
git commit -m "feat(mes-ablations): named momentum-alignment overlay for walker validation"
```

### Task 8: Runtime opt-in verification (MR env var) — A4

**Files:**
- Test: `tests/test_mes_config.py` (append)

**Interfaces:**
- Consumes: the generic `TRADINGAGENTS_MES_*` env machinery in `load_mes_config` (already field-driven — `enable_mean_reversion` is a bool dataclass field since the A-port).
- Produces: regression proof that the documented opt-in
  `TRADINGAGENTS_MES_ENABLE_MEAN_REVERSION=true` works with **zero code changes**; no new production code in this task.

- [x] **Step 1: Write the test (expected to pass already)**

Append to `tests/test_mes_config.py` (pattern matches `test_env_overrides_are_coerced_to_field_type`):

```python
@pytest.mark.unit
def test_mean_reversion_runtime_opt_in_via_env(monkeypatch):
    """A4: operators opt in at runtime with
    TRADINGAGENTS_MES_ENABLE_MEAN_REVERSION=true; the module default stays off."""
    assert MesChecklistConfig().enable_mean_reversion is False  # default stays off
    monkeypatch.setenv("TRADINGAGENTS_MES_ENABLE_MEAN_REVERSION", "true")
    assert load_mes_config().enable_mean_reversion is True
    # The alias map covers the PascalCase name too (tuner/profile parity).
    assert MesChecklistConfig.from_dict({"EnableMeanReversion": True}).enable_mean_reversion is True
```

- [x] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_config.py -v -k "mean_reversion or MeanReversion or env"`
Expected: PASS — this task locks the opt-in contract; if it fails, `_coerce`/`load_mes_config` regressed and must be fixed before proceeding.

- [x] **Step 3: Commit**

```bash
git add tests/test_mes_config.py
git commit -m "test(mes-config): pin MR runtime opt-in via TRADINGAGENTS_MES_ENABLE_MEAN_REVERSION"
```

### Task 9: Full-suite verification + CHANGELOG

**Files:**
- Modify: `CHANGELOG.md` (Unreleased → Added)

- [x] **Step 1: Run the full suite**

Run: `python -m pytest -q`
Expected: all green, same 2 skips as baseline (1520+ passed; roughly 30 new assertions from Tasks 1-7).

- [x] **Step 2: Run the targeted MR/momentum surface end-to-end**

Run: `python -m pytest tests/test_mes_render.py tests/test_mes_journal.py tests/test_mes_levels.py tests/test_mes_agents.py tests/test_mes_checklist.py tests/test_mes_config.py tests/test_mes_ablations.py tests/test_mes_mean_reversion.py -v`
Expected: PASS.

- [x] **Step 3: Update CHANGELOG.md**

Under `## [Unreleased] → ### Added`, add two bullets (Keep-a-Changelog style, matching the existing entries):

```markdown
- **MR setups surfaced end-to-end** (`enable_mean_reversion` runtime opt-in via
  `TRADINGAGENTS_MES_ENABLE_MEAN_REVERSION`, still default-off): `render_checklist`
  gains an informational "Mean-reversion candidate" section (additive; the trend
  ruling governs), check journal records capture all `mr_*` fields,
  `summarize_checks` gains an `MR` column (`long ENTRY` / `long watch` / `—`) so
  `mes review` grades fades, and the gatekeeper levels hint includes the MR plan
  only when MR fires.
- **Flag-gated momentum alignment** (`momentum_mode`, default `"cross"` for
  mes-tuner parity): `"alignment"` replaces the same-bar SMA/VWAP cross with
  SMA/VWAP agreement behind the unchanged Laguerre trend gate — a superset,
  never a downgrade; the momentum item labels the active mode; validated via
  the named `momentum-alignment` backtest ablation.
```

- [x] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): MR surfacing (Part A) + momentum alignment mode (Part B)"
```

## Self-Review (run against the spec, per the writing-plans skill)

**1. Spec coverage**

- Spec §3 A1 (render MR section + prompt paragraph): Task 2 (render section) + Task 4 (gatekeeper prompt paragraph). ✔
- Spec §3 A2 (journal `mr_*` fields + `summarize_checks` MR column, `mes review` grades MR) → Task 1 (review prompt reads `summarize_checks` output automatically — `cli/mes.py:542` consumes it unchanged). ✔
- Spec §3 A3 (levels hint MR block only when MR *fires*, informational, trend-verdict-governs) → Task 3 (incl. the user sub-decision: watch/in-zone candidates produce no hint block). ✔
- Spec §3 A4 (runtime opt-in via `TRADINGAGENTS_MES_ENABLE_MEAN_REVERSION=true`, thresholds untouched) → Task 8 (verification only; env plumbing is generic and needs no code). ✔
- Spec §4 B1 config/alias/round-trip → Task 5. B2 semantics + labeled item → Task 6. Named ablation validation → Task 7. Docs → Task 9. ✔
- Out of scope (MR actionability/Option 3, MR threshold relaxation, strictness/weights, SPY 3-of-5, MR outcome backfill): present in **no** task — verified by scanning the task list. ✔
- Global constraint "cross is byte-identical to today": pinned by Task 6 Step 4 (existing momentum suite stays green) + Task 7's `config_delta` single-field proof. ✔

**2. Placeholder scan** — no "TBD"/"TODO"/"similar to Task N"/test-without-code steps; every code step carries full code.

**3. Type consistency**

- `render_mr_levels_hint(result) -> str | None` and `render_gatekeeper_levels_hint(levels, result) -> str` (Task 3) are consumed by the CLI wiring in the same task; `render_checklist` MR header `"### Mean-reversion candidate (informational)"` (Task 2) is what Task 4's gatekeeper test keys on.
- `momentum_mode: str = "cross"` (Task 5) ↔ `_momentum` reads `cfg.momentum_mode == "alignment"` (Task 6) ↔ overlay `{"momentum_mode": "alignment"}` (Task 7) — names match.
- Journal MR cell values (`"long ENTRY"` / `"long watch"` / `"—"`) identical between Task 1's test and `_mr_label` implementation.
- `_TUNER_FIELD_ALIASES["MomentumMode"] = "momentum_mode"` makes `from_dict`/env/overrides agree; `Ablation.overlay` uses the snake_case field name so `config_delta` and `apply_ablation` are satisfied.

**Execution notes**

- Work in an isolated worktree created via `superpowers:using-git-worktrees` at execution time; the working tree currently carries the uncommitted mes-trade-management work (journal.py, levels.py, cli/mes.py, config.py overlap with this plan) — land or merge that first.
- Task order matters only for Tasks 5→6→7 (config field before semantics before overlay); Tasks 1–4 (Part A) are independent of Tasks 5–8 (Part B) and each ends green.
- Test-count estimate: 26 new tests (3 journal + 4 render + 5 levels + 2 agents + 4 config + 6 checklist + 1 ablation + 1 config env-pin) ≈ 35 assertions, within the designed 25–35 band.













