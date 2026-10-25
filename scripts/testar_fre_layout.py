#!/usr/bin/env python
"""Testa o chunker do FRE nos dois layouts que a Central já publicou, com
texto sintético (não depende de data/).

- Layout CVM (até a versão 6): cada página começa com o item, "PÁGINA: n
  de N" e o rodapé "Formulário de Referência - ... Versão : v".
- Layout "FRE WEB" (a partir da versão 7, 2026-10-20): cada página termina
  com "FRE WEB - Formulário de Referência Ano: aaaa Versão: v" seguido de
  "<item> <página>", que nomeia o texto **anterior**. Antes do conteúdo vêm
  capa, sumário e índice, com o mesmo rodapé.

Falha (exit 1) se alguma asserção quebrar.

    uv run python scripts/testar_fre_layout.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cbrag import knowledge_config as kc  # noqa: E402
sys.path.insert(0, str(ROOT / "scripts"))
import preparar_knowledge as pk  # noqa: E402

RODAPE_WEB = "FRE WEB - Formulário de Referência Ano: 2026 Versão: 7"

FRE_WEB = f"""[Fonte: RI — Central de Downloads | Data: 2026-10-20]
Grupo Casas Bahia S.A.
Formulário de Referência
Ano: 2026
 Versão: 7

Sumário
1. Atividades do emissor 03 2. Comentário dos diretores 04
{RODAPE_WEB}
Sumário 02

Índice
1. Atividades do emissor
1.1 Histórico do emissor 17
{RODAPE_WEB}
indíce | 1. Atividades do emissor 03

A Companhia foi fundada em 1946.
Em 2001, a Globex estabeleceu uma associação.
{RODAPE_WEB}
1.1 Histórico do emissor 17

Em 2016, a Cnova passou a ser subsidiária integral.
{RODAPE_WEB}
1.1 Histórico do emissor 18

As condições financeiras são adequadas.
{RODAPE_WEB}
2.1 Condições financeiras e patrimoniais 74
"""

# Mesmo conteúdo, paginado diferente (a página 17 do item 1.1 virou 20).
FRE_WEB_REPAGINADO = FRE_WEB.replace("emissor 17\n\nEm 2016", "emissor 20\n\nEm 2016").replace(
    "emissor 18\n", "emissor 21\n"
)

FRE_CVM = """[Fonte: RI — Central de Downloads | Data: 2026-09-18]
Índice
1.1 Histórico do emissor 1
1.1 Histórico do emissor
PÁGINA: 1 de 2
Formulário de Referência - 2026 - Grupo Casas Bahia S.A. Versão : 6
A Companhia foi fundada em 1946.
2.1 Condições financeiras e patrimoniais
PÁGINA: 2 de 2
Formulário de Referência - 2026 - Grupo Casas Bahia S.A. Versão : 6
As condições financeiras são adequadas.
"""


def main() -> int:
    falhas = []

    secoes = kc._secoes_fre(FRE_WEB)
    esperado = [
        ("1.1 Histórico do emissor", [
            "A Companhia foi fundada em 1946.",
            "Em 2001, a Globex estabeleceu uma associação.",
            "Em 2016, a Cnova passou a ser subsidiária integral.",
        ]),
        ("2.1 Condições financeiras e patrimoniais", ["As condições financeiras são adequadas."]),
    ]
    if secoes != esperado:
        falhas.append(f"FRE WEB: seções erradas\n    obtido:   {secoes}\n    esperado: {esperado}")

    ids = [i for i, _ in kc._chunk_fre(FRE_WEB)]
    ids_repaginado = [i for i, _ in kc._chunk_fre(FRE_WEB_REPAGINADO)]
    if not ids or ids != ids_repaginado:
        falhas.append("FRE WEB: número de página entrou na identidade do chunk")

    m = kc._FRE_VERSAO_RE.search(FRE_WEB)
    if not m or m.group(1) != "7":
        falhas.append("FRE WEB: versão não reconhecida (`Versão: 7`)")

    secoes_cvm = kc._secoes_fre(FRE_CVM)
    esperado_cvm = [
        ("1.1 Histórico do emissor", ["A Companhia foi fundada em 1946."]),
        ("2.1 Condições financeiras e patrimoniais", ["As condições financeiras são adequadas."]),
    ]
    if secoes_cvm != esperado_cvm:
        falhas.append(f"FRE CVM: seções erradas\n    obtido:   {secoes_cvm}")
    m = kc._FRE_VERSAO_RE.search(FRE_CVM)
    if not m or m.group(1) != "6":
        falhas.append("FRE CVM: versão não reconhecida (`Versão : 6`)")

    # Layout desconhecido: não pode sumir em silêncio.
    docs = kc._montar_documentos_fre("Texto de um FRE com layout que ninguém previu.", {})
    if not docs:
        falhas.append("layout desconhecido: 0 documentos, o FRE sairia do índice sem aviso")

    # Versão que incha (v8 da Central repetia o mesmo bloco em 28 itens)
    # ou encolhe pela metade não substitui a anterior; variação normal
    # substitui.
    meta = {n: {"data_iso": d} for n, d in [("_t_v6", "2026-09-18"), ("_t_v7", "2026-10-20"), ("_t_v8", "2026-10-22")]}
    pk.descartar_versoes_antigas_fre(meta, {"_t_v6": 6, "_t_v7": 7, "_t_v8": 8}, {"_t_v6": 850_000, "_t_v7": 855_000, "_t_v8": 12_183_108})
    if list(meta) != ["_t_v7"]:
        falhas.append(f"FRE inchado: vigente deveria ser a v7, ficou {list(meta)}")
    meta = {n: {"data_iso": d} for n, d in [("_t_v7", "2026-10-20"), ("_t_v8", "2026-10-22")]}
    pk.descartar_versoes_antigas_fre(meta, {"_t_v7": 7, "_t_v8": 8}, {"_t_v7": 855_000, "_t_v8": 1_200_000})
    if list(meta) != ["_t_v8"]:
        falhas.append(f"FRE com crescimento normal: vigente deveria ser a v8, ficou {list(meta)}")
    meta = {n: {"data_iso": d} for n, d in [("_t_v7", "2026-10-20"), ("_t_v8", "2026-10-22")]}
    pk.descartar_versoes_antigas_fre(meta, {"_t_v7": 7, "_t_v8": 8}, {"_t_v7": 855_000, "_t_v8": 300_000})
    if list(meta) != ["_t_v7"]:
        falhas.append(f"FRE encolhido: vigente deveria ser a v7, ficou {list(meta)}")

    if falhas:
        print("FALHAS:")
        for f in falhas:
            print(" -", f)
        return 1
    print("OK: os dois layouts do FRE viram seções; página fora da identidade; layout desconhecido não some; FRE inchado ou encolhido não substitui o anterior.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
