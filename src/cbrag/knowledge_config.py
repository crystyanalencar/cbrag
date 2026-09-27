"""Configuração compartilhada da base de conhecimento, usada pelo
IngestFlow (grava) e pelo CbragFlow (só lê): caminhos, chunking,
manifesto incremental, montagem de documentos com metadata e a busca que o
chat usa. O motor (Qdrant, denso + BM25) fica em qdrant_store.py.

Histórico: até 2026-09-10 o motor era o Chroma via wrapper do CrewAI
(`crewai.rag.chromadb`). Saiu porque Chroma local não tem vetor esparso
(BM25 só no Chroma Cloud) e o corpus — texto regulatório da CVM — pede
busca léxica (ver docs/busca-hibrida.md). Backup do storage antigo em
`D:/dev/github/_backups/casas_bahia_rag_chroma_2026-09-10/`.

Importante: os vetores densos só fazem sentido pro modelo de embedding que
os gerou (Qwen3 Embedding 8B via OpenRouter, 4096 dimensões, desde
2026-09-20 — antes era nomic-embed-text via Ollama, 768). Trocar de modelo
exige recriar a coleção e rodar o backfill do zero.
"""
import hashlib
import json
import re
import zlib
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
# Teto de chunks do mesmo arquivo no top-k do chat (None = sem teto).
MAX_POR_ARQUIVO_CHAT: int | None = None

