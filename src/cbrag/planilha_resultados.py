"""Consulta os indicadores da "Planilha de Resultados" da Central de Downloads
(data/cvm_estruturado/planilha_resultados.json, ver
scripts/extrair_planilha_resultados.py e docs/ingestao.md) — o que o dataset
ITR/DFP da CVM não tem: GMV, EBITDA ajustado, movimentação de lojas,
covenants, carteira do crediário e capex. Lucro e receita contábil seguem em
`dados_financeiros.py`, que é a fonte oficial.

Todo acesso ao arquivo passa por este módulo: se o dado migrar de JSON pra
outro formato, a tool não muda.
"""
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLANILHA_FILE = ROOT / "data" / "cvm_estruturado" / "planilha_resultados.json"
REVISOES_FILE = ROOT / "data" / "cvm_estruturado" / "planilha_revisoes.json"

# assunto -> (aba da planilha, prefixo de métrica ou None pra aba inteira,
# rótulo de exibição). Métrica com prefixo: GMV e EBITDA moram na aba DRE,
# junto com receita e lucro, que a CVM já cobre.
ASSUNTOS = {
    "lojas": ("Lojas", None, "Lojas (movimentação, total e área)"),
    "covenants": ("Covenants", None, "Covenants e endividamento"),
    "crediario": ("Crediário", None, "Carteira do crediário (CDCI)"),
    "capex": ("Capex", None, "Capex"),
    "gmv": ("DRE", "GMV", "GMV"),
    "ebitda": ("DRE", "EBITDA", "EBITDA"),
    "fluxo de caixa gerencial": ("FC gerencial", None, "Fluxo de caixa gerencial"),
    "resultado financeiro": ("Res. Financeiro", None, "Detalhe do resultado financeiro"),
}

# Outras grafias que o LLM costuma usar. A busca é tolerante (parâmetro
# `str`, não `Literal`), pelo mesmo motivo de `documentos_recentes.py`.
SINONIMOS = {
    "loja": "lojas",
    "fechamento de lojas": "lojas",
    "movimentacao de lojas": "lojas",
    "covenant": "covenants",
    "alavancagem": "covenants",
    "divida liquida": "covenants",
    "carteira": "crediario",
    "inadimplencia": "crediario",
    "cdci": "crediario",
    "investimentos": "capex",
    "gmv total": "gmv",
    "ebitda ajustado": "ebitda",
    "fluxo de caixa": "fluxo de caixa gerencial",
    "despesas financeiras": "resultado financeiro",
}

TRIMESTRES_PADRAO = 4
TRIMESTRES_MAX = 12


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento).casefold().strip()


def resolver_assunto(pedido: str) -> str | None:
    alvo = _normalizar(pedido)
    if not alvo:
        return None
    if alvo in ASSUNTOS:
        return alvo
    if alvo in SINONIMOS:
        return SINONIMOS[alvo]
    for chave in list(ASSUNTOS) + list(SINONIMOS):
        if chave in alvo or (len(alvo) >= 4 and alvo in chave):
            return SINONIMOS.get(chave, chave)
    return None


@lru_cache(maxsize=2)
def _ler(caminho: str, mtime: float) -> list[dict]:
    # `mtime` só existe pra invalidar o cache quando a ingestão troca o arquivo.
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def _registros() -> list[dict]:
    if not PLANILHA_FILE.exists():
        return []
    return _ler(str(PLANILHA_FILE), PLANILHA_FILE.stat().st_mtime)


def _revisoes() -> list[dict]:
    if not REVISOES_FILE.exists():
        return []
    return _ler(str(REVISOES_FILE), REVISOES_FILE.stat().st_mtime)


def _ordem_periodo(r: dict) -> tuple:
    return (r["ano"], r["trimestre"] or 5)  # anual depois do 4T do mesmo ano


