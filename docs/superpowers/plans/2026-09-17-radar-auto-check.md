# MES Radar `--auto-check` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `mes radar --auto-check` fires the full check path (checklist rendering + gatekeeper verdict + journal write) automatically whenever the radar's setup state is READY, on the same bar the radar already evaluated — no subprocess, no second data fetch, no timing skew.

**Architecture:** Extract the post-snapshot half of `mes check`'s loop into a shared helper `_run_check_once(snapshot, result, ...)` in `cli/mes.py` that prints the result, calls the gatekeeper, and appends to the journal. Radar gains `--auto-check` / `--auto-check-cooldown` flags; in one-shot mode it fires the helper once when READY, and in watch mode a pure `should_auto_check(state, prev_state, last_fired, now, cooldown)` trigger decides when to drop out of the Rich `Live`, run the check, and resume rendering.

**Tech Stack:** Python 3.12, Typer CLI, Rich (`Live`/`Panel`/`Console`), pytest with `@pytest.mark.unit` markers and `typer.testing.CliRunner`. No new dependencies.

**Spec:** This file implements the design locked in during the brainstorming session for `mes radar --auto-check`. Design decisions: trigger on READY state only; LLM behavior follows radar's own `--no-llm` flag; repeat handling = fire on every transition into READY plus re-fire at most once per cooldown (default 5 min) while READY persists; `--json` mode ignores auto-check; no changes to `check`'s CLI surface, no new config keys, no gatekeeper changes.

## Global Constraints

- Repo root: `/Users/akundrock/sandbox/TradingAgents` (work happens there, not in `thinkorswim-scripts`).
- Branch: new branch `radar-auto-check` based off `main` (current branch `auditable-check-summaries` stays done-but-unmerged and is NOT part of this plan; base on `main`, not on it).
- TDD throughout: every behavior change starts with a failing test, verified failing, then minimal implementation, verified passing, then commit.
- Commit message style: conventional commits with `mes` scope — `refactor(mes): …`, `feat(mes):`, `test(mes):` (matches history: `feat(mes): summarize_checks carries failing items…`).
- Tests never hit the network or the LLM: gatekeeper/LLM factories are monkeypatched or bypassed with `--no-llm`; snapshots come from `tests/mes_factories.py` builders (`make_snapshot`, `make_mes_series`).
- Tests use `typer.testing.CliRunner`, patch `mes_cli._load_snapshot` and `mes_cli._market_now` via `monkeypatch.setattr`, and are marked `@pytest.mark.unit`.
- `mes check` behavior must be unchanged after the Task 1 refactor (same output, same journal records, same flags).
- The 5m price cache (`_PRICE_HISTORY_CACHE` in `tradingagents/dataflows/schwab.py`) is irrelevant to correctness here — the fired check reuses the snapshot the radar already built in-hand.
- Out of scope (do not do): changes to `mes check`'s CLI options/behavior; new config keys in `MesChecklistConfig`; changes to `tradingagents/mes/radar.py` state classification or to `MesJournal`/gatekeeper APIs.

---

### Task 1: Extract `_run_check_once` shared check-pass helper from `mes check`

**Files:**
- Modify: `cli/mes.py` (new helper after `_verdict_headline`, which ends at line 251; `check` loop body at lines 344-415)
- Test: `tests/test_mes_radar.py` (append a new section at end of file, currently line 639)

**Interfaces:**
- Consumes: existing module-level helpers in `cli/mes.py` — `_sizing_payload(result, cfg, risk, stop_points) -> tuple[dict, str]`, `_print_result(result, snapshot)`, `_verdict_headline(verdict_markdown, tradeable) -> str`, `_VERDICT_STYLE` dict; `MesJournal.append_check(*, snapshot, result, verdict_markdown="", sizing=None)`; `suggest_trade_levels_from_snapshot(side, snapshot, result)`, `render_checklist(result, live=...)`, `render_market_context(snapshot)`, `render_trade_levels_hint(levels_hint)`.
- Produces: `_run_check_once(*, snapshot: MesSnapshot, result: ChecklistResult, cfg, journal: MesJournal, gatekeeper, hypothesis: str, past_context: str, risk_dollars: float, stop_points: float | None, as_json: bool = False, no_log: bool = False) -> tuple[dict, str, str]` — prints the checklist (or JSON), calls the gatekeeper when one is passed, prints the verdict panel, appends the check to the journal, returns `(sizing, sizing_note, verdict)`. Tasks 3-4 call it with exactly these keyword arguments.

- [ ] **Step 1: Create the working branch**

```bash
cd /Users/akundrock/sandbox/TradingAgents
git checkout -b radar-auto-check main
```

Uncommitted changes on the old branch (NOTES.md, untracked plan docs) travel with the checkout and are unrelated — leave them alone.

- [ ] **Step 2: Write the failing tests**

Append to the end of `/Users/akundrock/sandbox/TradingAgents/tests/test_mes_radar.py`. Also add `from tradingagents.mes.config import load_mes_config` to the imports at the top of the file (it does not currently import it):

