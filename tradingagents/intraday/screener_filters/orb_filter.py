from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from tradingagents.intraday.indicators.opening_range import EntryMode, compute_opening_range
from tradingagents.intraday.screener_filters.base import FilterResult, ScanContext

logger = logging.getLogger(__name__)


class OrbFilter:
    name = "orb"

    def prepare(
        self,
        bar_time: datetime,
        config: dict,
        *,
        session_start: datetime,
        session: object | None = None,
    ) -> None:
        return None

    def evaluate(self, ctx: ScanContext) -> FilterResult:
        config = ctx.config
        tz = str(config.get("intraday_timezone", "America/New_York"))
        entry_mode: EntryMode = str(
            config.get("screener_orb_entry_mode", config.get("pro_trader_entry_mode", "wick_touch"))
        )  # type: ignore[assignment]
        direction = str(config.get("screener_orb_direction", config.get("intraday_screener_direction", "long")))
        require_beyond = bool(config.get("screener_orb_require_price_beyond", True))
        min_range = float(config.get("screener_orb_min_range_width", 0.0))

        or_state = compute_opening_range(
            ctx.df_5m,
            ctx.bar_time,
            timezone=tz,
            entry_mode=entry_mode,
        )

        if or_state is None:
            return FilterResult(
                passed=False,
                direction="none",
                score=0.0,
                factors_met=[],
                factors_missing=["opening_range_defined"],
                metadata={"filter": self.name},
                reject_reason=f"orb:{ctx.symbol} no_opening_range",
            )

        if min_range > 0 and or_state.range_width < min_range:
            return FilterResult(
                passed=False,
                direction="none",
                score=0.0,
                factors_met=[],
                factors_missing=["min_range_width"],
                metadata={"filter": self.name, "range_width": or_state.range_width},
                reject_reason=f"orb:{ctx.symbol} range={or_state.range_width:.2f}<{min_range:.2f}",
            )

        close = float(ctx.df_5m.iloc[-1].get("Close", ctx.df_5m.iloc[-1].get("close", 0.0)))
        directions = _resolve_directions(direction)
        best: FilterResult | None = None

        for dir_choice in directions:
            result = _evaluate_direction(
                ctx.symbol,
                dir_choice,
                or_state,
                close,
                require_beyond=require_beyond,
            )
            if not result.passed:
                continue
            if best is None or abs(result.score) > abs(best.score):
                best = result

        if best is not None:
            return best

        return FilterResult(
            passed=False,
            direction="none",
            score=0.0,
            factors_met=[],
            factors_missing=["orb_breakout"],
            metadata={
                "filter": self.name,
                "orh": or_state.opening_range_high,
                "orl": or_state.opening_range_low,
                "bullish_orb": or_state.bullish_orb,
                "bearish_orb": or_state.bearish_orb,
            },
            reject_reason=f"orb:{ctx.symbol} no_breakout",
        )


def _resolve_directions(direction: str) -> list[Literal["long", "short"]]:
    if direction == "both":
        return ["long", "short"]
    if direction == "short":
        return ["short"]
    return ["long"]


def _evaluate_direction(
    symbol: str,
    direction: Literal["long", "short"],
    or_state: Any,
    close: float,
    *,
    require_beyond: bool,
) -> FilterResult:
    checks: dict[str, bool] = {"opening_range_defined": True, "entry_window": or_state.in_entry_window}

    if direction == "long":
        checks["orb_breakout"] = or_state.bullish_orb
        if require_beyond:
            checks["price_beyond_or"] = close > or_state.opening_range_high
        score = close - or_state.opening_range_high
    else:
        checks["orb_breakout"] = or_state.bearish_orb
        if require_beyond:
            checks["price_beyond_or"] = close < or_state.opening_range_low
        score = or_state.opening_range_low - close

    met = [k for k, ok in checks.items() if ok]
    missing = [k for k, ok in checks.items() if not ok]
    passed = len(missing) == 0

    return FilterResult(
        passed=passed,
        direction=direction if passed else "none",
        score=score if passed else 0.0,
        factors_met=met,
        factors_missing=missing,
        metadata={
            "filter": "orb",
            "orh": or_state.opening_range_high,
            "orl": or_state.opening_range_low,
            "range_width": or_state.range_width,
            "bullish_orb": or_state.bullish_orb,
            "bearish_orb": or_state.bearish_orb,
            "close": close,
        },
        reject_reason=None if passed else f"orb:{symbol} {direction} missing={','.join(missing)}",
    )
