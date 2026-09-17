#!/usr/bin/env python
"""Baixa a composição de administradores/conselho estruturada da Grupo Casas
Bahia direto do dataset FRE (Formulário de Referência) da CVM
(dados.cvm.gov.br) — uma linha por pessoa/cargo, com data de eleição/posse
exatas, sempre a versão mais recente arquivada.

Por quê isso existe: pergunta de "quem é o conselho hoje" é estado atual de
uma entidade que muda por evento (eleição, renúncia), não fato narrativo —
igual resultado financeiro (ver baixar_dfp_itr.py/dados_financeiros.py), a
CVM já disponibiliza isso estruturado (item 12 do Anexo 24 da ICVM 480) em
vez de precisar inferir de ata de assembleia em texto livre. O FRE é
reenviado pela empresa toda vez que a composição muda (confirmado: a
renúncia de 16/ago/2026 já aparece refletida no arquivo de 2026 com data de
modificação 06/set/2026), então o arquivo do ano corrente já é o snapshot
mais atual disponível — não precisa de histórico de anos anteriores como a
DRE precisa (lá o objetivo é comparar trimestres).
"""
import csv
import io
import json
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT_FILE = ROOT / "data/cvm_estruturado/conselho.json"

CNPJ_COMPANHIA = "33.041.260/0652-90"
NOME_ARQUIVO_CSV = "fre_cia_aberta_administrador_membro_conselho_fiscal_{ano}.csv"
ANOS_TENTATIVA = (2026, 2025)  # ano corrente primeiro; cai pro anterior se ainda não arquivado
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; cbrag-crawler/1.0)"}

COLUNAS_UTEIS = (
    "Nome", "Orgao_Administracao", "Cargo_Eletivo_Ocupado",
    "Data_Eleicao", "Data_Posse", "Prazo_Mandato", "Versao", "Data_Referencia",
)


def baixar_ano(ano: int) -> list[dict]:
    url = f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FRE/DADOS/fre_cia_aberta_{ano}.zip"
    resp = requests.get(url, headers=HEADERS, timeout=60)
    if resp.status_code != 200:
        print(f"FRE {ano}: sem dados ({resp.status_code})")
        return []
    nome_csv = NOME_ARQUIVO_CSV.format(ano=ano)
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        if nome_csv not in zf.namelist():
            return []
        with zf.open(nome_csv) as f:
            texto = f.read().decode("latin-1")
    leitor = csv.DictReader(io.StringIO(texto), delimiter=";")
    linhas = [
        {chave: linha[chave] for chave in COLUNAS_UTEIS}
        for linha in leitor
        if linha["CNPJ_Companhia"] == CNPJ_COMPANHIA
    ]
    return linhas


def main() -> dict:
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    for ano in ANOS_TENTATIVA:
        linhas = baixar_ano(ano)
        if linhas:
            # Versão mais recente arquivada nesse ano (empresa reenvia o FRE
            # inteiro a cada evento societário relevante).
            versao_max = max(int(l["Versao"]) for l in linhas)
            linhas = [l for l in linhas if int(l["Versao"]) == versao_max]
            print(f"FRE {ano} v{versao_max}: {len(linhas)} administradores/conselheiros")
            OUT_FILE.write_text(
                json.dumps(linhas, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            return {"conselho_linhas": len(linhas)}
    print("FRE: nenhum ano disponível")
    return {"conselho_linhas": 0}


if __name__ == "__main__":
    main()
