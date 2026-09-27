"""Consulta a composição de administradores estruturada da CVM
(data/cvm_estruturado/conselho.json, ver scripts/baixar_fre.py) — usada pra
responder pergunta de conselho/diretoria/conselho fiscal com a versão mais
recente arquivada, em vez de depender de busca vetorial sobre atas de
assembleia (que confunde membro atual com membro que já renunciou, ver
docs/retrieval.md).
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSELHO_FILE = ROOT / "data" / "cvm_estruturado" / "conselho.json"

# Orgao_Administracao (CVM) agrupado em rótulo de exibição.
GRUPOS_ORGAO = {
    "Conselho de Administração": "Pertence apenas ao Conselho de Administração",
    "Diretoria": "Pertence apenas à Diretoria",
    "Conselho Fiscal": "Conselho Fiscal",
}


def _ler_conselho() -> list[dict]:
    if not CONSELHO_FILE.exists():
        return []
    return json.loads(CONSELHO_FILE.read_text(encoding="utf-8"))


def contexto_composicao_conselho() -> str | None:
    """Texto pronto com a composição atual de conselho/diretoria/conselho
    fiscal, agrupada por órgão, na versão mais recente arquivada na CVM."""
    linhas = _ler_conselho()
    if not linhas:
        return None

    data_referencia = linhas[0]["Data_Referencia"]
    versao = linhas[0]["Versao"]
    blocos = [
        f"Composição de administradores da Grupo Casas Bahia — Formulário "
        f"de Referência CVM, versão {versao}, data de referência "
        f"{data_referencia} (fonte: CVM, dataset estruturado FRE):"
    ]
    for rotulo, valor_orgao in GRUPOS_ORGAO.items():
        do_grupo = [l for l in linhas if l["Orgao_Administracao"] == valor_orgao]
        if not do_grupo:
            continue
        blocos.append(f"\n{rotulo}:")
        for l in do_grupo:
            blocos.append(
                f"- {l['Nome']} — {l['Cargo_Eletivo_Ocupado']} "
                f"(última eleição/reeleição em {l['Data_Eleicao']}, posse "
                f"referente a essa eleição em {l['Data_Posse']} — NÃO é "
                f"necessariamente a data em que a pessoa assumiu o cargo "
                f"pela 1ª vez, pode ser reeleição de mandato anterior)"
            )
    return "\n".join(blocos)
