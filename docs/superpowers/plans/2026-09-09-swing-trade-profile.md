# Swing Trade Profile for Pro-Trader Post-Gate LLM Chain — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inject a swing-options trade profile (DTE ~3–4 weeks, delta ~0.70, ~1-day hold) into the post-gate LLM chain (Trader → Risk Debators → PM) when the screener runs `pro_trader_dashboard`, and persist option-structure output on each signal.

**Architecture:** A frozen `TradeProfile` dataclass + pure markdown renderer (`tradingagents/intraday/trade_profile.py`) is attached to `intraday_context` by `propagate_intraday` (pro-trader only, kill-switch respected). All four post-gate LLM agents append the rendered block to their prompts; the Trader additionally binds a `SwingTradeProposal` schema (subclass of `TraderProposal`). Structured fields flow into `IntradaySignal` and two new `signals.csv` columns. No new LLM calls, no graph topology changes.

**Tech Stack:** Python 3.12, Pydantic (`BaseModel`), LangGraph, pytest, `csv.DictWriter`.

**Spec:** `docs/superpowers/specs/2026-09-09-swing-trade-profile-design.md`

## Global Constraints

- No graph-topology changes; no new LLM nodes or calls.
- No real options-chain data fetched; option structure is reasoned about abstractly (parameters only).
- Profile attaches **only** when `strategy_name == "pro_trader_dashboard"`; `orb_breakout` and `base_momentum` chains are byte-identical to today's behavior.
- Kill switch `TRADINGAGENTS_PRO_TRADER_SWING_PROFILE=off` (default `on`) restores today's behavior for pro-trader too.
- Entry/stop remain **underlying-equity levels**; option structure is reasoned abstractly (no chain data).
- Non-swing runs bind plain `TraderProposal`; `SwingTradeProposal` binds only when the profile block is present in `intraday_context`.
- Regex fallback (`**Entry Price**` / `**Stop Loss**`) preserved as final extraction path; swing fields use the same markdown-extraction pattern.
- TDD per repo convention; baseline suite must pass before and after (run `pytest tests/ -q` at start to record the baseline count).
- ⚠️ The user has unrelated uncommitted changes in `.env.example`, `tradingagents/default_config.py`, `tests/test_env_overrides.py`. In tasks touching those files, stage only your own hunks (`git add -p` or review `git diff --cached` before committing). Never revert the unrelated hunks.

---

### Task 1: `TradeProfile` dataclass, builder (with kill switch), and renderer

**Files:**
- Create: `tradingagents/intraday/trade_profile.py`
- Test: `tests/test_trade_profile.py`

**Interfaces:**
- Consumes: nothing (leaf module; stdlib only).
- Produces (used by Tasks 2, 3, 5, 6):
  - `@dataclass(frozen=True) class TradeProfile` with fields `style: str = "swing"`, `dte_weeks: str = "3-4"`, `target_delta: str = "0.70"`, `hold_horizon_days: str = "1"`, `option_structure: str = "long calls (long setups) / long puts (short setups), ~3-4 weeks to expiration, ~0.70 delta (slightly ITM)"`.
  - `build_trade_profile(config: dict | None = None) -> TradeProfile | None` — returns `None` when `config.get("pro_trader_swing_profile_enabled", True)` is falsy (kill switch), else a `TradeProfile` with env-derived overrides applied.
  - `render_trade_profile(profile: TradeProfile, symbol: str, direction: str) -> str` — markdown block; direction `"short"` phrases "long puts", anything else phrases "long calls".
  - Config keys read: `pro_trader_swing_profile_enabled`, `pro_trader_swing_dte_weeks`, `pro_trader_swing_target_delta`, `pro_trader_swing_hold_horizon_days`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trade_profile.py
from __future__ import annotations

import pytest

from tradingagents.intraday.trade_profile import (
    TradeProfile,
    build_trade_profile,
    render_trade_profile,
)


@pytest.mark.unit
def test_trade_profile_defaults_are_swing_options():
    profile = TradeProfile()
    assert profile.style == "swing"
    assert profile.dte_weeks == "3-4"
    assert profile.target_delta == "0.70"
    assert profile.hold_horizon_days == "1"


@pytest.mark.unit
def test_build_trade_profile_uses_defaults_without_config():
    profile = build_trade_profile(None)
    assert profile is not None
    assert profile.dte_weeks == "3-4"
    assert profile.target_delta == "0.70"
    assert profile.hold_horizon_days == "1"


@pytest.mark.unit
def test_build_trade_profile_applies_config_overrides():
    config = {
        "pro_trader_swing_dte_weeks": "5-6",
        "pro_trader_swing_target_delta": "0.65",
        "pro_trader_swing_hold_horizon_days": "2",
    }
    profile = build_trade_profile(config)
    assert profile.dte_weeks == "5-6"
    assert profile.target_delta == "0.65"
    assert profile.hold_horizon_days == "2"


@pytest.mark.unit
def test_build_trade_profile_returns_none_when_kill_switch_off():
    assert build_trade_profile({"pro_trader_swing_profile_enabled": False}) is None
    assert build_trade_profile({"pro_trader_swing_profile_enabled": True}) is not None
    assert build_trade_profile({}) is not None
    assert build_trade_profile(None) is not None


@pytest.mark.unit
def test_render_trade_profile_contains_swing_vocabulary():
    profile = TradeProfile()
    text = render_trade_profile(profile, symbol="NVDA", direction="long")
    assert "Trade Profile" in text
    assert "3-4" in text
    assert "0.70" in text
    assert "trading day" in text
    assert "underlying" in text.lower()
    assert "HOLD" in text


@pytest.mark.unit
def test_render_trade_profile_direction_phrasing():
    profile = TradeProfile()
    long_text = render_trade_profile(profile, symbol="NVDA", direction="long")
    short_text = render_trade_profile(profile, symbol="TSLA", direction="short")
    assert "long calls" in long_text
    assert "long puts" in short_text
    assert "NVDA" in long_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trade_profile.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tradingagents.intraday.trade_profile'`

- [ ] **Step 3: Write minimal implementation**

