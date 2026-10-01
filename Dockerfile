# Imagen única para Cloud Run: API (FastAPI + LangGraph) + frontend de pruebas en el mismo origen.
#   /            -> frontend (frontend/static)
#   /api/v1/...  -> API del agente
#   /docs        -> Swagger
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    # Cloud Run: el sistema de archivos es efímero; /tmp es escribible.
    SQLITE_PATH=/tmp/analysis.db \
    FRONTEND_DIR=/app/frontend \
    LANGSMITH_TRACING=false

WORKDIR /app

COPY ait-msa-agent/requirements.txt .
RUN pip install -r requirements.txt

COPY ait-msa-agent/pyproject.toml .
COPY ait-msa-agent/src ./src
RUN pip install --no-deps . && useradd --create-home --uid 10001 app

COPY frontend/static ./frontend

USER app
EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn financial_agent.infrastructure.api:create_app --factory --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
