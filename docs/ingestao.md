# Ingestão: fontes, incremental e custo

Referência durável do pipeline que alimenta o índice. Sem status — o que
está aplicado ou pendente fica no `STATE.md`. Detalhe de cada fonte
(Akamai, Wayback, CVM): `fontes-de-dados.md`.

## Dois Flows, não um

`IngestFlow` (`uv run ingest`) e `CbragFlow` (chat) são separados porque o
Flow conversacional reroda `kickoff()` inteiro a cada `handle_turn()`: se
ingestão e chat fossem o mesmo Flow, cada pergunta reraparia e reembedaria
tudo. Cada script de `scripts/` é um `@listen` do `IngestFlow` (passo pode
ser Python puro, sem LLM), em ordem: coleta (Wayback, CVM, Central de
Downloads) → extração de PDF → preparação do corpus → embedding no Qdrant.

`Agent.kickoff()` standalone não faz retrieval automático de knowledge (isso
só existe em `execute_task()`/Crew). Por isso o retrieval é por tool, não
por injeção de contexto.

## Incremental: dois níveis

1. **Manifesto por arquivo** (`data/knowledge_storage/manifest.json`, fora
   do git): hash SHA-256 do conteúdo de cada `data/knowledge/*.txt`, mais
   chunks e data de embedding. Só arquivo novo ou mudado é processado.
2. **Diff por chunk** (`qdrant_store.indexar_arquivo`): dentro de um arquivo
   mudado, calcula quais chunks (por `doc_id`/id de ponto determinístico) já
   existem, e só chama embedding/BM25 pro delta; chunk que sumiu (órfão) é
   removido. Existe porque o FRE tem ~130MB e muda várias vezes por mês —
   sem isso cada versão reembedava o arquivo inteiro (custo real, OpenRouter
   cobra por token).

**O que o CrewAI não faz**: não há dedupe de chunk no framework
(`ChromaDBClient.add_documents` faz `upsert` sem `embeddings=`, o que chama a
função de embedding para todo o batch sempre). O upsert evita duplicar
armazenamento, não evita a chamada paga.

**Limite do diff por chunk — efeito avalanche**: o chunker é por offset fixo
(2000/200). Edição pequena no começo do documento desloca todo chunk depois
e muda quase todo hash sem conteúdo novo. Content-Defined Chunking (a técnica
de fronteira estável de restic/borgbackup) é a saída conhecida; LangChain
Indexing API (record manager por hash de chunk, limpa órfão) e LlamaIndex
`IngestionPipeline` + `DocstoreStrategy.UPSERTS` resolvem o mesmo problema
em nível de framework.

**FRE: só o delta entra (`kc._chunk_fre`, `qs.indexar_arquivo`).** O
Formulário de Referência (~350 páginas) é republicado várias vezes por mês
com poucas mudanças, e o chunker de offset fixo mais o cabeçalho com a data
faziam cada versão reembedar 100% (medido em 5 versões distintas). Três
decisões:
- **Chunking por conteúdo, em cima de palavras, não de linhas.** O PDF é
  reflowed entre versões: entre duas delas 39% das linhas diferiam com 2,6%
  de texto novo. A fronteira cai no fim de uma sentença cujo hash das últimas
  4 palavras satisfaz uma condição (depois de 700 caracteres), então uma
  edição só muda o chunk que a contém. Chunker por linha foi tentado antes e
  deu 562 chunks novos onde o de palavras dá 291 (v2 -> v3).
- **Identidade sem cabeçalho e sem página.** `doc_id` = hash das palavras do
  chunk. Saem do texto o índice do FRE, a numeração de página, o rodapé
  "Versão : N" e o ruído de assinatura eletrônica (Docusign, ID do
  escritório); o item (ex. "4.1 Descrição dos fatores de risco") fica na
  primeira linha do chunk, que nunca atravessa item.
- **O índice guarda só a versão vigente.** `indexar_arquivo` compara a
  versão nova com a categoria inteira no Qdrant (não com o mesmo arquivo):
  embeda o que é novo, remove o que sumiu, e só atualiza o payload
  (cabeçalho com versão/data, `arquivo`) do que ficou, sem reembedar.
  `preparar_knowledge.py` descarta as versões anteriores do corpus (fica a
  de data mais recente; empate, a de maior "Versão :"). Custo: histórico do
  FRE não é consultável no índice, só o estado atual.

