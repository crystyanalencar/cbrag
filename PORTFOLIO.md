# Chatbot RAG institucional — Grupo Casas Bahia

Projeto de portfólio: chatbot conversacional sobre uma empresa listada na
B3 (Grupo Casas Bahia), respondendo pergunta institucional, de governança e
financeira com dado público oficial. Nasceu como monitor de preço, pivotou
depois de esbarrar em bloqueio anti-bot em todo e-commerce testado — o
pivô e as camadas que vieram depois acabaram cobrindo mais terreno técnico
que o projeto original.

## Por que virou isso

Ideia inicial: monitorar preço de um produto (iPhone 17) em 3 e-commerces.
Site institucional, RI e os 3 marketplaces bloqueiam scraping direto
(Akamai/anti-bot) — mesmo com Playwright + Chromium headless real, 403 por
IP de datacenter, não só fingerprint. Decisão: abandonar o monitor de
preço, manter o alvo (Grupo Casas Bahia, que está em **recuperação
judicial desde ago/2026** — motivo a mais pra ser um caso de uso real) e
virar chatbot institucional/financeiro.

## Camadas técnicas

### 1. Web scraping com bypass de anti-bot
- Site institucional e RI bloqueiam acesso direto (Akamai). Contorno:
  raspar via **Wayback Machine** (CDX API lista snapshot, download não
  passa pelo Akamai do site original).
- Filtro na fonte: pixel de telemetria anti-bot (`/akam/`), widget de
  cookie (dois padrões distintos, fora de `<footer>`), páginas SPA sem
  conteúdo real no HTML estático arquivado (RI carrega dado financeiro via
  JS, Wayback só salva o shell).
- Retry com backoff exponencial + **circuit breaker**: falha de conexão
  consecutiva aborta o crawl cedo em vez de esgotar retry em milhares de
  URL quando o site inteiro está fora do ar (aconteceu de verdade numa
  sessão).
- Resultado: 79 páginas HTML limpas (de 104 brutas) + 9 PDFs institucionais.
- **Decisão de arquitetura tomada com dado**: depois de comparar contra a
  CVM, o Wayback provou render pouco valor pro chatbot (nenhuma das falhas
  de retrieval encontradas veio dele) — deprioritizado, coleta não
  expandida.

### 2. Integração com API pública regulatória (CVM)
- `dados.cvm.gov.br` — sem bloqueio, fonte primária. Dois datasets
  diferentes usados pra dois fins:
  - **IPE** (fatos relevantes, comunicados, apresentações): 828+ documentos
    PDF baixados e categorizados pelo próprio campo `Categoria` da CVM.
  - **ITR/DFP** (demonstrações financeiras): dataset **estruturado** (CSV,
    uma linha por conta contábil, com início/fim de período exatos) — usado
    pra responder pergunta numérica sem depender de LLM lendo tabela de PDF
    achatada (ver seção de RAG abaixo).

### 3. Orquestração com CrewAI (Flows, não scripts soltos)
- Dois **Flows** separados, não um script sequencial nem um Flow só:
  - `IngestFlow`: roda uma vez (ou sob demanda), cada etapa um `@listen`
    Python puro — coleta Wayback → coleta CVM (IPE + ITR/DFP) → extrai PDF
    → prepara corpus → embeda.
  - `CasasBahiaRagFlow`: Flow **conversacional** (multi-turno, histórico
    nativo do CrewAI), só o chat. Separado do `IngestFlow` de propósito —
    Flow conversacional reroda `kickoff()` inteiro a cada turno; se
    ingestão e chat fossem o mesmo Flow, cada pergunta re-raspava e
    reembedava tudo de novo.
- Achado relevante lendo o código-fonte da lib (não a doc): retrieval
  automático de `Agent` só existe no caminho `Agent.execute_task()`/`Crew`
  — `Agent.kickoff()` standalone (usado dentro do Flow conversacional) não
  consulta `Knowledge` nenhuma. Retrieval acabou sendo manual, injetado no
  prompt antes de chamar o agente.

### 4. RAG com Chroma — dos limites da API alta ao controle direto do vetor
- Base: `Knowledge`/`TextFileKnowledgeSource` do CrewAI, com `Chroma`
  local (persistido dentro do repo, portável entre máquina).
- **Indexação incremental**: manifesto por arquivo (hash SHA-256 do
  conteúdo), só reembeda o que mudou — evita reprocessar ~26 mil chunks a
  cada atualização de 1 documento novo.
- **Bug real encontrado e corrigido**: config do embedder guardada numa
  `ContextVar` do Python — isolada por thread, não por processo. CrewAI
  roda cada turno de conversa numa thread; uma guarda "configura só uma
  vez" deixava thread nova cair no embedder padrão (`openai`), batendo de
  frente com o que já tava persistido (`ollama`) — erro capturado
  silenciosamente, sintoma era o chat "esquecer" o contexto depois de 2
  perguntas, sem stack trace visível.
- **Limite descoberto na API de alto nível**: `TextFileKnowledgeSource`
  nunca repassa metadata estruturada pro storage (campo marcado
  `# Currently unused` no próprio código da lib) — sem isso, não dá pra
  filtrar/ordenar por data ou categoria de verdade, só por similaridade
  semântica pura. Isso causou falha real em produção: pergunta sobre "o
  último trimestre" trazia trimestre errado porque o score semântico do
  embedder ficava quase plano nesse corpus (pouco sinal de tópico) e
  boilerplate jurídico repetido (cláusula de dividendo, procedimento de
  conselho) afogava o chunk raro com fato concreto.
