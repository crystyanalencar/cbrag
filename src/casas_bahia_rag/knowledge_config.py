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
import hashlib
import json
from pathlib import Path

import crewai.rag.chromadb.config as _chromadb_config_module
from crewai.rag.chromadb.config import ChromaDBConfig
from crewai.rag.config.utils import get_rag_client, set_rag_config
from crewai.rag.embeddings.factory import build_embedder

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
SCORE_THRESHOLD = 0.35

# Roteamento por categoria: cada grupo mapeia termos de intenção da pergunta
# pra um conjunto de categorias (ver scripts/preparar_knowledge.py, campo
# "categoria" do sidecar) que de fato respondem esse tipo de pergunta.
# Existe porque busca semântica pura se afoga em boilerplate repetido (ex.:
# cláusula de política de dividendos repetida em toda ata de assembleia)
# quando a pergunta usa palavra genérica ("lucro", "conselho") — filtrar por
# categoria primeiro exclui o Estatuto Social/editais genéricos antes da
# busca vetorial rodar, e ordenar por data_ordinal (metadata real, não regex
# em texto) resolve recência sem precisar de heurística de peso.
GRUPOS_CATEGORIA = {
    "resultado_financeiro": {
        "termos": (
            "resultado", "lucro", "prejuízo", "prejuizo", "ebitda",
            "receita", "margem", "trimestre", "lair", "faturamento",
            "dívida", "divida", "endividamento",
        ),
        "categorias_contem": ("dados_econ_mico_financeiros", "itr", "resultado"),
    },
    "governanca": {
        "termos": (
            "conselho", "diretoria", "ceo", "presidente", "diretor",
            "administração", "administracao", "eleito", "eleição", "eleicao",
            "renúncia", "renuncia",
        ),
        "categorias_contem": ("reuni_o_da_administra_o", "assembleia", "elei_o"),
    },
}

OLLAMA_LLM = "ollama/gemma4:e4b"
# Geração via Gemini (fase 2, ver STATE.md) — lê GOOGLE_API_KEY/GEMINI_API_KEY
# do .env (carregado automaticamente pelo crewai.llm via load_dotenv()).
# Flash-Lite em vez de Flash puro: free tier bem mais folgado (15 RPM/1000
# RPD vs 10 RPM/250 RPD do 2.5 Flash) — resolve os 429 de cota diária
# batidos em sessão anterior.
GEMINI_LLM = "gemini/gemini-3.1-flash-lite"
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


def _hash_arquivo(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def _ler_manifesto() -> dict[str, str]:
    if not MANIFEST_FILE.exists():
        return {}
    return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))


def _hash_do_registro(registro) -> str | None:
    """Manifesto antigo (antes desta sessão) guardava só a hash como
    string; formato novo guarda {"hash", "chunks", "embedado_em"} pra dar
    visibilidade do que entrou no índice sem precisar consultar o Chroma."""
    if registro is None:
        return None
    return registro if isinstance(registro, str) else registro.get("hash")


def arquivos_pendentes(arquivos: list[Path]) -> list[Path]:
    """Filtra, dentre `arquivos`, só os que são novos ou mudaram desde a
    última vez que foram embedados (hash do conteúdo diferente do
    manifesto). Sem chamada de rede — só leitura de disco."""
    manifesto = _ler_manifesto()
    pendentes = []
    for caminho in arquivos:
        chave = caminho.name
        if _hash_do_registro(manifesto.get(chave)) != _hash_arquivo(caminho):
            pendentes.append(caminho)
    return pendentes


def ano_do_arquivo(caminho: Path) -> int | None:
    """Ano (de data_iso, sidecar de preparar_knowledge.py) pra permitir
    embedar em fases (2026 primeiro, testar, depois voltar pro resto) em
    vez de tudo de uma vez — útil com rate limit de API paga/free tier."""
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
    processado, preservando entradas de arquivos não tocados nesta rodada.
    `n_chunks` (nome do arquivo -> nº de chunks enviados) é opcional pra
    manter compatibilidade com chamadas que só querem marcar o hash."""
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
    """Mesmo slicing char-based que TextFileKnowledgeSource._chunk_text
    usava (crewai/knowledge/source/text_file_knowledge_source.py) —
    mantido idêntico pra não mudar o tamanho/overlap dos chunks já
    existentes, só o caminho de escrita (direto no ChromaDBClient, com
    metadata, em vez de via Knowledge/TextFileKnowledgeSource)."""
    return [
        texto[i : i + CHUNK_SIZE]
        for i in range(0, len(texto), CHUNK_SIZE - CHUNK_OVERLAP)
    ]


def montar_documentos(caminho: Path) -> list[dict]:
    """Chunka um arquivo de data/knowledge/ e monta os dicts pro
    ChromaDBClient.add_documents, com a metadata estruturada do sidecar
    (categoria, data_iso, data_ordinal) — Chroma não aceita valor None em
    metadata, então chaves com valor desconhecido são omitidas."""
    info = _ler_metadata_sidecar().get(caminho.name, {})
    metadata_base = {"arquivo": caminho.name}
    for chave in ("origem", "categoria", "data_iso", "data_ordinal"):
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


def cliente_rag():
    """Cliente de baixo nível do Chroma (get_rag_client), não o wrapper
    Knowledge/TextFileKnowledgeSource — usado tanto pro embedding
    (add_documents com metadata real) quanto pra busca (search com where),
    porque a API de alto nível do CrewAI marca `metadata` como "Currently
    unused" e nunca repassa pro storage (ver STATE.md)."""
    configurar_rag()
    return get_rag_client()


