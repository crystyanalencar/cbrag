# Retrieval: quando busca não é a ferramenta

Referência durável do que se aprendeu montando o chat. Sem status — o que
está aplicado ou pendente fica no `STATE.md`.

## Três tipos de pergunta, três mecanismos

| Pergunta | Natureza | Mecanismo | Módulo |
|---|---|---|---|
| "Qual o lucro do 2T26?" | valor num dataset | dado estruturado da CVM (ITR/DFP) | `dados_financeiros.py` |
| "Quem é conselheiro hoje?" | estado atual de entidade que muda por evento | último FRE arquivado | `composicao_conselho.py` |
| "Qual o último fato relevante?" | ordem no tempo | metadata do sidecar ordenada por `data_ordinal` | `documentos_recentes.py` |
| "Por que fecharam lojas?" | conteúdo narrativo | BM25 sobre chunks | `knowledge_config.buscar_contexto` |

O erro recorrente foi tratar as três primeiras como se fossem a quarta.
Busca por similaridade (densa ou léxica) responde "que trecho parece com a
pergunta" — não responde "qual é o valor", "qual é o vigente" nem "qual é o
mais recente". Cada vez que uma dessas perguntas foi jogada no RAG, o
sintoma foi o mesmo: resposta diferente a cada tentativa, e o agent
compensando com dezenas de reformulações da consulta.

## Por que "mais recente" não funciona em BM25

"Último", "mais recente", "atual" não têm âncora lexical: nenhum documento
contém essas palavras de forma discriminativa. O ranking cai em qualquer
documento com o termo genérico ("fato relevante") mais forte — normalmente
o mais longo ou o que repete o termo. Reformular a consulta não muda isso;
adicionar o ano ajuda só se o ano for conhecido de antemão.

O denso (`nomic-embed-text`) tem o mesmo problema agravado: neste corpus
regulatório o score fica quase plano (0.80-0.85 nos 1000 melhores), sem
sinal pra separar documento certo de boilerplate (ver medições no
`STATE.md`, fases 3 e 3.1). Cross-encoder de reranking foi testado e não
resolveu (12 min em CPU, e o documento certo continuou fora do top 8).

Roteamento por categoria + blend de recência no vetorial também foi tentado
e removido: era remendo pra diluição da busca, e o BM25 puro sem eles já
batia o golden. A solução que ficou é não usar busca pra esse tipo de
pergunta.

## Metadata é o que torna isso possível

`scripts/preparar_knowledge.py` grava `data/knowledge/_metadata.json` com,
por arquivo: `categoria_cvm` (limpa, do CSV IPE), `tipo_cvm`, `assunto`,
`data_iso`, `data_ordinal`. Mesma fonte alimenta o payload do Qdrant. Sem
esse sidecar, "listar os N mais recentes da categoria X" exigiria ler o
índice inteiro; com ele é um `sort` em memória sobre 732 entradas.

Documentos do site institucional (HTML/PDF do Wayback) não têm
`categoria_cvm` e ficam fora das listagens — só a CVM tem categoria
formal.

## O agent decide a ferramenta; a docstring é o roteador

Não há roteamento por palavra-chave no código: `Agent.kickoff()` com as
tools deixa o LLM escolher. Na prática o que roteia é a **docstring de cada
tool**, que diz quando usar e — mais importante — quando NÃO usar a
concorrente ("não use `buscar_conhecimento` pra descobrir qual é o mais
recente"). Instrução perto do ponto de uso funcionou melhor que a mesma
instrução no backstory.

Modelo grátis/instável pode ignorar tudo isso e entrar em loop de
reformulação. A proteção é estrutural, não de prompt: `max_iter` no Agent.
`max_execution_time` **não vale** em `Agent.kickoff()` — `_prepare_kickoff`
só repassa `max_iter` pro `AgentExecutor`; o timeout só existe no caminho
`execute_task()`/Crew (lido no fonte do crewai 1.15).

## Parâmetro de tool: tolerante, não estrito

Categoria de documento é `str` normalizada (sem acento, sem plural, casa
por prefixo), não `Literal`. `Literal` faz o pydantic rejeitar antes do
código rodar → erro de tool → iteração perdida, e modelos escrevem "fatos
relevantes"/"comunicado" em vez do valor exato. Mesmo princípio do
parâmetro dummy `confirmar` em `consultar_composicao_conselho` (provider
strict rejeitava tool sem parâmetro).
