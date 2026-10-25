#!/usr/bin/env python
"""Testa o ingest incremental do FRE sem gastar embedding.

Percorre as versões do Formulário de Referência de data/corpus_pdf em ordem
cronológica e, pra cada uma, calcula (com o chunker novo e com o legado)
quantos chunks são novos em relação à versão anterior — que é o que seria
embedado. Também confere que o chunker novo não perde conteúdo e não passa
de CHUNK_SIZE. Falha (exit 1) se alguma asserção quebrar.

    uv run python scripts/testar_fre_incremental.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cbrag import knowledge_config as kc  # noqa: E402
import preparar_knowledge as pk  # noqa: E402

INDICE = ROOT / "data/ri_central/_ri_index.json"
CORPUS = ROOT / "data/corpus_pdf"


def versoes_fre() -> list[tuple[str, str, str, str]]:
    """[(data_entrega, versão, nome, texto)] sem duplicatas de conteúdo,
    em ordem cronológica."""
    indice = json.loads(INDICE.read_text(encoding="utf-8"))
    achadas = []
    for nome, info in indice.items():
        if info.get("categoria") != kc.CATEGORIA_VERSIONADA:
            continue
        caminho = CORPUS / f"{nome}.txt"
        if not caminho.exists():
            continue
        texto = caminho.read_text(encoding="utf-8")
        m = kc._FRE_VERSAO_RE.search(texto)
        achadas.append((info["data_entrega"], int(m.group(1)) if m else 0, nome, texto))
    achadas.sort(key=lambda t: (t[0], t[1], t[2]))
    vistas, saida = set(), []
    for data, versao, nome, texto in achadas:
        corpo = frozenset(i for i, _ in kc._chunk_fre(texto))
        if corpo in vistas:
            print(f"  (ignorado, mesmo conteúdo de outra versão: {nome})")
            continue
        vistas.add(corpo)
        saida.append((data, str(versao), nome, texto))
    return saida


def chunks_legado(texto: str, data: str) -> set[str]:
    cabecalho = f"[Fonte: RI — Central de Downloads | Data: {data}]"
    return set(kc._chunk_texto(pk.inserir_cabecalhos(texto, cabecalho)))


def testar_qdrant(versoes) -> list[str]:
    """Sincroniza as versões em ordem num Qdrant em memória, com embedding
    falso que só conta chamadas: o que seria pago é o que chega em
    `embed_denso`. Confere que só o delta é embedado e que o índice termina
    com exatamente os chunks da última versão."""
    import os
    import tempfile
    import uuid

    os.environ["CBRAG_PERMITIR_QDRANT_LOCAL"] = "1"  # Qdrant em memória, não o de produção

    from qdrant_client import QdrantClient

    from cbrag import qdrant_store as qs

    falhas = []
    embedados: list[int] = []

    def embed_falso(textos, instruct=None):
        embedados.append(len(textos))
        return [[0.0] * qs.DIM_DENSO for _ in textos]

    qs._cliente = QdrantClient(":memory:")
    qs.embed_denso = embed_falso
    qs.garantir_colecao()

    with tempfile.TemporaryDirectory() as tmp:
        sidecar = {}
        kc.METADATA_SIDECAR = Path(tmp) / "_metadata.json"
        anteriores: set[str] = set()
        for data, versao, nome, texto in versoes:
            arquivo = Path(tmp) / f"{nome}.txt"
            arquivo.write_text(f"[Fonte: RI | Data: {data}]\n{texto}", encoding="utf-8")
            sidecar[arquivo.name] = {
                "origem": "RI — Central de Downloads", "data_iso": data,
                "data_ordinal": 1, "categoria_cvm": kc.CATEGORIA_VERSIONADA,
            }
            kc.METADATA_SIDECAR.write_text(json.dumps(sidecar), encoding="utf-8")

            docs = kc.montar_documentos(arquivo)
            alvo = {str(uuid.uuid5(qs._NAMESPACE, d["doc_id"])) for d in docs}
            esperado_novos = len(alvo - anteriores)
            embedados.clear()
            qs.indexar_arquivo(arquivo)
            total_embedado = sum(embedados)
            if total_embedado != esperado_novos:
                falhas.append(f"{nome}: embedou {total_embedado}, delta esperado {esperado_novos}")

            pontos, _ = qs.cliente().scroll(qs.COLLECTION, limit=10_000, with_payload=["arquivo", "content"])
            ids = {p.id for p in pontos}
            if ids != alvo:
                falhas.append(f"{nome}: índice tem {len(ids)} pontos, versão tem {len(alvo)}")
            if any(p.payload["arquivo"] != arquivo.name for p in pontos):
                falhas.append(f"{nome}: ponto com payload `arquivo` de versão antiga")
            if any(f"| Data: {data}]" not in p.payload["content"] for p in pontos):
                falhas.append(f"{nome}: chunk mantido com cabeçalho de data antiga")
            print(f"  qdrant {data} v{versao}: embedados {total_embedado}, pontos no índice {len(ids)}")
            anteriores = alvo
    return falhas


def main() -> int:
    versoes = versoes_fre()
    print(f"{len(versoes)} versões distintas do FRE\n")
    falhas = []
    anterior_novo = anterior_legado = None
    print(f"{'versão':<28}{'chunks':>7}{'novos':>7}{'removidos':>10}{'mantidos':>9} | {'legado: chunks':>15}{'novos':>7}")
    for data, versao, nome, texto in versoes:
        docs = kc._montar_documentos_fre(texto, {"origem": "RI — Central de Downloads", "data_iso": data})
        ids = {d["doc_id"] for d in docs}
        legado = chunks_legado(texto, data)

        # cobertura: as palavras dos chunks, na ordem, são exatamente as do
        # texto limpo (nada perdido, nada duplicado)
        esperadas = [w for _, linhas in kc._secoes_fre(texto) for ln in linhas for w in ln.split()]
        obtidas = [w for _, corpo in kc._chunk_fre(texto) for w in corpo.split("\n", 1)[1].split()]
        if esperadas != obtidas:
            falhas.append(f"{nome}: palavras dos chunks != palavras do texto ({len(obtidas)} vs {len(esperadas)})")
        if not esperadas:
            falhas.append(f"{nome}: nenhum conteúdo extraído")
        grandes = [d for d in docs if len(d["content"]) > kc.CHUNK_SIZE]
        if grandes:
            falhas.append(f"{nome}: {len(grandes)} chunk(s) acima de {kc.CHUNK_SIZE} caracteres")

        if anterior_novo is None:
            novos, removidos, mantidos = len(ids), 0, 0
            novos_leg = len(legado)
        else:
            novos, removidos, mantidos = len(ids - anterior_novo), len(anterior_novo - ids), len(ids & anterior_novo)
            novos_leg = len(legado - anterior_legado)
        print(f"{data} v{versao:<3} {nome[-8:]:<12}{len(ids):>7}{novos:>7}{removidos:>10}{mantidos:>9} | {len(legado):>15}{novos_leg:>7}")
        anterior_novo, anterior_legado = ids, legado

    print("\nSincronização no Qdrant (em memória, embedding falso):")
    falhas += testar_qdrant(versoes)

    if falhas:
        print("\nFALHAS:")
        for f in falhas:
            print(" -", f)
        return 1
    print("\nOK: cobertura completa, nenhum chunk acima do limite, só o delta é embedado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
