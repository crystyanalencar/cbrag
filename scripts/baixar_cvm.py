#!/usr/bin/env python
"""Baixa documentos periódicos e eventuais (IPE) da Grupo Casas Bahia S.A.
direto da CVM — fatos relevantes, apresentações a analistas, avisos aos
acionistas etc. Fonte oficial, sem bloqueio anti-bot (diferente do site
institucional/RI, que usam Akamai).
"""
import csv
import io
import json
import re
import time
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
PDF_DIR = ROOT / "data/cvm"
# Metadata estruturada do CSV IPE por documento (Categoria, Tipo, Especie,
# Assunto, datas) — sem isso preparar_knowledge.py tinha que recuperar a
# categoria de volta do nome do arquivo (Categoria+Assunto grudados num slug
# só, 442 valores distintos). Chave: nome do arquivo sem extensão.
INDICE_FILE = PDF_DIR / "_ipe_index.json"

CODIGO_CVM = "6505"  # Grupo Casas Bahia S.A.
# Fase 1 de uma migração por fases pra Central de Downloads (mziq, ver
# baixar_ri_mziq.py e STATE.md/CS-26): CVM aberta segue cobrindo só o
# histórico anterior a 2026 — cada fase futura desce esse limite mais um
# ano (2025, 2024...) conforme scripts/migrar_ano_para_central.py migra
# mais um ano. Não é decisão final de "histórico fica pra sempre na CVM".
ANOS = range(2021, 2026)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; cbrag-crawler/1.0)"}


def baixar_csv_do_ano(ano: int) -> list[dict]:
    url = f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{ano}.zip"
    resp = requests.get(url, headers=HEADERS, timeout=60)
    if resp.status_code != 200:
        print(f"{ano}: sem dados ({resp.status_code})")
        return []
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        nome_csv = zf.namelist()[0]
        with zf.open(nome_csv) as f:
            texto = f.read().decode("latin-1")
    leitor = csv.DictReader(io.StringIO(texto), delimiter=";")
    return [linha for linha in leitor if linha["Codigo_CVM"] == CODIGO_CVM]


def nome_arquivo(linha: dict) -> str:
    partes = [
        linha["Data_Entrega"][:10],
        linha["Categoria"],
        linha.get("Assunto", "") or linha.get("Tipo", ""),
        linha["Protocolo_Entrega"],
    ]
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", "_".join(partes)).strip("_").lower()
    return f"{slug[:180]}.pdf"


def salvar_indice(linhas: list[dict]) -> None:
    """Grava INDICE_FILE com as colunas úteis do CSV IPE, uma entrada por
    documento, chaveada pelo mesmo nome que `nome_arquivo` gera — é assim
    que preparar_knowledge.py casa o .txt extraído com a linha do CSV."""
    indice = {}
    for linha in linhas:
        chave = nome_arquivo(linha).removesuffix(".pdf")
        indice[chave] = {
            "categoria": linha["Categoria"],
            "tipo": linha.get("Tipo", ""),
            "especie": linha.get("Especie", ""),
            "assunto": linha.get("Assunto", ""),
            "data_entrega": linha["Data_Entrega"][:10],
            "data_referencia": linha.get("Data_Referencia", "")[:10],
            "protocolo": linha["Protocolo_Entrega"],
        }
    INDICE_FILE.write_text(json.dumps(indice, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Índice IPE: {len(indice)} documentos em {INDICE_FILE}")


def main() -> dict:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    todas_linhas = []
    for ano in ANOS:
        linhas = baixar_csv_do_ano(ano)
        print(f"{ano}: {len(linhas)} documentos da Grupo Casas Bahia")
        todas_linhas.extend(linhas)
    salvar_indice(todas_linhas)

    salvos, falhas = 0, 0
    for linha in todas_linhas:
        destino = PDF_DIR / nome_arquivo(linha)
        if destino.exists():
            salvos += 1
            continue
        try:
            resp = requests.get(linha["Link_Download"], headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"FALHA {linha['Link_Download']}: {e}")
            falhas += 1
            continue
        destino.write_bytes(resp.content)
        salvos += 1
        time.sleep(0.2)

    print(f"Total: {salvos} documentos salvos em {PDF_DIR}, {falhas} falharam")
    return {"cvm_salvos": salvos, "cvm_falhas": falhas}


if __name__ == "__main__":
    main()
