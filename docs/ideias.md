# Resumo da sessão — Definição do projeto de portfólio (set/2026)

_Registro do que foi discutido e decidido nesta conversa. Complementa (não substitui) o `STATE` e o `PORTFOLIO.md`, que continuam sendo os documentos vivos de referência._

## 1. Ponto de partida

Pedido inicial: ideias de portfólio fortes usando Airflow, dbt, Databricks, Python e SQL, visíveis no GitHub.

Primeira direção explorada: integrar Airflow (orquestração) e dbt (modelagem/testes) ao projeto de portfólio já fechado em sessão anterior (dataset Olist, Medallion Bronze/Silver/Gold no Databricks) — já que essas duas ferramentas não faziam parte das decisões originais e preenchiam uma lacuna real.

## 2. Descarte do Olist e busca por dado via API

Decisão do Crystyan: abandonar o Olist, buscar algo consumido via API — cogitou dados da B3.

Pesquisa feita:
- **brapi.dev** — API de cotações B3, mas free tier sem cadastro libera só 4 ativos (PETR4, VALE3, MGLU3, ITUB4).
- **BCB SGS** (`api.bcb.gov.br`) — API oficial e gratuita, sem cadastro, para séries macro (Selic, IPCA, câmbio). Limite: janelas de consulta de até 10 anos por chamada.
- **B3 for Developers** — existe, mas é voltado a market data institucional/pago.
- Recomendação na hora: BCB SGS como espinha dorsal + `yfinance` ou brapi.dev (com token) para cotações.

## 3. Descoberta do projeto real já construído: RAG institucional (Casas Bahia)

O Crystyan trouxe o `PORTFOLIO.md` de um projeto já em andamento, bem mais avançado do que a ideia de mercado financeiro: um chatbot RAG sobre dados institucionais/financeiros do Grupo Casas Bahia, usando dado público da CVM (IPE + ITR/DFP), com stack CrewAI (Flows + Knowledge), ChromaDB, Ollama (embedding local) e Gemini 2.5 Flash (geração). Nasceu como monitor de preço, pivotou após bloqueio anti-bot (Akamai) nos alvos testados.

Pontos fortes identificados: bugs reais encontrados e corrigidos (config de embedder isolada por thread via `ContextVar`; limite da API de alto nível do CrewAI que não repassa metadata pro Chroma), decisões de arquitetura defensáveis (uso do dataset estruturado ITR/DFP em vez de extrair tabela de PDF), decisões de não fazer documentadas com critério.

Gap identificado: o projeto não usa Databricks, Airflow, dbt nem PySpark — que é o que a headline do LinkedIn e o CV atual vendem, e o que vagas como a do Bradesco (já aplicada) filtram.

Ponto de atenção levantado: o chatbot analisa dados financeiros do próprio empregador atual, que está em recuperação judicial desde ago/2026 (fato público e amplamente noticiado, inclusive com página dedicada no RI oficial da empresa). Levantado como ponto de reflexão sobre percepção (otimismo vs. "abutre"), não como bloqueio.

## 4. Limpeza de referências ao Olist

O Crystyan reportou frustração: o Olist (projeto encerrado há tempos por ele) continuava reaparecendo. Identificadas duas fontes:
- **STATE.md** no Google Drive — atualizado nesta sessão: Olist marcado como encerrado, com aviso no topo do arquivo para sessões futuras não reabrirem o assunto; seção reescrita para descrever o projeto RAG como ativo.
- **Instruções personalizadas do Projeto** (Configurações do projeto → Instruções personalizadas, no claude.ai) — não editável via ferramenta; foi entregue um arquivo com o texto atualizado para o Crystyan colar manualmente.

Esclarecimento do Crystyan sobre o uso do dado da Casas Bahia: postura de transparência, dado 100% público, sem intenção de explorar a crise da empresa (comparável a usar dado de qualquer outra empresa em RJ, como a Braskem) — e não há interesse em permanecer na empresa a longo prazo.

## 5. Como ligar Airflow/Databricks/dbt ao projeto já existente

