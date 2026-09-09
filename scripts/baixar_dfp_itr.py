#!/usr/bin/env python
"""Baixa a Demonstração de Resultado (DRE) estruturada da Grupo Casas Bahia
direto dos datasets ITR/DFP da CVM (dados.cvm.gov.br) — CSV com uma linha
por conta contábil, com DT_INI_EXERC/DT_FIM_EXERC exatos por linha (dá pra
distinguir trimestre isolado de acumulado sem ambiguidade nenhuma).

Por quê isso existe: os PDFs de resultado (baixados via baixar_cvm.py,
categoria "dados econômico financeiros") têm a mesma tabela em formato
visual sem grade — extração de texto (pypdf ou pdfplumber, testado) achata
coluna/cabeçalho, e o LLM já confundiu trimestre com acumulado do semestre
lendo esse texto achatado. A CVM disponibiliza a mesma DRE em dataset
estruturado (mesmo padrão do IPE que baixar_cvm.py já usa, só que pras
demonstrações financeiras) — sem precisar resolver extração de tabela de
PDF pra esse dado específico.
"""
import csv
import io
import json
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT_FILE = ROOT / "data/cvm_estruturado/dre.json"

# Padrão de 6 dígitos zero-padded do dataset ITR/DFP — diferente do "6505"
# (sem padding) que baixar_cvm.py usa pro dataset IPE.
CODIGO_CVM = "006505"
ANOS = range(2021, 2027)
TIPOS = ("ITR", "DFP")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; casas-bahia-rag-crawler/1.0)"}

COLUNAS_UTEIS = (
    "DT_REFER", "VERSAO", "ORDEM_EXERC", "DT_INI_EXERC", "DT_FIM_EXERC",
    "CD_CONTA", "DS_CONTA", "VL_CONTA",
)


def baixar_dre(tipo: str, ano: int) -> list[dict]:
    url = (
        f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/{tipo}/DADOS/"
        f"{tipo.lower()}_cia_aberta_{ano}.zip"
    )
    resp = requests.get(url, headers=HEADERS, timeout=60)
    if resp.status_code != 200:
        print(f"{tipo} {ano}: sem dados ({resp.status_code})")
        return []
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        nome_csv = f"{tipo.lower()}_cia_aberta_DRE_con_{ano}.csv"
        if nome_csv not in zf.namelist():
            return []
        with zf.open(nome_csv) as f:
            texto = f.read().decode("latin-1")
    leitor = csv.DictReader(io.StringIO(texto), delimiter=";")
    linhas = [
        {chave: linha[chave] for chave in COLUNAS_UTEIS}
        for linha in leitor
        if linha["CD_CVM"] == CODIGO_CVM
    ]
    for linha in linhas:
        linha["tipo"] = tipo
    return linhas


def main() -> dict:
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    todas_linhas = []
    for tipo in TIPOS:
        for ano in ANOS:
            linhas = baixar_dre(tipo, ano)
            print(f"{tipo} {ano}: {len(linhas)} linhas da Grupo Casas Bahia")
            todas_linhas.extend(linhas)

    OUT_FILE.write_text(
        json.dumps(todas_linhas, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Total: {len(todas_linhas)} linhas de DRE salvas em {OUT_FILE}")
    return {"dre_linhas": len(todas_linhas)}


if __name__ == "__main__":
    main()