```python
# ---------------------------------------------------------------------------
# _run_check_once — shared full-check pass for `mes check` and radar --auto-check
# ---------------------------------------------------------------------------


class _StubJournal:
    """Records append_check calls; stands in for MesJournal."""

    def __init__(self):
        self.calls = []

    def append_check(self, *, snapshot, result, verdict_markdown="", sizing=None):
        self.calls.append(
            {
                "snapshot": snapshot,
                "result": result,
                "verdict_markdown": verdict_markdown,
                "sizing": sizing,
            }
        )


def test_run_check_once_prints_deterministic_verdict_and_logs():
    snap = _make_snapshot()
    result = _make_result()
    stub = _StubJournal()

    sizing, sizing_note, verdict = mes_cli._run_check_once(
        snapshot=snap,
        result=result,
        cfg=load_mes_config(),
        journal=stub,
        gatekeeper=None,
        hypothesis="",
        past_context="",
        risk_dollars=1000.0,
        stop_points=8.0,
    )

    assert "contracts" in sizing
    assert "Risk $1,000" in sizing_note
    assert verdict == ""  # no gatekeeper -> deterministic verdict path
    assert len(stub.calls) == 1
    assert stub.calls[0]["snapshot"] is snap
    assert stub.calls[0]["result"] is result
    assert stub.calls[0]["verdict_markdown"] == ""
```


```python
def test_run_check_once_calls_gatekeeper_and_logs_verdict():
    snap = _make_snapshot()
    result = _make_result()
    stub = _StubJournal()

    def fake_gatekeeper(**kwargs):
        assert kwargs["tradeable"] is True
        assert kwargs["hypothesis"] == "fade extremes into VWAP"
        return "**Verdict**: Wait\n\nNot at level."

    sizing, sizing_note, verdict = mes_cli._run_check_once(
        snapshot=snap,
        result=result,
        cfg=load_mes_config(),
        journal=stub,
        gatekeeper=fake_gatekeeper,
        hypothesis="fade extremes into VWAP",
        past_context="",
        risk_dollars=1000.0,
        stop_points=8.0,
    )

    assert "Wait" in verdict
    assert len(stub.calls) == 1
    assert "Not at level" in stub.calls[0]["verdict_markdown"]
    assert "contracts" in stub.calls[0]["sizing"]


def test_run_check_once_no_log_skips_journal():
    snap = _make_snapshot()
    result = _make_result()
    stub = _StubJournal()

    mes_cli._run_check_once(
        snapshot=snap,
        result=result,
        cfg=load_mes_config(),
        journal=stub,
        gatekeeper=None,
        hypothesis="",
        past_context="",
        risk_dollars=1000.0,
        stop_points=8.0,
        no_log=True,
    )

    assert stub.calls == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /Users/akundrock/sandbox/TradingAgents && python -m pytest tests/test_mes_radar.py -q -k run_check_once`
Expected: FAIL — 3 errors, `AttributeError: module 'cli.mes' has no attribute '_run_check_once'`.

- [ ] **Step 4: Implement the helper by moving the check-loop body**

In `cli/mes.py`, insert after `_verdict_headline` (after line 251, before the `# Commands` banner at line 254). This is the exact body of `check`'s loop (lines 352-408) wrapped in a function with `as_json`/`no_log` as parameters instead of closure variables:

```python
def _run_check_once(
    *,
    snapshot: MesSnapshot,
    result: ChecklistResult,
    cfg,
    journal: MesJournal,
    gatekeeper,
    hypothesis: str,
    past_context: str,
    risk_dollars: float,
    stop_points: float | None,
    as_json: bool = False,
    no_log: bool = False,
) -> tuple[dict, str, str]:
    """Run one full check pass on an already-built snapshot.

    Shared by ``mes check`` and ``mes radar --auto-check``: sizes the trade,
    prints the checklist result (or JSON), consults the gatekeeper when one is
    provided, prints the verdict panel, and appends the check to the journal.
    Returns ``(sizing, sizing_note, verdict_markdown)``.
    """
    sizing, sizing_note = _sizing_payload(result, cfg, risk_dollars, stop_points)

    if as_json:
        console.print_json(
            json.dumps(
                {
                    "as_of": snapshot.as_of.isoformat(),
                    "result": dataclasses.asdict(result),
                    "sizing": sizing,
                    "warnings": snapshot.warnings,
                },
                default=str,
            )
        )
    else:
        _print_result(result, snapshot)
        console.print(f"[dim]{sizing_note}[/dim]")

    verdict = ""
    if gatekeeper is not None:
        try:
            levels_hint = suggest_trade_levels_from_snapshot(result.side, snapshot, result)
            verdict = gatekeeper(
                checklist_markdown=render_checklist(result, live=snapshot.rth_started),
                market_context=render_market_context(snapshot),
                hypothesis=hypothesis,
                past_context=past_context,
                tradeable=result.tradeable,
                sizing_note=sizing_note,
                current_price=snapshot.mes.close,
                trade_levels_hint=render_trade_levels_hint(levels_hint) if levels_hint else "",
            )
        except Exception as exc:
            console.print(f"[yellow]Gatekeeper call failed:[/yellow] {exc}")

    if verdict and not as_json:
        headline = _verdict_headline(verdict, result.tradeable)
        console.print(
            Panel(
                Markdown(verdict),
                title=f"Verdict: {headline}",
                border_style=_VERDICT_STYLE[headline].split()[-1],
            )
        )
    elif not verdict and not as_json:
        call = "TRADEABLE" if result.tradeable else "STAND DOWN"
        style = "bold green" if result.tradeable else "bold red"
        console.print(Panel(f"[{style}]{call}[/{style}]", title="Deterministic Verdict"))

    if not no_log:
        journal.append_check(
            snapshot=snapshot,
            result=result,
            verdict_markdown=verdict,
            sizing=sizing,
        )

    return sizing, sizing_note, verdict
```

