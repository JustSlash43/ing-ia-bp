import pytest
from fastapi.testclient import TestClient

from financial_agent.infrastructure import financial_data
from financial_agent.infrastructure.api import create_app
from financial_agent.infrastructure.config import Settings
from tests.fake_llm import FakeLLM


@pytest.fixture
def settings(tmp_path):
    db = str(tmp_path / "test.db")
    financial_data.seed(db)
    return Settings(_env_file=None, groq_api_key="test", sqlite_path=db, tool_delay_seconds=0)


@pytest.fixture
def client_for(settings):
    """Crea un cliente HTTP con un FakeLLM y ajustes opcionales de configuración."""
    def make(llm: FakeLLM | None = None, **overrides) -> TestClient:
        return TestClient(create_app(settings.model_copy(update=overrides), llm or FakeLLM()))
    return make


def body(**overrides) -> dict:
    return {"analyst_id": "A01", "session_id": "S001",
            "query": "Calcula la razón corriente de 2024", "max_budget_usd": 0.50} | overrides
