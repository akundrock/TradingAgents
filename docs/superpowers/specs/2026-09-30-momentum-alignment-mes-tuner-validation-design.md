# Momentum-Alignment × mes-tuner: Cross-Repo Design & Validation Protocol

- **Date:** 2026-09-30
- **Status:** Proposed (pending implementation of the mes-tuner port, Part 1)
- **Scope:** TradingAgents (`~/sandbox/TradingAgents`) × mes-tuner (`~/sandbox/thinkorswim-scripts/mes-tuner`)
- **Related:** `docs/superpowers/specs/2026-09-29-mes-mr-surfacing-momentum-alignment-design.md` (Part B — shipped on the TA side)

---

## 1. Goal

1. **Document** the `momentum_mode` change set that shipped in TradingAgents (TA) and map it onto
   every mes-tuner surface it touches (config alias, engine gate, sweep table, parity partition).
2. **Specify the bounded mes-tuner port** that makes `MomentumMode` a first-class tuner knob
   (sweepable, walk-forwardable, LM-huntable).
3. **Define the validation protocol** — backtest + one-param sweep + walk-forward with the
   existing promotion gates — that must pass before `momentum_mode="alignment"` is opted into
   for live use.

The two repos stay in their established roles: **TA is the live checklist** (per-bar verdicts,
MR surfacing, journal), **mes-tuner is the economics engine** (simulator with cost model,
sweep, walk-forward, promotion gates). Nothing here changes that boundary; the port in Part 1
just gives the tuner the same momentum knob the checklist already has.

---

## 2. Background — what shipped in TA (recap with references)

| Surface | Location | Semantics |
|---|---|---|
| Config field | `tradingagents/mes/config.py` — `momentum_mode: str = "cross"` | `"cross"` = historic byte-identical behavior; `"alignment"` = SMA/VWAP **agreement** replaces only the same-bar cross; Laguerre gate untouched |
| Alias | `tradingagents/mes/config.py:18` — `_TUNER_FIELD_ALIASES["MomentumMode"]` | tuner-style PascalCase profile keys round-trip into `MesChecklistConfig.from_dict` |
| Env | `TRADINGAGENTS_MES_MOMENTUM_MODE` | env override, checked after explicit config |
| Checklist item | `tradingagents/mes/checklist.py:137-154` (`_momentum`), label at `checklist.py:410-413` | mode-labeled item (`momentum [cross]` / `momentum [alignment]`) |
| Ablation | `tradingagents/mes/backtest/ablations.py:193-204` — id `momentum-alignment` | replay overlay forcing `momentum_mode="alignment"` vs the cross baseline |

**Core safety property (the whole reason validation is tractable):** alignment is a **strict
superset** of cross. Both modes share the identical Laguerre trend gate (`0.2 < rsi < 0.8`,
hard-coded both sides); alignment only *removes* the requirement that the SMA crossed VWAP
on the same bar, keeping "SMA and VWAP agree" plus the identical Laguerre trend. Consequences:

- Every cross signal fires in alignment mode → `alignment ⊇ cross`, per side, per bar.
- Sweep/WF results in alignment mode can never *lose* cross signals, only add them — a
  downgrade of the baseline is impossible by construction; the risk is economic (extra
  low-quality entries), which is exactly what the simulator + gates measure.
- No enum validation by design: typo'd mode strings degrade to `cross` (pinned by TA test).

---

## 3. Cross-repo surface map

| Concept | TradingAgents | mes-tuner | Bridge |
|---|---|---|---|
| Config field | `MesChecklistConfig.momentum_mode` | `StrategyConfig.MomentumMode` — **missing (gap)** | `_TUNER_FIELD_ALIASES["MomentumMode"]` (TA side, config.py:18) |
| Profile key | — | `profiles/baseline.json["MomentumMode"]` (not yet present) | aliased key → TA `from_dict`; partition test W2.5.2 |
| Engine gate | `checklist.py:137-154` `_momentum` | `engine/strategy.py:211-230` (cross only) | same-bar cross + Laguerre 0.2/0.8 both sides |
| Sweep knob | — | `SWEEP_PARAMS`/`FIELD_MAP` in `tuning/sweep.py` — **no `momentumMode` entry** | — |
| Named ablation | `mes ablate --ablation momentum-alignment` | `make replay REPLAY_ABLATIONS=momentum-alignment` (cross-repo, runs TA console) | — |
| Replay harness | `cli/mes.py` `mes ablate` / `mes replay` (`--data-dir`, `--start/--end`, `--date`, `--min-bars`) | `make replay` → TA console script (`TA ?= tradingagents`) | `ORDER_HISTORY=../../TradingAgents/mes-order-history.csv` cross-check |
| Parity | `tests/_ta_mes_loader.py` (test-only shim) | `tests/test_w25_parity.py`, frozen fixture `FIXTURE_DATES` | exact 3-way key partition of `baseline.json` |

