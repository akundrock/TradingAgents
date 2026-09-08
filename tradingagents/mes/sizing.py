"""Position sizing for /MES, mirroring MES_PositionSizer.tos."""

from __future__ import annotations

from dataclasses import dataclass

from .checklist import tier_contract_band
from .config import MesChecklistConfig


@dataclass
class SizingResult:
    contracts: int
    risk_dollars: float
    stop_points: float
    risk_per_contract: float
    tier: str
    tier_band: tuple[int, int]
    capped_by: str


def size_position(
    risk_dollars: float,
    stop_points: float,
    tier: str,
    cfg: MesChecklistConfig,
) -> SizingResult:
    """Contracts = risk$ / (stop points x $5), then clamped to the tier band."""
    if stop_points <= 0:
        raise ValueError("stop_points must be positive")

    risk_per_contract = stop_points * cfg.contract_multiplier
    raw = int(risk_dollars // risk_per_contract)
    band_low, band_high = tier_contract_band(tier)

    contracts = min(raw, cfg.max_contracts)
    capped_by = "risk" if contracts == raw else "max_contracts"
    if band_high and contracts > band_high:
        contracts, capped_by = band_high, f"{tier} tier band"
    if band_high == 0:
        contracts, capped_by = 0, "tier below marginal"
    elif contracts < band_low:
        capped_by = "risk (below tier band)"

    return SizingResult(
        contracts=max(0, contracts),
        risk_dollars=risk_dollars,
        stop_points=stop_points,
        risk_per_contract=risk_per_contract,
        tier=tier,
        tier_band=(band_low, band_high),
        capped_by=capped_by,
    )


def suggest_stop_distance_points(result_side: str, last_price: float, vwap: float, atr: float) -> float:
    """Structural stop DISTANCE in points (not a price): the further of VWAP
    invalidation or 1 ATR. Callers convert to a price via ``entry ± distance``."""
    vwap_distance = abs(last_price - vwap)
    return round(max(vwap_distance, atr, 1.0), 2)
