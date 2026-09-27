# Ingestão: fontes, incremental e custo

Referência durável do pipeline que alimenta o índice. Detalhe de cada fonte
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

**Limite do diff por chunk**: o chunker é por offset fixo (2000/200). Edição
pequena no começo do documento desloca todo chunk depois e muda quase todo
hash sem conteúdo novo — exceto no FRE, que usa chunking por conteúdo
(abaixo).

**FRE: só o delta entra (`kc._chunk_fre`, `qs.indexar_arquivo`).** O
Formulário de Referência (~350 páginas) é republicado várias vezes por mês
com poucas mudanças.
- **Chunking por conteúdo, em cima de palavras, não de linhas** (o PDF é
  reflowed entre versões, então fronteira por linha desloca demais). A
  fronteira cai no fim de uma sentença cujo hash das últimas 4 palavras
  satisfaz uma condição (depois de 700 caracteres), então uma edição só muda
  o chunk que a contém.
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
  de data mais recente; empate, a de maior "Versão :"). Histórico do FRE não
  é consultável no índice, só o estado atual.

`scripts/testar_fre_incremental.py` reproduz o reindex sem gastar embedding
e falha se o chunker perder palavra, passar de `CHUNK_SIZE` ou embedar mais
que o delta. Rodar ao mexer no chunker do FRE: mudar `FRE_*` invalida a
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
  cresce ao longo do ano, não só fecha no fim).
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
  - "Planilha de Resultados" é `.xlsx` (pypdf não lê) e a companhia
    substitui o mesmo arquivo a cada divulgação (`file_name_original`
    "Planilha de Resultados  atual"): a API só guarda a versão corrente, e o
    Wayback e a CVM não têm cópias antigas. `baixar_ri_mziq` arquiva cada
    versão nova em `data/ri_central/_planilhas/<entrega>_<sha256[:8]>.xlsx`
    (fora do RAG). Estrutura e desenho da ingestão: seção abaixo.
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

## Planilha de Resultados (.xlsx): estrutura e desenho

**Estrutura (versão de 16/08/2026, 2T26, 2,1 MB, 11 abas).** Formato largo:
uma linha por métrica, uma coluna por período. Colunas A e B são o rótulo em
português e em inglês; o cabeçalho de período está na linha 4 (rótulo com
duas linhas, "2T26" e "2Q26" na mesma célula). Abas de série longa trazem também colunas anuais
intercaladas (`2018`, `2019`...), logo depois do 4T do ano.

| Aba | Períodos | Conteúdo |
|---|---|---|
| BP | 1T11-2T26, só trimestres | Balanço (milhões de R$) |
| DRE | 1T11-2T26 + 15 anuais | GMV (bruto/líquido, por canal), receita, EBITDA e EBITDA ajustado, e as mesmas linhas em % da receita |
| Res. Financeiro | 1T18-2T26 + 8 anuais | Detalhe do resultado financeiro (juros de dívida, CDCI, fornecedor convênio, arrendamento, FIDC) |
| Lojas | 1T09-2T26 | Aberturas, fechamentos e conversões por bandeira, área de vendas e total (mil m²), centros de distribuição |
| FluxodeCaixa | 1T16-2T26 | Fluxo de caixa (~95 linhas) |
| Conciliação FC | 1 período | Matriz pontual (2T26) por grupo de movimento, não é série |
| FC gerencial | 1T22-2T26 | Fluxo de caixa gerencial |
| Covenants | 4T23-2T26 | Dívida líquida + saldo CDCI / EBITDA ajustado 12 meses; covenant da 10ª emissão |
| Crediário | 1T21-2T26 | Carteira CDCI: em dia, vencidos por faixa de atraso, % sobre a carteira |
| Capex | 1T19-2T26 + 7 anuais | Logística, novas lojas, reforma, tecnologia, outros |

