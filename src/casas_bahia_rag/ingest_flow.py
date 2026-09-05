#!/usr/bin/env python
"""Flow de ingestão: coleta os dados públicos da Grupo Casas Bahia (Wayback +
CVM), prepara o corpus e constrói a base de conhecimento (embedding) usada
pelo CasasBahiaRagFlow (chat). Roda uma vez (ou quando quiser atualizar os
dados) — não é conversacional, não reroda a cada pergunta do usuário.

Cada etapa é Python puro (sem LLM) até a última, que constrói o Agent e
dispara o embedding via Ollama.
"""
import sys
from pathlib import Path

from pydantic import BaseModel

from crewai.flow.flow import Flow, listen, start

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import baixar_cvm  # noqa: E402
import crawl_wayback  # noqa: E402
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
    pdf_extraidos: int = 0
    pdf_pulados: int = 0
    pdf_falhas: int = 0
    knowledge_html: int = 0
    knowledge_pdf: int = 0
    knowledge_pronto: bool = False


class IngestFlow(Flow[IngestState]):
    @start()
    def coletar_wayback(self):
        resultado = crawl_wayback.main()
        for chave, valor in resultado.items():
            setattr(self.state, chave, valor)

    @listen(coletar_wayback)
    def coletar_cvm(self):
        resultado = baixar_cvm.main()
        for chave, valor in resultado.items():
            setattr(self.state, chave, valor)

    @listen(coletar_cvm)
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
        from casas_bahia_rag.main import rag_agent

        agent = rag_agent()  # constrói o Agent e dispara set_knowledge() (embedding)
        self.state.knowledge_pronto = agent.knowledge is not None


def kickoff():
    flow = IngestFlow()
    flow.kickoff()
    print(flow.state)


def plot():
    IngestFlow().plot("ingest_flow")


if __name__ == "__main__":
    kickoff()
