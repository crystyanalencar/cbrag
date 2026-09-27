# Busca híbrida: Qdrant, BM25 e vetor ponderado

Referência durável do mecanismo de retrieval. Complementa `retrieval.md`
(que trata de *quando busca não é a ferramenta*).

## Natureza do corpus

O corpus é majoritariamente texto jurídico-regulatório formal (atas,
comunicados, releases da CVM): terminologia precisa, nome próprio, sigla,
número. BM25 (mesma família do `FULLTEXT` do MySQL e do `tsvector` do
Postgres) resolve termo exato de forma determinística; o vetor cobre
paráfrase ("demissão em massa" vs "desligou 3 mil pessoas"). A busca é
híbrida: os dois motores rodam em paralelo e o resultado é fundido.

## Motor: Qdrant

Índice Qdrant com esparso nativo (BM25 via `fastembed`, stemmer
`portuguese`), filtro por payload e fusão RRF no motor, mesma API em modo
embedded e servidor.

**O wrapper `crewai.rag.qdrant` não serve pra híbrido**: `add_documents`
embeda só denso e `search` monta `query_points` com um vetor, filtro só
`MatchValue`. Usa-se `qdrant_client` direto (o que também elimina o problema
de `ContextVar` por thread do `set_rag_config` do CrewAI).

## Desenho da coleção

- Dois vetores nomeados: `denso` (cosine) e `bm25` (`Modifier.IDF` — sem ele
  o `fastembed`, que emite só TF, vira contagem crua de termo).
- Ponto = `uuid5(NAMESPACE, doc_id)`; `doc_id` (sha256 do chunk) segue como
  identidade no payload. Payload: `content`, `arquivo`, `origem`, `categoria`,
  `data_iso`, `data_ordinal`.
- Chunking fixo 2000/200 caracteres.
- Fusão RRF: `score(d) = Σ 1/(k + rank_i(d))`, k=60, por posição (cosine
  0..1 e BM25 sem teto não são comparáveis). 40 candidatos por lado, não 8 —
  RRF precisa de profundidade. **O RRF do motor não tem peso por lista**: o
  ponderado é fusão em Python (`buscar(..., peso_bm25=)`).
- Sem threshold de score: score de RRF não é similaridade.

## Por que cada busca do chat chama a API de embedding

Só o vetor `denso` exige API: pra comparar por significado, a pergunta tem
que virar vetor **no mesmo modelo que embedou o corpus** (`qwen/qwen3-embedding-8b`,
4096 dim, via OpenRouter; `qdrant_store.embed_denso` → `litellm.embedding`).
Modelo diferente gera vetores em espaços incomparáveis. O lado `bm25` é local
(`fastembed`), sem rede.

Custo: 1 chamada por `buscar_conhecimento`, poucas dezenas de tokens
(desprezível a US$ 0,01/mi tokens). O custo real do embedding é o do corpus na
ingestão. Consequência operacional: o chat precisa de chave OpenRouter válida
mesmo quando a geração usa outro provedor; sem ela a busca densa falha.
A `OPENROUTER_API_KEY` serve a geração (LLM do chat) e, por padrão, o
embedding (pergunta no chat + corpus na ingestão), lida pelo `litellm` do
ambiente. Com `OPENROUTER_EMBED_API_KEY` definida, `embed_denso` usa essa
chave no embedding: chaves separadas na mesma conta dão limite de crédito
por chave, então estouro de uma não derruba a outra. Sem ela cai na geral.

## Normalização do BM25

Aplicada no índice e na pergunta (`qdrant_store.normalizar_bm25`):
1. NFKD + descarte de não-ASCII antes do stemmer (o tokenizer do fastembed
   não tira acento por conta própria).
2. O cabeçalho `[Fonte: … | Data: …]` (repetido a cada ~800 caracteres) sai
   só do texto usado pelo BM25; o LLM continua vendo o cabeçalho no chunk.

Reprocessar só o esparso: `backfill_qdrant.py --so-bm25`.

## Query rewriting mora na docstring da tool

"Tem certeza?" não tem termo, nenhum motor acha "298 lojas" com isso. A tool
`buscar_conhecimento(consulta)` exige consulta autônoma e rica em termos, no
vocabulário do documento CVM ("diretor presidente", não "CEO"; "renúncia",
não "troca de comando"), e proíbe follow-up cru. O LLM já monta o argumento
— sem chamada extra.

## Medição de qualidade

`scripts/avaliar_retrieval.py` + `tests/golden_retrieval.json` (golden de 14
perguntas, 4 delas paráfrase de propósito, pra não premiar só busca léxica).
Métricas: recall@8 e MRR. Configuração corrente (híbrido ponderado,
`peso_bm25=0.7`, modo "chat", sem `meses_recentes`): recall@8 10/14 (0.71),
MRR 0.552, 3/5 falhas conhecidas recuperadas (medido 2026-09-27). Os 4 misses
restantes são os casos sem âncora lexical descritos em `retrieval.md`
("mais recente", "tem certeza" etc.) — não regridem com mudança de peso, só
com dado estruturado ou filtro de data.

## Modo embedded x servidor

O modo embedded avisa "não recomendado acima de 20 mil pontos" (o índice
inteiro fica em memória, busca é scan) e "payload indexes have no effect".
Com ~23 mil pontos: 21s de carga no primeiro uso do processo, ~1.2s por
consulta, e um lock que só admite um processo (chat ou ingestão, nunca os
dois). Servidor Qdrant elimina os três, com a mesma API e zero mudança de
código (`QDRANT_URL`).

## Trocar de embedder

Vetores só valem pro modelo que os gerou. Trocar `MODELO_DENSO`/`DIM_DENSO`
exige recriar a coleção e reembedar tudo, e re-medir o golden nos modos.

## Nome de tool

Snake_case curto, igual ao nome da função (não o nome que o CrewAI sanitiza
a partir da docstring). Nome longo/sanitizado é mais fácil do modelo
reescrever errado (`UNKNOWN_TOOL`); a policy do CrewAI é `warn` (o agent
tenta de novo), então o sintoma é lentidão ou resposta pior, não crash.
