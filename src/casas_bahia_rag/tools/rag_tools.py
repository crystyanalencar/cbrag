"""Tools do rag_agent (main.py) — o LLM decide sozinho quando chamar cada
uma (confirmado na doc oficial: docs.crewai.com/en/concepts/agents,
"the agent decides automatically when to call the tools"), substituindo o
roteamento por palavra-chave que existia antes (knowledge_config.
grupo_da_pergunta, ainda disponível como utilitário, não é mais chamada
daqui).
"""
from typing import Literal

from crewai.tools import tool

from casas_bahia_rag.composicao_conselho import contexto_composicao_conselho
from casas_bahia_rag.dados_financeiros import (
    contexto_resultado_financeiro,
    serie_resultado_financeiro,
)
from casas_bahia_rag.knowledge_config import buscar_contexto


@tool("Consultar resultado financeiro")
def consultar_resultado_financeiro(
    ano: int | None = None,
    periodo: Literal["trimestre", "ano"] = "trimestre",
) -> str:
    """Retorna a DRE (demonstração de resultado) consolidada da Grupo Casas
    Bahia, direto do dataset estruturado da CVM (ITR/DFP) — Receita,
    Resultado Bruto, EBIT, Resultado Financeiro, LAIR e Resultado Líquido,
    com período exato. Use pra qualquer pergunta sobre lucro, prejuízo,
    receita, EBITDA, margem ou resultado financeiro em geral — é a fonte
    mais confiável pra número.

    Não precisa saber o ano de antemão — deixe `ano=None` (padrão) e ajuste
    só `periodo`: `periodo="trimestre"` (padrão) devolve o último trimestre
    isolado disponível; `periodo="ano"` sem `ano` devolve o último ano
    fechado disponível (não o ano corrente, que ainda não fechou). Informe
    `ano` só se a pergunta citar um ano específico, no passado (ex.:
    "resultado de 2023"). Combine com `periodo="ano"` quando a pergunta
    pedir claramente o ano fechado/inteiro (ex.: "resultado de 2023 no ano
    todo"); use "trimestre" (padrão) se mencionar um trimestre específico ou
    não deixar claro."""
    resultado = contexto_resultado_financeiro(ano=ano, periodo=periodo)
    return resultado or "Nenhum dado estruturado de resultado financeiro disponível pra esse período."


@tool("Consultar série histórica de resultado financeiro")
def consultar_serie_historica_resultado(
    ano_inicio: int | None = None,
    ano_fim: int | None = None,
) -> str:
    """Retorna a série completa de trimestres isolados (Resultado Líquido —
    lucro/prejuízo) da Grupo Casas Bahia, um valor por trimestre, ordenada
    no tempo — direto do dataset estruturado da CVM. Use pra qualquer
    pergunta de tendência ou comparação ao longo de um período, NÃO de um
    trimestre/ano isolado: "algum trimestre teve lucro entre X e Y", "como
    evoluiu o resultado", "desde quando dá prejuízo", "compare 2022 com
    2024". NÃO use "Consultar resultado financeiro" pra esse tipo de
    pergunta — ela só devolve um ponto (um trimestre ou ano por vez), não
    serve pra varrer um intervalo; e NÃO tente montar a série chamando
    "Consultar resultado financeiro" várias vezes ano a ano, essa tool já
    devolve tudo de uma vez. `ano_inicio`/`ano_fim` opcionais — **omita os
    dois por padrão** (série completa disponível, 2021 em diante); não
    limite o intervalo por conta própria sem o usuário ter pedido um
    recorte específico. Se limitar, priorize sempre os anos mais recentes
    (nunca deixe de fora o período mais atual disponível)."""
    resultado = serie_resultado_financeiro(ano_inicio=ano_inicio, ano_fim=ano_fim)
    return resultado or "Nenhum dado estruturado de série histórica disponível pra esse período."


@tool("Consultar composição do conselho e diretoria")
def consultar_composicao_conselho(confirmar: bool = True) -> str:
    """Retorna a composição atual de Conselho de Administração, Diretoria e
    Conselho Fiscal da Grupo Casas Bahia, direto do Formulário de Referência
    (FRE) estruturado da CVM — sempre a versão mais recente arquivada, já
    refletindo renúncia/eleição recente. Use pra qualquer pergunta sobre
    quem são os membros do conselho, diretores, CEO/presidente ou conselho
    fiscal; não use a busca na base de conhecimento pra esse tipo de
    pergunta, atas de assembleia antigas podem trazer gente que já
    renunciou.

    `confirmar` é parâmetro dummy, ignore-o — não precisa passar nada.
    Existe só porque o Groq (fallback, ver STATE.md) rejeita em modo strict
    qualquer tool sem nenhum parâmetro (trata `properties: {}` como
    ausente, confirmado inspecionando o corpo HTTP real — bug do lado do
    provider, não do litellm/crewai)."""
    resultado = contexto_composicao_conselho()
    return resultado or "Nenhum dado estruturado de composição de conselho disponível."


@tool("Buscar na base de conhecimento")
def buscar_conhecimento(pergunta: str) -> str:
    """Busca trechos relevantes na base de conhecimento institucional da
    Grupo Casas Bahia (site institucional, RI, fatos relevantes, atas de
    assembleia/administração, demonstrações financeiras em PDF). Use pra
    estratégia, recuperação judicial, histórico da empresa ou qualquer coisa
    narrativa/qualitativa — não pra número de resultado financeiro (prefira
    "Consultar resultado financeiro") nem pra composição de conselho/
    diretoria (prefira "Consultar composição do conselho e diretoria")."""
    chunks = buscar_contexto(pergunta)
    if not chunks:
        return "Nada relevante encontrado na base de conhecimento pra essa pergunta."
    return "\n\n---\n\n".join(chunks)
