from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from tradingagents.intraday.scanner import write_signal_csv
from tradingagents.intraday.session import IntradaySignal


@pytest.mark.unit
def test_signal_csv_includes_swing_columns(tmp_path):
    signal = IntradaySignal(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        action="BUY",
        direction="long",
        entry_price=100.5,
        stop_loss=98.0,
        confidence="Strong",
        setup_score=5,
        gate_summary="G1=True G2=True",
        reasoning="r",
        option_structure="Long call, ~0.70 delta, 21 DTE",
        hold_horizon_days="1",
    )
    write_signal_csv(tmp_path, signal)

    csv_path = Path(str(tmp_path)) / "signals.csv"
    rows = csv_path.read_text().strip().splitlines()
    assert "option_structure" in rows[0]
    assert "hold_horizon_days" in rows[0]
    assert "Long call, ~0.70 delta, 21 DTE" in rows[1]


@pytest.mark.unit
def test_signal_csv_omits_none_swing_fields(tmp_path):
    signal = IntradaySignal(
        symbol="NVDA",
        bar_time=datetime(2026, 7, 27, 10, 0),
        action="HOLD",
        direction="none",
        entry_price=None,
        stop_loss=None,
        confidence="Marginal",
        setup_score=0,
        gate_summary="G1=False G2=False",
        reasoning="r",
    )
    write_signal_csv(tmp_path, signal)

    csv_path = Path(str(tmp_path)) / "signals.csv"
    rows = csv_path.read_text().strip().splitlines()
    assert "option_structure" in rows[0]
    assert "hold_horizon_days" in rows[0]
    assert rows[1].count(",") == rows[0].count(",")
    assert "None" not in rows[1]
