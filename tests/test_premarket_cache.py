from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from tradingagents.intraday.premarket_cache import (
    PremarketCache,
    analysts_match,
    cache_from_dict,
    cache_path,
    cache_to_dict,
    load_premarket_cache,
    merge_report,
    report_from_dict,
    report_to_dict,
    save_premarket_cache,
)
from tradingagents.intraday.session import DailyBiasReport


def _report(symbol: str = "NVDA", direction: str = "bullish") -> DailyBiasReport:
    return DailyBiasReport(
        symbol=symbol,
        trade_date="2026-07-28",
        direction=direction,  # type: ignore[arg-type]
        key_levels={"support": 100.0},
        summary="buy bias",
        computed_at=datetime(2026, 7, 28, 9, 5, 12),
    )


@pytest.mark.unit
def test_report_round_trip():
    report = _report()
    restored = report_from_dict(report_to_dict(report))
    assert restored.symbol == report.symbol
    assert restored.direction == report.direction
    assert restored.key_levels == report.key_levels
    assert restored.computed_at == report.computed_at


@pytest.mark.unit
def test_report_round_trip_with_analyst_reports():
    report = _report()
    report.analyst_reports = {"market_report": "bullish trend"}
    report.debate_summary = "bull case wins"
    restored = report_from_dict(report_to_dict(report))
    assert restored.analyst_reports == report.analyst_reports
    assert restored.debate_summary == report.debate_summary


@pytest.mark.unit
def test_v1_cache_backward_compat():
    data = {
        "session_date": "2026-07-28",
        "analysts": ["market"],
        "reports": {
            "NVDA": {
                "symbol": "NVDA",
                "trade_date": "2026-07-28",
                "direction": "bullish",
                "key_levels": {},
                "summary": "buy",
                "computed_at": "2026-07-28T09:05:12",
            }
        },
    }
    cache = cache_from_dict(data)
    assert cache is not None
    assert cache.reports["NVDA"].analyst_reports == {}


@pytest.mark.unit
def test_cache_schema_version_written(tmp_path):
    path = cache_path(tmp_path, "2026-07-28")
    cache = PremarketCache(
        session_date="2026-07-28",
        analysts=["market"],
        reports={"NVDA": _report()},
    )
    save_premarket_cache(path, cache)
    import json

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2


@pytest.mark.unit
def test_analysts_match_ignores_order():
    assert analysts_match(["market", "news"], ["news", "market"])
    assert not analysts_match(["market", "news"], ["market", "social"])


@pytest.mark.unit
def test_load_premarket_cache_missing_file(tmp_path):
    path = cache_path(tmp_path, "2026-07-28")
    assert load_premarket_cache(path, session_date="2026-07-28") is None


@pytest.mark.unit
def test_save_and_load_premarket_cache(tmp_path):
    path = cache_path(tmp_path, "2026-07-28")
    cache = PremarketCache(
        session_date="2026-07-28",
        analysts=["market", "news"],
        reports={"NVDA": _report()},
    )
    save_premarket_cache(path, cache)
    loaded = load_premarket_cache(path, session_date="2026-07-28")
    assert loaded is not None
    assert loaded.reports["NVDA"].direction == "bullish"
    assert loaded.analysts == ["market", "news"]


@pytest.mark.unit
def test_load_rejects_wrong_session_date(tmp_path):
    path = cache_path(tmp_path, "2026-07-28")
    save_premarket_cache(
        path,
        PremarketCache(session_date="2026-07-28", analysts=["market"], reports={}),
    )
    assert load_premarket_cache(path, session_date="2026-07-27") is None


@pytest.mark.unit
def test_merge_report_preserves_existing_entries():
    cache = PremarketCache(
        session_date="2026-07-28",
        analysts=["market"],
        reports={"NVDA": _report("NVDA")},
    )
    merged = merge_report(
        cache,
        session_date="2026-07-28",
        analysts=["market", "news"],
        symbol="AAPL",
        report=_report("AAPL", "bearish"),
    )
    assert "NVDA" in merged.reports
    assert merged.reports["AAPL"].direction == "bearish"
    assert merged.analysts == ["market", "news"]


@pytest.mark.unit
def test_cache_from_dict_invalid_returns_none():
    assert cache_from_dict({"bad": "data"}) is None


@pytest.mark.unit
def test_cache_to_dict_structure():
    data = cache_to_dict(
        PremarketCache(
            session_date="2026-07-28",
            analysts=["market"],
            reports={"SPY": _report("SPY")},
        )
    )
    assert data["session_date"] == "2026-07-28"
    assert "SPY" in data["reports"]