Em vez de tratar como escolha entre "manter o RAG" ou "construir do zero com stack clássico", a saída definida foi somar as duas coisas ao mesmo projeto:

- **Airflow**: passa a orquestrar a ingestão (hoje manual/sob demanda via `IngestFlow` do CrewAI), com valor real porque a CVM publica documento novo continuamente — corpus de RAG precisa se manter fresco.
- **Databricks + dbt**: entram na trilha de dado **estruturado** (ITR/DFP), que hoje é usado de forma ad-hoc. Ampliando de 1 empresa (Casas Bahia) para o universo de companhias listadas na B3 e em recuperação judicial, o volume de dado passa a justificar um Medallion Bronze/Silver/Gold de verdade, com dbt calculando indicadores financeiros (endividamento, liquidez, margem) — inclusive o item de roadmap "variação % entre período", que vira modelo dbt em vez de cálculo solto em Python.
- O chatbot CrewAI/Chroma (RAG vetorial) continua para pergunta qualitativa/documental; pergunta comparativa/numérica passa a ser respondida via SQL nas tabelas Gold — roteamento decidido no próprio agente.

## 6. O nicho: empresas B3 em recuperação judicial

Ideia do Crystyan: em vez de ficar só na Casas Bahia, ampliar para todas as empresas que entraram em RJ, fechando um nicho ligado ao momento atual do país.

Pesquisa confirmou que é um fenômeno real e documentado:
- 2025 bateu recorde histórico de recuperações judiciais no Brasil (~2,5 mil empresas, segundo a Serasa Experian), com varejo e agronegócio como setores mais afetados **no universo geral de empresas**.
- Entre companhias **listadas na B3** especificamente, existe uma lista de ~19–21 empresas em RJ (Americanas, AgroGalaxy, Atmasa, Bardella, Bombril, Coteminas, Hotéis Othon, Intercement, João Fortes, Light, Nexpe, Oi, OSX Brasil, Paranapanema, Pet Manguinhos, Rod Tiete, Rossi Residencial, Santanense, Springs Global, e agora Casas Bahia) — nesse recorte, varejo é minoria (predominam têxtil, construção civil, industrial/commodities e infraestrutura).

Consequência prática: setor entra como **variável de análise nos modelos Gold**, não como filtro de escopo — permite testar a hipótese "varejo é o mais afetado" com dado real em vez de assumi-la de antemão (evita viés de confirmação, e é uma narrativa de portfólio mais forte).

A lista de empresas deve ser **descoberta programaticamente**, varrendo o campo `Categoria` do dataset IPE da CVM por fatos relevantes de recuperação judicial/extrajudicial — não hardcoded a partir de reportagem.

## 7. Como escalar o RAG (Chroma/CrewAI) de 1 para ~20 empresas

- **Chroma**: uma única collection, particionada por metadata (`company_ticker` somado aos campos de categoria/data já usados) — não criar uma collection por empresa. Mantém busca cross-company funcionando.
- **Manifesto de indexação**: chave passa a ser `(empresa, arquivo)`, mesma lógica de hash incremental já existente.
- **Airflow em 3 DAGs**: *discovery* (varre a CVM por empresa nova entrando em RJ), *backfill por empresa* (dynamic task mapping — uma task por empresa, isolamento de falha), *incremental* (só documento novo, roda com frequência).
- `IngestFlow` precisa ser generalizado para aceitar `company` como parâmetro (hoje está fechado em cima de uma empresa só).
- Risco a planejar: escalar 20x multiplica rate limit de scraping da CVM, geração via Gemini e embedding local — manter o padrão de "indexação em fases" (mais recente primeiro) como default por empresa, não como exceção.

## 8. Migração de embedding: Ollama → Gemini API

Motivo: eliminar dependência de GPU/máquina local no serving, e fechar de vez a classe de bug do `ContextVar` (config de embedder divergindo entre threads).

Pesquisa de custo: Gemini Embedding 001 ≈ US$ 0,15/milhão de tokens (Embedding 2 ≈ US$ 0,20), com Batch API pela metade do preço. Estimativa: mesmo um backfill grande (dezenas de milhões de tokens, 20 empresas) deve custar poucos dólares no total — embedding é a parte mais barata de toda a arquitetura.

