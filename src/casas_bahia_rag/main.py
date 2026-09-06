#!/usr/bin/env python
"""Chatbot RAG conversacional sobre a Grupo Casas Bahia (institucional +
RI + financeiro/CVM). Só faz retrieval (Chroma já embedado pelo
IngestFlow, ver knowledge_config.py) + geração via LLM local Ollama.

Retrieval é feito manualmente (knowledge_config.buscar_contexto) em vez de
deixar o CrewAI recuperar sozinho, porque a recuperação automática só
existe no caminho Agent.execute_task()/Crew — Agent.kickoff() standalone
(o usado aqui dentro do Flow conversacional) não consulta knowledge nenhum.
"""
from crewai import Agent, Flow
from crewai.flow.conversational import ConversationConfig, ConversationState
from crewai.flow.flow import listen

from casas_bahia_rag.knowledge_config import OLLAMA_LLM, buscar_contexto

_agent: Agent | None = None


def rag_agent() -> Agent:
    """Constrói o Agent de geração uma única vez por processo (sem knowledge
    embutido — retrieval é manual, ver _montar_prompt)."""
    global _agent
    if _agent is not None:
        return _agent

    _agent = Agent(
        role="Especialista em Relações com Investidores e Institucional da Grupo Casas Bahia",
        goal=(
            "Responder perguntas sobre a Grupo Casas Bahia (institucional, "
            "governança, financeiro e RI) usando o contexto recuperado da base "
            "de conhecimento, sempre citando a fonte e a data do trecho usado."
        ),
        backstory=(
            "Você conhece a fundo os documentos institucionais e regulatórios "
            "da Grupo Casas Bahia (site institucional, RI, fatos relevantes e "
            "demonstrações financeiras arquivados na CVM). Cada trecho de "
            "contexto que você recebe traz um cabeçalho '[Fonte: ... | Data: "
            "...]' — use-o para citar a origem e para priorizar sempre a "
            "informação mais recente quando encontrar dados conflitantes de "
            "datas diferentes (ex.: capital social, composição do conselho, "
            "situação de recuperação judicial). Se o contexto não tiver a "
            "resposta, diga isso claramente em vez de inventar."
        ),
        llm=OLLAMA_LLM,
    )
    return _agent


def _montar_prompt(pergunta: str) -> str:
    chunks = buscar_contexto(pergunta)
    if not chunks:
        return pergunta
    contexto = "\n\n---\n\n".join(chunks)
    return (
        f"Contexto recuperado da base de conhecimento:\n\n{contexto}\n\n"
        f"Pergunta do usuário: {pergunta}"
    )


@ConversationConfig(defer_trace_finalization=True)
class CasasBahiaRagFlow(Flow[ConversationState]):
    conversational = True

    def route_turn(self, context: dict) -> str:
        return "ANSWER"

    @listen("ANSWER")
    def answer(self) -> str:
        """Responde perguntas institucionais/financeiras usando a base de conhecimento."""
        pergunta = self.state.current_user_message
        prompt = _montar_prompt(pergunta)
        reply = rag_agent().kickoff(prompt).raw
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
