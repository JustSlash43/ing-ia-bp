# Recorrido de una consulta

`POST /api/v1/analysis/query` con `{"analyst_id": "A01", "session_id": "S001", "query": "...", "max_budget_usd": 0.50}`.

## 1. Entrada HTTP y casos de uso

| # | Archivo → función | Qué hace |
|---|---|---|
| 1 | [api.py](../src/financial_agent/infrastructure/api.py#L31) `create_app` | Es el composition root. Siembra los datos mock en SQLite, crea las herramientas, el agente, el checkpointer `SqliteSaver` y el grafo, y enlaza los puertos con `partial`. |
| 2 | [api.py](../src/financial_agent/infrastructure/api.py#L56) `query` | Valida el cuerpo (Pydantic) y llama al caso de uso. |
| 3 | [use_cases.py](../src/financial_agent/application/use_cases.py#L10) `analyze` | Construye `AnalysisRequest`, que valida en el dominio y lanza `InvalidQuery`, convertido en HTTP 422. Lee el consumo de la sesión, calcula los tokens que le quedan, ejecuta el análisis y cobra los tokens a la sesión. |
| 4 | [session_ledger.py](../src/financial_agent/infrastructure/session_ledger.py#L32) `get_usage` / `charge` | Tabla `session_usage` en SQLite: tokens, costo y número de consultas por sesión. |

## 2. Orquestador (LangGraph)

| # | Archivo → función | Qué hace |
|---|---|---|
| 5 | [orchestrator.py](../src/financial_agent/infrastructure/orchestrator.py#L204) `run_analysis` | Arma el payload y la configuración (`thread_id = analysis_id`, metadata para LangSmith) y llama a `_execute`. Al final emite el log FinOps `analysis_finished`. |
| 6 | [orchestrator.py](../src/financial_agent/infrastructure/orchestrator.py#L251) `_execute` | Recorre `graph.stream` y revisa el timeout global después de cada nodo. Cualquier excepción o límite termina en `_force_partial`: respuesta parcial persistida, nunca un HTTP 500. |
| 7 | [orchestrator.py](../src/financial_agent/infrastructure/orchestrator.py#L66) `step` | Envuelve cada nodo: aplica el límite de nodos, mide la latencia y agrega la entrada de auditoría. |
| 8 | [orchestrator.py](../src/financial_agent/infrastructure/orchestrator.py#L90) `validate_input` | Normaliza la entrada y calcula los límites efectivos con [`policies.query_limits`](../src/financial_agent/domain/policies.py#L56): tokens = `min(10.000, restante de la sesión)` y USD = `max_budget_usd`. |
| 9 | [orchestrator.py](../src/financial_agent/infrastructure/orchestrator.py#L103) `budget_gate` | Aplica [`evaluate_session_budget`](../src/financial_agent/domain/policies.py#L118). Si la sesión no alcanza para una llamada completa al LLM, salta a `respond` con `stop_reason = session_budget` sin gastar tokens. |
| 10 | [orchestrator.py](../src/financial_agent/infrastructure/orchestrator.py#L110) `financial_analyst_agent` | Delega con `delegate()`, que valida que cada artifact corresponda a una llamada pedida. Separa los datos en `searches` y `ratios`, y guarda en el estado `input_tokens`, `output_tokens`, `tokens_used` y `cost_usd` (calculados con `policies.token_usage`). Un `AgentFailure` se convierte en estado. |
| 11 | [orchestrator.py](../src/financial_agent/infrastructure/orchestrator.py#L128) `policy_gate` | Aplica [`policies.decide`](../src/financial_agent/domain/policies.py#L185): `completed` si no hubo corte y hay respuesta; `partial` en cualquier otro caso. |
| 12 | [orchestrator.py](../src/financial_agent/infrastructure/orchestrator.py#L137) `respond` | Si la consulta terminó, usa la respuesta del agente. Si fue parcial, arma `partial_answer`, una respuesta determinista con los artifacts, y la advertencia de [`warning_for`](../src/financial_agent/domain/policies.py#L190). |
| 13 | [orchestrator.py](../src/financial_agent/infrastructure/orchestrator.py#L219) `read_analysis` | Arma la respuesta pública: `steps`, `usage` y `budget.query` (calculado con [`remaining`](../src/financial_agent/domain/policies.py#L78)). |

## 3. Sub-agente y control FinOps por paso

| # | Archivo → función | Qué hace |
|---|---|---|
| 14 | [agents.py](../src/financial_agent/infrastructure/agents.py#L47) `build_agents` | Crea el agente con `create_agent`. Solo registra las herramientas de su `AgentCard` y agrega el middleware `allowlist_guard`. |
| 15 | [agents.py](../src/financial_agent/infrastructure/agents.py#L73) `run_task` | Recorre el agente con `stream(stream_mode="updates")`. Por cada actualización `model` mide el paso (`_meter`) y revisa el circuit breaker antes de ejecutar las herramientas. Cuando llegan todas las respuestas de `tools` del paso, lo cierra, aplica las reglas del dominio y emite el log `agent_step`. Si una regla corta, cierra el stream y el siguiente paso no se ejecuta. |
| 16 | [agents.py](../src/financial_agent/infrastructure/agents.py#L134) `_meter` | Calcula `step_number`, `tool_called`, `input_tokens`, `output_tokens` y `tokens_used` (de `usage_metadata`), los acumulados de cada tipo, `step_cost`, `cumulative_tokens`, `cumulative_cost`, presupuesto restante y `elapsed_time`. |
| 17 | [policies.py](../src/financial_agent/domain/policies.py#L127) `check_next_step` | Revisa, en este orden, `max_steps`, tokens totales, tokens de input, tokens de output, USD y timeout, siempre sobre una **estimación del siguiente paso** (el último paso + `max_output_tokens`) para cortar antes de pasarse del límite. |
| 18 | [policies.py](../src/financial_agent/domain/policies.py#L167) `detect_loop` | Circuit breaker: si una llamada con la misma herramienta y los mismos argumentos se repite `loop_threshold` veces, corta antes de ejecutarla. |
| 19 | [policies.py](../src/financial_agent/domain/policies.py#L176) `check_tool_errors` | Circuit breaker: corta después de N errores consecutivos de herramientas. |
| 20 | [tools.py](../src/financial_agent/infrastructure/tools.py#L20) `search_financial_data` | Espera 2 s y consulta SQLite ([financial_data.py](../src/financial_agent/infrastructure/financial_data.py#L72) `find`). Normaliza alias en español y trimestres. El artifact incluye `requested_metric` y `requested_period`. |
| 21 | [tools.py](../src/financial_agent/infrastructure/tools.py#L33) `calculate_ratio` | Calcula `numerator / denominator` con 4 decimales. Si el denominador es 0, devuelve un error. |
| 22 | [finops_log.py](../src/financial_agent/infrastructure/finops_log.py#L24) `log_event` | Escribe una línea JSON por evento (`agent_step`, `analysis_finished`) con `severity`, lista para Cloud Logging. |

## Motivos de corte (`stop_reason`)

| Código | Dónde se decide | Cuándo |
|---|---|---|
| `session_budget` | `budget_gate` | A la sesión le quedan menos de 2 × `LLM_MAX_TOKENS` tokens. |
| `token_budget` | `check_next_step` | Lo acumulado más el siguiente paso estimado supera el presupuesto de tokens de la consulta. |
| `input_token_budget` | `check_next_step` | Los tokens de input acumulados más los estimados del siguiente paso (input del último paso + `LLM_MAX_TOKENS`) superan `MAX_INPUT_TOKENS_BUDGET`. |
| `output_token_budget` | `check_next_step` | Los tokens de output acumulados más `LLM_MAX_TOKENS` superan `MAX_OUTPUT_TOKENS_BUDGET`. |
| `cost_budget` | `check_next_step` | Lo mismo, pero en USD contra `max_budget_usd`. |
| `step_limit` | `check_next_step` / `recursion_limit` | Se ejecutaron `MAX_STEPS` pasos. |
| `timeout` | `check_next_step` / `_execute` | El tiempo transcurrido más la latencia promedio de un paso supera `GLOBAL_TIMEOUT_SECONDS`. |
| `loop_detected` | `detect_loop` | Se repitió una llamada idéntica. |
| `tool_errors` | `check_tool_errors` | Hubo N errores consecutivos de herramientas. |
| `agent_error` | `run_task` / `_execute` | Falló el proveedor del LLM (429, timeout de red, etc.). |
