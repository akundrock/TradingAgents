from __future__ import annotations

import logging

from tradingagents.intraday.screener_filters.base import (
    FilterMode,
    FilterResult,
    ScanContext,
    ScanFilter,
    ScreenedSymbol,
    combine_filter_results,
    filter_result_to_screened,
)
from tradingagents.intraday.screener_filters.orb_filter import OrbFilter
from tradingagents.intraday.screener_filters.rrs_filter import RrsFilter

logger = logging.getLogger(__name__)

FILTER_REGISTRY: dict[str, type[ScanFilter]] = {
    "orb": OrbFilter,
    "rrs": RrsFilter,
}


def get_filter(name: str) -> ScanFilter:
    key = name.strip().lower()
    if key not in FILTER_REGISTRY:
        raise ValueError(f"Unknown screener filter: {name}. Available: {list(FILTER_REGISTRY)}")
    return FILTER_REGISTRY[key]()


def resolve_filter_names(config: dict) -> list[str]:
    raw = config.get("intraday_screener_filters")
    if raw is None:
        return ["rrs"]
    if isinstance(raw, str):
        names = [part.strip().lower() for part in raw.split(",") if part.strip()]
    else:
        names = [str(part).strip().lower() for part in raw if str(part).strip()]
    if not names:
        return ["rrs"]
    return names


def resolve_filter_pipeline(config: dict) -> list[ScanFilter]:
    return [get_filter(name) for name in resolve_filter_names(config)]


def resolve_filter_mode(config: dict) -> FilterMode:
    raw = str(config.get("intraday_screener_filter_mode", "any")).strip().lower()
    if raw == "all":
        return "all"
    return "any"


def uses_rrs_filter(config: dict) -> bool:
    return "rrs" in resolve_filter_names(config)


__all__ = [
    "FILTER_REGISTRY",
    "FilterMode",
    "FilterResult",
    "OrbFilter",
    "RrsFilter",
    "ScanContext",
    "ScanFilter",
    "ScreenedSymbol",
    "combine_filter_results",
    "filter_result_to_screened",
    "get_filter",
    "resolve_filter_mode",
    "resolve_filter_names",
    "resolve_filter_pipeline",
    "uses_rrs_filter",
]