- **Correção estrutural**: bypassada a API alta, escrita direta no cliente
  baixo nível do Chroma (`ChromaDBClient.add_documents`, que sim suporta
  metadata nativa) — cada chunk ganhou categoria + data real como campo
  filtrável. Busca de pergunta financeira/governança passou a filtrar por
  `where` nativo do Chroma antes da busca vetorial rodar, não só reordenar
  resultado depois.
- **Tentativa registrada e revertida com honestidade**: testou extração de
  tabela de PDF com `pdfplumber` pra resolver leitura errada de coluna
  numérica (DRE sem grade visível). Não funcionou de forma confiável —
  detecção por linha não acha a tabela, detecção por texto fragmenta a
  prosa normal da página. Decisão final foi melhor: consumir a demonstração
  financeira **já estruturada** direto da CVM (dataset ITR/DFP) em vez de
  tentar re-extrair estrutura de tabela perdida num PDF.

### 5. LLM: geração via API (Gemini), embedding local (Ollama)
- Geração trocada de LLM local (`gemma4`, Ollama) pra **Gemini 2.5 Flash**
  — motivo: GPU local (6GB VRAM) disputava recurso entre geração e
  embedding rodando juntos; tirar a geração da GPU resolveu sem custo de
  reprocessamento (embedding não muda).
- Backstory do agente ajustado a partir de bug real observado: já teve
  resposta citando fonte/data em toda pergunta (irritava o usuário) e já
  confundiu trimestre isolado com acumulado de semestre lendo tabela mal
  formatada — os dois ajustados via engenharia de prompt depois de
  reproduzir o problema, não preventivamente.

### 6. Resiliência e observabilidade
- **Circuit breaker** no crawler (seção 1) — falha externa não trava o
  pipeline inteiro.
- **Manifesto auditável**: cada arquivo indexado grava hash + nº de chunks
  + timestamp — dá pra saber o que entrou no índice e quando sem consultar
  o vetor diretamente, e sem esperar o lote inteiro terminar pra persistir
  progresso (uma interrupção no meio não perde o que já foi processado).
- **Indexação em fases**: dataset grande demais pra reprocessar tudo de
  uma vez sob rate limit de API paga — arquivo mais recente primeiro,
  corte por ano opcional, backfill incremental depois (upsert idempotente,
  não duplica).
- **Diagnóstico reproduzido antes de corrigir**: cada bug de retrieval
  (recência, boilerplate afogando busca, tabela mal lida) foi isolado num
  script mínimo que reproduzia o sintoma fora do chat antes de qualquer
  fix — evita corrigir o sintoma errado.

### 7. Decisões deliberadas de não fazer (e por quê)
- Não trocar de vector DB (Pinecone etc.) — corpus pequeno o bastante pro
  Chroma local aguentar; trocar adicionaria custo/infra sem ganho real
  nesse porte.
- Não tentar "entender" com NLP genérico qual período uma pergunta como
  "trimestre anterior" quer dizer — não existe lib de data que saiba o que
  é "1T26" fiscal da CVM. Mais simples e robusto: sempre calcular e
  entregar os períodos mais prováveis (último trimestre, anterior, mesmo
  período ano passado) já comparados, e deixar o LLM mapear a linguagem
  natural pro número certo — o LLM já faz bem esse mapeamento quando o
  fato é explícito, não precisa reimplementar isso em Python.
- Não expandir a coleta do Wayback depois de medir que renderia pouco (ver
  seção 1) — decisão baseada em dado observado (nenhuma falha real veio de
  lá), não em suposição.
- Categoria de documento CVM sem valor semântico (insider trading, contrato
  de indenidade, escritura de debênture) filtrada na extração — reduz
  ruído no corpus sem perder cobertura do que importa pro chatbot.

## Roadmap (identificado, não implementado ainda)
- Variação % entre período (trimestre vs. trimestre, ano vs. ano) — dado
  já disponível no dataset estruturado da CVM, falta só o cálculo.
- Fallback de LLM (Groq) pra quando a API principal responder 503/
  sobrecarregada.
- Deploy do piloto: VPS pequena + túnel reverso (Cloudflare Tunnel) se
  optar por manter o vetor local num homelab em vez de nuvem — sem GPU
  necessária pra servir (geração e embedding via API).

## Stack

`CrewAI` (Flow + Knowledge) · `ChromaDB` · `Ollama` (embedding local,
`nomic-embed-text`) · `Gemini 2.5 Flash` (geração) · `requests` +
`BeautifulSoup` (scraping) · `pypdf` (extração de PDF) · `uv` (dependência
e ambiente) · dataset público CVM (`dados.cvm.gov.br`, IPE + ITR/DFP) ·
Wayback Machine CDX API.

## O que fica de lição pra outro projeto

- Testar hipótese barata antes de assumir: extração de tabela de PDF
  parecia a solução óbvia, e não era — a fonte estruturada já existia e
  ninguém tinha checado antes de partir pra engenharia de extração.
- Bug de concorrência (ContextVar por thread) não aparece em teste de 1
  pergunta — só se manifestou com múltiplos turnos de conversa, típico de
  sistema conversacional multi-turno.
- API de alto nível de framework esconde limitação que só aparece lendo o
  código-fonte instalado, não a documentação pública.
