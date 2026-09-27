"""Base de conhecimento no Qdrant: vetor denso (Qwen3 Embedding 8B via
OpenRouter) + vetor esparso BM25 (fastembed, stemmer português), busca
híbrida com fusão RRF feita pelo próprio motor.

Por que existe (ver docs/busca-hibrida.md): o corpus é majoritariamente texto
regulatório formal (atas/comunicados CVM) e a maioria das perguntas que
falhavam pedia match de termo exato (número de lojas, nome de executivo,
"remuneração") — ponto fraco de busca só-vetorial. Chroma local não tem
vetor esparso (só no Chroma Cloud), por isso a migração.

Usa `qdrant_client` direto, não o wrapper `crewai.rag.qdrant`: o wrapper
embeda só denso e filtra só por igualdade simples — não faz híbrido.

Modo embedded por padrão (arquivos em data/knowledge_storage/qdrant/, sem
servidor); com QDRANT_URL no .env usa servidor (docker na VM Oracle —
necessário pra ingestão agendada rodar concorrente com o chat sem disputar
o lock do arquivo embedded, ver docs/infra-producao.md).

Denso trocou de Ollama (`nomic-embed-text`, local, exige GPU) pra OpenRouter
(`qwen/qwen3-embedding-8b`, API) — a VM de produção não tem GPU. Confirmado
nomic-embed-text não existe no catálogo de embeddings do OpenRouter; Qwen3
Embedding também existe no Ollama (`qwen3-embedding:8b`) pra rodar de graça
localmente se um dia fizer sentido. Formato de entrada do Qwen3 é diferente
do nomic: só a pergunta leva prefixo de instrução, documento indexado fica
em texto puro (ver `embed_denso`).
"""
import atexit
import os
import re
import threading
import unicodedata
import uuid
from pathlib import Path

import litellm
from fastembed.sparse.bm25 import Bm25
from qdrant_client import QdrantClient, models

from cbrag import knowledge_config as kc

# litellm imprime um banner "Provider List" quando o modelo não está no mapa
# de custo/tokenizer dele (caso de embedding via OpenRouter) — cosmético,
# mas polui o log a cada lote (LOTE_EMBED=32) num backfill de milhares de
# chunks; a chamada funciona normalmente sem isso.
litellm.suppress_debug_info = True

QDRANT_PATH = kc.STORAGE_DIR / "qdrant"
COLLECTION = kc.COLLECTION_NAME
VETOR_DENSO = "denso"
VETOR_BM25 = "bm25"
DIM_DENSO = 4096  # qwen3-embedding-8b, dimensão nativa (sem truncar via Matryoshka)
MODELO_DENSO = "openrouter/qwen/qwen3-embedding-8b"
# Instrução exigida pelo Qwen3 Embedding só do lado da pergunta (o modelo
# foi treinado assim — documento indexado não leva instrução, só o texto).
_INSTRUCAO_BUSCA = (
    "Given a search query about Grupo Casas Bahia corporate/financial/"
    "regulatory documents, retrieve relevant passages"
)
LOTE_EMBED = 32
# Candidatos por lado antes da fusão. RRF precisa de profundidade: um chunk
# mediano nas duas listas (que é o que queremos) só vence se entrar nas
# duas — com 8 por lado ele nem aparece.
PROFUNDIDADE = 40
# Candidatos buscados por vaga do top-k quando há teto por arquivo (buscar
# com `max_por_arquivo`): sem sobra, o teto encolhe o resultado em vez de
# abrir espaço pra outros documentos.
_SOBRA_DEDUP = 4
# Modo que o chat usa (knowledge_config.buscar_resultados). Histórico com
# nomic-embed-text/Ollama: bm25 11/14 MRR 0.574 vs híbrido (sem peso) 11/14
# MRR 0.500 vs denso 5/14 — denso não somava nada, ficou em "bm25".
# Reavaliado em 2026-09-20 depois da troca pro Qwen3 Embedding 8B/OpenRouter
# (golden recalculado do zero, corpus 100% reembedado, ver
# docs/busca-hibrida.md): denso
# sozinho subiu pra 8/14 MRR 0.429 (ainda perde de bm25), híbrido sem peso
# 11/14 MRR 0.599 (empata recall, MRR melhor), híbrido com peso_bm25=0.7
# **12/14 MRR 0.699** — melhor resultado de todos, testados 0.6/0.7/0.8
# (0.699/0.693/0.651). Decisão: "hibrido" com PESO_BM25_CHAT abaixo.
MODO_CHAT = "hibrido"
# Só usado quando MODO_CHAT="hibrido" (buscar() ignora fora desse modo).
PESO_BM25_CHAT = 0.7
# uuid5 determinístico a partir do doc_id (sha256 do chunk): mesmo chunk ->
# mesmo ponto, upsert idempotente. Qdrant só aceita int ou UUID como id.
_NAMESPACE = uuid.UUID("6d0c2a5e-4b3f-4f0e-9c6a-2a1b7e8f9d10")

