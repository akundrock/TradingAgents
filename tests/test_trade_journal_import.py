from __future__ import annotations

from datetime import datetime
from pathlib import Path

from tradingagents.dataflows.trade_journal import (
    _futures_intraday_symbol_candidates,
    _naive_local_to_utc_naive,
    normalize_contract_root,
    parse_schwab_order_history_csv,
    reconstruct_round_trip_trades,
)


def test_normalize_contract_root_for_mes_contracts() -> None:
    assert normalize_contract_root("MESM6") == "MES"
    assert normalize_contract_root("mesu6") == "MES"
    assert normalize_contract_root("SPY") == "SPY"


def test_futures_intraday_symbol_candidates_expand_contract_year() -> None:
    ts = datetime(2026, 5, 5, 10, 0, 0)
    candidates = _futures_intraday_symbol_candidates("MESM6", ts)
    assert candidates[0] == "/MESM6"
    assert candidates[1] == "MESM6"
    assert "/MESM26" in candidates
    assert "MESM26" in candidates
    assert "/MES" in candidates
    assert "MES" in candidates


def test_naive_local_to_utc_naive_for_mountain_daylight_time() -> None:
    ts = datetime(2026, 5, 5, 9, 20, 39)
    utc_ts = _naive_local_to_utc_naive(ts, "America/Denver")
    assert utc_ts == datetime(2026, 5, 5, 15, 20, 39)


def test_parse_schwab_order_history_filters_filled_and_sorts(tmp_path: Path) -> None:
    csv = tmp_path / "orders.csv"
    csv.write_text(
        "\n".join(
            [
                "orderId,B/S,Contract,avgPrice,filledQty,Fill Time,Status,Notional Value",
                "1, Buy,MESM6,7300.0,1,05/05/2026 10:01:00, Filled,36500.0",
                "2, Sell,MESM6,7302.0,1,05/05/2026 10:10:00, Filled,36510.0",
                "3, Sell,MESM6,7301.0,1,05/05/2026 10:05:00, Canceled,36505.0",
            ]
        ),
        encoding="utf-8",
    )

    rows = parse_schwab_order_history_csv(csv)

    assert len(rows) == 2
    assert rows[0]["timestamp"] < rows[1]["timestamp"]
    assert rows[0]["ticker"] == "MES"
    assert rows[0]["side"] == "BUY"
    assert rows[1]["side"] == "SELL"


def test_reconstruct_round_trip_from_simple_buy_sell() -> None:
    fills = [
        {
            "timestamp": datetime(2026, 5, 5, 10, 1, 0),
            "trade_date": "2026-05-05",
            "ticker": "MES",
            "contract": "MESM6",
            "side": "BUY",
            "quantity": 1.0,
            "price": 7300.0,
            "notional": 36500.0,
            "import_id": "1",
        },
        {
            "timestamp": datetime(2026, 5, 5, 10, 10, 0),
            "trade_date": "2026-05-05",
            "ticker": "MES",
            "contract": "MESM6",
            "side": "SELL",
            "quantity": 1.0,
            "price": 7302.0,
            "notional": 36510.0,
            "import_id": "2",
        },
    ]

    trades, open_remainders = reconstruct_round_trip_trades(fills, contract_multiplier=5.0)

    assert len(trades) == 1
    assert len(open_remainders) == 0
    trade = trades[0]
    assert trade["direction"] == "LONG"
    assert trade["quantity"] == 1.0
    assert trade["entry_avg"] == 7300.0
    assert trade["exit_avg"] == 7302.0
    assert trade["realized_points"] == 2.0
    assert trade["realized_dollars"] == 10.0
