"""API HTTP y composition root (aquí se enlazan los puertos con sus implementaciones).

Arranque: uvicorn financial_agent.infrastructure.api:create_app --factory --reload
"""

import json
import logging
import sqlite3
from functools import partial
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel, Field

from financial_agent.application import use_cases
from financial_agent.domain.models import AGENT_CARDS, InvalidQuery, NotFound
from financial_agent.infrastructure import finops_log, financial_data, orchestrator, session_ledger
from financial_agent.infrastructure.agents import build_agents
from financial_agent.infrastructure.config import Settings, get_settings, groq_llm, limit_defaults, pricing
from financial_agent.infrastructure.tools import build_tools


class QueryRequest(BaseModel):
    analyst_id: str = Field(examples=["A01"])
    session_id: str = Field(examples=["S001"])
    query: str = Field(examples=["Calcula la razón corriente de 2024 y compárala con 2023"])
    max_budget_usd: float = Field(0.50, examples=[0.50])


def create_app(settings: Settings | None = None, llm=None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    finops_log.configure()

    # ---- composition root: puertos -> implementaciones
    financial_data.seed(settings.sqlite_path)
    tools = build_tools(settings.sqlite_path, settings.tool_delay_seconds)
    agents = build_agents(llm or groq_llm(settings), tools, pricing(settings))
    checkpointer = SqliteSaver(sqlite3.connect(settings.sqlite_path, check_same_thread=False))
    graph = orchestrator.build_graph(agents, limit_defaults(settings), checkpointer)

    run = partial(orchestrator.run_analysis, graph, settings.global_timeout_seconds)
    read = partial(orchestrator.read_analysis, graph)
    get_usage = partial(session_ledger.get_usage, settings.sqlite_path)
    charge = partial(session_ledger.charge, settings.sqlite_path)

    # ---- HTTP
    app = FastAPI(title="AIT MSA - Agente de Análisis Financiero", version="1.0.0")
    app.add_middleware(CORSMiddleware, allow_origin_regex=settings.cors_origin_regex,
                       allow_methods=["GET", "POST"],
                       # ngrok-skip-browser-warning: evita la página de aviso de ngrok cuando el backend se expone con ngrok
                       allow_headers=["Content-Type", "ngrok-skip-browser-warning"])
    for error, code in ((InvalidQuery, 422), (NotFound, 404)):
        app.add_exception_handler(error, lambda _, e, code=code: JSONResponse({"detail": str(e)}, code))

    @app.post("/api/v1/analysis/query")
    def query(body: QueryRequest):
        return use_cases.analyze(body.analyst_id, body.session_id, body.query, body.max_budget_usd,
                                 session_token_limit=settings.session_token_budget,
                                 run=run, get_usage=get_usage, charge=charge)

    @app.get("/api/v1/analysis/{analysis_id}")
    def get_analysis(analysis_id: str):
        return use_cases.get_analysis(analysis_id, read=read)

    @app.get("/api/v1/sessions/{session_id}/budget")
    def session_budget(session_id: str):
        return use_cases.get_session_budget(session_id, session_token_limit=settings.session_token_budget,
                                            get_usage=get_usage)

    @app.get("/api/v1/agents")
    def agent_cards():
        return [card.to_dict() for card in AGENT_CARDS.values()]

    @app.get("/api/v1/config")
    def public_config():
        """Límites vigentes y catálogo de datos (lo usa el frontend de pruebas)."""
        return {"limits": {**limit_defaults(settings), "session_token_budget": settings.session_token_budget,
                           "tool_delay_seconds": settings.tool_delay_seconds},
                "pricing_usd_per_million": {"input": settings.llm_price_input_per_million,
                                            "output": settings.llm_price_output_per_million},
                "model": settings.llm_model, "catalog": financial_data.catalog(settings.sqlite_path)}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    if settings.frontend_dir:  # imagen única: el frontend se sirve desde el mismo origen que la API
        _mount_frontend(app, Path(settings.frontend_dir))

    return app


def _mount_frontend(app: FastAPI, directory: Path) -> None:
    @app.get("/config.js", include_in_schema=False)
    def frontend_config():
        return Response(f"window.APP_CONFIG = {json.dumps({'apiUrl': ''})};", media_type="application/javascript",
                        headers={"Cache-Control": "no-store"})

    app.mount("/", StaticFiles(directory=directory, html=True), name="frontend")  # al final: no tapa la API
