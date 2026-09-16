"""W2.4 named-ablation registry: overlays, fingerprints, and harness stamping.

Every replay run must be a pure config overlay on a fresh baseline: these tests
pin (a) exactly which config fields each named ablation moves, (b) that the
baseline config is never mutated, (c) the checklist-level behavior of each
gate ablation, and (d) that the walker/outcome pipeline stamps ablation name +
config fingerprint on every record.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from tradingagents.mes.backtest import (
    ABLATIONS,
    BASELINE_NAME,
    apply_ablation,
    config_delta,
    config_fingerprint,
    get_ablation,
    list_ablations,
    load_session,
)
from tradingagents.mes.backtest.ablations import ENGINE_DEFAULTS
from tradingagents.mes.backtest.outcomes import join_outcomes
from tradingagents.mes.checklist import evaluate
from tradingagents.mes.config import MesChecklistConfig, _TUNER_FIELD_ALIASES, load_mes_config

from tests.mes_factories import find_item, make_snapshot, make_spy_series

FIXTURES = Path(__file__).parent / "fixtures"
MES_CSV = FIXTURES / "mes_sample_5m.csv"
SPY_CSV = FIXTURES / "spy_sample_5m.csv"

#: Plan order — the registry must present ablations in this sequence.
PLAN_ORDER = [
    "internals-off",
    "spy-gate-off",
    "divergence-veto-off",
    "engine-thresholds",
    "dynamic-threshold-off",
]

CHECKLIST_ABLATIONS = [name for name in PLAN_ORDER if name != "engine-thresholds"]


def _internals(add=300.0, tick=100.0, vold=1000.0, count=6):
    return [(add, tick, vold)] * count


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_registry_lists_all_plan_ablations_in_order():
    names = [ablation.name for ablation in list_ablations()]
    assert names == PLAN_ORDER
    for ablation in list_ablations():
        assert ablation.question.strip(), f"{ablation.name} must state its question"
        assert ablation.base in {"checklist", "engine"}
        assert ablation.overlay, "every ablation must move at least one config field"
    assert BASELINE_NAME == "baseline"  # run label; Ablation.base is config provenance
    assert set(ABLATIONS) == set(PLAN_ORDER)


@pytest.mark.unit
@pytest.mark.parametrize("name", CHECKLIST_ABLATIONS)
def test_ablation_overlay_changes_exactly_its_declared_fields(name):
    base = MesChecklistConfig()
    ablated = apply_ablation(base, name)
    delta = config_delta(base, ablated)
    assert set(delta) == set(get_ablation(name).overlay)
    for field, change in delta.items():
        assert change["from"] == getattr(base, field)
        assert change["to"] == getattr(ablated, field)
    assert config_delta(base, base) == {}


@pytest.mark.unit
def test_apply_ablation_never_mutates_the_baseline_config():
    base = load_mes_config()
    before = dataclasses.asdict(base)
    for name in PLAN_ORDER:
        apply_ablation(base, name)
    assert dataclasses.asdict(base) == before


@pytest.mark.unit
def test_engine_overlay_promotes_defaults_through_the_alias_map():
    ablation = get_ablation("engine-thresholds")
    assert ablation.base == "engine"
    assert ablation.overlay == {
        _TUNER_FIELD_ALIASES[pascal]: value for pascal, value in ENGINE_DEFAULTS.items()
    }
    assert ablation.overlay["add_threshold"] == 250.0
    assert ablation.overlay["tick_threshold"] == 600.0
    assert ablation.overlay["use_dynamic_tick_threshold"] is False
    # Every engine knob must land on a real checklist field.
    assert set(ablation.overlay) <= {
        field.name for field in dataclasses.fields(MesChecklistConfig)
    }
    delta = config_delta(MesChecklistConfig(), apply_ablation(MesChecklistConfig(), "engine-thresholds"))
    assert delta and set(delta) <= set(ablation.overlay)


# ---------------------------------------------------------------------------
# Fingerprints & errors
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_config_fingerprint_is_stable_short_and_discriminating():
    cfg = MesChecklistConfig()
    fingerprint = config_fingerprint(cfg)
    assert config_fingerprint(MesChecklistConfig()) == fingerprint
    assert len(fingerprint) == 12
    assert all(char in "0123456789abcdef" for char in fingerprint)
    tweaked = dataclasses.replace(cfg, add_threshold=cfg.add_threshold + 1)
    assert config_fingerprint(tweaked) != fingerprint
    # Ablated runs must not share the baseline fingerprint.
    for name in PLAN_ORDER:
        assert config_fingerprint(apply_ablation(cfg, name)) != fingerprint, name


@pytest.mark.unit
def test_unknown_ablation_names_the_available_ones():
    with pytest.raises(ValueError, match=r"'no-such-ablation'.*internals-off"):
        get_ablation("no-such-ablation")
    with pytest.raises(ValueError, match="available"):
        apply_ablation(MesChecklistConfig(), "no-such-ablation")


# ---------------------------------------------------------------------------
# Checklist-level behavior of the gate ablations
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_spy_gate_off_auto_passes_the_confluence_gate():
    weak_spy = make_spy_series(internals=_internals(add=0.0, tick=0.0))
    baseline = evaluate(make_snapshot(spy=weak_spy), "long")
    assert baseline.spy_confluence_ok is False
    assert any("SPY confirmations" in reason for reason in baseline.no_trade_reasons)

    ablated_cfg = apply_ablation(load_mes_config(), "spy-gate-off")
    result = evaluate(
        make_snapshot(cfg=ablated_cfg, spy=make_spy_series(internals=_internals(add=0.0, tick=0.0))),
        "long",
    )
    assert result.spy_confluence_ok is True
    assert not any("SPY confirmations" in r for r in result.no_trade_reasons)


@pytest.mark.unit
def test_divergence_veto_off_stops_vetting_opposing_divergence():
    from datetime import timedelta

    from tests.mes_factories import DEFAULT_AS_OF, make_bar

    spy = make_spy_series(internals=[(300.0, 100.0, 0.0), (300.0, 100.0, -100.0)])
    spy.bars = [
        make_bar(DEFAULT_AS_OF - timedelta(minutes=5), close=500.0, vold=0.0, add=300.0, tick=100.0),
        make_bar(DEFAULT_AS_OF, close=501.0, vold=-100.0, add=300.0, tick=100.0),
    ]
    cfg = load_mes_config()
    baseline = evaluate(make_snapshot(cfg=cfg, spy=spy), "long")
    assert any("diverging bearishly" in reason for reason in baseline.no_trade_reasons)

    ablated_cfg = apply_ablation(cfg, "divergence-veto-off")
    ablated = evaluate(make_snapshot(cfg=ablated_cfg, spy=spy), "long")
    assert not any("diverging" in reason for reason in ablated.no_trade_reasons)
    # The divergence is still reported informationally; only the veto is off.
    assert ablated.divergence == "bearish"


@pytest.mark.unit
def test_internals_off_skips_the_breadth_signal_items():
    cfg = apply_ablation(
        load_mes_config({"allow_missing_internals": True}), "internals-off"
    )
    snapshot = make_snapshot(cfg=cfg, spy=make_spy_series(internals=_internals(add=5000.0)))
    result = evaluate(snapshot, "long")
    add_item = find_item(result.mes_items, "$ADD confirms")
    assert add_item.note == "signal disabled"


# ---------------------------------------------------------------------------
# Harness stamping: walker records and joined outcomes
# ---------------------------------------------------------------------------

CSV_HEADER = "timestamp,open,high,low,close,volume,add,tick,vold\n"


def _write_csv(path: Path, rows: list[str]) -> Path:
    path.write_text(CSV_HEADER + "".join(rows), encoding="utf-8")
    return path


def _rows(date: str, count: int, base: float = 6450.0) -> list[str]:
    """Consecutive 5-minute bar rows (tz-stamped like the recorder writes them)."""
    return [
        f"{date}T{10 + (5 * i) // 60:02d}:{(5 * i) % 60:02d}:00-0400,"
        f"{base},{base + 2},{base - 2},{base + 0.5},1000,1718.0,754.0,44394963.0\n"
        for i in range(count)
    ]


@pytest.fixture()
def session_files(tmp_path):
    mes = _write_csv(tmp_path / "mes_2026-03-25.csv", _rows("2026-03-25", 8))
    spy = _write_csv(tmp_path / "spy_2026-03-25.csv", _rows("2026-03-25", 6, base=640.0))
    return mes, spy


@pytest.mark.unit
def test_load_session_labels_baseline_by_default(session_files):
    mes, spy = session_files
    session = load_session(mes, spy)
    assert session.ablation == BASELINE_NAME == "baseline"
    for record in session.walk().records:
        assert record.ablation == "baseline"
        assert record.config_fingerprint == config_fingerprint(session.cfg)


@pytest.mark.unit
@pytest.mark.parametrize("name", CHECKLIST_ABLATIONS)
def test_walked_records_carry_ablation_name_and_fingerprint(name, session_files):
    mes, spy = session_files
    run_cfg = apply_ablation(load_mes_config(), name)
    session = load_session(mes, spy, run_cfg, ablation=name)
    records = session.walk().records
    assert records and session.ablation == name
    expected_fingerprint = config_fingerprint(run_cfg)
    assert all(record.ablation == name for record in records)
    assert {record.config_fingerprint for record in records} == {expected_fingerprint}


@pytest.mark.unit
def test_cli_rearm_pattern_relabels_without_reloading_bars(session_files):
    """The CLI's `dataclasses.replace` re-arm shares bars and relabels stamps."""
    mes, spy = session_files
    template = load_session(mes, spy)
    bar_lists = (template.mes_bars, template.spy_bars)
    for name in PLAN_ORDER:
        run_cfg = apply_ablation(template.cfg, name)
        armed = dataclasses.replace(template, cfg=run_cfg, ablation=name)
        assert (armed.mes_bars, armed.spy_bars) == bar_lists  # bars shared, not re-parsed
        assert armed.ablation == name
        for record in armed.walk().records:
            assert record.ablation == name
            assert record.config_fingerprint == config_fingerprint(run_cfg)


@pytest.mark.unit
def test_join_outcomes_copies_stamping_from_records(session_files):
    mes, spy = session_files
    cfg = apply_ablation(load_mes_config(), "spy-gate-off")
    session = load_session(mes, spy, cfg, ablation="spy-gate-off")
    joined = join_outcomes(session.walk().records, session.mes_bars, cfg)
    assert joined.outcomes, "fixture must produce at least one outcome to assert on"
    for outcome in joined.outcomes:
        assert outcome.ablation == "spy-gate-off"
        assert outcome.config_fingerprint == config_fingerprint(cfg)


@pytest.mark.unit
def test_join_outcomes_falls_back_to_config_fingerprint_when_record_is_unstamped(session_files):
    mes, spy = session_files
    session = load_session(mes, spy)
    records = [
        dataclasses.replace(record, ablation="", config_fingerprint="")
        for record in session.walk().records
    ]
    cfg = load_mes_config()
    joined = join_outcomes(records, session.mes_bars, cfg)
    assert joined.outcomes
    for outcome in joined.outcomes:
        assert outcome.config_fingerprint == config_fingerprint(cfg)
