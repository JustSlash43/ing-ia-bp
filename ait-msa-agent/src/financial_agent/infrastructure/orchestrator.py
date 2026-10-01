"""Orquestador supervisor con LangGraph.

    validate_input -> budget_gate
      sin presupuesto de sesión -> respond (parcial con advertencia) -> FIN
      con presupuesto           -> financial_analyst_agent -> policy_gate -> respond -> FIN

El supervisor calcula los límites efectivos de la consulta (dominio), delega el análisis al
sub-agente con esos límites, decide con reglas del dominio si la respuesta es completa o
parcial, y arma la respuesta final. Ningún error de agente escapa: todo termina en estado.
"""

import logging
import operator
import time
from datetime import UTC, datetime
from typing import Annotated, NotRequired, TypedDict

from langgraph.config import get_config
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from financial_agent.domain import policies
from financial_agent.domain.models import AgentFailure, AnalysisRequest, InvalidQuery, StepLimitExceeded
from financial_agent.infrastructure.agents import AgentTask
from financial_agent.infrastructure.finops_log import log_event

logger = logging.getLogger("financial_agent.orchestrator")

AGENT = "financial_analyst_agent"
GRAPH_NODE_LIMIT = 6  # nodos del orquestador; los pasos del agente se limitan con max_steps


class Input(TypedDict):
    analyst_id: str
    session_id: str
    query: str
    max_budget_usd: float
    session_tokens_remaining: NotRequired[int]  # lo calcula el caso de uso; en Studio es opcional


class State(Input, total=False):
    status: str  # processing | completed | partial | invalid_input
    started_at: float
    finished_at: float
    limits: dict
    gate: dict
    agent_steps: list[dict]
    input_tokens: int     # tokens de entrada (prompt) consumidos por la solicitud
    output_tokens: int    # tokens de salida (completion) consumidos por la solicitud
    tokens_used: int
    cost_usd: float
    data: dict
    agent: dict
    stop: dict | None
    stop_reason: str | None
    findings: list[dict]
    answer: str
    warning: str | None
    error: str | None
    steps: Annotated[int, operator.add]
    audit: Annotated[list[dict], operator.add]


