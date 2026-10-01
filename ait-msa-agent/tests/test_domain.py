import pytest

from financial_agent.domain import policies
from financial_agent.domain.models import AnalysisRequest, InvalidQuery, Pricing

LIMITS = policies.query_limits(0.50, 30_000, max_tokens_budget=10_000, max_input_tokens_budget=8_000,
                               max_output_tokens_budget=2_000, max_steps=8, timeout_seconds=45,
                               max_output_tokens=400, loop_threshold=2, max_consecutive_tool_errors=3)


def step(n: int, tokens: int = 560, cost: float = 0.0002, output: int = 60) -> dict:
    return {"step_number": n, "tokens_used": tokens, "step_cost": cost,
            "input_tokens": tokens - output, "output_tokens": output,
            "cumulative_tokens": tokens * n, "cumulative_input_tokens": (tokens - output) * n,
            "cumulative_output_tokens": output * n, "cumulative_cost": cost * n}


@pytest.mark.parametrize("kwargs", [
    {"analyst_id": ""}, {"session_id": "S 001"}, {"query": "ab"}, {"query": "x" * 2001},
    {"max_budget_usd": 0}, {"max_budget_usd": 5.01},
])
def test_invalid_request(kwargs):
    data = {"analyst_id": "A01", "session_id": "S001", "query": "ratio corriente 2024", "max_budget_usd": 0.5} | kwargs
    with pytest.raises(InvalidQuery):
        AnalysisRequest(**data)


def test_cost_usd():
    assert policies.cost_usd(1_000_000, 1_000_000, Pricing(0.29, 0.59)) == pytest.approx(0.88)


def test_query_token_budget_never_exceeds_session_remaining():
    assert LIMITS["token_budget"] == 10_000
    limits = policies.query_limits(0.5, 3_000, **{k: LIMITS[k] for k in (
        "max_steps", "timeout_seconds", "max_output_tokens", "loop_threshold", "max_consecutive_tool_errors")},
        max_tokens_budget=10_000, max_input_tokens_budget=8_000, max_output_tokens_budget=2_000)
    assert limits["token_budget"] == 3_000


@pytest.mark.parametrize("available, verdict", [(799, "stop"), (800, "proceed")])
def test_session_budget_requires_one_full_llm_call(available, verdict):
    result = policies.evaluate_session_budget({**LIMITS, "token_budget": available})
    assert result["verdict"] == verdict
    assert (result["code"] == "session_budget") == (verdict == "stop")


def test_next_step_allowed_within_limits():
    assert policies.check_next_step([step(1)], LIMITS, elapsed_seconds=3) is None


@pytest.mark.parametrize("steps, limits, elapsed, code", [
    ([step(n) for n in range(1, 9)], LIMITS, 5, "step_limit"),
    ([step(1, tokens=4_000), step(2, tokens=4_000)], LIMITS, 5, "token_budget"),
    ([step(1, cost=0.2), step(2, cost=0.2)], LIMITS, 5, "cost_budget"),
    # 2 x 3.000 de input + (3.000 + 400) estimados > 8.000, aunque el total (6.200 + 3.500) cabe en 10.000
    ([step(1, tokens=3_100, output=100), step(2, tokens=3_100, output=100)], LIMITS, 5, "input_token_budget"),
    # 5 x 400 de output + 400 estimados > 2.000 (el input y el total siguen holgados)
    ([step(n, tokens=800, output=400) for n in range(1, 6)], LIMITS, 5, "output_token_budget"),
    ([step(1), step(2)], LIMITS, 40, "timeout"),
])
def test_next_step_stops(steps, limits, elapsed, code):
    assert policies.check_next_step(steps, limits, elapsed)["code"] == code


def test_token_budget_uses_estimate_before_overspending():
    # 9.100 usados + (560 + 400) estimados > 10.000: se corta antes de gastar
    steps = [{**step(1), "cumulative_tokens": 9_100}]
    assert policies.check_next_step(steps, LIMITS, 1)["code"] == "token_budget"


def test_circuit_breaker_detects_repeated_call():
    call = {"tool": "search_financial_data", "args": {"metric": "revenue", "period": "2024"}}
    other = {"tool": "search_financial_data", "args": {"metric": "revenue", "period": "2023"}}
    assert policies.detect_loop([call], [other], threshold=2) is None
    assert policies.detect_loop([call], [dict(call)], threshold=2)["code"] == "loop_detected"


def test_circuit_breaker_on_consecutive_tool_errors():
    assert policies.check_tool_errors([{"error": "x"}] * 2 + [{}], 3) is None
    assert policies.check_tool_errors([{}] + [{"error": "x"}] * 3, 3)["code"] == "tool_errors"


@pytest.mark.parametrize("stop, has_answer, status", [
    (None, True, "completed"), (None, False, "partial"), ({"code": "timeout"}, True, "partial"),
])
def test_decide(stop, has_answer, status):
    assert policies.decide(stop, has_answer) == status


def test_every_stop_reason_has_warning_and_proposal():
    for code in policies.STOP_REASONS:
        assert policies.warning_for(code) and policies.PROPOSALS[code]


def test_token_usage_sums_input_and_output():
    steps = [{"cumulative_input_tokens": 500, "cumulative_output_tokens": 60, "cumulative_tokens": 560,
              "cumulative_cost": 0.1},
             {"cumulative_input_tokens": 1200, "cumulative_output_tokens": 100, "cumulative_tokens": 1300,
              "cumulative_cost": 0.2}]
    assert policies.token_usage(steps) == {"input_tokens": 1200, "output_tokens": 100,
                                           "tokens_used": 1300, "cost_usd": 0.2}
    assert policies.token_usage([])["tokens_used"] == 0
