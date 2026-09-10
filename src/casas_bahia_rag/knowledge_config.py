"""Configuração compartilhada da base de conhecimento, usada pelo
IngestFlow (grava) e pelo CasasBahiaRagFlow (só lê): caminhos, chunking,
manifesto incremental, montagem de documentos com metadata e a busca que o
chat usa. O motor (Qdrant, denso + BM25) fica em qdrant_store.py.

Histórico: até 2026-09-10 o motor era o Chroma via wrapper do CrewAI
(`crewai.rag.chromadb`). Saiu porque Chroma local não tem vetor esparso
(BM25 só no Chroma Cloud) e o corpus — texto regulatório da CVM — pede
busca léxica (ver STATE.md, fase 3.2). Backup do storage antigo em
`D:/dev/github/_backups/casas_bahia_rag_chroma_2026-09-10/`.

Importante: os vetores densos só fazem sentido pro modelo de embedding que
os gerou (`nomic-embed-text`, 768 dimensões). Trocar de modelo exige
recriar a coleção e rodar o backfill do zero (~2h).
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = ROOT / "data" / "knowledge"
STORAGE_DIR = ROOT / "data" / "knowledge_storage"
MANIFEST_FILE = STORAGE_DIR / "manifest.json"
METADATA_SIDECAR = KNOWLEDGE_DIR / "_metadata.json"

COLLECTION_NAME = "casas_bahia_conhecimento"

# > o intervalo de repetição do cabeçalho [Fonte: ... | Data: ...] injetado
# por scripts/preparar_knowledge.py (800 caracteres), senão um chunk pode
# cair inteiro entre dois cabeçalhos e perder a origem/data.
CHUNK_SIZE = 2000
CHUNK_OVERLAP = 200
RESULTS_LIMIT = 8

OLLAMA_LLM = "ollama/gemma4:e4b"
# Geração via Gemini (fase 2, ver STATE.md) — lê GOOGLE_API_KEY/GEMINI_API_KEY
# do .env (carregado automaticamente pelo crewai.llm via load_dotenv()).
# Flash-Lite em vez de Flash puro: free tier bem mais folgado (15 RPM/1000
# RPD vs 10 RPM/250 RPD do 2.5 Flash) — resolve os 429 de cota diária
# batidos em sessão anterior.
GEMINI_LLM = "gemini/gemini-3.1-flash-lite"
# Fallback pro 503 (sobrecarga)/429 (cota diária) do Gemini — via litellm
# (`uv add "crewai[litellm]"`), lê GROQ_API_KEY do .env. Modelo escolhido:
# "llama-3.3-70b-versatile" e "llama-3.1-8b-instant" (nomes mais comuns)
# davam 404 nessa conta/key ("does not exist or you do not have access to
# it") — só `openai/gpt-oss-120b` respondeu; suporta tool-calling, testado
# ponta a ponta.
GROQ_LLM = "groq/openai/gpt-oss-120b"


def _hash_arquivo(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def _ler_manifesto() -> dict:
    if not MANIFEST_FILE.exists():
        return {}
    return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))


def _hash_do_registro(registro) -> str | None:
    """Manifesto antigo guardava só a hash como string; formato atual
    guarda {"hash", "chunks", "embedado_em"} pra dar visibilidade do que
    entrou no índice sem precisar consultar o banco."""
    if registro is None:
        return None
    return registro if isinstance(registro, str) else registro.get("hash")


def arquivos_pendentes(arquivos: list[Path]) -> list[Path]:
    """Filtra, dentre `arquivos`, só os que são novos ou mudaram desde a
    última vez que foram indexados (hash do conteúdo diferente do
    manifesto). Sem chamada de rede — só leitura de disco."""
    manifesto = _ler_manifesto()
    pendentes = []
    for caminho in arquivos:
        if _hash_do_registro(manifesto.get(caminho.name)) != _hash_arquivo(caminho):
            pendentes.append(caminho)
    return pendentes


def ano_do_arquivo(caminho: Path) -> int | None:
    """Ano (de data_iso, sidecar de preparar_knowledge.py) pra permitir
    indexar em fases (2026 primeiro, testar, depois voltar pro resto) em
    vez de tudo de uma vez."""
    data_iso = _ler_metadata_sidecar().get(caminho.name, {}).get("data_iso")
    return int(data_iso[:4]) if data_iso else None


def ordenar_por_recencia(arquivos: list[Path], ano_minimo: int | None = None) -> list[Path]:
    """Mais recente primeiro; se `ano_minimo` for passado, corta arquivo de
    ano anterior a esse (fica pendente pro manifesto, não processado agora
    — próxima chamada sem o corte faz o backfill, upsert idempotente)."""
    com_ano = [(caminho, ano_do_arquivo(caminho)) for caminho in arquivos]
    if ano_minimo is not None:
        com_ano = [(c, a) for c, a in com_ano if a is not None and a >= ano_minimo]
    com_ano.sort(key=lambda par: par[1] or 0, reverse=True)
    return [caminho for caminho, _ in com_ano]


def atualizar_manifesto(arquivos: list[Path], n_chunks: dict[str, int] | None = None) -> None:
    """Registra hash + quantidade de chunks + timestamp de cada arquivo
    processado, preservando entradas de arquivos não tocados nesta rodada."""
    from datetime import datetime, timezone

    n_chunks = n_chunks or {}
    manifesto = _ler_manifesto()
    for caminho in arquivos:
        manifesto[caminho.name] = {
            "hash": _hash_arquivo(caminho),
            "chunks": n_chunks.get(caminho.name),
            "embedado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.write_text(
        json.dumps(manifesto, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _chunk_texto(texto: str) -> list[str]:
    """Slicing char-based (mesmo tamanho/overlap desde o início do projeto,
    herdado do TextFileKnowledgeSource do CrewAI) — mudar isso muda o
    doc_id de todo chunk e força reindexar tudo. Chunking por estrutura do
    documento fica registrado como fase futura no STATE.md."""
    return [
        texto[i : i + CHUNK_SIZE]
        for i in range(0, len(texto), CHUNK_SIZE - CHUNK_OVERLAP)
    ]


def montar_documentos(caminho: Path) -> list[dict]:
    """Chunka um arquivo de data/knowledge/ e monta os dicts
    `{content, doc_id, metadata}` — doc_id é sha256 do chunk (identidade
    estável, vira o id do ponto no Qdrant), metadata vem do sidecar
    (`categoria_cvm`, `assunto`, `data_iso`, `data_ordinal`...); chaves com
    valor desconhecido são omitidas."""
    info = _ler_metadata_sidecar().get(caminho.name, {})
    metadata_base = {"arquivo": caminho.name}
    for chave in (
        "origem", "categoria", "data_iso", "data_ordinal",
        # campos limpos do CSV IPE (preparar_knowledge.campos_cvm)
        "categoria_cvm", "tipo_cvm", "especie_cvm", "assunto", "data_referencia",
    ):
        valor = info.get(chave)
        if valor is not None:
            metadata_base[chave] = valor

    texto = caminho.read_text(encoding="utf-8")
    documentos = []
    for chunk in _chunk_texto(texto):
        documentos.append(
            {
                "content": chunk,
                "doc_id": hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                "metadata": metadata_base,
            }
        )
    return documentos


def _ler_metadata_sidecar() -> dict[str, dict]:
    if not METADATA_SIDECAR.exists():
        return {}
    return json.loads(METADATA_SIDECAR.read_text(encoding="utf-8"))


def buscar_contexto(pergunta: str) -> list[str]:
    """Só o texto dos trechos, pro LLM — ver `buscar_resultados`."""
    return [r["content"] for r in buscar_resultados(pergunta)]


def buscar_resultados(pergunta: str) -> list[dict]:
    """Busca que o chat usa: índice Qdrant (qdrant_store.py), modo
    `MODO_CHAT` (BM25 puro hoje — decidido pelo golden, ver STATE.md fase
    3.2; híbrido fica a um switch de distância pra quando o embedder
    denso melhorar). Devolve dicts `{content, metadata, score}` na ordem
    do ranking, que `scripts/avaliar_retrieval.py` usa pra medir recall
    contra o golden sem depender do LLM. Sem roteamento por categoria nem
    blend de recência: eram remendos pra diluição da busca vetorial, e o
    BM25 sem eles já supera o Chroma com eles no golden."""
    from casas_bahia_rag import qdrant_store  # import local: qdrant_store importa este módulo

    return qdrant_store.buscar(pergunta, modo=qdrant_store.MODO_CHAT)
