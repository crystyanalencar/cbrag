#!/usr/bin/env python
"""Baixa páginas HTML e PDFs arquivados no Wayback Machine para os domínios
grupocasasbahia.com.br e ri.grupocasasbahia.com.br (ambos bloqueiam scraping
direto via Akamai), extrai texto limpo e monta o corpus local pro RAG.
"""
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
CDX_FILES = [ROOT / "data/cdx/cdx_main.json", ROOT / "data/cdx/cdx_ri.json"]
CORPUS_DIR = ROOT / "data/corpus"
PDF_DIR = ROOT / "data/pdfs"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; casas-bahia-rag-crawler/1.0)"}
MIN_TEXT_LEN = 200
TENTATIVAS = 4
# Circuit breaker: falha de conexão/timeout consecutiva (não 404/vazio)
# indica o site inteiro fora do ar, não uma URL específica ruim — sem isso,
# um web.archive.org indisponível faz o crawl esgotar TENTATIVAS pra cada
# uma das ~3300 URLs do CDX antes de desistir (já aconteceu numa sessão
# anterior). Aborta cedo e deixa pendente pra próxima rodada.
FALHAS_CONSECUTIVAS_LIMITE = 8

# Widgets de consentimento de cookie não são pegos pelos seletores de tag
# removidos em extrair_texto() (não ficam dentro de <footer>) e sempre
# aparecem no fim do texto extraído — cortar tudo a partir da primeira
# ocorrência do marcador remove o widget inteiro (RI usa um texto, o site
# institucional usa outro, mais o painel GDPR completo).
MARCADORES_COOKIE = ("Este site utiliza", "Utilizamos cookies para lhe proporcionar")

# O RI é uma SPA: listagens de documentos (atas, arquivamentos CVM, central
# de downloads etc.) são carregadas via JS. O Wayback só salva o HTML
# estático, então essas páginas ficam sem o conteúdo real — só o shell de
# menu/breadcrumb em volta desta mensagem de "sem resultado".
MARCADOR_VAZIO = "nenhum arquivo para o ano selecionado"


def get_com_retry(url: str, **kwargs) -> requests.Response:
    ultimo_erro = None
    for tentativa in range(TENTATIVAS):
        try:
            resp = requests.get(url, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            ultimo_erro = e
            time.sleep(2 * (tentativa + 1))
    raise ultimo_erro


def carregar_entradas():
    html_entries = []
    pdf_entries = []
    for path in CDX_FILES:
        rows = json.loads(path.read_text(encoding="utf-8"))[1:]
        for _urlkey, timestamp, original, mimetype, statuscode, *_ in rows:
            if statuscode != "200":
                continue
            if "/akam/" in original:
                continue  # pixel de telemetria anti-bot da Akamai, nunca tem conteúdo
            if mimetype == "text/html":
                html_entries.append((timestamp, original))
            elif mimetype == "application/pdf":
                pdf_entries.append((timestamp, original))
    return html_entries, pdf_entries


def nome_arquivo(url: str, ext: str) -> str:
    slug = re.sub(r"^https?://", "", url)
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", slug).strip("_").lower()
    return f"{slug[:150]}.{ext}"


def extrair_texto(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "svg"]):
        tag.decompose()
    texto = soup.get_text(separator="\n")
    linhas = [linha.strip() for linha in texto.splitlines()]
    linhas = [linha for linha in linhas if linha]
    for i, linha in enumerate(linhas):
        if linha.startswith(MARCADORES_COOKIE):
            linhas = linhas[:i]
            break
    texto = "\n".join(linhas)
    if MARCADOR_VAZIO in texto.lower():
        return ""
    return texto


def baixar_paginas(html_entries):
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    salvos, pulados, falhas, falhas_seguidas = 0, 0, 0, 0
    for timestamp, original in html_entries:
        destino = CORPUS_DIR / nome_arquivo(original, "txt")
        if destino.exists():
            salvos += 1
            continue
        wayback_url = f"http://web.archive.org/web/{timestamp}id_/{original}"
        try:
            resp = get_com_retry(wayback_url, headers=HEADERS, timeout=30)
        except requests.RequestException as e:
            print(f"FALHA {original}: {e}")
            falhas += 1
            falhas_seguidas += 1
            if falhas_seguidas >= FALHAS_CONSECUTIVAS_LIMITE:
                print(
                    f"{falhas_seguidas} falhas de conexão seguidas — "
                    "web.archive.org parece fora do ar. Abortando cedo, "
                    "resto fica pendente pra próxima rodada."
                )
                break
            continue
        falhas_seguidas = 0
        texto = extrair_texto(resp.text)
        if len(texto) < MIN_TEXT_LEN:
            pulados += 1
            continue
        destino.write_text(f"URL: {original}\n\n{texto}", encoding="utf-8")
        salvos += 1
        time.sleep(0.3)
    print(f"Páginas: {salvos} salvas, {pulados} puladas (texto curto), {falhas} falharam")
    return {"paginas_salvas": salvos, "paginas_puladas": pulados, "paginas_falhas": falhas}


def baixar_pdfs(pdf_entries):
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    salvos, falhas, falhas_seguidas = 0, 0, 0
    for timestamp, original in pdf_entries:
        destino = PDF_DIR / nome_arquivo(original, "pdf")
        if destino.exists():
            salvos += 1
            continue
        wayback_url = f"http://web.archive.org/web/{timestamp}id_/{original}"
        try:
            resp = get_com_retry(wayback_url, headers=HEADERS, timeout=30)
        except requests.RequestException as e:
            print(f"FALHA {original}: {e}")
            falhas += 1
            falhas_seguidas += 1
            if falhas_seguidas >= FALHAS_CONSECUTIVAS_LIMITE:
                print(
                    f"{falhas_seguidas} falhas de conexão seguidas — "
                    "web.archive.org parece fora do ar. Abortando cedo, "
                    "resto fica pendente pra próxima rodada."
                )
                break
            continue
        falhas_seguidas = 0
        destino.write_bytes(resp.content)
        salvos += 1
        time.sleep(0.3)
    print(f"PDFs: {salvos} salvos, {falhas} falharam")
    return {"pdfs_salvos": salvos, "pdfs_falhas": falhas}


def main() -> dict:
    html_entries, pdf_entries = carregar_entradas()
    print(f"{len(html_entries)} páginas HTML, {len(pdf_entries)} PDFs no CDX")
    resultado = baixar_paginas(html_entries)
    resultado.update(baixar_pdfs(pdf_entries))
    return resultado


if __name__ == "__main__":
    main()
