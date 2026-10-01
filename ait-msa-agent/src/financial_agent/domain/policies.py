"""Reglas FinOps deterministas: el LLM nunca decide si se sigue gastando o se corta.

Cada regla devuelve un *finding* con veredicto ("proceed" | "stop"), un código de
motivo, un texto legible y una propuesta de solución para el analista.
"""

import json
from collections import Counter

from financial_agent.domain.models import Pricing

STOP_REASONS = ("session_budget", "token_budget", "input_token_budget", "output_token_budget", "cost_budget",
                "step_limit", "timeout", "loop_detected", "tool_errors", "agent_error")

WARNINGS = {
    "session_budget": "Presupuesto de tokens de la sesión agotado: no se ejecutó el análisis.",
    "token_budget": "Presupuesto de tokens de la consulta agotado: la respuesta es parcial.",
    "input_token_budget": "Presupuesto de tokens de entrada (input) de la consulta agotado: la respuesta es parcial.",
    "output_token_budget": "Presupuesto de tokens de salida (output) de la consulta agotado: la respuesta es parcial.",
    "cost_budget": "Presupuesto en USD de la consulta (max_budget_usd) agotado: la respuesta es parcial.",
    "step_limit": "Se alcanzó el máximo de pasos del agente: la respuesta es parcial.",
    "timeout": "Se alcanzó el timeout global: la respuesta es parcial.",
    "loop_detected": "Circuit breaker: el agente repitió la misma herramienta con los mismos datos; se detuvo el bucle.",
    "tool_errors": "Circuit breaker: demasiados errores consecutivos de herramientas.",
    "agent_error": "El agente falló durante el análisis: se devuelven los datos obtenidos hasta el momento.",
}

PROPOSALS = {
    "session_budget": "Abrir una nueva sesión o solicitar ampliación del presupuesto de la sesión.",
    "token_budget": "Dividir la consulta en preguntas más acotadas (menos métricas o periodos).",
    "input_token_budget": "Acotar la consulta: cada paso reenvía todo el contexto, que crece con cada dato obtenido.",
    "output_token_budget": "Pedir una respuesta más breve o acotar la cantidad de métricas.",
    "cost_budget": "Aumentar max_budget_usd o acotar la consulta.",
    "step_limit": "Acotar la consulta a menos métricas o periodos por pregunta.",
    "timeout": "Reintentar con una consulta más acotada.",
    "loop_detected": "Reformular la consulta indicando métricas y periodos exactos.",
    "tool_errors": "Verificar que las métricas y periodos existan en el catálogo.",
    "agent_error": "Reintentar la consulta; si persiste, revisar la disponibilidad del LLM.",
}


def finding(source: str, verdict: str, code: str | None, reason: str, proposal: str | None = None) -> dict:
    return {"source": source, "verdict": verdict, "code": code, "reason": reason, "proposed_resolution": proposal}


def stop(code: str, reason: str) -> dict:
    return finding("finops", "stop", code, reason, PROPOSALS[code])


# ---------------------------------------------------------------------- costos y límites

def cost_usd(input_tokens: int, output_tokens: int, pricing: Pricing) -> float:
    return (input_tokens * pricing.input_per_million + output_tokens * pricing.output_per_million) / 1_000_000


def query_limits(max_budget_usd: float, session_tokens_remaining: int, *, max_tokens_budget: int,
                 max_input_tokens_budget: int, max_output_tokens_budget: int, max_steps: int,
                 timeout_seconds: float, max_output_tokens: int, loop_threshold: int,
                 max_consecutive_tool_errors: int) -> dict:
    """Límites efectivos de una consulta: el presupuesto de tokens nunca supera lo que le queda a la sesión.

    Hay tres topes de tokens independientes: total (input + output), solo input y solo output.
    """
    return {
        "max_steps": max_steps,
        "token_budget": max(0, min(max_tokens_budget, session_tokens_remaining)),
        "input_token_budget": max_input_tokens_budget,
        "output_token_budget": max_output_tokens_budget,
        "cost_budget_usd": max_budget_usd,
        "timeout_seconds": timeout_seconds,
        "max_output_tokens": max_output_tokens,
        "loop_threshold": loop_threshold,
        "max_consecutive_tool_errors": max_consecutive_tool_errors,
        "session_tokens_remaining": session_tokens_remaining,
    }


def remaining(limits: dict, usage: dict) -> dict:
    """Presupuesto restante de la consulta; `usage` viene de token_usage()."""
    return {
        "token_budget": limits["token_budget"],
        "tokens_used": usage["tokens_used"],
        "tokens_remaining": max(0, limits["token_budget"] - usage["tokens_used"]),
        "input_token_budget": limits["input_token_budget"],
        "input_tokens_used": usage["input_tokens"],
        "input_tokens_remaining": max(0, limits["input_token_budget"] - usage["input_tokens"]),
        "output_token_budget": limits["output_token_budget"],
        "output_tokens_used": usage["output_tokens"],
        "output_tokens_remaining": max(0, limits["output_token_budget"] - usage["output_tokens"]),
        "cost_budget_usd": limits["cost_budget_usd"],
        "cost_usd": round(usage["cost_usd"], 8),
        "cost_remaining_usd": round(max(0.0, limits["cost_budget_usd"] - usage["cost_usd"]), 8),
    }


