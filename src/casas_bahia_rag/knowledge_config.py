"""Configuração compartilhada da base de conhecimento (Chroma + embedder
Ollama), usada tanto pelo IngestFlow (grava) quanto pelo CasasBahiaRagFlow
(só lê).

Por que isso existe: por padrão o CrewAI grava o Chroma fora do repo
(`%LOCALAPPDATA%\\CrewAI\\<project>\\knowledge\\` no Windows) — inconsistente
com o resto do projeto e impossível de levar pra outra máquina sem
reprocessar. `set_rag_config()` aponta o cliente global do CrewAI pra
`data/knowledge_storage/` (dentro do repo, copiável) usando o embedder
Ollama configurado aqui uma única vez.

Importante: os vetores só fazem sentido pro modelo de embedding que os
gerou (`nomic-embed-text`, 768 dimensões). Trocar de modelo de embedding
não é compatível com o que já foi indexado — exige reprocessar tudo do
zero (apagar `data/knowledge_storage/` e rodar o IngestFlow de novo).
"""
from pathlib import Path

import crewai.rag.chromadb.config as _chromadb_config_module
from crewai.rag.chromadb.config import ChromaDBConfig
from crewai.rag.config.utils import set_rag_config
from crewai.rag.embeddings.factory import build_embedder
from crewai.knowledge.storage.knowledge_storage import KnowledgeStorage

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = ROOT / "data" / "knowledge"
STORAGE_DIR = ROOT / "data" / "knowledge_storage"
MARKER_FILE = STORAGE_DIR / ".embedded_ok"

COLLECTION_NAME = "casas_bahia_conhecimento"

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

def configurar_rag() -> None:
    """Aponta o cliente Chroma global pra data/knowledge_storage/.

    Chamado toda vez (não é guardado por flag): `set_rag_config()` salva a
    config numa `ContextVar`, que é isolada por thread/contexto de
    execução — não por processo. O CrewAI roda turnos de conversa em
    threads diferentes, então uma guarda tipo "só configura uma vez" deixa
    threads novas sem a config (caem no default `openai`, que bate de
    frente com o que já foi persistido como `ollama`). Repetir a chamada é
    barato (só monta objetos em memória, sem I/O de rede).
    """
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    # Construir Settings(persist_directory=...) na mão trava num bug de
    # compatibilidade pydantic no validador da lib. Em vez disso, aponta o
    # DEFAULT_STORAGE_PATH que ChromaDBConfig usa por padrão (mesmo caminho
    # que o código interno do CrewAI usa com sucesso) pra dentro do repo,
    # e deixa a fábrica de settings padrão fazer o resto.
    _chromadb_config_module.DEFAULT_STORAGE_PATH = str(STORAGE_DIR)
    embedding_function = build_embedder(OLLAMA_EMBEDDER)
    set_rag_config(ChromaDBConfig(embedding_function=embedding_function))


def ja_embedado() -> bool:
    return MARKER_FILE.exists()


def marcar_embedado() -> None:
    MARKER_FILE.write_text("ok", encoding="utf-8")


def _storage() -> KnowledgeStorage:
    configurar_rag()
    # embedder=None: usa o embedder global já configurado acima, não cria
    # um cliente/config próprio (ver KnowledgeStorage._init_client).
    return KnowledgeStorage(collection_name=COLLECTION_NAME, embedder=None)


def buscar_contexto(pergunta: str) -> list[str]:
    """Retorna os trechos mais relevantes da base já embedada."""
    resultados = _storage().search(
        [pergunta], limit=RESULTS_LIMIT, score_threshold=SCORE_THRESHOLD
    )
    return [r["content"] for r in resultados]
