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
import asyncio
import json
import logging
import threading
import time

import chainlit as cl
from chainlit.config import config as _cl_config
from chainlit.context import init_ws_context as _init_ws_context
from chainlit.message import Message as _ClMessage
from chainlit.server import app as _cl_app
from chainlit.server import sio as _sio
from chainlit.session import WebsocketSession as _WebsocketSession
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request as _Request
from starlette.responses import JSONResponse as _JSONResponse
from starlette.responses import StreamingResponse as _StreamingResponse

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
    "histórico e estratégia da empresa, e fatos relevantes e "
    "desenvolvimentos institucionais recentes."
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


_WIDGET_MENSAGEM_MAX = 2000
_WIDGET_SESSAO_TTL_S = 2 * 60 * 60
_widget_sessoes: dict[str, tuple[CbragFlow, float]] = {}
_widget_lock = threading.Lock()


def _widget_limpar_sessoes_expiradas(agora: float) -> None:
    expiradas = [
        sid for sid, (_, ultimo_uso) in _widget_sessoes.items()
        if agora - ultimo_uso > _WIDGET_SESSAO_TTL_S
    ]
    for sid in expiradas:
        flow, _ = _widget_sessoes.pop(sid)
        flow.finalize_session_traces()


_WIDGET_HEARTBEAT_S = 10.0


async def _widget_chat(request: _Request):
    """Endpoint enxuto pro widget de chat da home (`site/index.html`) — sem
    Chainlit UI em volta, reusa `CbragFlow.handle_turn` direto, a mesma API
    conversacional oficial do CrewAI que `responder()` acima usa. Sessão
    identificada pelo `session_id` gerado no navegador (não é a sessão
    WebSocket do Chainlit), guardada em memória com TTL — sem endpoint de
    encerramento explícito (widget não avisa quando a aba fecha), a limpeza
    é preguiçosa: roda a cada chamada.

    Resposta é SSE (`text/event-stream`), não JSON puro: um turno real leva
    25-40s (RAG + LLM) e nesse tempo o POST fica mudo — sem o heartbeat do
    WebSocket que o Chainlit normal usa (ver `responder()`/`cl.make_async`
    acima), rede móvel/proxy intermediário mata a conexão por inatividade
    achando que caiu, e o navegador nunca recebe a resposta que o servidor
    processou até o fim. Aqui manda um comentário SSE (`: ping`) a cada
    `_WIDGET_HEARTBEAT_S` enquanto `flow.handle_turn` roda em thread, e só a
    resposta final vem como `data: {...}`."""
    corpo = await request.json()
    mensagem = str(corpo.get("mensagem") or "").strip()
    session_id = str(corpo.get("session_id") or "").strip()
    if not mensagem or not session_id:
        return _JSONResponse({"erro": "mensagem e session_id são obrigatórios"}, status_code=400)
    if len(mensagem) > _WIDGET_MENSAGEM_MAX:
        return _JSONResponse({"erro": "mensagem longa demais"}, status_code=400)

    agora = time.monotonic()
    with _widget_lock:
        _widget_limpar_sessoes_expiradas(agora)
        flow, _ = _widget_sessoes.get(session_id, (None, 0.0))
        if flow is None:
            flow = CbragFlow()
        _widget_sessoes[session_id] = (flow, agora)

    async def eventos():
        local_tracing.sessao_atual.set(session_id)
        inicio = time.monotonic()
        tarefa = asyncio.ensure_future(
            run_in_threadpool(flow.handle_turn, mensagem, session_id=session_id)
        )
        resposta = None
        while resposta is None:
            try:
                resposta = await asyncio.wait_for(
                    asyncio.shield(tarefa), timeout=_WIDGET_HEARTBEAT_S
                )
            except TimeoutError:
                yield b": ping\n\n"
            except Exception:
                logger.exception("Erro no widget de chat (session_id=%s)", session_id)
                resposta = (
                    "Não consegui responder agora — deu um erro inesperado no "
                    "meu lado. Tenta de novo, ou reformula a pergunta."
                )
        local_tracing.gravar(
            {
                "tipo": "turno",
                "pergunta": mensagem,
                "resposta": resposta,
                "duracao_s": round(time.monotonic() - inicio, 1),
                "interrompido": False,
            }
        )
        payload = json.dumps({"resposta": resposta}, ensure_ascii=False)
        yield f"data: {payload}\n\n".encode()

    return _StreamingResponse(
        eventos(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_cl_app.add_api_route(
    f"{_cl_config.run.root_path}/api/chat", _widget_chat, methods=["POST"]
)


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
