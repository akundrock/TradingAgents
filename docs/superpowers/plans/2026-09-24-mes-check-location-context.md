# MES Check Location Context (Roadmap Option A Add-On) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich `summarize_checks` rows with each check's price-vs-VWAP/OR location and the thesis frame in force at log time, so the end-of-day review grades missed flips systematically instead of luck-of-the-narrative.

**Architecture:** Write-time capture only — `append_check` stores the snapshot's opening-range bounds (`orb_high`/`orb_low`) alongside the already-stored `last_price`/`vwap`. Read-time presentation only — `summarize_checks` renders a single compact `Location` column and a per-row `Frame` column derived by walking the day's interleaved journal records in append order (mirroring `active_frame` last-wins semantics). No recomputation at review time: `mes review` stays journal-only and replay-safe. The review prompt gets one explanatory paragraph; the roadmap doc records the scope amendment.

**Tech Stack:** Python 3.11, pytest (unit-marked), JSONL append-only journal, f-string prompt templates.

**Spec:** `/Users/akundrock/sandbox/TradingAgents/docs/superpowers/specs/2026-09-24-mes-thesis-flip-roadmap.md` ("Agreed sequencing" item 2, A-as-add-on; amendment in Task 4).

## Global Constraints

- **Journal is append-only JSONL.** Never rewrite or reorder existing records; new fields are forward-only additions to `append_check`.
- **Review stays journal-only / replay-safe.** `summarize_checks` may only read what is already in the journal — no recomputing the opening range from bars at review time.
- **Legacy records must render `-`, never crash.** Records written before this change lack `orb_high`/`orb_low`; the Location cell degrades gracefully.
- **Presentation only.** No changes to `mes check` runtime flow, gates, or any auto-check behavior (that is Option C's territory, out of scope).
- **Branch:** implement on `feat/mes-thesis-flip` (current working branch).
- **Test conventions:** pytest, each test decorated `@pytest.mark.unit`; factories from `tests/mes_factories.py`; run with `python -m pytest tests/test_mes_journal.py -v` from the repo root.
- **Session date in tests:** `2026-03-30` (Monday) via `tests/mes_factories.py`; `make_mes_series()` fixes VWAP at `99.0`, OR bounds at `101.0`/`98.0`, close at `100.0` (overridable via kwargs, including `opening_range_high=None`).

---

### Task 1: Capture opening-range bounds on check records

**Files:**
- Modify: `/Users/akundrock/sandbox/TradingAgents/tradingagents/mes/journal.py:151-153` (inside `append_check`)
- Test: `/Users/akundrock/sandbox/TradingAgents/tests/test_mes_journal.py`

**Interfaces:**
- Consumes: `snapshot.mes.opening_range_high` / `snapshot.mes.opening_range_low` (`float | None`, defined at `tradingagents/mes/snapshot.py:103-104`); `_num()` at `tradingagents/mes/journal.py:86` (coerces to `float | None`, None stays None).
- Produces: check records gain two keys, `orb_high: float | None` and `orb_low: float | None`, written after `vwap`. Task 2's Location column reads these keys.

- [x] **Step 1: Write the failing tests**

In `/Users/akundrock/sandbox/TradingAgents/tests/test_mes_journal.py`, update the factory import line:

```python
from tests.mes_factories import DEFAULT_AS_OF, make_mes_series, make_snapshot
```

Then add these two tests after `test_check_record_captures_the_result_and_internals` (around line 97):

```python
@pytest.mark.unit
def test_check_record_captures_opening_range_bounds(journal):
    snapshot = make_snapshot()
    journal.append_check(snapshot=snapshot, result=evaluate(snapshot, "long"))

    record = journal.load_checks("2026-03-30")[0]
    assert record["orb_high"] == 101.0
    assert record["orb_low"] == 98.0


@pytest.mark.unit
def test_check_record_tolerates_missing_opening_range(journal):
    snapshot = make_snapshot(mes=make_mes_series(opening_range_high=None, opening_range_low=None))
    journal.append_check(snapshot=snapshot, result=evaluate(snapshot, "long"))

    record = journal.load_checks("2026-03-30")[0]
    assert record["orb_high"] is None
    assert record["orb_low"] is None
```

(`make_mes_series()` defaults to `opening_range_high=101.0`, `opening_range_low=98.0`, and its `**overrides` loop sets any passed values onto the `SeriesState`.)

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_journal.py::test_check_record_captures_opening_range_bounds tests/test_mes_journal.py::test_check_record_tolerates_missing_opening_range -v`
Expected: FAIL with `KeyError: 'orb_high'` on both.

- [x] **Step 3: Implement — add the two fields to `append_check`**

In `/Users/akundrock/sandbox/TradingAgents/tradingagents/mes/journal.py`, inside `append_check`, change:

```python
            "last_price": _num(result.last_price),
            "vwap": _num(result.vwap),
            "atr": _num(result.atr),
```

to:

```python
            "last_price": _num(result.last_price),
            "vwap": _num(result.vwap),
            "orb_high": _num(snapshot.mes.opening_range_high),
            "orb_low": _num(snapshot.mes.opening_range_low),
            "atr": _num(result.atr),
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_journal.py -v`
Expected: all PASS (the two new keys are additive; no existing assertion inspects the full record dict except `test_check_record_captures_the_result_and_internals`, which only asserts named keys).

- [x] **Step 5: Commit**

```bash
git add tradingagents/mes/journal.py tests/test_mes_journal.py
git commit -m "feat(mes-journal): capture OR bounds on check records"
```

---

### Task 2: Location + Frame columns in `summarize_checks`

**Files:**
- Modify: `/Users/akundrock/sandbox/TradingAgents/tradingagents/mes/journal.py` — add module helpers after `_num` (after line 95), replace `summarize_checks` body (`journal.py:408-431`)
- Test: `/Users/akundrock/sandbox/TradingAgents/tests/test_mes_journal.py`

**Interfaces:**
- Consumes: `orb_high`/`orb_low` keys from Task 1; `load_day(date) -> list[dict]` (`journal.py:299`, returns all records in append order — checks interleaved with hypothesis/flip/other kinds); `_first_line` (`journal.py:23`).
- Produces: module-level helpers `_location_label(check: dict) -> str` and `_frame_timeline(day: list[dict]) -> list[str]`; `summarize_checks(date: str) -> str` now emits a 9-column table. The only consumer is `cli/mes.py:542` (`mes review`), which uses the returned markdown unchanged — no caller edits.

- [x] **Step 1: Write the failing tests**

Add to `/Users/akundrock/sandbox/TradingAgents/tests/test_mes_journal.py` after `test_summarize_checks_with_no_checks` (around line 153):

```python
@pytest.mark.unit
def test_summarize_checks_shows_location_and_frame(journal):
    journal.save_hypothesis(date="2026-03-30", hypothesis_markdown="Trend up")
    pre = make_snapshot(mes=make_mes_series(close=101.5))
    journal.append_check(snapshot=pre, result=evaluate(pre, "long"), verdict_markdown="**Verdict**: Wait")
    journal.append_flip(
        "2026-03-30",
        reason="VWAP lost on rolling internals",
        new_frame="",
        price=101.5,
        vwap=99.0,
    )
    post = make_snapshot(as_of=DEFAULT_AS_OF.replace(hour=13, minute=30), mes=make_mes_series(close=97.5))
    journal.append_check(snapshot=post, result=evaluate(post, "long"), verdict_markdown="**Verdict**: Wait")

    summary = journal.summarize_checks("2026-03-30")
    assert "| Time | Side | Frame | Score | Tier | Gates | Tradeable | Location | Verdict |" in summary
    assert "+2.50 vs VWAP · above OR-H" in summary
    assert "-1.50 vs VWAP · below OR-L" in summary
    assert "morning" in summary
    assert "flip 1" in summary


@pytest.mark.unit
def test_summarize_checks_location_handles_legacy_records(journal):
    # A record from before OR capture: no orb_high/orb_low keys at all.
    journal.directory.mkdir(parents=True, exist_ok=True)
    legacy = (
        '{"kind": "check", "logged_at": "2026-03-30T11:00:00", "as_of": "2026-03-30T11:00:00",'
        ' "session_date": "2026-03-30", "side": "long", "score": 6, "max_score": 10,'
        ' "tier": "standard", "gates_ok": true, "tradeable": true,'
        ' "last_price": 100.0, "vwap": 99.0, "verdict": "**Verdict**: Wait"}\n'
    )
    (journal.directory / "2026-03-30.jsonl").open("a", encoding="utf-8").write(legacy)

    summary = journal.summarize_checks("2026-03-30")
    assert "+1.00 vs VWAP" in summary  # VWAP context still renders
    assert "OR" not in summary  # OR context absent, not guessed


@pytest.mark.unit
def test_summarize_checks_frame_is_dash_before_any_frame_record(journal):
    snapshot = make_snapshot()
    journal.append_check(snapshot=snapshot, result=evaluate(snapshot, "long"))

    summary = journal.summarize_checks("2026-03-30")
    assert "| - |" in summary  # no hypothesis/flip journaled yet
```

Also update the header assertion in the existing `test_summarize_checks_contains_score_and_tier` (line ~141) — replace `assert "| Time | Side | Score | Tier |" in summary` with:

```python
    assert "| Time | Side | Frame | Score | Tier |" in summary
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mes_journal.py -v -k summarize`
Expected: FAIL — the new tests find no "Frame"/"Location" columns; the updated score/tier test fails against the old 7-column header.

- [x] **Step 3: Implement the helpers and rework `summarize_checks`**

In `/Users/akundrock/sandbox/TradingAgents/tradingagents/mes/journal.py`, immediately after the `_num` function ends (after line 95), add:

```python
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
```

- [x] **Step 4: Replace the `summarize_checks` body**

Replace the current body (`journal.py:408-431`) with exactly this implementation:

```python
    def summarize_checks(self, date: str) -> str:
        day = self.load_day(date)
        if not any(r.get("kind") == "check" for r in day):
            return f"No checks logged for {date}."
        frames = iter(_frame_timeline(day))
        rows = [
            "| Time | Side | Frame | Score | Tier | Gates | Tradeable | Location | Verdict |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for check in (r for r in day if r.get("kind") == "check"):
            as_of = str(check.get("as_of", ""))
            time_label = as_of[11:16] if len(as_of) >= 16 else as_of
            rows.append(
                "| {time} | {side} | {frame} | {score}/{max_score} | {tier} | {gates} | {tradeable} | {location} | {verdict} |".format(
                    time=time_label,
                    side=check.get("side", "?"),
                    frame=next(frames),
                    score=check.get("score", 0),
                    max_score=check.get("max_score", 0),
                    tier=check.get("tier", "?"),
                    gates="pass" if check.get("gates_ok") else "fail",
                    tradeable="yes" if check.get("tradeable") else "no",
                    location=_location_label(check),
                    verdict=_first_line(check.get("verdict", "")) or "-",
                )
            )
        return "\n".join(rows)
```

- [x] **Step 4b: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_journal.py -v`
Expected: all PASS (three new tests + updated score/tier test + all pre-existing tests).

- [x] **Step 5: Commit**

```bash
git add tradingagents/mes/journal.py tests/test_mes_journal.py
git commit -m "feat(mes-journal): Location + Frame columns in summarize_checks"
```

---

### Task 3: Review prompt explanation for the new columns

**Files:**
- Modify: `/Users/akundrock/sandbox/TradingAgents/tradingagents/agents/mes/review_agent.py:45-46`
- Test: `/Users/akundrock/sandbox/TradingAgents/tests/test_mes_agents.py`

**Interfaces:**
- Consumes: `create_mes_review_agent(llm)(hypothesis=..., checks_summary=..., outcome_summary=...)` — signature unchanged; `FakeLLM.prompts` records the rendered prompt string for assertions (`tests/test_mes_agents.py:41-57`).
- Produces: prompt paragraph after the "Logged Checks Across The Session:" header that teaches the review LLM the `Location`/`Frame` column semantics. No caller changes.

- [x] **Step 1: Write the failing test**

Add to `/Users/akundrock/sandbox/TradingAgents/tests/test_mes_agents.py` after `test_review_agent_falls_back_to_free_text` (around line 392, inside the review-agent test section):

```python
@pytest.mark.unit
def test_review_prompt_explains_check_location_columns():
    llm = FakeLLM({SessionReview: _review()})
    create_mes_review_agent(llm)(
        hypothesis="Trend up",
        checks_summary="| Time | Side | Frame |",
        outcome_summary="-1 handle",
    )
    prompt = llm.prompts[0]
    assert "Location" in prompt
    assert "Frame" in prompt
    assert "invalidation first became visible" in prompt
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mes_agents.py::test_review_prompt_explains_check_location_columns -v`
Expected: FAIL with `assert 'Location' in prompt` (or the missing-sentence assertion).

- [x] **Step 3: Implement — insert the explanatory paragraph**

In `/Users/akundrock/sandbox/TradingAgents/tradingagents/agents/mes/review_agent.py`, replace lines 45-46:

```python
**Logged Checks Across The Session:**
{checks_summary}"""
```

with:

```python
**Logged Checks Across The Session:**

Each row shows the thesis Frame in force at that moment (``morning`` or ``flip N``,
where ``flip N`` is the Nth journaled re-read) and the check's Location — the
signed distance from last price to VWAP plus the position versus the opening
range (above OR-H, below OR-L, or in OR). Use these columns to date when the
invalidation first became visible in the checks, and to catch checks that kept
arguing the morning frame after it had already flipped.
{checks_summary}"""
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mes_agents.py -v`
Expected: all PASS (existing prompt assertions like `"Trend up" in prompt` are unaffected — only a paragraph was added inside the Logged Checks section).

- [x] **Step 5: Commit**

```bash
git add tradingagents/agents/mes/review_agent.py tests/test_mes_agents.py
git commit -m "feat(mes-review): teach review prompt to read Location/Frame columns"
```

---

### Task 4: Amend the roadmap's Option A item

**Files:**
- Modify: `/Users/akundrock/sandbox/TradingAgents/docs/superpowers/specs/2026-09-24-mes-thesis-flip-roadmap.md` — "Agreed sequencing" section, item 2 (search for `**A-as-add-on:**`)

**Interfaces:**
- Consumes: nothing.
- Produces: doc truth reflecting the shipped scope (OR capture at write time, frame-per-row column) so Option C's future executors read the correct A scope.

- [x] **Step 1: Amend sequencing item 2**

Replace the bullet starting with `2. **A-as-add-on:** enrich \`summarize_checks\` rows...` with:

```markdown
2. **A-as-add-on:** enrich `summarize_checks` rows with each check's price-vs-VWAP/OR context so the review's missed-flip grading is systematic, not luck-of-the-narrative. Cheap; `journal.summarize_checks` + review prompt only. **Shipped:** `append_check` stores `orb_high`/`orb_low` at write time (legacy records render `-`), rows carry a compact `Location` cell (`+2.50 vs VWAP · above OR-H`) and a `Frame` column (`morning` / `flip N` from the append-order frame walk), and the review prompt explains both columns — this is the evidence trail C's trust period grades against.
```

- [x] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-09-24-mes-thesis-flip-roadmap.md
git commit -m "docs(mes-roadmap): mark A add-on shipped (OR capture + frame-per-row)"
```

---

### Task 5: Full-suite verification

**Files:** none (verification only).

- [x] **Step 1: Run the directly-touched test files**

Run: `cd /Users/akundrock/sandbox/TradingAgents && python -m pytest tests/test_mes_journal.py tests/test_mes_agents.py -v`
Expected: all PASS.

- [x] **Step 2: Run the wider MES suite**

Run: `cd /Users/akundrock/sandbox/TradingAgents && python -m pytest tests/ -k mes -q`
Expected: no failures. (`test_mes_cli_skip.py` and `test_mes_stop_quality.py` touch `append_check`/`summarize_checks` only through normal flows; the added keys are additive and nothing else asserts the old 7-column header.)

- [x] **Step 3: Live smoke of the rendered table**

Run from the repo root:

```bash
cd /Users/akundrock/sandbox/TradingAgents && python - <<'EOF'
import sys, tempfile
sys.path.insert(0, ".")
from tradingagents.mes.journal import MesJournal
from tradingagents.mes.checklist import evaluate
from tests.mes_factories import make_snapshot, make_mes_series, DEFAULT_AS_OF

with tempfile.TemporaryDirectory() as tmp:
    j = MesJournal({"mes_journal_dir": tmp})
    j.save_hypothesis(date="2026-03-30", hypothesis_markdown="Trend up")
    pre = make_snapshot(mes=make_mes_series(close=101.5))
    j.append_check(snapshot=pre, result=evaluate(pre, "long"), verdict_markdown="**Verdict**: Wait")
    j.append_flip("2026-03-30", reason="VWAP lost", new_frame="", price=101.5, vwap=99.0)
    post = make_snapshot(as_of=DEFAULT_AS_OF.replace(hour=13, minute=30))
    j.append_check(snapshot=post, result=evaluate(post, "long"), verdict_markdown="**Verdict**: Wait")
    print(j.summarize_checks("2026-03-30"))
EOF
```

Expected: a 9-column table; first row `... | morning | ... | +2.50 vs VWAP · above OR-H | ...`, second row `... | flip 1 | ... | +1.00 vs VWAP · in OR | ...` (default close 100.0 sits inside OR 98–101).

- [x] **Step 4: Wrap up**

Report files changed, test results, and note that `mes review` needs no code changes — the next `mes review` run (9/24 session) emits the enriched table automatically.

---

## Notes for executors

- Work on branch `feat/mes-thesis-flip`; do not merge to `main` (merge is the user's explicit action).
- Do not touch `cli/mes.py` — the review command consumes `summarize_checks` output as a string; zero caller changes.
- `journal.py` line numbers shift after Task 2's helper insertion; locate `summarize_checks` by name, not line number.
- Carried backlog (do NOT fold into this plan): `mes check --watch` stale-frame fix, `rule_fired` re-arm dedup, `expires_on` invented-date hazard, `tool_choice` for `openai_compatible`.
