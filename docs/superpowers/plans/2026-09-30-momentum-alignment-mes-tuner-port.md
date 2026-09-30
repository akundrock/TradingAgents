# Momentum-Alignment mes-tuner Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `MomentumMode` a first-class mes-tuner knob — config field, engine trigger, sweepable, LM-huntable, profile-driven — with cross-default byte-identical behavior.

**Architecture:** Six additive touchpoints in mes-tuner: a `StrategyConfig.MomentumMode` field (default `"cross"`), a mode-dependent momentum trigger in the engine mirroring TradingAgents' checklist `_momentum` exactly, a `"choice"` kind in the sweep candidate generator, its plumbing through the LM hunt schema/validators, a `MomentumMode: "cross"` key in the frozen baseline profile plus a new alignment profile, and a sync round-trip guard. Alignment is a strict superset of cross (shared hard-coded Laguerre gate); tests pin both the superset property and cross-mode byte-identity.

**Tech Stack:** Python 3.11+ / pytest / dataclasses in the mes-tuner repo; no new dependencies.

**Spec:** `/Users/akundrock/sandbox/TradingAgents/docs/superpowers/specs/2026-09-30-momentum-alignment-mes-tuner-validation-design.md` (§2 superset property, §3 ordering constraint, §4 port spec, §4.5/4.6 tests). The plan argues from the spec; executors read both. **Documented deviation from spec §4:** the spec lists five touchpoints; mechanical validation of a trial port proved the `"choice"` descriptor kind must additionally flow through `mes_tuner/lm/schema.py` (`PARAMETER_BOUNDS`, `ParameterDescriptor`, descriptions) and `mes_tuner/lm/validate.py` (choice validation + coercion) or the full suite fails to import (9 collection errors) — spec §1.2 requires the knob be LM-huntable, so these are in scope as Task 2.

**Validation note for implementers:** every code block in this plan was executed against the real repo and suite before being written down (14 momentum tests, 3 sweep tests, LM choice tests, W2.5 parity with the new key — 292 passed, then reverted). The fixture golden values in Task 1 are real captured values, not estimates. Re-verify rather than re-derive.

## Global Constraints

- **Implementation repo:** `/Users/akundrock/sandbox/thinkorswim-scripts/mes-tuner` (paths relative to that root). The spec lives in the sibling TradingAgents repo — **read-only**; no TA changes.
- **Branch:** feature branch off `main` @ `beb6a11` (`git checkout -b feat/momentum-mode-port`); `main` must stay clean until the plan completes.
- **Ordering constraint (spec §3, hard):** `StrategyConfig.from_dict` (`mes_tuner/config.py:138-140`) silently filters unknown keys. Tasks 1-2 (field, engine, sweep, LM plumbing) must be committed **before** any profile gains `"MomentumMode"` (Task 3). Violating it makes W2.5 fail with an unclassifiable diff (TA runs alignment, tuner silently drops the key).
- **Default `MomentumMode: str = "cross"`** keeps every existing profile and test byte-identical. No enum validation in the config: unknown strings are stored verbatim and the engine treats any non-`"alignment"` value as cross (pinned by test, mirrors TA).
- **Laguerre bounds stay hard-coded `0.2`/`0.8`** (spec §7 — not sweepable).
- **Strict superset (spec §2):** alignment removes only the same-bar-cross requirement; it must never remove a cross signal.
- **One commit per task**; the byte-identical pin (Task 1) and parity-unchanged gates run before `baseline.json` gains the key (Task 3) — spec §4.6.
- **Test runner:** `make test` from the mes-tuner root (= `.venv/bin/python -m pytest -q`; `.venv` exists). Baseline before this plan: 270 passed. Corpus: `../tos-market-data/data` (151 `mes_*.csv`); frozen fixture dates `("2026-03-27", "2026-04-08", "2026-05-08", "2026-03-26", "2026-05-13")` (`tests/_ta_mes_loader.py:96`). Data dir: `Path(__file__).resolve().parents[2] / "tos-market-data" / "data"` (= `tests/_ta_mes_loader.py:88`).
- **Conventions:** tests build `mes_tuner.data.loader.Bar` values with tz-aware timestamps and drive `Engine(cfg)` + `process_bar` per bar (mirror `tests/test_signal_fixtures.py`, `tests/test_mean_reversion.py`); skip fixture tests when recordings are missing.
- **Known pinned count:** `tests/test_lm_validate.py::test_field_map_includes_signal_toggles` pins `len(DEFAULT_TUNING) == 38`; Task 2 updates it to 39 (documented in-task).

---

### Task 1: `MomentumMode` config field + mode-dependent engine trigger

**Files:**
- Modify: `mes_tuner/config.py` (field next to `EnableMomentum`, line 15)
- Modify: `mes_tuner/engine/strategy.py:211-233` (momentum trigger block)
- Create: `tests/test_momentum_mode.py`