def token_usage(steps: list[dict]) -> dict:
    """Tokens de entrada/salida y costo consumidos por una solicitud, a partir de sus pasos."""
    return {
        "input_tokens": steps[-1]["cumulative_input_tokens"] if steps else 0,
        "output_tokens": steps[-1]["cumulative_output_tokens"] if steps else 0,
        "tokens_used": steps[-1]["cumulative_tokens"] if steps else 0,
        "cost_usd": steps[-1]["cumulative_cost"] if steps else 0.0,
    }


def session_budget(token_limit: int, usage: dict) -> dict:
    return {
        "token_limit": token_limit,
        "tokens_used": usage["tokens_used"],
        "tokens_remaining": max(0, token_limit - usage["tokens_used"]),
        "cost_usd": round(usage["cost_usd"], 8),
        "queries": usage["queries"],
    }


# ---------------------------------------------------------------------- reglas

def evaluate_session_budget(limits: dict) -> dict:
    """Antes de gastar: la sesión debe poder pagar al menos una llamada completa al LLM."""
    available, needed = limits["token_budget"], limits["max_output_tokens"] * 2
    if available < needed:
        return stop("session_budget", f"La sesión tiene {available} tokens disponibles; "
                                      f"se necesitan al menos {needed} para una llamada al LLM.")
    return finding("finops", "proceed", None, f"Presupuesto disponible para la consulta: {available} tokens.")


def check_next_step(steps: list[dict], limits: dict, elapsed_seconds: float) -> dict | None:
    """Decide si el agente puede ejecutar un paso más. Usa estimaciones para no pasarse del límite.

    Estimación del siguiente paso:
      - input: el input del último paso + max_output_tokens (el contexto crece con la respuesta
        anterior y los resultados de las herramientas);
      - output: max_output_tokens (lo máximo que el LLM puede generar en una llamada);
      - total y costo: proporcionales a lo anterior. Tiempo: latencia promedio por paso.
    """
    if not steps:
        return None
    last = steps[-1]
    tokens, cost = last["cumulative_tokens"], last["cumulative_cost"]
    used_in, used_out = last["cumulative_input_tokens"], last["cumulative_output_tokens"]
    next_in = last["input_tokens"] + limits["max_output_tokens"]
    next_out = limits["max_output_tokens"]
    next_tokens = last["tokens_used"] + limits["max_output_tokens"]
    next_cost = last["step_cost"] * (next_tokens / max(last["tokens_used"], 1))
    avg_latency = elapsed_seconds / len(steps)

    if len(steps) >= limits["max_steps"]:
        return stop("step_limit", f"Se ejecutaron {len(steps)} de {limits['max_steps']} pasos permitidos.")
    if tokens + next_tokens > limits["token_budget"]:
        return stop("token_budget", f"Usados {tokens} tokens; el siguiente paso (~{next_tokens}) "
                                    f"superaría el presupuesto de {limits['token_budget']}.")
    if used_in + next_in > limits["input_token_budget"]:
        return stop("input_token_budget", f"Usados {used_in} tokens de input; el siguiente paso (~{next_in}) "
                                          f"superaría el presupuesto de input de {limits['input_token_budget']}.")
    if used_out + next_out > limits["output_token_budget"]:
        return stop("output_token_budget", f"Usados {used_out} tokens de output; el siguiente paso (hasta {next_out}) "
                                           f"superaría el presupuesto de output de {limits['output_token_budget']}.")
    if cost + next_cost > limits["cost_budget_usd"]:
        return stop("cost_budget", f"Costo acumulado ${cost:.6f}; el siguiente paso (~${next_cost:.6f}) "
                                   f"superaría max_budget_usd=${limits['cost_budget_usd']}.")
    if elapsed_seconds + avg_latency > limits["timeout_seconds"]:
        return stop("timeout", f"Transcurridos {elapsed_seconds:.1f}s; el siguiente paso (~{avg_latency:.1f}s) "
                               f"superaría el timeout global de {limits['timeout_seconds']:.0f}s.")
    return None


def detect_loop(history: list[dict], planned: list[dict], threshold: int) -> dict | None:
    """Circuit breaker: abre el circuito si una llamada idéntica (herramienta + argumentos) se repite."""
    counts = Counter(_signature(c) for c in history + planned)
    repeated = [sig for sig, n in counts.items() if n >= threshold and sig in {_signature(c) for c in planned}]
    if repeated:
        return stop("loop_detected", f"Llamada repetida {counts[repeated[0]]} veces: {repeated[0]}.")
    return None


def check_tool_errors(tool_results: list[dict], max_consecutive: int) -> dict | None:
    streak = 0
    for result in tool_results:
        streak = streak + 1 if result.get("error") else 0
    if streak >= max_consecutive:
        return stop("tool_errors", f"{streak} errores consecutivos de herramientas.")
    return None


def decide(stop_finding: dict | None, has_answer: bool) -> str:
    """completed: el agente terminó su respuesta sin cortes. partial: cualquier corte FinOps o error."""
    return "completed" if stop_finding is None and has_answer else "partial"


def warning_for(code: str | None) -> str | None:
    return WARNINGS.get(code) if code else None


def _signature(call: dict) -> str:
    return f"{call['tool']}({json.dumps(call['args'], sort_keys=True, ensure_ascii=False)})"

