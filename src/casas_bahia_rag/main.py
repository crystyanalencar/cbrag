#!/usr/bin/env python
"""Chatbot RAG conversacional sobre a Grupo Casas Bahia (institucional +
RI + financeiro/CVM). Base já embedada pelo IngestFlow (ver
knowledge_config.py); geração via Gemini.

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
import re
import time

import litellm
from google.genai.errors import APIError as GeminiAPIError

from crewai import Agent, Flow
from crewai.flow.conversational import ConversationConfig, ConversationState
from crewai.flow.flow import listen

from casas_bahia_rag.knowledge_config import (  # noqa: F401
    GEMINI_LLM,
    GROQ_LLM,
    OLLAMA_LLM,
)
from casas_bahia_rag.tools.rag_tools import (
    buscar_conhecimento,
    consultar_composicao_conselho,
    consultar_resultado_financeiro,
    consultar_serie_historica_resultado,
)

# Workaround de bug do crewai (não nosso app): a lib sempre marca as
# mensagens com 'cache_breakpoint' (feature de prompt caching), mas só o
# provider nativo Anthropic sabe remover essa chave antes de mandar pra
# API — o fallback litellm (usado pro Groq abaixo) repassa a chave crua,
# que a API do Groq rejeita ("property cache_breakpoint is unsupported").
# Não usamos Anthropic nesse projeto, então desativa a marcação global em
# vez de reimplementar o strip que falta upstream.
import crewai.llms.cache as _crewai_cache  # noqa: E402

_crewai_cache.mark_cache_breakpoint = lambda message: dict(message)

_TOOLS = [
    consultar_resultado_financeiro,
    consultar_serie_historica_resultado,
    consultar_composicao_conselho,
    buscar_conhecimento,
]

_agent: Agent | None = None
_fallback_agent: Agent | None = None


_ROLE = "Especialista em Relações com Investidores e Institucional da Grupo Casas Bahia"
_GOAL = (
    "Responder perguntas sobre a Grupo Casas Bahia (institucional, "
    "governança, financeiro e RI) de forma direta, usando as tools "
    "disponíveis pra buscar informação antes de responder, citando "
    "fonte e data só quando o usuário pedir pra confirmar a origem."
)
_BACKSTORY = (
    "Você conhece a fundo os documentos institucionais e regulatórios "
    "da Grupo Casas Bahia. Sempre que a pergunta puder ser respondida "
    "com informação da empresa (institucional, governança, "
    "financeiro, RI, recuperação judicial), use as tools disponíveis "
    "antes de responder — nunca responda de memória. Pra número de "
    "resultado financeiro (lucro, prejuízo, receita, EBITDA), prefira "
    "sempre a tool de resultado financeiro estruturado; ela já vem "
    "com o período exato, sem ambiguidade. Trecho vindo da busca na "
    "base de conhecimento traz cabeçalho '[Fonte: ... | Data: ...]' "
    "— use-o internamente pra priorizar a informação mais recente "
    "quando houver dados conflitantes de datas diferentes, mas NÃO "
    "inclua esse cabeçalho na resposta por padrão, responda de forma "
    "direta. Só mencione fonte e data se o usuário pedir "
    "explicitamente pra confirmar a origem. Se as tools não trouxerem "
    "a resposta, diga isso claramente em vez de inventar. ATENÇÃO ao "
    "ler tabela financeira vinda de PDF (DRE, balanço): a extração "
    "não preserva alinhamento de coluna, então valores de períodos "
    "diferentes podem aparecer lado a lado na mesma linha — sempre "
    "confira o cabeçalho de coluna mais próximo antes de citar um "
    "número vindo de tabela, e deixe explícito qual período exato o "
    "valor cobre (trimestre isolado vs. acumulado do semestre/ano). "
    "Pra série histórica (tendência ao longo do tempo), sempre chame a "
    "tool sem `ano_inicio`/`ano_fim` (série completa) a menos que o "
    "usuário peça um recorte específico — nunca limite o intervalo por "
    "conta própria. Se por algum motivo restringir o período, priorize "
    "sempre os anos mais recentes, nunca deixe de fora o período mais "
    "atual. Se o usuário apontar que um dado esperado (ex. um ano) não "
    "apareceu na resposta, NUNCA invente uma explicação de sistema "
    "('estava configurado até X', 'limite da base') — isso é fabricação. "
    "Chame a tool de novo sem filtro de período e responda com o que "
    "vier; se ainda assim faltar, diga que não sabe o motivo."
)


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
        llm=GEMINI_LLM,
    )
    return _agent


def fallback_agent() -> Agent:
    """Mesmo agente, mesmas tools, LLM Groq — usado só quando o Gemini
    devolve 503 (sobrecarga) ou 429 (cota diária esgotada), ver
    `_kickoff_com_fallback` abaixo."""
    global _fallback_agent
    if _fallback_agent is not None:
        return _fallback_agent

    _fallback_agent = Agent(
        role=_ROLE,
        goal=_GOAL,
        backstory=_BACKSTORY,
        tools=_TOOLS,
        llm=GROQ_LLM,
    )
    return _fallback_agent


_ERROS_TEMPORARIOS = (GeminiAPIError, litellm.RateLimitError, litellm.ServiceUnavailableError)
_ESPERA_MAXIMA_SEGUNDOS = 20.0  # nunca trava o chat esperando cota diária


def _segundos_de_retry(erro: Exception) -> float | None:
    """Extrai o tempo de espera sugerido pelo provider (quando existe).
    Groq manda "Please try again in 14.06999s" na mensagem; Gemini manda
    um RetryInfo estruturado com campo `retryDelay` tipo "14s" dentro de
    `details`. Sem isso no erro, não dá pra saber se vale esperar."""
    texto = f"{erro} {getattr(erro, 'details', '')}"
    m = re.search(r"try again in ([\d.]+)\s*s", texto, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", texto)
    if m:
        return float(m.group(1))
    return None


def _kickoff_com_retry(agente: Agent, mensagens) -> str:
    """1 retry automático quando o provider sugere uma espera curta (rate
    limit por minuto/segundo, ex. TPM do Groq) — não adianta pra cota
    diária esgotada (TPD/RPD), o `retryDelay` nesse caso vem bem maior que
    `_ESPERA_MAXIMA_SEGUNDOS`, então nem tenta esperar."""
    try:
        return agente.kickoff(mensagens).raw
    except _ERROS_TEMPORARIOS as erro:
        espera = _segundos_de_retry(erro)
        if espera is None or espera > _ESPERA_MAXIMA_SEGUNDOS:
            raise
        time.sleep(espera)
        return agente.kickoff(mensagens).raw


def _kickoff_com_fallback(mensagens) -> str:
    """Tenta o Gemini primeiro (com 1 retry se o erro sugerir espera curta,
    ver `_kickoff_com_retry`); em 503 (sobrecarga) ou 429 (cota) — já
    reproduzidos de verdade, ver STATE.md — cai pro Groq (idem, com seu
    próprio retry) em vez de propagar o erro pro usuário."""
    try:
        return _kickoff_com_retry(rag_agent(), mensagens)
    except GeminiAPIError as erro:
        if erro.code not in (429, 503):
            raise
        try:
            return _kickoff_com_retry(fallback_agent(), mensagens)
        except _ERROS_TEMPORARIOS as erro_groq:
            # Já é o fallback — não tem pra onde cair depois disso. Erro
            # mais comum aqui: "Request too large" (uma chamada só já passa
            # do TPM do Groq por causa do histórico de conversa acumulado +
            # backstory + schema das tools) — retry não ajuda nunca nesse
            # caso, a chamada continua grande do mesmo jeito. Sem isso o
            # erro cru derrubava o chat inteiro.
            logging.getLogger(__name__).error(
                "Gemini e Groq indisponíveis: %s", erro_groq
            )
            return (
                "Não consegui responder agora — Gemini e Groq (fallback) "
                "estão indisponíveis no momento. Tente de novo em alguns "
                "minutos, ou pergunte algo mais curto (conversas longas "
                "aumentam a chance de estourar o limite do Groq)."
            )


@ConversationConfig(defer_trace_finalization=True)
class CasasBahiaRagFlow(Flow[ConversationState]):
    conversational = True

    def route_turn(self, context: dict) -> str:
        return "ANSWER"

    @listen("ANSWER")
    def answer(self) -> str:
        """Responde perguntas institucionais/financeiras — o Agent decide
        sozinho, via tool-calling, se/quando consultar a base de
        conhecimento ou a DRE estruturada. Histórico da conversa
        (conversation_messages) já vem pronto do ConversationalMixin, não
        precisa de contexto pré-injetado na mensagem. Cai pro Groq
        automaticamente se o Gemini estiver sobrecarregado/sem cota, ver
        `_kickoff_com_fallback`."""
        reply = _kickoff_com_fallback(self.conversation_messages)
        self.append_assistant_message(reply)
        return reply


def chat():
    """REPL local no terminal pra testar o chatbot."""
    flow = CasasBahiaRagFlow()
    flow.chat()


def kickoff():
    chat()


if __name__ == "__main__":
    kickoff()
