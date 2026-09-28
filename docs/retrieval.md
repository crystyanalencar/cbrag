# Retrieval: quando busca não é a ferramenta

Referência durável do que se aprendeu montando o chat.

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

Busca vetorial tem o mesmo problema agravado: neste corpus regulatório o
score fica quase plano nos milhares melhores resultados, sem sinal pra
separar documento certo de boilerplate. A solução é não usar busca pra esse
tipo de pergunta — dado estruturado ou metadata ordenada (tabela acima).

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

## Anexos tabulares de documento grande (Petição Inicial da RJ)

A Petição Inicial de 18/08/2026 da Central (`2026-08-18_peticao_inicial_0cb0465d.txt`)
tem 26 MB e ~14,2 mil chunks: o corpo narrativo (~2 mil linhas) e os anexos
(Doc. 9 relação nominal de credores; Doc. 20 fornecedores classificados
"Essencial"; contratos, certidões). A CVM também tem a petição (versões de
18, 20 e 21/08 + anexo 1), mas sem esses anexos; a lista de credores e a de
fornecedores essenciais só existem na Central.

Pergunta em linguagem natural ("quais os credores essenciais?", "cite as
empresas de serviços digitais") não traz os anexos: o top-k de 8 vem do corpo
narrativo (que repete "credores" e "essenciais" com muito mais densidade). O
chunk de linha de tabela é só `1120339 25012398000107 GOOGLE CLOUD ... Essencial`:
o título do anexo e o cabeçalho de colunas ficam só no primeiro chunk da
tabela, então o embedding denso não tem semântica e o BM25 perde pro texto
corrido. Prova: consulta que **imita a tabela** ("Fornecedor CNPJ Nome do
fornecedor CLASSIFICAÇÃO Essencial GOOGLE CLOUD", "Devedor Classificação
Credor Extraconcursal BANCO BRADESCO") devolve quase só chunks do Doc. 20/Doc. 9.
Ou seja, o dado está indexado; falta contexto no chunk e sobra concorrência
no top-k (par CVM × Central duplicado ocupa 2 slots por documento).

**A reformulação virou garantia de código, não só instrução de docstring**
(`rag_tools._buscar_anexo_essencial_se_pedido`). Motivo: reproduzido em
produção (2026-09-27) que o LLM, na primeira tentativa, não reformulava
sozinho pra imitar a tabela e em vez disso **inventava uma desculpa
técnica** ("minha base não permite extração automatizada de listas
tabulares em anexos") — variante do mesmo padrão de fabricação de
desculpa institucional já visto com "sigilo" (`agente-e-chat.md` § Prompt
não é garantia). `buscar_conhecimento` detecta o radical "essencia" +
"fornecedor"/"credor" na consulta (qualquer forma: singular, plural) e
injeta a busca tabular sozinho, sem depender de o LLM notar que a primeira
resposta veio só em tese.

Como diagnosticar: `eventos.jsonl` do chat grava só ~1 chunk por resultado de
tool, então não mostra o top-k. Rodar `kc.buscar_resultados(consulta)` dentro
do container (`/app/.venv/bin/python`) e ler `metadata` de cada resultado.

## Teto de chunks por arquivo no top-k: medido, sem efeito no golden

`qdrant_store.buscar(max_por_arquivo=N)` limita quantos chunks do mesmo arquivo
entram no top-k (busca `limite × 4` candidatos e preenche). Com N = 1, 2 e 3 o
golden de 14 perguntas ficou igual (10/14, MRR 0,625). O miss "a companhia está
passando por dificuldades financeiras graves?" persistiu mesmo com 8 documentos
distintos no top-8: é ranking (pergunta sem âncora lexical, o fato relevante
da RJ não pontua), não um documento longo ocupando as vagas. O teto ficou no
código com default `None` (sem efeito); ainda não foi medido nos anexos
tabulares da petição, que é onde a hipótese original fazia mais sentido.

## Golden depois de trocar de fonte

O golden mede pelo nome do arquivo esperado (substring). Trocar a CVM pela
Central muda o nome (`2026_08_18_...` vira `2026-08-18_...`), então o campo
`esperado` aceita os dois formatos e a migração deve ser conferida antes de
apagar a fonte antiga: `scripts/avaliar_retrieval.py --sem-cvm-2026` simula o
resultado sem a CVM 2026 (busca 40 candidatos e descarta os arquivos dela). Na
migração de 2026 a simulação previu 10/14 e o real deu 10/14; o que muda é o
caso "tem certeza sobre o número de lojas?", que só passava pelo arquivo da
CVM e volta a falhar.

## Filtro de data determinístico: opt-in, não blend de recência

O caso "tem certeza sobre o número de lojas? houve fechamento de lojas
recentemente?" (golden, `falha_conhecida`) é o mesmo padrão de "mais recente
não tem âncora lexical" descrito acima, com uma variante: o corpus tem
*vários* documentos ao longo dos anos discutindo "fechamento de lojas"/"plano
de transformação", cada um com um número diferente (298 lojas desde o início
do plano, 55-57 lojas em 2023/2024, 9 lojas nos últimos 12 meses) — a busca
híbrida pode trazer qualquer um deles, inclusive o mais antigo, porque nenhum
motor tem sinal de recência.

Diferente do resto desta página (onde a resposta é "não use busca, use dado
estruturado"), aqui o fato É narrativo — não existe tabela estruturada pra
"fechamento cumulativo do plano". A solução testada foi filtro
**determinístico** de `data_ordinal` (`qdrant_store._filtro_data_minima`,
payload já indexado), exposto como `buscar_conhecimento(meses_recentes=N)` —
corta candidato fora da janela antes do ranking, não pondera score.

Pesquisado antes de implementar: um "blend de recência" (pesar score por
idade) já foi tentado e removido antes (`knowledge_config.buscar_resultados`
ainda documenta isso) — era remendo pra busca vetorial fraca da era Chroma, e
literatura de RAG temporal confirma que decay agressivo prejudica recall e
precisa de meia-vida ajustada por tipo de conteúdo (documento jurídico/
regulatório aguenta ~1 ano sem perder relevância, diferente de notícia).
Filtro é opt-in por chamada: sem isso, pergunta sobre fato antigo ("quem era
o CEO em 2020") continua competindo em pé de igualdade.

Medido com `scripts/avaliar_retrieval.py --meses-recentes N --verboso`:
aplicado a UMA pergunta (a que já falhava), o caso vira `OK` (documento de
2T26 entra no top-8). Aplicado **a todas as 14** do golden (simulação de uso
incorreto, só pra confirmar o mecanismo), recall cai de 10/14 pra 7/14 — os
casos de fato antigo (2020/2021/2023) somem do top-8 porque o filtro os
exclui. Isso não é regressão do golden padrão (o modo "chat" do script não
passa `meses_recentes`, então o comportamento sem filtro continua 10/14); é
a confirmação de que o filtro tem que ser decisão da LLM por pergunta —
nunca default global — e a docstring da tool instrui isso.
