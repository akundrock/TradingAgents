from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from tradingagents.intraday.session import DailyBiasReport


@dataclass
class StrategyResult:
    passed: bool
    direction: Literal["long", "short", "none"]
    reason: str
    factors_met: list[str]
    factors_missing: list[str]


class IntradayStrategy(Protocol):
    name: str

    def check_setup(
        self,
        symbol: str,
        mtf: object,
        daily_bias: DailyBiasReport,
    ) -> StrategyResult: ...

    def describe(self) -> str: ...


def render_strategy_summary(
    strategy: dict | None,
    *,
    strategy_name: str = "unknown",
) -> str:
    if not strategy:
        return "Intraday strategy signal unavailable."

    name = str(strategy.get("name", strategy_name))
    direction = str(strategy.get("direction", "none"))
    reason = str(strategy.get("reason", ""))
    met = strategy.get("factors_met") or []
    missing = strategy.get("factors_missing") or []

    lines = [
        f"Strategy: {name}",
        f"Direction: {direction}",
        f"Reason: {reason}",
    ]
    if met:
        lines.append(f"Factors met: {', '.join(met)}")
    if missing:
        lines.append(f"Factors missing: {', '.join(missing)}")
    return "\n".join(lines)
