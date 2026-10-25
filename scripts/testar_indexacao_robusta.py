#!/usr/bin/env python
"""Testa o que derrubou o FRE do índice no run de 2026-10-24, com
texto sintético, Qdrant em memória e embedding falso (não depende de data/
nem gasta embedding).

- Embedding que falha no meio da indexação não apaga a versão anterior.
- Erro transitório do OpenRouter (503) é tentado de novo; erro definitivo não.
- Arquivo indexado como documento genérico e depois como FRE não fica
  duplicado: os pontos genéricos dele saem.
- O índice da Central mantém a entrada de arquivo que ainda está em disco
  quando a API para de listá-lo (a v8 tomou o protocolo da v7).

Falha (exit 1) se alguma asserção quebrar.

    uv run python scripts/testar_indexacao_robusta.py
"""
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

os.environ["CBRAG_PERMITIR_QDRANT_LOCAL"] = "1"  # Qdrant em memória, não o de produção

from qdrant_client import QdrantClient  # noqa: E402

from cbrag import knowledge_config as kc  # noqa: E402
from cbrag import qdrant_store as qs  # noqa: E402
import baixar_ri_mziq as ri  # noqa: E402
from testar_fre_layout import FRE_WEB  # noqa: E402

FRE_V8 = FRE_WEB.replace("Versão: 7", "Versão: 8").replace(
    "As condições financeiras são adequadas.", "As condições financeiras seguem adequadas em 2026."
)

chamadas: list[int] = []
falhar_com: list[Exception] = []


def embed_falso(textos, instruct=None):
    if falhar_com:
        raise falhar_com.pop(0)
    chamadas.append(len(textos))
    return [[0.0] * qs.DIM_DENSO for _ in textos]


