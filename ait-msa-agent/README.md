# ait-msa-agent: agente de análisis financiero con control FinOps

Este agente de IA asiste a los analistas financieros del banco con indicadores, ratios y tendencias de varios periodos. Controla el costo en cada paso: limita los pasos, los tokens, los dólares y el tiempo, detecta bucles y lleva un presupuesto por sesión.

**Stack:**
- LangGraph para el orquestador.
- LangChain `create_agent` para el sub-agente.
- `langchain-groq` con el modelo `qwen/qwen3.8-27b`.
- FastAPI.
- SQLite para los checkpoints, los datos mock y el consumo por sesión.
- LangSmith para la observabilidad (opcional).

## Arquitectura hexagonal

```
src/financial_agent/
├── domain/                 Reglas puras, sin frameworks
│   ├── models.py           AnalysisRequest (validación), Pricing, AgentCard + allowlist, errores
│   └── policies.py         Reglas FinOps: límites, presupuesto, circuit breaker, decisión completed/partial
├── application/
│   ├── ports.py            Puertos (Callable): RunAnalysis, ReadAnalysis, GetSessionUsage, ChargeSession
│   └── use_cases.py        analyze, get_analysis, get_session_budget
└── infrastructure/         Adaptadores
    ├── api.py              FastAPI + composition root (create_app --factory)
    ├── orchestrator.py     Grafo LangGraph + timeout global + corte forzado a respuesta parcial
    ├── agents.py           Sub-agente create_agent + allowlist + medición FinOps por paso
    ├── tools.py            search_financial_data (SQLite, 2 s de delay) y calculate_ratio
    ├── financial_data.py   Datos financieros mock en SQLite (2021-2025 y trimestres 2025)
    ├── session_ledger.py   Consumo de tokens y USD por sesión en SQLite
    ├── finops_log.py       Logs JSON estructurados (Cloud Logging)
    ├── config.py           Settings (pydantic-settings) + creación del LLM
    └── studio.py           Entrada para LangGraph Studio
```

Las dependencias van `infrastructure → application → domain`. **El LLM nunca decide si se sigue gastando:** cada corte lo decide una función pura de `domain/policies.py`.

```
validate_input → budget_gate ─┬─ sin presupuesto de sesión ─────────────────────────────→ respond → FIN
                              └─ financial_analyst_agent → policy_gate → respond → FIN
                                   └── bucle interno: LLM → herramientas → reglas FinOps → ...
```

El recorrido completo, archivo por archivo, está en [docs/FLUJO.md](docs/FLUJO.md).

## Límites y presupuesto

| Límite | Valor | Variable |
|---|---|---|
| Pasos del agente (1 paso = 1 llamada al LLM + sus herramientas) | 8 | `MAX_STEPS` |
| Tokens por consulta | 10.000 | `MAX_TOKENS_BUDGET` |
| Tokens de input por consulta (prompt + contexto + resultados de herramientas) | 8.000 | `MAX_INPUT_TOKENS_BUDGET` |
| Tokens de output por consulta (lo que genera el LLM) | 2.000 | `MAX_OUTPUT_TOKENS_BUDGET` |
| **Tokens por sesión** (propuesta) | **30.000** | `SESSION_TOKEN_BUDGET` |
| USD por consulta | `max_budget_usd` del request (máx. 5) | — |
| Timeout global | 45 s | `GLOBAL_TIMEOUT_SECONDS` |
| Circuit breaker de bucles | 1 llamada idéntica repetida | `LOOP_THRESHOLD=2` |
| Circuit breaker de errores | 3 errores seguidos de herramientas | `MAX_CONSECUTIVE_TOOL_ERRORS` |

**Por qué 30.000 tokens por sesión.** Una consulta compleja usa entre 3.000 y 8.000 tokens: unos 700 tokens de prompt y herramientas por llamada, un contexto que crece en cada paso y de 3 a 6 pasos. Con 30.000 tokens, el analista puede hacer unas 3 consultas complejas del tope máximo (10.000) o unas 6 a 8 consultas normales por sesión, y el costo queda acotado a unos pocos centavos. Cada consulta recibe `min(10.000, lo que le queda a la sesión)`. Si a la sesión no le alcanza para una llamada completa al LLM (2 × `LLM_MAX_TOKENS`), la consulta ni siquiera se ejecuta.

**Topes de input y output.** Los tres topes de tokens se revisan por separado y el que se agote primero corta la consulta (`token_budget`, `input_token_budget` u `output_token_budget`). El de input controla el crecimiento del contexto, que se reenvía completo en cada paso. El de output controla lo que genera el modelo, que es lo más caro por token y lo que limita el plan gratuito de Groq.

**Corte antes de pasarse del límite.** Después de cada paso, el dominio estima el costo del siguiente: el del último paso más `LLM_MAX_TOKENS`, porque el contexto crece. Si lo acumulado más esa estimación supera el presupuesto, el agente se detiene **antes** de llamar al LLM y devuelve una respuesta parcial con los datos ya obtenidos y una advertencia.

**Costos.** Se calculan con `usage_metadata` (los tokens reales que reporta Groq) y con los precios de `LLM_PRICE_INPUT_PER_MILLION` y `LLM_PRICE_OUTPUT_PER_MILLION`. Por defecto valen 0,29 y 0,59 USD por millón de tokens. Es un supuesto: ajústalos a la tarifa vigente del modelo.

