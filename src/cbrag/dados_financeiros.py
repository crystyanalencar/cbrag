"""Consulta a DRE estruturada da CVM (data/cvm_estruturado/dre.json, ver
scripts/baixar_dfp_itr.py) — usada pra responder pergunta de resultado
financeiro (lucro, prejuízo, receita, EBITDA...) com número exato e período
sem ambiguidade, em vez de depender do LLM ler tabela achatada de PDF.
"""
import json
from datetime import date
from pathlib import Path
from typing import Literal

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


# Faixa de dias entre início e fim do período que distingue trimestre
# isolado de acumulado (semestre/9 meses/ano) e ano fechado de acumulado
# parcial, no mesmo dataset ITR/DFP. `scripts/extrair_planilha_resultados.py`
# importa essas duas constantes em vez de reimplementar a faixa — mudar
# aqui já vale lá também.
_DIAS_TRIMESTRE = (80, 100)
_DIAS_ANO_FECHADO = (355, 370)


def _eh_trimestre_isolado(linha: dict) -> bool:
    """~90 dias entre início e fim do período — distingue trimestre isolado
    de acumulado (semestre/9 meses/ano), que também aparecem no mesmo
    dataset com o mesmo DT_FIM_EXERC."""
    inicio = date.fromisoformat(linha["DT_INI_EXERC"])
    fim = date.fromisoformat(linha["DT_FIM_EXERC"])
    return _DIAS_TRIMESTRE[0] <= (fim - inicio).days <= _DIAS_TRIMESTRE[1]


def _eh_ano_fechado(linha: dict) -> bool:
    """~365 dias entre início e fim do período — acumulado do ano inteiro,
    não trimestre isolado nem acumulado parcial (semestre/9 meses)."""
    inicio = date.fromisoformat(linha["DT_INI_EXERC"])
    fim = date.fromisoformat(linha["DT_FIM_EXERC"])
    return _DIAS_ANO_FECHADO[0] <= (fim - inicio).days <= _DIAS_ANO_FECHADO[1]


def _numero_trimestre(linha: dict) -> int:
    return (date.fromisoformat(linha["DT_INI_EXERC"]).month - 1) // 3 + 1