**Interfaces:**
- Consumes: `StrategyConfig` dataclass; `Engine.process_bar(bar) -> SignalSnapshot` with `momentum_long` / `momentum_short` booleans (`mes_tuner/engine/strategy.py:37-41, 180-233`); `load_bars_csv(path, tz)` from `mes_tuner.data.loader`.
- Produces: `StrategyConfig.MomentumMode: str = "cross"` (exact PascalCase name — Tasks 2-3 depend on it); engine trigger honoring `MomentumMode == "alignment"`; `tests/test_momentum_mode.py` whose frozen goldens later tasks must not disturb.

- [ ] **Step 1: Verify the pre-port golden (BEFORE any edit — byte-identical baseline)**

Run from the mes-tuner root; the output must match the `CROSS_FIXTURE_GOLDEN` constants embedded in Step 2's test file (they were captured on this machine from the pre-port engine at `beb6a11`). If any value differs, STOP and report — do not paste drifted values into the plan's golden.

```bash
cd /Users/akundrock/sandbox/thinkorswim-scripts/mes-tuner
.venv/bin/python - <<'EOF'
import hashlib, json
from dataclasses import asdict
from pathlib import Path
from mes_tuner.config import StrategyConfig
from mes_tuner.data.loader import load_bars_csv
from mes_tuner.engine.strategy import Engine

DATA = Path("../tos-market-data/data")
DATES = ("2026-03-27", "2026-04-08", "2026-05-08", "2026-03-26", "2026-05-13")
cfg = StrategyConfig.from_dict(json.loads(Path("profiles/baseline.json").read_text()))
for d in DATES:
    bars = load_bars_csv(str(DATA / f"mes_{d}.csv"), cfg.SessionLocation)
    eng = Engine(cfg)
    snaps = [eng.process_bar(b) for b in bars]
    mom_l = sum(1 for s in snaps if s.momentum_long)
    mom_s = sum(1 for s in snaps if s.momentum_short)
    fp = hashlib.sha256(repr([asdict(s) for s in snaps]).encode()).hexdigest()[:12]
    print(f'    "{d}": ({len(bars)}, {mom_l}, {mom_s}, "{fp}"),')
EOF
```

Expected output (pre-port engine, cross defaults):

```
    "2026-03-27": (90, 0, 2, "33be84b3adfa"),
    "2026-04-08": (174, 3, 4, "ab5012156553"),
    "2026-05-08": (90, 1, 1, "4bb74343f493"),
    "2026-03-26": (119, 2, 2, "fd1a2d79ca48"),
    "2026-05-13": (174, 0, 0, "6ad08b5d1a4a"),
```

- [ ] **Step 2: Write the failing test file `tests/test_momentum_mode.py`**

Create the file with **exactly** the content in the block below (the golden values from Step 1 are already pasted; Step 1 re-verifies them before you touch any code).