Ponto técnico importante: vetores do Ollama e do Gemini não são comparáveis (espaços vetoriais diferentes) — a migração exige **reembedar o corpus inteiro**, não uma troca incremental. Recomendação: fazer a migração de embedder na mesma leva do backfill das novas empresas, não como troca isolada. Usar Batch API para o backfill grande; free tier deve bastar para as atualizações incrementais do dia a dia.

## 9. Decisão de hospedagem e separação em dois projetos

O Crystyan definiu que quer rodar isso sem depender da própria máquina (queda de energia/internet de casa não deve afetar o demo público), mas também não quer abrir mão de mostrar competência em modelo local — o que levou à divisão:

### Projeto 1 — "Radar de Recuperação Judicial B3" (RAG institucional, cloud)
- Geração e embedding via Gemini API (sem GPU necessária).
- Camada estruturada nova: Databricks + Airflow + dbt, cobrindo as ~20 empresas.
- Hospedagem: VPS cloud paga e barata (Hetzner, ~US$ 5/mês) — decisão tomada porque disponibilidade pública não deve depender de energia/internet/roteador de casa. Segurança bem configurada (Cloudflare Tunnel, isolamento) resolve o risco de exposição, mas **não resolve** o risco de disponibilidade da rede residencial — por isso a VPS, não o homelab, para este projeto especificamente.
- **Hostinger avaliado e descartado**: planos VPS (KVM) têm preço de renovação 100–150% acima do promocional (ex.: KVM1 sai de R$ 29,99 para R$ 59,99/mês); Hetzner/Contabo entregam specs iguais ou melhores por menos, sem esse salto.
- Domínio: não precisa comprar um novo — o domínio que o Crystyan já usa no homelab (hoje com subdomínios privados via Tailscale) comporta um subdomínio público novo sem custo adicional, caso ele prefira reaproveitar em vez de registrar um domínio dedicado ao portfólio.

### Projeto 2 — versão self-hosted com n8n (repositório separado, planejado)
- Mesmo domínio de dados (empresas B3 em RJ), escopo reduzido (1–3 empresas) — objetivo é provar uma capacidade complementar, não replicar a abrangência do projeto 1.
- Ollama para geração **e** embedding (sem API paga), orquestrado por n8n em vez de Airflow/CrewAI Flow.
- Hospedagem: homelab próprio via Cloudflare Tunnel — aqui a disponibilidade variável da rede residencial é aceitável, porque faz parte da própria tese do projeto (self-hosted tem esse trade-off, e demonstrar isso com consciência é o ponto).
- Checklist de segurança discutido para a exposição pública via Tunnel: sem port forward no roteador; subdomínio público separado dos subdomínios privados existentes; serviço isolado em container/rede Docker própria (sem dividir namespace com Home Assistant, FreshRSS etc.); editor do n8n permanece só no Tailscale, nunca exposto publicamente; WAF/rate limit gratuito da Cloudflare no hostname público; chave de API dedicada a esse serviço; volume do Chroma incluído na rotina de backup (Duplicati) já existente; limite de recurso (CPU/RAM/GPU) no container para não impactar outros serviços da mesma máquina em caso de pico de tráfego.
- Narrativa dos dois projetos juntos: "sei quando vale API gerenciada (disponibilidade, zero manutenção) e sei quando vale self-hosted (custo marginal zero, controle total do dado) — implementei os dois caminhos pro mesmo tipo de problema."

## Onde as decisões estão registradas

- `PORTFOLIO.md` (docs do Projeto do Claude) — arquitetura técnica completa dos dois projetos, Fase 1 (concluída) e Fase 2 (planejada) do Projeto 1, e a seção do Projeto 2.
- `STATE.md` (Google Drive, pasta local sincronizada) — status geral do Plano B, checklist e próximas tarefas, com o Olist marcado como encerrado.
- Instruções personalizadas do Projeto (claude.ai) — texto atualizado entregue ao Crystyan para colar manualmente em Configurações do projeto.