Resultado nas 5 versões distintas do FRE de 2026 (chunks novos por versão,
novo x legado): 717 x 501, 155 x 497, 291 x 495, 1 x 495, 25 x 486.
`scripts/testar_fre_incremental.py` reproduz isso sem gastar embedding e
falha se o chunker perder palavra, passar de `CHUNK_SIZE` ou embedar mais que
o delta. Rodar de novo ao mexer no chunker do FRE: mudar `FRE_*` invalida a
identidade de todos os chunks (reembeda o FRE inteiro, uma vez).

**O manifesto descreve um índice específico, não o corpus-fonte.** Quando ele
não acompanha o Qdrant (ex.: índice migrado sem levar o manifesto), o
`embutir_conhecimento` acha que nada foi embedado e reprocessa o corpus
inteiro, com custo pago. A fonte da verdade é o Qdrant: o manifesto se
reconstrói contando pontos por `arquivo` no payload.

**Mudar o texto embedado invalida o hash.** O cabeçalho
`[Fonte: … | Data: …]` faz parte do conteúdo hasheado; mexer nele reembeda
tudo. Idem trocar de embedder.

## Metadata por chunk

`TextFileKnowledgeSource` não anexa metadado por chunk, e o campo `metadata`
da API alta do CrewAI nunca chega no storage. Por isso o cabeçalho
`[Fonte | Data]` é injetado a cada ~800 caracteres (sem ele o RAG não
distingue dado de 2021 de dado de 2026 em corpus com estatuto/capital social
que se sobrepõem), e `preparar_knowledge.py` grava o sidecar
`data/knowledge/_metadata.json` (`categoria_cvm`, `tipo_cvm`, `assunto`,
`data_iso`, `data_ordinal`, `origem`) que alimenta o payload do Qdrant e a
tool `consultar_documentos_recentes`.

O nome do arquivo não carrega a categoria limpa: Categoria e Assunto ficam
concatenados num slug (442 valores distintos). A categoria limpa (21 valores)
vem do índice `_ipe_index.json`, gravado por `baixar_cvm.py` a partir das
colunas separadas do CSV IPE.

## Dado estruturado da CVM (não passa pelo RAG)

Pergunta de valor, estado atual ou ordem no tempo não é busca (ver
`retrieval.md`). Fontes:

- **DRE** — `ITR_CIA_ABERTA`/`DFP_CIA_ABERTA`: uma linha por conta contábil,
  com `DT_INI_EXERC`/`DT_FIM_EXERC` exatos (distingue trimestre isolado de
  acumulado sem ambiguidade, o que extração de tabela de PDF não consegue).
  `VL_CONTA` vem em milhares (`ESCALA_MOEDA=MIL`). O código CVM é
  zero-padded (`006505`) aqui e sem padding (`6505`) no IPE. Cada linha vem
  em par `ÚLTIMO`/`PENÚLTIMO`, o que dá variação a/a sem novo download. A CVM
  não reporta 4º trimestre isolado (só embutido no acumulado do ano).
- **Composição do conselho** — FRE, arquivo
  `fre_cia_aberta_administrador_membro_conselho_fiscal_{ano}.csv`, filtrado
  por CNPJ, versão mais recente. A empresa reenvia toda vez que a composição
  muda. Cuidado: `Data_Eleicao`/`Data_Posse` são da **última reeleição**, não
  da posse original.
- **Princípio**: "estado atual de X" (conselho, diretoria, capital social,
  auditor) é dado estruturado com histórico de eventos. Checar primeiro se a
  CVM tem dataset equivalente (FCA, FRE, ITR/DFP, IPE) antes de tentar RAG
  sobre atas.
- Um erro que custou uma tentativa: FCA/FRE **não** trazem só contagem
  agregada; o arquivo certo é o de `administrador_membro_conselho_fiscal`,
  distinto dos datasets de autodeclaração de gênero/raça no mesmo zip.