- [ ] **Step 5: Rewire `check`'s loop to call the helper**

In `cli/mes.py`, replace the body of `check`'s `while True:` loop from `result = evaluate(snapshot, side)` (line 352) through the `journal.append_check(...)` block (line 408) with:

```python
        result = evaluate(snapshot, side)
        _run_check_once(
            snapshot=snapshot,
            result=result,
            cfg=cfg,
            journal=journal,
            gatekeeper=gatekeeper,
            hypothesis=hypothesis,
            past_context=past_context,
            risk_dollars=risk_dollars,
            stop_points=stop_points,
            as_json=as_json,
            no_log=no_log,
        )
```

The `while True:` loop, snapshot load with its error handling, `if not watch: break`, and the `time.sleep(watch)` tail stay exactly as they are (lines 344-351 and 410-415).

- [ ] **Step 6: Run the new tests and the refactor safety net**

Run: `cd /Users/akundrock/sandbox/TradingAgents && python -m pytest tests/test_mes_radar.py tests/test_mes_cli_trade.py tests/test_mes_journal.py -q`
Expected: ALL PASS (3 new `run_check_once` tests green; no existing regressions).

- [ ] **Step 7: Verify `mes check` is unchanged end-to-end**

Run a quick CLI smoke against recorded CSV if one exists under `out/`, otherwise verify via the trade/CLI suites only:

```bash
python -m pytest tests/ -q -x -k "mes" 2>&1 | tail -5
```

Expected: all `mes` tests pass with the same counts as before the change.

- [ ] **Step 8: Commit**

```bash
cd /Users/akundrock/sandbox/TradingAgents
git add cli/mes.py tests/test_mes_radar.py
git commit -m "refactor(mes): extract _run_check_once from mes check loop"
```

---

### Task 2: `should_auto_check` trigger function in `tradingagents/mes/radar.py`

**Files:**
- Modify: `tradingagents/mes/radar.py` (add import of `datetime`; add `should_auto_check` after `build_proximity` at the end of the module)
- Modify: `tradingagents/mes/__init__.py:34` (export the new function)
- Test: `tests/test_mes_radar.py`

**Interfaces:**
- Consumes: `SetupState` enum already defined in `tradingagents/mes/radar.py` (values: `GATES_CLOSED`, `BLOCKED`, `LOW_CONVICTION`, `AT_LEVEL_MISSING_CONFLUENCE`, `CONFLUENCE_OK_WAITING_LOCATION`, `READY`).
- Produces: `should_auto_check(state: SetupState, prev_state: SetupState | None, last_fired: datetime | None, now: datetime, cooldown_seconds: float) -> bool` — exported from `tradingagents.mes.radar` and re-exported from `tradingagents.mes`. Task 3 imports it in `cli/mes.py` as `from tradingagents.mes.radar import should_auto_check` and calls it per radar tick.

- [ ] **Step 1: Write the failing tests**

Add `from datetime import datetime` to the imports at the top of `/Users/akundrock/sandbox/TradingAgents/tests/test_mes_radar.py`, add `should_auto_check` to the `from tradingagents.mes.radar import (...)` list, then append:

```python
# ---------------------------------------------------------------------------
# should_auto_check — radar --auto-check trigger decision (pure function)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_should_auto_check_fires_on_transition_into_ready():
    now = datetime(2026, 3, 30, 11, 0)
    assert should_auto_check(
        SetupState.READY, SetupState.CONFLUENCE_OK_WAITING_LOCATION, None, now, 300.0
    )


@pytest.mark.unit
def test_should_auto_check_fires_when_radar_starts_already_ready():
    now = datetime(2026, 3, 30, 11, 0)
    assert should_auto_check(SetupState.READY, None, None, now, 300.0)


@pytest.mark.unit
def test_should_auto_check_never_fires_outside_ready():
    now = datetime(2026, 3, 30, 11, 0)
    non_ready = [
        SetupState.GATES_CLOSED,
        SetupState.BLOCKED,
        SetupState.LOW_CONVICTION,
        SetupState.AT_LEVEL_MISSING_CONFLUENCE,
        SetupState.CONFLUENCE_OK_WAITING_LOCATION,
    ]
    for state in non_ready:
        assert not should_auto_check(state, None, None, now, 300.0)
        # even mid-cooldown with a fired history, non-READY never fires
        assert not should_auto_check(state, state, now, now, 300.0)


@pytest.mark.unit
def test_should_auto_check_suppressed_within_cooldown():
    now = datetime(2026, 3, 30, 11, 5)
    last_fired = datetime(2026, 3, 30, 11, 2)
    assert not should_auto_check(SetupState.READY, SetupState.READY, last_fired, now, 300.0)


@pytest.mark.unit
def test_should_auto_check_refires_after_cooldown():
    now = datetime(2026, 3, 30, 11, 5)
    last_fired = datetime(2026, 3, 30, 11, 0)
    assert should_auto_check(SetupState.READY, SetupState.READY, last_fired, now, 300.0)


@pytest.mark.unit
def test_should_auto_check_zero_cooldown_refires_every_ready_tick():
    now = datetime(2026, 3, 30, 11, 0)
    assert should_auto_check(SetupState.READY, SetupState.READY, now, now, 0.0)


@pytest.mark.unit
def test_should_auto_check_transition_refires_even_within_cooldown():
    """A fresh entry into READY is a new setup moment — it fires immediately."""
    now = datetime(2026, 3, 30, 11, 8)
    last_fired = datetime(2026, 3, 30, 11, 7)
    assert should_auto_check(
        SetupState.READY, SetupState.BLOCKED, last_fired, now, 300.0
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/akundrock/sandbox/TradingAgents && python -m pytest tests/test_mes_radar.py -q -k should_auto_check`
Expected: FAIL — `ImportError: cannot import name 'should_auto_check' from 'tradingagents.mes.radar'`.

