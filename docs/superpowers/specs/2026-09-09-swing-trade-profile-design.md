# Design: Swing Trade Profile for the Pro-Trader-Dashboard Post-Gate LLM Chain

- **Date:** 2026-09-09
- **Status:** Approved design, ready for implementation planning
- **Scope:** Screener mode (`tradingagents intraday --screener`), strategy `pro_trader_dashboard`, the LLM chain that runs after both gates pass

## 1. Problem

In screener mode, when both gates pass (Gate 1 = pro-trader-dashboard rule
checklist, Gate 2 = daily-bias or SuperTrend confirmation), the LLM chain
`Trader → Aggressive/Neutral/Conservative Analyst → Portfolio Manager`
(`tradingagents/graph/intraday_graph.py`) runs with a **generic** Trader prompt
(`tradingagents/agents/trader/trader.py`). None of the prompts know the user's
actual trade style:

- Swing trades expressed with **long calls/puts ~3–4 weeks out (DTE)**,
- at **~0.70 delta** (slightly in-the-money),
- held **~1 trading day** (occasionally 2).

The prompt vocabulary is day-trade shaped, so LLM reasoning and outputs
(entry/stop/thesis) don't align with how these trades are actually taken.

## 2. Goal

Inject a shared **swing trade profile** into every post-gate LLM agent so the
whole chain reasons in swing-option terms, and make the trade parameters
(DTE, delta, hold horizon, option structure) first-class structured output that
is persisted with each signal.

Non-goals:

- No real options-chain data is fetched; option structure is reasoned about
  abstractly (parameters only).
- No graph-topology changes; no new LLM nodes or calls.
- No per-strategy prompt registry; profile applies only to
  `pro_trader_dashboard`.
- No dashboard UI changes.

## 3. Architecture

### New module: `tradingagents/intraday/trade_profile.py`

A frozen dataclass + pure renderer — single source of truth for swing vocabulary:

```python
@dataclass(frozen=True)
class TradeProfile:
    style: str = "swing"
    dte_weeks: str = "3-4"
    target_delta: str = "0.70"
    hold_horizon_days: str = "1"
    option_structure: str = (
        "Long calls (long) / long puts (short), ~3-4 weeks to expiration, "
        "~0.70 delta (slightly ITM)"
    )

def build_trade_profile(...) -> TradeProfile   # env overrides applied here
def render_trade_profile(profile, symbol, direction) -> str  # markdown block
```

`build_trade_profile()` reads the three env tunables with dataclass defaults as
fallback. Renderer output is the markdown block shown in Section 6, which all
four agents render from, so reasoning vocabulary stays consistent across the
chain.

### Activation rule

`propagate_intraday()` attaches `intraday_context["trade_profile"]` **only when
`strategy_name == "pro_trader_dashboard"`**. `orb_breakout` and
`base_momentum` chains are byte-identical to today's behavior. The kill-switch
env var (Section 4.5) also prevents attachment.

## 4. Components

### 4.1 `TradeProfile` dataclass + renderer (`tradingagents/intraday/trade_profile.py`)

As described in Section 3 (Architecture). The renderer takes symbol + direction so the profile
block can be phrased per-direction ("long calls" vs "long puts").

### 4.2 `propagate_intraday` injection

`tradingagents/graph/intraday_graph.py` — in `propagate_intraday`, build
`trade_profile` only when strategy is pro-trader and kill switch is off, and
place it inside `intraday_context` next to the existing `strategy` block.

### 4.2 Trader: structured output

`tradingagents/agents/schemas.py` — add:

```python
class SwingTradeProposal(TraderProposal):
    option_structure: str | None = None       # e.g. "Long call, ~0.70 delta, 21 DTE"
    hold_horizon_days: str | None = None      # e.g. "1" (typically closed same or next day)
    option_direction: str | None = None       # "long call" / "long put"
```

All fields optional/`None`-defaulted; the repo's `invoke_structured_or_freetext`
freetext fallback covers malformed output, and regex extraction
(`**Entry Price**` / `**Stop Loss**`) remains the final fallback path — today's
safety net, preserved.

