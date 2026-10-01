// Casos de prueba: cada uno indica el tipo de respuesta que se espera del agente.
// expect.status: "completed" | "partial" | "http_422"; expect.stopReasons: motivos de corte aceptados.
// Los casos marcados con llm=true dependen del comportamiento del modelo real.
window.TEST_CASES = [
  {
    id: "complete-ratio",
    title: "Análisis completo · liquidez",
    description: "Dos búsquedas en paralelo y un ratio. Debe terminar dentro de todos los límites.",
    request: { query: "Calcula la razón corriente (activo corriente / pasivo corriente) de 2024.", max_budget_usd: 0.5 },
    expect: { status: "completed", stopReasons: [null] },
  },
  {
    id: "complete-trend",
    title: "Multi-periodo · tendencia",
    description: "Margen neto de tres años y su tendencia. Consulta compleja que suele completarse en 3-4 pasos.",
    request: { query: "Calcula el margen neto (utilidad neta / ingresos) de 2023, 2024 y 2025 y describe la tendencia.", max_budget_usd: 0.5 },
    expect: { status: "completed", stopReasons: [null] },
    llm: true,
  },
  {
    id: "complete-quarterly",
    title: "Trimestral · crecimiento",
    description: "Compara trimestres de 2025 y calcula el crecimiento.",
    request: { query: "Compara los ingresos de 2025-Q1 y 2025-Q4 y calcula el ratio Q4/Q1.", max_budget_usd: 0.5 },
    expect: { status: "completed", stopReasons: [null] },
  },
  {
    id: "missing-data",
    title: "Dato inexistente",
    description: "La herramienta responde found=false; el agente debe decirlo sin inventar cifras.",
    request: { query: "¿Cuál fue el goodwill de la empresa en 2024?", max_budget_usd: 0.5 },
    expect: { status: "completed", stopReasons: [null] },
    llm: true,
  },
  {
    id: "usd-budget",
    title: "Presupuesto USD agotado",
    description: "max_budget_usd muy bajo: se corta antes de superar el presupuesto y devuelve respuesta parcial con advertencia.",
    request: { query: "Calcula el margen neto (utilidad neta / ingresos) de 2023, 2024 y 2025 y describe la tendencia.", max_budget_usd: 0.0004 },
    expect: { status: "partial", stopReasons: ["cost_budget"] },
  },
  {
    id: "step-limit",
    title: "Máximo de pasos / tokens",
    description: "Consulta secuencial muy larga: debe cortarse por max_steps (8) o por un presupuesto de tokens (total, input u output).",
    request: {
      query: "Año por año, de 2021 a 2025 y en orden: busca los ingresos de ese año, espera el resultado, luego busca la utilidad neta, espera el resultado y calcula el margen. Solo después pasa al año siguiente. No pidas datos en paralelo.",
      max_budget_usd: 0.5,
    },
    expect: { status: "partial", stopReasons: ["step_limit", "token_budget", "input_token_budget", "output_token_budget"] },
    llm: true,
  },
  {
    id: "loop",
    title: "Circuit breaker · bucle",
    description: "Se pide repetir la misma búsqueda: el circuit breaker corta antes de ejecutar la llamada repetida.",
    request: { query: "Busca revenue de 2024. Después vuelve a llamar search_financial_data con revenue y 2024 exactamente igual para verificar el dato, y solo entonces responde.", max_budget_usd: 0.5 },
    expect: { status: "partial", stopReasons: ["loop_detected"] },
    llm: true,
  },
  {
    id: "session-budget",
    title: "Presupuesto de sesión agotado",
    description: "Repite consultas en una sesión nueva hasta agotar sus tokens (30.000 por defecto). Consume tokens reales.",
    request: { query: "Calcula el margen neto (utilidad neta / ingresos) de 2021, 2022, 2023, 2024 y 2025 y describe la tendencia.", max_budget_usd: 0.5 },
    repeatUntil: "session_budget",
    maxRuns: 15,
    expect: { status: "partial", stopReasons: ["session_budget"] },
  },
  {
    id: "invalid-query",
    title: "Entrada inválida · query vacía",
    description: "El dominio rechaza la consulta antes de gastar tokens.",
    request: { query: "", max_budget_usd: 0.5 },
    expect: { status: "http_422" },
  },
  {
    id: "invalid-budget",
    title: "Entrada inválida · presupuesto",
    description: "max_budget_usd debe ser > 0 y ≤ 5.",
    request: { query: "Ingresos de 2024", max_budget_usd: 0 },
    expect: { status: "http_422" },
  },
];
