# Busca: por que Qdrant, BM25 e híbrido ponderado

Referência durável das decisões de retrieval. Sem status — o que está
aplicado ou pendente fica no `STATE.md`. Complementa `retrieval.md` (que
trata de *quando busca não é a ferramenta*).

## Natureza do corpus decide o motor

O corpus é majoritariamente texto jurídico-regulatório formal (atas,
comunicados, releases da CVM): terminologia precisa, nome próprio, sigla,
número. Busca só-vetorial é fraca exatamente nisso. BM25 (mesma família do
`FULLTEXT` do MySQL e do `tsvector` do Postgres) resolve termo exato de
forma determinística; o vetor ainda salva paráfrase ("demissão em massa" vs
"desligou 3 mil pessoas"). Híbrido é o padrão de mercado, e em corpus formal
BM25 sozinho costuma ganhar do denso puro — foi o que se mediu aqui.

## Por que Qdrant e não Chroma

- Vetor esparso/BM25/`Rrf()` no Chroma existem só no Chroma Cloud. Testado
  com `chromadb==1.5.9` + `PersistentClient`: falha
  `Sparse vector indexing is not enabled in local`. O mantenedor confirma
  (issue #6185) sem previsão. E o `crewai` instalado pina `chromadb~=1.1`.
- Alternativa descartada: FTS5 do SQLite ao lado do Chroma. Seria motor
  caseiro (normalização PT, stemming manual, sintaxe MATCH, RRF em Python,
  dois índices pra manter em sincronia) — tudo que o Qdrant já entrega.
- Comparados e descartados: Pinecone (SaaS, dado sai da máquina, cota),
  Milvus (etcd+minio pra 23 mil chunks; Milvus Lite sem BM25), LanceDB
  (embedded, sem wrapper CrewAI).
- Qdrant: esparso nativo, BM25 via `fastembed` com stemmer `portuguese`,
  filtro por payload, fusão RRF no motor, mesma API embedded e servidor.

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

## Normalização do BM25 (dois defeitos achados olhando os erros)

Aplicada no índice e na pergunta (`qdrant_store.normalizar_bm25`):
1. **Acento quebrava o stem.** O tokenizer do fastembed não tira acento:
   "renúncia" e "renunciaram" viravam tokens diferentes. Fix: NFKD +
   descarte de não-ASCII antes do stemmer.
2. **O cabeçalho `[Fonte: … | Data: …]`** (repetido a cada ~800 caracteres)
   carregava a categoria CVM em texto e inflava o score de todo chunk para
   pergunta com "conselho de administração". Sai só do texto do BM25; o LLM
   continua vendo o cabeçalho.

Reprocessar só o esparso: `backfill_qdrant.py --so-bm25`.

## Query rewriting mora na docstring da tool

"Tem certeza?" não tem termo, nenhum motor acha "298 lojas" com isso. A tool
`buscar_conhecimento(consulta)` exige consulta autônoma e rica em termos, no
vocabulário do documento CVM ("diretor presidente", não "CEO"; "renúncia",
não "troca de comando"), e proíbe follow-up cru. O LLM já monta o argumento
— sem chamada extra.

## Medições (golden de 14 perguntas, 4 delas paráfrase de propósito)

`scripts/avaliar_retrieval.py` + `tests/golden_retrieval.json`. Métricas:
recall@8 e MRR.

| configuração | recall@8 | MRR |
|---|---|---|
| Chroma denso `nomic-embed-text` (baseline, 10 perguntas) | 5/10 | 0.450 |
| Qdrant denso `nomic-embed-text` | 5/14 | 0.310 |
| Qdrant BM25 | 11/14 | 0.574 |
| Qdrant BM25 + denso `qwen3-embedding-8b`, RRF sem peso | 11/14 | 0.599 |
| denso `qwen3-embedding-8b` puro | 8/14 | 0.429 |
| **híbrido ponderado, `peso_bm25=0.7`** | **12/14** | **0.699** |

Leitura: com `nomic-embed-text` o denso não contribuía nada mensurável —
nem nas paráfrases. Trocar pro Qwen3 subiu o denso (5/14 → 8/14) e só então
o híbrido passou o BM25 puro. A causa do denso fraco antes era o embedder
(pouco discriminativo em PT-BR jurídico; score quase plano, 0.80-0.85 nos
1000 melhores), não o conceito.

**Armadilha de medição**: golden só de pergunta léxica premia BM25 por
construção. Por isso as 4 paráfrases.

## Descartado, com o porquê

- **Reranking cross-encoder** (`bge-reranker-v2-m3`): 12 min pra 300 pares em
  CPU, e o documento certo continuou fora do top 8 (hipótese, não confirmada:
  truncamento em `max_length=512` em chunk de 2000 caracteres).
- **Roteamento por categoria + blend de recência** no vetorial: remendo pra
  diluição da busca; o BM25 puro sem eles já batia o golden. Blend de
  recência ainda prejudica pergunta histórica ("quem foi o CEO anterior").
- **`pdfplumber` para tabela de DRE em PDF**: a tabela não tem grade; a
  detecção por texto separa colunas mas fragmenta a prosa da mesma página em
  palavra solta, e `layout=True` colapsa as duas mini-tabelas lado a lado.
  Resolvido por outro caminho: DRE estruturada da CVM (ver `ingestao.md`).

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

## Typo de nome de tool

O CrewAI sanitiza o nome do decorator ("Buscar na base de conhecimento" →
`buscar_na_base_de_conhecimento`) e o modelo reescreveu errado
(`UNKNOWN_TOOL`). Nome de tool é snake_case curto, igual ao nome da função.
A policy do CrewAI é `warn` (o agent tenta de novo): o sintoma é lentidão ou
resposta pior, não crash.
