# MES Thesis Flips — Roadmap: Machine-Detected Invalidation (Option C) and the Structured Frame (Option D)

**Status:** design note / roadmap (no implementation tasks yet).
**Shipped:** Option B — manual flip journaling (`feat/mes-thesis-flip`, commit `ab29d07`).
**Related:** `plans/2026-09-23-mes-standing-rules.md` (trigger engine Option C reuses), `plans/2026-09-17-radar-auto-check.md`, `plans/2026-09-22-mes-copilot.md`.

## Where we are: Option B shipped (manual flip journaling)

The original options ladder, cheapest → most structural:

- **A. Review-side only** — enrich `summarize_checks` with price-vs-VWAP/OR context and teach the review prompt to grade a demonstrably-fired-but-unjournaled invalidation as a discipline failure. Zero new machinery. *Not yet done; partially absorbed by B's prompt (see below).*
- **B. Manual flip journal command** — *shipped.* A `flip` journal kind written via `mes flip`; no detection at all. You decide when the frame died; the copilot makes writing it down cheap and explicit.
- **C. Machine-detected invalidation watcher** — the next step; design below.
- **D. Structured frame schema** — the enabler that makes C fully machine-checked; design below.

### What Option B actually built (the machinery C will reuse)

- **Record:** `MesJournal.append_flip` (`tradingagents/mes/journal.py:195`) appends `{kind: "flip", reason, new_frame, price, vwap, as_of}`. `reason` is the invalidation evidence; `new_frame` may be empty (flip to "no frame — stand down").
- **Frame resolution:** `MesJournal.active_frame` (`journal.py:332`) — the *last* frame-defining record wins in append order. A journaled `flip` supersedes the morning `hypothesis`; a mid-session hypothesis re-save supersedes the flip.
- **Consumption:** every check now grades against the live frame — `mes check` and both auto-check paths resolve the hypothesis via `journal.active_frame` (`cli/mes.py:471` inside `_run_check_once`; flat-path auto-check at `cli/mes.py:1011`). The radar path pre-reads it for the tick (`cli/mes.py:861`).
- **Review grading:** `mes review` grades the morning hypothesis via `load_hypothesis` (unchanged, by design — it grades the morning read) and surfaces flips separately: `summarize_flips` (`journal.py:433`) renders the "Intraday Thesis Flips" table into the review prompt (`tradingagents/agents/mes/review_agent.py:53-58`). Prompt item 3 (`review_agent.py:34`) grades both directions: a flip journaled but declared late, and checks that kept arguing the dead frame after a flip.

**The convention B creates and the review can grade:** the very next logged check after visible invalidation must be either a `flip` or a check consistent with the flipped frame. That is a human discipline the journal now makes visible and gradeable.

**Day-to-day usage:** when you conclude the morning frame is dead, run `mes flip --reason "<what killed it>" [--frame "<new day-type/bias thesis>"]` *before* your next check. Empty `--frame` = stand down; subsequent auto-checks will still run but grade against "no frame". `mes review` then grades timeliness and post-flip coherence.

## Option C: machine-detected invalidation watcher

Invalidation clauses in a good morning read are chart-watchable — the same shape as standing rules ("two consecutive 1m closes above VWAP + 15 min above OR high", "above prior VAL + SPY through VWAP"). An invalidation is a **second class of trigger whose required action is a re-read instead of a check**.

**Reuse the standing-rules machinery, don't rebuild it:**

- `rules.py:89` silently skips unknown trigger kinds — the schema already admits future kinds. Add `kind: "invalidation"` beside `level_retest` in `tradingagents/mes/rules.py`.
- Level vocabulary already exists: `_LEVEL_RESOLVERS` (`tradingagents/mes/rules.py:39`) resolves VWAP, OR top/bottom, PDH/PDL, prior close/VAH/VAL/POC, overnight high/low. Clause patterns like "two consecutive closes above VWAP" or "15 min above OR high" compose from these resolvers plus persistence predicates on the 1m bars the snapshot already carries.
- Fire → resolution accounting is built: `journal.rule_compliance` (`journal.py:351`) tallies checked / skipped / missed / open via `_resolve_fires`. An invalidation fire resolves when a `flip` record lands after the fire's timestamp — a mirror of `_resolve_fires` matching fires to checks/skips.

