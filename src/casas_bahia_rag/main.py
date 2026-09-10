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
termo (`knowledge_config.grupo_da_pergunta`, mantida só como utilitário,
não é mais chamada daqui).
"""
from crewai import Agent, Flow
from crewai.flow.conversational import ConversationConfig, ConversationState
from crewai.flow.flow import listen

from casas_bahia_rag.knowledge_config import GEMINI_LLM, OLLAMA_LLM  # noqa: F401
from casas_bahia_rag.tools.rag_tools import (
    buscar_conhecimento,
    consultar_composicao_conselho,
    consultar_resultado_financeiro,
    consultar_serie_historica_resultado,
)

_agent: Agent | None = None


def rag_agent() -> Agent:
    """Constrói o Agent de geração uma única vez por processo, com as
    tools de retrieval (ver tools.py) — o LLM decide quando chamar cada
    uma."""
    global _agent
    if _agent is not None:
        return _agent

    _agent = Agent(
        role="Especialista em Relações com Investidores e Institucional da Grupo Casas Bahia",
        goal=(
            "Responder perguntas sobre a Grupo Casas Bahia (institucional, "
            "governança, financeiro e RI) de forma direta, usando as tools "
            "disponíveis pra buscar informação antes de responder, citando "
            "fonte e data só quando o usuário pedir pra confirmar a origem."
        ),
        backstory=(
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
            "valor cobre (trimestre isolado vs. acumulado do semestre/ano)."
        ),
        tools=[
            consultar_resultado_financeiro,
            consultar_serie_historica_resultado,
            consultar_composicao_conselho,
            buscar_conhecimento,
        ],
        llm=GEMINI_LLM,
    )
    return _agent


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
        precisa de contexto pré-injetado na mensagem."""
        reply = rag_agent().kickoff(self.conversation_messages).raw
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