```python
"""Momentum-alignment port (spec §4.1/§4.2): MomentumMode field + engine trigger.

Cross (default) must stay byte-identical to the pre-port engine — pinned by a
frozen-fixture golden captured before the port (CROSS_FIXTURE_GOLDEN) plus a
synthetic pin. Alignment is the strict superset: it drops only the same-bar
SMA/VWAP cross requirement, keeping "SMA and VWAP agree" and the identical
hard-coded Laguerre gate (0.2/0.8). Unknown mode strings degrade to cross
(no enum validation, mirrors the TA checklist's stance)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from mes_tuner.config import StrategyConfig
from mes_tuner.data.loader import Bar, load_bars_csv
from mes_tuner.engine.strategy import Engine

BASE_TS = datetime.fromisoformat("2026-06-02T09:30:00-05:00")
DATA_DIR = Path(__file__).resolve().parents[2] / "tos-market-data" / "data"
FIXTURE_DATES = ("2026-03-27", "2026-04-08", "2026-05-08", "2026-03-26", "2026-05-13")
BASELINE_PROFILE = Path(__file__).resolve().parents[1] / "profiles/baseline.json"

#: Pre-port engine output over the frozen fixture, cross defaults: date ->
#: (bar count, momentum_long grants, momentum_short grants, sha256[:12] of the
#: repr of the per-bar SignalSnapshot-asdict stream). Captured before the port
#: landed; the cross-mode path must reproduce it exactly.
CROSS_FIXTURE_GOLDEN: dict[str, tuple[int, int, int, str]] = {
    # <Step-1 paste the five tuples verbatim here>
    "2026-03-27": (90, 0, 2, "33be84b3adfa"),
    "2026-04-08": (174, 3, 4, "ab5012156553"),
    "2026-05-08": (90, 1, 1, "4bb74343f493"),
    "2026-03-26": (119, 2, 2, "fd1a2d79ca48"),
    "2026-05-13": (174, 0, 0, "6ad08b5d1a4a"),
}


def _bar(minute: int, *, o: float, h: float, l: float, c: float, v: float = 100.0) -> Bar:
    return Bar(
        timestamp=BASE_TS + timedelta(minutes=minute),
        open=o, high=h, low=l, close=c, volume=v,
        add=500.0, tick=700.0, vold=100.0,
    )


def _momo_cfg(**overrides) -> StrategyConfig:
    cfg = StrategyConfig.default()
    cfg.EnableMomentum = True
    cfg.EnableVWAP = cfg.EnableATRRange = cfg.EnablePattern = False
    cfg.EnableADD = cfg.EnableTICK = cfg.EnableVOLD = cfg.EnableVolumeSurge = False
    cfg.EnableTierMinConfirmations = False
    cfg.MinConfirmations = 1
    cfg.RequireRTH = False
    cfg.EnforceSessionFilters = False
    cfg.AllowMissingInternals = True
    cfg.SMALength = 2
    cfg.NFE = 2
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _replay(bars: list[Bar], cfg: StrategyConfig) -> list:
    engine = Engine(cfg)
    return [engine.process_bar(bar) for bar in bars]


def _grants(snaps) -> list[tuple[bool, bool]]:
    return [(s.momentum_long, s.momentum_short) for s in snaps]


# Seed flat 100, dip to 90, pop to 130 (cross-up long fires bar 2), crash to 70
# (cross-down short fires bar 4), follow-through (bar 5: SMA stays below VWAP,
# Laguerre falling — alignment-only short grant, no same-bar cross). Indicator
# values verified against the engine: SMA/VWAP per bar 95.0/97.78, 110/102.38,
# 100/98.33, 67.5/95.0, 63.5/91.97; Laguerre 0/0/0.5/0.323/0.323/0.265.
MOMO_BARS = [
    _bar(0, o=100, h=100, l=100, c=100, v=100),
    _bar(1, o=100, h=120, l=80, c=90, v=200),
    _bar(2, o=120, h=130, l=130, c=130, v=50),
    _bar(3, o=80, h=70, l=70, c=70, v=50),
    _bar(4, o=70, h=75, l=65, c=65, v=50),
    _bar(5, o=70, h=72, l=60, c=62, v=50),
]


def test_momentum_mode_field_defaults_cross() -> None:
    assert StrategyConfig.default().MomentumMode == "cross"
    assert StrategyConfig.from_dict({"MomentumMode": "alignment"}).MomentumMode == "alignment"


def test_unknown_mode_degrades_to_cross() -> None:
    """Typo'd mode strings run the cross path (no enum validation, TA parity)."""
    cross = _grants(_replay(MOMO_BARS, _momo_cfg()))
    typo = _grants(_replay(MOMO_BARS, _momo_cfg(MomentumMode="typo")))
    expected = [
        (False, False),
        (False, False),
        (True, False),  # cross-up long on the pop bar
        (False, False),
        (False, True),  # cross-down short
        (False, False),
    ]
    assert cross == typo == expected


def test_cross_mode_byte_identical_synthetic() -> None:
    """Explicit MomentumMode="cross" reproduces the untouched-default engine."""
    implicit = [asdict(s) for s in _replay(MOMO_BARS, _momo_cfg())]
    explicit = [
        asdict(s)
        for s in _replay(MOMO_BARS, _momo_cfg(MomentumMode="cross"))
    ]
    assert explicit == implicit



def test_alignment_superset_synthetic() -> None:
    """Alignment ⊇ cross per bar/side, with exactly the bar-5 short added."""
    cross = _grants(_replay(MOMO_BARS, _momo_cfg()))
    align = _grants(_replay(MOMO_BARS, _momo_cfg(MomentumMode="alignment")))
    for (cross_l, cross_s), (align_l, align_s) in zip(cross, align):
        assert cross_l <= align_l and cross_s <= align_s
    # The alignment-only grant: bar 5 short (SMA 63.5 < VWAP 91.97, Laguerre
    # 0.265 falling, no same-bar cross) — cross keeps it False.
    assert cross[5] == (False, False)
    assert align[5] == (False, True)
    added = sum(
        1 for (cl, cs), (al, ash) in zip(cross, align) if (al and not cl) or (ash and not cs)
    )
    assert added == 1


@pytest.mark.parametrize("date", FIXTURE_DATES)
def test_frozen_fixture_cross_golden(date: str) -> None:
    mes_csv = DATA_DIR / f"mes_{date}.csv"
    if not mes_csv.is_file():
        pytest.skip(f"fixture recording missing: {mes_csv}")
    cfg = StrategyConfig.from_dict(json.loads(BASELINE_PROFILE.read_text()))
    bars = load_bars_csv(str(mes_csv), cfg.SessionLocation)
    snaps = _replay(bars, cfg)
    fp = hashlib.sha256(repr([asdict(s) for s in snaps]).encode()).hexdigest()[:12]
    n_long = sum(1 for s in snaps if s.momentum_long)
    n_short = sum(1 for s in snaps if s.momentum_short)
    assert (len(bars), n_long, n_short, fp) == CROSS_FIXTURE_GOLDEN[date]


@pytest.mark.parametrize("date", FIXTURE_DATES)
def test_frozen_fixture_alignment_superset(date: str) -> None:
    mes_csv = DATA_DIR / f"mes_{date}.csv"
    if not mes_csv.is_file():
        pytest.skip(f"fixture recording missing: {mes_csv}")
    cfg = StrategyConfig.from_dict(json.loads(BASELINE_PROFILE.read_text()))
    align_cfg = StrategyConfig.from_dict({**cfg.to_dict(), "MomentumMode": "alignment"})
    bars = load_bars_csv(str(DATA_DIR / f"mes_{date}.csv"), cfg.SessionLocation)
    cross = _replay(bars, cfg)
    align = _replay(bars, align_cfg)
    added = 0
    for c, a in zip(cross, align):
        assert a.momentum_long or not c.momentum_long
        assert a.momentum_short or not c.momentum_short
        added += (a.momentum_long and not c.momentum_long) + (a.momentum_short and not c.momentum_short)
    assert added >= 1

```

