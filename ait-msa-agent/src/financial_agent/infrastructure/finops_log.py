"""Logs FinOps estructurados: una línea JSON por evento.

Cloud Run/Cloud Logging interpreta cada línea JSON como un log estructurado (campo `severity`),
lo que permite filtrar y crear métricas por session_id, analyst_id, costo o tokens.
"""

import json
import logging
import sys
from datetime import UTC, datetime

logger = logging.getLogger("financial_agent.finops")


def configure() -> None:
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False


def log_event(event: str, severity: str = "INFO", **fields) -> None:
    record = {"severity": severity, "event": event, "timestamp": datetime.now(UTC).isoformat(), **fields}
    logger.log(logging.getLevelName(severity), json.dumps(record, ensure_ascii=False, default=str))
