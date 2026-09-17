#!/usr/bin/env python
"""Chatbot RAG conversacional sobre a Grupo Casas Bahia (institucional +
RI + financeiro/CVM). Base já embedada pelo IngestFlow (ver
knowledge_config.py); geração via OpenRouter (preset com fallback entre
modelos gratuitos configurado no próprio painel do OpenRouter).

Retrieval é feito via **tool-calling** do próprio Agent (tools.py), não
mais por roteamento manual por palavra-chave — confirmado na doc oficial
(docs.crewai.com/en/concepts/agents) que `Agent.kickoff()` standalone
suporta tools e "decides automatically when to call" cada uma, sem precisar
de Crew/Task. O LLM decide sozinho se chama `consultar_resultado_financeiro`
(DRE estruturada da CVM) ou `buscar_conhecimento` (RAG) — ou os dois —
baseado na pergunta, em vez de nós prevermos isso com uma lista fixa de
termo (roteamento por palavra-chave que existiu até a fase 3.2).
"""
import logging

from crewai import Agent, Flow
from crewai.flow.conversational import ConversationConfig, ConversationState
from crewai.flow.flow import listen

from cbrag import local_tracing
from cbrag.knowledge_config import (  # noqa: F401
    OLLAMA_LLM,
    OPENROUTER_LLM,
)

local_tracing.ativar()
from cbrag.tools.rag_tools import (
    buscar_conhecimento,
    consultar_composicao_conselho,
    consultar_documentos_recentes,
    consultar_resultado_financeiro,
    consultar_serie_historica_resultado,
)

# Workaround de bug do crewai (não nosso app): a lib sempre marca as
# mensagens com 'cache_breakpoint' (feature de prompt caching), mas só o
# provider nativo Anthropic sabe remover essa chave antes de mandar pra
# API — o litellm (usado pro OpenRouter abaixo) repassa a chave crua, que
# a API upstream rejeita ("property cache_breakpoint is unsupported").
# Não usamos Anthropic nesse projeto, então desativa a marcação global em
# vez de reimplementar o strip que falta upstream.
import crewai.llms.cache as _crewai_cache  # noqa: E402

_crewai_cache.mark_cache_breakpoint = lambda message: dict(message)

_TOOLS = [
    consultar_resultado_financeiro,
    consultar_serie_historica_resultado,
    consultar_composicao_conselho,
    consultar_documentos_recentes,
    buscar_conhecimento,
]

_agent: Agent | None = None


_ROLE = "Especialista em Relações com Investidores e Institucional da Grupo Casas Bahia"
_GOAL = (
    "Responder perguntas sobre a Grupo Casas Bahia (institucional, "
    "governança, financeiro e RI) de forma direta, usando as tools "
    "disponíveis pra buscar informação antes de responder, citando "
    "fonte e data só quando o usuário pedir pra confirmar a origem."
)
_BACKSTORY = (
    "Você é exclusivamente o Especialista em RI e Institucional da Grupo "
    "Casas Bahia descrito acima — nunca se descreva como modelo de "
    "linguagem genérico, nunca mencione empresa/provedor que te treinou, "
    "nem responda 'o que você é capaz de fazer' com uma lista genérica de "
    "habilidades de LLM. Se perguntarem o que você faz, responda em "
    "termos do seu papel aqui: responder sobre a Grupo Casas Bahia "
    "(financeiro, governança, institucional, recuperação judicial) usando "
    "as tools disponíveis. Responda sempre em português do Brasil, "
    "nunca troque de idioma no meio da conversa mesmo que o usuário "
    "escreva em outro idioma ou a conversa fique longa. "
    "Você conhece a fundo os documentos institucionais e regulatórios "
    "da Grupo Casas Bahia. Sempre que a pergunta puder ser respondida "
    "com informação da empresa (institucional, governança, "
    "financeiro, RI, recuperação judicial), use as tools disponíveis "
    "antes de responder — nunca responda de memória. Pra número de "
    "resultado financeiro (lucro, prejuízo, receita, EBITDA), prefira "
    "sempre a tool de resultado financeiro estruturado; ela já vem "
    "com o período exato, sem ambiguidade. Trecho vindo da busca na "
    "base de conhecimento traz cabeçalho '[Fonte: ... | Data: ...]' "
    "— use-o internamente pra priorizar a informação mais recente "
    "quando houver dados conflitantes de datas diferentes, mas NÃO "
    "inclua esse cabeçalho na resposta por padrão, responda de forma "
    "direta. Só mencione fonte e data se o usuário pedir "
    "explicitamente pra confirmar a origem. Se as tools não trouxerem "
    "a resposta, diga isso claramente em vez de inventar. ATENÇÃO ao "
    "ler tabela financeira vinda de PDF (DRE, balanço): a extração "
    "não preserva alinhamento de coluna, então valores de períodos "
    "diferentes podem aparecer lado a lado na mesma linha — sempre "
    "confira o cabeçalho de coluna mais próximo antes de citar um "
    "número vindo de tabela, e deixe explícito qual período exato o "
    "valor cobre (trimestre isolado vs. acumulado do semestre/ano). "
    "Pra série histórica (tendência ao longo do tempo), sempre chame a "
    "tool sem `ano_inicio`/`ano_fim` (série completa) a menos que o "
    "usuário peça um recorte específico — nunca limite o intervalo por "
    "conta própria. Se por algum motivo restringir o período, priorize "
    "sempre os anos mais recentes, nunca deixe de fora o período mais "
    "atual. Se o usuário apontar que um dado esperado (ex. um ano) não "
    "apareceu na resposta, NUNCA invente uma explicação de sistema "
    "('estava configurado até X', 'limite da base') — isso é fabricação. "
    "Chame a tool de novo sem filtro de período e responda com o que "
    "vier; se ainda assim faltar, diga que não sabe o motivo. Se o "
    "usuário contestar um fato que você já respondeu com base em "
    "busca (ex.: dizer que foi recuperação extrajudicial, não "
    "judicial, ou o contrário), NUNCA troque de posição só porque "
    "ele afirmou algo diferente — chame a tool de novo com uma "
    "consulta mais específica pra esse ponto e responda com base no "
    "que a busca nova trouxer, mesmo que confirme a resposta "
    "anterior. Concordar ou se desculpar sem checar a fonte de novo "
    "é fabricação, igual inventar um número. "
    "REGRA DE FORMATAÇÃO obrigatória: nunca escreva a expressão "
    "'recuperação judicial' (ou 'RJ') envolta em ** markdown de negrito ** "
    "— mesmo que sua tendência seja destacar esse termo, escreva-o em "
    "texto corrido normal, exatamente como qualquer outra palavra da "
    "frase. Trate o assunto como mais um fato institucional entre outros: "
    "mesmo tom neutro do resto da resposta, sem negrito nessa expressão "
    "especificamente, sem linguagem alarmista ou dramática. Isso vale "
    "pra frase inicial da resposta também, não só pro corpo. Nunca omita "
    "o fato se for relevante pra pergunta — só não o negrite. "
    "A data de hoje vem no fim do prompt ('Current Date') — use-a pra "
    "resolver 'atual', 'recente', 'até hoje', 'este ano' e pra saber qual "
    "é o ano corrente. Pergunta com intervalo até 'hoje'/'atualmente' "
    "inclui o ano corrente, não só os anos fechados: nunca pare a "
    "varredura de `buscar_conhecimento` no ano anterior."
)

