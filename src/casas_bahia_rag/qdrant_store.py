"""Base de conhecimento no Qdrant: vetor denso (nomic-embed-text via Ollama)
+ vetor esparso BM25 (fastembed, stemmer português), busca híbrida com
fusão RRF feita pelo próprio motor.

Por que existe (ver STATE.md, fase 3.2): o corpus é majoritariamente texto
regulatório formal (atas/comunicados CVM) e a maioria das perguntas que
falhavam pedia match de termo exato (número de lojas, nome de executivo,
"remuneração") — ponto fraco de busca só-vetorial. Chroma local não tem
vetor esparso (só no Chroma Cloud), por isso a migração.

Usa `qdrant_client` direto, não o wrapper `crewai.rag.qdrant`: o wrapper
embeda só denso e filtra só por igualdade simples — não faz híbrido.

Modo embedded por padrão (arquivos em data/knowledge_storage/qdrant/, sem
servidor); com QDRANT_URL no .env usa servidor (docker no VPS), mesma API.
"""
import atexit
import os
import re
import unicodedata
import uuid
from pathlib import Path

import ollama
from fastembed.sparse.bm25 import Bm25
from qdrant_client import QdrantClient, models

from casas_bahia_rag import knowledge_config as kc

QDRANT_PATH = kc.STORAGE_DIR / "qdrant"
COLLECTION = kc.COLLECTION_NAME
VETOR_DENSO = "denso"
VETOR_BM25 = "bm25"
DIM_DENSO = 768  # nomic-embed-text
MODELO_DENSO = "nomic-embed-text"
LOTE_EMBED = 32
# Candidatos por lado antes da fusão. RRF precisa de profundidade: um chunk
# mediano nas duas listas (que é o que queremos) só vence se entrar nas
# duas — com 8 por lado ele nem aparece.
PROFUNDIDADE = 40
# Modo que o chat usa (knowledge_config.buscar_resultados). "bm25" decidido
# pelo golden (scripts/avaliar_retrieval.py, 14 perguntas): bm25 11/14 MRR
# 0.574 vs híbrido 11/14 MRR 0.500 vs denso 5/14 — o denso (nomic-embed-text)
# não soma nada mensurável neste corpus hoje. Trocar pra "hibrido" quando o
# embedder denso for trocado e o golden mostrar ganho.
MODO_CHAT = "bm25"
# uuid5 determinístico a partir do doc_id (sha256 do chunk): mesmo chunk ->
# mesmo ponto, upsert idempotente. Qdrant só aceita int ou UUID como id.
_NAMESPACE = uuid.UUID("6d0c2a5e-4b3f-4f0e-9c6a-2a1b7e8f9d10")

_cliente: QdrantClient | None = None
_bm25: Bm25 | None = None


def cliente() -> QdrantClient:
    """Singleton: o modo embedded trava o diretório, abrir duas vezes no
    mesmo processo dá erro."""
    global _cliente
    if _cliente is None:
        url = os.environ.get("QDRANT_URL")
        if url:
            _cliente = QdrantClient(url=url, api_key=os.environ.get("QDRANT_API_KEY"))
        else:
            QDRANT_PATH.mkdir(parents=True, exist_ok=True)
            _cliente = QdrantClient(path=str(QDRANT_PATH))
        # Fechar explicitamente antes do interpretador morrer: o __del__ do
        # cliente embedded roda tarde demais e estoura "sys.meta_path is
        # None" no shutdown (só ruído, mas polui todo script).
        atexit.register(_cliente.close)
    return _cliente


def bm25() -> Bm25:
    """fastembed emite só TF por termo (tokenização + stopwords + stemmer
    Snowball 'portuguese'); o IDF é aplicado pelo Qdrant via
    `Modifier.IDF` na coleção — sem o modifier BM25 vira contagem crua."""
    global _bm25
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


def embed_denso(textos: list[str]) -> list[list[float]]:
    """Em lote via `ollama.embed` (bem mais rápido que um-a-um)."""
    vetores: list[list[float]] = []
    for i in range(0, len(textos), LOTE_EMBED):
        resposta = ollama.embed(model=MODELO_DENSO, input=textos[i : i + LOTE_EMBED])
        vetores.extend(resposta["embeddings"])
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


def indexar_arquivo(caminho: Path) -> int:
    """Remove os pontos antigos do arquivo (chunk que sumiu não some no
    upsert) e insere os atuais com os dois vetores. Devolve nº de chunks."""
    documentos = kc.montar_documentos(caminho)
    if not documentos:
        return 0
    c = cliente()
    c.delete(COLLECTION, points_selector=_filtro_arquivo(caminho.name))
    densos = embed_denso([d["content"] for d in documentos])
    esparsos = list(bm25().embed([_texto_bm25(d) for d in documentos], batch_size=256))
    pontos = [_ponto(d, v, e) for d, v, e in zip(documentos, densos, esparsos)]
    for i in range(0, len(pontos), 256):
        c.upsert(COLLECTION, points=pontos[i : i + 256])
    return len(pontos)


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
) -> list[dict]:
    """Devolve `[{content, metadata, score}]` na ordem do ranking.

    modo: "denso" (só vetor), "bm25" (só léxico) ou "hibrido" (os dois,
    fundidos por RRF). `peso_bm25=None` usa o RRF do motor (sem peso);
    com valor (0..1) funde em Python com esse peso pro BM25 e o resto pro
    denso. Os modos isolados existem pra medição
    (`scripts/avaliar_retrieval.py`), o chat usa "hibrido".
    """
    c = cliente()
    filtro = _filtro_categorias(categorias)
    prefetches = []
    if modo in ("denso", "hibrido"):
        prefetches.append(
            models.Prefetch(
                query=embed_denso([pergunta])[0], using=VETOR_DENSO,
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
    for p in pontos:
        payload = dict(p.payload or {})
        content = payload.pop("content", "")
        resultados.append({"id": p.id, "content": content, "metadata": payload, "score": p.score})
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
