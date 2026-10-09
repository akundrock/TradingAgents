# MES Process Fidelity Plan

## Decisions locked

- **Product:** MES futures (not SPY options)
- **First lever:** Process fidelity — capture more of the current edge by making the loop hard to skip
- **Path:** A (close holes), then a thin slice of B (Option D) after invalidation alerts work
- **Out of scope for this plan:** Edge retune / expectancy hunting via `/Users/akundrock/sandbox/thinkorswim-scripts` (Phase 2)
- **Automation posture:** Human stays in the loop for entries; system escalates with banners/alerts and required acknowledgements — no auto-entry

## Current baseline

From [planning inventory](./planning-inventory-and-repo-timeline.md):

- Live loop: radar → auto-check → `--alert` → standing rules → flips → trade mgmt → review
- Local `main` ~38 commits ahead of `origin/main` (copilot through momentum alignment)
- Journals show process misses (missed frame flip 9/30; no-check day 10/06) as the recent drag
- Roadmap already names Option **C** (invalidation watcher) and **D** (structured frame) in `docs/superpowers/specs/2026-09-24-mes-thesis-flip-roadmap.md`
- Engine to reuse: standing-rules / `level_retest` path in `tradingagents/mes/rules.py`, wired through `cli/mes.py` (`copilot`, `review`, `flip`, `skip`)

## Phase 0 — Push current stack, then branch

Work on the local TradingAgents checkout (`/Users/akundrock/sandbox/TradingAgents`).

1. Push current `main` (unpushed commits) to `origin/main`
2. Create and check out `cursor/mes-process-fidelity-ac07` from that tip
3. Verify `mes copilot --alert`, standing rules, `mes flip`, and review → `standing_rules.json` still run end-to-end
4. Land in-repo plan at `docs/superpowers/plans/2026-10-09-mes-process-fidelity.md`

## Phase 1 — Standing-rules hygiene

Goal: rules never silently expire into a "no process" day.

- Session start / copilot boot: if `standing_rules.json` is missing or past `expires`, surface a loud **RULES EXPIRED / NONE** state (alert when `--alert`)
- After `mes review`, require either new machine-checkable rules **or** an explicit "no rules / blank day" ack before the next session is considered ready
- Keep existing `level_retest` behavior; grade compliance in the next review as today
- Tests: expired/missing rules → banner/alert; post-review without rules or ack → not ready

## Phase 2 — Option C: invalidation watcher

Goal: frame kills cannot be ignored (addresses 9/30-style miss).

- Add trigger kind `invalidation` to the standing-rules engine (reuse evaluation loop from `level_retest`)
- On fire: **RE-READ REQUIRED** banner + `--alert`; block "quiet copilot" until `mes flip` **or** explicit no-flip reason (extend `mes skip` / flip ack pattern — pick one existing command path and stick to it)
- Seed invalidation clauses from review / morning frame where available; allow manual rule entries consistent with current JSON shape
- Align with `docs/superpowers/specs/2026-09-24-mes-thesis-flip-roadmap.md` Option C
- Tests: invalidation fire → banner; ack via flip/skip clears; no silent continue

## Phase 3 — Thin Option D: structured morning frame

Goal: give Option C and standing rules a machine-readable day thesis without building the full B "enforced skeleton."

- Structured fields on morning hypothesis / journal: at least `day_type`, `bias`, and a small list of machine clauses (invalidation + key levels)
- Copilot/render shows Frame columns from this structure (location/frame work already partially shipped)
- Review grades against the structured frame (flip timeliness, clause hits) using existing review agent hooks
- **Not in this thin slice:** hard "must log 3 checks or halt" gates, auto-flip, or quiet-day lockdowns (full B)

## Phase 4 — Phase-2 backlog only (document, do not build here)

Capture in Magpie Context for later:

- MES edge retune + measurement using `/Users/akundrock/sandbox/thinkorswim-scripts` (LLM hunting tool + original strategy docs)
- mes-tuner `MomentumMode` port / live `alignment` opt-in
- Full process scorecard (approach C) if KPIs are still weak after C/D

## Implementation home

- Repo: `akundrock/TradingAgents` on magpie-local
- Primary touch points: `tradingagents/mes/rules.py`, `journal.py`, `radar`/copilot path, `cli/mes.py`, review/gatekeeper agents, thesis-flip roadmap/spec

## Success criteria

- Expired/missing standing rules are impossible to miss at session start
- Invalidation events force flip or explicit no-flip before the loop goes quiet
- Morning frame is structured enough that review and rules consume it
- No auto-entries; no Phase-2 edge work in this delivery
