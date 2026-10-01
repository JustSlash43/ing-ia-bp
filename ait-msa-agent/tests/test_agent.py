"""Herramientas, sub-agente (allowlist, métricas por paso, circuit breaker) y grafo directo (como en Studio)."""

import time

from financial_agent.domain import policies
from financial_agent.domain.models import Pricing
from financial_agent.infrastructure.agents import build_agents
from financial_agent.infrastructure.config import limit_defaults
from financial_agent.infrastructure.orchestrator import build_graph
from financial_agent.infrastructure.tools import build_tools
from tests.fake_llm import FakeLLM

PRICING = Pricing(0.29, 0.59)
STEP_FIELDS = {"step_number", "tool_called", "tokens_used", "cumulative_cost", "elapsed_time"}


def limits(settings, **overrides) -> dict:
    d = limit_defaults(settings)
    return {**policies.query_limits(0.50, 30_000, **d), **overrides}


def run(settings, llm, **limit_overrides) -> dict:
    agent = build_agents(llm, build_tools(settings.sqlite_path, 0), PRICING)["financial_analyst_agent"]
    context = {"analysis_id": "t1", "analyst_id": "A01", "session_id": "S001", "started_at": time.time()}
    return agent("Calcula la razón corriente 2024", limits(settings, **limit_overrides), context)


# ---------------------------------------------------------------------- herramientas

def test_search_tool_reads_sqlite_and_normalizes(settings):
    search = build_tools(settings.sqlite_path, 0)["search_financial_data"]
    msg = search.invoke({"type": "tool_call", "id": "1", "name": "search_financial_data",
                         "args": {"metric": "Activo Corriente", "period": "FY2024"}})
    assert msg.artifact | {"found": True, "value": 990.0, "metric": "current_assets", "period": "2024"} == msg.artifact
    assert msg.artifact["requested_metric"] == "Activo Corriente"  # identificadores de entrada en el artifact


def test_search_tool_quarter_and_not_found(settings):
    search = build_tools(settings.sqlite_path, 0)["search_financial_data"]
    q = search.invoke({"type": "tool_call", "id": "1", "name": "search_financial_data",
                       "args": {"metric": "revenue", "period": "Q2 2025"}}).artifact
    missing = search.invoke({"type": "tool_call", "id": "2", "name": "search_financial_data",
                             "args": {"metric": "goodwill", "period": "2024"}}).artifact
    assert (q["period"], q["value"]) == ("2025-Q2", 425.0)
    assert missing["found"] is False and "revenue" in missing["available_metrics"]


def test_search_tool_has_simulated_delay(settings):
    search = build_tools(settings.sqlite_path, 0.2)["search_financial_data"]
    start = time.perf_counter()
    search.invoke({"metric": "revenue", "period": "2024"})
    assert time.perf_counter() - start >= 0.2


def test_ratio_tool():
    from financial_agent.infrastructure.tools import calculate_ratio
    assert calculate_ratio(990, 650)[1]["result"] == 1.5231
    assert calculate_ratio(1, 0)[1]["error"]


# ---------------------------------------------------------------------- sub-agente

def test_agent_meters_every_step(settings):
    result = run(settings, FakeLLM())
    steps = result["steps"]
    assert result["stop"] is None and result["summary"]
    assert [s["step_number"] for s in steps] == [1, 2, 3]
    assert all(STEP_FIELDS <= s.keys() for s in steps)
    assert steps[0]["tool_called"] == ["search_financial_data", "search_financial_data"]
    assert steps[1]["tool_called"] == ["calculate_ratio"] and steps[2]["tool_called"] is None
    assert [s["cumulative_tokens"] for s in steps] == [560, 1120, 1680]
    assert steps[-1]["cumulative_cost"] == round(3 * policies.cost_usd(500, 60, PRICING), 8)
    assert steps[-1]["remaining_tokens"] == 10_000 - 1680
    assert {a["tool"] for a in result["artifacts"]} == {"search_financial_data", "calculate_ratio"}


def test_allowlist_blocks_rogue_tool(settings):
    result = run(settings, FakeLLM(rogue_tool="transfer_funds"))
    assert result["blocked_tool_calls"] == [{"tool": "transfer_funds", "args": {"metric": "x"}}]
    assert all(c["tool"] != "transfer_funds" for c in result["tool_calls"])
    assert result["summary"]  # se recupera y termina


def test_circuit_breaker_stops_loop_before_running_tool(settings):
    result = run(settings, FakeLLM(loop=True))
    assert result["stop"]["code"] == "loop_detected"
    assert len(result["steps"]) == 2 and len(result["artifacts"]) == 1  # la repetición no se ejecutó


def test_step_limit(settings):
    metrics = ("revenue", "net_income", "ebitda", "cash", "equity", "inventory", "total_assets", "total_liabilities",
               "current_assets", "current_liabilities")
    plan = [[("search_financial_data", {"metric": m, "period": "2024"})] for m in metrics]
    result = run(settings, FakeLLM(plan=plan))
    assert result["stop"]["code"] == "step_limit" and len(result["steps"]) == 8


def test_provider_error_becomes_state(settings):
    result = run(settings, FakeLLM(fail=True))
    assert result["stop"]["code"] == "agent_error" and result["steps"] == []


# ---------------------------------------------------------------------- grafo directo (Studio)

def test_graph_runs_without_checkpointer(settings):
    agents = build_agents(FakeLLM(), build_tools(settings.sqlite_path, 0), PRICING)
    graph = build_graph(agents, limit_defaults(settings))
    state = graph.invoke({"analyst_id": "A01", "session_id": 1, "query": "Razón corriente 2024", "max_budget_usd": 0.5})
    assert state["status"] == "completed" and state["session_id"] == "1"
    assert (state["input_tokens"], state["output_tokens"], state["tokens_used"]) == (1500, 180, 1680)
    assert [a["actor"] for a in state["audit"]] == [
        "validate_input", "budget_gate", "financial_analyst_agent", "policy_gate", "respond"]


def test_graph_invalid_input_ends_cleanly(settings):
    graph = build_graph({}, limit_defaults(settings))
    state = graph.invoke({"analyst_id": "A01", "session_id": "S1", "query": "x", "max_budget_usd": 0.5})
    assert state["status"] == "invalid_input"