def build_graph(agents: dict[str, AgentTask], defaults: dict, checkpointer=None):

    def step(name, fn):
        """Envuelve cada nodo: aplica el límite de nodos, mide latencia y registra la auditoría."""
        def node(state: State):
            number = state.get("steps", 0) + 1
            if number > GRAPH_NODE_LIMIT:
                raise StepLimitExceeded(f"Se alcanzó el máximo de {GRAPH_NODE_LIMIT} nodos del orquestador.")
            start = time.perf_counter()
            update, action, detail = fn(state)
            entry = {"step": number, "actor": name, "action": action, "timestamp": datetime.now(UTC).isoformat(),
                     "detail": {**detail, "latency_ms": round((time.perf_counter() - start) * 1000)}}
            logger.info("nodo %s %s -> %s", number, name, action)
            return {**update, "steps": 1, "audit": [entry]}
        return node

    def delegate(task: str, limits: dict, context: dict) -> dict:
        """Envía la tarea al sub-agente y valida que cada artifact corresponda a una llamada pedida."""
        run = agents[AGENT](task, limits, context)
        for artifact in run["artifacts"]:
            if not any(_matches(artifact, call) for call in run["tool_calls"]):
                raise AgentFailure(f"[{AGENT}] artifact sin llamada correspondiente: {artifact}")
        return run

    # ------------------------------------------------------------------ nodos

    def validate_input(state: State):
        try:
            r = request(state)
        except (InvalidQuery, KeyError, TypeError, ValueError) as e:  # el flujo termina limpio y queda registrado
            return {"status": "invalid_input", "error": str(e)}, "input_rejected", {"error": str(e)}
        session_remaining = int(state.get("session_tokens_remaining", defaults["max_tokens_budget"]))
        limits = policies.query_limits(r.max_budget_usd, session_remaining, **defaults)
        # Se guardan normalizados (p. ej. Studio puede enviar ids como número).
        return ({"status": "processing", "started_at": time.time(), "limits": limits, "analyst_id": r.analyst_id,
                 "session_id": r.session_id, "query": r.query, "max_budget_usd": r.max_budget_usd,
                 **policies.token_usage([])},
                "input_validated", {"limits": limits})

    def budget_gate(state: State):
        gate = policies.evaluate_session_budget(state["limits"])
        update = {"gate": gate}
        if gate["verdict"] == "stop":
            update |= {"status": "partial", "stop": gate, "stop_reason": gate["code"], "findings": [gate]}
        return update, "budget_checked", gate

    def financial_analyst_agent(state: State):
        context = {"analysis_id": get_config()["configurable"].get("thread_id", "studio"),
                   "analyst_id": state["analyst_id"], "session_id": state["session_id"],
                   "started_at": state["started_at"]}
        try:
            run = delegate(state["query"], state["limits"], context)
        except AgentFailure as e:
            failure = policies.stop("agent_error", str(e))
            return ({"stop": failure, "agent_steps": [], "data": _empty_data(), "agent": {}, **policies.token_usage([])},
                    "agent_error", {"error": str(e)})
        data = {"searches": [a for a in run["artifacts"] if a["tool"] == "search_financial_data"],
                "ratios": [a for a in run["artifacts"] if a["tool"] == "calculate_ratio"]}
        trace = {k: run[k] for k in ("tool_calls", "blocked_tool_calls", "summary")}
        usage = policies.token_usage(run["steps"])
        return ({"agent_steps": run["steps"], "data": data, "stop": run["stop"], "agent": trace, **usage},
                "analysis_executed", {**trace, **usage, "agent_steps": len(run["steps"]),
                                      "stop_reason": run["stop"]["code"] if run["stop"] else None})

    def policy_gate(state: State):
        stop = state.get("stop")
        status = policies.decide(stop, bool(state["agent"].get("summary")))
        if status == "partial" and stop is None:  # el agente terminó sin texto de respuesta
            stop = policies.stop("agent_error", "El agente no entregó una respuesta final.")
        findings = [state["gate"], stop or policies.finding("finops", "proceed", None, "Análisis dentro de los límites.")]
        return ({"status": status, "stop": stop, "stop_reason": stop["code"] if stop else None, "findings": findings},
                "policy_decision", {"status": status, "findings": findings})

    def respond(state: State):
        stop = state.get("stop")
        if state["status"] == "completed":
            answer, warning = state["agent"]["summary"], None
        else:
            answer = partial_answer(state.get("data") or _empty_data(), (state.get("agent") or {}).get("summary", ""))
            warning = f"{policies.warning_for(stop['code'])} {stop['reason']}"
        return ({"answer": answer, "warning": warning, "finished_at": time.time()},
                "response_ready", {"status": state["status"], "warning": warning})

    # ------------------------------------------------------------------ grafo

    g = StateGraph(State, input_schema=Input)
    for fn in (validate_input, budget_gate, financial_analyst_agent, policy_gate, respond):
        g.add_node(fn.__name__, step(fn.__name__, fn))

    g.add_edge(START, "validate_input")
    g.add_conditional_edges("validate_input", lambda s: END if s["status"] == "invalid_input" else "budget_gate",
                            ["budget_gate", END])
    g.add_conditional_edges("budget_gate", lambda s: "respond" if s["status"] == "partial" else "financial_analyst_agent",
                            ["financial_analyst_agent", "respond"])
    g.add_edge("financial_analyst_agent", "policy_gate")
    g.add_edge("policy_gate", "respond")
    g.add_edge("respond", END)
    return g.compile(checkpointer=checkpointer)


def request(state: State) -> AnalysisRequest:
    return AnalysisRequest(str(state["analyst_id"]).strip(), str(state["session_id"]).strip(),
                           str(state["query"]).strip(), float(state["max_budget_usd"]))


def partial_answer(data: dict, summary: str) -> str:
    """Respuesta parcial determinista: solo datos obtenidos de las herramientas (artifacts)."""
    lines = [summary] if summary else []
    found = [s for s in data["searches"] if s.get("found")]
    ratios = [r for r in data["ratios"] if r.get("result") is not None]
    if found or ratios:
        lines.append("Datos obtenidos antes del corte:")
        lines += [f"- {s['metric']} {s['period']}: {s['value']:,.2f} {s['unit']} {s['currency']}" for s in found]
        lines += [f"- Ratio {r['numerator']} / {r['denominator']} = {r['result']}" for r in ratios]
    else:
        lines.append("No se alcanzó a obtener datos financieros antes del corte.")
    return "\n".join(lines)


def _matches(artifact: dict, call: dict) -> bool:
    args = call["args"]
    if artifact["tool"] != call["tool"]:
        return False
    if artifact["tool"] == "search_financial_data":
        return (str(artifact["requested_metric"]), str(artifact["requested_period"])) == \
               (str(args.get("metric")), str(args.get("period")))
    try:
        return (float(artifact["numerator"]), float(artifact["denominator"])) == \
               (float(args.get("numerator")), float(args.get("denominator")))
    except (TypeError, ValueError):
        return False


