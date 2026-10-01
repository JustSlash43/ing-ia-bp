# Imagen del backend para Cloud Run
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    # Cloud Run: el sistema de archivos es efímero; /tmp es escribible.
    SQLITE_PATH=/tmp/analysis.db \
    LANGSMITH_TRACING=false

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY pyproject.toml .
COPY src ./src
RUN pip install --no-deps . && useradd --create-home --uid 10001 app

USER app
EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn financial_agent.infrastructure.api:create_app --factory --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
