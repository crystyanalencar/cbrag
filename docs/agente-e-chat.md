# Agente, tools e interface de chat

Referência durável de como o agent se comporta e por quê. Sem status — o que
está aplicado ou pendente fica no `STATE.md`. A escolha de ferramenta por
tipo de pergunta está em `retrieval.md`.

## Arquitetura

Flow conversacional do CrewAI (`conversational = True`, `handle_turn`) com
**um** `Agent` chamado por `Agent.kickoff()` e tools; sem Crew nem Task. O
histórico é do próprio Flow, por `session_id`, e cada sessão de navegador
tem Flow próprio (usuários simultâneos funcionam). Desvios conscientes da
doc do CrewAI: instruções no `backstory` (a doc quer na task; sem Task aqui,
e instrução perto da tool funcionou melhor), 5 tools (limite recomendado),
import `crewai.flow.conversational`.

Tools: `consultar_resultado_financeiro`, `consultar_serie_historica_resultado`,
`consultar_composicao_conselho`, `consultar_documentos_recentes`,
`buscar_conhecimento`. A escolha entre elas é do LLM; **a docstring é o
roteador**, e diz também quando NÃO usar a concorrente.

## Prompt não é garantia; código é

Regra que se provou várias vezes: instrução em prompt/docstring reduz o
erro, não o elimina. O que precisa valer sempre vai pro código.

- **Fabricação de explicação.** Perguntado "desde quando há resultado
  negativo", o LLM chamou a série com `ano_fim=2024` por conta própria; ao
  ser questionado por que faltava 2025, inventou que "o sistema cobre só até
  2024". A instrução explícita no prompt não impediu (confirmado nos args
  reais da chamada seguinte). Fix estrutural em `serie_resultado_financeiro`:
  se `ano_fim` esconde dado mais recente, a função anexa todos os trimestres
  posteriores com nota "fora do intervalo pedido, mas dado mais recente".
- **Loop de reformulação.** Modelo instável repetia a mesma busca 15-20
  vezes. Proteção estrutural: `Agent(max_iter=8)`. `max_execution_time` é
  **inerte** em `Agent.kickoff()` (`_prepare_kickoff` só repassa `max_iter`;
  o timeout só existe em `execute_task()`/Crew), então não dá falsa
  segurança.
- **Data de hoje**: `inject_date=True` (recalculado por chamada). Um
  `date.today()` no f-string do backstory ficava fixo no import — container
  no ar por semanas ficaria com "hoje" velho. Sem a data, "até hoje" era
  ambíguo e a varredura ano a ano parava em 2025.
- **Data de reeleição**: `contexto_composicao_conselho()` avisa no próprio
  texto retornado que `eleito em`/`posse em` é a última reeleição (o modelo
  ignorava o aviso só na docstring).
- **Nome de tool**: snake_case curto igual ao nome da função (ver
  `busca-hibrida.md`).
- **Parâmetro de tool tolerante, não estrito**: categoria é `str`
  normalizada, não `Literal`, porque `Literal` faz o pydantic rejeitar antes
  do código rodar e gasta uma iteração. Tool sem parâmetro nenhum é rejeitada
  por provider em modo `strict` (`properties: {}` tratado como ausente):
  `consultar_composicao_conselho` tem o parâmetro dummy `confirmar: bool`
  (sem underscore; com underscore o pydantic o trata como atributo privado e
  o campo some do schema).

## Comportamento de resposta

- Pergunta agregada multi-ano: uma busca **por ano** com o ano explícito,
  nunca uma só (BM25 empata termo genérico entre anos).
- **Contagens de fechamento de loja não se somam.** São bases diferentes que
  a própria empresa nunca soma: fechamento líquido rolling de 12 meses,
  evento pontual da fase 2 pós-RJ (298 lojas, comunicado de 18/08/2026) e
  acumulado da fase 1 2023-2024. Resposta cita cada número com seu
  período/evento e avisa que não há total consolidado. Extrair série
  trimestral pra somar foi avaliado e descartado (mesmo esforço da DRE, valor
  baixo).
- Não trocar de posição só porque o usuário contestou: rebuscar antes.
- Citar fonte/data só quando o usuário pede (citar sempre irritava).
- Não se apresentar como "modelo de linguagem treinado pelo Google" e travar
  em pt-BR (modelo do preset trocou pra espanhol no meio de uma conversa).
- O cabeçalho do chunk não expõe o slug do arquivo (o LLM repete o que lê).

## Provider de LLM

