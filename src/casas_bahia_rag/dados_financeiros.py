"""Consulta a DRE estruturada da CVM (data/cvm_estruturado/dre.json, ver
scripts/baixar_dfp_itr.py) — usada pra responder pergunta de resultado
financeiro (lucro, prejuízo, receita, EBITDA...) com número exato e período
sem ambiguidade, em vez de depender do LLM ler tabela achatada de PDF.
"""
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DRE_FILE = ROOT / "data" / "cvm_estruturado" / "dre.json"

# Contas mais relevantes da DRE consolidada padrão CVM (CD_CONTA), na ordem
# em que aparecem no demonstrativo — cobre o que geralmente é perguntado.
CONTAS_RESUMO = {
    "3.01": "Receita",
    "3.03": "Resultado Bruto",
    "3.05": "Resultado Antes do Resultado Financeiro e dos Tributos (EBIT)",
    "3.06": "Resultado Financeiro",
    "3.07": "Resultado Antes dos Tributos sobre o Lucro (LAIR)",
    "3.09": "Resultado Líquido das Operações Continuadas",
    "3.11": "Lucro/Prejuízo Consolidado do Período (Resultado Líquido)",
}


def _ler_dre() -> list[dict]:
    if not DRE_FILE.exists():
        return []
    return json.loads(DRE_FILE.read_text(encoding="utf-8"))


def _eh_trimestre_isolado(linha: dict) -> bool:
    """~90 dias entre início e fim do período — distingue trimestre isolado
    de acumulado (semestre/9 meses/ano), que também aparecem no mesmo
    dataset com o mesmo DT_FIM_EXERC."""
    inicio = date.fromisoformat(linha["DT_INI_EXERC"])
    fim = date.fromisoformat(linha["DT_FIM_EXERC"])
    return 80 <= (fim - inicio).days <= 100


def contexto_resultado_financeiro() -> str | None:
    """Texto pronto com as linhas-chave da DRE do último trimestre
    disponível, isolado (não acumulado) — período explícito, sem exigir do
    LLM nenhuma leitura de tabela ambígua."""
    linhas = _ler_dre()
    if not linhas:
        return None

    candidatas = [
        linha
        for linha in linhas
        if linha["ORDEM_EXERC"] == "ÚLTIMO" and _eh_trimestre_isolado(linha)
    ]
    if not candidatas:
        return None

    fim_mais_recente = max(linha["DT_FIM_EXERC"] for linha in candidatas)
    do_trimestre = [l for l in candidatas if l["DT_FIM_EXERC"] == fim_mais_recente]

    por_conta = {l["CD_CONTA"]: l for l in do_trimestre}
    inicio = por_conta[next(iter(por_conta))]["DT_INI_EXERC"]

    linhas_texto = [
        f"DRE consolidada — trimestre de {inicio} a {fim_mais_recente} "
        "(fonte: CVM, dataset estruturado ITR/DFP):"
    ]
    for codigo, rotulo in CONTAS_RESUMO.items():
        linha = por_conta.get(codigo)
        if linha:
            # VL_CONTA vem em milhares de reais (ESCALA_MOEDA=MIL, padrão
            # CVM) — dividir por 1000 pra bater com o "R$ Milhões" que os
            # releases em PDF usam.
            milhoes = float(linha["VL_CONTA"]) / 1000
            sinal = "-" if milhoes < 0 else ""
            texto_valor = f"{sinal}R$ {abs(milhoes):,.0f} milhões".replace(",", ".")
            linhas_texto.append(f"- {rotulo}: {texto_valor}")
    return "\n".join(linhas_texto)
