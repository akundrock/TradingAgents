from __future__ import annotations

from tradingagents.intraday.strategies.base_momentum import BaseMomentumStrategy
from tradingagents.intraday.strategies.pro_trader_dashboard import ProTraderDashboardStrategy
from tradingagents.intraday.strategy import IntradayStrategy

STRATEGY_REGISTRY: dict[str, type[IntradayStrategy]] = {
    "base_momentum": BaseMomentumStrategy,
    "pro_trader_dashboard": ProTraderDashboardStrategy,
}


def get_strategy(name: str) -> IntradayStrategy:
    if name not in STRATEGY_REGISTRY:
        raise ValueError(
            f"Unknown strategy: {name}. Available: {list(STRATEGY_REGISTRY)}"
        )
    return STRATEGY_REGISTRY[name]()