**Dois documentos chamados "FRE", não confundir.** O dataset estruturado
(`baixar_fre.py`, usado por `composicao_conselho.py`) é um resumo —
administradores e conselho. O Formulário de Referência completo que a
Central de Downloads lista (~130MB: estrutura societária, risco,
administração, remuneração, partes relacionadas) é o documento mais rico da
base e entra no RAG como PDF.

## Fontes de documento corrente

- **CVM aberta** (`dados.cvm.gov.br`, IPE): um zip por ano, atualizado
  incrementalmente com lag de ~4-5 dias (o zip do ano corrente existe e
  cresce; uma conclusão anterior de "só fecha no fim do ano" estava errada).
- **Central de Downloads do RI** (`api.mziq.com`, plataforma Mziq): API JSON
  de terceiro, **sem Akamai**, `curl` puro funciona. Documento direto da
  companhia, sem intermediário. Fonte de 2026 em diante.
  `POST …/mzfilemanager/company/<id>/filter/categories/year/meta` com
  `{"year","categories","language","published"}` devolve título, data,
  categoria e `file_url` do PDF.
  - **O `year` da Central é o do exercício, não o da publicação.** DFP,
    release e apresentação do 4T25 publicados em mar/2026 estão no ano 2025.
    A data no nome do arquivo (e o `data_iso`) é a de publicação, e é por ela
    que `migrar_ano_para_central.py` apaga a CVM. Por isso `baixar_ri_mziq`
    coleta também o ano anterior a `ANO_INICIAL`, só com o publicado a partir
    de 01/01 de `ANO_INICIAL`. Sem isso, apagar a CVM 2026 perderia DFP,
    release e relatório anual. O Formulário de Referência fica de fora dessa
    coleta extra: a CVM só tem o dataset estruturado dele, não o PDF, então
    não há o que preservar.
  - `categoria_cvm`, `tipo_cvm` e `especie_cvm` são derivados do prefixo de
    `file_title` (`baixar_ri_mziq.classificar`) pro **mesmo vocabulário do
    CSV IPE**, e o `_ri_index.json` tem o mesmo formato do `_ipe_index.json`.
    Foi conferido contra a CVM: deck de resultado e apresentação
    institucional são Comunicado ao Mercado / "Apresentações a analistas",
    não Dados Econômico-Financeiros (esses são release e ITR/DFP); Ata de
    RCA é Reunião da Administração / Conselho de Administração / Ata;
    esclarecimento de notícia é "Esclarecimentos sobre questionamentos da
    CVM/B3". Sem categoria de propósito (a CVM não tem): transcrição de
    vídeo de resultado e Formulário Cadastral. Informe de Governança tem
    categoria própria (o IPE não tem equivalente).
  - Títulos de baixo valor são pulados no download (processual de
    assembleia, dívida/mercado de capitais), decididos revisando os títulos
    distintos de 2026 um a um.
  - **GOV entra como texto legível**: `extrair_texto_pdfs.extrair_csv_governanca`
    transforma cada linha do CSV em "Prática X - ... / Adotada: Sim|Não|Não se
    aplica / Explicação: ..." (a extensão real, `.csv`, é decidida pelo
    `Content-Type` no download). Categoria própria "Informe de Governança",
    junto com o PDF "Informe de Governança" do mesmo informe.
  - **Arquivos da Central não são confiáveis pela extensão nem pelo
    tamanho.** O `GOV` (Informe do Código Brasileiro de Governança
    Corporativa) vem como `.pdf` mas é CSV `;` em UTF-8 (`Content-Type:
    text/csv`), por isso o pypdf falha nele. Os FRE chegam **truncados pela
    própria API** (`Content-Length` de 16/32/128 MiB exatos, sem `%%EOF`);
    o pypdf reconstrói as páginas e o texto sai completo (a última página
    extraída é a "N de N"), então é aceitável. O FRE de 26/08 é caso
    diferente: 24,8MB reais seguidos de zeros, sem como recuperar.
  - "Planilha de Resultados" é `.xlsx` (pypdf não lê): 11 abas (Lojas,
    Covenants, Crediário…), mais granular que qualquer dataset público. Não é
    ingerida até haver uma 2ª versão pra confirmar estrutura estável.
