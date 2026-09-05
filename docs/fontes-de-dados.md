# Fontes de dados — Grupo Casas Bahia

## Bloqueio Akamai no site institucional e no RI

`grupocasasbahia.com.br` e `ri.grupocasasbahia.com.br` (mesmo WAF Akamai,
cookie `akavpau_*` em ambos) retornam 403 pra qualquer acesso programático
testado:
- `requests`/`curl` com header de browser real: 403.
- Playwright + Chromium real, headless, com `user_agent`/`locale` de
  browser: 403 também.

O bloqueio não é (só) fingerprint de TLS/JS — é reputação de IP/datacenter.
Rodando de um ambiente cloud/sandbox, qualquer requisição cai no challenge
antes de chegar no conteúdo. Proxy residencial ou serviço anti-bot dedicado
resolveriam, mas são custo recorrente pra pegar conteúdo que, no fim, tem
fonte pública alternativa (ver abaixo) — por isso não foi o caminho
escolhido.

## Contorno: Wayback Machine

`web.archive.org` não passa pelo Akamai do site original — o crawler do
Internet Archive já visitou essas páginas no passado e serve o snapshot
salvo, sem re-requisitar o site ao vivo.

- CDX API (`web.archive.org/cdx/search/cdx?url=<domínio>*&output=json
  &filter=statuscode:200&collapse=urlkey`) lista todas as URLs arquivadas
  de um domínio, com timestamp do snapshot mais recente por URL
  (`collapse=urlkey` já deduplica).
- Snapshot em modo raw (sem toolbar da Wayback injetada) é
  `web.archive.org/web/<timestamp>id_/<url original>`.
- Internet Archive tem instabilidade frequente (erros de conexão
  recusada, página "Temporarily Offline") — não é um sinal de bloqueio,
  é o serviço mesmo caindo às vezes. Retry com backoff resolve a maioria;
  rodar o crawler de novo (idempotente) resolve o resto.

## Contorno pra dados financeiros/RI: CVM

Toda companhia aberta brasileira é obrigada a publicar documentos na CVM —
essa é uma fonte mais completa e sempre atualizada que o site de RI da
empresa (que só teria a foto congelada do Wayback). Sem bloqueio de
nenhuma espécie.

- `dados.cvm.gov.br`: portal de dados abertos (CKAN). Dataset relevante:
  `cia_aberta-doc-ipe` (documentos periódicos e eventuais — fatos
  relevantes, apresentações a analistas, avisos aos acionistas, políticas
  etc.), um ZIP por ano em
  `dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_<ano>.zip`.
  CSV dentro do zip é `;`-separado, encoding `latin-1`.
- Cada linha do CSV tem `Codigo_CVM` (código da empresa) e `Link_Download`
  (URL direta em `rad.cvm.gov.br/ENET/frmDownloadDocumento.aspx...`) — esse
  link devolve o PDF direto, sem exigir sessão/login.
- Código CVM da Grupo Casas Bahia: **6505**. CNPJ: 33.041.260/0652-90.
  Aparece no CSV como `GRUPO CASAS BAHIA S.A.` (nome atual; documentos mais
  antigos podem estar sob o nome anterior, Via Varejo).
