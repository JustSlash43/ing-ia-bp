"""Herramientas mockeadas del analista financiero.

Usan response_format="content_and_artifact": el LLM lee el JSON y el orquestador toma el
artifact estructurado, que incluye los identificadores de entrada (metric, period,
numerator, denominator) para validar que corresponden a lo que se pidió.
"""

import json
import time

from langchain_core.tools import StructuredTool

from financial_agent.infrastructure import financial_data


def _result(data: dict) -> tuple[str, dict]:
    return json.dumps(data, ensure_ascii=False), data


def search_financial_data(db_path: str, delay: float, metric: str, period: str) -> tuple[str, dict]:
    time.sleep(delay)  # latencia simulada del sistema de datos financieros
    row = financial_data.find(db_path, metric, period)
    data = {"requested_metric": metric, "requested_period": period,
            "metric": financial_data.normalize_metric(metric), "period": financial_data.normalize_period(period)}
    if row is None:
        available = financial_data.catalog(db_path)
        return _result({**data, "found": False,
                        "error": "No hay datos para esa métrica y periodo.",
                        "available_metrics": available["metrics"], "available_periods": available["periods"]})
    return _result({**data, "found": True, "value": row["value"], "currency": row["currency"], "unit": row["unit"]})


def calculate_ratio(numerator: float, denominator: float) -> tuple[str, dict]:
    data = {"numerator": numerator, "denominator": denominator}
    if denominator == 0:
        return _result({**data, "result": None, "error": "El denominador no puede ser 0."})
    return _result({**data, "result": round(numerator / denominator, 4)})


SEARCH_DESCRIPTION = """Busca el valor de una métrica financiera para un periodo (millones de USD).
metric: revenue, cost_of_goods_sold, operating_income, ebitda, net_income, interest_expense,
total_assets, total_liabilities, equity, current_assets, current_liabilities, inventory, cash,
operating_cash_flow. period: año '2021'..'2025' o trimestre '2025-Q1'..'2025-Q4' (solo revenue y net_income)."""

RATIO_DESCRIPTION = "Calcula numerator / denominator y devuelve el resultado numérico (4 decimales)."


def build_tools(db_path: str, delay: float) -> dict[str, StructuredTool]:
    def search(metric: str, period: str) -> tuple[str, dict]:
        return search_financial_data(db_path, delay, metric, period)

    tools = (
        StructuredTool.from_function(search, name="search_financial_data", description=SEARCH_DESCRIPTION,
                                     response_format="content_and_artifact"),
        StructuredTool.from_function(calculate_ratio, name="calculate_ratio", description=RATIO_DESCRIPTION,
                                     response_format="content_and_artifact"),
    )
    return {t.name: t for t in tools}

