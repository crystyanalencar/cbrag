"""Interface web do chatbot RAG, via Chainlit. Camada de interface só —
reusa o `CbragFlow` de `main.py` (tools, LLM via OpenRouter,
histórico) sem duplicar lógica.

Usa `flow.handle_turn(mensagem, session_id=...)`, a API oficial do CrewAI
pra servir Flow conversacional via web (REST/WebSocket/UI customizada,
docs.crewai.com/en/guides/flows/conversational-flows) — mantém o
histórico em `state.messages` por `session_id`, em vez de `flow.chat()`
(REPL de terminal). Não confundir com a abordagem anterior descartada
(chamar `_kickoff` direto, reimplementando o histórico na mão) —
`handle_turn` já cobre isso, incluindo `route_turn`.

`ConversationConfig(defer_trace_finalization=True)`, já configurado em
`CbragFlow`, mantém 1 lote de trace aberto por sessão em vez de 1
por turno — por isso finaliza no fim da sessão do Chainlit
(`on_chat_end`), não a cada mensagem.

Rodar: `uv run chainlit run src/cbrag/chainlit_app.py`
"""
import logging
import time

import chainlit as cl
from chainlit.config import config as _cl_config
from chainlit.context import init_ws_context as _init_ws_context
from chainlit.message import Message as _ClMessage
from chainlit.server import sio as _sio
from chainlit.session import WebsocketSession as _WebsocketSession

from cbrag import local_tracing
from cbrag.main import TURNOS_CANCELADOS, CbragFlow

_FLOW = "flow"
_SESSION_ID = "session_id"
_LOADER = "loader_atual"
_AVISO = "aviso_atual"

logger = logging.getLogger(__name__)

_DESCRICAO = (
    "Assistente sobre a **Grupo Casas Bahia** — institucional, governança "
    "e financeiro — construído sobre dados públicos oficiais (CVM e site "
    "institucional), não sobre memória do modelo.\n\n"
    "**Dá pra perguntar**: resultado de um trimestre (lucro/prejuízo, "
    "receita, EBITDA), série histórica, membros do Conselho e Diretoria, "
    "histórico e estratégia da empresa, e o processo de pedido de **recuperação "
    "judicial** em curso desde ago/2026."
)


@cl.set_chat_profiles
async def perfis(_usuario):
    return [
        cl.ChatProfile(
            name="Casas Bahia RAG",
            markdown_description=_DESCRICAO,
            icon="/public/avatars/cbrag.svg",
            default=True,
        )
    ]


@cl.on_chat_start
async def iniciar():
    cl.user_session.set(_FLOW, CbragFlow())
    cl.user_session.set(_SESSION_ID, cl.user_session.get("id"))


@cl.on_message
async def responder(mensagem: cl.Message):
    flow: CbragFlow | None = cl.user_session.get(_FLOW)
    if flow is None:
        await iniciar()
        flow = cl.user_session.get(_FLOW)
    session_id = cl.user_session.get(_SESSION_ID)

    loader = cl.CustomElement(name="BrandLoader")
    aviso = cl.Message(content="", elements=[loader])
    await aviso.send()
    cl.user_session.set(_LOADER, loader)
    cl.user_session.set(_AVISO, aviso)
    local_tracing.sessao_atual.set(session_id)
    inicio = time.monotonic()

    try:
        # `handle_turn` é síncrono e bloqueante (chamada de LLM) — rodar
        # direto aqui trava o event loop do Chainlit inteiro, o WebSocket
        # para de responder heartbeat e o navegador acha que perdeu conexão
        # ("Could not reach the server") mesmo o processo estando vivo,
        # ainda mais visível com modelo local (bem mais lento que Gemini).
        # `cl.make_async` roda em thread separada, sem travar o loop.
        resposta = await cl.make_async(flow.handle_turn)(
            mensagem.content, session_id=session_id
        )
    except Exception:
        logger.exception("Erro ao processar turno (session_id=%s)", session_id)
        resposta = (
            "Não consegui responder agora — deu um erro inesperado no meu "
            "lado. Tenta de novo, ou reformula a pergunta."
        )

    await loader.remove()
    aviso.content = resposta
    aviso.elements = []
    await aviso.update()
    cl.user_session.set(_LOADER, None)
    cl.user_session.set(_AVISO, None)
    local_tracing.gravar(
        {
            "tipo": "turno",
            "pergunta": mensagem.content,
            "resposta": resposta,
            "duracao_s": round(time.monotonic() - inicio, 1),
            "interrompido": False,
        }
    )


@cl.on_stop
async def parar():
    """`socket.stop()` cancela a task via `asyncio.Task.cancel()` —
    `CancelledError` não é `Exception` (é `BaseException` desde o Python
    3.8), então o `except Exception` de `responder()` não captura e o
    código de limpeza do loader nunca roda, deixando os pontinhos girando
    pra sempre. Limpa aqui, fora da task cancelada."""
    loader: cl.CustomElement | None = cl.user_session.get(_LOADER)
    aviso: cl.Message | None = cl.user_session.get(_AVISO)
    if loader is not None:
        await loader.remove()
    if aviso is not None:
        aviso.content = "Consulta interrompida."
        aviso.elements = []
        await aviso.update()
    cl.user_session.set(_LOADER, None)
    cl.user_session.set(_AVISO, None)
    if aviso is not None:
        # A thread do handle_turn segue até o fim (não é cancelável); o
        # Flow vê o id aqui e não grava a resposta no histórico.
        TURNOS_CANCELADOS.add(cl.user_session.get(_SESSION_ID))
        local_tracing.gravar({"tipo": "turno", "interrompido": True})


@cl.on_chat_end
async def encerrar():
    # Reconexão/troca de rede do cliente dispara on_chat_end pra sessão em
    # que on_chat_start nunca rodou (visto em prod, celular saindo do wifi)
    # — sem Flow não há trace pra fechar; exceção aqui vira toast de erro
    # pro usuário.
    flow: CbragFlow | None = cl.user_session.get(_FLOW)
    if flow is not None:
        flow.finalize_session_traces()


@_sio.on("stop")
async def _stop_pt_br(sid):
    """Sobrescreve o handler nativo do Chainlit (`chainlit/socket.py`) só
    pra trocar a mensagem hardcoded em inglês ("Task manually stopped.")
    por pt-BR — não existe chave de tradução pra isso, é string literal no
    servidor. `python-socketio` reatribui o handler por evento (dict), o
    último `@sio.on("stop")` registrado (este, importado depois do
    `chainlit.socket`) vence. Réplica fiel do handler original; se o
    Chainlit mudar essa lógica numa versão futura, esta cópia fica
    desatualizada silenciosamente — conferir aqui se o stop parar de
    funcionar direito após upgrade do `chainlit`."""
    if session := _WebsocketSession.get(sid):
        _init_ws_context(session)
        await _ClMessage(content="Consulta interrompida manualmente.").send()
        if session.current_task:
            session.current_task.cancel()
        if _cl_config.code.on_stop:
            await _cl_config.code.on_stop()
