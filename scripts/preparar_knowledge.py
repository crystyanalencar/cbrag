#!/usr/bin/env python
"""Prepara data/knowledge/ a partir de data/corpus/ e data/corpus_pdf/,
injetando um cabeçalho de origem+data a cada ~800 caracteres do texto.

Por quê: TextFileKnowledgeSource do CrewAI não anexa metadado por chunk (só
guarda o texto puro), e o corpus cobre ~5 anos de documentos que se
sobrepõem/superam (assembleias, estatuto, capital social mudam com o
tempo). Sem data no próprio texto, o LLM não tem como saber se um chunk
recuperado é a informação vigente ou uma versão antiga. Repetir o cabeçalho
a cada bloco (em vez de só no topo do arquivo) garante que, não importa
onde o chunker char-based do CrewAI corte o texto, o chunk resultante
carrega origem+data — desde que o intervalo de repetição seja menor que o
chunk_size usado no TextFileKnowledgeSource.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS_HTML_DIR = ROOT / "data/corpus"
CORPUS_PDF_DIR = ROOT / "data/corpus_pdf"
CDX_FILES = [ROOT / "data/cdx/cdx_main.json", ROOT / "data/cdx/cdx_ri.json"]
OUT_DIR = ROOT / "data/knowledge"

INTERVALO_CABECALHO = 800  # caracteres; deve ser < chunk_size do TextFileKnowledgeSource

CVM_NOME_RE = re.compile(r"^(\d{4})_(\d{2})_(\d{2})_(.+)$")
CVM_CATEGORIA_SUFIXO_RE = re.compile(r"_\d{6,}ipe.*$")
UPLOADS_DATA_RE = re.compile(r"uploads_(\d{4})_(\d{2})")


def montar_mapa_wayback() -> dict[str, str]:
    """original_url -> timestamp (YYYYMMDDhhmmss) a partir dos CDX salvos."""
    mapa = {}
    for caminho in CDX_FILES:
        linhas = json.loads(caminho.read_text(encoding="utf-8"))[1:]
        for _urlkey, timestamp, original, *_ in linhas:
            mapa[original] = timestamp
    return mapa


def formatar_data_wayback(timestamp: str) -> str:
    return f"{timestamp[0:4]}-{timestamp[4:6]}-{timestamp[6:8]}"


def inserir_cabecalhos(texto: str, cabecalho: str) -> str:
    paragrafos = texto.split("\n")
    blocos: list[str] = []
    atual: list[str] = []
    tamanho_atual = 0

    def fechar_bloco():
        if atual:
            blocos.append(f"{cabecalho}\n" + "\n".join(atual))

    for linha in paragrafos:
        atual.append(linha)
        tamanho_atual += len(linha) + 1
        if tamanho_atual >= INTERVALO_CABECALHO:
            fechar_bloco()
            atual = []
            tamanho_atual = 0
    fechar_bloco()

    return "\n\n".join(blocos)


def processar_html(mapa_wayback: dict[str, str]) -> int:
    n = 0
    for caminho in sorted(CORPUS_HTML_DIR.glob("*.txt")):
        conteudo = caminho.read_text(encoding="utf-8")
        primeira_linha, _, corpo = conteudo.partition("\n\n")
        url = primeira_linha.removeprefix("URL: ").strip()

        timestamp = mapa_wayback.get(url)
        data = formatar_data_wayback(timestamp) if timestamp else "data desconhecida"
        origem = "RI" if "ri.grupocasasbahia.com.br" in url else "Institucional (site)"

        cabecalho = f"[Fonte: {origem} | URL: {url} | Data do snapshot: {data}]"
        destino = OUT_DIR / caminho.name
        destino.write_text(inserir_cabecalhos(corpo, cabecalho), encoding="utf-8")
        n += 1
    return n


def metadado_pdf(nome_arquivo: str) -> tuple[str, str]:
    """Retorna (origem, data) a partir do nome do arquivo extraído."""
    m = CVM_NOME_RE.match(nome_arquivo)
    if m:
        ano, mes, dia, resto = m.groups()
        categoria_slug = CVM_CATEGORIA_SUFIXO_RE.sub("", resto)
        categoria = categoria_slug.replace("_", " ")
        return f"CVM — {categoria}", f"{ano}-{mes}-{dia}"

    m = UPLOADS_DATA_RE.search(nome_arquivo)
    if m:
        ano, mes = m.groups()
        return "Institucional (PDF do site)", f"{ano}-{mes} (aprox., data de upload)"

    return "Institucional (PDF do site)", "data desconhecida"


def processar_pdfs() -> int:
    n = 0
    for caminho in sorted(CORPUS_PDF_DIR.glob("*.txt")):
        origem, data = metadado_pdf(caminho.stem)
        cabecalho = f"[Fonte: {origem} | Arquivo: {caminho.stem} | Data: {data}]"
        corpo = caminho.read_text(encoding="utf-8")
        destino = OUT_DIR / caminho.name
        destino.write_text(inserir_cabecalhos(corpo, cabecalho), encoding="utf-8")
        n += 1
    return n


def main() -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mapa_wayback = montar_mapa_wayback()
    n_html = processar_html(mapa_wayback)
    n_pdf = processar_pdfs()
    print(f"data/knowledge/: {n_html} páginas HTML + {n_pdf} PDFs preparados com cabeçalho de origem/data")
    return {"knowledge_html": n_html, "knowledge_pdf": n_pdf}


if __name__ == "__main__":
    main()
