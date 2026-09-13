"""Log local de execução do chat, em JSONL, 100% no disco do servidor —
sem trace pra nuvem do CrewAI (desligado por `CREWAI_TRACING_ENABLED=false`
no compose; a conversa de terceiros não sai do homelab). Mecanismo oficial
pra isso é o event bus do CrewAI (`crewai.events.BaseEventListener`,
`docs.crewai.com/en/concepts/event-listener`).

Grava em `data/logs/eventos.jsonl` (rotação por tamanho, ver `_handler`):
- `turno` (gravado por `chainlit_app.responder`): session_id, pergunta,
  resposta, duração, se foi interrompido — é o registro pra analisar uso.
- `tool`/`tool_erro`: nome, argumentos, resultado truncado (o texto completo
  está na base; aqui é só pra ver o que o agent fez).
- `resposta`/`resposta_erro`: saída final do agent por kickoff.

`session_id` entra em todo evento via ContextVar setada no `responder`
(propaga pra thread do `cl.make_async`; tool chamada em thread paralela
pelo executor pode vir sem — limitação conhecida).

Importar este módulo já registra o listener (via `ativar()`, chamado no
import de `main.py`) — precisa manter a instância viva em memória, senão o
event bus não dispara os handlers.
"""
import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

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
TAMANHO_MAX_BYTES = 20 * 1024 * 1024
ARQUIVOS_ROTACAO = 5
TRUNCAR_RESULTADO = 500

sessao_atual: ContextVar[str | None] = ContextVar("sessao_atual", default=None)

_logger: logging.Logger | None = None


def _handler() -> logging.Logger:
    global _logger
    if _logger is None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            LOG_PATH, maxBytes=TAMANHO_MAX_BYTES, backupCount=ARQUIVOS_ROTACAO, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger("casas_bahia_rag.eventos")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        _logger = logger
    return _logger


def _truncar(texto: str, limite: int = TRUNCAR_RESULTADO) -> str:
    return texto if len(texto) <= limite else texto[:limite] + f"… [+{len(texto) - limite} chars]"


def gravar(registro: dict) -> None:
    registro = {
        "gravado_em": datetime.now(timezone.utc).isoformat(),
        "session_id": sessao_atual.get(),
        **registro,
    }
    _handler().info(json.dumps(registro, ensure_ascii=False, default=str))


class LocalEventListener(BaseEventListener):
    def setup_listeners(self, crewai_event_bus) -> None:
        @crewai_event_bus.on(ToolUsageFinishedEvent)
        def _tool_ok(source, event: ToolUsageFinishedEvent) -> None:
            gravar(
                {
                    "tipo": "tool",
                    "tool": event.tool_name,
                    "argumentos": event.tool_args,
                    "resultado": _truncar(str(event.output)),
                }
            )

        @crewai_event_bus.on(ToolUsageErrorEvent)
        def _tool_erro(source, event: ToolUsageErrorEvent) -> None:
            gravar(
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
            gravar({"tipo": "resposta", "resposta": getattr(saida, "raw", str(saida))})

        @crewai_event_bus.on(LiteAgentExecutionErrorEvent)
        def _resposta_erro(source, event: LiteAgentExecutionErrorEvent) -> None:
            gravar({"tipo": "resposta_erro", "erro": str(event.error)})


_listener: LocalEventListener | None = None


def ativar() -> None:
    """Idempotente: chamar mais de uma vez não duplica o listener (guarda
    global, mesmo padrão dos singletons de `qdrant_store.py`)."""
    global _listener
    if _listener is None:
        _listener = LocalEventListener()
