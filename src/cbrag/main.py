#!/usr/bin/env python
"""Chatbot RAG conversacional sobre a Grupo Casas Bahia (institucional +
RI + financeiro/CVM). Base já embedada pelo IngestFlow (ver
knowledge_config.py); geração via OpenRouter (preset com fallback entre
modelos gratuitos configurado no próprio painel do OpenRouter).

Retrieval é feito via **tool-calling** do próprio Agent (tools.py), não
mais por roteamento manual por palavra-chave — confirmado na doc oficial
(docs.crewai.com/en/concepts/agents) que `Agent.kickoff()` standalone
suporta tools e "decides automatically when to call" cada uma, sem precisar
de Crew/Task. O LLM decide sozinho se chama `consultar_resultado_financeiro`
(DRE estruturada da CVM) ou `buscar_conhecimento` (RAG) — ou os dois —
baseado na pergunta, em vez de nós prevermos isso com uma lista fixa de
termo (roteamento por palavra-chave que existiu até a fase 3.2).
"""
import logging

from crewai import Agent, Flow
from crewai.flow.conversational import ConversationConfig, ConversationState
from crewai.flow.flow import listen

from cbrag import local_tracing
from cbrag.knowledge_config import (  # noqa: F401
    OLLAMA_LLM,
    OPENROUTER_LLM,
)
from cbrag.prompts import BACKSTORY as _BACKSTORY
from cbrag.prompts import GOAL as _GOAL
from cbrag.prompts import ROLE as _ROLE

local_tracing.ativar()
from cbrag.tools.rag_tools import (
    buscar_conhecimento,
    consultar_composicao_conselho,
    consultar_cobertura_da_base,
    consultar_documentos_recentes,
    consultar_indicadores_operacionais,
    consultar_resultado_financeiro,
    consultar_serie_historica_resultado,
)

# Workaround de bug do crewai (não nosso app): a lib sempre marca as
# mensagens com 'cache_breakpoint' (feature de prompt caching), mas só o
# provider nativo Anthropic sabe remover essa chave antes de mandar pra
# API — o litellm (usado pro OpenRouter abaixo) repassa a chave crua, que
# a API upstream rejeita ("property cache_breakpoint is unsupported").
# Não usamos Anthropic nesse projeto, então desativa a marcação global em
# vez de reimplementar o strip que falta upstream.
import crewai.llms.cache as _crewai_cache  # noqa: E402

_crewai_cache.mark_cache_breakpoint = lambda message: dict(message)

_TOOLS = [
    consultar_resultado_financeiro,
    consultar_serie_historica_resultado,
    consultar_composicao_conselho,
    consultar_documentos_recentes,
    consultar_cobertura_da_base,
    consultar_indicadores_operacionais,
    buscar_conhecimento,
]

_agent: Agent | None = None


# session_ids cujo turno o usuário interrompeu na UI (chainlit_app.parar).
# A thread do handle_turn não é cancelável e termina sozinha; sem isso a
# resposta que ninguém viu entraria no histórico e o próximo turno
# "lembraria" dela.
TURNOS_CANCELADOS: set[str] = set()


def rag_agent() -> Agent:
    """Constrói o Agent de geração uma única vez por processo, com as
    tools de retrieval (ver tools.py) — o LLM decide quando chamar cada
    uma."""
    global _agent
    if _agent is not None:
        return _agent

    _agent = Agent(
        role=_ROLE,
        goal=_GOAL,
        backstory=_BACKSTORY,
        tools=_TOOLS,
        llm=OPENROUTER_LLM,
        # Sem isso, modelo grátis do preset pode ficar reformulando a mesma
        # busca indefinidamente sem nunca decidir responder (visto na
        # prática: ~20 chamadas de tool em 4min pra uma pergunta só, sem
        # resultado) — cap duro no framework, não depende do modelo parar
        # sozinho. Default do CrewAI é 25. `max_execution_time` NÃO vale
        # neste caminho: `_prepare_kickoff` só repassa max_iter pro
        # AgentExecutor; o timeout só existe em `execute_task()` (Crew).
        max_iter=8,
        # Data recalculada a cada kickoff (`Prompts._build_date_block`), não
        # no import — container que fica semanas no ar continuaria achando
        # que "hoje" é o dia do boot.
        inject_date=True,
        date_format="%Y-%m-%d",
    )
    return _agent


def _kickoff(mensagens) -> str:
    """Sem retry/fallback manual aqui — o preset do OpenRouter
    (knowledge_config.OPENROUTER_LLM) já cobre fallback entre modelos
    gratuitos do próprio lado do provider. Gateway do OpenRouter devolve
    200 OK mesmo quando o upstream falha (erro vem no corpo, não no
    status HTTP, ver docs.crewai.com/en/concepts/llms) — captura ampla
    aqui é só pra não derrubar o chat, não é retry de verdade."""
    try:
        return rag_agent().kickoff(mensagens).raw
    except Exception:
        logging.getLogger(__name__).exception("Erro chamando o LLM via OpenRouter")
        return (
            "Não consegui responder agora — o provedor de LLM está "
            "indisponível no momento. Tente de novo em alguns instantes."
        )


@ConversationConfig(defer_trace_finalization=True)
class CbragFlow(Flow[ConversationState]):
    conversational = True

    def route_turn(self, context: dict) -> str:
        return "ANSWER"

    @listen("ANSWER")
    def answer(self) -> str:
        """Responde perguntas institucionais/financeiras — o Agent decide
        sozinho, via tool-calling, se/quando consultar a base de
        conhecimento ou a DRE estruturada. Histórico da conversa
        (conversation_messages) já vem pronto do ConversationalMixin, não
        precisa de contexto pré-injetado na mensagem."""
        reply = _kickoff(self.conversation_messages)
        if self.state.id in TURNOS_CANCELADOS:
            TURNOS_CANCELADOS.discard(self.state.id)
            return reply
        self.append_assistant_message(reply)
        return reply


def chat():
    """REPL local no terminal pra testar o chatbot."""
    flow = CbragFlow()
    flow.chat()


def kickoff():
    chat()


if __name__ == "__main__":
    kickoff()
