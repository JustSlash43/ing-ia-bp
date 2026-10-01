from functools import lru_cache

from langchain_groq import ChatGroq
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from financial_agent.domain.models import Pricing


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    groq_api_key: SecretStr = SecretStr("")
    llm_model: str = "qwen/qwen3.8-27b"
    llm_request_timeout: float = 15.0
    llm_max_retries: int = 1
    # Tokens de salida por llamada. El plan gratuito de Groq limita los tokens de salida por minuto.
    llm_max_tokens: int = 400
    # Precio en USD por millón de tokens (supuesto; ajustar a la tarifa vigente del modelo).
    llm_price_input_per_million: float = 0.29
    llm_price_output_per_million: float = 0.59

    # Límites del agente (requisitos de la prueba)
    max_steps: int = 8
    max_tokens_budget: int = 10_000           # tokens por consulta (input + output)
    max_input_tokens_budget: int = 8_000      # tokens de entrada por consulta (prompt + contexto + herramientas)
    max_output_tokens_budget: int = 2_000     # tokens de salida por consulta (lo que genera el LLM)
    global_timeout_seconds: float = 45.0
    # Presupuesto por sesión: ~3 consultas complejas completas (3 x 10.000 tokens)
    session_token_budget: int = 30_000
    # Circuit breaker
    loop_threshold: int = 2                   # una llamada idéntica repetida abre el circuito
    max_consecutive_tool_errors: int = 3

    tool_delay_seconds: float = 2.0
    sqlite_path: str = "data/analysis.db"
    # Carpeta con el frontend estático (frontend/static). Si se define, la API también lo sirve en "/".
    frontend_dir: str = ""

    # Frontend de pruebas: cualquier puerto de localhost; en Cloud Run se agrega la URL del frontend.
    cors_origin_regex: str = r"^https?://(localhost|127\.0\.0\.1|\[::1?\])(:\d+)?$"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def groq_llm(settings: Settings) -> ChatGroq:
    return ChatGroq(
        model=settings.llm_model,
        api_key=settings.groq_api_key,
        temperature=0,
        reasoning_effort="none",  # sin razonamiento: menos tokens y las decisiones FinOps están en el dominio
        max_tokens=settings.llm_max_tokens,
        request_timeout=settings.llm_request_timeout,
        max_retries=settings.llm_max_retries,
    )


def pricing(settings: Settings) -> Pricing:
    return Pricing(settings.llm_price_input_per_million, settings.llm_price_output_per_million)


def limit_defaults(settings: Settings) -> dict:
    """Límites que el orquestador aplica a cada consulta (ver domain.policies.query_limits)."""
    return {
        "max_tokens_budget": settings.max_tokens_budget,
        "max_input_tokens_budget": settings.max_input_tokens_budget,
        "max_output_tokens_budget": settings.max_output_tokens_budget,
        "max_steps": settings.max_steps,
        "timeout_seconds": settings.global_timeout_seconds,
        "max_output_tokens": settings.llm_max_tokens,
        "loop_threshold": settings.loop_threshold,
        "max_consecutive_tool_errors": settings.max_consecutive_tool_errors,
    }
