from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from tradingagents.intraday.session import DailyBiasReport


@dataclass
class PremarketCache:
    session_date: str
    analysts: list[str]
    reports: dict[str, DailyBiasReport] = field(default_factory=dict)


def cache_path(output_dir: Path | str, session_date: str) -> Path:
    return Path(output_dir).expanduser() / session_date / "premarket_bias.json"


def analysts_match(cached: list[str], current: list[str]) -> bool:
    return set(cached) == set(current)


def report_to_dict(report: DailyBiasReport) -> dict:
    return {
        "symbol": report.symbol,
        "trade_date": report.trade_date,
        "direction": report.direction,
        "key_levels": report.key_levels,
        "summary": report.summary,
        "computed_at": report.computed_at.isoformat(),
    }


def report_from_dict(data: dict) -> DailyBiasReport:
    direction = data.get("direction", "neutral")
    if direction not in ("bullish", "bearish", "neutral"):
        direction = "neutral"
    return DailyBiasReport(
        symbol=str(data["symbol"]),
        trade_date=str(data["trade_date"]),
        direction=direction,  # type: ignore[arg-type]
        key_levels={str(k): float(v) for k, v in (data.get("key_levels") or {}).items()},
        summary=str(data.get("summary", "")),
        computed_at=datetime.fromisoformat(str(data["computed_at"])),
    )


def cache_to_dict(cache: PremarketCache) -> dict:
    return {
        "session_date": cache.session_date,
        "analysts": list(cache.analysts),
        "reports": {
            symbol: report_to_dict(report)
            for symbol, report in cache.reports.items()
        },
    }


def cache_from_dict(data: dict) -> PremarketCache | None:
    try:
        session_date = str(data["session_date"])
        analysts = [str(a) for a in data.get("analysts", [])]
        reports = {
            str(symbol): report_from_dict(report_data)
            for symbol, report_data in (data.get("reports") or {}).items()
        }
    except (KeyError, TypeError, ValueError):
        return None
    return PremarketCache(session_date=session_date, analysts=analysts, reports=reports)


def load_premarket_cache(path: Path, *, session_date: str) -> PremarketCache | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    cache = cache_from_dict(raw)
    if cache is None or cache.session_date != session_date:
        return None
    return cache


def save_premarket_cache(path: Path, cache: PremarketCache) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cache_to_dict(cache), indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def merge_report(
    cache: PremarketCache | None,
    *,
    session_date: str,
    analysts: list[str],
    symbol: str,
    report: DailyBiasReport,
) -> PremarketCache:
    if cache is None:
        cache = PremarketCache(session_date=session_date, analysts=list(analysts), reports={})
    cache.session_date = session_date
    cache.analysts = list(analysts)
    cache.reports[symbol] = report
    return cache