```python
# tradingagents/intraday/trade_profile.py
"""Swing trade profile: shared swing-option vocabulary for the post-gate LLM chain.

Single source of truth for the pro-trader-dashboard swing style. ``propagate_intraday``
attaches the profile to ``intraday_context`` and every post-gate agent renders it
into its prompt so Trader, risk debators, and PM reason in the same vocabulary.

See docs/superpowers/specs/2026-09-09-swing-trade-profile-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradeProfile:
    style: str = "swing"
    dte_weeks: str = "3-4"
    target_delta: str = "0.70"
    hold_horizon_days: str = "1"
    option_structure: str = (
        "long calls (long setups) / long puts (short setups), "
        "~3-4 weeks to expiration, ~0.70 delta (slightly ITM)"
    )


def build_trade_profile(config: dict | None = None) -> TradeProfile | None:
    """Build the swing TradeProfile, or None when the kill switch is off.

    Config keys (pre-coerced from env by default_config):
    - ``pro_trader_swing_profile_enabled`` (default True; False = kill switch)
    - ``pro_trader_swing_dte_weeks`` (default "3-4")
    - ``pro_trader_swing_target_delta`` (default "0.70")
    - ``pro_trader_swing_hold_horizon_days`` (default "1")
    """
    config = config or {}
    if not config.get("pro_trader_swing_profile_enabled", True):
        return None
    return TradeProfile(
        dte_weeks=str(config.get("pro_trader_swing_dte_weeks") or "3-4"),
        target_delta=str(config.get("pro_trader_swing_target_delta") or "0.70"),
        hold_horizon_days=str(config.get("pro_trader_swing_hold_horizon_days") or "1"),
    )


def render_trade_profile(profile: TradeProfile, symbol: str, direction: str) -> str:
    """Render the trade profile as a markdown block for agent prompts."""
    if direction == "short":
        structure = (
            f"long puts, ~{profile.dte_weeks} weeks to expiration, "
            f"~{profile.target_delta} delta (slightly ITM)"
        )
    else:
        structure = (
            f"long calls, ~{profile.dte_weeks} weeks to expiration, "
            f"~{profile.target_delta} delta (slightly ITM)"
        )
    return (
        "**Trade Profile: Swing Options**\n"
        f"- Symbol: {symbol}\n"
        f"- Style: {profile.style} trade expressed with long options\n"
        f"- Structure: {structure}\n"
        f"- Hold horizon: typically ~{profile.hold_horizon_days} trading day(s); "
        "closes within days, not weeks\n"
        "- Entry/stop/targets are **underlying-equity levels**; the executor maps "
        f"the underlying move to the ~{profile.target_delta}-delta contract.\n"
        "- Invalidation = stop on the underlying, not option premium.\n"
        '- Time matters: a setup that needs "wait a week" is a **HOLD**. '
        "A valid swing setup must act within ~1 day."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trade_profile.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add tradingagents/intraday/trade_profile.py tests/test_trade_profile.py
git commit -m "feat: add swing TradeProfile dataclass, builder, and prompt renderer"
```

---

### Task 2: Config keys + env overrides (kill switch and tunables)

**Files:**
- Modify: `tradingagents/default_config.py` — add 4 keys to `DEFAULT_CONFIG` after `"pro_trader_min_premarket_volume": 0,` (line ~270); add 4 entries to `_ENV_OVERRIDES` near the other `TRADINGAGENTS_PRO_TRADER_*` entries (line ~58)
- Modify: `.env.example` (pro-trader env section)
- Test: `tests/test_env_overrides.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `DEFAULT_CONFIG` keys and env mappings (Task 3/8 consume the config keys):
  - `pro_trader_swing_profile_enabled: bool = True` ← `TRADINGAGENTS_PRO_TRADER_SWING_PROFILE`
  - `pro_trader_swing_dte_weeks: str = "3-4"` ← `TRADINGAGENTS_PRO_TRADER_DTE_WEEKS`
  - `pro_trader_swing_target_delta: str = "0.70"` ← `TRADINGAGENTS_PRO_TRADER_TARGET_DELTA`
  - `pro_trader_swing_hold_horizon_days: str = "1"` ← `TRADINGAGENTS_PRO_TRADER_HOLD_HORIZON_DAYS`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_env_overrides.py` (the `_reload_with_env` helper already exists in that file):

```python
@pytest.mark.unit
def test_pro_trader_swing_profile_env_overrides(monkeypatch):
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_PRO_TRADER_SWING_PROFILE="off",
        TRADINGAGENTS_PRO_TRADER_DTE_WEEKS="5-6",
        TRADINGAGENTS_PRO_TRADER_TARGET_DELTA="0.65",
        TRADINGAGENTS_PRO_TRADER_HOLD_HORIZON_DAYS="2",
    )
    assert dc.DEFAULT_CONFIG["pro_trader_swing_profile_enabled"] is False
    assert dc.DEFAULT_CONFIG["pro_trader_swing_dte_weeks"] == "5-6"
    assert dc.DEFAULT_CONFIG["pro_trader_swing_target_delta"] == "0.65"
    assert dc.DEFAULT_CONFIG["pro_trader_swing_hold_horizon_days"] == "1"
```

**Note:** string values pass through `_coerce` unchanged (they are `str`-typed references), so `"5-6"`, `"0.65"`, `"2"` round-trip as strings. If `_reload_with_env` is named differently, check the helpers at the top of `tests/test_env_overrides.py` and use the same helper the neighboring tests use.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_env_overrides.py::test_pro_trader_swing_profile_env_overrides -v`
Expected: FAIL with `KeyError: 'pro_trader_swing_profile_enabled'`

- [ ] **Step 3: Write minimal implementation**

In `tradingagents/default_config.py`, inside the `DEFAULT_CONFIG` dict, after the line `"pro_trader_min_premarket_volume": 0,` (line ~270):

```python
    # Swing trade profile injected into the pro-trader post-gate LLM chain
    # (docs/superpowers/specs/2026-09-09-swing-trade-profile-design.md).
    "pro_trader_swing_profile_enabled": True,
    "pro_trader_swing_dte_weeks": "3-4",
    "pro_trader_swing_target_delta": "0.70",
    "pro_trader_swing_hold_horizon_days": "1",
```

In `_ENV_OVERRIDES`, after `"TRADINGAGENTS_PRO_TRADER_MIN_PREMARKET_VOLUME": "pro_trader_min_premarket_volume",` (line ~50):

```python
    "TRADINGAGENTS_PRO_TRADER_SWING_PROFILE": "pro_trader_swing_profile_enabled",
    "TRADINGAGENTS_PRO_TRADER_DTE_WEEKS": "pro_trader_swing_dte_weeks",
    "TRADINGAGENTS_PRO_TRADER_TARGET_DELTA": "pro_trader_swing_target_delta",
    "TRADINGAGENTS_PRO_TRADER_HOLD_HORIZON_DAYS": "pro_trader_swing_hold_horizon_days",
```

In `.env.example`, in the pro-trader section (near the other `TRADINGAGENTS_PRO_TRADER_*` lines):

```bash
# Swing trade profile injected into the post-gate LLM chain when the
# pro-trader-dashboard strategy gates a screener trade
# (docs/superpowers/specs/2026-09-09-swing-trade-profile-design.md).
# Set to "off" to restore the generic intraday prompts.
TRADINGAGENTS_PRO_TRADER_SWING_PROFILE=on
# TRADINGAGENTS_PRO_TRADER_DTE_WEEKS=3-4
# TRADINGAGENTS_PRO_TRADER_TARGET_DELTA=0.70
# TRADINGAGENTS_PRO_TRADER_HOLD_HORIZON_DAYS=1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_env_overrides.py -v`
Expected: PASS (all tests including the new one)

- [ ] **Step 5: Commit (careful: pre-existing unrelated edits in these files)**

