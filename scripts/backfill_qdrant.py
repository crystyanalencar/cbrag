"""Constrói/atualiza o índice Qdrant a partir de data/knowledge/ (denso via
OpenRouter/Qwen3 Embedding + BM25 via fastembed), incremental pelo manifesto
(data/knowledge_storage/manifest.json): só arquivo novo ou mudado (hash) é
reprocessado. Mesma lógica de `IngestFlow.embutir_conhecimento`, sem rodar
o resto do ingest (crawl, CVM, PDFs) — útil pra reconstruir só o índice.

Uso:
    uv run python scripts/backfill_qdrant.py
    ANO_MINIMO=2026 uv run python scripts/backfill_qdrant.py   # em fases
    uv run python scripts/backfill_qdrant.py --so-bm25          # só o esparso
Precisa de `OPENROUTER_API_KEY` no `.env` (embedding denso via Qwen3 Embedding).
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cbrag import knowledge_config as kc  # noqa: E402
from cbrag import qdrant_store as qs  # noqa: E402


def main() -> dict:
    qs.garantir_colecao()
    if "--so-bm25" in sys.argv:
        # Só recalcula o vetor esparso (normalização do BM25 mudou) — sem
        # chamar o embedder denso, sem tocar no denso nem no manifesto.
        inicio = time.time()
        n = qs.reindexar_bm25()
        print(f"BM25 recalculado em {n} pontos, {time.time() - inicio:.0f}s")
        return {"bm25_reindexado": n}
    arquivos = sorted(kc.KNOWLEDGE_DIR.glob("*.txt"))
    pendentes = kc.arquivos_pendentes(arquivos)
    ano_minimo = os.environ.get("ANO_MINIMO")
    pendentes = kc.ordenar_por_recencia(pendentes, ano_minimo=int(ano_minimo) if ano_minimo else None)
    print(f"{len(pendentes)} arquivo(s) pendente(s) de {len(arquivos)}")

    total_chunks = 0
    inicio = time.time()
    for i, caminho in enumerate(pendentes, start=1):
        n = qs.indexar_arquivo(caminho)
        kc.atualizar_manifesto([caminho], n_chunks={caminho.name: n})
        total_chunks += n
        if i % 25 == 0 or i == len(pendentes):
            print(f"  {i}/{len(pendentes)} arquivos, {total_chunks} chunks, {time.time() - inicio:.0f}s")
    return {"arquivos": len(pendentes), "chunks": total_chunks}


if __name__ == "__main__":
    print(main())
