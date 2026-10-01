"""Punto de entrada para LangGraph Studio (`langgraph dev`). Studio aporta su propia persistencia."""

from financial_agent.infrastructure import financial_data
from financial_agent.infrastructure.agents import build_agents
from financial_agent.infrastructure.config import get_settings, groq_llm, limit_defaults, pricing
from financial_agent.infrastructure.orchestrator import build_graph
from financial_agent.infrastructure.tools import build_tools

settings = get_settings()
financial_data.seed(settings.sqlite_path)
graph = build_graph(
    build_agents(groq_llm(settings), build_tools(settings.sqlite_path, settings.tool_delay_seconds), pricing(settings)),
    limit_defaults(settings),
)