- [ ] **Step 3: Run the tests to verify they fail** Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_momentum_mode.py -q`
Expected: FAIL with `AttributeError: 'StrategyConfig' object has no attribute 'MomentumMode'` on every test that touches the field or the mode (`test_momentum_mode_field_defaults_cross`, the synthetic superset test, and the fixture superset tests). This is the required red state.

- [ ] **Step 4: Implement the config field**

In `mes_tuner/config.py`, immediately after `EnableMomentum: bool = True` (line 15), insert:

```python
    # Checklist momentum_mode (tradingagents/mes/config.py): "cross" = historic
    # byte-identical same-bar SMA/VWAP cross trigger; "alignment" = SMA/VWAP
    # agreement replaces only the same-bar cross — the Laguerre trend gate
    # (0.2 < rsi < 0.8, hard-coded) is untouched in both modes, so alignment
    # is a strict superset of cross. Unknown strings degrade to cross
    # (mirrors TA's no-enum-validation stance).
    MomentumMode: str = "cross"
```

- [ ] **Step 5: Implement the engine trigger**

In `mes_tuner/engine/strategy.py`, replace the momentum block (lines 211-233 — the `# @tos:95-98 momentum` comment and both `momentum_long`/`momentum_short` assignments) with the spec §4.2 ported form:

```python
        # @tos:95-98 momentum (+ checklist momentum_mode port: alignment drops
        # only the same-bar SMA/VWAP cross requirement — superset of cross;
        # Laguerre 0.2/0.8 bounds hard-coded on both paths, parity with TA).
        trend_up = lag_ready and lag_rsi > 0.2 and lag_rsi >= prev_lag_rsi
        trend_down = lag_ready and lag_rsi < 0.8 and lag_rsi <= prev_lag_rsi
        alignment = self.cfg.MomentumMode == "alignment"
        if alignment:
            long_trigger = sma_ready and vwap_ready and sma_val > vwap_val
            short_trigger = sma_ready and vwap_ready and sma_val < vwap_val
        else:
            long_trigger = (
                self._prev_sma_ready
                and self._prev_vwap_ready
                and sma_ready
                and vwap_ready
                and self._prev_sma < self._prev_vwap
                and sma_val > vwap_val
            )
            short_trigger = (
                self._prev_sma_ready
                and self._prev_vwap_ready
                and sma_ready
                and vwap_ready
                and self._prev_sma > self._prev_vwap
                and sma_val < vwap_val
            )
        momentum_long = self.cfg.EnableMomentum and long_trigger and trend_up
        momentum_short = self.cfg.EnableMomentum and short_trigger and trend_down
```

