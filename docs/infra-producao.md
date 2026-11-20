# Infra de produção

Como a produção é montada, no nível do que está no repo. Config de máquina,
rede e postura de segurança ficam fora dele: quem opera a própria cópia
decide a própria segurança.

## Topologia

Cloudflare (proxy, DNS) → VM ARM64 sem GPU → Caddy (reverse proxy) →
containers via `docker-compose.yml`: `chat` (Chainlit), `qdrant` (servidor,
sem porta pública) e `ingest` (profile separado). Rotas: `/` landing e
`/docs` (estáticos servidos pelo Caddy a partir de `site/`), `/chat` app.

- Chainlit atrás de sub-path: `CHAINLIT_ROOT_PATH=/chat` (suporte oficial,
  `APIRouter(prefix=root_path)`), lido pelo compose com default vazio.
- Caddy precisa de hostname explícito no site block pro `tls internal`
  emitir certificado (`:443` sozinho falha o handshake).

## Onde mora o índice

O Qdrant é servidor (container `qdrant` do compose, sem porta pública) e é a
**única** fonte da verdade do índice: corpus embedado com Qwen3 (4096
dimensões). A ingestão roda no mesmo host, agendada, incremental por
manifesto. Sem `QDRANT_URL`, `qdrant_store.cliente()` cai no modo embedded e
abre `data/knowledge_storage/qdrant`, um índice local que não é o de
produção. Foi isso que fez um embedding "local" falhar (`could not broadcast
(4096,) into (768,)`), e por isso `indexar_arquivo` e `reindexar_bm25`
exigem `QDRANT_URL` (`exigir_servidor`; testes em memória liberam com
`CBRAG_PERMITIR_QDRANT_LOCAL=1`). Fora do servidor dá pra preparar e
conferir o texto (`extrair_texto_pdfs`, `preparar_knowledge`, contagem de
chunks/custo), não embedar.

**`chat` e `ingest` são imagens separadas** (`cbrag-chat` e `cbrag-ingest`,
ambas `build: .`). `docker compose up -d --build chat` reconstrói só a do
chat; a ingestão agendada não reconstrói, então continua rodando o código
velho até alguém rodar `docker compose --profile ingest build ingest`. Todo
deploy que mexe em `scripts/` ou no pipeline precisa dos dois builds.

## Versões, prontidão e limites

- **Versões fixas, sem `latest`.** A imagem do Qdrant e a do uv usada no
  `Dockerfile` têm versão fixa. Atualizar o Qdrant migra o formato do storage
  sem volta. Por isso a troca de versão é deliberada e nunca pode ir para
  versão anterior à que já abriu o volume (conferir com `GET /` no
  container antes de mudar a tag). Mantenha a versão do `qdrant-client` do
  `uv.lock` compatível com a do servidor.
- **Healthcheck por `/readyz`.** A imagem do Qdrant não traz `curl` nem
  `wget`, então o teste faz a requisição HTTP pelo `/dev/tcp` do bash.
  `chat` e `ingest` usam `depends_on: condition: service_healthy`, ou seja,
  esperam o Qdrant responder e não apenas o processo subir.
- **Limites de recurso nos três serviços.** Chat 2 CPU/2G, qdrant 1 CPU/1G,
  ingest 1.5 CPU/3G. O limite do ingest existe para que a extração de um PDF
  grande não esgote a memória do host e derrube o chat. O efeito colateral é
  que, se um PDF passar do limite, o kernel mata o container inteiro (exit
  137) e o run do dia aborta sem resumo. O try/except por arquivo não pega
  esse caso.

## Permissões de volume

`docker-compose.yml` usa bind mount relativo dentro do checkout, não volume
nomeado. O `Dockerfile` cria o usuário do container com uid 1000: pasta de
`data/` criada no host por outro uid, ou pelo Docker como `root`, não é
gravável e dá `PermissionError` no primeiro turno de chat ou na ingestão
(fix: `chown -R 1000:1000`). Teste HTTP simples não pega isso, só um turno
real (inclusive o endpoint `/api/chat` do widget, que também escreve em
`data/logs/eventos.jsonl` via `local_tracing`).

## Ambiente de teste do Playwright headed

Pra o caminho headed do Akamai (`ingestao.md`): `xvfb` + Node ≥20 +
`npx playwright install chromium`.
