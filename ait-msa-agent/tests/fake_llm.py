"""LLM falso con tool calling y usage_metadata, para probar el ciclo completo sin llamar a Groq.

Comportamiento por defecto (PLAN): paso 1 busca activo y pasivo corriente 2024 en paralelo,
paso 2 calcula la razón corriente, paso 3 responde. Opciones:
  - loop: repite siempre la misma búsqueda (activa el circuit breaker).
  - rogue_tool: primer intento con una herramienta fuera de la allowlist.
  - delay: espera antes de cada respuesta (timeout).
  - fail: lanza una excepción (error del proveedor).
  - input_tokens / output_tokens: consumo reportado por llamada (presupuesto).
"""

import time

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

PLAN = [
    [("search_financial_data", {"metric": "current_assets", "period": "2024"}),
     ("search_financial_data", {"metric": "current_liabilities", "period": "2024"})],
    [("calculate_ratio", {"numerator": 990, "denominator": 650})],
]
FINAL_ANSWER = "La razón corriente 2024 es 1.5231 (990 / 650 millones USD): liquidez holgada."


class FakeLLM(BaseChatModel):
    tools: list[str] = []
    plan: list = PLAN
    loop: bool = False
    rogue_tool: str | None = None
    delay: float = 0.0
    fail: bool = False
    input_tokens: int = 500
    output_tokens: int = 60

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, **kwargs):
        return self.model_copy(update={"tools": [t.name for t in tools]})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("Groq no disponible")
        message = self._reply(messages)
        message.usage_metadata = {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                                  "total_tokens": self.input_tokens + self.output_tokens}
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _reply(self, messages) -> AIMessage:
        turn = sum(isinstance(m, AIMessage) for m in messages)
        tried_rogue = any(isinstance(m, ToolMessage) and m.status == "error" for m in messages)
        if self.rogue_tool and not tried_rogue:
            return self._calls(turn, [(self.rogue_tool, {"metric": "x"})])
        if self.loop:
            return self._calls(turn, [("search_financial_data", {"metric": "revenue", "period": "2024"})])
        index = turn - (1 if self.rogue_tool else 0)
        if index < len(self.plan):
            return self._calls(turn, self.plan[index])
        return AIMessage(FINAL_ANSWER)

    @staticmethod
    def _calls(turn: int, calls: list) -> AIMessage:
        return AIMessage("", tool_calls=[{"name": name, "args": args, "id": f"c{turn}-{i}"}
                                         for i, (name, args) in enumerate(calls)])