Geração via OpenRouter com **preset** (`openrouter/@preset/free-tier-first`),
controlado no painel — modelo, ordem, reasoning e limite de gasto ficam do
lado do provider, não no código (decisão do usuário). Consequência: o modelo
muda por chamada; comportamento errático (identidade genérica, troca de
idioma, loop) é tratado com backstory, `max_iter` e tools determinísticas,
não trocando modelo. Modelo só-grátis falhou em conversa longa (118k tokens
acumulados): ignorou o resultado de tool e fabricou data de eleição e um
diretor. Dois grátis também falharam juntos num pico (429 e 504).

Histórico que explica o código atual: Ollama local → Gemini com fallback
Groq (retry, detecção de 429/503) → OpenRouter. Toda a lógica de fallback e
retry manual foi removida; `_kickoff()` só captura exceção pra não derrubar
o chat. Bug conhecido do `crewai` na época do Groq (litellm): a lib marca
mensagens com `cache_breakpoint` e só o provider Anthropic sabe removê-lo.

## Desempenho

O custo é round-trip de LLM, não retrieval: pergunta multi-ano dispara várias
`buscar_conhecimento` em sequência, cada uma um round-trip completo com
reasoning (480-1500 tokens por chamada), e o histórico acumulado é
reprocessado a cada resposta (chegou a 118k tokens). Medido: tool
estruturada local 11 ms; Qdrant ~1.2s por busca (21s na 1ª chamada do
processo, em modo embedded).

## Interface (Chainlit)

- Chamada ao LLM em thread (`cl.make_async`): sem isso o event loop trava e o
  navegador acha que perdeu conexão.
- **`ChatProfile` com ícone+descrição é exclusivo da logo**: o frontend mostra
  um OU outro na tela inicial.
- Botão "Sobre" = `chainlit.md` via `.chainlit/translations/pt-BR.json`
  (cópia do pt-PT com "Leia-me"→"Sobre"; sem pt-BR caía em inglês).
- Loader animado por Custom Element (`public/elements/BrandLoader.jsx`,
  transpilado no navegador — feature oficial).
- **Stop manual**: `asyncio.CancelledError` é `BaseException`, o
  `except Exception` do `responder()` não pega; `@cl.on_stop` remove o loader.
  A thread do `handle_turn` não é cancelável e seguia até o fim, então
  `main.TURNOS_CANCELADOS` (set de `session_id`) evita gravar no histórico
  resposta que ninguém viu. A mensagem "Task manually stopped." é literal em
  `chainlit/socket.py`: o handler `stop` está sobrescrito em
  `chainlit_app.py` (o `python-socketio` fica com o último `@sio.on`).
  **Armadilha**: se o Chainlit mudar essa lógica num upgrade, a réplica
  desatualiza em silêncio.
- `on_chat_end` pode disparar pra sessão em que `on_chat_start` nunca rodou
  (troca de rede, recarga no celular): `encerrar()` e `responder()` criam o
  Flow se faltar.
- **Marca**: logo/avatar/favicon eram o logo oficial da Grupo Casas Bahia
  (risco de marca registrada). Trocados por ícone SVG original; disclaimer de
  não-afiliação em `chainlit.md` e no rodapé da landing. `logo_*`,
  `favicon.*` e `avatars/{nome}.*` são resolvidos por glob, extensão livre.
- Modo escuro automático (`prefers-color-scheme`) foi removido do site
  estático (parecia "muito escuro" com o SO em dark); toggle manual com
  `localStorage`.
- Cor de marca: usar fonte confiável (`theme-color` do `<meta>` do site
  institucional, `#0033C6`; acento `#e71a3b`), nunca hex tirado de grep bruto
  (o laranja veio de selo promocional).
- `allow_origins` restrito ao domínio de produção.

## Observabilidade local

O trace do CrewAI Plus é uma SPA autenticada e efêmera, sem API/export/
retenção documentados e sem MCP oficial. Em vez disso,
`local_tracing.py` (`BaseEventListener` + `crewai_event_bus`, mecanismo
oficial) grava `data/logs/eventos.jsonl`: chamadas de tool (nome, args,
resultado truncado a 500 caracteres), resposta final e um evento `turno`
(pergunta, resposta, duração, interrompido), todos com `session_id` via
`ContextVar`. O agent é standalone, por isso os eventos são os "Lite"
(`LiteAgentExecutionCompletedEvent`), não os `AgentExecution*` de Crew.
Rotação 20MB×5. Em produção `CREWAI_TRACING_ENABLED=false` e
`CREWAI_DISABLE_TELEMETRY=true`: conversa de terceiros não sai da VM. Pra
analisar uso: `grep '"tipo": "turno"' data/logs/eventos.jsonl`.
