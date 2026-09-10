# casas_bahia_rag

Chatbot RAG conversacional sobre a Grupo Casas Bahia (institucional, RI,
governança e financeiro), construído com CrewAI (Flow conversacional +
tool-calling) sobre documentos públicos da CVM e do site da empresa.

- **Geração**: Gemini (`gemini-3.1-flash-lite`), fallback automático pro Groq.
- **Retrieval**: Qdrant embedded com dois vetores por chunk — denso
  (`nomic-embed-text` via Ollama) e esparso BM25 (fastembed, stemmer
  português). O chat usa BM25 puro; híbrido com RRF fica disponível por
  configuração (`qdrant_store.MODO_CHAT`).
- **Dados estruturados** (DRE trimestral, composição do conselho) vêm dos
  datasets abertos da CVM e viram tools próprias — o LLM decide qual chamar.

Estado do trabalho, decisões e pendências: [STATE.md](STATE.md).

## Requisitos

- Python >= 3.10 < 3.14, [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com) rodando local com `nomic-embed-text` (embedding)
- `.env` com `GEMINI_API_KEY` (ou `GOOGLE_API_KEY`) e `GROQ_API_KEY`
  (fallback). Opcional: `QDRANT_URL`/`QDRANT_API_KEY` pra usar um servidor
  Qdrant em vez do modo embedded.

```bash
uv sync
```

## Rodar

```bash
uv run ingest   # coleta CVM/site, prepara o corpus e indexa no Qdrant (incremental)
uv run chat     # REPL do chatbot no terminal
```

Utilitários:

```bash
uv run python scripts/backfill_qdrant.py            # só reindexa (sem recoletar)
uv run python scripts/backfill_qdrant.py --so-bm25  # recalcula só o vetor esparso
uv run python scripts/avaliar_retrieval.py --modo chat      # recall@8/MRR no golden
uv run python scripts/avaliar_retrieval.py --modo qdrant-hibrido --verboso
```

## Layout

- `src/casas_bahia_rag/main.py` — Flow conversacional, agente, fallback de LLM
- `src/casas_bahia_rag/tools/rag_tools.py` — tools que o agente chama
- `src/casas_bahia_rag/qdrant_store.py` — índice Qdrant (denso + BM25, busca)
- `src/casas_bahia_rag/knowledge_config.py` — caminhos, chunking, manifesto
- `src/casas_bahia_rag/dados_financeiros.py`, `composicao_conselho.py` — dados estruturados da CVM
- `scripts/` — coleta (Wayback, CVM), extração de PDF, preparação do corpus, avaliação
- `tests/golden_retrieval.json` — perguntas com documento esperado, pra medir retrieval
- `data/` — corpus e índice (gerados, fora do git)
