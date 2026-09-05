#!/usr/bin/env python
"""Baixa documentos periódicos e eventuais (IPE) da Grupo Casas Bahia S.A.
direto da CVM — fatos relevantes, apresentações a analistas, avisos aos
acionistas etc. Fonte oficial, sem bloqueio anti-bot (diferente do site
institucional/RI, que usam Akamai).
"""
import csv
import io
import re
import time
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
PDF_DIR = ROOT / "data/cvm"

CODIGO_CVM = "6505"  # Grupo Casas Bahia S.A.
ANOS = range(2021, 2027)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; casas-bahia-rag-crawler/1.0)"}


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


def main() -> dict:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    todas_linhas = []
    for ano in ANOS:
        linhas = baixar_csv_do_ano(ano)
        print(f"{ano}: {len(linhas)} documentos da Grupo Casas Bahia")
        todas_linhas.extend(linhas)

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
