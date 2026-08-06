from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tradingagents.intraday.mtf_validator import MTFValidationResult
from tradingagents.intraday.session import DailyBiasReport
from tradingagents.intraday.strategy import StrategyResult


@dataclass
class GateResult:
    passed: bool
    gate1_strategy: bool
    gate1_reason: str
    gate2_mtf_alignment: bool
    gate2_reason: str
    final_direction: Literal["long", "short", "none"]


def resolve_require_daily_bias_alignment(config: dict) -> bool:
    """Return whether Gate 2 (30m trend vs daily bias) should run.

    ``intraday_require_daily_bias_alignment`` is the master switch. When it is
    true, ``orb_breakout`` + screener disables Gate 2 by default because
    screener-added symbols use neutral daily bias unless pre-market is run.
    Set ``intraday_orb_breakout_screener_disable_gate2`` to false to keep Gate 2.
    """
    if not bool(config.get("intraday_require_daily_bias_alignment", True)):
        return False

    if not bool(config.get("intraday_orb_breakout_screener_disable_gate2", True)):
        return True

    strategy = str(config.get("intraday_strategy", "base_momentum"))
    screener_enabled = bool(config.get("intraday_screener_enabled", False))
    if strategy == "orb_breakout" and screener_enabled:
        return False

    return True


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

        require_alignment = resolve_require_daily_bias_alignment(config)
        gate2_pass = self._mtf_aligns_with_bias(mtf, daily_bias, direction)
        if require_alignment and not gate2_pass:
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
            )

        gate2_reason = (
            "MTF alignment satisfied" if require_alignment else "MTF alignment check disabled"
        )
        return GateResult(
            passed=True,
            gate1_strategy=True,
            gate1_reason=gate1_reason,
            gate2_mtf_alignment=True,
            gate2_reason=gate2_reason,
            final_direction=direction,
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