def erro_http(status: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(f"{status} Server Error", response=resp)


def preparar(tmp: Path, sidecar: dict, nome: str, texto: str, fre: bool) -> Path:
    arquivo = tmp / nome
    arquivo.write_text(texto, encoding="utf-8")
    sidecar[nome] = {"origem": "RI — Central de Downloads", "data_iso": "2026-10-20", "data_ordinal": 1}
    if fre:
        sidecar[nome]["categoria_cvm"] = kc.CATEGORIA_VERSIONADA
    kc.METADATA_SIDECAR.write_text(json.dumps(sidecar), encoding="utf-8")
    return arquivo


def ids_de(arquivo: Path) -> set[str]:
    return {str(uuid.uuid5(qs._NAMESPACE, d["doc_id"])) for d in kc.montar_documentos(arquivo)}


def ids_no_indice() -> set:
    pontos, _ = qs.cliente().scroll(qs.COLLECTION, limit=10_000)
    return {p.id for p in pontos}


def reiniciar() -> None:
    qs._cliente = QdrantClient(":memory:")
    qs.garantir_colecao()
    chamadas.clear()
    falhar_com.clear()


def testar_qdrant() -> list[str]:
    falhas = []
    qs.embed_denso = embed_falso
    time.sleep = lambda s: None  # as esperas entre tentativas, sem esperar de verdade
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        kc.METADATA_SIDECAR = tmp / "_metadata.json"

        # 1. falha definitiva no embedding da v8: a v7 continua inteira
        reiniciar()
        sidecar: dict = {}
        v7 = preparar(tmp, sidecar, "2026-10-20_fre_v7.txt", FRE_WEB, fre=True)
        qs.indexar_arquivo(v7)
        v8 = preparar(tmp, sidecar, "2026-10-22_fre_v8.txt", FRE_V8, fre=True)
        if not ids_de(v8) - ids_de(v7):
            falhas.append("cenário inválido: v8 sintética não tem chunk novo")
        falhar_com.append(erro_http(400))
        try:
            qs.indexar_arquivo(v8)
            falhas.append("falha definitiva: indexar_arquivo não propagou o erro")
        except requests.HTTPError:
            pass
        if ids_no_indice() != ids_de(v7):
            falhas.append(f"falha definitiva: índice tem {len(ids_no_indice())} pontos, v7 tem {len(ids_de(v7))}")

        # 2. 503 isolado: tenta de novo e termina com a v8
        falhar_com.append(erro_http(503))
        qs.indexar_arquivo(v8)
        if ids_no_indice() != ids_de(v8):
            falhas.append("503 isolado: índice não terminou com exatamente a v8")

        # 3. 503 em todas as tentativas: desiste, e a versão anterior fica
        reiniciar()
        sidecar = {}
        v7 = preparar(tmp, sidecar, "2026-10-20_fre_v7.txt", FRE_WEB, fre=True)
        qs.indexar_arquivo(v7)
        v8 = preparar(tmp, sidecar, "2026-10-22_fre_v8.txt", FRE_V8, fre=True)
        falhar_com.extend(erro_http(503) for _ in range(len(qs.ESPERAS_REENVIO_EMBED) + 1))
        try:
            qs.indexar_arquivo(v8)
            falhas.append("503 persistente: indexar_arquivo não desistiu")
        except requests.HTTPError:
            pass
        if ids_no_indice() != ids_de(v7):
            falhas.append("503 persistente: versão anterior não ficou no índice")

        # 4. arquivo indexado como genérico e depois como FRE: sem duplicata
        reiniciar()
        sidecar = {}
        generico = preparar(tmp, sidecar, "2026-10-20_fre_v7.txt", FRE_WEB, fre=False)
        qs.indexar_arquivo(generico)
        ids_genericos = ids_no_indice()
        fre = preparar(tmp, sidecar, "2026-10-20_fre_v7.txt", FRE_WEB, fre=True)
        qs.indexar_arquivo(fre)
        if ids_genericos & ids_de(fre):
            falhas.append("cenário inválido: chunks genérico e FRE com o mesmo id")
        if ids_no_indice() != ids_de(fre):
            falhas.append(
                f"genérico → FRE: índice tem {len(ids_no_indice())} pontos, FRE tem {len(ids_de(fre))} "
                f"({len(ids_no_indice() & ids_genericos)} genéricos sobraram)"
            )
    return falhas


def testar_indice_ri() -> list[str]:
    falhas = []
    with tempfile.TemporaryDirectory() as d:
        ri.PDF_DIR = Path(d)
        ri.INDICE_FILE = ri.PDF_DIR / "_ri_index.json"
        anterior = {
            "2026-10-20_formulario_de_referencia_versao_7_ec971a60": {"categoria": kc.CATEGORIA_VERSIONADA},
            "2026-01-10_apagado_do_disco_aaaa0000": {"categoria": "Comunicado ao Mercado"},
            "2026-10-22_formulario_de_referencia_versao_8_ec971a60": {"categoria": "velha"},
        }
        ri.INDICE_FILE.write_text(json.dumps(anterior), encoding="utf-8")
        (ri.PDF_DIR / "2026-10-20_formulario_de_referencia_versao_7_ec971a60.pdf").write_bytes(b"%PDF")
        (ri.PDF_DIR / "2026-10-22_formulario_de_referencia_versao_8_ec971a60.pdf").write_bytes(b"%PDF")
        novo = {"2026-10-22_formulario_de_referencia_versao_8_ec971a60": {"categoria": kc.CATEGORIA_VERSIONADA}}
        mantidas = ri.preservar_entradas_em_disco(novo)
        if mantidas != 1 or "2026-10-20_formulario_de_referencia_versao_7_ec971a60" not in novo:
            falhas.append(f"índice RI: entrada da v7 em disco não foi mantida ({mantidas} mantidas)")
        if "2026-01-10_apagado_do_disco_aaaa0000" in novo:
            falhas.append("índice RI: manteve entrada de arquivo que não está em disco")
        if novo["2026-10-22_formulario_de_referencia_versao_8_ec971a60"]["categoria"] != kc.CATEGORIA_VERSIONADA:
            falhas.append("índice RI: entrada antiga sobrescreveu a que a API listou")
    return falhas


def main() -> int:
    falhas = testar_qdrant() + testar_indice_ri()
    if falhas:
        print("FALHAS:")
        for f in falhas:
            print(" -", f)
        return 1
    print("OK: falha no embedding não apaga a versão anterior, 503 é tentado de novo, "
          "genérico que vira FRE não duplica, índice RI mantém arquivo em disco.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
