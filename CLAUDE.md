# cbrag — o que nunca pode ficar ambíguo

**O índice vetorial e o embedding moram no Qdrant servidor da VM Oracle.
Nesta máquina não há índice de produção.**

- Corpus inteiro embedado com Qwen3 (`qwen/qwen3-embedding-8b`, 4096
  dimensões, via OpenRouter). Não existe mais nada de nomic/768 em uso.
- A ingestão (coleta, extração, preparo, embedding) **roda na VM** (`uv run
  ingest`, timer systemd), incremental por manifesto. Não rodar
  `qs.indexar_arquivo`, `backfill_qdrant.py` nem embedding aqui.
- `data/knowledge_storage/qdrant` local é sobra embedded de 2026-09-10 (768
  dimensões, nomic): **não é o índice**, não usar, não apagar às cegas. Sem
  `QDRANT_URL` o cliente cai nele; por isso `indexar_arquivo` e
  `reindexar_bm25` levantam `RuntimeError` sem `QDRANT_URL`.
- `data/` local (corpus, `knowledge/`, `_metadata.json`) serve pra preparar e
  conferir texto/categoria/chunks antes de subir; não está na VM (a VM coleta
  e gera o dela).
- Plano de 2026: a Central entra **incremental e em paralelo** à CVM 2026 só
  pra testar; testado, `scripts/migrar_ano_para_central.py 2026` apaga a CVM
  2026 (redundante; conferir cobertura por `categoria_cvm` antes, ver `docs/decisoes.md`. A Petição Inicial da RJ existe nas duas fontes, mas os anexos dela só na Central).

Detalhe: `docs/infra-producao.md` (onde mora o quê) e o estado de retomada, que mora fora deste repo (regra global).
