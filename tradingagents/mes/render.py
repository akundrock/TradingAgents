"""Markdown rendering of checklist results, shared by the CLI and the LLM prompts."""

from __future__ import annotations

from .checklist import ChecklistResult, CheckItem
from .snapshot import MesSnapshot


def _mark(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _items_table(items: list[CheckItem], show_weight: bool) -> str:
    header = "| Check | Result | Observed | Threshold |"
    divider = "| --- | --- | --- | --- |"
    if show_weight:
        header = "| Check | Result | Observed | Threshold | Weight |"
        divider = "| --- | --- | --- | --- | --- |"
    lines = [header, divider]
    for item in items:
        row = f"| {item.name} | {_mark(item.passed)} | {item.observed} | {item.threshold} |"
        if show_weight:
            row = row[:-1] + f" {item.weight} |"
        lines.append(row)
    return "\n".join(lines)


def render_checklist(result: ChecklistResult, *, live: bool = True) -> str:
    """Full markdown checklist; this is also the gatekeeper agent's prompt context."""
    vwap_label = f"{result.vwap:.2f}" if live else "not yet set"
    if live:
        atr_bands = f"{result.lower_atr_band:.2f} / {result.upper_atr_band:.2f}"
    else:
        atr_bands = "not yet set"
    parts = [
        f"## MES Entry Checklist — {result.as_of_label}",
        "",
        f"**Side evaluated**: {result.side}  ",
        f"**MES price**: {result.last_price:.2f}  ",
        f"**Score**: {result.score}/{result.max_score} "
        f"({result.confirmations} confirmations, {result.required} required) — tier **{result.tier}**  ",
        f"**Gates**: {'OK' if result.gates_ok else 'BLOCKED'}  ",
        f"**SPY confluence**: {result.spy_confirmations}/5 (3 required)  ",
        f"**Divergence**: {result.divergence}  ",
        f"**Deterministic verdict**: {'TRADEABLE' if result.tradeable else 'NOT TRADEABLE'}",
        "",
        "### SPY — direction",
        "",
        _items_table(result.spy_items, show_weight=False),
        "",
        "### MES — execution",
        "",
        _items_table(result.mes_items, show_weight=True),
        "",
        "### Levels",
        "",
        f"- VWAP: {vwap_label}",
        f"- ATR({result.atr:.2f}) bands: {atr_bands}",
    ]
    if result.opening_range_high is not None and result.opening_range_low is not None:
        parts.append(
            f"- Opening range: {result.opening_range_low:.2f} – {result.opening_range_high:.2f}"
        )
    if result.gate_reasons:
        parts.extend(["", "### Gate blocks", ""] + [f"- {r}" for r in result.gate_reasons])
    if result.no_trade_reasons:
        parts.extend(["", "### No-trade conditions", ""] + [f"- {r}" for r in result.no_trade_reasons])
    if result.warnings:
        parts.extend(["", "### Data warnings", ""] + [f"- {w}" for w in result.warnings])
    return "\n".join(parts)


def render_market_context(snapshot: MesSnapshot) -> str:
    """Compact premarket / internals summary for the morning and review prompts."""
    spy, mes = snapshot.spy, snapshot.mes
    live = snapshot.rth_started
    lines = [
        f"## Market context — {snapshot.session_date} as of {snapshot.as_of.strftime('%H:%M ET')}",
        "",
    ]
    if not live:
        lines.extend(
            [
                "Regular trading hours have not started. Today's VWAP, session change and "
                "opening range do not exist yet, so the levels below come from the prior "
                "completed session plus the overnight range.",
                "",
            ]
        )
    lines.extend(
        [
            "| Field | SPY | /MES |",
            "| --- | --- | --- |",
            f"| Last | {spy.close:.2f} | {mes.close:.2f} |",
            f"| Session VWAP | {_level(spy.vwap, live)} | {_level(mes.vwap, live)} |",
            f"| Session change | {_signed(spy.session_change(), live)} "
            f"| {_signed(mes.session_change(), live)} |",
            f"| ATR | {spy.atr:.2f} | {mes.atr:.2f} |",
        ]
    )

    lines.extend(_prior_session_lines(snapshot))

    lines.extend(
        [
            "",
            "| Internal | Value | Read |",
            "| --- | --- | --- |",
            f"| $ADD | {_fmt(snapshot.add)} | {_add_read(snapshot)} |",
            f"| $TICK | {_fmt(snapshot.tick)} | {_tick_read(snapshot)} |",
            f"| $VOLD | {_fmt(snapshot.vold)} | {_vold_read(snapshot)} |",
        ]
    )
    if mes.opening_range_high is not None and mes.opening_range_low is not None:
        lines.extend(
            [
                "",
                f"Opening range (/MES): {mes.opening_range_low:.2f} – {mes.opening_range_high:.2f}",
            ]
        )
    if snapshot.warnings:
        lines.extend(["", "Notes:"] + [f"- {w}" for w in snapshot.warnings])
    return "\n".join(lines)


def _prior_session_lines(snapshot: MesSnapshot) -> list[str]:
    prior_mes, prior_spy = snapshot.prior_mes, snapshot.prior_spy
    if prior_mes is None and prior_spy is None:
        return []

    label = (prior_mes or prior_spy).session_date
    rows = [
        "",
        f"### Prior session ({label}) — levels to mark",
        "",
        "| Level | SPY | /MES |",
        "| --- | --- | --- |",
    ]
    for name, attr in (
        ("High", "high"),
        ("Low", "low"),
        ("Close", "close"),
        ("VWAP", "vwap"),
        ("POC", "poc"),
        ("VAH", "vah"),
        ("VAL", "val"),
    ):
        spy_value = getattr(prior_spy, attr, None) if prior_spy else None
        mes_value = getattr(prior_mes, attr, None) if prior_mes else None
        rows.append(f"| {name} | {_opt(spy_value)} | {_opt(mes_value)} |")

    if snapshot.overnight_mes:
        high, low = snapshot.overnight_mes
        rows.extend(["", f"Overnight range (/MES): {low:.2f} – {high:.2f}"])
    return rows


def _level(value: float, live: bool) -> str:
    return f"{value:.2f}" if live else "not yet set"


def _signed(value: float, live: bool) -> str:
    return f"{value:+.2f}" if live else "not yet set"


def _opt(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.2f}"


def _fmt(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:+.0f}"


def _add_read(snapshot: MesSnapshot) -> str:
    add, cfg = snapshot.add, snapshot.config
    if add is None:
        return "unavailable"
    if add > cfg.add_trend_threshold:
        return "bullish breadth"
    if add < -cfg.add_trend_threshold:
        return "bearish breadth"
    if abs(add) <= cfg.add_chop_threshold:
        return "chop zone"
    return "leaning " + ("bullish" if add > 0 else "bearish")


def _tick_read(snapshot: MesSnapshot) -> str:
    tick, cfg = snapshot.tick, snapshot.config
    if tick is None:
        return "unavailable"
    if snapshot.tick_whipsawing():
        return "whipsawing around 0"
    if abs(tick) >= cfg.tick_extreme_threshold:
        return "exhaustion extreme"
    eff = snapshot.tick_effective_threshold()
    signal = snapshot.tick_signal()
    thresh_label = f"thresh ±{eff:.0f}"
    if signal == "bull":
        label = "buyers in control"
        if snapshot.tick_burst("long"):
            return f"{label} burst ({thresh_label})"
        return f"{label} ({thresh_label})"
    if signal == "bear":
        label = "sellers in control"
        if snapshot.tick_burst("short"):
            return f"{label} burst ({thresh_label})"
        return f"{label} ({thresh_label})"
    if signal == "persistent_buy":
        streak = snapshot.tick_streak(positive=True)
        return f"persistent buy x{streak} ({thresh_label})"
    if signal == "persistent_sell":
        streak = snapshot.tick_streak(positive=False)
        return f"persistent sell x{streak} ({thresh_label})"
    return f"neutral ({thresh_label})"


def _vold_read(snapshot: MesSnapshot) -> str:
    if snapshot.vold is None:
        return "unavailable"
    divergence = snapshot.vold_bar_divergence()
    z = snapshot.vold_z_score()
    z_hint = f", z={z:+.1f}" if z is not None else ""
    if divergence == "bearish":
        return f"divergence bearish{z_hint}"
    if divergence == "bullish":
        return f"divergence bullish{z_hint}"
    if snapshot.vold_confirms("long"):
        return f"confirms bullish{z_hint}"
    if snapshot.vold_confirms("short"):
        return f"confirms bearish{z_hint}"
    slope = snapshot.vold_slope()
    if slope is None:
        return "flat"
    if slope > 0:
        return f"rising{z_hint}"
    if slope < 0:
        return f"falling{z_hint}"
    return f"flat{z_hint}"
