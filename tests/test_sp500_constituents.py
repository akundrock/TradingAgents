from __future__ import annotations

from unittest.mock import patch

import pytest

from tradingagents.intraday.indicators.sp500_constituents import (
    fetch_sp500_from_wikipedia,
    is_sp500_constituent,
    refresh_sp500_constituents,
    write_sp500_constituents,
)


def test_is_sp500_constituent_pltr():
    assert is_sp500_constituent("PLTR")
    assert is_sp500_constituent("pltr")


def test_is_sp500_constituent_snap_not_in_sp500():
    assert not is_sp500_constituent("SNAP")


def test_refresh_sp500_from_wikipedia_live():
    """Live fetch — skipped if Wikipedia unavailable."""
    symbols = fetch_sp500_from_wikipedia()
    assert len(symbols) >= 500
    assert "PLTR" in symbols
    assert "NVDA" in symbols


def test_write_and_reload_sp500(tmp_path):
    payload = {
        "as_of": "2026-08-04",
        "source": "test",
        "symbols": ["AAPL", "PLTR", "MSFT"],
    }
    path = tmp_path / "sp500_constituents.json"
    write_sp500_constituents(payload, path=path)

    with patch(
        "tradingagents.intraday.indicators.sp500_constituents._DATA_PATH",
        path,
    ):
        from tradingagents.intraday.indicators.sp500_constituents import (
            clear_sp500_cache,
            is_sp500_constituent,
        )

        clear_sp500_cache()
        assert is_sp500_constituent("PLTR")
        assert not is_sp500_constituent("SNAP")

    clear_sp500_cache()


@pytest.mark.unit
def test_refresh_sp500_constituents_mocked():
    with patch(
        "tradingagents.intraday.indicators.sp500_constituents.fetch_sp500_from_wikipedia",
        return_value=["AAPL", "PLTR"],
    ):
        payload = refresh_sp500_constituents(source="wikipedia")
    assert payload["symbols"] == ["AAPL", "PLTR"]
    assert payload["source"] == "wikipedia"
