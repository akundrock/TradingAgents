# MES Trade Management — Design

**Date:** 2026-09-04
**Branch:** `mes-trade-management`
**Status:** Approved design, ready for implementation planning

---

## Problem

The MES copilot answers "should I take this trade?" well: `premarket` sets a
hypothesis, `check` rules on go/no-go with a deterministic checklist plus LLM
gatekeeper, `radar` shows proximity to a valid entry. But once a trade is on,
the copilot has nothing to say. The user manages stops, partials, and exits
by hand with no tool support, and `mes review` grades only the morning
hypothesis — not execution.

## Goal

A post-entry trade management layer that mirrors the existing architecture:
a pure, deterministic ladder module decides every hard level move; a light LLM
manager adds advisory commentary it can never turn into level changes. The
trade lifecycle (open → adjust → close) lives in the existing append-only
session journal so review can grade execution and future sessions can replay
decisions.

## Non-Goals (v1)

- Auto-detecting Schwab fills or placing orders (the user declares fills;
  execution stays in TOS).
- Multiple open trades per day (one open trade at a time).
- Tuner backtesting of ladder parameters (config fields are staged for it;
  the harness is a future session).
- Auto-reversing when the checklist flips side.

---

## Architecture

A new pure module `tradingagents/mes/management.py`, following the
`checklist.py` / `radar.py` pattern (no LLM, no I/O, fully replayable):

```python
@dataclass
class OpenTrade:
    side: str                # "long" | "short"
    contracts: int           # opened contracts
    remaining: int           # not yet closed by partials
    entry: float             # fill price
    stop: float              # current stop (moves as ladder fires)
    initial_stop: float
    target: float | None
    entry_time: datetime
    initial_risk_points: float   # |entry - stop| -> defines 1R
    fired: dict[str, str]        # event name -> as_of it fired
    manual_events: list[str]     # freeform notes from the user

@dataclass
class MgmtEvent:
    name: str          # e.g. "breakeven", "partial", "trail", "time_stop"
    as_of: datetime
    detail: str        # e.g. "stop 6478.25 -> 6483.50 (BE)"

@dataclass
class MgmtReport:
    r_now: float                      # current open profit in R
    stop: float                       # current (possibly adjusted) stop
    target: float | None
    events_fired: list[MgmtEvent]     # this-bar transitions
    next_event: str                   # what the ladder is waiting for
    recommendation: str               # HOLD / TIGHTEN / EXIT / FLATTEN
    reasons: list[str]
```

**Core function:** `evaluate_management(snapshot, result, trade, cfg) -> MgmtReport`

**Core function:** `evaluate_management(snapshot, result, trade, cfg) -> MgmtReport`
— same shape as `build_proximity`. Reads the live snapshot, returns the ladder
state. Stops only ever move in the protective direction (tighten, never
loosen); this is enforced in code, not convention.

**LLM layer (hybrid):** a lightweight trade manager agent, invoked only from
`status`, receives the mechanical report plus market context and returns
advisory commentary (e.g. "internals flipped, consider tightening"). It never
alters levels and its output is never parsed into state. Deterministic rules
own the plan, mirroring the gatekeeper/checklist split.

### Position state lifecycle

Journal record kinds appended to the existing per-session JSONL:

- `trade_opened` — side, contracts, entry, stop, target, R, plus entry context
  (score, tier, confirmations, SPY confluence, tier band).
- `trade_adjusted` — stop moved / partial noted / LLM comment / manual note.
- `trade_closed` — exit price, reason (`stop` / `target` / `manual` /
  `eod_flatten`), realized R, MFE/MAE.

Position state is reconstructed by replaying the day's JSONL
(`load_open_trade(date)`), so `status` after a terminal restart works, and
review/backtests can replay every decision.

### Config additions (all `TRADINGAGENTS_MES_*`-overridable)

| Field | Default | Meaning |
|---|---|---|
| `breakeven_at_r` | 1.0 | open profit (R) that moves stop to breakeven |
| `breakeven_cushion_ticks` | 1 | ticks past entry the BE stop lands (covers fees) |
| `partial_at_r` | 1.5 | R at which the partial fires |
| `partial_fraction` | 0.5 | fraction closed at the partial trigger |
| `trail_atr_multiple` | 1.0 | ATR multiple for the post-partial trail |
| `exit_on_confluence_loss` | false | make confluence-flip exit a hard rule |
| `time_stop_buffer_minutes` | 10 | flatten this many minutes before `exit_time` |

Config aliases follow the existing `MesChecklistConfig` pattern so the tuner
can eventually backtest them.

---

## Command Surface

### `mes trade enter`

```
tradingagents mes trade enter --side long --contracts 1 \
    --entry 6482.25 --stop 6451.25 --target 6520.00
```

- Stop defaults from `suggest_stop_points` (VWAP/ATR structural distance);
  target from `suggest_trade_levels_from_snapshot`; each overridable by flag.
- Records entry context (score, tier, confirmations, SPY confluence, tier band)
  in the `trade_opened` record so review can grade tier vs. outcome.
