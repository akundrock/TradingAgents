from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tradingagents.intraday.indicators.supertrend import compute_supertrend
from tradingagents.intraday.mtf_validator import MTFValidationResult
from tradingagents.intraday.session import DailyBiasReport
from tradingagents.intraday.strategy import StrategyResult

Gate2Mode = Literal["off", "daily_bias", "supertrend"]


@dataclass
class GateResult:
    passed: bool
    gate1_strategy: bool
    gate1_reason: str
    gate2_mtf_alignment: bool
    gate2_reason: str
    final_direction: Literal["long", "short", "none"]
    gate2_mode: Gate2Mode = "off"


def resolve_gate2_mode(config: dict) -> Gate2Mode:
    """Resolve the active Gate 2 confirmation mode."""
    explicit = config.get("intraday_gate2_mode")
    if explicit in ("off", "daily_bias", "supertrend"):
        return explicit

    strategy = str(config.get("intraday_strategy", "base_momentum"))
    screener_enabled = bool(config.get("intraday_screener_enabled", False))
    if strategy == "orb_breakout" and screener_enabled:
        if bool(config.get("intraday_orb_breakout_screener_disable_gate2", False)):
            return "off"
        return "supertrend"

    if not bool(config.get("intraday_require_daily_bias_alignment", True)):
        return "off"
    return "daily_bias"


def resolve_require_daily_bias_alignment(config: dict) -> bool:
    """Return whether Gate 2 uses daily-bias + 30m trend alignment."""
    return resolve_gate2_mode(config) == "daily_bias"


class GatingLayer:
    def evaluate(
        self,
        mtf: MTFValidationResult,
        strategy_result: StrategyResult,
        daily_bias: DailyBiasReport,
        config: dict,
    ) -> GateResult:
        direction = strategy_result.direction
        gate1_pass = (
            strategy_result.passed
            and direction in ("long", "short")
        )
        gate1_reason = strategy_result.reason

        if not gate1_pass:
            return GateResult(
                passed=False,
                gate1_strategy=False,
                gate1_reason=gate1_reason,
                gate2_mtf_alignment=False,
                gate2_reason="Not evaluated (Gate 1 failed)",
                final_direction="none",
            )

        gate2_mode = resolve_gate2_mode(config)
        if gate2_mode == "off":
            return GateResult(
                passed=True,
                gate1_strategy=True,
                gate1_reason=gate1_reason,
                gate2_mtf_alignment=True,
                gate2_reason="Gate 2 disabled",
                final_direction=direction,
                gate2_mode=gate2_mode,
            )

        if gate2_mode == "daily_bias":
            gate2_pass = self._mtf_aligns_with_bias(mtf, daily_bias, direction)
            if not gate2_pass:
                gate2_reason = (
                    f"30-min trend '{mtf.trend_30min}' does not align with daily bias "
                    f"'{daily_bias.direction}' for {direction} setup"
                )
                return GateResult(
                    passed=False,
                    gate1_strategy=True,
                    gate1_reason=gate1_reason,
                    gate2_mtf_alignment=False,
                    gate2_reason=gate2_reason,
                    final_direction=direction,
                    gate2_mode=gate2_mode,
                )
            return GateResult(
                passed=True,
                gate1_strategy=True,
                gate1_reason=gate1_reason,
                gate2_mtf_alignment=True,
                gate2_reason="MTF alignment satisfied (daily_bias mode)",
                final_direction=direction,
                gate2_mode=gate2_mode,
            )

        gate2_pass, supertrend_direction = self._supertrend_aligns(mtf, direction)
        if not gate2_pass:
            gate2_reason = (
                f"SuperTrend '{supertrend_direction}' does not align with "
                f"{direction} setup"
            )
            return GateResult(
                passed=False,
                gate1_strategy=True,
                gate1_reason=gate1_reason,
                gate2_mtf_alignment=False,
                gate2_reason=gate2_reason,
                final_direction=direction,
                gate2_mode=gate2_mode,
            )

        return GateResult(
            passed=True,
            gate1_strategy=True,
            gate1_reason=gate1_reason,
            gate2_mtf_alignment=True,
            gate2_reason=f"SuperTrend aligned ({supertrend_direction})",
            final_direction=direction,
            gate2_mode=gate2_mode,
        )

    @staticmethod
    def _mtf_aligns_with_bias(
        mtf: MTFValidationResult,
        daily_bias: DailyBiasReport,
        direction: Literal["long", "short", "none"],
    ) -> bool:
        if direction == "long":
            return daily_bias.direction == "bullish" and mtf.trend_30min == "up"
        if direction == "short":
            return daily_bias.direction == "bearish" and mtf.trend_30min == "down"
        return False

    @staticmethod
    def _supertrend_aligns(
        mtf: MTFValidationResult,
        direction: Literal["long", "short", "none"],
    ) -> tuple[bool, str]:
        state = compute_supertrend(mtf.df_5min)
        if state.direction == "init":
            return False, "init"
        if direction == "long":
            return state.is_long, state.direction
        if direction == "short":
            return state.is_short, state.direction
        return False, state.direction
