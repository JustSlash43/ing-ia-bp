"""End-to-end por HTTP: cada tipo de respuesta que el frontend de pruebas muestra."""

from tests.conftest import body
from tests.fake_llm import FINAL_ANSWER, FakeLLM

URL = "/api/v1/analysis/query"


def test_completed_analysis(client_for):
    r = client_for().post(URL, json=body())
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "completed" and data["warning"] is None and data["answer"] == FINAL_ANSWER
    assert data["usage"]["tokens_used"] == 1680 and data["usage"]["agent_steps"] == 3
    assert data["budget"]["query"]["tokens_remaining"] == 10_000 - 1680
    assert (data["usage"]["input_tokens"], data["usage"]["output_tokens"]) == (1500, 180)
    assert data["budget"]["query"]["input_tokens_remaining"] == 8_000 - 1500
    assert data["budget"]["query"]["output_tokens_remaining"] == 2_000 - 180
    assert data["budget"]["session"] == {"token_limit": 30_000, "tokens_used": 1680, "tokens_remaining": 28_320,
                                         "cost_usd": data["usage"]["cost_usd"], "queries": 1}
    assert data["data"]["ratios"][0]["result"] == 1.5231


def test_partial_when_usd_budget_exhausted(client_for):
    data = client_for().post(URL, json=body(max_budget_usd=0.0003)).json()
    assert (data["status"], data["stop_reason"]) == ("partial", "cost_budget")
    assert "max_budget_usd" in data["warning"]
    assert "current_assets 2024: 990.00" in data["answer"]  # respuesta parcial con los datos obtenidos
    assert data["usage"]["cost_usd"] <= 0.0003


def test_partial_when_token_budget_exhausted(client_for):
    data = client_for(FakeLLM(input_tokens=4_000), max_input_tokens_budget=50_000).post(URL, json=body()).json()
    assert (data["status"], data["stop_reason"]) == ("partial", "token_budget")
    assert data["usage"]["tokens_used"] <= 10_000 and data["usage"]["agent_steps"] == 2


def test_partial_when_input_token_budget_exhausted(client_for):
    data = client_for(FakeLLM(input_tokens=1_500), max_input_tokens_budget=3_000).post(URL, json=body()).json()
    assert (data["status"], data["stop_reason"]) == ("partial", "input_token_budget")
    assert data["usage"]["input_tokens"] <= 3_000 and "input" in data["warning"]
    assert data["budget"]["query"]["input_tokens_remaining"] == 3_000 - data["usage"]["input_tokens"]


def test_partial_when_output_token_budget_exhausted(client_for):
    data = client_for(FakeLLM(output_tokens=300), max_output_tokens_budget=800).post(URL, json=body()).json()
    assert (data["status"], data["stop_reason"]) == ("partial", "output_token_budget")
    assert data["usage"]["output_tokens"] <= 800 and "output" in data["warning"]
    last = data["steps"][-1]
    assert last["remaining_output_tokens"] == 800 - last["cumulative_output_tokens"]


def test_session_budget_is_consumed_across_queries(client_for):
    client = client_for(session_token_budget=2_000)
    first, second, third = (client.post(URL, json=body()).json() for _ in range(3))
    assert first["stop_reason"] == second["stop_reason"] == "token_budget"
    assert third["stop_reason"] == "session_budget" and third["steps"] == []
    assert third["budget"]["session"]["tokens_remaining"] < 800
    assert client.get("/api/v1/sessions/S001/budget").json()["queries"] == 3
    other = client.post(URL, json=body(session_id="S002")).json()  # otra sesión no se afecta
    assert other["stop_reason"] != "session_budget" and other["steps"]


def test_timeout_returns_partial(client_for):
    data = client_for(FakeLLM(delay=0.4), global_timeout_seconds=1.0).post(URL, json=body()).json()
    assert (data["status"], data["stop_reason"]) == ("partial", "timeout")
    assert data["usage"]["elapsed_time"] < 1.5


def test_circuit_breaker_loop(client_for):
    data = client_for(FakeLLM(loop=True)).post(URL, json=body()).json()
    assert (data["status"], data["stop_reason"]) == ("partial", "loop_detected")
    assert "Circuit breaker" in data["warning"]


def test_provider_error_is_partial_not_500(client_for):
    r = client_for(FakeLLM(fail=True)).post(URL, json=body())
    assert r.status_code == 200 and r.json()["stop_reason"] == "agent_error"


def test_invalid_input_is_422(client_for):
    client = client_for()
    assert client.post(URL, json=body(query="")).status_code == 422
    assert client.post(URL, json=body(max_budget_usd=-1)).status_code == 422
    assert client.post(URL, json={"query": "sin ids"}).status_code == 422


def test_analysis_is_persisted_and_readable(client_for):
    client = client_for()
    data = client.post(URL, json=body()).json()
    assert client.get(f"/api/v1/analysis/{data['analysis_id']}").json()["status"] == "completed"
    assert client.get("/api/v1/analysis/no-existe").status_code == 404


def test_agents_and_config_endpoints(client_for):
    client = client_for()
    cards = client.get("/api/v1/agents").json()
    assert cards[0]["allowed_tools"] == ["calculate_ratio", "search_financial_data"]
    config = client.get("/api/v1/config").json()
    assert config["limits"]["max_steps"] == 8 and config["limits"]["max_tokens_budget"] == 10_000
    assert "2025-Q1" in config["catalog"]["periods"]


def test_serves_frontend_from_same_origin(client_for, tmp_path):
    (tmp_path / "index.html").write_text("<h1>front</h1>")
    client = client_for(frontend_dir=str(tmp_path))
    assert client.get("/").text == "<h1>front</h1>"
    assert '"apiUrl": ""' in client.get("/config.js").text
    assert client.get("/api/v1/agents").status_code == 200  # la API no queda tapada por el frontend