(The Laguerre `0.2`/`0.8` bounds stay hard-coded on both paths — spec §7.)

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_momentum_mode.py -q`
Expected: 14 passed — defaults, unknown-mode degradation, synthetic superset, synthetic byte-identity, 5 frozen goldens, 5 fixture superset tests. The goldens prove cross mode is byte-identical to the pre-port engine.

- [ ] **Step 7: Run the full suite**

Run: `make test`
Expected: 284 passed (270 pristine baseline + 14 new), 0 failures (default cross is byte-identical; this is the spec §4.6 ordering gate — runs before the profile gains the key in Task 3).

- [ ] **Step 8: Commit**

```bash
git add mes_tuner/config.py mes_tuner/engine/strategy.py tests/test_momentum_mode.py
git commit -m "feat(engine): MomentumMode knob (cross default) with checklist-alignment trigger"
```

---

### Task 2: Sweep + LM-hunt wiring — `momentumMode` as a one-candidate choice knob

**Files:**
- Modify: `mes_tuner/tuning/sweep.py` (`FIELD_MAP` line 15, `DEFAULT_TUNING` line 67-128, `_candidate_values` line 134-162, `run_sweep` call site lines 209-214)
- Modify: `mes_tuner/lm/schema.py` (`PARAMETER_BOUNDS` line 14-17, `ParameterDescriptor` line 61-68, `PARAMETER_DESCRIPTIONS` line ~192, `describe_available_parameters` line 199-216)
- Modify: `mes_tuner/lm/validate.py` (`validate_parameter_change` choice branch after the `time` branch; `apply_parameter_change` coercion chain)
- Modify: `tests/test_lm_validate.py` (append choice tests; update the pinned knob count)
- Create: `tests/test_sweep_momentum_mode.py`

**Interfaces:**
- Consumes: `StrategyConfig.MomentumMode` (Task 1).
- Produces: `_candidate_values(default, minimum, maximum, kind, candidates=None)` (new trailing kwarg; positional callers unaffected); `FIELD_MAP["momentumMode"] == "MomentumMode"`; `DEFAULT_TUNING` gains `{"name": "momentumMode", "type": "choice", "default": "cross", "candidates": ["alignment"]}`; `PARAMETER_BOUNDS` entries carry `"candidates"`; `validate_parameter_change` accepts `type: "choice"`; `ParameterDescriptor.candidates`. The LM-hunt path (spec §1.2 "LM-huntable") relies on all of these.

**Why the LM files are in scope (spec deviation note):** spec §4 lists five touchpoints, but a trial implementation proved `mes_tuner/lm/schema.py` derives `PARAMETER_BOUNDS` from every `DEFAULT_TUNING` descriptor and `KeyError: 'min'`s on a descriptor without `min`/`max` — the full suite fails to collect. Spec §1.2 requires the knob be "LM-huntable", so the choice kind must also flow through the LM schema/validate path. Treat this as part of Task 2's deliverable.

- [ ] **Step 1: Write the failing sweep tests `tests/test_sweep_momentum_mode.py`**

```python
"""Sweep wiring for the momentum-mode knob (spec §4.3).

One-param-at-a-time: momentumMode yields exactly one candidate
(cross -> alignment); the choice branch feeds descriptor["candidates"],
not the min/max numeric lattice.
"""

from __future__ import annotations

from pathlib import Path

from mes_tuner.config import StrategyConfig
from mes_tuner.tuning.sweep import DEFAULT_TUNING, FIELD_MAP, _candidate_values, run_sweep


def test_momentum_mode_in_field_map_and_default_tuning() -> None:
    assert FIELD_MAP["momentumMode"] == "MomentumMode"
    desc = next(d for d in DEFAULT_TUNING if d["name"] == "momentumMode")
    assert desc == {
        "name": "momentumMode",
        "type": "choice",
        "default": "cross",
        "candidates": ["alignment"],
    }


def test_candidate_values_choice_branch() -> None:
    assert _candidate_values("cross", None, None, "choice", candidates=["alignment"]) == ["alignment"]
    # Default already at the only candidate -> no candidates (idempotent sweep).
    assert _candidate_values("alignment", None, None, "choice", candidates=["alignment"]) == []
    # The choice branch ignores min/max entirely.
    assert _candidate_values("cross", 0, 10, "choice", candidates=["alignment"]) == ["alignment"]


def test_sweep_momentum_mode_single_candidate() -> None:
    """cross baseline -> exactly one momentumMode candidate (alignment)."""
    import json
    from datetime import datetime

    from mes_tuner.data.loader import Bar

    bars = [
        Bar(datetime.fromisoformat("2026-06-01T10:00:00-05:00"), 100, 101, 99, 100, 100, add=500),
        Bar(datetime.fromisoformat("2026-06-01T10:05:00-05:00"), 100, 101, 99, 100, 100, add=500),
    ]
    baseline = StrategyConfig.default()

    tuning = [d for d in DEFAULT_TUNING if d["name"] == "momentumMode"]
    plan = run_sweep(baseline, bars, out_dir=Path("/tmp/mes-tuner-sweep-momo"), tuning=tuning)

    assert plan.parameter_counts == {"momentumMode": 1}
    artifact = json.loads(Path(plan.results[0]["artifact"]).read_text())
    assert artifact["candidate_config"]["MomentumMode"] == "alignment"