- **Wayback** (`web.archive.org`): só pra páginas do site institucional sem
  API; baixo valor (as falhas de coleta nunca vieram dele). `crawl_wayback.py`
  tem circuit breaker e a etapa não bloqueia as seguintes.

**Migração por fase, ano a ano**, não um corte fixo. `baixar_cvm.py` tem
`ANOS` capado; cada fase desce o limite um ano e roda
`migrar_ano_para_central.py <ano>` (apaga vetores, manifesto e arquivos dos
documentos CVM daquele ano). Só depois de confirmar que a Central cobre, por
`categoria_cvm`, tudo que a CVM cobria — senão perde dado.

**Republicação com tradução**: comunicado republicado só com anexo em inglês
tem texto PT idêntico. Aceito como duplicata: o diff por chunk já evita
reembedar, e `documentos_recentes` mostra o mesmo fato duas vezes com datas
diferentes (ruído pequeno, filtrar não paga a complexidade).

## Akamai: a causa é headless, não IP

Site institucional e RI (`ri.grupocasasbahia.com.br`) usam Akamai. Testado em
três camadas: `curl`/`requests` com headers completos → 403; Playwright
**headless** (Chromium real) → 403; Playwright **headed** (via Xvfb, servidor
sem tela) → 200. O bloqueio é detecção de headless; reproduzido também de IP
de datacenter. A Central de Downloads dispensa isso por ser API separada,
mas o caminho headed é o plano B se algo do RI só existir no site.

## Extração de PDF

`pypdf` sobre `data/pdfs/` + `data/cvm/` (+ `data/ri_central/`). Categorias
sem valor semântico são puladas na origem: valores mobiliários negociados
(insider trading), contratos de indenidade, escrituras de debênture,
regimentos internos, termo de emissão de nota comercial. Falha recorrente:
`Stream has ended unexpectedly` do pypdf em alguns PDFs (as duas petições
iniciais da recuperação judicial, por exemplo).

## Custo e agenda

- Embedding denso: `qwen/qwen3-embedding-8b` via OpenRouter, dimensão 4096
  (o `nomic-embed-text` do Ollama não existe no catálogo do OpenRouter —
  checado na API; e a VM não tem GPU). Qwen3 Embedding também roda no Ollama
  se um dia valer rodar local.
- Corpus completo: ~23 mil chunks. Estimar chunks e custo **antes** de
  embedar (OpenRouter cobra por token).
- Execução: timer systemd na VM (`cbrag-ingest.{service,timer}`, fora do
  repo, `OnCalendar` 11:00 UTC = 8h Brasília; a VM fica em UTC e o Brasil
  não tem horário de verão desde 2019).
- O `docker-compose.yml` tem o service `ingest` em profile separado; monta
  `data/` gravável. Container roda como uid 1000; pastas de dado criadas por
  outro dono (`ubuntu` = uid 1001 nessa VM) dão `PermissionError` — corrigir
  com `chown -R 1000:1000`.
- `coletar_dre_estruturada` refaz o histórico completo a cada run (sem
  incremental) e tem custo de rede, não de token.

## Extração genérica de fatos temporais (ideia, não construída)

Pergunta de trajetória no tempo pra assunto não previsto (dívida, litígio,
parceria) não se resolve com regex por assunto — isso é o mesmo erro de
sempre, só trocou de mecanismo. Direção reconhecida: extração de fatos por
LLM no ingest, estilo GraphRAG (schema fixo `entidade, atributo, valor,
data, documento_fonte`, custo pago uma vez por documento, consulta sobre a
tabela de fatos, não sobre texto). Refinamento do usuário: **extração
dirigida por âncora** — partir do estado atual conhecido (FRE) e caminhar
pra trás só nos documentos da categoria/cargo, até a ponta mais recente da
cadeia coincidir com a âncora (teste de sanidade de graça; extração cega não
tem). Distinção: fato discreto com valor explícito é extração pura;
tendência sobre série numérica é cálculo sobre dado estruturado
(`consultar_serie_historica_resultado`), não extração.