- [ ] **Step 3: Implement `should_auto_check`**

In `tradingagents/mes/radar.py`, add to the stdlib imports at the top:

```python
from datetime import datetime
```

Then append at the end of the module:

```python
def should_auto_check(
    state: SetupState,
    prev_state: SetupState | None,
    last_fired: datetime | None,
    now: datetime,
    cooldown_seconds: float,
) -> bool:
    """Decide whether an auto-check should fire on this radar tick.

    Fires on any entry into :attr:`SetupState.READY` — including the very first
    tick if radar started while READY — and re-fires at most once per
    ``cooldown_seconds`` while READY persists. Never fires outside READY.
    """
    if state is not SetupState.READY:
        return False
    if prev_state is not SetupState.READY:
        return True
    if last_fired is None:
        return True
    return (now - last_fired).total_seconds() >= cooldown_seconds
```

- [ ] **Step 4: Export from the package**

In `tradingagents/mes/__init__.py` line 34, change:

```python
from .radar import LevelDistance, ProximityReport, SetupState, build_proximity
```

to:

```python
from .radar import (
    LevelDistance,
    ProximityReport,
    SetupState,
    build_proximity,
    should_auto_check,
)
```

and add `"should_auto_check",` to the `__all__` list next to `"build_proximity"`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/akundrock/sandbox/TradingAgents && python -m pytest tests/test_mes_radar.py -q`
Expected: ALL PASS (existing radar tests + 7 new trigger tests).

- [ ] **Step 6: Commit**

```bash
cd /Users/akundrock/sandbox/TradingAgents
git add tradingagents/mes/radar.py tradingagents/mes/__init__.py tests/test_mes_radar.py
git commit -m "feat(mes): add should_auto_check trigger for radar auto-check"
```

---

### Task 3: Radar `--auto-check` flags + one-shot wiring

**Files:**
- Modify: `cli/mes.py` (radar command, lines 621-692: signature, docstring, setup, JSON branch, one-shot branch)
- Test: `tests/test_mes_radar.py` (append CLI tests; reuses `_make_snapshot`, `_make_result`, `_invoke_radar`-style patching, `radar_runner`)

**Interfaces:**
- Consumes: `mes_cli._run_check_once` (Task 1), `should_auto_check(state, prev_state, last_fired, now, cooldown_seconds)` (Task 2, imported into `cli/mes.py`), `SetupState.READY`, `MesJournal`, `DEFAULT_CONFIG`, `create_mes_gatekeeper_agent`, `_make_llm`, `_past_context` — all already imported or defined in `cli/mes.py`.
- Produces: radar CLI options `--auto-check` (bool, default False), `--auto-check-cooldown` (float minutes, default 5.0), `--no-llm` (bool), `--no-log` (bool). The watch loop (Task 4) uses local state `cooldown_seconds = auto_check_cooldown * 60.0`, `last_fired: datetime | None`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `/Users/akundrock/sandbox/TradingAgents/tests/test_mes_radar.py`. First add to the top-of-file imports (none of these are currently at module level except `pytest`/`mes_cli`): `from datetime import datetime`, `from tradingagents.mes.journal import MesJournal`, and extend the existing `from tests.mes_factories import (...)` import to include `DEFAULT_AS_OF`. Then append:

```python
# ---------------------------------------------------------------------------
# radar --auto-check: fires the full check path on READY
# ---------------------------------------------------------------------------


def _ready_fixture(monkeypatch, tmp_path):
    """Patches the CLI so radar evaluates a genuinely READY setup.

    overnight_high=7681.0 is 2.25 pts above price 7678.75 -> within the default
    band, and _make_result() is tradeable -> build_proximity reports READY.
    Journal writes go to tmp_path via a patched DEFAULT_CONFIG.
    """
    snap = _make_snapshot(overnight_high=7681.0)
    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(mes_cli, "_market_now", lambda cfg: DEFAULT_AS_OF)
    monkeypatch.setattr(mes_cli, "DEFAULT_CONFIG", {"mes_journal_dir": str(tmp_path)})
    # Pin the checklist result so the test exercises the auto-check trigger,
    # not the real checklist thresholds on factory data.
    monkeypatch.setattr(mes_cli, "evaluate", lambda snapshot, side: _make_result())
    return snap


