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

from cbrag.composicao_conselho import contexto_composicao_conselho
from cbrag.dados_financeiros import (
    contexto_resultado_financeiro,
    serie_resultado_financeiro,
)
from cbrag.documentos_recentes import CATEGORIAS, listar_documentos_recentes, resumo_cobertura_da_base
from cbrag.knowledge_config import buscar_contexto
from cbrag.planilha_resultados import ASSUNTOS as ASSUNTOS_INDICADORES
from cbrag.planilha_resultados import contexto_indicadores


@tool("consultar_resultado_financeiro")
def consultar_resultado_financeiro(
    ano: int | None = None,
    periodo: Literal["trimestre", "ano"] = "trimestre",
    trimestre: int | None = None,
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
    não deixar claro.

    `trimestre` (1, 2 ou 3) escolhe um trimestre específico: "resultado do
    1º trimestre de 2026" -> `ano=2026, trimestre=1`. Sem ele vem sempre o
    ÚLTIMO trimestre do período, que NÃO é o 1T quando o 2T já saiu. Se a
    pergunta citar "1T", "2T", "3T" ou "primeiro/segundo/terceiro
    trimestre", informe `trimestre`. O 4T isolado não existe no dataset
    (só o acumulado do ano, use `periodo="ano"`)."""
    resultado = contexto_resultado_financeiro(ano=ano, periodo=periodo, trimestre=trimestre)
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

    ATENÇÃO ao ler as datas `eleito em`/`posse em`: é a data da ÚLTIMA
    eleição/reeleição registrada no FRE, não a data em que a pessoa
    assumiu o cargo pela primeira vez — FRE é reenviado a cada assembleia,
    e reeleição (comum, mandato costuma ser anual) atualiza essa data sem
    a pessoa ter saído do cargo. Pra pergunta sobre quando alguém ASSUMIU/
    ENTROU pela primeira vez num cargo (ex. "quando o Renato Franklin virou
    CEO"), essa data NÃO é a resposta — use `buscar_conhecimento` (fato
    relevante/ata da eleição original) pra isso, e se as duas fontes
    trouxerem datas diferentes pra "quando entrou", explique a diferença
    (posse original vs. última reeleição) em vez de escolher uma só.

    `confirmar` é parâmetro dummy, ignore-o — não precisa passar nada.
    Existe porque o Groq (usado antes como fallback) rejeitava
    em modo strict qualquer tool sem nenhum parâmetro (tratava
    `properties: {}` como ausente, confirmado inspecionando o corpo HTTP
    real — bug do lado do provider, não do litellm/crewai). Mantido mesmo
    depois da troca pro preset OpenRouter (fase 4) porque o preset pode
    rotear pra modelos com a mesma limitação — sem custo manter."""
    resultado = contexto_composicao_conselho()
    return resultado or "Nenhum dado estruturado de composição de conselho disponível."


@tool("consultar_documentos_recentes")
def consultar_documentos_recentes(
    categoria: str,
    quantidade: int = 5,
    ano: int | None = None,
) -> str:
    """Lista os documentos MAIS RECENTES de uma categoria de arquivamento da
    Grupo Casas Bahia na CVM, ordenados pela data de entrega (mais recente
    primeiro), com trecho do conteúdo dos primeiros — lista determinística
    lida da metadata, SEM busca por termo. Use pra qualquer pergunta de
    ordem no tempo: "último/mais recente X", "últimos N X", "o que saiu
    recentemente", "quais X de <ano>" (passe `ano`), "o que a empresa
    divulgou sobre a recuperação judicial" (categoria de RJ).

    `categoria` aceita, com ou sem acento/plural: "Fato Relevante",
    "Comunicado ao Mercado", "Assembleia" (atas, editais, mapas de votação),
    "Reunião da Administração" (atas de conselho/diretoria), "Aviso aos
    Acionistas", "Dados Econômico-Financeiros" (releases e DFs),
    "Informações de Companhias em Recuperação Judicial ou Extrajudicial"
    (petições e sentenças da RJ), "Calendário de Eventos Corporativos".

    NUNCA use `buscar_conhecimento` pra descobrir QUAL é o mais recente —
    ela busca por termo e não sabe ordenar por data, devolve documento
    antigo com o termo forte. Fluxo certo: esta tool primeiro (diz qual é e
    a data, e o trecho costuma bastar); só se precisar de detalhe além do
    trecho, aí `buscar_conhecimento` com o assunto exato + data. Se a
    resposta desta tool já responde a pergunta, responda direto — não
    confirme com outras buscas. Cobre só arquivamentos na CVM (não o site
    institucional)."""
    resultado = listar_documentos_recentes(categoria, quantidade=quantidade, ano=ano)
    if resultado:
        return resultado
    validas = "; ".join(CATEGORIAS)
    return (
        f"Nenhum documento encontrado pra categoria '{categoria}'"
        + (f" em {ano}" if ano is not None else "")
        + f". Categorias válidas: {validas}."
    )


@tool("consultar_indicadores_operacionais")
def consultar_indicadores_operacionais(
    assunto: str,
    ano: int | None = None,
    trimestre: int | None = None,
    quantidade_trimestres: int = 4,
) -> str:
    """Indicadores da Planilha de Resultados que a companhia publica no RI,
    por trimestre, com unidade explícita: lojas (abertas, fechadas,
    convertidas por bandeira, total de lojas, área de vendas e centros de
    distribuição), covenants (dívida líquida, saldo do crediário, covenant
    da dívida e seu limite), crediário (carteira, vencidos por faixa de
    atraso, inadimplência acima de 90 dias), capex (por destino), GMV
    (bruto e líquido, por canal), EBITDA e EBITDA ajustado, fluxo de caixa
    gerencial e detalhe do resultado financeiro. Use pra "quantas lojas
    fechou no 2T26?", "qual o covenant?", "inadimplência do crediário",
    "capex do ano", "GMV do trimestre", "EBITDA ajustado".

    `assunto` aceita, com ou sem acento: "lojas", "covenants", "crediario",
    "capex", "gmv", "ebitda", "fluxo de caixa gerencial", "resultado
    financeiro". Sem `ano`, devolve os últimos `quantidade_trimestres`
    (padrão 4, máx. 12); com `ano`, os trimestres daquele ano (mais o total
    anual quando existe); com `ano` e `trimestre`, só aquele.

    NÃO use pra receita, lucro ou resultado contábil (é
    `consultar_resultado_financeiro`, dataset oficial da CVM, e prevalece se
    os dois divergirem). Não substitui `buscar_conhecimento` pra o porquê de
    um número: esta tool só traz o número."""
    resultado = contexto_indicadores(
        assunto, ano=ano, trimestre=trimestre, quantidade_trimestres=quantidade_trimestres
    )
    if resultado:
        return resultado
    validos = "; ".join(ASSUNTOS_INDICADORES)
    return (
        f"Nenhum indicador encontrado pra '{assunto}'"
        + (f" em {ano}" if ano is not None else "")
        + (f", trimestre {trimestre}" if trimestre is not None else "")
        + f". Assuntos válidos: {validos}."
    )


@tool("consultar_cobertura_da_base")
def consultar_cobertura_da_base(quantidade: int = 10) -> str:
    """Diz até quando vai cada fonte da base (documentos e resultado
    financeiro) e lista os documentos mais recentes de QUALQUER categoria.
    Use quando a pergunta for sobre a própria base, sem assunto ou categoria:
    "qual a data mais recente de dados?", "até quando vão as informações?",
    "o que foi divulgado/adicionado recentemente?", "qual o documento mais
    novo?". Se a pergunta citar uma categoria ("último fato relevante"), use
    `consultar_documentos_recentes`. Responda com a data mais recente entre
    as fontes, dizendo de qual fonte é cada data (documento vs. DRE), sem
    escolher só a DRE."""
    return resumo_cobertura_da_base(quantidade=quantidade)


@tool("buscar_conhecimento")
def buscar_conhecimento(consulta: str) -> str:
    """Busca trechos relevantes na base de conhecimento institucional da
    Grupo Casas Bahia (site institucional, RI, fatos relevantes, atas de
    assembleia/administração, comunicados ao mercado, demonstrações
    financeiras em PDF). Use pra estratégia, recuperação judicial, histórico
    da empresa, fechamento de lojas, mudanças de executivos ou qualquer coisa
    narrativa/qualitativa — não pra número de resultado financeiro (prefira
    `consultar_resultado_financeiro`), nem pra composição ATUAL de conselho/
    diretoria (prefira `consultar_composicao_conselho`), nem pra "qual o
    último/mais recente X" ou "quais X de tal ano" (prefira
    `consultar_documentos_recentes` — esta busca é por termo e NÃO sabe
    ordenar por data; reformular a consulta não resolve isso).

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