**Trader node change** (`agents/trader/trader.py`): when
`intraday_context.trade_profile` is present, bind `SwingTradeProposal` instead
of `TraderProposal`, add a one-line system instruction ("You are evaluating a
swing option trade: …"), and append the rendered profile to the user prompt.
When absent, bind plain `TraderProposal` — `orb_breakout`/`base_momentum`
behavior unchanged.

### 4.3 Risk debators + Portfolio Manager

Each of the three debators (`agents/risk_mgmt/*_debator.py`) and the PM
(`agents/managers/portfolio_manager.py`) get the same rendered profile block
appended to their user prompt with a one-line role-specific instruction:

- Risk debators: "Evaluate risk for a swing options position with {dte_weeks}
  DTE, ~{target_delta} delta, expected hold ~1 day — focus on overnight gap
  risk, theta, and event risk within the hold window."
- PM keeps its final-decision output format unchanged; profile block informs
  reasoning only.

### 4.4 Signal persistence

`IntradaySignal` (`tradingagents/intraday/session.py`) gains two optional
fields: `option_structure: str | None = None` and
`hold_horizon_days: str | None = None`.

`signals.csv` (`scanner.py:863` fieldnames) gains columns `option_structure`
and `hold_horizon_days` appended to the fieldname list; append-mode
`DictWriter` keeps old files readable (old rows simply lack the columns).
`_state_to_intraday_signal` maps the structured fields → signal fields. The
`reasoning` column continues to carry the full PM/trader text.

### 4.5 Config / kill switch

- `TRADINGAGENTS_PRO_TRADER_SWING_PROFILE` (default `"on"`): when `"off"`, the
  profile is never attached and the pro-trader chain behaves exactly as today.
- Tunables (defaults baked into `TradeProfile`; env only overrides):
  - `TRADINGAGENTS_PRO_TRADER_DTE_WEEKS` (default `3-4`)
  - `TRADINGAGENTS_PRO_TRADER_TARGET_DELTA` (default `0.70`)
  - `TRADINGAGENTS_PRO_TRADER_HOLD_HORIZON_DAYS` (default `1`)

Wired through `default_config.py` + the existing env map, with tests extended
in `tests/test_env_overrides.py`.

## 5. Data flow (end to end)

1. Screener: universe → RRS pre-filter → per-symbol Gate 1 (pro-trader rules)
   → Gate 2 (daily-bias/SuperTrend) — unchanged.
2. Both gates pass → `propagate_intraday()` builds `intraday_context`,
   attaches `trade_profile` (pro-trader only, kill switch respected).
3. Trader runs with swing-aware system line + profile block; structured
   `SwingTradeProposal` output.
4. Risk debators and PM receive the same profile block; reasoning consistent.
5. `_state_to_intraday_signal()` reads structured fields first; regex on
   freetext remains fallback. Entry/stop remain **underlying** levels (the
   human executor maps to the ~0.70-delta contract).
6. `IntradaySignal` + new fields → `signals.csv` (new columns) + dashboard.

## 6. Rendered profile block (prompt text)

All four agents receive this block (Trader additionally gets the system-line
role instruction):

> **Trade Profile: Swing Options** (pro_trader_dashboard)
> - Style: swing trade expressed with long options
> - Structure: long calls (long setups) / long puts (short setups), ~3–4
>   weeks to expiration, ~0.70 delta (slightly ITM)
> - Hold horizon: typically ~1 trading day (rarely 2); closes within days,
>   not weeks
> - Entry/stop/targets are **underlying-equity levels**; the executor maps the
>   underlying move to the ~0.70-delta contract.
> - Invalidation = stop on the underlying, not option premium.
> - Time matters: a setup that needs "wait a week" is a **HOLD**. A valid
>   swing setup must act within ~1 day.

Role-specific lines:

- Trader system line: "You are evaluating a swing option trade: reason about
  the underlying's move, then express entry/stop/targets on the underlying."
- Risk debators: overnight gap risk, theta within the short hold, event risk
  in the hold window.
- PM: weigh the swing thesis against the risk debate for a final decision,
  same output format as today.

## 7. Error handling

- Missing env vars → dataclass defaults apply.
- Malformed LLM output → existing `invoke_structured_or_freetext` fallback;
  regex extraction remains final fallback.
- Missing `trade_profile` key → agents behave exactly as today.
- Kill switch `off` → profile never attached; chain identical to current
  behavior.

## 8. Testing

- Unit: `TradeProfile` defaults, renderer content (DTE/delta/hold text,
  direction phrasing), env overrides (`tests/test_env_overrides.py`).
- Unit: `SwingTradeProposal` parsing — valid JSON populates fields; malformed
  output falls back to freetext + regex extraction.
- Integration: `propagate_intraday` with pro-trader → `trade_profile` present;
  with `orb_breakout` → absent; kill switch off → absent.
- Integration: `_state_to_intraday_signal` maps structured fields →
  `IntradaySignal` fields; `signals.csv` rows include the new columns.
- TDD per repo convention; run baseline suite (~1174 tests) before and after.

## 9. Explicit decisions (from brainstorming)

- **Approach 1 chosen:** enrich existing chain (Trader + risk + PM), no new
  LLM calls, no topology change.
- Profile hardcoded to `pro_trader_dashboard` (not generic config layer).
- Structured output schema for the Trader; regex preserved as fallback.
- Risk debators + PM get the swing context too (whole chain consistent).
- No real options-chain data fetched; option structure reasoned abstractly.
- Persistence extended: new CSV columns; existing behavior otherwise
  unchanged.

## 10. Out of scope

- Fetching option chains/IV; option-price-level outputs.
- Per-strategy prompt registry or prompt templating system.
- Changes to gating, RRS screener, universe selection, or the lazy-bias path.
- Dashboard UI changes beyond what flows through existing signal fields.