```

- [ ] **Step 2: Append the failing LM-choice tests to `tests/test_lm_validate.py`**

```python
def test_validate_momentum_mode_choice_ok() -> None:
    change = ParameterChange("momentumMode", "cross", "alignment")
    validate_parameter_change(change)
    baseline = StrategyConfig.default()
    candidate = apply_parameter_change(baseline, change)
    assert candidate.MomentumMode == "alignment"


def test_validate_momentum_mode_rejects_unknown_value() -> None:
    change = ParameterChange("momentumMode", "cross", "bogus")
    with pytest.raises(ValueError, match="candidates"):
        validate_parameter_change(change)


def test_validate_momentum_mode_default_value_rejected() -> None:
    change = ParameterChange("momentumMode", "cross", "cross")
    with pytest.raises(ValueError):
        validate_parameter_change(change)  # cross is not in candidates -> rejected


def test_descriptors_carry_momentum_mode_candidates() -> None:
    from mes_tuner.lm.schema import describe_available_parameters, descriptors_to_json

    desc = next(
        d for d in describe_available_parameters(StrategyConfig.default())
        if d.name == "momentumMode"
    )
    assert desc.type == "choice"
    assert desc.candidates == ["alignment"]
    as_json = descriptors_to_json([desc])[0]
    assert as_json["candidates"] == ["alignment"]
```

(`ParameterChange`, `apply_parameter_change`, `validate_parameter_change`, `describe_available_parameters` etc. are already imported in `tests/test_lm_validate.py`; add only the two imports shown inside the last test.)

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sweep_momentum_mode.py tests/test_lm_validate.py -q`
Expected: FAIL — `_candidate_values() got an unexpected keyword argument 'candidates'`, `momentumMode` missing from `FIELD_MAP`/`DEFAULT_TUNING`, `KeyError: 'candidates'` in the LM choice tests, and `ParameterChange` lookups failing.

- [ ] **Step 4: Implement the sweep changes**

In `mes_tuner/tuning/sweep.py`:

1. `FIELD_MAP` — add after `"minConfirmations": "MinConfirmations",` (alphabetical position):

```python
    "momentumMode": "MomentumMode",
```

2. `DEFAULT_TUNING` — append after the `exitTime` entry (inside the list, before the closing `]`):

```python
    # Momentum-mode knob (spec §4.3): one-param-at-a-time yields exactly one
    # candidate (cross -> alignment). type "choice" reads descriptor
    # ["candidates"]; min/max are unused for this kind.
    {"name": "momentumMode", "type": "choice", "default": "cross", "candidates": ["alignment"]},
```

3. `_candidate_values` — new signature and choice branch (insert as the first `if`, mirroring the `bool` branch):

```python
def _candidate_values(
    default: Any,
    minimum: Any,
    maximum: Any,
    kind: str,
    candidates: list[Any] | None = None,
) -> list[Any]:
    if kind == "choice":
        return [c for c in (candidates or []) if c != default]

    if kind == "bool":
        return [not bool(default)]
    # ... the int/float64/time branches and the numeric tail are unchanged
```

4. `run_sweep` call site (the `_candidate_values(...)` invocation inside the descriptor loop) — pass candidates through and make min/max optional:

```python
        values = _candidate_values(
            current_value,
            descriptor.get("min"),
            descriptor.get("max"),
            descriptor["type"],
            candidates=descriptor.get("candidates"),
        )
```

- [ ] **Step 5: Implement the LM schema/validate plumbing**

In `mes_tuner/lm/schema.py`:

1. `PARAMETER_BOUNDS` — tolerate choice descriptors and carry candidates:

```python
PARAMETER_BOUNDS: dict[str, dict[str, Any]] = {
    d["name"]: {
        "type": d["type"],
        "min": d.get("min"),
        "max": d.get("max"),
        "default": d["default"],
        "candidates": d.get("candidates"),
    }
    for d in DEFAULT_TUNING
}
```

2. `ParameterDescriptor` — add the field (with default so positional construction elsewhere still works):

```python
@dataclass
class ParameterDescriptor:
    name: str
    description: str
    type: str
    default: Any
    min: Any
    max: Any
    candidates: list[Any] | None = None
```

3. `PARAMETER_DESCRIPTIONS` — add after the `"exitTime"` entry:

```python
    "momentumMode": "Momentum trigger mode: cross (same-bar SMA/VWAP cross, the historic behavior) or alignment (SMA/VWAP agreement without the same-bar cross — a strict superset that can only add signals).",
```

4. `describe_available_parameters` — pass the candidates through (`candidates=bounds.get("candidates"),` added to the `ParameterDescriptor(...)` construction).

In `mes_tuner/lm/validate.py`:

5. `validate_parameter_change` — add a `choice` branch between the `time` and `else` branches:

