"""Tiny fixture test for scripts/mes_journal_expectancy.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "mes_journal_expectancy.py"
_SPEC = importlib.util.spec_from_file_location("mes_journal_expectancy", _SCRIPT)
assert _SPEC and _SPEC.loader
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MOD
_SPEC.loader.exec_module(_MOD)

SAMPLE_FLOOR = _MOD.SAMPLE_FLOOR
aggregate = _MOD.aggregate
format_markdown = _MOD.format_markdown
join_closed_trades = _MOD.join_closed_trades
load_journal = _MOD.load_journal


def _write_day(path: Path, events: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n",
        encoding="utf-8",
    )


@pytest.mark.unit
def test_join_closed_trades_pulls_tier_from_open():
    events = [
        {
            "kind": "trade_opened",
            "side": "long",
            "entry": 100.0,
            "entry_time": "2026-10-07T11:39",
            "entry_context": {"tier": "marginal", "score": 4},
        },
        {
            "kind": "trade_closed",
            "side": "long",
            "entry": 100.0,
            "exit_price": 102.0,
            "entry_time": "2026-10-07T11:39",
            "realized_r": 0.5,
            "mfe_r": 0.6,
            "mae_r": -0.1,
            "reason": "target",
        },
    ]
    closed = join_closed_trades(events, session_date="2026-10-07")
    assert len(closed) == 1
    assert closed[0].tier == "marginal"
    assert closed[0].side == "long"
    assert closed[0].realized_r == 0.5


@pytest.mark.unit
def test_aggregate_marks_low_n_not_interpretable(tmp_path: Path):
    day = tmp_path / "2026-10-07.jsonl"
    events = []
    for i in range(3):
        events.append(
            {
                "kind": "trade_opened",
                "side": "short",
                "entry": 100.0 + i,
                "entry_time": f"2026-10-07T11:{i:02d}",
                "entry_context": {"tier": "low"},
            }
        )
        events.append(
            {
                "kind": "trade_closed",
                "side": "short",
                "entry": 100.0 + i,
                "exit_price": 99.0,
                "entry_time": f"2026-10-07T11:{i:02d}",
                "realized_r": 0.2 if i else -1.0,
                "mfe_r": 0.0,
                "mae_r": 0.0,
                "reason": "manual",
            }
        )
    _write_day(day, events)

    trades = load_journal(tmp_path)
    rows = aggregate(trades, sample_floor=SAMPLE_FLOOR)
    assert len(trades) == 3
    assert rows[0]["n"] == 3
    assert rows[0]["hit_rate"] is None
    assert rows[0]["expectancy_r"] is None

    md = format_markdown(trades, rows, journal_dir=tmp_path)
    assert "Insufficient N" in md
    assert "none meet the sample floor" in md
    assert "n<5 — not interpretable" in md


@pytest.mark.unit
def test_aggregate_interpretable_when_floor_met(tmp_path: Path):
    day = tmp_path / "2026-10-08.jsonl"
    events = []
    for i in range(SAMPLE_FLOOR):
        events.append(
            {
                "kind": "trade_opened",
                "side": "long",
                "entry": 200.0 + i,
                "entry_time": f"2026-10-08T12:{i:02d}",
                "entry_context": {"tier": "standard"},
            }
        )
        events.append(
            {
                "kind": "trade_closed",
                "side": "long",
                "entry": 200.0 + i,
                "exit_price": 201.0,
                "entry_time": f"2026-10-08T12:{i:02d}",
                "realized_r": 1.0 if i < 3 else -0.5,
                "mfe_r": 1.2,
                "mae_r": -0.2,
                "reason": "target",
            }
        )
    _write_day(day, events)

    trades = load_journal(tmp_path)
    rows = aggregate(trades)
    assert rows[0]["n"] == SAMPLE_FLOOR
    assert rows[0]["hit_rate"] == pytest.approx(0.6)
    assert rows[0]["expectancy_r"] == pytest.approx((1.0 * 3 + -0.5 * 2) / 5)