⚠️ `.env.example`, `tradingagents/default_config.py`, and `tests/test_env_overrides.py` carry unrelated uncommitted user edits. Stage only your hunks:

```bash
git add -p tradingagents/default_config.py .env.example tests/test_env_overrides.py
# select only the swing-profile hunks (verify with: git diff --cached)
git commit -m "feat: add swing profile config keys and env overrides"
```

If hunk-level staging is impractical, commit whole files and add a commit-body line: `Includes pre-existing unrelated local edits.`

---

### Task 3: Attach profile in `propagate_intraday`

**Files:**
- Modify: `tradingagents/graph/intraday_graph.py` (`propagate_intraday`, lines 90-129)
- Test: `tests/test_intraday_graph.py`

**Interfaces:**
- Consumes: `build_trade_profile`, `render_trade_profile` from `tradingagents.intraday.trade_profile` (Task 1); config keys from Task 2.
- Produces: `intraday_context["trade_profile"] = {"enabled": True, "block": str}` — key present **only** for pro-trader when enabled. Consumers (Tasks 5, 6) read `state["intraday_context"].get("trade_profile", {}).get("block")`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_intraday_graph.py`:

```python
@pytest.mark.unit
def test_propagate_intraday_attaches_swing_profile_for_pro_trader():
    captured = {}

    class _Graph:
        def invoke(self, state, **kwargs):
            captured.update(state)
            return {"trader_investment_plan": "", "final_trade_decision": ""}

    from tradingagents.graph.propagation import Propagator

    graph = IntradayTradingGraph.__new__(IntradayTradingGraph)
    graph.callbacks = []
    graph.propagator = Propagator()
    graph.graph = _Graph()

    mtf = MTFValidationResult(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        snapshot_5min={"Close": 1.0},
        snapshot_30min={"Close": 1.0},
        snapshot_daily={},
        trend_5min="up",
        trend_30min="up",
        daily_bias_direction="bullish",
        trends_aligned=True,
        vwap_5min=1.0,
        atr_5min=1.0,
    )
    gate = GateResult(
        passed=True,
        gate1_strategy=True,
        gate1_reason="ok",
        gate2_mtf_alignment=True,
        gate2_reason="ok",
        final_direction="long",
    )
    strategy_result = StrategyResult(
        passed=True,
        direction="long",
        reason="ok",
        factors_met=["orb_breakout"],
        factors_missing=[],
    )
    bias = DailyBiasReport(
        symbol="NVDA",
        trade_date="2026-07-27",
        direction="bullish",
        key_levels={},
        summary="buy",
        computed_at=datetime(2026, 7, 27, 9, 0),
    )

    graph.propagate_intraday(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        daily_bias=bias,
        mtf=mtf,
        strategy_result=strategy_result,
        gate_result=gate,
        strategy_name="pro_trader_dashboard",
    )

    ctx = captured["intraday_context"]
    assert "trade_profile" in ctx
    assert "Trade Profile" in ctx["trade_profile"]["block"]
    assert "0.70" in ctx["trade_profile"]["block"]


@pytest.mark.unit
def test_propagate_intraday_no_profile_for_other_strategies():
    captured = {}

    class _Graph:
        def invoke(self, state, **kwargs):
            captured.update(state)
            return {"trader_investment_plan": "", "final_trade_decision": ""}

    from tradingagents.graph.propagation import Propagator

    graph = IntradayTradingGraph.__new__(IntradayTradingGraph)
    graph.callbacks = []
    graph.propagator = Propagator()
    graph.graph = _Graph()

    mtf = MTFValidationResult(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        snapshot_5min={"Close": 1.0},
        snapshot_30min={"Close": 1.0},
        snapshot_daily={},
        trend_5min="up",
        trend_30min="up",
        daily_bias_direction="bullish",
        trends_aligned=True,
        vwap_5min=1.0,
        atr_5min=1.0,
    )
    gate = GateResult(
        passed=True,
        gate1_strategy=True,
        gate1_reason="ok",
        gate2_mtf_alignment=True,
        gate2_reason="ok",
        final_direction="long",
    )
    strategy_result = StrategyResult(
        passed=True,
        direction="long",
        reason="ok",
        factors_met=["orb_breakout"],
        factors_missing=[],
    )
    bias = DailyBiasReport(
        symbol="NVDA",
        trade_date="2026-07-27",
        direction="bullish",
        key_levels={},
        summary="buy",
        computed_at=datetime(2026, 7, 27, 9, 0),
    )

    graph.propagate_intraday(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        daily_bias=bias,
        mtf=mtf,
        strategy_result=strategy_result,
        gate_result=gate,
        strategy_name="orb_breakout",
    )

    assert captured["intraday_context"]["strategy"]["name"] == "orb_breakout"
    assert "trade_profile" not in captured["intraday_context"]
```

**Implementer note:** both tests are full copies of `test_intraday_state_contains_context` (same fixtures) differing only in `strategy_name` and final assertions.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_intraday_graph.py -v -k swing_profile`
Expected: FAIL with `KeyError: 'trade_profile'`

- [ ] **Step 3: Write minimal implementation**

In `tradingagents/graph/intraday_graph.py`, add import:

```python
from tradingagents.intraday.trade_profile import build_trade_profile, render_trade_profile
```

In `propagate_intraday`, extract the inline `intraday_context` literal into a local variable (same content), then conditionally add the profile. The final version of the context-building portion of `propagate_intraday`:

```python
        trade_date = bar_time.strftime("%Y-%m-%d")
        init_state = self.propagator.create_initial_state(symbol, trade_date)
        intraday_context = {
            "scan_time": bar_time.isoformat(),
            "mtf_5min_snapshot": mtf.snapshot_5min,
            "mtf_30min_snapshot": mtf.snapshot_30min,
            "daily_bias": daily_bias.direction,
            "daily_bias_report": daily_bias.summary,
            "trends_aligned": mtf.trends_aligned,
            "vwap": mtf.vwap_5min,
            "atr": mtf.atr_5min,
            "strategy": {
                "name": strategy_name,
                "direction": strategy_result.direction,
                "factors_met": strategy_result.factors_met,
                "factors_missing": strategy_result.factors_missing,
                "reason": strategy_result.reason,
            },
            "gate_results": {
                "gate1": gate_result.gate1_strategy,
                "gate2": gate_result.gate2_mtf_alignment,
            },
        }
        trade_profile = self._build_trade_profile_block(
            symbol=symbol,
            strategy_name=strategy_name,
            direction=strategy_result.direction,
        )
        if trade_profile is not None:
            intraday_context["trade_profile"] = trade_profile
        init_state.update(
            {
                "market_report": daily_bias.summary,
                "fundamentals_report": "",
                "sentiment_report": "",
                "news_report": "",
                "investment_plan": daily_bias.summary,
                "magpie_signal": build_default_magpie_signal(enabled=False),
                "intraday_context": intraday_context,
            }
        )
```

Add the helper method to `IntradayTradingGraph`:

