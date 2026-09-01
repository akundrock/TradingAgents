"""Volume profile for prior-session levels (POC / value area).

Built from 5m bars rather than tick data, so volume is spread evenly across each
bar's high-low span. That is coarse next to a true TPO profile but is enough to
mark the levels the pre-market routine asks for.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_VALUE_AREA = 0.70


@dataclass
class VolumeProfile:
    poc: float
    vah: float
    val: float


def volume_profile(
    bars,
    tick_size: float,
    *,
    value_area: float = DEFAULT_VALUE_AREA,
) -> VolumeProfile | None:
    """Point of control and value-area bounds, or ``None`` when bars are unusable."""
    if not bars or tick_size <= 0:
        return None

    buckets: dict[int, float] = {}
    for bar in bars:
        low, high = min(bar.low, bar.high), max(bar.low, bar.high)
        first, last = int(low / tick_size), int(high / tick_size)
        span = last - first + 1
        if span <= 0:
            continue
        share = (bar.volume or 0.0) / span
        for level in range(first, last + 1):
            buckets[level] = buckets.get(level, 0.0) + share

    total = sum(buckets.values())
    if not buckets or total <= 0:
        return None

    poc_level = max(buckets, key=lambda level: buckets[level])
    target = total * value_area

    # Grow outward from the POC, always taking the heavier adjacent level.
    low_level = high_level = poc_level
    covered = buckets[poc_level]
    while covered < target and (low_level - 1 in buckets or high_level + 1 in buckets):
        below = buckets.get(low_level - 1, 0.0)
        above = buckets.get(high_level + 1, 0.0)
        if above >= below:
            high_level += 1
            covered += above
        else:
            low_level -= 1
            covered += below

    return VolumeProfile(
        poc=poc_level * tick_size,
        vah=high_level * tick_size,
        val=low_level * tick_size,
    )