**Corpus:** 151 `mes_*.csv` sessions at `~/sandbox/thinkorswim-scripts/tos-market-data/data`
(`DATA ?= ../tos-market-data/data` in the tuner Makefile). Frozen 5-session W2.5 fixture:
`2026-03-27, 2026-04-08, 2026-05-08, 2026-03-26, 2026-05-13`.

**Ordering constraint (do not violate):** `StrategyConfig.from_dict`
(`mes_tuner/config.py:138-140`) **silently filters unknown keys**. If `baseline.json` gains
`MomentumMode` before the tuner field exists, TA would run `alignment` while the tuner drops
the key and runs cross → per-bar verdict divergence on alignment-only grants → W2.5 fails
with an unclassifiable diff. The port (Part 1, steps 4.1-4.3) must land **before** any
profile gains the key.

---


## 4. Part 1 — Spec: mes-tuner port (bounded)

Five touchpoints in mes-tuner, all additive and cross-defaulted.

### 4.1 `mes_tuner/config.py` — add the field

Add next to `EnableMomentum`:

```python
MomentumMode: str = "cross"
```

Default `"cross"` keeps every existing profile and test byte-identical.

### 4.2 `mes_tuner/engine/strategy.py:211-230` — mode-dependent trigger

Current cross-only form (both sides identical in shape):

```python
momentum_long = (
    self.cfg.EnableMomentum
    and self._prev_sma_ready and self._prev_vwap_ready and sma_ready and vwap_ready
    and self._prev_sma < self._prev_vwap and sma_val > vwap_val
    and trend_up
)
```

Ported form (mirrors TA `_momentum` exactly; readiness clauses unchanged):

```python
alignment = self.cfg.MomentumMode == "alignment"
if alignment:
    long_trigger = sma_ready and vwap_ready and sma_val > vwap_val
    short_trigger = sma_ready and vwap_ready and sma_val < vwap_val
else:
    long_trigger = (
        self._prev_sma_ready and self._prev_vwap_ready and sma_ready and vwap_ready
        and self._prev_sma < self._prev_vwap and sma_val > vwap_val
    )
    short_trigger = (
        self._prev_sma_ready and self._prev_vwap_ready and sma_ready and vwap_ready
        and self._prev_sma > self._prev_vwap and sma_val < vwap_val
    )
momentum_long = self.cfg.EnableMomentum and long_trigger and trend_up
momentum_short = self.cfg.EnableMomentum and short_trigger and trend_down
```

- Laguerre bounds stay hard-coded `0.2`/`0.8` (parity with TA; see §7).
- `"cross"` (and any unknown string) must produce output identical to today — pinned by test.

### 4.3 `mes_tuner/tuning/sweep.py` — make the mode sweepable

1. `FIELD_MAP`: add `"momentumMode": "MomentumMode"`.
2. `DEFAULT_TUNING`: add descriptor
   `{"name": "momentumMode", "type": "choice", "default": "cross", "candidates": ["alignment"]}`.
