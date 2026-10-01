# Frontend de pruebas

Es una página estática servida con la librería estándar de Python, sin dependencias. Sirve para consumir `POST /api/v1/analysis/query` y para ejecutar casos de prueba que indican el tipo de respuesta esperado.

```bash
python3 server.py                                    # http://localhost:5500 -> API en http://localhost:8000
API_URL=http://localhost:8001 PORT=5600 python3 server.py
```

## Qué muestra

- Estado de la consulta: `completed` o `partial`, con su `stop_reason` y la advertencia.
- Respuesta del agente.
- Métricas FinOps por paso: `step_number`, `tool_called`, `tokens_used`, `cumulative_cost` y `elapsed_time`.
- Barras de presupuesto restante: tokens totales, de input y de output, USD de la consulta, y tokens de la sesión.
- Datos devueltos por las herramientas, hallazgos del dominio, auditoría y JSON completo.

## Casos de prueba

Los casos están en `static/cases.js`. Cada caso usa una sesión nueva y aislada, y al terminar compara la respuesta real con la esperada (✓ / ✗).

Cuando un caso termina, su nombre pasa a ser un enlace que reabre su resultado completo. Si un caso hizo varias consultas, como el de presupuesto de sesión, se muestra un selector «Consulta 1…N». Si durante «Ejecutar todos» abres un resultado anterior, la vista se queda en ese resultado y los siguientes casos se ejecutan en segundo plano.

| Caso | Esperado |
|---|---|
| Análisis completo · liquidez | `completed` |
| Multi-periodo · tendencia | `completed` |
| Trimestral · crecimiento | `completed` |
| Dato inexistente | `completed` (el agente dice que no hay datos) |
| Presupuesto USD agotado | `partial · cost_budget` |
| Máximo de pasos / tokens | `partial · step_limit \| token_budget \| input_token_budget \| output_token_budget` |
| Circuit breaker · bucle | `partial · loop_detected` |
| Presupuesto de sesión agotado | `partial · session_budget` (repite consultas hasta agotar la sesión) |
| Entrada inválida (query / presupuesto) | HTTP 422 |

Los casos marcados con **LLM** dependen de cómo el modelo real planifica las llamadas, así que pueden variar entre ejecuciones. El resto se determina por las reglas del dominio.

## Cloud Run

```bash
PROJECT_ID=mi-proyecto API_URL=https://ait-msa-agent-xxxx.run.app ./deploy.sh
```

El backend acepta por defecto cualquier origen `https://*.run.app`; puedes restringirlo con `CORS_ORIGIN_REGEX`.