def contexto_resultado_financeiro(
    ano: int | None = None,
    periodo: Literal["trimestre", "ano"] = "trimestre",
    trimestre: int | None = None,
) -> str | None:
    """Texto pronto com as linhas-chave da DRE, período explícito, sem
    exigir do LLM nenhuma leitura de tabela ambígua.

    Sem `ano`: último trimestre isolado disponível (comportamento antigo).
    Com `ano`: trimestre isolado mais recente dentro desse ano
    (`periodo="trimestre"`, padrão) ou o ano fechado inteiro
    (`periodo="ano"`, ~365 dias) — CVM publica os dois pro mesmo ano, mesmo
    CD_CONTA, então é ambíguo sem esse parâmetro.

    `trimestre` (1 a 3) escolhe um trimestre específico e vale sobre
    `periodo`; sem `ano`, pega o mais recente que tenha aquele trimestre.
    O 4T isolado não existe no dataset (só embutido no acumulado do ano,
    ver `serie_resultado_financeiro`), então 4 devolve o aviso em vez de
    cair em outro trimestre sem dizer."""
    if trimestre is not None:
        if trimestre == 4:
            return (
                "O 4º trimestre isolado não existe no dataset da CVM (ITR cobre só "
                "1T/2T/3T; o 4T só aparece embutido no acumulado do ano, no DFP). "
                'Use periodo="ano" pro resultado do ano fechado.'
            )
        if trimestre not in (1, 2, 3):
            return f"Trimestre inválido: {trimestre}. Use 1, 2 ou 3."
        periodo = "trimestre"

    linhas = _ler_dre()
    if not linhas:
        return None

    eh_periodo = _eh_ano_fechado if periodo == "ano" else _eh_trimestre_isolado

    candidatas = [
        linha
        for linha in linhas
        if linha["ORDEM_EXERC"] == "ÚLTIMO"
        and eh_periodo(linha)
        and (ano is None or date.fromisoformat(linha["DT_FIM_EXERC"]).year == ano)
        and (trimestre is None or _numero_trimestre(linha) == trimestre)
    ]
    if not candidatas:
        return None

    fim_mais_recente = max(linha["DT_FIM_EXERC"] for linha in candidatas)
    do_trimestre = [l for l in candidatas if l["DT_FIM_EXERC"] == fim_mais_recente]

    por_conta = {l["CD_CONTA"]: l for l in do_trimestre}
    inicio = por_conta[next(iter(por_conta))]["DT_INI_EXERC"]

    if periodo == "ano":
        rotulo_periodo = "ano fechado"
    else:
        fim_ano = fim_mais_recente[2:4]
        rotulo_periodo = f"{_numero_trimestre(por_conta[next(iter(por_conta))])}T{fim_ano} (trimestre isolado)"
    linhas_texto = [
        f"DRE consolidada — {rotulo_periodo} de {inicio} a {fim_mais_recente} "
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


def serie_resultado_financeiro(
    ano_inicio: int | None = None,
    ano_fim: int | None = None,
    conta: str = "3.11",
) -> str | None:
    """Série histórica de trimestres isolados (não acumulados) de uma conta
    da DRE, ordenada cronologicamente — pra pergunta de tendência/comparação
    ao longo do tempo ("algum trimestre teve lucro entre X e Y", "como
    evoluiu a receita"), que `contexto_resultado_financeiro` (um ponto só)
    não responde. `conta` é o CD_CONTA (ver `CONTAS_RESUMO`), padrão
    Resultado Líquido. Sem `ano_inicio`/`ano_fim`: série completa disponível.

    Nota: a CVM não reporta o 4º trimestre isolado (ITR cobre só 1T/2T/3T;
    o 4T só aparece embutido no acumulado do ano no DFP) — ausência de "4T"
    na série é limitação do dataset, não erro."""
    linhas = _ler_dre()
    if not linhas:
        return None

    todas = [
        linha
        for linha in linhas
        if linha["CD_CONTA"] == conta
        and linha["ORDEM_EXERC"] == "ÚLTIMO"
        and _eh_trimestre_isolado(linha)
    ]
    if not todas:
        return None
    todas.sort(key=lambda l: l["DT_FIM_EXERC"])
    mais_recente = todas[-1]

    candidatas = todas
    if ano_inicio is not None:
        candidatas = [
            l for l in candidatas
            if date.fromisoformat(l["DT_FIM_EXERC"]).year >= ano_inicio
        ]
    if ano_fim is not None:
        candidatas = [
            l for l in candidatas
            if date.fromisoformat(l["DT_FIM_EXERC"]).year <= ano_fim
        ]
    if not candidatas:
        return None

    def _texto_linha(l: dict) -> str:
        inicio = date.fromisoformat(l["DT_INI_EXERC"])
        fim = date.fromisoformat(l["DT_FIM_EXERC"])
        trimestre_num = (inicio.month - 1) // 3 + 1
        milhoes = float(l["VL_CONTA"]) / 1000
        sinal = "-" if milhoes < 0 else ""
        texto_valor = f"{sinal}R$ {abs(milhoes):,.0f} milhões".replace(",", ".")
        return f"- {trimestre_num}T{fim.year % 100:02d}: {texto_valor}"

    rotulo_conta = CONTAS_RESUMO.get(conta, conta)
    linhas_texto = [
        f"Série trimestral isolada (não acumulada) de {rotulo_conta} "
        "(fonte: CVM, dataset estruturado ITR/DFP; 4º trimestre isolado não "
        "existe nesse dataset, só o acumulado do ano):"
    ]
    linhas_texto += [_texto_linha(l) for l in candidatas]

    # O intervalo pedido (ano_fim) pode deixar de fora dado mais recente que
    # o próprio LLM decidiu cortar por conta própria mesmo instruído a não
    # limitar (reforço só no prompt não bastou). Garantia no
    # código: tudo que é mais recente que o fim do intervalo pedido some
    # aqui embaixo, não só o último trimestre — senão criaria buraco (ex.
    # pedir até 2024 quando já tem 2025 e 2026 esconderia o ano inteiro de
    # 2025, não só o trimestre mais atual).
    if mais_recente not in candidatas:
        fim_pedido = candidatas[-1]["DT_FIM_EXERC"]
        excluidos_mais_recentes = [l for l in todas if l["DT_FIM_EXERC"] > fim_pedido]
        linhas_texto.append(
            "\n(Fora do intervalo pedido, mas são os dados mais recentes "
            "disponíveis — inclua na resposta se relevante, não esconda por "
            "causa do filtro pedido:)"
        )
        linhas_texto += [_texto_linha(l) for l in excluidos_mais_recentes]
    return "\n".join(linhas_texto)
