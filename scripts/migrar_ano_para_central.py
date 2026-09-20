#!/usr/bin/env python
"""Migração por fases CVM aberta -> Central de Downloads (mziq), um ano por
vez (ver STATE.md/CS-26). Execução manual única por fase — não entra no
`ingest_flow` recorrente.

Pré-condição (checar à mão antes de rodar, não automatizado aqui): o ano já
foi coletado da Central (`baixar_ri_mziq.py` cobrindo esse ano) e passou por
todo o pipeline (extrair -> preparar -> embutir), com cobertura de
categoria_cvm comparável à da CVM aberta pro mesmo ano, e o golden set
(`avaliar_retrieval.py`) rodado como baseline com a duplicação ainda
presente. Só depois disso apagar — este script não verifica cobertura
sozinho, é decisão de quem roda.

Uso: `uv run python scripts/migrar_ano_para_central.py 2026`
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cbrag import knowledge_config as kc  # noqa: E402
from cbrag import qdrant_store as qs  # noqa: E402

CVM_DIR = ROOT / "data/cvm"
CORPUS_PDF_DIR = ROOT / "data/corpus_pdf"


def arquivos_cvm_do_ano(ano: int) -> list[str]:
    metadata = json.loads(kc.METADATA_SIDECAR.read_text(encoding="utf-8"))
    return [
        nome
        for nome, info in metadata.items()
        if (info.get("data_iso") or "").startswith(str(ano))
        and (info.get("origem") or "").startswith("CVM —")
    ]


def remover_do_manifesto(nomes: set[str]) -> None:
    manifesto = kc._ler_manifesto()
    for nome in nomes:
        manifesto.pop(nome, None)
    kc.MANIFEST_FILE.write_text(json.dumps(manifesto, indent=2, ensure_ascii=False), encoding="utf-8")


def remover_da_metadata(nomes: set[str]) -> None:
    metadata = json.loads(kc.METADATA_SIDECAR.read_text(encoding="utf-8"))
    for nome in nomes:
        metadata.pop(nome, None)
    kc.METADATA_SIDECAR.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")


def main(ano: int) -> None:
    qs.garantir_colecao()
    c = qs.cliente()
    nomes = arquivos_cvm_do_ano(ano)
    if not nomes:
        print(f"Nada da CVM pro ano {ano} em {kc.METADATA_SIDECAR}. Nada a fazer.")
        return

    print(f"{len(nomes)} arquivo(s) da CVM/{ano} — removendo vetores, manifesto e arquivos.")
    for nome in nomes:
        c.delete(qs.COLLECTION, points_selector=qs._filtro_arquivo(nome))
        (kc.KNOWLEDGE_DIR / nome).unlink(missing_ok=True)
        (CORPUS_PDF_DIR / f"{Path(nome).stem}.txt").unlink(missing_ok=True)
        (CVM_DIR / f"{Path(nome).stem}.pdf").unlink(missing_ok=True)

    remover_do_manifesto(set(nomes))
    remover_da_metadata(set(nomes))
    print(f"Migração {ano} concluída: {len(nomes)} arquivo(s) removido(s) do Qdrant/disco.")
    print("Rode avaliar_retrieval.py de novo e compare com o baseline antes de considerar fechado.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ano", type=int, help="Ano a migrar (ex.: 2026)")
    args = parser.parse_args()
    main(args.ano)
