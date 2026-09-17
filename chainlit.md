# Casas Bahia RAG

Assistente sobre a **Grupo Casas Bahia** — institucional, governança e financeiro — construído sobre dados públicos oficiais (CVM e site institucional), não sobre memória do modelo.

## O que dá pra perguntar

- **Financeiro**: resultado de um trimestre específico (lucro/prejuízo, receita, EBITDA), série histórica ao longo do tempo, comparação entre períodos.
- **Governança**: quem são os membros atuais do Conselho de Administração, Diretoria e Conselho Fiscal.
- **Institucional**: histórico da empresa, estratégia, fatos relevantes e desenvolvimentos institucionais recentes.
- Perguntas de acompanhamento na mesma conversa (o assistente mantém o contexto do que já foi perguntado).

## Como funciona por baixo

- Dado financeiro vem direto da **DRE estruturada da CVM** (não é lido de PDF na hora) — período exato, sem ambiguidade.
- Composição do Conselho vem do **Formulário de Referência (FRE)** da CVM, sempre a versão mais recente arquivada.
- Perguntas mais abertas (estratégia, histórico, fatos relevantes) usam busca híbrida (léxica + semântica) sobre o corpus institucional e regulatório.
- Sem dado: o assistente diz que não encontrou, em vez de inventar.

## Limites

Corpus cobre só a Grupo Casas Bahia, dados públicos até a data mais recente arquivada na CVM/Wayback. Não é aconselhamento financeiro.

## Aviso

Projeto de portfólio pessoal, **sem vínculo, chancela ou afiliação com a Grupo Casas Bahia**. Usa exclusivamente dados públicos (CVM, site institucional via Wayback Machine).