```python
    elif kind == "choice":
        from_mode = str(change.from_value)
        to_mode = str(change.to_value)
        allowed = list(bounds.get("candidates") or [])
        if to_mode not in allowed:
            raise ValueError(f"{change.name} value {to_mode!r} not in candidates {allowed}")
        if from_mode == to_mode:
            raise ValueError(f"{change.name} change is a no-op")
    else:
```

6. `apply_parameter_change` — insert a choice arm in the coercion chain (before the `time` branch):

```python
    elif bounds["type"] == "choice":
        data[field_name] = str(change.to_value)
```

7. `tests/test_lm_validate.py::test_field_map_includes_signal_toggles` — update the pinned count:

```python
    # 34 original knobs + dynamic-threshold family (Phase 3 W3.2)
    # + momentumMode (momentum-alignment port, spec §4.3).
    assert len(names) == 39
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_sweep_momentum_mode.py tests/test_lm_validate.py -q`
Expected: PASS (3 new sweep tests + 4 new LM tests + the updated pinned-count test).

- [ ] **Step 7: Run the full suite**

Run: `make test`
Expected: 291 passed (284 after Task 1 + 3 sweep + 4 LM-choice tests; the pinned count test updated in place), 0 failures.

- [ ] **Step 8: Commit**

```bash
git add mes_tuner/tuning/sweep.py mes_tuner/lm/schema.py mes_tuner/lm/validate.py tests/test_sweep_momentum_mode.py tests/test_lm_validate.py
git commit -m "feat(sweep,lm): momentumMode as a one-candidate choice knob with LM-hunt schema/validate plumbing"
```

---

### Task 3: Profiles + partition/parity gates + sync round-trip guard

**Files:**
- Modify: `profiles/baseline.json` (add `"MomentumMode": "cross"`)
- Create: `profiles/momentum_alignment.json` (copy with `"alignment"`)
- Modify: `tests/test_sync.py` (extend with the round-trip guard)
- Verify (no edits): `tests/test_w25_parity.py` — the standing cross-repo drift alarm

**Interfaces:**
- Consumes: `StrategyConfig.MomentumMode` (Task 1); TA's `_TUNER_FIELD_ALIASES["MomentumMode"]` via the unchanged `tests/_ta_mes_loader.py` shim (the aliased key flows into TA's `MesChecklistConfig.from_dict`, so the W2.5.2 partition stays exact with no loader edits).
- Produces: the profile keys the spec §5 validation protocol consumes (`make sweep/walkforward PROFILE=profiles/momentum_alignment.json`); the `merge_profile` round-trip guarantee for the key.

- [ ] **Step 1: Update `profiles/baseline.json` (ordering gate cleared by Tasks 1-2)**

Add the key immediately after `"EnableMomentum": true,` (line 2), mirroring the dataclass field order:

```json
  "EnableMomentum": true,
  "MomentumMode": "cross",
  "EnableVWAP": true,
```

Keep 2-space indent and the rest of the file byte-identical. Sanity check:

Run: `.venv/bin/python -c "import json; d=json.load(open('profiles/baseline.json')); assert d['MomentumMode']=='cross'; print(len(d), 'keys')"`
Expected: `73 keys` (72 pre-port + the new one).

- [ ] **Step 2: Create `profiles/momentum_alignment.json`**

Copy of the updated baseline with exactly one differing key:

```bash
cd /Users/akundrock/sandbox/thinkorswim-scripts/mes-tuner
.venv/bin/python - <<'EOF'
import json
from pathlib import Path

data = json.loads(Path("profiles/baseline.json").read_text())
data["MomentumMode"] = "alignment"
Path("profiles/momentum_alignment.json").write_text(json.dumps(data, indent=2) + "\n")
EOF
diff <(python3 -m json.tool --sort-keys profiles/baseline.json) <(python3 -m json.tool --sort-keys profiles/momentum_alignment.json)
```

Expected diff: exactly one line — `"MomentumMode": "alignment"` vs `"cross"`.

- [ ] **Step 3: Extend the sync round-trip guard (spec §4.4(3))**

Append to `tests/test_sync.py` (mirrors `test_write_appends_checklist_only_fields`):

```python
def test_write_preserves_momentum_mode_round_trip(tmp_path: Path) -> None:
    """--write keeps the momentum-mode key the .tos inputs don't carry (spec §4.4(3))."""
    import json

    sync = _sync_module()
    out = tmp_path / "profile.json"
    out.write_text(json.dumps({"MomentumMode": "alignment"}))
    assert sync.main(["--write", "--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["MomentumMode"] == "alignment"  # tuned value preserved, not clobbered
```

Run: `.venv/bin/python -m pytest tests/test_sync.py -q`
Expected: PASS — `merge_profile` is additive (`py_default.to_dict()` carries the new field; existing profile values win via `data.update(existing)`), so the key survives a `--write` round-trip with its tuned value.

