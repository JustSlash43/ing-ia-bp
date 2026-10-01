"""Casos de uso del análisis financiero. Reciben los puertos como argumentos."""

import uuid

from financial_agent.application.ports import ChargeSession, GetSessionUsage, ReadAnalysis, RunAnalysis
from financial_agent.domain.models import AnalysisRequest, NotFound
from financial_agent.domain.policies import session_budget


def analyze(analyst_id: str, session_id: str, query: str, max_budget_usd: float, *,
            session_token_limit: int, run: RunAnalysis, get_usage: GetSessionUsage,
            charge: ChargeSession) -> dict:
    request = AnalysisRequest(analyst_id.strip(), session_id.strip(), query.strip(), max_budget_usd)
    usage = get_usage(request.session_id)
    result = run(str(uuid.uuid4()), request, max(0, session_token_limit - usage["tokens_used"]))
    updated = charge(request.session_id, request.analyst_id, result["usage"]["tokens_used"], result["usage"]["cost_usd"])
    result["budget"]["session"] = session_budget(session_token_limit, updated)
    return result


def get_analysis(analysis_id: str, *, read: ReadAnalysis) -> dict:
    result = read(analysis_id)
    if result is None:
        raise NotFound(analysis_id)
    return result


def get_session_budget(session_id: str, *, session_token_limit: int, get_usage: GetSessionUsage) -> dict:
    return {"session_id": session_id, **session_budget(session_token_limit, get_usage(session_id))}
