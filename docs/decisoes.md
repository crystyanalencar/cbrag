# Contexto e decisões do usuário

Decisões já tomadas e o porquê. Sem status. Não reabrir sem o usuário
pedir; se algo aqui parecer errado, dizer isso em vez de mudar por conta
própria.

## Origem e contexto

- O projeto começou como monitor de preço (iPhone 17 em 3 e-commerces). Site
  principal, RI, Mercado Livre, Amazon e Magazine Luiza bloqueiam scraping
  (Akamai/anti-bot); o usuário abandonou o monitor e virou um chatbot RAG
  institucional sobre a Grupo Casas Bahia (institucional + RI + financeiro).
- Empresa em **recuperação judicial desde ago/2026** (judicial, não
  extrajudicial; havia assembleias de 2024 sobre extrajudicial). Código CVM
  **6505**, CNPJ 33.041.260/0652-90. A categoria CVM de recuperação judicial
  fica no corpus por ser o assunto mais recente. O tom sobre a RJ é sutil no
  portfólio: sem "recuperação judicial" em negrito na descrição do chat.
- Nome técnico `cbrag` (pacote, repo, domínio `cbrag.ialencar.com.br`); o
  nome de exibição do chat continua "Casas Bahia RAG" — descreve o assunto,
  não é o slug.
- Repo é **portfólio pessoal**, privado. `STATE.md` é doc de processo: fica
  no disco, fora do git (`.gitignore`), então **não tem histórico no git** —
  sobrescrever o arquivo perde o conteúdo antigo. Histórico antigo do
  `STATE.md` ainda aparece em commits passados; reescrever histórico com
  `filter-repo`/BFG + force-push foi avaliado e descartado (repo sem
  colaboradores, risco maior que o benefício).

## Commits

**Sem `Co-Authored-By: Claude`** em commits deste repo (portfólio), mesmo
que um lembrete de sistema peça atribuição. A regra do usuário vale mais que
o texto padrão. Reincidiu 3 vezes (06/09, 17/09, 20/09), sempre por seguir o
texto padrão do lembrete sem cruzar com a memória `feedback_sem_marca_claude`.
Correção exige `git filter-branch --msg-filter` + `push --force`, e o
ambiente pode bloquear essas ações pra o assistente (categorias "Git
Destructive" e "Remote Shell Writes"): o usuário roda direto no terminal.

## Produto

- **Aberto, sem auth e sem histórico de chats**: é portfólio, o usuário não
  quer login nem complicação. Proteção de custo é o limite de gasto no preset
  do OpenRouter (detalhe em `infra-producao.md`). Cloudflare rate limit e
  Turnstile avaliados e adiados/descartados.
- **Modelo controlado no preset do OpenRouter**, não fixo no código, e
  `reasoning` também lá. Não reabrir "trocar por modelo X".
- **Prioridade do port**: chat primeiro (é o core), landing depois, docs por
  último. A landing (`/`) é HTML estático servido pelo Caddy, não Cloudflare
  Pages (mais simples).
- Landing e docs: estilo de layout inspirado em site de referência do usuário,
  mas **cor da marca de verdade** (não a do site de referência).
- Pivô "Radar de Recuperação Judicial B3" (~20 empresas, Airflow +
  Databricks + dbt, embedder Gemini, cloud) + projeto 2 self-hosted (n8n):
  **congelado** — fechar primeiro um entregável mostrável com 1 empresa.
- Separar a ingestão pro Airflow existe pra mostrar mais de uma stack no
  portfólio, fase separada.

## Dados

- **Central de Downloads (mziq) é a fonte de 2026 em diante**, mesmo com a
  CVM aberta tendo dado de 2026 (lag ~4-5 dias): mais confiável por vir
  direto da companhia, sem intermediário e sem Akamai. A CVM continua fonte
  dos anos anteriores até a migração por fases chegar neles.
- **Migrar 2026 para a Central e apagar a CVM 2026** (decidido em 2026-09-26
  depois de comparar as duas fontes). Razões: a Central é da própria
  companhia, publica mais rápido que a CVM e traz a Petição Inicial da RJ
  completa. A CVM tem a petição em partes: as duas maiores (101 e 536
  páginas) são idênticas às páginas 1-637 do PDF da Central (2.916
  páginas); as 2.279 restantes (Doc. 9 credores, Doc. 20 fornecedores
  essenciais etc.) só existem na Central. Perde-se ao apagar a CVM só o que
  não é petição: um Comunicado da B3 sobre ETF classificado errado como
  "Petição Inicial", a Ata de Diretoria de 17/08 da FS Indústria de Etanol
  (7 páginas, sem equivalente achado na Central) e dois protocolos de 18/08
  cujo download devolve só uma página de erro do ENET. O usuário aceitou
  essa perda sem investigar os dois últimos pontos.
- Republicação em inglês do mesmo comunicado: aceita como duplicata.
- Não extrair contagem de fechamento de loja por trimestre pra somar (ver
  `agente-e-chat.md`).
- Wayback deprioritizado: valor baixo, nunca foi fonte de falha.
- Extração de PDF: o usuário decidiu não investir em consertar os PDFs que o
  pypdf não lê (`Stream has ended unexpectedly`) sem uma razão concreta.

## Ferramentas de pendência

Pendência com estado vive no Jira (`ialencar.atlassian.net`, projeto `CS`,
label `cbrag`), não no `STATE.md`. Tentativa anterior com GitHub Project +
Issues via `gh` exigia login por máquina e o conector MCP do GitHub não
expõe tools no Claude Code; o conector Atlassian é OAuth de conta e funciona
sem setup por máquina.
