"""Tipos del dominio, catálogo de agentes (Agent Cards) y errores.

No depende de LangChain, LangGraph ni FastAPI.
"""

import re
from dataclasses import asdict, dataclass

ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_QUERY_LENGTH = 2000
MAX_BUDGET_USD = 5.0  # techo de seguridad por consulta


class AnalysisError(Exception):
    pass


class InvalidQuery(AnalysisError):
    pass


class AgentFailure(AnalysisError):
    """El sub-agente no entregó un resultado utilizable."""


class StepLimitExceeded(AnalysisError):
    pass


class NotFound(AnalysisError):
    pass


@dataclass(frozen=True)
class AnalysisRequest:
    analyst_id: str
    session_id: str
    query: str
    max_budget_usd: float

    def __post_init__(self):
        for field in ("analyst_id", "session_id"):
            if not ID_PATTERN.match(getattr(self, field)):
                raise InvalidQuery(f"{field} debe tener 1-64 caracteres alfanuméricos, '-' o '_'")
        if not 3 <= len(self.query.strip()) <= MAX_QUERY_LENGTH:
            raise InvalidQuery(f"query debe tener entre 3 y {MAX_QUERY_LENGTH} caracteres")
        if not 0 < self.max_budget_usd <= MAX_BUDGET_USD:
            raise InvalidQuery(f"max_budget_usd debe ser mayor a 0 y menor o igual a {MAX_BUDGET_USD}")


@dataclass(frozen=True)
class Pricing:
    """Precio del modelo en USD por millón de tokens."""

    input_per_million: float
    output_per_million: float


@dataclass(frozen=True)
class AgentCard:
    """Descriptor de un sub-agente, inspirado en el Agent Card de A2A."""

    name: str
    description: str
    skills: tuple[str, ...]
    allowed_tools: frozenset[str]

    def allows(self, tool: str) -> bool:
        return tool in self.allowed_tools

    def to_dict(self) -> dict:
        return {**asdict(self), "skills": list(self.skills), "allowed_tools": sorted(self.allowed_tools)}


AGENT_CARDS: dict[str, AgentCard] = {card.name: card for card in (
    AgentCard("financial_analyst_agent",
              "Analiza indicadores, ratios y tendencias financieras sobre varios periodos.",
              ("financial_data_lookup", "ratio_analysis", "trend_analysis"),
              frozenset({"search_financial_data", "calculate_ratio"})),
)}
