"""Puertos: lo que la aplicación necesita del exterior, expresado como firmas de funciones.

La infraestructura los implementa (LangGraph, SQLite) y se inyectan en api.py.
"""

from collections.abc import Callable

from financial_agent.domain.models import AnalysisRequest

RunAnalysis = Callable[[str, AnalysisRequest, int], dict]   # (analysis_id, request, tokens de sesión) -> resultado
ReadAnalysis = Callable[[str], dict | None]                  # (analysis_id) -> resultado guardado
GetSessionUsage = Callable[[str], dict]                      # (session_id) -> {tokens_used, cost_usd, queries}
ChargeSession = Callable[[str, str, int, float], dict]       # (session_id, analyst_id, tokens, costo) -> uso actualizado
