# cbrag

![Python](https://img.shields.io/badge/python-3.10%20--%203.13-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Status](https://img.shields.io/badge/status-em%20produção-brightgreen)

Chatbot RAG conversacional sobre o Grupo Casas Bahia (institucional, RI,
governança e financeiro), construído com [CrewAI](https://crewai.com) sobre
documentos públicos da CVM e do site institucional da empresa — não sobre
memória do modelo. Em produção: **[cbrag.ialencar.com.br](https://cbrag.ialencar.com.br)**.

História de como o projeto chegou nesse formato, contada em prosa:
[cbrag.ialencar.com.br/docs](https://cbrag.ialencar.com.br/docs). Este
documento é a referência técnica.

## Por que este projeto é interessante

- **Retrieval híbrido medido, não por impressão manual** — todo ajuste de
  busca (BM25 puro → denso → híbrido, pesos diferentes) validado contra um
  golden set com recall@8 e MRR (`tests/golden_retrieval.json`).
- **Dado exato não passa por busca semântica** — resultado financeiro
  trimestral e composição de conselho vêm de dataset oficial da CVM
  (tool determinística), não de PDF lido por similaridade.
- **Ingestão incremental de verdade** — hash SHA-256 por arquivo evita
  reprocessar (e reembedar, com custo real de API) documento que não mudou.
- **Em produção real**, 24/7, atrás de Cloudflare, com ingestão diária
  automática — não é só `docker run` local.

## Status

Em produção, rodando 24/7 numa VM Oracle Cloud, atrás de Cloudflare. Chat e
landing no ar; `/docs` do site é a narrativa do projeto (link acima), não
documentação de API. Ingestão diária roda sozinha via timer systemd,
incremental (dia sem novidade na CVM = sem custo de embedding).

## Arquitetura

Dois Flows CrewAI independentes, sem sobreposição de responsabilidade: um
coleta e indexa o dado (roda 1x/dia, sem LLM até o embedding), o outro
responde ao usuário (roda por turno de chat, decide sozinho qual tool
chamar).

```
IngestFlow (agendado, systemd timer, 8h Brasília)
─────────────────────────────────────────────────
  coletar_wayback ──▶ coletar_cvm ──▶ coletar_dre_estruturada
        │ (não-bloqueante:              │
        │  falha não trava o resto)     ▼
        │                          extrair_pdfs
        │                                │
        └────────────────────────────────┼──▶ preparar_knowledge
                                          │         │
                                          │         ▼
                                          │   embutir_conhecimento
                                          │   (Qdrant: denso + BM25,
                                          │    incremental via manifesto
                                          │    hash SHA-256 por arquivo)
                                          ▼
                                data/cvm_estruturado/*.json
                                (DRE, conselho — sem passar por LLM)


CbragFlow (conversacional, 1 por sessão de chat)
─────────────────────────────────────────────────
  usuário pergunta
        │
        ▼
  Agent (OpenRouter, tool-calling) ──▶ decide qual tool chamar
        │
        ├─▶ consultar_resultado_financeiro     (DRE estruturada CVM)
        ├─▶ consultar_serie_historica_resultado (DRE estruturada CVM)
        ├─▶ consultar_composicao_conselho       (FRE estruturado CVM)
        ├─▶ consultar_documentos_recentes       (metadado + data_ordinal)
        └─▶ buscar_conhecimento                 (Qdrant: híbrido BM25+denso)
        │
        ▼
  resposta (Chainlit, streaming) + evento gravado em data/logs/eventos.jsonl
```

A decisão de separar "pergunta de estado atual/número exato" (tools
estruturadas, sem ambiguidade) de "pergunta narrativa" (busca híbrida) é o
núcleo do design — evita o erro comum de tratar toda pergunta como busca
semântica, inclusive pra dado que muda (conselho, resultado trimestral) e
onde acertar a fonte importa mais que "parecer relevante".

## Stack

| Categoria | Tecnologia |
|---|---|
| Orquestração de agente | CrewAI (Flow conversacional + `Agent.kickoff()` com tool-calling) |
| Geração (LLM) | OpenRouter — modelos configurados no painel, sem mudar código (hoje: NVIDIA Nemotron 3.5 Lightning, Google Gemma 4 26B A4B, Google Gemini 3.1 Flash Lite) |
| Embedding denso | Qwen3 Embedding 8B via OpenRouter |
| Retrieval | Qdrant — vetor denso + esparso BM25 (fastembed, stemmer PT) por chunk, busca híbrida ponderada (RRF) |
| Dados estruturados | Datasets abertos da CVM (FRE, ITR/DFP) — DRE trimestral e composição de conselho como tools determinísticas |
| Interface | Chainlit (chat web) |
| Observabilidade | Tracing local próprio (`crewai.events` + JSONL), sem dependência de serviço externo |
| Deploy | Docker Compose, Caddy (reverse proxy + TLS), Oracle Cloud (VM ARM64) |
| Borda / segurança de rede | Cloudflare, Tailscale |
| Agendamento | systemd timer (ingestão diária) |

## Funcionalidades implementadas

**Retrieval híbrido, medido por golden set** — todo ajuste de busca
(BM25 puro → denso → híbrido, pesos diferentes) foi validado contra um
conjunto de perguntas com documento esperado anotado (`tests/golden_retrieval.json`),
medindo recall@8 e MRR — não por impressão manual.

**Dados estruturados como tool, não como busca** — resultado financeiro
trimestral e composição de conselho/diretoria vêm de dataset oficial da CVM
(ITR/DFP e FRE), não de leitura de PDF por similaridade. Elimina ambiguidade
de "qual número é esse" que busca semântica não resolve bem.

**Consciência temporal explícita** — o agente recebe a data de hoje
dinamicamente a cada chamada (`inject_date`), não fixada na inicialização;
sem isso, "até hoje"/"ano atual" é ambíguo pro modelo.

**Ingestão incremental de verdade** — manifesto por hash SHA-256 por
arquivo (`data/knowledge_storage/manifest.json`) evita reprocessar (e
reembedar, com custo real de API) documento que não mudou.

**Interface conversacional com cancelamento correto** — stop manual
(`@cl.on_stop`) limpa o loader e marca o turno como cancelado antes de
gravar no histórico, evitando que uma resposta que ninguém viu apareça
depois.

**Tom cuidadoso em tema sensível** — o backstory do agente proíbe
destaque/negrito na menção ao processo de recuperação judicial da empresa;
o dado não é omitido, só não é dramatizado.

## Modelo de dado

- `data/cvm_estruturado/` — DRE (ITR/DFP) e FRE (conselho), JSON gerado
  direto do dataset CVM, sem passar por chunking nem embedding.
- `data/knowledge/` + `_metadata.json` — corpus textual (HTML institucional
  via Wayback, PDFs extraídos) com metadado por documento (categoria CVM,
  `data_ordinal` pra ordenação por recência).
- `data/knowledge_storage/manifest.json` — hash por arquivo, controla o
  incremental do embedding.
- Qdrant — 1 coleção, 2 vetores por ponto (denso Qwen3 + esparso BM25),
  payload com `arquivo`/categoria pra filtro.

## Estrutura de diretórios

```
src/cbrag/
├── main.py              # CbragFlow — Flow conversacional, agent, tool-calling
├── ingest_flow.py        # IngestFlow — pipeline de coleta + indexação
├── chainlit_app.py       # interface web (Chainlit)
├── qdrant_store.py        # índice Qdrant (denso + BM25, busca híbrida)
├── knowledge_config.py    # caminhos, chunking, manifesto incremental
├── dados_financeiros.py   # tool: DRE estruturada CVM
├── composicao_conselho.py # tool: conselho/diretoria (FRE)
├── documentos_recentes.py # tool: ordenação por recência (data_ordinal)
├── local_tracing.py       # observabilidade local (JSONL)
└── tools/rag_tools.py      # wrappers @tool expostos ao agent

scripts/                   # coleta (Wayback, CVM), extração de PDF,
                            # preparo do corpus, avaliação de retrieval
tests/golden_retrieval.json # golden set (pergunta → documento esperado)
site/                      # landing + docs estáticos (servidos pelo Caddy)
data/                      # corpus e índice — gerado, fora do git
```

## Instalação e configuração

Requisitos: Python >= 3.10 < 3.14, [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

`.env`:

```
OPENROUTER_API_KEY=...          # geração (e embedding, se não houver a chave abaixo) — obrigatório
OPENROUTER_EMBED_API_KEY=...    # opcional — chave só do embedding (limite de crédito próprio)
QDRANT_URL=...                  # opcional — usa embedded local sem isso
QDRANT_API_KEY=...              # opcional
```

## Como executar

```bash
uv run ingest    # coleta CVM/site, prepara o corpus e indexa no Qdrant (incremental)
uv run chat      # REPL do chatbot no terminal
uv run chainlit run src/cbrag/chainlit_app.py   # interface web
```

A ingestão precisa rodar antes do chat funcionar — sem índice não há o que
buscar. Em corpus grande, o embedding inicial (sem manifesto prévio) pode
levar horas.

## Como executar testes / avaliação

```bash
uv run python scripts/avaliar_retrieval.py --modo chat            # recall@8 / MRR no golden set
uv run python scripts/avaliar_retrieval.py --modo qdrant-hibrido --verboso
uv run python scripts/backfill_qdrant.py            # reindexa sem recoletar
uv run python scripts/backfill_qdrant.py --so-bm25  # recalcula só o vetor esparso
```

Não há suíte de testes unitários tradicional — a validação central do
projeto é o golden set de retrieval acima, porque o risco real deste tipo
de sistema é resposta errada por recuperação errada, não exceção de código.

## Componentes principais

- **`CbragFlow` / `main.py`** — Flow conversacional de 1 agent
  (`Agent.kickoff()` standalone, sem `Crew`/`Task`); o LLM decide sozinho
  qual tool chamar por pergunta.
- **`IngestFlow` / `ingest_flow.py`** — pipeline determinístico (sem LLM
  até a etapa de embedding), agendado via systemd timer na VM.
- **`qdrant_store.py`** — abstrai o índice: cria os dois vetores por chunk,
  expõe busca BM25 pura, densa e híbrida ponderada (`MODO_CHAT`,
  `PESO_BM25_CHAT`).
- **`local_tracing.py`** — escuta eventos do `crewai_event_bus` (tool
  call, resposta final) e grava em JSONL local — permite reconstruir
  qualquer sessão sem depender de serviço de tracing externo. Dados de usuário não são coletados.

## Limitações atuais

- `coletar_dre_estruturada` refaz o histórico completo da DRE a cada
  execução (sem incremental) — só `embutir_conhecimento` tem manifesto
  hoje.
- Coleta do site institucional depende do Wayback Machine como contorno
  (o site oficial bloqueia scraping direto via Akamai) — instável quando o
  Wayback está fora do ar (não bloqueia o pipeline, só reduz o que é
  coletado naquele dia).
- Sem limpeza de chunk órfão nem detecção de arquivo removido no
  incremental — só cobre arquivo novo/alterado.

## Roadmap

- Fact-checker multi-agent (segunda passada auditando a resposta contra o
  chunk fonte).
- Separar a etapa de ingestão para Airflow.

## Segurança

- `allow_origins` do CORS restrito ao domínio de produção.
- Projeto não usa nem versiona nenhum asset de marca (logo) da Grupo Casas
  Bahia — ícone da interface é original, criado pra este projeto.

Este é um projeto de portfólio pessoal, sem vínculo, chancela ou afiliação
com o Grupo Casas Bahia. Usa exclusivamente dados públicos (CVM, site
institucional via Wayback Machine).

## Licença

[MIT](LICENSE).

## Autoria

Crystyan Alencar
