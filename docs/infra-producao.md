# Infra de produção e acesso

Referência durável de como a produção é montada e das armadilhas de
operação. Config de máquina (Caddyfile, timer systemd, `.env`), rede e
postura de segurança ficam fora do repo — quem opera a própria cópia decide
a própria segurança.

## Topologia

Cloudflare (proxy, DNS) → VM Oracle (ARM64 Ampere, Ubuntu, 2 vCPU/11GB, sem
GPU) → Caddy (reverse proxy, `/etc/caddy/Caddyfile`, fora do repo) →
containers via `docker-compose.yml`: `chat` (Chainlit), `qdrant` (servidor,
sem porta pública) e `ingest` (profile separado). Rotas: `/` landing e
`/docs` (estáticos servidos pelo Caddy a partir de `site/`), `/chat` app.

- Chainlit atrás de sub-path: `CHAINLIT_ROOT_PATH=/chat` (suporte oficial,
  `APIRouter(prefix=root_path)`), lido pelo compose com default vazio.
- Caddy precisa de hostname explícito no site block pro `tls internal`
  emitir certificado (`:443` sozinho falha o handshake).
- O Caddyfile guarda o path do checkout. Mover/renomear a pasta do app
  derruba `/` e `/docs` (404) até o path ser trocado e o Caddy recarregado.
- `.env` da VM: conferir que o arquivo termina em newline antes de `>>`
  (uma linha foi colada na da chave).

## Onde mora o índice (e o que a máquina de dev não tem)

O Qdrant é servidor (container `qdrant` do compose, só na VM, sem porta
pública) e é a **única** fonte da verdade do índice:
corpus embedado com Qwen3 (4096 dimensões). A ingestão roda na VM
(`cbrag-ingest.timer` -> `ingest`), incremental por manifesto. A máquina de
dev não tem Qdrant server: sem `QDRANT_URL`, `qdrant_store.cliente()` cai no
modo embedded e abre `data/knowledge_storage/qdrant`, sobra de 2026-09-10 do
tempo do nomic (768 dimensões) — nunca foi nem é o índice de produção.
Foi isso que fez um embedding "local" da Central falhar (`could not
broadcast (4096,) into (768,)`), e por isso `indexar_arquivo` e
`reindexar_bm25` exigem `QDRANT_URL` (`exigir_servidor`; testes em memória
liberam com `CBRAG_PERMITIR_QDRANT_LOCAL=1`). O que dá pra fazer na máquina
de dev é preparar e conferir o texto (`extrair_texto_pdfs`,
`preparar_knowledge`, contagem de chunks/custo), não embedar.

**`chat` e `ingest` são imagens separadas** (`cbrag-chat` e `cbrag-ingest`,
ambas `build: .`). `docker compose up -d --build chat` reconstrói só a do
chat; o `ExecStart` do timer não tem `--build`, então a ingestão continua
rodando o código velho até alguém rodar `docker compose --profile ingest
build ingest`. Todo deploy que mexe em `scripts/` ou no pipeline precisa dos
dois builds.

## Permissões de volume

Dado fica em disco externo montado na VM: `docker-compose.yml` usa bind
mount relativo dentro do checkout, não volume nomeado (que cairia no disco
de sistema em `/var/lib/docker/volumes`).

O `Dockerfile` cria o usuário do container com uid 1000. Nessa VM o usuário
`ubuntu` é uid 1001, então pasta criada por ele (`cvm_estruturado`,
`knowledge`, `cdx`) ou pelo Docker como `root` (`logs`, `fastembed_cache`)
não é gravável: `PermissionError` no primeiro turno de chat ou na ingestão.
Fix: `chown -R 1000:1000`. Teste HTTP simples não pega isso, só um turno real.

`qdrant/.lock` de execução embedded antiga (dono root) precisa ser removido
manualmente; o Qdrant recria.

## Ambiente de teste do Playwright headed

Pra o caminho headed do Akamai (`ingestao.md`): `xvfb` + Node ≥20 (via
NodeSource; o apt da VM só tinha 18) + `npx playwright install chromium`.