def _formatar(valor: float, unidade: str) -> str:
    if unidade == "%":
        return f"{valor * 100:.1f}%"
    if unidade == "x":
        return f"{valor:.1f}x"
    texto = f"{valor:,.0f}" if float(valor).is_integer() or abs(valor) >= 100 else f"{valor:,.1f}"
    texto = texto.replace(",", ".")
    if unidade == "R$ milhões":
        return f"R$ {texto} mi"
    if unidade == "mil m²":
        return f"{texto} mil m²"
    return texto


def ultimo_periodo() -> str | None:
    """Rótulo do trimestre mais recente com dado (ex.: '2T26'), pra
    `resumo_cobertura_da_base`."""
    trimestres = [r for r in _registros() if r["tipo_periodo"] == "trimestre"]
    if not trimestres:
        return None
    return max(trimestres, key=_ordem_periodo)["periodo"]


def contexto_indicadores(
    assunto: str,
    ano: int | None = None,
    trimestre: int | None = None,
    quantidade_trimestres: int = TRIMESTRES_PADRAO,
) -> str | None:
    """Texto pronto: as métricas do assunto por período, com unidade
    explícita. Sem `ano`, os últimos `quantidade_trimestres` trimestres;
    com `ano`, os trimestres daquele ano (mais a coluna anual, se existir);
    com `ano` e `trimestre`, só aquele. `None` se o assunto não existe ou
    não há dado no recorte."""
    chave = resolver_assunto(assunto)
    if chave is None:
        return None
    aba, prefixo, rotulo = ASSUNTOS[chave]
    registros = [
        r for r in _registros()
        if r["aba"] == aba and (prefixo is None or r["metrica_pt"].startswith(prefixo))
    ]
    if not registros:
        return None

    trimestres = sorted(
        {(r["ano"], r["trimestre"]) for r in registros if r["tipo_periodo"] == "trimestre"}
    )
    if ano is not None:
        escolhidos = {t for t in trimestres if t[0] == ano and (trimestre is None or t[1] == trimestre)}
        incluir_ano = trimestre is None
    else:
        quantidade = max(1, min(quantidade_trimestres, TRIMESTRES_MAX))
        escolhidos = set(trimestres[-quantidade:])
        incluir_ano = False
    selecionados = [
        r for r in registros
        if (r["tipo_periodo"] == "trimestre" and (r["ano"], r["trimestre"]) in escolhidos)
        or (r["tipo_periodo"] == "ano" and incluir_ano and r["ano"] == ano)
    ]
    if not selecionados:
        return None

    versao = max(r["versao"] for r in registros)
    periodos = sorted({(r["ano"], r["trimestre"] or 5, r["periodo"]) for r in selecionados})
    ordem = [p[2] for p in periodos]

    por_metrica: dict[tuple, dict] = {}
    # Ordem da planilha: os registros já vêm linha a linha da aba.
    for r in selecionados:
        chave_m = (r["grupo"], r["metrica_pt"], r["ocorrencia"], r["unidade"])
        por_metrica.setdefault(chave_m, {})[r["periodo"]] = r["valor"]

    ultimo = ultimo_periodo()
    linhas = [
        f"{rotulo} — Planilha de Resultados do RI (Central de Downloads), versão de "
        f"{versao}; último trimestre divulgado: {ultimo}. Períodos: {', '.join(ordem)}."
    ]
    for (grupo, metrica, _, unidade), valores in por_metrica.items():
        nome = f"{grupo} > {metrica}" if grupo else metrica
        pares = "; ".join(f"{p} {_formatar(valores[p], unidade)}" for p in ordem if p in valores)
        linhas.append(f"- {nome.strip()}: {pares}")

    revisados = _periodos_revisados(aba, {p for p in ordem})
    if revisados:
        linhas.append(
            "\nAtenção: valores destes períodos foram revisados entre versões da "
            f"planilha (a versão acima é a mais recente): {', '.join(sorted(revisados))}."
        )
    return "\n".join(linhas)


def _periodos_revisados(aba: str, periodos: set[str]) -> set[str]:
    return {rev["periodo"] for rev in _revisoes() if rev["aba"] == aba and rev["periodo"] in periodos}