def _empty_data() -> dict:
    return {"searches": [], "ratios": []}


# ---------------------------------------------------------------------- ejecución
# Implementan los puertos RunAnalysis / ReadAnalysis (se enlazan en api.py).

def run_analysis(graph, timeout: float, analysis_id: str, r: AnalysisRequest, session_tokens_remaining: int) -> dict:
    payload = {"analyst_id": r.analyst_id, "session_id": r.session_id, "query": r.query,
               "max_budget_usd": r.max_budget_usd, "session_tokens_remaining": session_tokens_remaining}
    config = {"configurable": {"thread_id": analysis_id}, "recursion_limit": GRAPH_NODE_LIMIT,
              "metadata": {"analyst_id": r.analyst_id, "session_id": r.session_id}, "tags": ["financial-analysis"]}
    _execute(graph, timeout, config, payload)
    result = read_analysis(graph, analysis_id)
    budget = result["budget"]["query"] or {}
    log_event("analysis_finished", "WARNING" if result["status"] != "completed" else "INFO",
              analysis_id=analysis_id, analyst_id=r.analyst_id, session_id=r.session_id, status=result["status"],
              stop_reason=result["stop_reason"], **result["usage"],
              tokens_remaining=budget.get("tokens_remaining"), cost_remaining_usd=budget.get("cost_remaining_usd"))
    return result


def read_analysis(graph, analysis_id: str) -> dict | None:
    s = graph.get_state({"configurable": {"thread_id": analysis_id}}).values
    if not s:
        return None
    steps = s.get("agent_steps", [])
    usage = {k: s.get(k, 0) for k in ("input_tokens", "output_tokens", "tokens_used", "cost_usd")}
    limits = s.get("limits", {})
    return {
        "analysis_id": analysis_id,
        "analyst_id": s.get("analyst_id"),
        "session_id": s.get("session_id"),
        "query": s.get("query"),
        "status": s.get("status"),
        "answer": s.get("answer"),
        "warning": s.get("warning"),
        "stop_reason": s.get("stop_reason"),
        "data": s.get("data") or _empty_data(),
        "steps": steps,
        "usage": {
            "agent_steps": len(steps),
            **usage,
            "cost_usd": round(usage["cost_usd"], 8),
            "elapsed_time": round(s.get("finished_at", time.time()) - s.get("started_at", time.time()), 3),
        },
        "budget": {"query": policies.remaining(limits, usage) if limits else None, "session": None},
        "limits": limits,
        "findings": s.get("findings", []),
        "agent": s.get("agent") or {},
        "audit_trail": s.get("audit", []),
    }


def _execute(graph, timeout: float, config: dict, payload: dict) -> None:
    deadline = time.monotonic() + timeout
    try:
        for _ in graph.stream(payload, config):  # una iteración por nodo completado
            if time.monotonic() > deadline and graph.get_state(config).next:
                _force_partial(graph, config, "timeout", f"Se superó el timeout global de {timeout:.0f}s.")
                break
    except (StepLimitExceeded, GraphRecursionError):
        _force_partial(graph, config, "step_limit", f"Se alcanzó el máximo de {GRAPH_NODE_LIMIT} nodos.")
    except Exception as e:  # cualquier fallo inesperado termina en respuesta parcial persistida
        logger.exception("Fallo inesperado en %s", config["configurable"]["thread_id"])
        _force_partial(graph, config, "agent_error", f"{type(e).__name__}: {e}")


def _force_partial(graph, config: dict, code: str, reason: str) -> None:
    """Cierra el flujo con lo obtenido hasta el momento y una advertencia, sin perder el estado."""
    state = graph.get_state(config).values
    stop = policies.stop(code, reason)
    logger.warning("Corte forzado (%s): %s", code, reason)
    graph.update_state(config, {
        "status": "partial", "stop": stop, "stop_reason": code,
        "warning": f"{policies.warning_for(code)} {reason}",
        "answer": partial_answer(state.get("data") or _empty_data(), ""),
        "finished_at": time.time(),
        "audit": [{"step": state.get("steps", 0), "actor": "orchestrator", "action": "forced_partial",
                   "timestamp": datetime.now(UTC).isoformat(), "detail": {"trigger": code, "reason": reason}}],
    }, as_node="respond")
