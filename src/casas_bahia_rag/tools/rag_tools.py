"""Tools do rag_agent (main.py) — o LLM decide sozinho quando chamar cada
uma (confirmado na doc oficial: docs.crewai.com/en/concepts/agents,
"the agent decides automatically when to call the tools"), substituindo o
roteamento por palavra-chave que existia antes da fase 3.2.
"""
from typing import Literal

from crewai.tools import tool

# Nomes das tools são snake_case curtos, iguais ao nome da função. O
# crewai sanitiza o nome dado no decorator pra function calling
# ("Buscar na base de conhecimento" virava
# `buscar_na_base_de_conhecimento`), e nome longo em português deu typo
# real do LLM em produção: chamou `buscar_na_base_de_conshcimento` →
# "Tool not found" (UNKNOWN_TOOL). Nome curto e sem preposição reduz a
# chance de o modelo reescrever errado.

from casas_bahia_rag.composicao_conselho import contexto_composicao_conselho
from casas_bahia_rag.dados_financeiros import (
    contexto_resultado_financeiro,
    serie_resultado_financeiro,
)
from casas_bahia_rag.knowledge_config import buscar_contexto


@tool("consultar_resultado_financeiro")
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


@tool("consultar_serie_historica_resultado")
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
    2024". NÃO use `consultar_resultado_financeiro` pra esse tipo de
    pergunta — ela só devolve um ponto (um trimestre ou ano por vez), não
    serve pra varrer um intervalo; e NÃO tente montar a série chamando
    `consultar_resultado_financeiro` várias vezes ano a ano, essa tool já
    devolve tudo de uma vez. `ano_inicio`/`ano_fim` opcionais — **omita os
    dois por padrão** (série completa disponível, 2021 em diante); não
    limite o intervalo por conta própria sem o usuário ter pedido um
    recorte específico. Se limitar, priorize sempre os anos mais recentes
    (nunca deixe de fora o período mais atual disponível)."""
    resultado = serie_resultado_financeiro(ano_inicio=ano_inicio, ano_fim=ano_fim)
    return resultado or "Nenhum dado estruturado de série histórica disponível pra esse período."


@tool("consultar_composicao_conselho")
def consultar_composicao_conselho(confirmar: bool = True) -> str:
    """Retorna a composição atual de Conselho de Administração, Diretoria e
    Conselho Fiscal da Grupo Casas Bahia, direto do Formulário de Referência
    (FRE) estruturado da CVM — sempre a versão mais recente arquivada, já
    refletindo renúncia/eleição recente. Use pra qualquer pergunta sobre
    quem são os membros do conselho, diretores, CEO/presidente ou conselho
    fiscal; não use `buscar_conhecimento` pra esse tipo de
    pergunta, atas de assembleia antigas podem trazer gente que já
    renunciou.

    `confirmar` é parâmetro dummy, ignore-o — não precisa passar nada.
    Existe só porque o Groq (fallback, ver STATE.md) rejeita em modo strict
    qualquer tool sem nenhum parâmetro (trata `properties: {}` como
    ausente, confirmado inspecionando o corpo HTTP real — bug do lado do
    provider, não do litellm/crewai)."""
    resultado = contexto_composicao_conselho()
    return resultado or "Nenhum dado estruturado de composição de conselho disponível."


@tool("buscar_conhecimento")
def buscar_conhecimento(consulta: str) -> str:
    """Busca trechos relevantes na base de conhecimento institucional da
    Grupo Casas Bahia (site institucional, RI, fatos relevantes, atas de
    assembleia/administração, comunicados ao mercado, demonstrações
    financeiras em PDF). Use pra estratégia, recuperação judicial, histórico
    da empresa, fechamento de lojas, mudanças de executivos ou qualquer coisa
    narrativa/qualitativa — não pra número de resultado financeiro (prefira
    `consultar_resultado_financeiro`) nem pra composição ATUAL de conselho/
    diretoria (prefira `consultar_composicao_conselho`).

    A busca é por TERMOS (léxica, estilo full-text sobre documentos formais
    da CVM), então `consulta` NÃO deve ser a frase do usuário copiada: monte
    uma consulta autônoma e rica em palavras-chave, no vocabulário que um
    documento oficial usaria — entidade, assunto, cargo, ano/mês quando
    houver. Nunca mande follow-up sem contexto ("tem certeza?", "e antes
    disso?"): reescreva incorporando o assunto da conversa. Prefira termo de
    documento a jargão: "diretor presidente" em vez de "CEO", "renúncia" e
    "eleição" em vez de "troca de comando", "fechamento de lojas" em vez de
    "reduziu operação". Exemplos: usuário "quem era o CEO antes do Renato
    Franklin?" -> consulta "renúncia diretor presidente eleição Renato
    Franklin"; usuário "tem certeza sobre o número de lojas?" -> consulta
    "fechamento de lojas deficitárias quantidade de lojas 2026". Se a
    primeira busca não trouxer a resposta, tente UMA reformulação com
    sinônimos formais antes de dizer que não achou.

    Pergunta que cobre VÁRIOS anos ou pede um total acumulado ("de 2024 até
    hoje", "desde X", "ao todo") NÃO deve virar uma consulta genérica só —
    o fato relevante de cada ano fica em documento diferente e uma busca só
    traz o ano com termo mais forte, perdendo os outros. Chame esta tool
    UMA VEZ POR ANO do intervalo, cada vez com o ano daquela chamada
    explícito na consulta (ex.: "fechamento de lojas deficitárias 2024",
    depois "fechamento de lojas deficitárias 2025", depois "...2026").
    "Até hoje"/"atualmente"/"recente" SEMPRE inclui o ano corrente (ver a
    data de hoje no seu backstory) — nunca pare a varredura no ano
    anterior só porque é o último ano fechado.

    NUNCA some os números que vierem de anos/documentos diferentes nem
    escolha só o maior pra apresentar como "o total": cada comunicado
    reporta a contagem à sua própria maneira (evento pontual, acumulado
    desde uma data-base diferente, fase específica do plano) e não são
    aditivos — somar ou escolher um deles como "o acumulado" é fabricar um
    número que não está em nenhum documento. Responda citando CADA número
    com o período/evento exato que o documento atribui a ele (ex.: "298
    lojas fechadas numa ação pontual em ago/2026" e, separadamente, "60
    lojas fechadas até o 2T24 desde o início do plano em 2023"), e diga
    explicitamente que a base não traz um total consolidado único pro
    intervalo pedido, se não trouxer."""
    chunks = buscar_contexto(consulta)
    if not chunks:
        return "Nada relevante encontrado na base de conhecimento pra essa pergunta."
    return "\n\n---\n\n".join(chunks)
