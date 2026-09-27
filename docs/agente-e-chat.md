# Agente, tools e interface de chat

Referência durável de como o agent se comporta e por quê. A escolha de
ferramenta por tipo de pergunta está em `retrieval.md`.

## Arquitetura

Flow conversacional do CrewAI (`conversational = True`, `handle_turn`) com
**um** `Agent` chamado por `Agent.kickoff()` e tools; sem Crew nem Task. O
histórico é do próprio Flow, por `session_id`, e cada sessão de navegador
tem Flow próprio (usuários simultâneos funcionam). Desvios conscientes da
doc do CrewAI: instruções no `backstory` (a doc quer na task; sem Task aqui,
e instrução perto da tool funcionou melhor), 7 tools (a doc recomenda até 5: as duas
novas, `consultar_cobertura_da_base` e `consultar_indicadores_operacionais`, cobrem
perguntas que nenhuma outra respondia; se a escolha de tool piorar, é o primeiro suspeito),
import `crewai.flow.conversational`.

Tools: `consultar_resultado_financeiro`, `consultar_serie_historica_resultado`,
`consultar_composicao_conselho`, `consultar_documentos_recentes`,
`consultar_cobertura_da_base`, `consultar_indicadores_operacionais`,
`buscar_conhecimento`. A escolha entre elas é do LLM; **a docstring é o
roteador**, e diz também quando NÃO usar a concorrente.

## Prompt não é garantia; código é

Regra que se provou várias vezes: instrução em prompt/docstring reduz o
erro, não o elimina. O que precisa valer sempre vai pro código.

- **Fabricação de explicação.** Se `ano_fim` (passado pelo LLM) esconde dado
  mais recente, `serie_resultado_financeiro` anexa todos os trimestres
  posteriores com a nota "fora do intervalo pedido, mas dado mais recente" —
  guarda estrutural contra o modelo inventar que "o sistema cobre só até
  aquele ano".
- **Loop de reformulação.** `Agent(max_iter=8)` limita repetição de busca.
  `max_execution_time` é **inerte** em `Agent.kickoff()` (`_prepare_kickoff`
  só repassa `max_iter`; o timeout só existe em `execute_task()`/Crew), então
  não dá falsa segurança.
- **Data de hoje**: `inject_date=True` (recalculado por chamada). Um
  `date.today()` no f-string do backstory ficava fixo no import — container
  no ar por semanas ficaria com "hoje" velho. Sem a data, "até hoje" era
  ambíguo e a varredura ano a ano parava em 2025.
- **Fabricação de desculpa institucional.** Pergunta com valor nomeado que a
  busca não acha (ex. "quanto deve à Samsung") fazia o modelo inventar
  sigilo/privacidade ("não me cabe divulgar") em vez de dizer que não achou —
  fabricação disfarçada de política, mais grave que alucinar um número.
  Backstory proíbe essa saída explicitamente e manda responder só "não
  encontrei esse valor específico".
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
  período/evento e avisa que não há total consolidado.
- Não trocar de posição só porque o usuário contestou: rebuscar antes.
- Citar fonte/data só quando o usuário pede.
- Não se apresentar como "modelo de linguagem treinado pelo Google"; travar
  resposta em pt-BR independente do modelo escolhido pelo preset.
- O cabeçalho do chunk não expõe o slug do arquivo (o LLM repete o que lê).

## Provider de LLM

Geração via OpenRouter com **preset** (`openrouter/@preset/free-tier-first`),
controlado no painel — modelo, ordem, reasoning e limite de gasto ficam do
lado do provider, não no código. Consequência: o modelo muda por chamada;
comportamento errático (identidade genérica, troca de idioma, loop,
conversa longa ignorando resultado de tool) é tratado com backstory,
`max_iter` e tools determinísticas, não trocando modelo. `_kickoff()` só
captura exceção pra não derrubar o chat — sem fallback nem retry manual
entre providers, isso é papel do preset do OpenRouter.

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
- **Marca**: logo/avatar/favicon são ícone SVG original, não o logo oficial
  do Grupo Casas Bahia (risco de marca registrada); disclaimer de
  não-afiliação em `chainlit.md` e no rodapé da landing. `logo_*`,
  `favicon.*` e `avatars/{nome}.*` são resolvidos por glob, extensão livre.
- Tema do site estático: dropdown Claro/Escuro/Sistema, mesma chave
  `localStorage["vite-ui-theme"]` que o Chainlit usa em `/chat` (antes cada
  um tinha chave própria e o tema não sincronizava entre `/`, `/docs` e
  `/chat`). "Sistema" segue `prefers-color-scheme` de verdade; só os modos
  explícitos (Claro/Escuro) fixam `[data-theme]` e ignoram o SO.
- Cor de marca vem do `theme-color` do `<meta>` do site institucional
  (`#0033C6`; acento `#e71a3b`).
- `allow_origins` restrito ao domínio de produção.

### Widget de chat da home

Balão flutuante em `site/index.html` (bolhas + input), sem Chainlit UI em
volta: rota própria `POST {root_path}/api/chat` (`_widget_chat` em
`chainlit_app.py`), reusa `CbragFlow.handle_turn` puro — mesma API
conversacional oficial do CrewAI que a página `/chat` usa.

- **SSE, não JSON puro.** Um turno real leva 25-40s (RAG + LLM); sem
  heartbeat nesse tempo, rede móvel/proxy mata a conexão por inatividade e o
  navegador nunca vê a resposta pronta. `_widget_chat` manda `: ping` via
  SSE a cada 10s enquanto `handle_turn` roda em thread, e só o resultado
  final vem como `data: {...}`.
- **Sessão por `session_id` do navegador**, não a sessão WebSocket do
  Chainlit — guardada em memória do processo com TTL de 2h, limpa
  preguiçosamente a cada chamada (widget não avisa quando a aba fecha).
  Botão "Novo chat" só gera `session_id` novo e limpa o histórico local.
- Frontend poda bolhas antigas ao reabrir o painel (painel pesado se
  acumular sessão longa) e renderiza markdown básico (negrito `**`, itálico
  `*` e lista) na resposta — `*itálico*` sozinho vazava como asterisco
  literal até o LLM começar a usar pra grifar termo em inglês/jargão
  (`*releases*`, `*roadshows*`), quando ficou comum o bastante pra doer.
- **Armadilha de tipo**: anotação de retorno com `Union`
  (`JSONResponse | StreamingResponse`) numa rota custom do FastAPI do
  Chainlit quebra o startup (`FastAPIError: Invalid args for response
  field`). `uv run python -c "import cbrag.chainlit_app"` local pega isso
  sem precisar buildar container.

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