# session_ids cujo turno o usuário interrompeu na UI (chainlit_app.parar).
# A thread do handle_turn não é cancelável e termina sozinha; sem isso a
# resposta que ninguém viu entraria no histórico e o próximo turno
# "lembraria" dela.
TURNOS_CANCELADOS: set[str] = set()


def rag_agent() -> Agent:
    """Constrói o Agent de geração uma única vez por processo, com as
    tools de retrieval (ver tools.py) — o LLM decide quando chamar cada
    uma."""
    global _agent
    if _agent is not None:
        return _agent

    _agent = Agent(
        role=_ROLE,
        goal=_GOAL,
        backstory=_BACKSTORY,
        tools=_TOOLS,
        llm=OPENROUTER_LLM,
        # Sem isso, modelo grátis do preset pode ficar reformulando a mesma
        # busca indefinidamente sem nunca decidir responder (visto na
        # prática: ~20 chamadas de tool em 4min pra uma pergunta só, sem
        # resultado) — cap duro no framework, não depende do modelo parar
        # sozinho. Default do CrewAI é 25. `max_execution_time` NÃO vale
        # neste caminho: `_prepare_kickoff` só repassa max_iter pro
        # AgentExecutor; o timeout só existe em `execute_task()` (Crew).
        max_iter=8,
        # Data recalculada a cada kickoff (`Prompts._build_date_block`), não
        # no import — container que fica semanas no ar continuaria achando
        # que "hoje" é o dia do boot.
        inject_date=True,
        date_format="%Y-%m-%d",
    )
    return _agent


def _kickoff(mensagens) -> str:
    """Sem retry/fallback manual aqui — o preset do OpenRouter
    (knowledge_config.OPENROUTER_LLM) já cobre fallback entre modelos
    gratuitos do próprio lado do provider. Gateway do OpenRouter devolve
    200 OK mesmo quando o upstream falha (erro vem no corpo, não no
    status HTTP, ver docs.crewai.com/en/concepts/llms) — captura ampla
    aqui é só pra não derrubar o chat, não é retry de verdade."""
    try:
        return rag_agent().kickoff(mensagens).raw
    except Exception:
        logging.getLogger(__name__).exception("Erro chamando o LLM via OpenRouter")
        return (
            "Não consegui responder agora — o provedor de LLM está "
            "indisponível no momento. Tente de novo em alguns instantes."
        )


@ConversationConfig(defer_trace_finalization=True)
class CbragFlow(Flow[ConversationState]):
    conversational = True

    def route_turn(self, context: dict) -> str:
        return "ANSWER"

    @listen("ANSWER")
    def answer(self) -> str:
        """Responde perguntas institucionais/financeiras — o Agent decide
        sozinho, via tool-calling, se/quando consultar a base de
        conhecimento ou a DRE estruturada. Histórico da conversa
        (conversation_messages) já vem pronto do ConversationalMixin, não
        precisa de contexto pré-injetado na mensagem."""
        reply = _kickoff(self.conversation_messages)
        if self.state.id in TURNOS_CANCELADOS:
            TURNOS_CANCELADOS.discard(self.state.id)
            return reply
        self.append_assistant_message(reply)
        return reply


def chat():
    """REPL local no terminal pra testar o chatbot."""
    flow = CbragFlow()
    flow.chat()


def kickoff():
    chat()


if __name__ == "__main__":
    kickoff()