def _ler_metadata_sidecar() -> dict[str, dict]:
    if not METADATA_SIDECAR.exists():
        return {}
    return json.loads(METADATA_SIDECAR.read_text(encoding="utf-8"))


def grupo_da_pergunta(pergunta: str) -> str | None:
    pergunta_lower = pergunta.lower()
    for nome_grupo, grupo in GRUPOS_CATEGORIA.items():
        if any(termo in pergunta_lower for termo in grupo["termos"]):
            return nome_grupo
    return None


def _categorias_do_grupo(nome_grupo: str) -> list[str]:
    """Resolve os substrings de `categorias_contem` pras categorias
    concretas que de fato existem no corpus (lidas do sidecar) — o `where`
    do Chroma só faz match exato/`$in`, não substring, então essa resolução
    precisa acontecer em Python antes da busca."""
    substrings = GRUPOS_CATEGORIA[nome_grupo]["categorias_contem"]
    categorias_existentes = {
        info["categoria"] for info in _ler_metadata_sidecar().values() if info.get("categoria")
    }
    return [
        categoria
        for categoria in categorias_existentes
        if any(sub in categoria for sub in substrings)
    ]


def buscar_contexto(pergunta: str) -> list[str]:
    """Retorna os trechos mais relevantes da base já embedada.

    Pergunta que bate com um grupo de intenção conhecido (resultado
    financeiro, governança — ver GRUPOS_CATEGORIA) busca só dentro das
    categorias relevantes via `where` nativo do Chroma, ordenado por
    `data_ordinal` real (metadata, não regex em texto) — evita o afogamento
    por boilerplate repetido (ex.: cláusula de dividendo repetida em toda
    ata) que fazia o chunk certo nem entrar no top-K da busca semântica
    livre. Pergunta sem grupo conhecido (narrativa/qualitativa) segue busca
    semântica normal, sem filtro.
    """
    cliente = cliente_rag()
    nome_grupo = grupo_da_pergunta(pergunta)

    if nome_grupo is None:
        resultados = cliente.search(
            collection_name=COLLECTION_NAME,
            query=pergunta,
            limit=RESULTS_LIMIT,
            score_threshold=SCORE_THRESHOLD,
        )
        return [r["content"] for r in resultados]

    categorias = _categorias_do_grupo(nome_grupo)
    if not categorias:
        resultados = cliente.search(
            collection_name=COLLECTION_NAME,
            query=pergunta,
            limit=RESULTS_LIMIT,
            score_threshold=SCORE_THRESHOLD,
        )
        return [r["content"] for r in resultados]

    # Testado com a pergunta "quais são os membros do conselho": a ata de
    # renúncia relevante só aparecia na posição 132 dentre os candidatos da
    # categoria "governança" (categoria é ampla — 189 valores distintos
    # batem no substring, cobre toda reunião/assembleia, não só composição
    # de conselho). limit=RESULTS_LIMIT*6 (48) era curto demais pra
    # garantir recall; 300 cobre com folga.
    candidatos = cliente.search(
        collection_name=COLLECTION_NAME,
        query=pergunta,
        limit=300,
        score_threshold=0.0,
        where={"categoria": {"$in": categorias}},
    )
    if not candidatos:
        return []

    # Ordenar só por data (como fazia antes) reintroduz o problema oposto:
    # documento recente mas irrelevante (ex. outra reunião da
    # administração sobre assunto sem relação) supera um documento antigo
    # mas relevante. Com recall já garantido pelo limit acima, mistura
    # score semântico (sinal fraco, mas não nulo) com recência.
    datas = [c["metadata"].get("data_ordinal") or 0 for c in candidatos]
    data_min, data_max = min(datas), max(datas)
    intervalo = (data_max - data_min) or 1

    def score_combinado(candidato: dict) -> float:
        recencia = ((candidato["metadata"].get("data_ordinal") or 0) - data_min) / intervalo
        return 0.4 * candidato["score"] + 0.6 * recencia

    candidatos.sort(key=score_combinado, reverse=True)
    return [r["content"] for r in candidatos[:RESULTS_LIMIT]]