O que a torna útil: GMV, EBITDA ajustado, movimentação de lojas, covenants,
carteira do crediário e capex **não existem** no dataset ITR/DFP da CVM. O que
se sobrepõe (BP, DRE contábil, fluxo de caixa) a CVM continua sendo a fonte
oficial; a planilha serve de conferência.

**Armadilhas de leitura.** Linha de grupo (sem valor) dá contexto às linhas
seguintes: em Lojas, "Abertas/Fechadas/Convertidas" se repetem para Casas Bahia
e Ponto Frio, e só o cabeçalho de grupo distingue. `-` é vazio, não zero. A
unidade não está numa coluna: vem do rótulo ("milhões de R$"), do formato da
célula (`0.0%`) ou da aba (mil m², contagem, múltiplo). Abas terminam em
rodapés (notas de republicação, "canal descontinuado no 3T19") que não são
dado. A dimensão declarada de Lojas chega a 1.736 colunas por formatação, só
~70 têm dado. Números antigos são republicados sem aviso (nota "republicados
em 12/02/2014").

**Desenho, no mesmo padrão do FRE/DRE da CVM** (script coleta e grava JSON em
`data/cvm_estruturado/`, módulo em `src/cbrag/` lê, tool do agent consulta,
nada passa pelo embedding):
- Extração em formato longo, uma linha por (aba, grupo, métrica, período):
  `aba, grupo, metrica_pt, metrica_en, periodo ("2T26"), tipo_periodo
  (trimestre|ano), ano, trimestre, valor, unidade, versao (data de entrega da
  planilha)`. Formato longo porque cada divulgação acrescenta uma coluna, e a
  estrutura larga não cabe em JSON consultável.
- Todas as versões arquivadas entram, com `versao` na linha. A consulta usa a
  versão mais recente por padrão e sinaliza quando um valor de um período
  antigo mudou entre versões (republicação), coisa que só a planilha revela.
- Validação cruzada obrigatória na extração: receita líquida e lucro líquido
  por trimestre da aba DRE contra `dre.json` da CVM. Divergência acima de
  arredondamento (a planilha está em milhões, a CVM em milhares) barra a
  gravação e avisa em vez de publicar número errado.
- Tool nova (`consultar_indicadores_operacionais`), separada da de resultado
  financeiro: assunto (lojas, covenants, crediario, capex, gmv, ebitda
  ajustado), ano e trimestre opcionais, sempre devolvendo período e unidade
  explícitos. Docstring diz quando NÃO usar (lucro/receita contábil segue em
  `consultar_resultado_financeiro`).
- Roda no `ingest` depois de `coletar_ri_central`, só quando aparece hash novo.
  Dependência nova: `openpyxl` (leitura com `data_only=True`, valores já
  calculados no arquivo).
- Fora do desenho: Conciliação FC (matriz de um período só) e o RAG sobre o
  xlsx.

## Akamai: bloqueio é por detecção de headless, não IP

Site institucional e RI (`ri.grupocasasbahia.com.br`) usam Akamai, que
bloqueia acesso headless (`curl`, Playwright headless) mesmo com headers de
browser real, mas libera Playwright **headed** (via Xvfb, servidor sem
tela). A Central de Downloads dispensa isso por ser API separada; o caminho
headed é o plano B se algo do RI só existir no site.

## Extração de PDF

`pypdf` sobre `data/pdfs/` + `data/cvm/` (+ `data/ri_central/`). Categorias
sem valor semântico são puladas na origem: valores mobiliários negociados
(insider trading), contratos de indenidade, escrituras de debênture,
regimentos internos, termo de emissão de nota comercial. Falha recorrente:
`Stream has ended unexpectedly` do pypdf em alguns PDFs (as duas petições
iniciais da recuperação judicial, por exemplo).

## Custo e agenda

- Embedding denso: `qwen/qwen3-embedding-8b` via OpenRouter, dimensão 4096
  (o `nomic-embed-text` do Ollama, usado antes, não existe no catálogo do
  OpenRouter — checado na API; e a VM não tem GPU pra rodar local).
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
