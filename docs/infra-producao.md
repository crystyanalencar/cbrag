# Infra de produção e acesso

Referência durável de como a produção é montada e das armadilhas de
operação. Sem status — o que está aplicado ou pendente fica no `STATE.md`.
Nada aqui contém segredo nem IP; o que é config de máquina fica fora do repo
(Caddyfile, timer systemd, `.env`).

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
pública, acesso por Tailscale) e é a **única** fonte da verdade do índice:
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
build ingest`. Foi assim que o `cbrag-ingest` ficou 36h atrás do `chat`
depois de um deploy: a coleta nova do bucket 2025 da Central só entrou porque
a imagem foi reconstruída antes da ingestão manual. Todo deploy que mexe em
`scripts/` ou no pipeline precisa dos dois builds.

## Rede e segurança

- Duas camadas de firewall: Security List da OCI e `iptables` da VM, ambos
  restringindo TCP/443 aos ranges oficiais da Cloudflare
  (`cloudflare.com/ips-v4`). Defesa em profundidade: não confiar só na
  Security List.
- SSH só por Tailscale (`100.64.0.0/10` no iptables). No Tailscale, `ping`
  passa por ser plano de controle, mas SSH é dado, filtrado à parte pela
  ACL: node novo precisa estar na lista de hosts da ACL, senão dá timeout
  mesmo com ping respondendo.
- **TLS "Full", não "Full Strict"**: a Cloudflare aceita o certificado
  autoassinado do Caddy sem validar CA. Full Strict pede Cloudflare Origin CA
  Certificate + `tls <cert> <key>` no Caddyfile.
- Firewall cobre só IPv4 da Cloudflare; hoje irrelevante (só há registro
  `A`), mas vira buraco se um `AAAA` for criado.
- Docker publica porta por fora do `ufw` (regra própria de iptables): `ufw`
  não protege container em `0.0.0.0`.
- Sem auth e sem histórico de chats **de propósito** (portfólio). Proteção de
  custo: limite de gasto no preset do OpenRouter (quando estoura, o chat
  responde "provedor indisponível"; subir no painel) e `allow_origins`
  restrito ao domínio. Rate limit da Cloudflare no plano free só permite
  janela de 10s: overengineering pra um domínio de baixo tráfego, e uma regra
  mal calibrada bloqueou o próprio dono (`Error 1015`). Turnstile exige
  código.
- Container do chat: não-root (uid 1000), sem docker socket, mounts `:ro`
  onde dá, 2 CPU/2GB.
- `docker compose config` **expande `env_file` e imprime a chave real**.
  Regra: nunca imprimir objeto/config que possa conter segredo (`repr()` de
  `LLM` também inclui `api_key`). Para validar sintaxe do compose, usar
  `docker compose config -q`.

## Permissões de volume

O `Dockerfile` cria o usuário do container com uid 1000. Nessa VM o usuário
`ubuntu` é uid 1001, então pasta criada por ele (`cvm_estruturado`,
`knowledge`, `cdx`) ou pelo Docker como `root` (`logs`, `fastembed_cache`)
não é gravável: `PermissionError` no primeiro turno de chat ou na ingestão.
Fix: `chown -R 1000:1000`. Teste HTTP simples não pega isso, só um turno real.

`qdrant/.lock` de execução embedded antiga (dono root) precisa ser removido
manualmente; o Qdrant recria.

## Hospedagem: por que Oracle e não homelab

Homelab local ficou como plano A por nobreak e custo zero, mas a VM Oracle
roda 24/7 e tem IP público fixo; a instância local foi desligada (superfície
exposta à toa) e o Funnel do Tailscale que a publicava, removido. O homelab
fica como backup frio. Decisões que ficaram:

- Padrão de dado em disco externo de app CasaOS: bind mount relativo dentro
  do checkout, não volume nomeado (que cai no disco de sistema em
  `/var/lib/docker/volumes`).
- Sem GPU na VM, então embedding é via API (ver `ingestao.md`).
- Tunnel (Cloudflare, precisa de domínio) e Funnel (Tailscale) convivem;
  Funnel usa porta própria quando a 443 já tem outro serviço.

## CrewAI AMP (avaliado e descartado pra agendamento)

Não tem cron/scheduler nativo (só triggers por evento: Gmail, Calendar, Slack,
webhook). Tem plano free (50 execuções/mês; caberia a ingestão diária), mas
migrar exigiria expor o Qdrant publicamente (hoje só Tailscale), trocando
rede fechada por segredo público só pra ganhar um dashboard de auditoria.

## Acesso remoto (`ssh-mcp`)

Config em `%APPDATA%\ssh-mcp\config.toml` (fora do repo). O prompt de
aprovação interativa (elicitation) **não funciona neste cliente MCP** — nega
sempre; por isso só a policy de role/grupo protege (`approvalPolicy = "auto"`
no profile `oracle`). Comando multi-linha (heredoc, `cmd1\ncmd2`) não separa
certo: um comando por chamada. `sftp-upload-file` não funciona no Windows:
gerar o `.tar.gz` local e usar `scp` (pipe do PowerShell corrompe binário).
Registrar deploy key direto do host de origem: o filtro de alta entropia do
harness redige qualquer chave, até pública.

## Ambiente de teste do Playwright headed

Pra o caminho headed do Akamai (`ingestao.md`): `xvfb` + Node ≥20 (via
NodeSource; o apt da VM só tinha 18) + `npx playwright install chromium`.
