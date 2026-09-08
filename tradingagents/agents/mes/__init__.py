"""Agents for the /MES futures trading copilot.

Unlike the equity-research agents, these are not LangGraph nodes: each factory
returns a plain callable that a CLI invokes directly with keyword arguments and
that returns rendered markdown.
"""

from .gatekeeper_agent import create_mes_gatekeeper_agent
from .manager_agent import create_mes_manager_agent
from .morning_agent import create_mes_morning_agent
from .review_agent import create_mes_review_agent

__all__ = [
    "create_mes_gatekeeper_agent",
    "create_mes_manager_agent",
    "create_mes_morning_agent",
    "create_mes_review_agent",
]
