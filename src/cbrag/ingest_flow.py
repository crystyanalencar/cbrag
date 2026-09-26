#!/usr/bin/env python
"""Flow de ingestão: coleta os dados públicos da Grupo Casas Bahia (Wayback +
CVM), prepara o corpus e constrói a base de conhecimento (embedding) usada
pelo CbragFlow (chat). Agendado diariamente na VM (systemd timer, service
`ingest` do docker-compose.yml) — não é conversacional, não reroda a cada
pergunta do usuário.

Cada etapa é Python puro (sem LLM); a última indexa no Qdrant (embedding
denso via OpenRouter/Qwen3 Embedding + BM25 local), incremental por
manifesto — dia sem novidade na CVM só confirma "nada novo" e sai rápido.
"""
import sys
from pathlib import Path

from pydantic import BaseModel

from crewai.flow.flow import Flow, listen, start

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import baixar_cvm  # noqa: E402
import baixar_dfp_itr  # noqa: E402
import baixar_ri_mziq  # noqa: E402
import crawl_wayback  # noqa: E402
import extrair_planilha_resultados  # noqa: E402
import extrair_texto_pdfs  # noqa: E402
import preparar_knowledge  # noqa: E402


class IngestState(BaseModel):
    paginas_salvas: int = 0
    paginas_puladas: int = 0
    paginas_falhas: int = 0
    pdfs_salvos: int = 0
    pdfs_falhas: int = 0
    cvm_salvos: int = 0
    cvm_falhas: int = 0
    ri_central_salvos: int = 0
    ri_central_pulados: int = 0
    ri_central_falhas: int = 0
    dre_linhas: int = 0
    planilha_versoes: int = 0
    planilha_valores: int = 0
    planilha_revisoes: int = 0
    pdf_extraidos: int = 0
    pdf_pulados: int = 0
    pdf_falhas: int = 0
    knowledge_html: int = 0
    knowledge_pdf: int = 0
    knowledge_pronto: bool = False


class IngestFlow(Flow[IngestState]):
    @start()
    def coletar_wayback(self):
        # Não-bloqueante: Wayback já se mostrou instável (web.archive.org
        # fora do ar trava o crawl inteiro) e rende pouco valor pro chatbot
        # comparado ao CVM (ver STATE.md) — falha aqui não deve impedir as
        # etapas seguintes de rodar com o que já existe em data/corpus/.
        try:
            resultado = crawl_wayback.main()
        except Exception as e:
            print(f"coletar_wayback falhou, seguindo sem bloquear: {e}")
            return
        for chave, valor in resultado.items():
            setattr(self.state, chave, valor)

    @listen(coletar_wayback)
    def coletar_cvm(self):
        resultado = baixar_cvm.main()
        for chave, valor in resultado.items():
            setattr(self.state, chave, valor)

    @listen(coletar_cvm)
    def coletar_ri_central(self):
        # Central de Downloads do RI (mziq) — fonte de 2026 em diante (ver
        # STATE.md/CS-26); CVM aberta (coletar_cvm) segue só com histórico.
        resultado = baixar_ri_mziq.main()
        for chave, valor in resultado.items():
            setattr(self.state, chave, valor)

    @listen(coletar_ri_central)
    def coletar_dre_estruturada(self):
        # DRE estruturada (dados.cvm.gov.br, dataset ITR/DFP) em paralelo
        # aos PDFs de "dados econômico-financeiros" já baixados por
        # coletar_cvm — mesma informação, mas com DT_INI_EXERC/DT_FIM_EXERC
        # exatos por linha, sem ambiguidade de coluna que a extração de
        # texto do PDF não resolve (ver dados_financeiros.py, STATE.md).
        resultado = baixar_dfp_itr.main()
        for chave, valor in resultado.items():
            setattr(self.state, chave, valor)

    @listen(coletar_dre_estruturada)
    def extrair_planilha(self):
        # Planilha de Resultados (.xlsx arquivado por coletar_ri_central) ->
        # JSON longo em data/cvm_estruturado. Não-bloqueante: a conferência
        # com a DRE da CVM pode barrar a gravação (SystemExit), e isso não
        # deve impedir a indexação dos documentos; o JSON anterior fica.
        try:
            resultado = extrair_planilha_resultados.main()
        except (Exception, SystemExit) as e:
            print(f"extrair_planilha falhou, seguindo sem bloquear: {e}")
            return
        for chave, valor in resultado.items():
            setattr(self.state, chave, valor)

    @listen(extrair_planilha)
    def extrair_pdfs(self):
        resultado = extrair_texto_pdfs.main()
        for chave, valor in resultado.items():
            setattr(self.state, chave, valor)

    @listen(extrair_pdfs)
    def preparar_corpus(self):
        resultado = preparar_knowledge.main()
        for chave, valor in resultado.items():
            setattr(self.state, chave, valor)

    @listen(preparar_corpus)
    def embutir_conhecimento(self):
        # Índice Qdrant (qdrant_store.py): cada chunk entra com vetor denso
        # (OpenRouter/Qwen3 Embedding) e esparso BM25 (fastembed), metadata do sidecar no
        # payload. Não usa o Knowledge/TextFileKnowledgeSource do CrewAI
        # (não repassa metadata) nem o wrapper crewai.rag.qdrant (só denso).
        import os

        from cbrag import knowledge_config as kc
        from cbrag import qdrant_store as qs

        qs.garantir_colecao()
        arquivos = sorted(kc.KNOWLEDGE_DIR.glob("*.txt"))
        pendentes = kc.arquivos_pendentes(arquivos)
        if not pendentes:
            print("Nada novo pra embedar (manifesto já cobre todos os arquivos).")
            self.state.knowledge_pronto = True
            return

        # ANO_MINIMO permite embedar em fases (ex.: ANO_MINIMO=2026 só
        # processa o ano mais recente pra testar rápido; rodar de novo sem
        # a variável faz o backfill do resto — upsert idempotente, sem
        # duplicar). Sempre processa do mais recente pro mais antigo, com
        # ou sem corte.
        ano_minimo = os.environ.get("ANO_MINIMO")
        pendentes = kc.ordenar_por_recencia(
            pendentes, ano_minimo=int(ano_minimo) if ano_minimo else None
        )
        if not pendentes:
            print(f"Nada pendente a partir de ANO_MINIMO={ano_minimo}.")
            self.state.knowledge_pronto = True
            return

        print(f"{len(pendentes)} arquivo(s) novo(s)/mudado(s) pra indexar.")
        for caminho in pendentes:
            n = qs.indexar_arquivo(caminho)
            kc.atualizar_manifesto([caminho], n_chunks={caminho.name: n})
            print(f"  {caminho.name}: {n} chunks indexados")
        self.state.knowledge_pronto = True


def kickoff():
    flow = IngestFlow()
    flow.kickoff()
    print(flow.state)


def plot():
    IngestFlow().plot("ingest_flow")


if __name__ == "__main__":
    kickoff()