@pytest.mark.unit
def test_radar_auto_check_fires_on_ready_one_shot(monkeypatch, tmp_path):
    _ready_fixture(monkeypatch, tmp_path)
    result = radar_runner.invoke(
        mes_cli.mes_app, ["radar", "--no-watch", "--auto-check", "--no-llm"]
    )
    assert result.exit_code == 0, result.output
    assert "Deterministic Verdict" in result.output
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    records = [e for e in journal.load_day("2026-03-30") if e["kind"] == "check"]
    assert len(records) == 1
    assert records[0]["tradeable"] is True


@pytest.mark.unit
def test_radar_without_auto_check_still_never_writes_journal(monkeypatch, tmp_path):
    _ready_fixture(monkeypatch, tmp_path)
    result = radar_runner.invoke(mes_cli.mes_app, ["radar", "--no-watch"])
    assert result.exit_code == 0
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    assert journal.load_day("2026-03-30") == []


@pytest.mark.unit
def test_radar_auto_check_skipped_when_not_ready(monkeypatch, tmp_path):
    """Gates closed -> never READY -> no check run even with --auto-check."""
    snap = _make_snapshot()
    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(mes_cli, "_market_now", lambda cfg: DEFAULT_AS_OF)
    monkeypatch.setattr(mes_cli, "DEFAULT_CONFIG", {"mes_journal_dir": str(tmp_path)})
    monkeypatch.setattr(
        mes_cli, "evaluate",
        lambda snapshot, side: _make_result(gates_ok=False, gate_reasons=["outside RTH"]),
    )
    result = radar_runner.invoke(
        mes_cli.mes_app, ["radar", "--no-watch", "--auto-check", "--no-llm"]
    )
    assert result.exit_code == 0
    assert "Deterministic Verdict" not in result.output
    result = radar_runner.invoke(
        mes_cli.mes_app, ["radar", "--no-watch", "--auto-check", "--no-llm"]
    )
    assert result.exit_code == 0
    assert "Deterministic Verdict" not in result.output
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    assert journal.load_day("2026-03-30") == []


@pytest.mark.unit
def test_radar_auto_check_no_llm_never_builds_gatekeeper(monkeypatch, tmp_path):
    def _boom(*args, **kwargs):
        raise AssertionError("gatekeeper must not be created with --no-llm")

    _ready_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(mes_cli, "create_mes_gatekeeper_agent", _boom)
    result = radar_runner.invoke(
        mes_cli.mes_app, ["radar", "--no-watch", "--auto-check", "--no-llm"]
    )
    assert result.exit_code == 0, result.output
    assert "Deterministic Verdict" in result.output


@pytest.mark.unit
def test_radar_auto_check_calls_gatekeeper_and_logs_verdict(monkeypatch, tmp_path):
    _ready_fixture(monkeypatch, tmp_path)

    def fake_gatekeeper(**kwargs):
        return "**Verdict**: Wait\n\nSizing unclear."

    monkeypatch.setattr(mes_cli, "_make_llm", lambda config, deep=False: object())
    monkeypatch.setattr(mes_cli, "create_mes_gatekeeper_agent", lambda llm: fake_gatekeeper)
    result = radar_runner.invoke(
        mes_cli.mes_app, ["radar", "--no-watch", "--auto-check"]
    )
    assert result.exit_code == 0, result.output
    assert "Verdict: Wait" in result.output
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    records = [e for e in journal.load_day("2026-03-30") if e["kind"] == "check"]
    assert len(records) == 1
    assert "Wait" in records[0]["verdict"]


@pytest.mark.unit
def test_radar_auto_check_json_mode_ignores_flag(monkeypatch, tmp_path):
    _ready_fixture(monkeypatch, tmp_path)
    result = radar_runner.invoke(
        mes_cli.mes_app, ["radar", "--no-watch", "--json", "--auto-check", "--no-llm"]
    )
    assert result.exit_code == 0, result.output
    assert '"state"' in result.output
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    assert journal.load_day("2026-03-30") == []