```python
    def _build_trade_profile_block(
        self, *, symbol: str, strategy_name: str, direction: str
    ) -> dict | None:
        """Swing profile block for pro-trader runs; None when off/other strategy."""
        if strategy_name != "pro_trader_dashboard":
            return None
        profile = build_trade_profile(self.config)
        if profile is None:
            return None
        return {
            "enabled": True,
            "block": render_trade_profile(profile, symbol=symbol, direction=direction),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_intraday_graph.py -v`
Expected: PASS (all, including pre-existing tests)

- [ ] **Step 5: Commit**

```bash
git add tradingagents/graph/intraday_graph.py tests/test_intraday_graph.py
git commit -m "feat: attach swing trade profile to intraday_context for pro-trader"
```

---

### Task 4: `SwingTradeProposal` schema + renderer

**Files:**
- Modify: `tradingagents/agents/schemas.py` (add after `render_trader_proposal`, line ~181)
- Test: `tests/test_schemas.py` — check first with `ls tests/ | grep -i schema`; if a schema-test file exists, add there instead

**Interfaces:**
- Consumes: `TraderProposal`, `render_trader_proposal` from `tradingagents/agents/schemas.py`.
- Produces (consumed by Task 5 and Task 7):
  - `class SwingTradeProposal(TraderProposal)` with fields `option_structure: str | None = None`, `hold_horizon_days: str | None = None`, `option_direction: str | None = None`.
  - `render_swing_trade_proposal(proposal: SwingTradeProposal) -> str` — base markdown plus `**Option Structure**:` / `**Hold Horizon**:` / `**Option Direction**:` lines (inserted before the `FINAL TRANSACTION PROPOSAL` trailer so Task 7's regex finds them and the FINAL line stays last).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_schemas.py
from __future__ import annotations

import pytest

from tradingagents.agents.schemas import (
    SwingTradeProposal,
    render_swing_trade_proposal,
)


@pytest.mark.unit
def test_swing_trade_proposal_parses_all_fields():
    proposal = SwingTradeProposal(
        action="Buy",
        reasoning="ORB breakout with aligned RRS.",
        entry_price=100.5,
        stop_loss=98.0,
        option_structure="Long call, ~0.70 delta, 21 DTE",
        hold_horizon_days="1",
        option_direction="long call",
    )
    assert proposal.option_structure == "Long call, ~0.70 delta, 21 DTE"
    assert proposal.hold_horizon_days == "1"
    assert proposal.option_direction == "long call"


@pytest.mark.unit
def test_swing_trade_proposal_fields_optional():
    proposal = SwingTradeProposal(action="Hold", reasoning="wait")
    assert proposal.option_structure is None
    assert proposal.hold_horizon_days is None
    assert proposal.option_direction is None


@pytest.mark.unit
def test_render_swing_trade_proposal_includes_option_fields():
    proposal = SwingTradeProposal(
        action="Buy",
        reasoning="ORB breakout with aligned RRS.",
        entry_price=100.5,
        stop_loss=98.0,
        option_structure="Long call, ~0.70 delta, 21 DTE",
        hold_horizon_days="1",
        option_direction="long call",
    )
    text = render_swing_trade_proposal(proposal)
    assert "**Option Structure**: Long call, ~0.70 delta, 21 DTE" in text
    assert "**Hold Horizon**: 1" in text
    assert "**Option Direction**: long call" in text
    assert "FINAL TRANSACTION PROPOSAL: **BUY**" in text
    # FINAL line stays last (backward-compat for grep-based consumers)
    assert text.strip().endswith("FINAL TRANSACTION PROPOSAL: **BUY**")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_schemas.py -v -k swing`
Expected: FAIL with `ImportError: cannot import name 'SwingTradeProposal'`

- [ ] **Step 3: Write minimal implementation**

In `tradingagents/agents/schemas.py`, after `render_trader_proposal`:

```python
class SwingTradeProposal(TraderProposal):
    """TraderProposal extended for swing option trades (pro-trader screener).

    Field descriptions double as output instructions (repo structured-output
    pattern); all fields optional so a HOLD or partial answer stays valid.
    """

    option_structure: str | None = Field(
        default=None,
        description=(
            "The option structure for this swing trade, e.g. "
            "'Long call, ~0.70 delta, 21 DTE'. Null when action is Hold."
        ),
    )
    hold_horizon_days: str | None = Field(
        default=None,
        description=(
            "Expected holding period in trading days for the option position, "
            "e.g. '1' (close within about a day)."
        ),
    )
    option_direction: str | None = Field(
        default=None,
        description=(
            "The option leg: exactly 'long call' or 'long put', or null for HOLD."
        ),
    )


def render_swing_trade_proposal(proposal: SwingTradeProposal) -> str:
    """Render SwingTradeProposal: base markdown plus swing fields before the FINAL line."""
    parts = [
        f"**Action**: {proposal.action.value}",
        "",
        f"**Reasoning**: {proposal.reasoning}",
    ]
    if proposal.entry_price is not None:
        parts.extend(["", f"**Entry Price**: {proposal.entry_price}"])
    if proposal.stop_loss is not None:
        parts.extend(["", f"**Stop Loss**: {proposal.stop_loss}"])
    if proposal.position_sizing:
        parts.extend(["", f"**Position Sizing**: {proposal.position_sizing}"])
    if proposal.option_structure:
        parts.extend(["", f"**Option Structure**: {proposal.option_structure}"])
    if proposal.hold_horizon_days:
        parts.extend(["", f"**Hold Horizon**: {proposal.hold_horizon_days}"])
    if proposal.option_direction:
        parts.extend(["", f"**Option Direction**: {proposal.option_direction}"])
    parts.extend([
        "",
        f"FINAL TRANSACTION PROPOSAL: **{proposal.action.value.upper()}**",
    ])
    return "\n".join(parts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_schemas.py -v -k swing`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add tradingagents/agents/schemas.py tests/test_schemas.py
git commit -m "feat: add SwingTradeProposal schema with option structure fields"
```

---

### Task 5: Trader node consumes profile + binds swing schema

**Files:**
- Modify: `tradingagents/agents/trader/trader.py`
- Test: `tests/test_trader_swing.py` (new file)

**Interfaces:**
- Consumes: `intraday_context["trade_profile"]["block"]` (Task 3), `SwingTradeProposal` + `render_swing_trade_proposal` (Task 4).
- Produces: `trader_investment_plan` graph-state string. With profile: rendered from `SwingTradeProposal` via `render_swing_trade_proposal` (contains `**Option Structure**:` and `**Hold Horizon**` lines). Without profile: unchanged `TraderProposal` markdown. `_state_to_intraday_signal` (Task 7) regex-extracts from this text.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trader_swing.py
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.agents.schemas import SwingTradeProposal, TraderProposal
from tradingagents.agents.trader.trader import create_trader


def _swing_state() -> dict:
    return {
        "company_of_interest": "NVDA",
        "investment_plan": "buy the dip",
        "magpie_signal": None,
        "intraday_context": {
            "strategy": {"name": "pro_trader_dashboard", "direction": "long"},
            "trade_profile": {
                "enabled": True,
                "block": (
                    "**Trade Profile: Swing Options**\n"
                    "- Structure: long calls, ~3-4 weeks to expiration, ~0.70 delta"
                ),
            },
        },
    }


def _plain_state() -> dict:
    return {
        "company_of_interest": "NVDA",
        "investment_plan": "plan",
        "magpie_signal": None,
        "intraday_context": {
            "strategy": {"name": "base_momentum", "direction": "long"},
        },
    }


@pytest.mark.unit
def test_trader_binds_swing_schema_and_includes_profile():
    with (
        patch("tradingagents.agents.trader.trader.bind_structured") as mock_bind,
        patch(
            "tradingagents.agents.trader.trader.invoke_structured_or_freetext"
        ) as mock_invoke,
    ):
        mock_invoke.return_value = "FINAL TRANSACTION PROPOSAL: **BUY**"
        node = create_trader(MagicMock())
        node(_swing_state())

    # Second positional arg of bind_structured is the schema class.
    assert mock_bind.call_args[0][1] is SwingTradeProposal

    messages = mock_invoke.call_args[0][2]
    system_content = messages[0]["content"]
    user_content = messages[1]["content"]
    assert "swing option trade" in system_content
    assert "**Trade Profile: Swing Options**" in user_content


@pytest.mark.unit
def test_trader_binds_plain_schema_without_profile():
    with (
        patch("tradingagents.agents.trader.trader.bind_structured") as mock_bind,
        patch(
            "tradingagents.agents.trader.trader.invoke_structured_or_freetext"
        ) as mock_invoke,
    ):
        mock_invoke.return_value = "rendered"
        node = create_trader(MagicMock())
        node(_plain_state())

    assert mock_bind.call_args[0][1] is TraderProposal
    user_content = mock_invoke.call_args[0][2][1]["content"]
    assert "Trade Profile" not in user_content
```

**Implementer note:** fix the typo `system_content` → `system_content = system_content if you name it differently` — the assertion variable must be `system_content`, so write that line as `assert "swing option trade" in system_content`. Read `tradingagents/agents/trader/trader.py` first to confirm `bind_structured`/`invoke_structured_or_freetext` are module-level imports (they are, lines 14-19), which makes the patch targets valid.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trader_swing.py -v`
Expected: FAIL — first test fails on `assert mock_bind.call_args[0][1] is SwingTradeProposal` (currently binds `TraderProposal`).

- [ ] **Step 3: Write minimal implementation**

Rewrite `tradingagents/agents/trader/trader.py`'s `create_trader` to move schema binding inside the node and branch on profile presence. Full replacement for the factory:

```python
def create_trader(llm):
    def trader_node(state, name):
        company_name = state["company_of_interest"]
        instrument_context = get_instrument_context_from_state(state)
        investment_plan = state["investment_plan"]
        intraday_context = state.get("intraday_context") or {}
        strategy_block = intraday_context.get("strategy")
        trade_profile_block = (intraday_context.get("trade_profile") or {}).get("block")

        if strategy_block:
            signal_label = "Intraday Strategy Signal"
            signal_summary = render_strategy_summary(strategy_block)
            system_extra = (
                "Anchor your reasoning in the analysts' reports, the research plan, and the "
                "deterministic intraday strategy signal when it is available."
            )
        else:
            signal_label = "Magpie Strategy Signal"
            signal_summary = render_magpie_signal_summary(state.get("magpie_signal"))
            system_extra = (
                "Anchor your reasoning in the analysts' reports, the research plan, and the "
                "deterministic Magpie strategy signal when it is available. If the Magpie signal "
                "is unavailable or disabled, say so briefly and rely on the rest of the evidence."
            )

        swing_system_line = ""
        profile_text = ""
        if trade_profile_block:
            proposal_model = SwingTradeProposal
            render_fn = render_swing_trade_proposal
            swing_system_line = (
                " You are evaluating a swing option trade: reason about the underlying's "
                "move, then express entry/stop/targets on the underlying; the executor "
                "maps the move to the ~0.70-delta contract."
            )
            profile_text = f"\n\n{trade_profile_block}"
        else:
            proposal_model = TraderProposal
            renderer = render_trader_proposal
            swing_system_line = ""

        structured_llm = bind_structured(llm, proposal_model, "Trader")

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a trading agent analyzing market data to make investment decisions. "
                    "Based on your analysis, provide a specific recommendation to buy, sell, or hold. "
                    + system_extra
                    + swing_system_line
                    + get_language_instruction()
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Based on a comprehensive analysis by a team of analysts, here is an investment "
                    f"plan tailored for {company_name}. {instrument_context} This plan incorporates "
                    f"insights from current technical market trends, macroeconomic indicators, and "
                    f"social media sentiment. Use this plan as a foundation for evaluating your next "
                    f"trading decision.\n\nProposed Investment Plan: {investment_plan}\n\n"
                    f"{signal_label}:\n{signal_summary}\n\n"
                    f"Leverage these insights to make an informed and strategic decision."
                    + (f"\n\n{trade_profile_block}" if trade_profile_block else "")
                ),
            },
        ]

        trader_plan = invoke_structured_or_freetext(
            structured_llm,
            llm,
            messages,
            render_fn,
            "Trader",
        )

        return {
            "messages": [AIMessage(content=trader_plan)],
            "trader_investment_plan": trader_plan,
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")
```

**Implementer notes (read before editing):**
1. The variable naming above intentionally shows the swing branch assignments; reconcile names when writing (`proposal_model`/`render_fn`/`swing_system_line` must be defined in BOTH branches before use — the `else` branch must set `swing_system_line = ""` and leave `trade_profile_block` unused). A clean final version:

```python
        if trade_profile_block:
            proposal_model = SwingTradeProposal
            render_fn = render_swing_trade_proposal
            swing_system_line = (
                " You are evaluating a swing option trade: reason about the "
                "underlying's move, then express entry/stop/targets on the "
                "underlying; the executor maps the move to the ~0.70-delta contract."
            )
        else:
            proposal_model = TraderProposal
            render_fn = render_trader_proposal
            swing_system_line = ""
```

   then the messages block uses `proposal_model` for binding, appends `swing_system_line` to the system content, and appends `f"\n\n{trade_profile_block}"` to the user content when present. Delete the old factory-time `structured_llm = bind_structured(llm, TraderProposal, "Trader")` line (line 13) and bind inside the node.
2. Keep the existing `system_extra` logic (Magpie vs strategy branch) untouched — the swing line is additive.
3. `invoke_structured_or_freetext` signature: `(structured_llm, llm, messages, render_fn, label)` — the swing branch passes `render_swing_trade_proposal`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trader_swing.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run neighboring suites**

Run: `pytest tests/test_intraday_graph.py tests/test_pro_trader_strategy.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tradingagents/agents/trader/trader.py tests/test_trader_swing.py
git commit -m "feat: trader binds SwingTradeProposal and renders swing profile"
```

---

### Task 6: Risk debators + PM receive profile

**Files:**
- Create: `tradingagents/agents/utils/profile_prompt.py`
- Modify: `tradingagents/agents/risk_mgmt/aggressive_debator.py` (prompt assembly, line ~25-37)
- Modify: `tradingagents/agents/risk_mgmt/conservative_debator.py` (same pattern)
- Modify: `tradingagents/agents/risk_mgmt/neutral_debator.py` (same pattern)
- Modify: `tradingagents/agents/managers/portfolio_manager.py` (prompt f-string, lines ~42-63)
- Test: `tests/test_profile_prompt.py` (new)

**Interfaces:**
- Consumes: `intraday_context["trade_profile"]["block"]` (Task 3).
- Produces: `append_trade_profile_block(base_prompt: str, profile_block: str | None, role_line: str) -> str` and constant `SWING_RISK_ROLE_LINE` in `tradingagents/agents/utils/profile_prompt.py`. No later task consumes these (prompt-text only).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_profile_prompt.py
from __future__ import annotations

import pytest

from tradingagents.agents.utils.profile_prompt import (
    SWING_PM_ROLE_LINE,
    SWING_RISK_ROLE_LINE,
    append_trade_profile_block,
)


@pytest.mark.unit
def test_appends_profile_with_role_line_when_present():
    base = "BASE PROMPT"
    result = append_trade_profile_block(
        base,
        "**Trade Profile: Swing Options**\n- Structure: long calls",
        SWING_RISK_ROLE_LINE,
    )
    assert result.startswith("BASE PROMPT")
    assert "Trade Profile" in result
    assert SWING_RISK_ROLE_LINE in result
    assert "overnight gap risk" in result


@pytest.mark.unit
def test_returns_base_unchanged_when_profile_absent():
    base = "BASE PROMPT"
    assert append_trade_profile_block(base, None, SWING_RISK_ROLE_LINE) == base
    assert append_trade_profile_block(base, "", SWING_RISK_ROLE_LINE) == base
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_profile_prompt.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tradingagents.agents.utils.profile_prompt'`

- [ ] **Step 3: Write minimal implementation**

```python
# tradingagents/agents/utils/profile_prompt.py
"""Append the swing trade-profile block to an agent prompt when present."""
from __future__ import annotations

SWING_RISK_ROLE_LINE = (
    "Evaluate risk for a swing options position with the stated DTE, ~0.70 "
    "delta, expected hold ~1 day - focus on overnight gap risk, theta, and "
    "event risk within the hold window."
)

SWING_PM_ROLE_LINE = (
    "Weigh the swing thesis (DTE/delta/hold horizon in the profile below) "
    "against the risk debate for the final decision."
)


def append_trade_profile_block(
    base_prompt: str, profile_block: str | None, role_line: str
) -> str:
    """Append the rendered trade-profile block with a role-specific instruction.

    ``profile_block`` is the pre-rendered markdown block (the ``block`` value
    from ``intraday_context["trade_profile"]``), or None when absent or
    kill-switched; in that case ``base_prompt`` is returned unchanged.
    """
    if not profile_block:
        return base_prompt
    return f"{base_prompt}\n\n---\n\n{role_line}\n\n{profile_block}"
```

Wire into each debator file. **Apply exactly this wiring** — in each of `aggressive_debator.py`, `conservative_debator.py`, `neutral_debator.py`:

Add import at top:

```python
from tradingagents.agents.utils.profile_prompt import (
    SWING_RISK_ROLE_LINE,
    append_trade_profile_block,
)
```

Then replace the final `response = llm.invoke(prompt)` with:

```python
        profile_block = (state.get("intraday_context") or {}).get("trade_profile", {}).get("block")
        prompt = append_trade_profile_block(prompt, profile_block, SWING_RISK_ROLE_LINE)

        response = llm.invoke(prompt)
```

Note `get_language_instruction()` is already concatenated at the end of each f-string; leave it there and let `append_trade_profile_block` run after (profile block sits after the language instruction — acceptable; if reviewers prefer before, append the profile before `+ get_language_instruction()` instead: build `prompt = prompt_without_lang + profile_suffix + get_language_instruction()`).

For `portfolio_manager.py`, inside `portfolio_manager_node` after `prompt = f"""..."""` and before `invoke_structured_or_freetext(...)`:

```python
from tradingagents.agents.utils.profile_prompt import SWING_PM_ROLE_LINE, append_trade_profile_block

        profile_block = (state.get("intraday_context") or {}).get("trade_profile", {}).get("block")
        if profile_block:
            prompt = append_trade_profile_block(prompt, profile_block, SWING_PM_ROLE_LINE)
```

Note: debators read `state` dict — the node signature is `def aggressive_node(state) -> dict`; `state` already contains `intraday_context` (seeded by `propagate_intraday`), so `state.get("intraday_context")` works.

- [ ] **Step 4: Write node-level tests (still part of this task's red-green cycle — add before Step 5's green run)**

```python
# tests/test_risk_debator_swing.py
from unittest.mock import MagicMock

import pytest

from tradingagents.agents.risk_mgmt.aggressive_debator import create_aggressive_debator
from tradingagents.agents.risk_mgmt.conservative_debator import create_conservative_debator
from tradingagents.agents.risk_mgmt.neutral_debator import create_neutral_debator


def _risk_state(profile_block: str | None) -> dict:
    intraday_context = {}
    if profile_block:
        intraday_context["trade_profile"] = {"enabled": True, "block": profile_block}
    return {
        "risk_debate_state": {
            "history": "",
            "aggressive_history": "",
            "conservative_history": "",
            "neutral_history": "",
            "count": 0,
        },
        "market_report": "m",
        "sentiment_report": "s",
        "news_report": "n",
        "fundamentals_report": "f",
        "trader_investment_plan": "buy NVDA",
        "intraday_context": intraday_context,
    }


class _RecordingLLM:
    def __init__(self):
        self.prompts: list[str] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return MagicMock(content="ok")


@pytest.mark.unit
@pytest.mark.parametrize("factory", [create_aggressive_debator, create_conservative_debator, create_neutral_debator])
def test_debator_prompt_includes_profile_when_present(factory):
    llm = _RecordingLLM()
    node = factory(llm)
    node(_risk_state("SWING-PROFILE-BLOCK"))
    assert "SWING-PROFILE-BLOCK" in llm.prompts[0]
    assert "overnight gap risk" in llm.prompts[0]


@pytest.mark.unit
@pytest.mark.parametrize("factory", [create_aggressive_debator, create_conservative_debator, create_neutral_debator])
def test_debator_prompt_unchanged_without_profile(factory):
    llm = _RecordingLLM()
    node = factory(llm)
    node(_risk_state(None))
    assert "Trade Profile" not in llm.prompts[0]


@pytest.mark.unit
def test_pm_prompt_includes_profile_when_present():
    with patch(
        "tradingagents.agents.managers.portfolio_manager.invoke_structured_or_freetext"
    ) as mock_invoke:
        mock_invoke.return_value = "PM decision"
        node = create_portfolio_manager(MagicMock())
        state = {
            "risk_debate_state": {"history": "debate"},
            "investment_plan": "plan",
            "trader_investment_plan": "buy",
            "intraday_context": {
                "trade_profile": {"enabled": True, "block": "PM-PROFILE-BLOCK"},
            },
        }
        node(state)

    prompt_arg = _pm_prompt_arg(mock_invoke)
    assert "PM-PROFILE-BLOCK" in prompt_arg
    assert "swing thesis" in prompt_arg


@pytest.mark.unit
def test_pm_prompt_without_profile_has_no_block():
    with patch(
        "tradingagents.agents.managers.portfolio_manager.invoke_structured_or_freetext"
    ) as mock_invoke:
        mock_invoke.return_value = "PM decision"
        node = create_portfolio_manager(MagicMock())
        state = {
            "risk_debate_state": {"history": "debate"},
            "investment_plan": "plan",
            "trader_investment_plan": "buy",
            "intraday_context": {},
        }
        node(state)

    prompt_arg = _pm_prompt_arg(mock_invoke)
    assert "PM-PROFILE-BLOCK" not in prompt_arg


# helpers


def _pm_prompt_arg(mock_invoke) -> str:
    """The PM passes a plain string prompt (not a messages list)."""
    args = mock_invoke.call_args[0]
    candidate = args[2] if len(args) > 2 else args[-1]
    assert isinstance(candidate, str)
    return candidate


# Add these imports at the top of the test file:
# from unittest.mock import MagicMock, patch
# from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
```

**Implementer note:** `invoke_structured_or_freetext(structured_llm, llm, prompt, render_fn, label)` — the prompt string is `call_args[0][2]` (third positional arg). If the actual call signature differs when you implement Task 6, adjust the helper index accordingly.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_profile_prompt.py tests/test_risk_debator_swing.py -v`
Expected: PASS

Run: `pytest tests/ -k "debator or portfolio_manager" -q`
Expected: PASS (no regressions)

- [ ] **Step 6: Commit**

```bash
git add tradingagents/agents/utils/profile_prompt.py tradingagents/agents/risk_mgmt/aggressive_debator.py tradingagents/agents/risk_mgmt/conservative_debator.py tradingagents/agents/risk_mgmt/neutral_debator.py tradingagents/agents/managers/portfolio_manager.py tests/test_profile_prompt.py tests/test_risk_debator_swing.py
git commit -m "feat: risk debators and PM receive swing trade profile context"
```

---

### Task 7: Signal fields + CSV columns

**Files:**
- Modify: `tradingagents/intraday/session.py:41-51` (add fields to `IntradaySignal`)
- Modify: `tradingagents/graph/intraday_graph.py` (`_state_to_intraday_signal`, lines 143-183, plus `_extract_float` helper area ~186-190)
- Modify: `tradingagents/intraday/scanner.py` (`_emit_signal` fieldnames ~863-876 and row dict ~878-890)
- Test: `tests/test_intraday_graph.py`, plus wherever CSV emission is tested (check: `rg -l "signals.csv" tests/`)

**Interfaces:**
- Consumes: markdown lines `**Option Structure**: ...` / `**Hold Horizon**: ...` emitted by `render_swing_trade_proposal` (Task 4) into `trader_investment_plan` (Task 5). Regex extraction reads them — no new plumbing.
- Produces: `IntradaySignal.option_structure: str | None = None`, `IntradaySignal.hold_horizon_days: str | None = None`; `signals.csv` columns `option_structure`, `hold_horizon_days`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_intraday_graph.py` (reuse module-level imports already present; add a `_gate()` local helper mirroring the existing tests' `GateResult(...)` construction):

```python
@pytest.mark.unit
def test_state_to_signal_extracts_swing_fields_from_markdown():
    final_state = {
        "trader_investment_plan": (
            "**Action**: Buy\n\n**Reasoning**: r\n\n**Entry Price**: 100.5\n\n"
            "**Stop Loss**: 98.0\n\n**Option Structure**: Long call, ~0.70 delta, 21 DTE\n\n"
            "**Hold Horizon**: 1\n\nFINAL TRANSACTION PROPOSAL: **BUY**"
        ),
        "final_trade_decision": "**Rating**: Buy",
    }
    signal = _state_to_intraday_signal(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        direction="long",
        strategy_result=StrategyResult(
            passed=True, direction="long", reason="ok", factors_met=["a"], factors_missing=[]
        ),
        gate_result=GateResult(
            passed=True,
            gate1_strategy=True,
            gate1_reason="ok",
            gate2_mtf_alignment=True,
            gate2_reason="ok",
            final_direction="long",
        ),
        final_state=final_state,
    )
    assert signal.option_structure == "Long call, ~0.70 delta, 21 DTE"
    assert signal.hold_horizon_days == "1"
    assert signal.entry_price == 100.5
    assert signal.stop_loss == 98.0


@pytest.mark.unit
def test_state_to_signal_swing_fields_none_without_markers():
    signal = _state_to_intraday_signal(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        direction="long",
        strategy_result=StrategyResult(passed=True, direction="long", reason="ok", factors_met=["a"], factors_missing=[]),
        gate_result=GateResult(
            passed=True,
            gate1_strategy=True,
            gate1_reason="ok",
            gate2_mtf_alignment=True,
            gate2_reason="ok",
            final_direction="long",
        ),
        final_state={
            "trader_investment_plan": "**Action**: Buy\n**Entry Price**: 100.5\n**Stop Loss**: 98.0",
            "final_trade_decision": "**Rating**: Buy",
        },
    )
    assert signal.option_structure is None
    assert signal.hold_horizon_days is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_intraday_graph.py::test_state_to_signal_extracts_swing_fields_from_markdown -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'option_structure'` (dataclass lacks fields) or `AssertionError` on `None`

- [ ] **Step 3: Implement signal fields + extraction**

In `tradingagents/intraday/session.py`, extend the dataclass (keep field order; new fields last with defaults):

```python
@dataclass
class IntradaySignal:
    symbol: str
    bar_time: datetime
    action: Literal["BUY", "SELL", "HOLD"]
    direction: Literal["long", "short", "none"]
    entry_price: float | None
    stop_loss: float | None
    confidence: str
    setup_score: int
    gate_summary: str
    reasoning: str
    option_structure: str | None = None
    hold_horizon_days: str | None = None
```

In `tradingagents/graph/intraday_graph.py`, add a string-extraction helper next to `_extract_float`:

```python
def _extract_str(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None
```

In `_state_to_intraday_signal`, after the existing entry/stop extraction (lines ~159-160):

```python
    option_structure = _extract_str(r"\*\*Option Structure\*\*:\s*(.+)", trader_plan)
    hold_horizon_days = _extract_str(r"\*\*Hold Horizon\*\*:\s*(.+)", trader_plan)
```

and add to the `IntradaySignal(...)` construction:

```python
        option_structure=option_structure,
        hold_horizon_days=hold_horizon_days,
```

- [ ] **Step 4: CSV columns**

In `tradingagents/intraday/scanner.py` `_emit_signal`: append to the `fieldnames` list after `"reasoning"`:

```python
                    "option_structure",
                    "hold_horizon_days",
```

and to the `writerow` dict after `"reasoning": signal.reasoning,`:

```python
                    "option_structure": signal.option_structure,
                    "hold_horizon_days": signal.hold_horizon_days,
```

- [ ] **Step 5: CSV test**

Find the existing CSV test (`rg -n "signals.csv" tests/`) and extend, or add to `tests/test_scanner_signals.py`:

```python
@pytest.mark.unit
def test_signal_csv_includes_swing_columns(tmp_path, monkeypatch):
    from tradingagents.intraday.scanner import WatchlistScanner
    from tradingagents.intraday.session import IntradaySignal
    from datetime import datetime

    scanner = WatchlistScanner.__new__(WatchlistScanner)
    scanner.config = {"intraday_output_dir": str(tmp_path)}
    scanner.session = SimpleNamespace(
        session_date="2026-07-27",
        signal_log=[],
        last_signal_by_symbol={},
    )
    scanner.dashboard = None
    signal = IntradaySignal(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        action="BUY",
        direction="long",
        entry_price=100.5,
        stop_loss=98.0,
        confidence="Strong",
        setup_score=5,
        gate_summary="G1=True G2=True",
        reasoning="r",
        option_structure="Long call, ~0.70 delta, 21 DTE",
        hold_horizon_days="1",
    )
    scanner._emit_signal(signal)

    rows = (scanner._output_dir / "2026-07-27" / "signals.csv").read_text().strip().splitlines()
    assert "option_structure" in rows[0]
    assert "hold_horizon_days" in rows[0]
    assert "Long call, ~0.70 delta, 21 DTE" in rows[1]
```

**Implementer note:** `WatchlistScanner`'s constructor and `_output_dir` attribute may differ from this scaffold — read `tradingagents/intraday/scanner.py` `__init__` and `_emit_signal` first; if constructing the scanner is heavy, extract the CSV write into a module-level function `write_signal_csv(out_dir, signal)` and unit-test that directly (preferred: smaller test surface, no scanner scaffolding).

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_intraday_graph.py -v` and the CSV test file
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add tradingagents/intraday/session.py tradingagents/intraday/scanner.py tradingagents/graph/intraday_graph.py tests/test_intraday_graph.py
git commit -m "feat: persist option structure and hold horizon in intraday signals"
```

---

### Task 8: Baseline sweep + kill-switch end-to-end verification

**Files:**
- Test: `tests/test_intraday_graph.py` (kill-switch test), `tests/test_env_overrides.py` (already covered in Task 2)
- Modify: nothing (verification only)

**Interfaces:**
- Consumes: all prior tasks.
- Produces: verified kill switch + green suite.

- [ ] **Step 1: Write the kill-switch graph test**

Add to `tests/test_intraday_graph.py` (fixtures identical to Task 3's pro-trader test):

```python
@pytest.mark.unit
def test_propagate_intraday_no_profile_when_kill_switch_off(monkeypatch):
    captured = {}

    class _Graph:
        def invoke(self, state, **kwargs):
            captured.update(state)
            return {"trader_investment_plan": "", "final_trade_decision": ""}

    from tradingagents.graph.propagation import Propagator

    graph = IntradayTradingGraph.__new__(IntradayTradingGraph)
    graph.callbacks = []
    graph.config = {"pro_trader_swing_profile_enabled": False}
    graph.propagator = Propagator()
    graph.graph = _Graph()

    # ... same mtf/gate/strategy_result/bias fixtures as
    # test_propagate_intraday_attaches_swing_profile_for_pro_trader ...

    graph.propagate_intraday(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        daily_bias=bias,
        mtf=mtf,
        strategy_result=strategy_result,
        gate_result=gate,
        strategy_name="pro_trader_dashboard",
    )

    assert "trade_profile" not in captured["intraday_context"]
```

- [ ] **Step 2: Run to verify pass (implementation already honors the switch via Task 1's `build_trade_profile` returning None)**

Run: `pytest tests/test_intraday_graph.py -v -k kill_switch`
Expected: PASS (the Task 3 guard `if trade_profile is not None` skips attachment when `build_trade_profile` returns `None`)

If FAIL: check `_build_trade_profile_block` reads `self.config` (the `__new__`-constructed graph in tests needs `graph.config = {...}` set — add `graph.config = {}` in the test scaffold for the enabled case, and the kill-switch case sets the key explicitly).

- [ ] **Step 3: Run the full baseline**

Run: `pytest tests/ -q`
Expected: all PASS (compare count with the baseline you recorded before Task 1 — no new failures)

- [ ] **Step 4: Commit**

```bash
git add tests/test_intraday_graph.py
git commit -m "test: verify swing profile kill switch in propagate_intraday"
```

---

### Task 9: Docs touch-up

**Files:**
- Modify: `docs/intraday/README.md` (only if it documents the signal CSV columns or post-gate chain)
- `.env.example` already covered by Task 2.

**Interfaces:** documentation only.

- [ ] **Step 1: Find doc references**

Run: `rg -n "signals.csv|Trader → Risk|intraday_context" docs/`
If `docs/intraday/README.md` documents the post-gate chain or CSV columns, add: the two new columns (`option_structure`, `hold_horizon_days`) and a short paragraph: "When the pro-trader-dashboard strategy gates a screener trade, a swing trade profile (DTE ~3-4 weeks, delta ~0.70, ~1-day hold) is injected into the Trader/risk/PM prompts; disable with `TRADINGAGENTS_PRO_TRADER_SWING_PROFILE=off`." If nothing documents the chain or CSV columns, skip this task.

- [ ] **Step 2: Commit (only if docs changed)**

```bash
git add docs/intraday/README.md
git commit -m "docs: document swing trade profile in intraday README"
```

---

## Self-Review (performed against the spec)

- **Spec coverage:** profile dataclass+renderer (Task 1), env kill-switch + tunables (Task 2), injection via `intraday_context` (Task 3), `SwingTradeProposal` structured output with regex fallback (Tasks 4-5), risk debators + PM receive block (Task 6), `IntradaySignal`/CSV persistence (Task 7), kill-switch test + full baseline (Task 8), docs (Task 9). All spec sections §3-§8 map to tasks.
- **Placeholder scan:** all test code complete; two `...` markers in Task 3/8 are explicit "reuse the fixture scaffold from the named test" instructions with the differing lines shown verbatim.
- **Type consistency:** `build_trade_profile(config) -> TradeProfile | None` (Task 1) matches Task 3's `_build_trade_profile_block` guard and Task 8's kill-switch test; `trade_profile = {"enabled": bool, "block": str}` shape consistent across Tasks 3/5/6; `SwingTradeProposal` field names identical in Tasks 4, 5, 7; CSV columns match `IntradaySignal` field names.
