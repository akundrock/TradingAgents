"""Named ablation overlays for the replay harness (AZP Phase 2 W2.4).

Each ablation is one named, diffable :class:`MesChecklistConfig` overlay that
answers one question from the replay-harness plan, applied over the baseline
config with :func:`apply_ablation` (a fresh ``dataclasses.replace`` — the
checklist's defaults are never mutated):

- ``internals-off`` — is the breadth complex additive evidence, or
  pseudo-confluence with price-vs-VWAP (scorecard §87)? (base: checklist)
- ``spy-gate-off`` — is the MES half alone enough of an edge, or is the
  SPY-context gate doing real protective work? (base: checklist)
- ``divergence-veto-off`` — does the opposite-direction $VOLD divergence veto
  protect expectancy, or does it just keep good trades out? (base: checklist)
- ``engine-thresholds`` — do the two threshold sets (mes-tuner engine defaults
  vs the playbook checklist values) differ in outcomes? (base: engine)
- ``dynamic-threshold-off`` — is the TOS-dashboard dynamic ±$TICK threshold
  adaptive value, or a risk amplifier? (base: checklist)

Guarantees (plan rule 1 + W2.4):

* Ablations are pure config overlays applied by the harness — the harness
  never re-implements checklist logic; it calls ``evaluate()`` unchanged.
* The checklist's defaults are never mutated: every run starts from a fresh
  baseline ``MesChecklistConfig`` (or a caller-supplied one) and derives an
  overlay copy via :func:`dataclasses.replace`.
* Where a needed toggle did not exist as config, minimal additive default-on
  fields were added to ``MesChecklistConfig`` (``enable_spy_context``,
  ``divergence_veto``); their default path is byte-identical to the historic
  checklist, which the untouched W2.1–W2.3 test suites prove.
* ``internals-off`` and ``dynamic-threshold-off`` need no new fields: they are
  overlays of toggles the checklist already honors.
* ``engine-thresholds`` promotes mes-tuner's ``StrategyConfig`` defaults
  through the shared ``_TUNER_FIELD_ALIASES`` map (the same aliases the W2.5
  parity test guards), so threshold drift between the repos surfaces as a
  config delta in the ablation report instead of a silent mismatch.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from tradingagents.mes.config import MesChecklistConfig, _TUNER_FIELD_ALIASES

#: The harness's no-ablation run label: what every record/outcome is stamped
#: with when the untouched playbook checklist runs. Distinct from
#: ``Ablation.base``, which names the *config provenance* an overlay is built
#: on ("checklist" = playbook defaults, "engine" = mes-tuner engine defaults).
BASELINE_NAME = "baseline"


@dataclass(frozen=True)
class Ablation:
    """One named ablation: a question plus the config overlay that answers it."""

    name: str
    question: str
    #: ``checklist`` = playbook defaults; ``engine`` = mes-tuner engine defaults.
    base: str
    #: ``MesChecklistConfig`` field -> overlaid value. Must not mutate any
    #: loaded config: :func:`apply_ablation` copies via ``dataclasses.replace``.
    overlay: dict[str, Any]


def _engine_overlay() -> dict[str, Any]:
    """Promote mes-tuner's ``StrategyConfig`` defaults into checklist field values.

    Looks each engine default up in the shared alias map (PascalCase ->
    snake_case), so the engine-thresholds overlay is always expressed through
    the same field mapping the W2.5 parity test uses, and fails loudly if an
    engine knob is renamed without updating the alias map.
    """
    overlay: dict[str, Any] = {}
    for pascal, value in ENGINE_DEFAULTS.items():
        field = _TUNER_FIELD_ALIASES.get(pascal)
        if field is None:
            raise ValueError(
                f"engine default {pascal!r} is not mapped by _TUNER_FIELD_ALIASES — "
                "update the alias map before trusting the engine-thresholds ablation"
            )
        overlay[field] = value
    return overlay


# Mirror of mes-tuner's ``StrategyConfig`` defaults (mes-tuner/mes_tuner/
# config.py), expressed in the PascalCase names the shared alias map expects
# and promoted through _engine_overlay(). Keep in sync with the tuner's
# defaults — W2.5's cross-repo parity test is the drift alarm in the other
# direction, and the engine-thresholds config delta in each report shows any
# drift that appears here.
ENGINE_DEFAULTS: dict[str, Any] = {
    "EnableMomentum": True,
    "EnableVWAP": True,
    "EnableATRRange": True,
    "EnablePattern": True,
    "EnableADD": True,
    "EnableTICK": True,
    "EnableVOLD": True,
    "EnableVolumeSurge": True,
    "SMALength": 5,
    "NFE": 13,
    "ATRLength": 14,
    "ATRMultiplier": 2.0,
    "VolumeLookback": 20,
    "VolumeMultiplier": 1.2,
    "WickToBodyRatio": 1.2,
    "MaxBodyToRangeRatio": 0.6,
    "RetracementPercent": 40.0,
    "PatternRequireVWAPSide": False,
    "AddThreshold": 250.0,
    "TickThreshold": 600.0,
    "VOLDThreshold": 0.0,
    "VOLDUseTrend": True,
    "UseDynamicThreshold": False,  # the one engine default the checklist tunes on
    "TickLookback": 20,
    "TickMultiplier": 1.5,
    "StreakBars": 3,
    "TickBurstMultiplier": 1.5,
    "VOLDZScoreLookback": 20,
    "MinConfirmations": 4,
    "EnableTierMinConfirmations": True,
    "MinConfirmationsMarginal": 3,
    "MinConfirmationsStandard": 4,
    "MinConfirmationsPremium": 5,
    "MinATRPoints": 0.0,
    "RequireRTH": True,
    "EnforceSessionFilters": True,
    "BlockMorningUntil": "09:45",
}


#: The ablation matrix, in plan order. Read via :func:`list_ablations`.
ABLATIONS: dict[str, Ablation] = {
    ablation.name: ablation
    for ablation in (
        Ablation(
            name="internals-off",
            question=(
                "is the breadth complex additive evidence, or pseudo-confluence "
                "with price-vs-VWAP (scorecard §87)?"
            ),
            base="checklist",
            # $ADD/$TICK/$VOLD removed from the MES scoring half only (the three
            # existing per-signal toggles the checklist already honors); the
            # SPY half and the no-trade vetoes stay, so this isolates scoring.
            overlay={"enable_add": False, "enable_tick": False, "enable_vold": False},
        ),
        Ablation(
            name="spy-gate-off",
            question=(
                "is the MES half alone enough of an edge, or is the SPY-context "
                "gate doing real protective work?"
            ),
            base="checklist",
            # W1.3 gate semantics: no SPY items, confluence auto-passes, no
            # SPY-derived no-trade vetoes (confirmations, exhaustion).
            overlay={"enable_spy_context": False},
        ),
        Ablation(
            name="divergence-veto-off",
            question=(
                "does the opposite-direction $VOLD divergence veto protect "
                "expectancy, or does it just keep good trades out?"
            ),
            base="checklist",
            overlay={"divergence_veto": False},
        ),
        Ablation(
            name="engine-thresholds",
            question=(
                "do the two threshold sets — mes-tuner engine defaults vs the "
                "playbook checklist values — differ in outcomes?"
            ),
            base="engine",
            overlay=_engine_overlay(),
        ),
        Ablation(
            name="dynamic-threshold-off",
            question=(
                "is the TOS MES_TICK_Panel dynamic ±$TICK threshold adaptive "
                "value, or a risk amplifier?"
            ),
            base="checklist",
            overlay={"use_dynamic_tick_threshold": False},
        ),
    )
}


def list_ablations() -> list[Ablation]:
    """All named ablations, in plan order."""
    return list(ABLATIONS.values())


def get_ablation(name: str) -> Ablation:
    """Return the named ablation, raising a helpful error for unknown names."""
    try:
        return ABLATIONS[name]
    except KeyError:
        known = ", ".join(ABLATIONS)
        raise ValueError(f"unknown ablation {name!r} (available: {known})") from None


def apply_ablation(cfg: MesChecklistConfig, name: str) -> MesChecklistConfig:
    """Return ``cfg`` with the named ablation's overlay applied, as a new object.

    The input config is never mutated — every replay run builds its own overlay
    copy on top of the same fresh baseline.
    """
    ablation = get_ablation(name)
    return dataclasses.replace(cfg, **ablation.overlay)


def config_delta(
    base: MesChecklistConfig, overlaid: MesChecklistConfig
) -> dict[str, dict[str, Any]]:
    """Fields that differ between two configs, as ``{field: {"from", "to"}}``.

    This is what makes each run diffable: the report can show exactly which
    knobs an ablation moved, and an empty delta proves a run was the baseline.
    """
    return {
        f.name: {
            "from": getattr(base, f.name),
            "to": getattr(overlaid, f.name),
        }
        for f in dataclasses.fields(MesChecklistConfig)
        if getattr(base, f.name) != getattr(overlaid, f.name)
    }


def config_fingerprint(cfg: MesChecklistConfig) -> str:
    """Short stable hash of a config's values, stamped onto every report line."""
    payload = json.dumps(cfg.to_dict(), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