@pytest.mark.unit
def test_radar_auto_check_no_log_writes_nothing(monkeypatch, tmp_path):
    _ready_fixture(monkeypatch, tmp_path)
    result = radar_runner.invoke(
        mes_cli.mes_app,
        ["radar", "--no-watch", "--auto-check", "--no-llm", "--no-log"],
    )
    assert result.exit_code == 0
    assert "Deterministic Verdict" in result.output
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    assert journal.load_day("2026-03-30") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/akundrock/sandbox/TradingAgents && python -m pytest tests/test_mes_radar.py -q -k auto_check`
Expected: FAIL — unknown option `--auto-check` (Typer "No such option"), so all 7 matching tests error with exit code 2.

- [ ] **Step 3: Add the flags to the radar signature and docstring**

In `cli/mes.py`, replace the radar signature and docstring (lines 621-653) with:

```python
@mes_app.command("radar")
def radar(
    side: str = typer.Option("auto", "--side", help="Evaluate 'long', 'short', or 'auto'."),
    watch: int = typer.Option(60, "--watch", help="Re-run every N seconds. Use --no-watch for one-shot."),
    no_watch: bool = typer.Option(False, "--no-watch", help="Run once then exit."),
    within: float | None = typer.Option(
        None, "--within", help="Proximity band in points (default: min(4.0, 0.5×ATR))."
    ),
    alert: bool = typer.Option(
        False, "--alert", help="Print a bell character when state is READY or AT_LEVEL."
    ),
    auto_check: bool = typer.Option(
        False,
        "--auto-check",
        help="Run the full check path (checklist + gatekeeper unless --no-llm, journal write) whenever state is READY. Ignored with --json.",
    ),
    auto_check_cooldown: float = typer.Option(
        5.0,
        "--auto-check-cooldown",
        help="Minimum minutes between auto-checks while READY persists.",
    ),
    no_llm: bool = typer.Option(False, "--no-llm", help="Deterministic checklist only; skip the gatekeeper."),
    no_log: bool = typer.Option(False, "--no-log", help="Do not append auto-check results to the journal."),
    as_of: str | None = typer.Option(None, "--as-of", help="Bar timestamp to evaluate. Defaults to now."),
    date: str | None = typer.Option(None, "--date", help="Session date when --as-of is a bare time."),
    as_json: bool = typer.Option(False, "--json", help="Emit ProximityReport as JSON then exit."),
    mes_csv: Path | None = typer.Option(None, "--mes-csv", help="Replay from a recorded /MES bar CSV."),
    spy_csv: Path | None = typer.Option(None, "--spy-csv", help="Replay from a recorded SPY bar CSV."),
):
    """Compact live proximity view: how close is a valid MES trade entry?

    Shows the live $ADD/$TICK/$VOLD readings and any snapshot data warnings
    (e.g. sparse $TICK coverage) alongside the setup state. Without --auto-check
    it runs without the LLM gatekeeper and never writes to the journal.

    With --auto-check, whenever the state is READY the full check path runs on
    the same bar the radar just evaluated: checklist render, gatekeeper verdict
    (skipped with --no-llm), and a journal write (skipped with --no-log). While
    READY persists it re-fires at most once per --auto-check-cooldown minutes.
    Ignored in --json mode (pure machine query).

    Examples:

        tradingagents mes radar

        tradingagents mes radar --watch 15 --within 3 --side long --alert

        tradingagents mes radar --auto-check --auto-check-cooldown 5

        tradingagents mes radar --no-watch --json
    """
```

- [ ] **Step 4: Add the import and wire the one-shot + JSON branches**

Add at the top of `cli/mes.py`, next to the other `tradingagents.mes` imports:

```python
from tradingagents.mes.radar import should_auto_check
```

Then replace the radar body from `cfg = load_mes_config()` (line 657 of the original file) through the end of the one-shot branch (line 692) with:

```python
    cfg = load_mes_config()
    interval = watch if not no_watch else 0

    _ALERT_STATES = {SetupState.READY, SetupState.AT_LEVEL_MISSING_CONFLUENCE}

    cooldown_seconds = auto_check_cooldown * 60.0

    journal: MesJournal | None = None
    gatekeeper = None
    past_context = ""
    if auto_check:
        app_config = DEFAULT_CONFIG.copy()
        journal = MesJournal(app_config)
        if not no_llm:
            try:
                gatekeeper = create_mes_gatekeeper_agent(_make_llm(app_config))
                past_context = _past_context(app_config)
            except Exception as exc:
                console.print(f"[yellow]Gatekeeper unavailable, running deterministic only:[/yellow] {exc}")

    def _one_shot(stamp: datetime) -> tuple[MesSnapshot, ChecklistResult, ProximityReport]:
        snapshot = _load_snapshot(stamp, cfg, mes_csv, spy_csv)
        result = evaluate(snapshot, side)
        report = build_proximity(snapshot, result, proximity_band=within)
        return snapshot, result, report

    def _fire_auto_check(snapshot: MesSnapshot, result: ChecklistResult, stamp: datetime) -> None:
        """Full check path on the radar's in-hand snapshot (same bar, zero skew)."""
        if journal is None:
            return
        hypothesis_record = journal.load_hypothesis(stamp.strftime("%Y-%m-%d"))
        hypothesis = (hypothesis_record or {}).get("hypothesis", "")
        _run_check_once(
            snapshot=snapshot,
            result=result,
            cfg=cfg,
            journal=journal,
            gatekeeper=gatekeeper,
            hypothesis=hypothesis,
            past_context=past_context,
            risk_dollars=cfg.default_risk_dollars,
            stop_points=None,
            as_json=False,
            no_log=no_log,
        )

    if as_json:
        stamp = _parse_as_of(as_of, cfg, date=date) if as_of else _market_now(cfg)
        try:
            _, _, report = _one_shot(stamp)
        except Exception as exc:
            console.print(f"[red]Snapshot failed:[/red] {exc}")
            raise typer.Exit(code=1)
        console.print_json(json.dumps(dataclasses.asdict(report), default=str))
        return

    if no_watch or interval == 0:
        stamp = _parse_as_of(as_of, cfg, date=date) if as_of else _market_now(cfg)
        try:
            snapshot, result, report = _one_shot(stamp)
        except Exception as exc:
            console.print(f"[red]Snapshot failed:[/red] {exc}")
            raise typer.Exit(code=1)
        console.print(Panel(_render_radar(report, stamp), title="MES Radar", border_style="blue"))
        if alert and report.state in _ALERT_STATES:
            console.print("\a", end="")
        if auto_check and report.state == SetupState.READY:
            _fire_auto_check(snapshot, result, stamp)
        return
