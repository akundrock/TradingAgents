"""W2.6 evidence-report emitter tests: sections, honesty, determinism.

``report.build_evidence_report`` is the pure formatter behind
``out/backtest-evidence.md`` — the single grep-able artifact Phase 3 sweeps and
the W0.4 scorecard re-score consume. These tests pin the section spec:

* every grep-able heading present, in order, for a synthetic manifest +
  synthetic run_stats (no engine involved);
* exclusion reasons render verbatim (never silently dropped);
* the ``n<5`` sample floor renders not-interpretable (never a bare percentage);
* an honest ``gate.met is False`` failure is the headline, not buried;
* the parity line is emitted verbatim;
* fingerprints + git SHA appear;
* identical inputs → byte-identical markdown (no wall clock inside);
* plain markdown only — no Rich/ANSI escape codes leak in.

W2.6.3 cross-check tests: the TOS order-history parser normalizes M/D/YY dates,
keeps only filled MES rows, and tolerates malformed rows with a counted warning
(never raised).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.mes import mes_app
from tests.test_mes_ablations import _rows, _write_csv
from tests.test_mes_backtest_tier_table import _outcome
from tradingagents.mes.backtest import (
    SessionCandidate,
    build_tier_table,
    evaluate_gate,
    format_tier_table,
    join_outcomes,
    load_session,
)
from tradingagents.mes.backtest.ablations import config_fingerprint
from tradingagents.mes.backtest.report import (
    PARITY_STATUS,
    build_evidence_report,
    load_order_history,
)
from tradingagents.mes.config import MesChecklistConfig

# ---------------------------------------------------------------------------
# Synthetic fixtures (no wall clock, no engine)
# ---------------------------------------------------------------------------


def _candidate(
    date: str,
    mes_bars: int = 174,
    spy_bars: int | None = 160,
    excluded: str | None = None,
) -> SessionCandidate:
    return SessionCandidate(
        date=date,
        mes_path=Path(f"data/mes_{date}.csv"),
        spy_path=None if spy_bars is None else Path(f"data/spy_{date}.csv"),
        mes_bars=mes_bars,
        spy_bars=spy_bars or 0,
        excluded=excluded,
    )


def _run_stats(**overrides) -> dict:
    """One baseline-shaped run_stats entry over synthetic outcomes.

    Sessions is set to ``GATE_MIN_SESSIONS`` so the gate evaluates; premium
    loses to standard on purpose so the honest-failure rendering is exercised.
    """
    outcomes = [
        *[_outcome(tier="premium", realized_r=-0.5) for _ in range(5)],
        *[_outcome(tier="standard", realized_r=1.0) for _ in range(3)],
    ]
    table = build_tier_table(outcomes, sessions=20)
    gate = evaluate_gate(table)
    cfg = MesChecklistConfig()
    stats = {
        "config_fingerprint": config_fingerprint(cfg),
        "config_delta": {},
        "records": 200,
        "tradeable": 8,
        "trades": len(outcomes),
        "wins": sum(1 for outcome in outcomes if outcome.realized_r > 0),
        "total_r": round(sum(outcome.realized_r for outcome in outcomes), 4),
        "avg_r": 0.5,
        "tier_table": table,
        "gate": gate,
        "tradeable_by_date": {"2026-03-25": 5, "2026-03-26": 3},
    }
    stats.update(overrides)
    return stats


def _manifest() -> list[SessionCandidate]:
    return [
        _candidate("2026-03-25", mes_bars=174, spy_bars=170),
        _candidate(
            "2026-03-26",
            mes_bars=10,
            spy_bars=None,
            excluded=("no same-date spy_*.csv recording (SPY half of the checklist cannot run)"),
        ),
    ]


def _report(**overrides) -> str:
    kwargs = {
        "manifest": _manifest(),
        "run_stats": {"baseline": _run_stats()},
        "parity_status": PARITY_STATUS,
        "git_sha": "abc1234def",
        "generated_at": "2026-09-15T12:00:00+00:00",
        "data_dir": "../thinkorswim-scripts/tos-market-data/data",
        "wall_clock_seconds": 42.5,
    }
    kwargs.update(overrides)
    return build_evidence_report(**kwargs)


# ---------------------------------------------------------------------------
# Section spec (W2.6.1)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_all_sections_present_in_order():
    report = _report()
    headings = [line for line in report.splitlines() if line.startswith("#")]
    assert headings == [
        "# MES backtest evidence — full corpus",
        "## Coverage manifest",
        "## Cross-check (live fills)",
        "## Tier × side — baseline",
        "## Ablation deltas",
        "## Parity status (W2.5)",
        "## Known limitations",
    ]


@pytest.mark.unit
def test_header_carries_generated_at_sha_corpus_and_wall_clock():
    report = _report()
    assert "generated 2026-09-15T12:00:00+00:00" in report
    assert "git abc1234def" in report
    assert "../thinkorswim-scripts/tos-market-data/data" in report
    assert "sessions walked: 1 / excluded: 1" in report
    assert "wall-clock 42.5s" in report


@pytest.mark.unit
def test_coverage_lists_every_session_and_keeps_reasons_verbatim():
    manifest = _manifest()
    report = _report(manifest=manifest)
    assert "| 2026-03-25 | 174 | 170 | included |" in report
    assert "| 2026-03-26 | 10 | 0 | excluded |" in report
    # the reason text appears verbatim, never paraphrased or dropped
    assert manifest[1].excluded in report
    assert "sessions included: 1 / excluded: 1" in report


@pytest.mark.unit
def test_n_below_floor_renders_not_interpretable_never_a_bare_percentage():
    report = _report()
    assert "n<5 — not interpretable" in report
    # the below-floor cell (standard × long, n=3) never renders a bare percentage
    below_floor_row = next(
        line for line in report.splitlines() if line.startswith("| standard |") and "n<5" in line
    )
    assert "%" not in below_floor_row
    assert "n<5 — not interpretable" in below_floor_row


@pytest.mark.unit
def test_honest_gate_failure_is_headline_not_buried():
    report = _report()
    assert "GATE FAILED" in report  # evaluate_gate met=False on the synthetic stats
    baseline_section = report.split("## Tier × side — baseline")[1].split("## Ablation")[0]
    assert "GATE FAILED" in baseline_section


@pytest.mark.unit
def test_parity_line_emitted_verbatim():
    report = _report()
    assert PARITY_STATUS in report
    assert PARITY_STATUS in report.split("## Parity status (W2.5)")[1]
    assert "azp-phase2-w25-parity-spot-check-plan.md" in report
    assert "red-by-design" in PARITY_STATUS.lower()
    assert "unexplained=3" in PARITY_STATUS


@pytest.mark.unit
def test_fingerprint_and_git_sha_present():
    report = _report()
    assert _run_stats()["config_fingerprint"] in report
    assert "git abc1234def" in report


@pytest.mark.unit
def test_deterministic_builds_are_byte_identical():
    assert _report() == _report()


@pytest.mark.unit
def test_report_is_plain_markdown_no_rich_or_ansi():
    report = _report()
    assert "\x1b[" not in report
    for rich_token in ("[bold", "[/bold]", "[green]", "[red]", "[yellow]", "[cyan]"):
        assert rich_token not in report


@pytest.mark.unit
def test_ablation_deltas_render_per_overlay_anchored_on_baseline():
    cfg = MesChecklistConfig()
    ablated = _run_stats(
        records=180,
        tradeable=6,
        config_fingerprint=config_fingerprint(cfg),
        config_delta={"enable_spy_context": {"from": True, "to": False}},
    )
    report = _report(
        run_stats={"baseline": _run_stats(), "spy-gate-off": ablated},
    )
    ablation_section = report.split("## Ablation deltas")[1].split("## Parity")[0]
    assert "### spy-gate-off" in ablation_section
    assert "enable_spy_context True→False" in ablation_section
    assert "anchored on baseline" in report
    # deltas are anchored on baseline: records 180-200 and tradeable 6-8
    assert "−20" in ablation_section or "-20" in ablation_section
    assert "-2" in ablation_section


# ---------------------------------------------------------------------------
# Cross-check parser (W2.6.3)
# ---------------------------------------------------------------------------

_ORDER_CSV = (
    "orderId,B/S,Contract,Status,Fill Time,Date\n"
    "101, Buy,MESM6, Filled,05/05/2026 09:20:39,5/5/26\n"
    "102, Sell,MESM6, Filled,05/05/2026 09:57:16,5/5/26\n"
    "103, Buy,MESM6, Filled,05/06/2026 11:34:28,5/6/26\n"
)


def _write_order_csv(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "order-history.csv"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.unit
def test_order_history_happy_path_counts_buys_and_sells(tmp_path):
    fills = load_order_history(_write_order_csv(tmp_path, _ORDER_CSV))
    assert [f["date"] for f in fills] == ["2026-05-05", "2026-05-05", "2026-05-06"]
    assert [f["side"] for f in fills] == ["buy", "sell", "buy"]


@pytest.mark.unit
def test_order_history_malformed_rows_tolerated_with_counted_warning(tmp_path):
    messy = _ORDER_CSV.replace("103, Buy,MESM6, Filled,05/06/2026 11:34:28,5/6/26\n", "")
    messy += "104, Buy,MESM6, Filled,05/06/2026 12:00:00,not-a-date\n"
    messy += "105,,,,,\n"
    path = _write_order_csv(tmp_path, messy)
    with pytest.warns(UserWarning, match="2 malformed"):
        fills = load_order_history(path)
    assert len(fills) == 2  # tolerated, never raised


@pytest.mark.unit
def test_order_history_normalizes_m_d_yy_to_iso_date(tmp_path):
    path = _write_order_csv(tmp_path, _ORDER_CSV)
    fills = load_order_history(path)
    assert {f["date"] for f in fills} == {"2026-05-05", "2026-05-06"}


@pytest.mark.unit
def test_order_history_skips_unfilled_and_non_mes_rows(tmp_path):
    extra = _ORDER_CSV + (
        "106, Buy,SPXW6, Filled,05/06/2026 10:00:00,5/6/26\n"  # wrong contract
        "107, Sell,MESM6, Working,05/06/2026 10:05:00,5/6/26\n"  # not filled
    )
    fills = load_order_history(_write_order_csv(tmp_path, extra))
    assert len(fills) == 3


@pytest.mark.unit
def test_cross_check_section_context_not_gate():
    fills = [
        {"date": "2026-03-25", "side": "buy"},
        {"date": "2026-03-25", "side": "sell"},
    ]
    report = _report(order_history=fills)
    cross = report.split("## Cross-check (live fills)")[1].split("## Tier")[0]
    assert "| 2026-03-25 |" in cross
    assert "2" in cross  # live fill count rendered
    assert "not a pass/fail gate" in report


@pytest.mark.unit
def test_cross_check_skipped_line_without_order_history():
    report = _report(order_history=None)
    assert "cross-check skipped — no order history" in report


# ---------------------------------------------------------------------------
# CLI wiring (W2.6.2): `mes replay --report PATH` reuses the run's in-memory
# structures and writes the file; no --report → nothing written; the report's
# tier table matches an independent rebuild of the same tiny corpus (the W2.4
# synthetic-fixture pattern).
# ---------------------------------------------------------------------------

runner = CliRunner()


def _tiny_corpus(tmp_path: Path) -> Path:
    """One walkable session (8 MES bars + 6 SPY bars), W2.4 fixture style."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_csv(data_dir / "mes_2026-03-25.csv", _rows("2026-03-25", 8))
    _write_csv(data_dir / "spy_2026-03-25.csv", _rows("2026-03-25", 6, base=640.0))
    return data_dir