## API

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/v1/analysis/query` | Ejecuta un análisis. |
| GET | `/api/v1/analysis/{analysis_id}` | Devuelve un análisis guardado (desde el checkpoint). |
| GET | `/api/v1/sessions/{session_id}/budget` | Devuelve el presupuesto restante de la sesión. |
| GET | `/api/v1/agents` | Devuelve los Agent Cards con su allowlist. |
| GET | `/api/v1/config` | Devuelve los límites vigentes y el catálogo de datos mock. |
| GET | `/health` | Health check. |

Respuesta de `POST /api/v1/analysis/query` (resumida):

```json
{
  "analysis_id": "…", "status": "completed | partial", "stop_reason": null,
  "answer": "La razón corriente 2024 es 1.52…", "warning": null,
  "steps": [{"step_number": 1, "tool_called": ["search_financial_data", "search_financial_data"],
             "tokens_used": 812, "input_tokens": 760, "output_tokens": 52,
             "cumulative_tokens": 812, "cumulative_input_tokens": 760, "cumulative_output_tokens": 52, "step_cost": 0.00026,
             "cumulative_cost": 0.00026, "remaining_tokens": 9188, "remaining_budget_usd": 0.49974,
             "elapsed_time": 2.61, "llm_latency_ms": 590, "tool_latency_ms": 2010}],
  "usage": {"agent_steps": 3, "input_tokens": 2200, "output_tokens": 450, "tokens_used": 2650, "cost_usd": 0.0008, "elapsed_time": 4.9},
  "budget": {"query":   {"token_budget": 10000, "tokens_remaining": 7350, "input_tokens_remaining": 5800,
                         "output_tokens_remaining": 1800, "cost_remaining_usd": 0.4992},
             "session": {"token_limit": 30000, "tokens_remaining": 27350, "queries": 1}},
  "data": {"searches": [...], "ratios": [...]}, "findings": [...], "audit_trail": [...]
}
```

Una respuesta parcial devuelve HTTP 200 con `status: "partial"`, `stop_reason` y una advertencia en `warning`. Una entrada inválida devuelve HTTP 422.

## Ejecutar en local

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt && pip install -e .
cp .env.example .env          # y completa GROQ_API_KEY

uvicorn financial_agent.infrastructure.api:create_app --factory --reload    # http://localhost:8000/docs
```

El frontend de pruebas está en `../frontend`: `python3 ../frontend/server.py`, y luego abre http://localhost:5500.

### LangGraph Studio y LangSmith

```bash
langgraph dev        # abre Studio con el grafo "financial_analysis"
```

- Studio ejecuta el grafo directamente: el timeout se aplica dentro del agente, pero el presupuesto de sesión no, porque `session_tokens_remaining` es un campo opcional de la entrada.
- Con `LANGSMITH_TRACING=false` (el valor por defecto) no sale ninguna traza de la máquina.
- Para tener observabilidad en LangSmith, configura `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY` y `LANGSMITH_PROJECT`. Cada ejecución lleva en la metadata `analysis_id`, `analyst_id` y `session_id`.

## Tests

```bash
pytest -q        # 43 tests, ~1 s, con FakeLLM (no gastan tokens)
```

| Nivel | Qué cubren |
|---|---|
| Dominio | Límites, estimación antes de gastar, circuit breaker y decisión. |
| Agente | Métricas por paso, allowlist (`rogue_tool`), bucle, `max_steps` y error del proveedor. |
| Grafo | Ejecución directa, como en Studio. |
| End-to-end por HTTP | `completed`, parcial por USD, por tokens, por timeout y por bucle, agotamiento de la sesión en varias consultas, 422, 404 y persistencia. |

## Logs FinOps

Cada paso emite una línea JSON, y cada consulta emite un resumen:

```json
{"severity":"INFO","event":"agent_step","analysis_id":"…","analyst_id":"A01","session_id":"S001","step_number":2,"tool_called":["calculate_ratio"],"tokens_used":930,"cumulative_tokens":1742,"cumulative_cost":0.00056,"remaining_tokens":8258,"remaining_budget_usd":0.49944,"elapsed_time":3.4,"stop_reason":null}
{"severity":"WARNING","event":"analysis_finished","status":"partial","stop_reason":"cost_budget","tokens_used":1120,"cost_usd":0.00036,"tokens_remaining":8880,"cost_remaining_usd":0.00004,…}
```

En Cloud Run estas líneas llegan a Cloud Logging como logs estructurados. Desde ahí puedes filtrar con `jsonPayload.event="analysis_finished"` o crear métricas basadas en logs, por ejemplo el costo por `session_id`.

## Despliegue en Cloud Run

```bash
# Una sola vez: habilitar las APIs y guardar la API key en Secret Manager (ver comentarios en deploy.sh)
PROJECT_ID=mi-proyecto ./deploy.sh
```

`deploy.sh` hace tres cosas:
1. Construye la imagen con Cloud Build, así que no necesitas Docker local.
2. La publica en Artifact Registry.
3. Despliega el servicio con `GROQ_API_KEY` desde Secret Manager y un `--timeout` de 120 s, mayor que el timeout global del agente.

También puedes probar la imagen en local:

```bash
docker build -t ait-msa-agent .
docker run -p 8080:8080 --env-file .env ait-msa-agent
```

**Limitaciones conocidas:**
- **SQLite en Cloud Run es efímero y está separado por instancia** (`/tmp`). Es suficiente para la prueba. En producción, los checkpoints pasan a `PostgresSaver` (Cloud SQL) y el ledger de sesiones a Postgres o Redis, cambiando solo `api.py` y `session_ledger.py`.
- **El timeout es cooperativo:** se revisa entre pasos. Una llamada al LLM que ya empezó no se interrumpe, pero queda acotada por `LLM_REQUEST_TIMEOUT` (15 s) y se contabiliza en el costo.
- **Consultas concurrentes en la misma sesión** pueden pasar el `budget_gate` a la vez. Para evitarlo, habría que reservar el presupuesto de forma atómica antes de ejecutar.
