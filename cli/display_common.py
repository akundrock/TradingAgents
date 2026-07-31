from __future__ import annotations

from typing import Protocol


ANALYST_ORDER = ["market", "social", "news", "fundamentals"]
ANALYST_AGENT_NAMES = {
    "market": "Market Analyst",
    "social": "Sentiment Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}
ANALYST_REPORT_MAP = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
}
REPORT_SECTION_TITLES = {
    "market_report": "Market Analysis",
    "sentiment_report": "Social Sentiment",
    "news_report": "News Analysis",
    "fundamentals_report": "Fundamentals Analysis",
    "investment_plan": "Research Team Decision",
}
PREMARKET_RESEARCH_AGENTS = [
    "Bull Researcher",
    "Bear Researcher",
    "Research Manager",
]


class AnalystStatusBuffer(Protocol):
    selected_analysts: list[str]
    agent_status: dict[str, str]
    report_sections: dict[str, str | None]

    def update_agent_status(self, agent: str, status: str) -> None: ...

    def update_report_section(self, section_name: str, content: str) -> None: ...


def update_analyst_statuses(
    buffer: AnalystStatusBuffer,
    chunk: dict,
    *,
    wall_time_tracker=None,
) -> None:
    """Update analyst statuses based on accumulated report state."""
    from tradingagents.graph.analyst_execution import sync_analyst_tracker_from_chunk

    selected = buffer.selected_analysts
    found_active = False

    if wall_time_tracker is not None:
        sync_analyst_tracker_from_chunk(wall_time_tracker, chunk)

    for analyst_key in ANALYST_ORDER:
        if analyst_key not in selected:
            continue

        agent_name = ANALYST_AGENT_NAMES[analyst_key]
        report_key = ANALYST_REPORT_MAP[analyst_key]

        if chunk.get(report_key):
            buffer.update_report_section(report_key, chunk[report_key])

        has_report = bool(buffer.report_sections.get(report_key))

        if has_report:
            buffer.update_agent_status(agent_name, "completed")
        elif not found_active:
            buffer.update_agent_status(agent_name, "in_progress")
            found_active = True
        else:
            buffer.update_agent_status(agent_name, "pending")

    if (
        not found_active
        and selected
        and buffer.agent_status.get("Bull Researcher") == "pending"
    ):
        buffer.update_agent_status("Bull Researcher", "in_progress")


def update_research_status_from_chunk(buffer: AnalystStatusBuffer, chunk: dict) -> None:
    """Advance research-team agent status from streamed graph chunks."""
    debate = chunk.get("investment_debate_state") or {}
    if debate.get("bull_history"):
        buffer.update_agent_status("Bull Researcher", "completed")
        if not debate.get("bear_history"):
            buffer.update_agent_status("Bear Researcher", "in_progress")
    if debate.get("bear_history"):
        buffer.update_agent_status("Bear Researcher", "completed")
        if not debate.get("judge_decision"):
            buffer.update_agent_status("Research Manager", "in_progress")
    if chunk.get("investment_plan"):
        buffer.update_report_section("investment_plan", str(chunk["investment_plan"]))
        buffer.update_agent_status("Research Manager", "completed")