OLLAMA_LLM = "ollama/gemma4:e4b"
# Geração via OpenRouter (fase 4) — substituiu Gemini+Groq manual. Lê
# OPENROUTER_API_KEY do .env (via litellm, `crewai[litellm]`, já instalado
# pro Groq antigo). Preset free-tier (fase de testes) — já se observou
# modelo grátis ignorando resultado de tool em conversa longa; considerar
# isso antes de fechar pra produção.
OPENROUTER_LLM = "openrouter/@preset/free-tier-first"


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
    documento fica registrado como fase futura."""
    return [
        texto[i : i + CHUNK_SIZE]
        for i in range(0, len(texto), CHUNK_SIZE - CHUNK_OVERLAP)
    ]


# Formulário de Referência (FRE): mesmo documento de ~350 páginas
# republicado várias vezes por mês com poucas mudanças. O chunker por offset
# fixo (acima) desloca todo chunk depois de qualquer edição, e o cabeçalho
# com a data da versão muda o hash de todos; então o FRE tem chunker e
# identidade próprios, e o índice guarda só a versão vigente (ver
# qdrant_store._sincronizar_versao e docs/ingestao.md).
CATEGORIA_VERSIONADA = "Formulário de Referência"
FRE_CHUNK_MIN = 700    # caracteres de corpo antes de aceitar uma fronteira por hash
FRE_CORPO_MAX = 1750   # + título da seção + cabeçalho fica abaixo de CHUNK_SIZE
FRE_FRONTEIRA_K = 3    # 1 fim de sentença em K encerra o chunk (após o mínimo): ~1,1 mil caracteres
_FRE_PAGINA_RE = re.compile(r"^PÁGINA: \d+ de \d+\s*$")
_FRE_RODAPE_RE = re.compile(r"^Formulário de Referência - \d{4} - .+ Versão : \d+\s*$")
_FRE_RUIDO_RE = re.compile(r"^(Docusign Envelope ID:|JUR_SP - )")
_FRE_VERSAO_RE = re.compile(r"Versão : (\d+)")


def _secoes_fre(texto: str) -> list[tuple[str, list[str]]]:
    """Texto extraído do FRE -> [(item, linhas)]. Cada página começa com
    três linhas: o item (ex. "2.1 Condições financeiras e patrimoniais"),
    "PÁGINA: n de N" e "Formulário de Referência - ... Versão : v"; esse
    cabeçalho, o índice que o antecede, a numeração de página e o ruído
    de assinatura eletrônica saem: são justamente o que muda de uma versão
    pra outra sem o conteúdo mudar."""
    linhas = texto.splitlines()
    marcadores: dict[int, str] = {}  # índice da linha "PÁGINA:" -> item
    descartar: set[int] = set()
    for i, linha in enumerate(linhas):
        if not _FRE_PAGINA_RE.match(linha):
            continue
        j = i - 1
        while j >= 0 and not linhas[j].strip():
            j -= 1
        if j < 0:
            continue
        marcadores[i] = " ".join(linhas[j].split())
        descartar.update({j, i})
        if i + 1 < len(linhas) and _FRE_RODAPE_RE.match(linhas[i + 1]):
            descartar.add(i + 1)
    if not marcadores:
        return []

    inicio = min(min(descartar), min(marcadores))
    secoes: list[tuple[str, list[str]]] = []
    item = None
    for idx in range(inicio, len(linhas)):
        if idx in marcadores:
            item = marcadores[idx]
            if not secoes or secoes[-1][0] != item:
                secoes.append((item, []))
            continue
        if idx in descartar or item is None:
            continue
        linha = " ".join(linhas[idx].split())
        if not linha or _FRE_RUIDO_RE.match(linha) or _FRE_RODAPE_RE.match(linha):
            continue
        secoes[-1][1].append(linha)
    return [(titulo, corpo) for titulo, corpo in secoes if corpo]


def _chunk_fre(texto: str) -> list[tuple[str, str]]:
    """FRE -> [(identidade, corpo)], chunking por conteúdo (content-defined)
    dentro de cada item, em cima do fluxo de **palavras**, não de linhas:
    o PDF é reflowed entre versões (a quebra de linha muda sem o texto
    mudar — medido: 39% das linhas diferentes entre duas versões com 2,6% de
    texto realmente novo), então linha não serve de átomo.

    A fronteira só cai no fim de uma sentença (palavra terminada em `.;:!?`)
    cujo hash das últimas 4 palavras satisfaz a condição, depois de um
    mínimo de tamanho. Editar um trecho só muda o chunk que o contém: o
    seguinte recomeça na mesma sentença de fronteira e sai idêntico. Trecho
    sem pontuação (tabela) é cortado em FRE_CORPO_MAX. Nunca atravessa item
    (2.1, 4.1...), que vai na primeira linha do chunk.

    `identidade` = hash das palavras (sem quebra de linha, sem cabeçalho);
    `corpo` mantém as quebras de linha da versão atual, pra tabela continuar
    legível pro LLM."""
    chunks: list[tuple[str, str]] = []
    for titulo, linhas in _secoes_fre(texto):
        palavras: list[tuple[str, bool]] = []  # (palavra, começa linha nova)
        for n, linha in enumerate(linhas):
            for k, palavra in enumerate(linha.split()):
                palavras.append((palavra, n > 0 and k == 0))

        buffer: list[tuple[str, bool]] = []
        tamanho = 0

        def fechar():
            nonlocal buffer, tamanho
            if buffer:
                identidade = hashlib.sha256(
                    (titulo + "\n" + " ".join(p for p, _ in buffer)).encode("utf-8")
                ).hexdigest()
                corpo = titulo + "\n"
                for i, (palavra, quebra) in enumerate(buffer):
                    corpo += ("\n" if quebra and i else " " if i else "") + palavra
                chunks.append((identidade, corpo))
            buffer, tamanho = [], 0

        for i, (palavra, quebra) in enumerate(palavras):
            if buffer and tamanho + len(palavra) + 1 > FRE_CORPO_MAX:
                fechar()
            buffer.append((palavra, quebra))
            tamanho += len(palavra) + 1
            if (
                tamanho >= FRE_CHUNK_MIN
                and palavra[-1] in ".;:!?"
                and zlib.crc32(" ".join(p for p, _ in buffer[-4:]).encode("utf-8")) % FRE_FRONTEIRA_K == 0
            ):
                fechar()
        fechar()
    return chunks


def _montar_documentos_fre(texto: str, metadata_base: dict) -> list[dict]:
    """`doc_id` é a identidade do chunk (hash das palavras), **sem** o
    cabeçalho com a data nem as quebras de linha: chunk que não mudou de
    uma versão pra outra mantém o mesmo id e não é reembedado. O cabeçalho
    (com versão e data vigentes) só entra no `content`."""
    versao = _FRE_VERSAO_RE.search(texto)
    cabecalho = (
        f"[Fonte: {metadata_base.get('origem', 'RI')} — Formulário de Referência"
        f"{f' (versão {versao.group(1)})' if versao else ''}"
        f" | Data: {metadata_base.get('data_iso') or 'data desconhecida'}]"
    )
    return [
        {"content": f"{cabecalho}\n{corpo}", "doc_id": identidade, "metadata": metadata_base}
        for identidade, corpo in _chunk_fre(texto)
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
    if metadata_base.get("categoria_cvm") == CATEGORIA_VERSIONADA:
        return _montar_documentos_fre(texto, metadata_base)
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


def buscar_resultados(pergunta: str, max_por_arquivo: int | None = MAX_POR_ARQUIVO_CHAT) -> list[dict]:
    """Busca que o chat usa: índice Qdrant (qdrant_store.py), modo
    `MODO_CHAT` — híbrido ponderado (`PESO_BM25_CHAT`) desde 2026-09-20,
    melhor resultado no golden depois da troca de embedder (ver
    docs/busca-hibrida.md).
    Devolve dicts `{content, metadata, score}` na ordem do ranking, que
    `scripts/avaliar_retrieval.py` usa pra medir recall contra o golden
    sem depender do LLM. Sem roteamento por categoria nem blend de
    recência: eram remendos pra diluição da busca vetorial, e o BM25 sem
    eles já supera o Chroma com eles no golden."""
    from cbrag import qdrant_store  # import local: qdrant_store importa este módulo

    peso_bm25 = qdrant_store.PESO_BM25_CHAT if qdrant_store.MODO_CHAT == "hibrido" else None
    return qdrant_store.buscar(
        pergunta, modo=qdrant_store.MODO_CHAT, peso_bm25=peso_bm25, max_por_arquivo=max_por_arquivo
    )
