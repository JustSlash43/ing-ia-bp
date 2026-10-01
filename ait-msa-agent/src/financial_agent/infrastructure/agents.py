"""Sub-agente de análisis financiero (LangChain `create_agent`) con control FinOps por paso.

El agente se construye a partir de su AgentCard y se expone como una función
`task(texto, limits, context) -> resultado`, al estilo A2A: el orquestador envía la tarea
y sus límites, y recibe artefactos + métricas por paso.

Un *paso* es una iteración del agente: una llamada al LLM más las herramientas que pidió.
`run_task` recorre el agente con `stream()` y, después de cada actualización, mide tokens,
costo y tiempo, y consulta las reglas del dominio (presupuesto, max_steps, timeout,
circuit breaker). Si una regla corta, se cierra el stream y el siguiente paso no se ejecuta.

La allowlist se aplica en dos capas:
  1. Al agente solo se le registran las herramientas de su card.
  2. Un middleware bloquea en ejecución cualquier llamada fuera de la allowlist.
"""

import re
import time
from collections.abc import Callable
from functools import partial

from langchain.agents import create_agent
from langchain.agents.middleware import wrap_tool_call
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphRecursionError

from financial_agent.domain import policies
from financial_agent.domain.models import AGENT_CARDS, AgentCard, Pricing
from financial_agent.infrastructure.finops_log import log_event

AgentTask = Callable[[str, dict, dict], dict]

PROMPTS = {
    "financial_analyst_agent": """Eres un agente de análisis financiero que asiste a analistas de un banco.
- Obtén cada dato con search_financial_data (una llamada por métrica y periodo). Si necesitas varios
  datos, pídelos todos en paralelo en la misma respuesta.
- Calcula cada ratio con calculate_ratio usando los valores obtenidos; nunca calcules de memoria.
- No repitas una llamada con los mismos argumentos.
- Si un dato no existe, dilo; nunca inventes cifras.
- Responde en español, en máximo 150 palabras: valores con su periodo, ratios y la tendencia observada.
- Entrega solo análisis; no emitas recomendaciones de compra o venta.""",
}


def build_agents(llm: BaseChatModel, tools: dict[str, BaseTool], pricing: Pricing) -> dict[str, AgentTask]:
    """Registro de sub-agentes: nombre -> función que ejecuta una tarea con límites FinOps."""
    registry = {}
    for card in AGENT_CARDS.values():
        agent = create_agent(
            model=llm,
            tools=[tools[name] for name in card.allowed_tools],
            system_prompt=PROMPTS[card.name],
            middleware=[allowlist_guard(card)],
            name=card.name,
        )
        registry[card.name] = partial(run_task, agent, card, pricing)
    return registry


def allowlist_guard(card: AgentCard):
    @wrap_tool_call(name=f"{card.name}_allowlist")
    def guard(request, handler):
        tool = request.tool_call["name"]
        if card.allows(tool):
            return handler(request)
        return ToolMessage(f"ERROR: '{card.name}' no tiene permitido usar '{tool}'.",
                           name=tool, tool_call_id=request.tool_call["id"], status="error")
    return guard


