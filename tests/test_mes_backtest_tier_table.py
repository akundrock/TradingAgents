"""W2.3 tier→outcome table tests: grouping, sample floor, gate semantics."""

import pytest

from tradingagents.mes.backtest import (
    TradeOutcome,
    build_tier_table,
    evaluate_gate,
    format_tier_table,
)


def _outcome(
    *,
    tier: str = "premium",
    side: str = "long",
    realized_r: float = 1.0,
    mfe_r: float = 1.0,
    mae_r: float = -0.5,
    hold_bars: int = 3,
    session_date: str = "2026-03-25",
) -> TradeOutcome:
    return TradeOutcome(
        session_date=session_date,
        signal_timestamp=f"{session_date}T11:00",
        entry_timestamp=f"{session_date}T11:05",
        exit_timestamp=f"{session_date}T11:20",
        side=side,
        tier=tier,
        entry_price=100.0,
        stop=99.0,
        initial_stop=99.0,
        target=None,
        initial_risk_points=1.0,
        exit_price=100.0 + realized_r,
        exit_reason="target" if realized_r > 0 else "stop",
        realized_r=realized_r,
        mfe_r=mfe_r,
        mae_r=mae_r,
        hold_bars=hold_bars,
    )


@pytest.mark.unit
def test_groups_by_tier_and_side():
    outcomes = [
        _outcome(tier="premium", side="long"),
        _outcome(tier="premium", side="long"),
        _outcome(tier="premium", side="short"),
        _outcome(tier="standard", side="long"),
        _outcome(tier="marginal", side="short"),
    ]
    table = build_tier_table(outcomes)

    assert table.cell("premium", "long").n == 2
    assert table.cell("premium", "short").n == 1
    assert table.cell("standard", "long").n == 1
    assert table.cell("marginal", "short").n == 1
    assert table.cell("standard", "short") is None  # no such closed trades


@pytest.mark.unit
def test_sample_floor_hides_percentages():
    table = build_tier_table([_outcome(tier="standard") for _ in range(3)])
    cell = table.cell("standard", "long")

    assert cell.n == 3
    assert cell.hit_rate is None and cell.expectancy_r is None
    assert cell.median_mfe_r is None and cell.median_mae_r is None
    assert cell.median_hold_bars is None

    rendered = format_tier_table(table)
    assert "n<5 — not interpretable" in rendered
    assert "%" not in rendered  # never a bare percentage below the floor


@pytest.mark.unit
def test_hit_rate_expectancy_and_total_math():
    rs = [1.5, 1.5, 1.5, -1.0, -1.0, 2.0]  # 4 wins / 6 trades, sum = 4.5
    outcomes = [_outcome(tier="premium", realized_r=r) for r in rs]
    table = build_tier_table(outcomes)
    cell = table.cell("premium", "long")

    assert cell.n == 6
    assert cell.hit_rate == 4 / 6
    assert cell.expectancy_r == 4.5 / 6
    assert cell.total_r == 4.5
    assert table.expectancy("premium") == 0.75


@pytest.mark.unit
def test_medians_over_cell_trades():
    outcomes = [
        _outcome(mfe_r=1.0, mae_r=-0.2, hold_bars=2),
        _outcome(mfe_r=2.0, mae_r=-0.4, hold_bars=4),
        _outcome(mfe_r=3.0, mae_r=-0.9, hold_bars=9),
        _outcome(mfe_r=0.5, mae_r=-0.4, hold_bars=5),
        _outcome(mfe_r=2.5, mae_r=-0.8, hold_bars=7),
    ]
    cell = build_tier_table(outcomes).cell("premium", "long")

    assert cell.median_mfe_r == 2.0  # median of [1.0, 2.0, 3.0, 0.5, 2.5]
    assert cell.median_mae_r == -0.4
    assert cell.median_hold_bars == 5.0


@pytest.mark.unit
def test_gate_met_when_premium_beats_standard():
    premium = [_outcome(tier="premium", realized_r=1.0 + i * 0.1) for i in range(6)]
    standard = [_outcome(tier="standard", realized_r=-0.5) for _ in range(5)]
    table = build_tier_table(premium + standard, sessions=25)

    gate = table and __import__("tradingagents.mes.backtest", fromlist=["evaluate_gate"]).evaluate_gate(table)
    assert gate.met is True
    assert "GATE MET" in gate.message


@pytest.mark.unit
def test_gate_failure_is_the_headline_finding():
    premium = [_outcome(tier="premium", realized_r=-1.0) for _ in range(5)]
    standard = [_outcome(tier="standard", realized_r=1.0) for _ in range(5)]
    table = build_tier_table(premium + standard, sessions=30)

    from tradingagents.mes.backtest import evaluate_gate

    gate = evaluate_gate(table)
    assert gate.evaluated and gate.met is False
    assert "GATE FAILED" in gate.message


@pytest.mark.unit
def test_gate_not_evaluated_below_min_sessions():
    table = build_tier_table(
        [_outcome(tier="premium", realized_r=1.0), _outcome(tier="standard", realized_r=-1.0)],
        sessions=3,
    )
    gate = evaluate_gate(table)

    assert gate.evaluated is False and gate.met is None
    assert "20" in gate.message


@pytest.mark.unit
def test_fixture_outcomes_render_a_table():
    """End-to-end: walker -> joiner -> tier table on the real fixture session."""
    from pathlib import Path

    from tradingagents.mes.backtest import ReplaySession, join_outcomes
    from tradingagents.mes.config import load_mes_config
    from tradingagents.mes.snapshot import load_csv_bars

    fixtures = Path(__file__).parent / "fixtures"
    cfg = load_mes_config()
    session = ReplaySession(
        mes_bars=load_csv_bars(fixtures / "mes_sample_5m.csv"),
        spy_bars=load_csv_bars(fixtures / "spy_sample_5m.csv"),
        cfg=cfg,
    )
    walked = session.walk()
    report = join_outcomes(walked.records, session.mes_bars, cfg)

    table = build_tier_table(report.outcomes, sessions=1)
    rendered = format_tier_table(table)
    assert "tier | side | n" in rendered
    for outcome in report.outcomes:
        assert outcome.tier in rendered
