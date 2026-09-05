#!/usr/bin/env python
"""Chatbot RAG conversacional sobre a Grupo Casas Bahia (institucional +
RI + financeiro/CVM), usando um único Agent com knowledge_sources sobre
data/knowledge/ e LLM local via Ollama.

Retrieval é feito manualmente (agent.knowledge.query) em vez de deixar o
CrewAI recuperar sozinho, porque a recuperação automática só existe no
caminho Agent.execute_task()/Crew — Agent.kickoff() standalone (o usado
aqui dentro do Flow conversacional) não consulta knowledge nenhum. E o
Agent é construído uma única vez (módulo cacheia a instância): reconstruir
o Agent a cada turno chamaria set_knowledge() de novo, que re-chunka e
re-embeda os 732 arquivos via Ollama a cada pergunta do usuário.
"""
from pathlib import Path

from crewai import Agent, Flow
from crewai.flow.conversational import ConversationConfig, ConversationState
from crewai.flow.flow import listen
from crewai.knowledge.source.text_file_knowledge_source import TextFileKnowledgeSource

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = ROOT / "data" / "knowledge"

# > o intervalo de repetição do cabeçalho [Fonte: ... | Data: ...] injetado
# por scripts/preparar_knowledge.py (800 caracteres), senão um chunk pode
# cair inteiro entre dois cabeçalhos e perder a origem/data.
CHUNK_SIZE = 2000
CHUNK_OVERLAP = 200
RESULTS_LIMIT = 8
SCORE_THRESHOLD = 0.35

OLLAMA_LLM = "ollama/gemma4:e4b"
OLLAMA_EMBEDDER = {
    "provider": "ollama",
    "config": {
        "model_name": "nomic-embed-text",
        "url": "http://localhost:11434/api/embeddings",
    },
}

_agent: Agent | None = None


def _knowledge_sources() -> list[TextFileKnowledgeSource]:
    # Path objects (não str) evitam o prefixo automático "knowledge/" que o
    # CrewAI aplica a paths string (convenção pra pasta knowledge/ na raiz
    # do projeto, que não é onde guardamos nosso corpus).
    arquivos = sorted(KNOWLEDGE_DIR.glob("*.txt"))
    if not arquivos:
        raise RuntimeError(
            f"Nenhum arquivo em {KNOWLEDGE_DIR} — rode o IngestFlow primeiro (uv run ingest)."
        )
    return [
        TextFileKnowledgeSource(
            file_paths=arquivos,
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
        )
    ]


def rag_agent() -> Agent:
    """Constrói o Agent uma única vez por processo (embedding é caro)."""
    global _agent
    if _agent is not None:
        return _agent

    agent = Agent(
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
        knowledge_sources=_knowledge_sources(),
        embedder=OLLAMA_EMBEDDER,
    )
    agent.set_knowledge()  # dispara o chunking+embedding (uma vez só)
    _agent = agent
    return agent


def _montar_prompt(pergunta: str) -> str:
    agent = rag_agent()
    if agent.knowledge is None:
        return pergunta

    resultados = agent.knowledge.query(
        [pergunta], results_limit=RESULTS_LIMIT, score_threshold=SCORE_THRESHOLD
    )
    if not resultados:
        return pergunta

    contexto = "\n\n---\n\n".join(r["content"] for r in resultados)
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