_cliente: QdrantClient | None = None
_bm25: Bm25 | None = None
_lock_cliente = threading.Lock()
_lock_bm25 = threading.Lock()


def cliente() -> QdrantClient:
    """Singleton: o modo embedded trava o diretório, abrir duas vezes no
    mesmo processo dá erro. Lock evita a corrida quando o agent chama a
    tool de busca mais de uma vez em paralelo (threads concorrentes viam
    `_cliente is None` ao mesmo tempo e a segunda tentava abrir o diretório
    já travado pela primeira)."""
    global _cliente
    if _cliente is None:
        with _lock_cliente:
            if _cliente is None:
                url = os.environ.get("QDRANT_URL")
                if url:
                    _cliente = QdrantClient(url=url, api_key=os.environ.get("QDRANT_API_KEY"))
                else:
                    QDRANT_PATH.mkdir(parents=True, exist_ok=True)
                    _cliente = QdrantClient(path=str(QDRANT_PATH))
                # Fechar explicitamente antes do interpretador morrer: o
                # __del__ do cliente embedded roda tarde demais e estoura
                # "sys.meta_path is None" no shutdown (só ruído, mas polui
                # todo script).
                atexit.register(_cliente.close)
    return _cliente


def bm25() -> Bm25:
    """fastembed emite só TF por termo (tokenização + stopwords + stemmer
    Snowball 'portuguese'); o IDF é aplicado pelo Qdrant via
    `Modifier.IDF` na coleção — sem o modifier BM25 vira contagem crua."""
    global _bm25
    if _bm25 is None:
        with _lock_bm25:
            if _bm25 is None:
                _bm25 = Bm25("Qdrant/bm25", language="portuguese")
    return _bm25


