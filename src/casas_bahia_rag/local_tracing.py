"""Log local de execução do chat — tool calls e resposta final do agent,
em JSONL, sem depender do CrewAI AMP (traces de lá são "ephemeral trace
batch": link de nuvem com access_code, sem API/MCP pra reler depois, sem
retenção documentada — ver STATE.md). Mecanismo oficial pra isso é o
próprio event bus do CrewAI (`crewai.events.BaseEventListener`,
`docs.crewai.com/en/concepts/event-listener`), rodando 100% local.

Importar este módulo já registra o listener (via `ativar()`, chamado no
import de `main.py`) — precisa manter a instância viva em memória, senão o
event bus não dispara os handlers.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from crewai.events import BaseEventListener
from crewai.events.types.agent_events import (
    LiteAgentExecutionCompletedEvent,
    LiteAgentExecutionErrorEvent,
)
from crewai.events.types.tool_usage_events import (
    ToolUsageErrorEvent,
    ToolUsageFinishedEvent,
)

from casas_bahia_rag.knowledge_config import ROOT

LOG_DIR = ROOT / "data" / "logs"
LOG_PATH = LOG_DIR / "eventos.jsonl"


def _gravar(registro: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    registro["gravado_em"] = datetime.now(timezone.utc).isoformat()
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


class LocalEventListener(BaseEventListener):
    """Grava em `data/logs/eventos.jsonl`: cada chamada de tool (consulta +
    resultado ou erro) e a resposta final de cada turno do agent."""

    def setup_listeners(self, crewai_event_bus) -> None:
        @crewai_event_bus.on(ToolUsageFinishedEvent)
        def _tool_ok(source, event: ToolUsageFinishedEvent) -> None:
            _gravar(
                {
                    "tipo": "tool",
                    "tool": event.tool_name,
                    "argumentos": event.tool_args,
                    "resultado": str(event.output),
                }
            )

        @crewai_event_bus.on(ToolUsageErrorEvent)
        def _tool_erro(source, event: ToolUsageErrorEvent) -> None:
            _gravar(
                {
                    "tipo": "tool_erro",
                    "tool": event.tool_name,
                    "argumentos": event.tool_args,
                    "erro": str(event.error),
                }
            )

        @crewai_event_bus.on(LiteAgentExecutionCompletedEvent)
        def _resposta(source, event: LiteAgentExecutionCompletedEvent) -> None:
            saida = event.output
            _gravar(
                {
                    "tipo": "resposta",
                    "resposta": getattr(saida, "raw", str(saida)),
                }
            )

        @crewai_event_bus.on(LiteAgentExecutionErrorEvent)
        def _resposta_erro(source, event: LiteAgentExecutionErrorEvent) -> None:
            _gravar({"tipo": "resposta_erro", "erro": str(event.error)})


_listener: LocalEventListener | None = None


def ativar() -> None:
    """Idempotente: chamar mais de uma vez não duplica o listener (guarda
    global, mesmo padrão dos singletons de `qdrant_store.py`)."""
    global _listener
    if _listener is None:
        _listener = LocalEventListener()