```

Note: this removes the stale `import dataclasses as _dc` / `_dc2` lines — the JSON branch uses the module-level `json` and `dataclasses` imports `cli/mes.py` already has.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/akundrock/sandbox/TradingAgents && python -m pytest tests/test_mes_radar.py -q`
Expected: ALL PASS (6 new auto-check tests + existing radar + trigger tests).

- [ ] **Step 6: Commit**

```bash
cd /Users/akundrock/sandbox/TradingAgents
git add cli/mes.py tests/test_mes_radar.py
git commit -m "feat(mes): radar --auto-check fires full check on READY (one-shot)"
```

---

### Task 4: Radar watch loop — fire, drop out of `Live`, print, resume

**Files:**
- Modify: `cli/mes.py` (radar watch loop, original lines 694-713)
- Test: `tests/test_mes_radar.py` (append watch-mode tests; add `import time` to the top-of-file imports)

**Interfaces:**
- Consumes: `_run_check_once` (Task 1), `should_auto_check(state, prev_state, last_fired, now, cooldown_seconds)` (Task 2), `_fire_auto_check(snapshot, result, stamp)` closure and `cooldown_seconds` local from Task 3, `prev_state` watch-loop local.
- Produces: watch-loop behavior — on a fire: `live.stop()`, check output printed to the console, `live.start()` again, `last_fired = stamp`; `prev_state` keeps its existing meaning (last tick's report.state).

- [ ] **Step 1: Write the failing tests**

Append to `/Users/akundrock/sandbox/TradingAgents/tests/test_mes_radar.py`:

```python
# ---------------------------------------------------------------------------
# radar --auto-check in watch mode: transition fire + cooldown re-fire
# ---------------------------------------------------------------------------


def _invoke_radar_watch(monkeypatch, tmp_path, ticks: int, extra_args=None):
    """Run `radar --watch 1 --auto-check` for exactly `ticks` ticks.

    The first `ticks - 1` sleeps pass, then sleep raises KeyboardInterrupt so
    the loop exits after `ticks` iterations. All ticks use the same stamp.
    """
    _ready_fixture(monkeypatch, tmp_path)
    sleeps: list[int] = []

    def fake_sleep(seconds):
        if len(sleeps) >= ticks - 1:
            raise KeyboardInterrupt
        sleeps.append(seconds)

    monkeypatch.setattr(time, "sleep", fake_sleep)
    return radar_runner.invoke(mes_cli.mes_app, ["radar", "--watch", "1", "--auto-check", "--no-llm"] + (extra_args or []))


@pytest.mark.unit
def test_radar_watch_auto_check_fires_once_and_cooldown_suppresses_refire(monkeypatch, tmp_path):
    result = _invoke_radar_watch(monkeypatch, tmp_path, ticks=2, extra_args=["--auto-check-cooldown", "5"])
    assert result.exit_code == 0, result.output
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    records = [e for e in journal.load_day("2026-03-30") if e["kind"] == "check"]
    # Tick 1: transition into READY fires. Tick 2: still READY within the
    # 5-minute cooldown on the same stamp -> suppressed.
    assert len(records) == 1


@pytest.mark.unit
def test_radar_watch_auto_check_refires_after_cooldown(monkeypatch, tmp_path):
    result = _invoke_radar_watch(monkeypatch, tmp_path, ticks=2, extra_args=["--auto-check-cooldown", "0"])
    assert result.exit_code == 0, result.output
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    records = [e for e in journal.load_day("2026-03-30") if e["kind"] == "check"]
    # Cooldown 0: the second READY tick is already at/past the cooldown -> refire.
    assert len(records) == 2


@pytest.mark.unit
def test_radar_watch_without_auto_check_writes_no_journal(monkeypatch, tmp_path):
    snap = _make_snapshot(overnight_high=7681.0)
    monkeypatch.setattr(mes_cli, "_load_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(mes_cli, "_market_now", lambda cfg: DEFAULT_AS_OF)
    monkeypatch.setattr(mes_cli, "DEFAULT_CONFIG", {"mes_journal_dir": str(tmp_path)})
    monkeypatch.setattr(mes_cli, "evaluate", lambda s, side: _make_result())
    sleeps: list[int] = []

    def fake_sleep(seconds):
        if sleeps:
            raise KeyboardInterrupt
        sleeps.append(seconds)

    monkeypatch.setattr(time, "sleep", fake_sleep)
    result = radar_runner.invoke(mes_cli.mes_app, ["radar", "--watch", "1"])
    assert result.exit_code == 0, result.output
    journal = MesJournal({"mes_journal_dir": str(tmp_path)})
    assert journal.load_day("2026-03-30") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/akundrock/sandbox/TradingAgents && python -m pytest tests/test_mes_radar.py -q -k radar_watch`
Expected: FAIL — first test finds 2 check records (no cooldown logic yet), others may pass; confirm at least the cooldown-suppression test fails before implementing.

- [ ] **Step 3: Wire the watch loop**

In `cli/mes.py`, replace the watch loop (original lines 694-713) with:

```python
    # ---- Watch loop with Rich Live ----
    prev_state: SetupState | None = None
    last_fired: datetime | None = None
    with Live(console=console, refresh_per_second=1, screen=False) as live:
        while True:
            stamp = _parse_as_of(as_of, cfg, date=date) if as_of else _market_now(cfg)
            try:
                snapshot, result, report = _one_shot(stamp)
                panel = Panel(_render_radar(report, stamp), title="MES Radar", border_style="blue")
                live.update(panel)
                if alert and report.state in _ALERT_STATES and report.state != prev_state:
                    console.print("\a", end="")
                if should_auto_check(report.state, prev_state, last_fired, stamp, cooldown_seconds):
                    live.stop()
                    try:
                        _fire_auto_check(snapshot, result, stamp)
                    finally:
                        live.start()
                    last_fired = stamp
                prev_state = report.state
            except Exception as exc:
                live.update(Panel(f"[red]Snapshot error:[/red] {exc}", border_style="red"))

            try:
                __import__("time").sleep(interval)
            except KeyboardInterrupt:
                break
```

The only changes from the current loop: unpack the `_one_shot` tuple, add the `last_fired` local, and insert the `should_auto_check` fire block (stop `Live`, run the check on the in-hand snapshot/result, restart `Live`, record `last_fired`) between the alert bell and the `prev_state` update.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/akundrock/sandbox/TradingAgents && python -m pytest tests/test_mes_radar.py -q`
Expected: ALL PASS (watch tests + one-shot + trigger + helper + existing radar tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/akundrock/sandbox/TradingAgents
git add cli/mes.py tests/test_mes_radar.py
git commit -m "feat(mes): radar --auto-check fires full check on READY in watch mode"
```

---

### Task 5: Full-suite verification and wrap-up

**Files:**
- No production changes; verification only.

**Interfaces:**
- Consumes: everything built in Tasks 1-4.
- Produces: confidence that nothing else regressed, and a clean branch.

- [ ] **Step 1: Run the full test suite**

Run: `cd /Users/akundrock/sandbox/TradingAgents && python -m pytest tests/ -q 2>&1 | tail -5`
Expected: ALL PASS, same failure count as the baseline before this branch (zero).

- [ ] **Step 2: Manual smoke of the CLI surface (no LLM, no network)**

```bash
cd /Users/akundrock/sandbox/TradingAgents
python -m cli.main mes radar --help   # shows --auto-check, --auto-check-cooldown, --no-llm, --no-log
```

If a recorded session CSV exists (e.g. under `out/`), also run:
`python -m cli.main mes radar --no-watch --mes-csv out/<file>.csv --auto-check --no-llm --no-log --as-of "11:00"` and confirm the radar panel prints and no journal file is created (because `--no-log`). Skip if no CSV is present — the CliRunner tests already cover the behavior.

- [ ] **Step 3: Verify the commit history is clean**

```bash
cd /Users/akundrock/sandbox/TradingAgents && git log --oneline main..HEAD
```

Expected: 5 commits — plan doc, `_run_check_once` refactor, `should_auto_check`, one-shot auto-check, watch-loop auto-check.

- [ ] **Step 4: Confirm out-of-scope files are untouched**

```bash
cd /Users/akundrock/sandbox/TradingAgents && git diff main --stat
```

Expected: only `cli/mes.py`, `tests/test_mes_radar.py`, `tradingagents/mes/radar.py`, `tradingagents/mes/__init__.py`, and the plan doc. No changes under `tradingagents/mes/journal.py`, `checklist.py`, `config.py`, or `tradingagents/agents/`.

---

## Self-Review Checklist (run before execution)

1. **Spec coverage:** trigger READY-only (`should_auto_check` + one-shot guard + watch guard) ✓; LLM follows `--no-llm` (gatekeeper created only `if not no_llm`, `past_context` skipped with `--no-llm`) ✓; transition + cooldown re-fire (Task 2 semantics + Task 4 wiring) ✓; no subprocess / same-bar verdict (snapshot + result passed in-hand) ✓; journal visible via `mes review` (`append_check` with the same fields as `check`) ✓; `--json` untouched ✓; `--alert` untouched ✓.
2. **Known rough edges to watch during execution:** (a) Rich `Live` under `CliRunner` is non-terminal — watch tests assert journal state and exit codes, not rendering; (b) `_ready_fixture` patches `mes_cli.evaluate` so tests are deterministic regardless of factory checklist thresholds; (c) monkeypatching `time.sleep` is global — fine because tests run sequentially under pytest by default.