def garantir_colecao() -> None:
    c = cliente()
    if not c.collection_exists(COLLECTION):
        c.create_collection(
            COLLECTION,
            vectors_config={
                VETOR_DENSO: models.VectorParams(size=DIM_DENSO, distance=models.Distance.COSINE)
            },
            sparse_vectors_config={
                VETOR_BM25: models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )
    # Filtro sem índice de payload é scan linear em modo embedded.
    for campo, tipo in (
        ("arquivo", models.PayloadSchemaType.KEYWORD),
        ("categoria", models.PayloadSchemaType.KEYWORD),
        ("categoria_cvm", models.PayloadSchemaType.KEYWORD),
        ("tipo_cvm", models.PayloadSchemaType.KEYWORD),
        ("data_ordinal", models.PayloadSchemaType.INTEGER),
    ):
        c.create_payload_index(COLLECTION, campo, tipo)


def embed_denso(textos: list[str], instruct: str | None = None) -> list[list[float]]:
    """Em lote via `litellm.embedding` (bem mais rápido que um-a-um).
    `instruct=True` (usado só em `buscar()`) prefixa cada texto com a
    instrução exigida pelo Qwen3 Embedding do lado da pergunta —
    `indexar_arquivo` nunca passa isso, documento vai em texto puro.

    Chave: `OPENROUTER_EMBED_API_KEY` se existir (limite de crédito próprio
    pro embedding, separado da geração); senão `None` e o litellm usa a
    `OPENROUTER_API_KEY` geral."""
    api_key = os.getenv("OPENROUTER_EMBED_API_KEY") or None
    if instruct:
        textos = [f"Instruct: {instruct}\nQuery: {t}" for t in textos]
    vetores: list[list[float]] = []
    for i in range(0, len(textos), LOTE_EMBED):
        resposta = litellm.embedding(
            model=MODELO_DENSO, input=textos[i : i + LOTE_EMBED], api_key=api_key
        )
        vetores.extend(item["embedding"] for item in resposta.data)
    return vetores


_CABECALHO_RE = re.compile(r"^\[Fonte: .*?\| Data: .*?\]\s*$", re.MULTILINE)


def normalizar_bm25(texto: str) -> str:
    """Mesma normalização pros dois lados (índice e pergunta), senão termo
    não casa. Medido no golden antes de existir:
    - Acento: o tokenizer/stemmer do fastembed não tira acento, então
      "renúncia" e "renunciaram" viravam tokens diferentes ("renúnc" vs
      "renunc"), idem "número"/"numero". NFKD + descarte de não-ASCII
      resolve; stem roda em cima do texto sem acento nos dois lados.
    - Cabeçalho `[Fonte: ... | Data: ...]` repetido a cada ~800 caracteres
      (preparar_knowledge.py) carrega a categoria da CVM em texto ("reuni o
      da administra o", "conselho") — inflava BM25 de todo chunk pra
      pergunta com "conselho de administração", empurrando ata de
      assembleia genérica pro topo. Sai do texto do BM25 (fica no chunk que
      o LLM lê, ele precisa da data)."""
    sem_cabecalho = _CABECALHO_RE.sub(" ", texto)
    return unicodedata.normalize("NFKD", sem_cabecalho).encode("ascii", "ignore").decode()


def _texto_bm25(documento: dict) -> str:
    """Texto que o BM25 enxerga: `assunto` do CSV da CVM (quando existe) na
    frente do chunk. O assunto é o campo mais denso em termo do documento
    ("renúncia do sr. X ao cargo de diretor presidente") e um chunk do
    meio da ata pode não repetir esses termos — anexar garante que todo
    chunk do documento casa com a pergunta que cita o assunto. Só pro
    esparso; o denso embeda o chunk puro."""
    assunto = documento["metadata"].get("assunto")
    texto = f"{assunto}\n{documento['content']}" if assunto else documento["content"]
    return normalizar_bm25(texto)


def reindexar_bm25(lote: int = 512) -> int:
    """Recalcula só o vetor esparso de todos os pontos (sem Ollama, sem
    mexer no denso) — pra quando a normalização do BM25 muda. Segundos a
    minutos, não horas."""
    exigir_servidor()
    c = cliente()
    total = 0
    offset = None
    while True:
        pontos, offset = c.scroll(
            COLLECTION, limit=lote, offset=offset, with_payload=["content", "assunto"], with_vectors=False,
        )
        if not pontos:
            break
        textos = [_texto_bm25({"content": p.payload["content"], "metadata": p.payload}) for p in pontos]
        esparsos = bm25().embed(textos, batch_size=256)
        c.update_vectors(
            COLLECTION,
            points=[
                models.PointVectors(
                    id=p.id,
                    vector={VETOR_BM25: models.SparseVector(indices=e.indices.tolist(), values=e.values.tolist())},
                )
                for p, e in zip(pontos, esparsos)
            ],
        )
        total += len(pontos)
        if offset is None:
            break
    return total


def _ponto(documento: dict, denso: list[float], esparso) -> models.PointStruct:
    payload = {**documento["metadata"], "doc_id": documento["doc_id"], "content": documento["content"]}
    return models.PointStruct(
        id=str(uuid.uuid5(_NAMESPACE, documento["doc_id"])),
        vector={
            VETOR_DENSO: denso,
            VETOR_BM25: models.SparseVector(
                indices=esparso.indices.tolist(), values=esparso.values.tolist()
            ),
        },
        payload=payload,
    )


def _filtro_arquivo(nome: str) -> models.Filter:
    return models.Filter(must=[models.FieldCondition(key="arquivo", match=models.MatchValue(value=nome))])


def exigir_servidor() -> None:
    """Escrita no índice só contra o Qdrant **servidor** (`QDRANT_URL`). Sem
    ela o cliente cai no modo embedded, que aqui é uma sobra de 2026-09-10 do
    tempo do nomic (768 dimensões) — não é o índice de produção, que mora no
    servidor da VM (ver CLAUDE.md e docs/infra-producao.md). Indexar nele
    falha no meio (dimensão) ou, pior, grava num índice que ninguém consulta.
    `CBRAG_PERMITIR_QDRANT_LOCAL=1` libera de propósito (testes em memória)."""
    if os.environ.get("QDRANT_URL") or os.environ.get("CBRAG_PERMITIR_QDRANT_LOCAL") == "1":
        return
    raise RuntimeError(
        "QDRANT_URL não definido: indexar cairia no Qdrant embedded local, que não é o "
        "índice de produção (o embedding e o índice moram no servidor da VM). Rode a "
        "ingestão na VM, ou defina QDRANT_URL apontando pro servidor."
    )


def _filtro_categoria_cvm(categoria: str) -> models.Filter:
    return models.Filter(must=[models.FieldCondition(key="categoria_cvm", match=models.MatchValue(value=categoria))])


def indexar_arquivo(caminho: Path) -> int:
    """Reindexa um arquivo cujo hash mudou desde a última vez (ver
    `knowledge_config.arquivos_pendentes`) sem reembedar chunk cujo
    conteúdo é idêntico a um chunk já indexado (doc_id = sha256 do chunk,
    ponto id = uuid5 determinístico dele — mesmo conteúdo => mesmo ponto).
    Só chama `embed_denso`/`bm25().embed` pro delta real: chunk novo que
    não existia antes. Chunk que sumiu (órfão) é removido; chunk que
    permanece igual não é tocado. Existe pra não repetir o custo de
    embedding em arquivo reenviado quase inteiro a cada versão (FRE via
    Central de Downloads, ver docs/ingestao.md, CS-27) — não resolve o
    efeito avalanche do chunker por offset fixo (edição no início do
    documento desloca tudo depois dela e muda quase todo doc_id mesmo
    assim), só evita o desperdício óbvio quando a mudança não desloca
    offset.

    O FRE (`kc.CATEGORIA_VERSIONADA`) não sofre disso: tem chunker por
    conteúdo e identidade sem a data (`kc._chunk_fre`), e a comparação é
    contra a versão anterior do índice, não contra o mesmo arquivo — o
    índice fica só com a versão vigente e cada versão nova embeda apenas o
    delta."""
    exigir_servidor()
    documentos = kc.montar_documentos(caminho)
    if not documentos:
        return 0
    c = cliente()
    ids_alvo = {str(uuid.uuid5(_NAMESPACE, d["doc_id"])): d for d in documentos}

    # Documento versionado (FRE): o índice guarda só a versão vigente, então
    # "o que já existe" é a categoria inteira, não o arquivo — a versão nova
    # é comparada com a anterior, que é outro arquivo.
    versionado = documentos[0]["metadata"].get("categoria_cvm") == kc.CATEGORIA_VERSIONADA
    filtro_existentes = (
        _filtro_categoria_cvm(kc.CATEGORIA_VERSIONADA) if versionado else _filtro_arquivo(caminho.name)
    )

    pontos_existentes, _ = c.scroll(
        COLLECTION,
        scroll_filter=filtro_existentes,
        limit=10_000,
        with_payload=["content"] if versionado else False,
        with_vectors=False,
    )
    ids_existentes = {p.id for p in pontos_existentes}
    ids_orfaos = ids_existentes - ids_alvo.keys()
    ids_novos = ids_alvo.keys() - ids_existentes

    if ids_orfaos:
        c.delete(COLLECTION, points_selector=models.PointIdsList(points=list(ids_orfaos)))

    documentos_novos = [ids_alvo[i] for i in ids_novos]
    if documentos_novos:
        densos = embed_denso([d["content"] for d in documentos_novos])
        esparsos = list(bm25().embed([_texto_bm25(d) for d in documentos_novos], batch_size=256))
        pontos = [_ponto(d, v, e) for d, v, e in zip(documentos_novos, densos, esparsos)]
        for i in range(0, len(pontos), 256):
            c.upsert(COLLECTION, points=pontos[i : i + 256])

    if versionado:
        # Chunk mantido tem o mesmo texto, mas o cabeçalho (versão/data
        # vigentes) e o arquivo de origem são os da versão nova: atualiza o
        # payload, sem reembedar nada.
        conteudo_atual = {p.id: (p.payload or {}).get("content") for p in pontos_existentes}
        atualizados = 0
        for id_ponto in ids_existentes & ids_alvo.keys():
            d = ids_alvo[id_ponto]
            if conteudo_atual[id_ponto] == d["content"]:
                continue
            c.set_payload(
                COLLECTION,
                payload={**d["metadata"], "doc_id": d["doc_id"], "content": d["content"]},
                points=[id_ponto],
            )
            atualizados += 1
        print(
            f"  {kc.CATEGORIA_VERSIONADA}: {len(documentos_novos)} chunk(s) novo(s) embedado(s), "
            f"{len(ids_existentes & ids_alvo.keys())} mantido(s) ({atualizados} com cabeçalho atualizado), "
            f"{len(ids_orfaos)} removido(s)"
        )

    return len(documentos)


def _filtro_categorias(categorias: list[str] | None) -> models.Filter | None:
    """Filtro opcional por `categoria_cvm` (valor limpo do CSV IPE, ex.
    "Fato Relevante"). O chat não usa hoje (BM25 sem filtro já bate o
    golden); fica pronto pra pergunta que precise restringir."""
    if not categorias:
        return None
    return models.Filter(
        must=[models.FieldCondition(key="categoria_cvm", match=models.MatchAny(any=categorias))]
    )


def buscar(
    pergunta: str,
    modo: str = "hibrido",
    limite: int = kc.RESULTS_LIMIT,
    categorias: list[str] | None = None,
    profundidade: int = PROFUNDIDADE,
    peso_bm25: float | None = None,
    max_por_arquivo: int | None = None,
) -> list[dict]:
    """Devolve `[{content, metadata, score}]` na ordem do ranking.

    modo: "denso" (só vetor), "bm25" (só léxico) ou "hibrido" (os dois,
    fundidos por RRF). `peso_bm25=None` usa o RRF do motor (sem peso);
    com valor (0..1) funde em Python com esse peso pro BM25 e o resto pro
    denso. Os modos isolados existem pra medição
    (`scripts/avaliar_retrieval.py`), o chat usa "hibrido".

    `max_por_arquivo`: no máximo N chunks do mesmo arquivo no resultado.
    Busca `limite * _SOBRA_DEDUP` candidatos e preenche `limite` respeitando
    o teto, pra um documento longo (o FRE, a Petição) não ocupar o top-k
    inteiro e esconder os demais.
    """
    limite_final = limite
    if max_por_arquivo is not None:
        limite = limite_final * _SOBRA_DEDUP
    c = cliente()
    filtro = _filtro_categorias(categorias)
    prefetches = []
    if modo in ("denso", "hibrido"):
        prefetches.append(
            models.Prefetch(
                query=embed_denso([pergunta], instruct=_INSTRUCAO_BUSCA)[0], using=VETOR_DENSO,
                limit=profundidade, filter=filtro,
            )
        )
    if modo in ("bm25", "hibrido"):
        q = next(bm25().query_embed(normalizar_bm25(pergunta)))
        prefetches.append(
            models.Prefetch(
                query=models.SparseVector(indices=q.indices.tolist(), values=q.values.tolist()),
                using=VETOR_BM25, limit=profundidade, filter=filtro,
            )
        )
    if not prefetches:
        raise ValueError(f"modo desconhecido: {modo}")

    if len(prefetches) == 1:
        resposta = c.query_points(
            COLLECTION, query=prefetches[0].query, using=prefetches[0].using,
            query_filter=filtro, limit=limite, with_payload=True,
        )
        pontos = resposta.points
    elif peso_bm25 is None:
        resposta = c.query_points(
            COLLECTION, prefetch=prefetches,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limite, with_payload=True,
        )
        pontos = resposta.points
    else:
        # RRF do Qdrant não tem peso por lista; com peso, roda as duas
        # buscas e funde aqui (mesma fórmula, k=60).
        listas = [
            c.query_points(
                COLLECTION, query=pf.query, using=pf.using, query_filter=filtro,
                limit=profundidade, with_payload=True,
            ).points
            for pf in prefetches
        ]
        pontos = _rrf(listas, pesos=[1 - peso_bm25, peso_bm25])[:limite]

    resultados = []
    por_arquivo: dict[str, int] = {}
    for p in pontos:
        payload = dict(p.payload or {})
        if max_por_arquivo is not None:
            arquivo = payload.get("arquivo", "")
            if por_arquivo.get(arquivo, 0) >= max_por_arquivo:
                continue
            por_arquivo[arquivo] = por_arquivo.get(arquivo, 0) + 1
        content = payload.pop("content", "")
        resultados.append({"id": p.id, "content": content, "metadata": payload, "score": p.score})
        if len(resultados) == limite_final:
            break
    return resultados


def _rrf(listas: list[list], pesos: list[float], k: int = 60) -> list:
    """Reciprocal Rank Fusion ponderado: score(d) = soma_i w_i / (k + rank_i(d)),
    documento ausente numa lista contribui 0 nela."""
    acumulado: dict = {}
    por_id: dict = {}
    for lista, peso in zip(listas, pesos):
        for rank, p in enumerate(lista, start=1):
            acumulado[p.id] = acumulado.get(p.id, 0.0) + peso / (k + rank)
            por_id.setdefault(p.id, p)
    ordenados = sorted(acumulado.items(), key=lambda par: par[1], reverse=True)
    saida = []
    for pid, score in ordenados:
        p = por_id[pid]
        p.score = score
        saida.append(p)
    return saida
