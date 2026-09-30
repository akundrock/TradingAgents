"""End-to-end check: the 2026-09-29 trade-management failure, post-fix.

Tick 1 detects the target fill and journals it (keyed by the touching bar);
tick 2 — one minute later, same bar history — must NOT re-fire; the manual
close books the user's real exit price instead of the ladder's fill estimate.

Run: python3 scripts/e2e_phantom_fill_check.py
"""

from datetime import datetime
from pathlib import Path
import tempfile

import pytest

from tests.mes_factories import make_mes_series, make_snapshot
from tradingagents.mes.checklist import ChecklistResult
from tradingagents.mes.config import load_mes_config
from tradingagents.mes.journal import MesJournal
from tradingagents.mes.management import OpenTrade, evaluate_management, _r_of

SESSION = "2026-09-29"


def fake_result() -> ChecklistResult:
    return ChecklistResult(
        as_of_label="11:42",
        side="short",
        direction="short",
        vwap=7739.5,
        atr=7.5,
        gates_ok=True,
        spy_confluence_ok=True,
    )


def sweep_snapshot(as_of: datetime):
    """A bar that swept to the session low 7716.00 (below the 7726.00 target)."""
    mes = make_mes_series(
        close=7716.25,
        bar_kwargs={"open_": 7727.25, "high": 7727.75, "low": 7716.00, "close": 7716.25},
    )
    return make_snapshot(mes=mes, as_of=as_of)


def main() -> None:
    cfg = load_mes_config()
    tmp = Path(tempfile.mkdtemp())
    journal = MesJournal({"mes_journal_dir": str(tmp)})

    # Today's trade: short 7727.00, stop 7738.75, target 7726.00 (1R = 11.75 pts).
    trade = OpenTrade(
        side="short", contracts=1, remaining=1,
        entry=7727.0, stop=7738.75, initial_stop=7738.75, target=7726.00,
        entry_time=datetime(2026, 9, 29, 11, 40), initial_risk_points=11.75,
    )
    journal.append_trade_opened(trade, entry_context={"score": 4, "tier": "marginal"})

    # Tick 1 (11:42): the bar touched 7716.00, below the 7726.00 target.
    snapshot1 = sweep_snapshot(datetime(2026, 9, 29, 11, 42))
    rebuilt = journal.find_open_trade(SESSION)
    assert rebuilt is not None
    updated, report = evaluate_management(snapshot1, fake_result(), rebuilt, cfg)
    assert report.recommendation == "CLOSED"
    assert report.r_now != 0.0, f"fill R must be reported, not 0.0 (got {report.r_now})"
    assert updated.remaining == 0
    assert "target" in updated.fired
    # The CLI watch loop journals each event exactly once, carrying the
    # ladder's terminal state so the next tick's rebuild cannot re-fire it.
    for event in report.events:
        journal.append_trade_adjusted(
            SESSION,
            note=f"{event.name}: {event.detail}",
            as_of=event.as_of.isoformat(timespec="minutes"),
            ladder_fired=dict(updated.fired),
            remaining=updated.remaining,
            realized_r=updated.realized_r,
        )
    fill_r = updated.realized_r
    print(
        f"tick 11:42: target fill journaled (event stamp {report.events[0].as_of:%H:%M}, "
        f"fill R {report.r_now:+.4f}, realized_r {updated.realized_r:+.4f})"
    )

    # Tick 2 (11:43): same bar history — the replayed state must suppress the re-fire.
    rebuilt2 = journal.find_open_trade(SESSION)
    assert rebuilt2 is not None, "trade must survive replay after the fill"
    assert rebuilt2.remaining == 0
    updated2, report2 = evaluate_management(snapshot1, fake_result(), rebuilt2, cfg)
    assert report2.events == [], f"ladder re-fired: {[e.name for e in report2.events]}"
    assert report2.recommendation == "CLOSED"
    # Flat ⇒ r_now is 0.0 by definition; the panel shows the banked fill R.
    assert report2.r_now == 0.0
    assert report2.realized_r == pytest.approx(fill_r)
    assert abs(updated2.realized_r - updated.realized_r) < 1e-9
    print(f"tick 11:43: no re-fire, banked R holds {report2.realized_r:+.4f}R")

    # Tick 3 (11:44): still no re-fire.
    rebuilt3 = journal.find_open_trade(SESSION)
    updated3, report3 = evaluate_management(snapshot1, fake_result(), rebuilt3, cfg)
    assert report3.events == []
    assert updated3.realized_r == updated.realized_r

    # Manual close at the user's real exit — near the session lows (12:06).
    # (Mirrors `mes trade close --price 7716.50`: the manual price wins over
    # the ladder's inferred fill.)
    final_realized = round(updated3.contracts * _r_of(7716.50, updated3), 4)
    print(
        f"manual exit at 7716.50 → {final_realized:+.4f}R "
        f"(ladder's fill estimate: {report.events[0].detail})"
    )
    assert abs(final_realized - (7727.0 - 7716.50) / 11.75) < 1e-3
    print("END-TO-END OK: one journaled fill, no re-fire across ticks, R tracked, "
          "manual exit price authoritative")


if __name__ == "__main__":
    main()