def run_task(agent, card: AgentCard, pricing: Pricing, task: str, limits: dict, context: dict) -> dict:
    """Ejecuta la tarea paso a paso y devuelve artefactos, métricas por paso y el motivo de corte."""
    started_at = context["started_at"]
    steps: list[dict] = []
    artifacts: list[dict] = []
    tool_calls: list[dict] = []
    blocked: list[dict] = []
    answer, stop, pending = "", None, 0
    # Segunda barrera de max_steps: cada paso son 2 supersteps (model + tools).
    config = {"recursion_limit": limits["max_steps"] * 2 + 2,
              "metadata": {k: context[k] for k in ("analysis_id", "analyst_id", "session_id")},
              "tags": ["financial-analysis", card.name]}

    try:
        stream = agent.stream({"messages": [HumanMessage(task)]}, config, stream_mode="updates")
        for update in stream:
            for node, data in update.items():
                messages = (data or {}).get("messages", [])
                if node == "model":
                    ai = next(m for m in reversed(messages) if isinstance(m, AIMessage))
                    steps.append(_meter(ai, steps, pricing, limits, started_at))
                    planned = [{"tool": c["name"], "args": c["args"]} for c in ai.tool_calls]
                    pending = len(planned)
                    if not planned:
                        answer = _clean(ai.text)
                    else:
                        stop = policies.detect_loop(tool_calls, planned, limits["loop_threshold"])
                        tool_calls += [c for c in planned if card.allows(c["tool"])]
                        blocked += [c for c in planned if not card.allows(c["tool"])]
                    if not planned or stop:  # paso cerrado: respuesta final o circuito abierto
                        _log_step(steps[-1], context, stop)
                elif node == "tools":  # llamadas en paralelo llegan como varias actualizaciones
                    results = [m for m in messages if isinstance(m, ToolMessage)]
                    artifacts += [{"tool": m.name, "tool_call_id": m.tool_call_id, **m.artifact}
                                  for m in results if isinstance(m.artifact, dict)]
                    pending -= len(results)
                    if pending > 0:
                        continue
                    _close_step(steps[-1], started_at)
                    stop = (policies.check_tool_errors(artifacts, limits["max_consecutive_tool_errors"])
                            or policies.check_next_step(steps, limits, time.time() - started_at))
                    _log_step(steps[-1], context, stop)
            if stop:
                stream.close()  # el siguiente paso no se ejecuta
                break
    except GraphRecursionError:
        stop = policies.stop("step_limit", f"Se alcanzó el límite de recursión del agente ({config['recursion_limit']}).")
    except Exception as e:  # el error se convierte en estado; se conservan los datos ya obtenidos
        stop = policies.stop("agent_error", f"[{card.name}] {type(e).__name__}: {e}")

    return {
        "agent": card.name,
        "steps": [{k: v for k, v in s.items() if not k.startswith("_")} for s in steps],
        "artifacts": artifacts,
        "tool_calls": tool_calls,
        "blocked_tool_calls": blocked,
        "summary": answer,
        "stop": stop,
    }


def _meter(ai: AIMessage, steps: list[dict], pricing: Pricing, limits: dict, started_at: float) -> dict:
    """Métricas FinOps de un paso a partir del usage_metadata que reporta el proveedor."""
    usage = ai.usage_metadata or {}
    input_tokens, output_tokens = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    tokens = usage.get("total_tokens", input_tokens + output_tokens)
    cost = policies.cost_usd(input_tokens, output_tokens, pricing)
    prev = steps[-1] if steps else {"cumulative_tokens": 0, "cumulative_input_tokens": 0,
                                    "cumulative_output_tokens": 0, "cumulative_cost": 0.0}
    cumulative_tokens, cumulative_cost = prev["cumulative_tokens"] + tokens, prev["cumulative_cost"] + cost
    cumulative_in = prev["cumulative_input_tokens"] + input_tokens
    cumulative_out = prev["cumulative_output_tokens"] + output_tokens
    now = time.time()
    return {
        "step_number": len(steps) + 1,
        "tool_called": [c["name"] for c in ai.tool_calls] or None,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "tokens_used": tokens,
        "step_cost": round(cost, 8),
        "cumulative_tokens": cumulative_tokens,
        "cumulative_input_tokens": cumulative_in,
        "cumulative_output_tokens": cumulative_out,
        "cumulative_cost": round(cumulative_cost, 8),
        "remaining_tokens": max(0, limits["token_budget"] - cumulative_tokens),
        "remaining_input_tokens": max(0, limits["input_token_budget"] - cumulative_in),
        "remaining_output_tokens": max(0, limits["output_token_budget"] - cumulative_out),
        "remaining_budget_usd": round(max(0.0, limits["cost_budget_usd"] - cumulative_cost), 8),
        "elapsed_time": round(now - started_at, 3),
        "llm_latency_ms": round((now - steps[-1]["_ended_at"] if steps else now - started_at) * 1000),
        "tool_latency_ms": 0,
        "_ended_at": now,
    }


def _close_step(step: dict, started_at: float) -> None:
    now = time.time()
    step["tool_latency_ms"] = round((now - step["_ended_at"]) * 1000)
    step["elapsed_time"] = round(now - started_at, 3)
    step["_ended_at"] = now


def _log_step(step: dict, context: dict, stop: dict | None) -> None:
    log_event("agent_step", **{k: context[k] for k in ("analysis_id", "analyst_id", "session_id")},
              **{k: v for k, v in step.items() if not k.startswith("_")},
              stop_reason=stop["code"] if stop else None)


def _clean(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
