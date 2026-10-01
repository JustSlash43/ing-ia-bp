"""Datos financieros simulados en SQLite (fuente de la herramienta search_financial_data).

Cifras anuales 2021-2025 y trimestrales 2025 de una empresa ficticia, en millones de USD.
"""

import re
import sqlite3
from contextlib import closing
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS financial_data (
    metric      TEXT NOT NULL,
    period      TEXT NOT NULL,
    value       REAL NOT NULL,
    currency    TEXT NOT NULL DEFAULT 'USD',
    unit        TEXT NOT NULL DEFAULT 'millions',
    PRIMARY KEY (metric, period)
)"""

YEARS = ("2021", "2022", "2023", "2024", "2025")
ANNUAL = {
    "revenue":             (1200, 1350, 1480, 1620, 1710),
    "cost_of_goods_sold":  (720, 800, 870, 950, 1000),
    "operating_income":    (210, 245, 270, 300, 318),
    "ebitda":              (290, 330, 365, 402, 420),
    "net_income":          (120, 138, 152, 171, 165),
    "interest_expense":    (35, 38, 42, 44, 47),
    "total_assets":        (2400, 2580, 2750, 2950, 3100),
    "total_liabilities":   (1450, 1540, 1620, 1720, 1800),
    "equity":              (950, 1040, 1130, 1230, 1300),
    "current_assets":      (820, 870, 930, 990, 1040),
    "current_liabilities": (560, 590, 610, 650, 700),
    "inventory":           (180, 195, 210, 230, 245),
    "cash":                (210, 230, 255, 270, 260),
    "operating_cash_flow": (190, 215, 240, 265, 250),
}
QUARTERLY_2025 = {
    "revenue":    (410, 425, 430, 445),
    "net_income": (40, 42, 41, 42),
}

ALIASES = {
    "ingresos": "revenue", "ventas": "revenue",
    "costo_de_ventas": "cost_of_goods_sold", "costo_ventas": "cost_of_goods_sold", "cogs": "cost_of_goods_sold",
    "utilidad_operativa": "operating_income", "utilidad_neta": "net_income", "net_profit": "net_income",
    "gastos_financieros": "interest_expense", "gasto_intereses": "interest_expense",
    "activos_totales": "total_assets", "activo_total": "total_assets",
    "pasivos_totales": "total_liabilities", "pasivo_total": "total_liabilities",
    "patrimonio": "equity", "activo_corriente": "current_assets", "activos_corrientes": "current_assets",
    "pasivo_corriente": "current_liabilities", "pasivos_corrientes": "current_liabilities",
    "inventario": "inventory", "inventarios": "inventory", "efectivo": "cash",
    "flujo_de_caja_operativo": "operating_cash_flow", "flujo_operativo": "operating_cash_flow",
}


def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    return conn


def seed(path: str) -> None:
    rows = [(m, y, v) for m, values in ANNUAL.items() for y, v in zip(YEARS, values)]
    rows += [(m, f"2025-Q{q}", v) for m, values in QUARTERLY_2025.items() for q, v in enumerate(values, 1)]
    with closing(connect(path)) as conn, conn:
        conn.executemany("INSERT OR IGNORE INTO financial_data (metric, period, value) VALUES (?, ?, ?)", rows)


def find(path: str, metric: str, period: str) -> dict | None:
    with closing(connect(path)) as conn:
        row = conn.execute("SELECT * FROM financial_data WHERE metric = ? AND period = ?",
                           (normalize_metric(metric), normalize_period(period))).fetchone()
    return dict(row) if row else None


def catalog(path: str) -> dict:
    with closing(connect(path)) as conn:
        metrics = [r[0] for r in conn.execute("SELECT DISTINCT metric FROM financial_data ORDER BY metric")]
        periods = [r[0] for r in conn.execute("SELECT DISTINCT period FROM financial_data ORDER BY period")]
    return {"metrics": metrics, "periods": periods, "currency": "USD", "unit": "millions"}


def normalize_metric(metric: str) -> str:
    key = re.sub(r"[\s\-]+", "_", str(metric).strip().lower())
    return ALIASES.get(key, key)


def normalize_period(period: str) -> str:
    p = str(period).strip().upper().replace("FY", "").replace(" ", "")
    if m := re.fullmatch(r"(\d{4})[-_/]?Q([1-4])", p) or re.fullmatch(r"Q([1-4])[-_/]?(\d{4})", p):
        year, quarter = (m.group(1), m.group(2)) if len(m.group(1)) == 4 else (m.group(2), m.group(1))
        return f"{year}-Q{quarter}"
    return p