3. `_candidate_values`: add a `"choice"` branch. Note `run_sweep` currently calls
   `_candidate_values(current, min, max, kind)` — for choice descriptors it must also pass
   `descriptor["candidates"]` (bool/int/float64/time kinds don't fit a string mode).

`run_sweep` is one-param-at-a-time, so `momentumMode` yields exactly one candidate
(`cross → alignment`); screening of *adjacent* knobs under alignment happens via the
alignment profile (4.4) plus the sweep's existing single-param mechanics.

### 4.4 Profiles

1. `profiles/baseline.json`: add `"MomentumMode": "cross"` — the W2.5.2 partition stays exact
   (the key is aliased on the TA side), and TA receives `momentum_mode="cross"` (its own
   default) → replay verdicts unchanged.
2. New `profiles/momentum_alignment.json`: copy of baseline with
   `"MomentumMode": "alignment"`.
3. Guard: `make sync-inputs` (`tools/sync_tos_inputs.py`) must not silently drop the new key —
   add a regression assertion that `MomentumMode` survives a sync round-trip.

### 4.5 Tests (mes-tuner side)

1. **Byte-identical cross mode:** with `MomentumMode` unset/`"cross"`, per-bar verdicts and
   trades on the frozen fixture equal the pre-port engine (regression pin).
2. **Superset property (tuner engine):** over the fixture + one arbitrary corpus session,
   every signal granted in cross mode is granted in alignment mode; at least one
   alignment-only grant exists (mirror of TA's ablation test).
3. **W2.5 partition stays exact** after `"MomentumMode"` joins `baseline.json` (aliased key).
4. **Parity unchanged at default:** W2.5 replay with the updated `baseline.json` produces the
   identical per-bar diff classes as before the port (cross is default on both sides).
5. **Sweep wiring:** `momentumMode` appears in a sweep plan with exactly one candidate
   (`alignment`); `_candidate_values("choice")` unit test.

### 4.6 Test ordering

Steps 4.5(1)/(4) must run **before** `baseline.json` gains the key (enforces the ordering
constraint from §3); the port commits in one change set: field + engine + sweep + profile +
tests together.

---


## 5. Part 2 — Validation protocol

Stage order is mandatory (correctness before economics). Commands run from
`~/sandbox/thinkorswim-scripts/mes-tuner` with defaults `INSTRUMENT=mes`,
`DATA=../tos-market-data/data` (151 sessions), `PROFILE=profiles/baseline.json`.

### Stage 0 — correctness baselines (frozen references)

| Check | Command | Pass criterion |
|---|---|---|
| TA unit suite | `pytest` (in `~/sandbox/TradingAgents`) | 1561 passed, 2 skipped, 72 subtests (current known-good, incl. the pending repaint-fix set) |
| TA momentum tests | `pytest tests/test_mes_momentum*.py -q` | 14 tests pass live |
| Cross-repo W2.5 parity | `pytest tests/test_w25_parity.py` (tuner repo) | green before and after the port |
| TOS golden parity | `make parity INSTRUMENT=mes` | unchanged vs golden exports |
| Baseline backtest | `make backtest INSTRUMENT=mes` | record metrics as the frozen reference for all deltas below |

### Stage 1 — TA-native replay validation (real checklist, no new code)

```bash
tradingagents mes ablate --data-dir ../tos-market-data/data \
  --date 2026-03-27 --date 2026-04-08 --date 2026-05-08 \
  --date 2026-03-26 --date 2026-05-13 --ablation momentum-alignment   # frozen fixture
tradingagents mes ablate --data-dir ../tos-market-data/data --ablation momentum-alignment
```

Equivalent make-level path: `make replay REPLAY_ABLATIONS=momentum-alignment` (from the tuner,
via the `TA ?= tradingagents` console script).

**Pass criteria:**

1. Baseline-anchored delta table shows alignment-only grants; **zero cross-only rows** per
   session (superset invariant on real data).
2. Hand-check 3 alignment-only grants against the bar data: SMA/VWAP agreement + Laguerre
   trend held, but the SMA/VWAP cross was stale (multi-bar agreement) — the intended edge case.
3. On the frozen 5-session fixture, grant counts match the W2.5 replay's alignment-only diff
   class — no new divergence class appears.

### Stage 2 — one-param sweep (economics screen over the corpus)

```bash
make sweep INSTRUMENT=mes                                        # includes momentumMode=alignment
make sweep INSTRUMENT=mes PROFILE=profiles/momentum_alignment.json   # guardrail knobs under alignment
```

The first run adds a single `momentumMode` candidate to `out/sweep/sweep-manifest.json`
(ranked by `delta_pnl`, with per-candidate artifacts). The second sweeps adjacent knobs
(`minConfirmations`, `smaLength`, session windows) with alignment as baseline.

**Pass criterion:** the `momentumMode=alignment` candidate has `delta_expectancy ≥ 0` vs the
cross baseline on the corpus; guardrail sweeps show graceful (not cliff-edge) degradation of
the alignment delta as neighbors vary.

### Stage 3 — walk-forward A/B vs cross (the promotion gate)

```bash
make walkforward INSTRUMENT=mes PROFILE=profiles/momentum_alignment.json
make walkforward INSTRUMENT=mes PROFILE=profiles/momentum_alignment.json \
  WF_TRAIN_DAYS=30 WF_FOLDS=6          # robustness variant
```

Defaults (`Makefile`): train 20d / test 10d / 4 folds / anchor=end, weekday-session windows.
Gate configuration (`WF_GATE_FLAGS`): `--min-trades 5 --min-signal-ratio 0.25
--fold-pass-mode aggregate --fold-pass-ratio 0.75 --min-net-expectancy 1.00
--cost-rt-dollars 2.50 --pf-floor 1.2 --pf-baseline-ratio 0.95`.

**Pass criteria:**

1. All active gates pass for alignment vs the **cross baseline** on the aggregated folds:
   net expectancy ≥ $1.00/trade (after $2.50 RT cost), PF ≥ 1.2 and ≥ 0.95× baseline,
   drawdown ≤ 1.05× baseline, signal-ratio ≥ 0.25, regime balance within threshold.
2. Alignment ≥ cross on test folds — because alignment is a strict superset, trade counts can
   only rise; the gate question is whether the added trades keep expectancy above floor.
3. Gate outcomes stable across fold geometries (report both geometries).

### Stage 4 — optional LM-assisted hunt + promotion

```bash
make lm-hunt INSTRUMENT=mes PROFILE=profiles/momentum_alignment.json
make promote-run     # promoted profile into the run dir (baseline.json stays committed)
make replay          # cross-repo TA replay of the promoted profile (REPLAY_PROFILE = RUN_PROFILE)
```

`lm-hunt` post-pass guards apply unchanged (trade-retention 0.25 / min 200 trades;
`LM_HUNT_RANK_METRIC=expectancy`). Promote to the committed `baseline.json` only via
`make promote` after the full runbook passes.

---

## 6. Live opt-in decision gate

Opt `momentum_mode="alignment"` into the live checklist only when **all** hold:

1. Stage 0 green (TA suite incl. the pending repaint-fix commit set; parity unchanged).
2. Stage 1: superset invariant verified on the corpus; alignment-only grants sane.
3. Stage 2: alignment economically ≥ cross on the corpus.
4. Stage 3: all walk-forward gates pass vs the cross baseline on test folds.
5. W2.5 parity re-run green with `MomentumMode` present in `baseline.json`.
6. The ThinkScript chart implements the same mode (separate change) — otherwise the live
   config stays `cross` until it does.

## 7. Out of scope

- ThinkScript (`.tos`) port of the alignment rule; `make sync-inputs` semantics beyond the
  regression assertion in 4.4(3).
- Laguerre `NFE` / bound (`0.2`/`0.8`) sweepability (hard-coded on both sides today).
- SPY-instrument tuning (`make tune` skips parity for spy by design; alignment is MES-scoped).
- Any change to TA checklist semantics — the TA side is complete and committed
  (`c8e60a5`, `9c43e7e`).

## 8. Operational notes

- The TA repaint fix (`cli/mes.py` + 2 test files) is verified but **uncommitted**; commit it
  before any `make replay` run. If the known `/tmp` demo hazard checks out HEAD over
  `cli/mes.py`, re-apply from `/tmp/mes_fixed_BACKUP.py`.
- Ad-hoc TA-side experiments: `TRADINGAGENTS_MES_MOMENTUM_MODE=alignment`.
- Walk-forward gate reference (`mes_tuner/tuning/gates.py` / Makefile defaults): train 20d,
  test 10d, 4 folds, anchor end; `min_net_expectancy $1.00` @ `$2.50/RT`; `pf_floor 1.2`;
  `pf_baseline_ratio 0.95`; fold pass mode `aggregate`.



