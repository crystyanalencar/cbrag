"""Mede o retrieval da base de conhecimento contra tests/golden_retrieval.json.

Por que existe: a troca de motor de busca (Chroma denso -> Qdrant
denso+BM25, ver docs/busca-hibrida.md) precisava de número antes e depois —
sem isso, qualquer ajuste de modo/peso/embedder vira impressão. Baseline
histórico do Chroma (10 perguntas originais): recall@8 5/10, MRR 0.450.

Métricas (por pergunta, agregadas no fim):
- recall@K: 1 se algum dos top-K trouxe um arquivo esperado, senão 0.
- MRR: 1/posição do primeiro acerto (0 se não achou).

Uso:
    uv run python scripts/avaliar_retrieval.py            # modo padrão
    uv run python scripts/avaliar_retrieval.py --modo qdrant-hibrido
    uv run python scripts/avaliar_retrieval.py --k 8 --verboso
Precisa de `OPENROUTER_API_KEY` no `.env` (embedding da pergunta via Qwen3 Embedding).
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cbrag import knowledge_config as kc  # noqa: E402
from cbrag import qdrant_store as qs  # noqa: E402

GOLDEN = ROOT / "tests" / "golden_retrieval.json"

# Cada modo é uma função pergunta -> lista de dicts com `metadata.arquivo`,
# na ordem de ranking. Novos motores (qdrant-denso, qdrant-bm25,
# qdrant-hibrido) entram aqui sem mexer no resto.
MODOS = {
    "chat": kc.buscar_resultados,  # o que o chat usa de fato (MODO_CHAT do qdrant_store)
    "qdrant-denso": lambda pergunta: qs.buscar(pergunta, modo="denso"),
    "qdrant-bm25": lambda pergunta: qs.buscar(pergunta, modo="bm25"),
    "qdrant-hibrido": lambda pergunta: qs.buscar(pergunta, modo="hibrido"),
    "qdrant-hibrido-60": lambda pergunta: qs.buscar(pergunta, modo="hibrido", peso_bm25=0.6),
    "qdrant-hibrido-70": lambda pergunta: qs.buscar(pergunta, modo="hibrido", peso_bm25=0.7),
    "qdrant-hibrido-80": lambda pergunta: qs.buscar(pergunta, modo="hibrido", peso_bm25=0.8),
}


def posicao_do_acerto(resultados: list[dict], esperado: list[str]) -> int | None:
    for i, r in enumerate(resultados, start=1):
        arquivo = (r.get("metadata") or {}).get("arquivo", "")
        if any(sub in arquivo for sub in esperado):
            return i
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modo", choices=sorted(MODOS), default="chat")
    parser.add_argument("--k", type=int, default=kc.RESULTS_LIMIT)
    parser.add_argument(
        "--sem-cvm-2026", action="store_true",
        help="simula a migração: busca 40 candidatos (modo chat), descarta os arquivos da CVM de 2026 e fica com os k primeiros",
    )
    parser.add_argument(
        "--max-por-arquivo", type=int, default=kc.MAX_POR_ARQUIVO_CHAT,
        help="teto de chunks do mesmo arquivo no top-k (só no modo chat)",
    )
    parser.add_argument(
        "--meses-recentes", type=int, default=None,
        help="filtro de data_ordinal_min (só no modo chat) — mede se o filtro "
        "determinístico recupera as falhas conhecidas de 'mais recente' sem "
        "regredir as demais",
    )
    parser.add_argument("--verboso", action="store_true", help="lista os arquivos devolvidos por pergunta")
    args = parser.parse_args()

    casos = json.loads(GOLDEN.read_text(encoding="utf-8"))["casos"]
    buscar = MODOS[args.modo]
    if args.modo == "chat":
        buscar = lambda pergunta: kc.buscar_resultados(  # noqa: E731
            pergunta, max_por_arquivo=args.max_por_arquivo, meses_recentes=args.meses_recentes
        )

    acertos = 0
    soma_rr = 0.0
    acertos_falha_conhecida = 0
    n_falha_conhecida = 0
    if args.sem_cvm_2026:
        # Aproximação de `migrar_ano_para_central.py 2026`: não apaga nada,
        # só tira do resultado o que a migração apagaria. Arquivo da CVM tem
        # nome AAAA_MM_DD_...; o da Central, AAAA-MM-DD_...
        cvm_2026 = re.compile(r"^2026_\d\d_\d\d_")

        def buscar(pergunta):  # noqa: F811
            candidatos = qs.buscar(
                pergunta, modo=qs.MODO_CHAT, limite=40,
                peso_bm25=qs.PESO_BM25_CHAT if qs.MODO_CHAT == "hibrido" else None,
                max_por_arquivo=args.max_por_arquivo,
            )
            return [r for r in candidatos if not cvm_2026.match((r.get("metadata") or {}).get("arquivo", ""))]

    print(f"modo={args.modo}  k={args.k}  perguntas={len(casos)}\n")
    for caso in casos:
        resultados = buscar(caso["pergunta"])[: args.k]
        pos = posicao_do_acerto(resultados, caso["esperado"])
        marca = "OK " if pos else "MISS"
        conhecida = caso.get("falha_conhecida", False)
        if conhecida:
            n_falha_conhecida += 1
            acertos_falha_conhecida += bool(pos)
        acertos += bool(pos)
        soma_rr += (1 / pos) if pos else 0.0
        print(f"[{marca}] pos={pos if pos else '-':>2}  {'(falha conhecida) ' if conhecida else ''}{caso['pergunta']}")
        if args.verboso:
            for i, r in enumerate(resultados, start=1):
                print(f"        {i:2d}. {(r.get('metadata') or {}).get('arquivo', '?')[:110]}")

    n = len(casos)
    print(f"\nrecall@{args.k} = {acertos}/{n} = {acertos / n:.2f}")
    print(f"MRR       = {soma_rr / n:.3f}")
    if n_falha_conhecida:
        print(f"falhas conhecidas recuperadas = {acertos_falha_conhecida}/{n_falha_conhecida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