**Enforcement modes:**

1. **Banner only (do this first).** A persistent "RE-READ REQUIRED" panel row mirroring "LOG A CHECK NOW" (`cli/mes.py:997-1001`), shown until a `flip` lands in the journal for that clause. No check gating.
2. **Downgrade auto-checks (opt-in later).** While an invalidation fire is open, `should_auto_check` (`tradingagents/mes/radar.py:289`) or the flat-path twin (`cli/mes.py:1003-1011`) suppresses or downgrades further auto-checks — the machine stops grading against a frame it has evidence is dead. Manual `mes check` stays available.

**Arming:** review-prescribed first. The same `standing_rules.json` pipeline (review prescribes → per-tick evaluation) carries invalidation clauses; each clause needs a stable id (`inv_1`, …) as its per-session dedup key, mirroring `rule_id`. Only after a few sessions of trust in the clause definitions should clauses be machine-derived — which is Option D.

## Option D: make the frame structured, not prose

The real prerequisite for C being *machine-checked* rather than human-prescribed. Today the morning read is prose only: `save_hypothesis` (`journal.py:177`) stores `hypothesis_markdown` as a single string; there is no machine-readable `day_type`/`bias` field, so there is nothing to "flip" mechanically — a flip's `new_frame` is equally prose.

**Schema change:** add structured fields to the morning hypothesis record — `day_type`, `bias`, and chart-watchable invalidation clauses (level vocabulary from `_LEVEL_RESOLVERS` plus bar-condition parameters: consecutive 1m closes, dwell minutes, second-symbol confirmation). The flip then carries structured `from`/`to` day-type/bias, making it a **typed state transition** with the machine-checked clause as its cause, and `active_frame` returns a typed frame instead of prose the check prompt re-parses.

**Payoffs beyond enabling C:**

- `hypothesis_grade` sharpens — review currently grades day-type/bias/invalidation from prose; with typed fields it grades structured values against outcomes.
- Copilot/gatekeeper prompts get a typed frame as context, no re-parsing.
- Invalidation clauses arm themselves from the morning read (C's watcher becomes automatic), closing the loop: morning schema → watcher → flip → review grade → next morning's rules.

**Sequencing:** D can land incrementally *after* C — review-prescribed clauses (structured dicts, prose clause text) work while the morning read is still prose. D is required only when clauses should be machine-derived from the morning read itself.

## Agreed sequencing

1. ~~Option B: manual flip journal~~ — **shipped** (`ab29d07`).
2. **A-as-add-on:** enrich `summarize_checks` rows with each check's price-vs-VWAP/OR context so the review's missed-flip grading is systematic, not luck-of-the-narrative. Cheap; `journal.summarize_checks` + review prompt only. **Shipped:** `append_check` stores `orb_high`/`orb_low` at write time (legacy records render `-`), rows carry a compact `Location` cell (`+2.50 vs VWAP · above OR-H`) and a `Frame` column (`morning` / `flip N` from the append-order frame walk), and the review prompt explains both columns — this is the evidence trail C's trust period grades against.
3. **C:** `invalidation` trigger kind + "RE-READ REQUIRED" banner (enforcement mode 1). Run review-prescribed for a few sessions before any auto-check gating (mode 2).
4. **D:** structured morning schema once the clause vocabulary has stabilized from C sessions.

## Adjacent debts this work touches (agreed backlog, unaddressed)

- **Re-arm-after-exit dedup for `rule_fired`** — per-session dedup means a level that re-arms after price exits and re-enters its band never re-fires; invalidation clauses inherit the question (does an invalidation that "un-fires" re-arm?). Decide before C.
- **`expires_on` model-invented-date hazard** (`cli/mes.py`) — reviews sometimes invent dates; review-armed invalidation clauses inherit this. Constrain to explicit dates or "today" at C time.
- **Optional `tool_choice` for the `openai_compatible` client** — unrelated to C/D; listed to keep the backlog in one place.