def _invoke_replay(*args: str):
    return runner.invoke(mes_app, ["replay", *args])


@pytest.mark.unit
def test_replay_report_flag_writes_file_and_creates_out_dir(tmp_path):
    _tiny_corpus(tmp_path)
    report_path = tmp_path / "out" / "backtest-evidence.md"
    result = _invoke_replay(
        "--data-dir",
        str(tmp_path / "data"),
        "--min-bars",
        "2",
        "--report",
        str(report_path),
    )
    assert result.exit_code == 0, result.output
    assert report_path.exists(), "--report must write the file"
    assert report_path.parent.is_dir(), "--report creates the parent dir"
    assert "Wrote evidence report to" in result.output
    assert "# MES backtest evidence — full corpus" in report_path.read_text()
    assert "## Coverage manifest" in report_path.read_text()


@pytest.mark.unit
def test_replay_without_report_flag_writes_nothing(tmp_path):
    _tiny_corpus(tmp_path)
    result = _invoke_replay(
        "--data-dir",
        str(tmp_path / "data"),
        "--min-bars",
        "2",
    )
    assert result.exit_code == 0, result.output
    assert not (tmp_path / "out").exists(), "no --report → no report dir, no file"


@pytest.mark.unit
def test_replay_report_tier_counts_match_console_tier_table(tmp_path):
    data_dir = _tiny_corpus(tmp_path)
    report_path = tmp_path / "out" / "backtest-evidence.md"
    result = _invoke_replay(
        "--data-dir",
        str(data_dir),
        "--min-bars",
        "2",
        "--report",
        str(report_path),
    )
    assert result.exit_code == 0, result.output

    # Independent rebuild of the same session (no engine shared with the CLI).
    session = load_session(
        tmp_path / "data" / "mes_2026-03-25.csv",
        tmp_path / "data" / "spy_2026-03-25.csv",
    )
    joined = join_outcomes(session.walk().records, session.mes_bars, session.cfg)
    expected = format_tier_table(build_tier_table(joined.outcomes, sessions=1))
    report = report_path.read_text()
    baseline_section = report.split("## Tier × side — baseline")[1].split("\n## ")[0]
    assert expected in baseline_section
