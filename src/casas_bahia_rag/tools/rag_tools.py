"""Tools do rag_agent (main.py) — o LLM decide sozinho quando chamar cada
uma (confirmado na doc oficial: docs.crewai.com/en/concepts/agents,
"the agent decides automatically when to call the tools"), substituindo o
roteamento por palavra-chave que existia antes (knowledge_config.
grupo_da_pergunta, ainda disponível como utilitário, não é mais chamada
daqui).
"""
from crewai.tools import tool

from casas_bahia_rag.composicao_conselho import contexto_composicao_conselho
from casas_bahia_rag.dados_financeiros import contexto_resultado_financeiro
from casas_bahia_rag.knowledge_config import buscar_contexto


@tool("Consultar resultado financeiro")
def consultar_resultado_financeiro() -> str:
    """Retorna a DRE (demonstração de resultado) consolidada mais recente
    da Grupo Casas Bahia, direto do dataset estruturado da CVM (ITR/DFP) —
    Receita, Resultado Bruto, EBIT, Resultado Financeiro, LAIR e Resultado
    Líquido do último trimestre isolado, com período exato. Use pra
    qualquer pergunta sobre lucro, prejuízo, receita, EBITDA, margem ou
    resultado financeiro em geral — é a fonte mais confiável pra número,
    não precisa de outro argumento."""
    resultado = contexto_resultado_financeiro()
    return resultado or "Nenhum dado estruturado de resultado financeiro disponível."


@tool("Consultar composição do conselho e diretoria")
def consultar_composicao_conselho() -> str:
    """Retorna a composição atual de Conselho de Administração, Diretoria e
    Conselho Fiscal da Grupo Casas Bahia, direto do Formulário de Referência
    (FRE) estruturado da CVM — sempre a versão mais recente arquivada, já
    refletindo renúncia/eleição recente. Use pra qualquer pergunta sobre
    quem são os membros do conselho, diretores, CEO/presidente ou conselho
    fiscal; não use a busca na base de conhecimento pra esse tipo de
    pergunta, atas de assembleia antigas podem trazer gente que já
    renunciou."""
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