- [ ] **Step 4: Run the W2.5 partition + parity gates (spec §4.5(3)/(4))**

Run: `.venv/bin/python -m pytest tests/test_w25_parity.py -q`
Expected: PASS — `test_w252_baseline_profile_partitions_exactly` still exact (`MomentumMode` flows through TA's `_TUNER_FIELD_ALIASES`), `test_w253_parity_gate_zero_unexplained` still zero unexplained diffs (cross is the default on both sides).

- [ ] **Step 5: Full suite**

Run: `make test`
Expected: 292 passed (284 after Task 1 + 3 sweep + 4 LM-choice + 1 sync guard), 0 failures.

- [ ] **Step 6: Commit**

```bash
git add profiles/baseline.json profiles/momentum_alignment.json tests/test_sync.py
git commit -m "feat(profiles): MomentumMode in baseline (cross) + momentum_alignment profile; sync round-trip guard"
```

---

### Task 4: End-to-end verification of the port (no code changes)

**Files:** none modified. This task proves the shipped port works through the real CLI paths the spec §5 validation protocol will use.

**Interfaces:**
- Consumes: everything from Tasks 1-3.
- Produces: recorded evidence (task report only — no commit) that parity is quiet at the default and the alignment profile runs through the real CLI paths.

- [ ] **Step 1: Parity target green with the updated profile**

Run: `make parity INSTRUMENT=mes`
Expected: exit 0, zero unexplained diffs (cross default both sides).

- [ ] **Step 2: Backtest the alignment profile (superset evidence)**

Run: `make backtest PROFILE=profiles/momentum_alignment.json OUT=out/momentum-alignment-smoke`
Expected: completes; more trades than the cross baseline (superset). Record both trade counts in the task report — no gate here; spec §5 Stages 1-3 own the economics.

- [ ] **Step 3: Sweep plan structure end-to-end**

Run: `make sweep PROFILE=profiles/baseline.json OUT=out/momentum-sweep-smoke`
Expected: the plan JSON under `out/momentum-sweep-smoke/` reports `parameter_counts["momentumMode"] == 1`. If the full sweep is slow, this step gates on plan structure only — interrupt after inspecting `parameter_counts`.

- [ ] **Step 4: Report only (no commit)**

Record in the task report: parity verdict, cross-vs-alignment trade counts, sweep plan `parameter_counts["momentumMode"]`. Leave `out/` artifacts untracked.

---

## Self-review record (writing-plans checklist)

1. **Spec coverage:** §4.1 field → Task 1; §4.2 trigger → Task 1; §4.3 sweep → Task 2 (+ the LM schema/validate plumbing required by §1.2's "LM-huntable" — the documented sixth touchpoint found by trial implementation); §4.4 profiles + sync guard → Task 3; §4.5 tests — (1) byte-identical pin: Task 1 (synthetic + frozen golden), (2) superset → Task 1 (synthetic + fixture), (3) partition exact → Task 3 via existing `test_w252` with the key present, (4) parity unchanged → Task 3 via existing `test_w253`, (5) sweep wiring → Task 2; §4.6 test ordering → Global Constraints + task order (goldens and parity gates run before `baseline.json` gains the key).
2. **Placeholders:** none — every code step carries complete code; the only fill-in is `CROSS_FIXTURE_GOLDEN`, whose exact values are printed in Task 1 Step 1 and must be re-verified before pasting.
3. **Type consistency:** `MomentumMode` (PascalCase field, camelCase sweep key, TA alias `momentum_mode`) used identically across all tasks; `_candidate_values(..., candidates=None)` matches the `run_sweep` call site; `ParameterDescriptor.candidates` matches the `PARAMETER_BOUNDS["candidates"]` key; `StrategyConfig.from_dict({**cfg.to_dict(), "MomentumMode": "alignment"})` pattern is consistent everywhere.

## Execution notes

- Per-task commits land on a feature branch of the mes-tuner repo; the ordering constraint holds at every commit. After all tasks, use superpowers:finishing-a-development-branch — merging to `main` and any push are the user's call.
- Spec §5 Stages 0-4 (corpus ablation, one-param sweep, walk-forward gates, LM hunt + promotion) are **operational runs over real data**, not code tasks — they start after this plan merges, per spec §6's gate ordering.
- Trial-implementation evidence (all code in this plan executed green against the real repo, then reverted to a clean `main`): full suite 292 passed with the complete change set applied; the LM touchpoint discovery (`lm/schema.py` `PARAMETER_BOUNDS` / `ParameterDescriptor` / `validate.py` choice plumbing) is baked into Task 2 and accounts for the +5 tests over the spec's five-touchpoint count.