- Refuses a second open trade for the date (`--force` escape hatch).
- Supports `--as-of` / CSV replay for practicing on recorded sessions.

### `mes trade status` — watch loop

Radar-style Rich Live panel, `--watch 15` default, `--no-watch` one-shot,
`--json` for scripting:

- Header: side, contracts, entry fill and time; current price, open points, R.
- Plan block: current stop (labeled `initial`/`BE`/`trailed`), target.
- `NEXT →` the next ladder trigger and its R distance.
- Fired events list.
- `[HOLD|TIGHTEN|EXIT|FLATTEN]` recommendation with reasons.
- Optional one-paragraph LLM advisory under the mechanical report
  (`--no-llm` skips; output is commentary only, never parsed).
- `--alert` bell on `EXIT`/`FLATTEN` or any fired ladder event.

Ladder triggers evaluate **on bar close** (5m), matching the checklist's
bar-based worldview and keeping CSV replay faithful.

### `mes trade close --price X --reason manual|stop|target|eod`

Records `trade_closed` with realized R, MFE/MAE, and a summary line. If the
user forgets, `review` flags "position still open" and treats EOD flatten as
the exit for grading.

## The Ladder (default config)

| Trigger (on bar close) | Action | 1-contract behavior |
|---|---|---|
| +1.0R open profit | Stop → breakeven: entry ± `breakeven_cushion_ticks` × tick size in the profit direction (1 tick covers fees) | fires normally |
| +1.5R | Take 50% off; **1 contract**: tighten stop to lock ~half the open gain | degrades cleanly |
| After partial | Trail remainder 1.0×ATR behind highest close (long) / lowest (short) since entry | same |
| `exit_time` − 10 min | FLATTEN (time stop) regardless of P&L | always on |

- `R` = `|entry − initial_stop|` in points, fixed at entry.
- **Stops only tighten, never loosen** — enforced in `evaluate_management`;
  every move is journaled as `trade_adjusted`.
- **Gaps through a level:** the event fills at the *open price*, not the level
  price, so replay accounting reflects slippage reality.
- **Confluence flip against the position** (checklist flips side, `tradeable`
  goes False for the side, SPY confluence < 3/5): mechanical report raises
  `recommendation: EXIT` with the reason and alerts. Advisory in v1;
  `exit_on_confluence_loss=false` config flag can harden it later.
- **Single-contract degradation:** with 1 contract the partial event degrades
  to *tighten stop to lock ~half the open gain* instead of selling a
  nonexistent fraction.

### Edge cases

- **No trade open** → `status` says so and suggests `mes trade enter`.
- **Stale open position from a prior date** → `enter` refused; `review` surfaces it.
- **Missing internals / data gaps** → price-only rules keep running
  (the R ladder needs only bars); internals advisories degrade to
  "unavailable" like snapshot warnings.
- **Side mismatch** (checklist flips against the open trade) → feeds the
  advisory and EXIT recommendation; never auto-reverses.

### Review integration

`mes review` gains a "Trades" section: per closed trade — entry context
(tier/score), realized R, MFE/MAE, fired ladder events vs. plan, and adherence
flags (manual stop move, held past time stop). The review agent prompt receives
this so the session grade covers execution, not just the hypothesis.

---

## Error Handling

| Situation | Behavior |
|---|---|
| LLM manager fails mid-watch | Caught, rendered dim "manager unavailable"; mechanical ladder continues (same pattern as gatekeeper failure in `check`). |
| Malformed journal line | Skipped with a warning by `MesJournal.load_day`; replay uses last known good state. |
| Journal write failure | Logged warning, loop continues. |
| Duplicate events | `fired` dict is idempotent; re-running `status` cannot double-fire. |
| Data gaps | Ladder evaluates on the latest bar; MFE/MAE skip gaps; warning renders in panel. |

## Testing

- **`tests/test_mes_management.py`** — pure-function tests: every ladder trigger
  fires exactly once; stop never loosens; single-contract partial degradation;
  gap-through-stop accounting; time stop; EXIT advisory on confluence flip.
  Synthetic bars, no network.
- **Journal round-trips** — `trade_opened → adjusted × N → closed` replayed
  through `load_open_trade` reconstructs identical state; malformed lines
  skipped; open trade survives a "restart" (new process, same journal).
- **CLI tests** — extend `tests/test_mes_agents.py`: `enter` validation
  (duplicate open, missing side), `close` recording, `status --no-watch --json`
  shape, `--no-llm` path. CSV-replay end-to-end: enter on a recorded session,
  step `--as-of` through bars, verify ladder fires at correct R marks and
  open-price fills on gaps.
- **LLM manager** — tested as a formatting/context function with a stub LLM,
  same as gatekeeper tests; output is advisory text, never parsed into state.

## Out of Scope (v1)

See [Non-Goals](#non-goals-v1) — identical list, repeated here only because
the earlier design discussion scoped it separately.

- Auto-detected Schwab fills / order placement
- Multiple open trades per day
- Tuner backtesting of ladder parameters (config fields staged for it)
- Auto-reversing when the checklist flips side
