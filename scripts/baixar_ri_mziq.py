#!/usr/bin/env python
"""Baixa documentos da Central de Downloads do RI (api.mziq.com, plataforma
Mziq) do Grupo Casas Bahia S.A. — fonte oficial, sem Akamai (diferente do
site institucional/RI em si, que usa WAF/CDN anti-bot), publicada no dia do
fato societário.

Fonte única pra documento corrente a partir de ANO_INICIAL (ver
docs/ingestao.md, CS-26): CVM aberta (baixar_cvm.py) segue cobrindo só o
histórico anterior a esse ano, capado em fases — cada fase futura desce
ANO_INICIAL mais um ano e repete a migração
(scripts/migrar_ano_para_central.py) pro ano recém coberto.

O ano da Central é o do exercício/referência do documento, não o da
publicação: DFP, release e apresentação do 4T25 entregues em mar/2026 estão
no ano 2025. Como a migração apaga a CVM por data de entrega (`data_iso` =
data no nome do arquivo = publicação), o ano anterior a ANO_INICIAL também é
coletado, só com o que foi publicado a partir de ANO_INICIAL e sem o
Formulário de Referência: a CVM não tem o PDF dele (só o dataset estruturado
de administração, ver docs/ingestao.md), então não há o que preservar, e as
versões do exercício anterior são superadas pelo FRE corrente.
"""
import hashlib
import json
import re
import time
from datetime import date
from pathlib import Path

import requests

from cbrag.texto import sem_acento

ROOT = Path(__file__).resolve().parents[1]
PDF_DIR = ROOT / "data/ri_central"
INDICE_FILE = PDF_DIR / "_ri_index.json"
# Cópias da "Planilha de Resultados" (.xlsx), uma por versão. A companhia
# substitui o mesmo arquivo a cada divulgação ("Planilha de Resultados
# atual") e a API não guarda as antigas, então sem arquivar aqui a versão
# anterior se perde. Subpasta de PDF_DIR: já montada no serviço ingest, e
# `extrair_texto_pdfs` só olha `*.pdf`/`*.csv` no nível de cima.
PLANILHAS_DIR = PDF_DIR / "_planilhas"

COMPANY_ID = "ce9bff9f-fb19-49b9-9588-c4c6b7052c9c"
URL_META = f"https://api.mziq.com/mzfilemanager/company/{COMPANY_ID}/filter/categories/year/meta"
CATEGORIA_MZIQ = "central_de_downloads_central_de_downloads"
IDIOMA = "pt_BR"

ANO_INICIAL = 2026  # fase 1 — desce em fases futuras junto com baixar_cvm.ANOS
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; cbrag-crawler/1.0)"}

# Títulos (prefixo, sem acento/case) de baixo valor semântico — mesmo
# espírito de CATEGORIAS_EXCLUIDAS em extrair_texto_pdfs.py, decidido
# revisando os 82 títulos distintos de 2026.
TITULOS_EXCLUIDOS = (
    # processual de assembleia — a Ata da mesma assembleia já cobre o fato
    "manual para participacao",
    "boletim de voto a distancia",
    "edital de convocacao",
    # dívida/mercado de capitais — jurídico/técnico, mesma categoria que já
    # é excluída hoje pra CVM
    "escrituras e aditamentos de debentures",
    "termo de emissao",
    "quinto aditamento",  # mesmo aditamento que "Escrituras e aditamentos..." (só o título muda)
    "anuncio de inicio de distribuicao",
    "anuncio de encerramento",
    "aviso a mercado",
    "aviso ao mercado",
    "aviso aos mercado",
    "relatorio agente fiduciario",
    # .xlsx, não .pdf — extrair_texto_pdfs.py (pypdf) não lê. Fora desta
    # fase por decisão explícita: só 1 versão até agora, sem histórico
    # pra confirmar estrutura de abas estável.
    "planilha de resultados",
)

# Vocabulário CVM (coluna Categoria/Tipo/Espécie do CSV IPE) que a Central
# reproduz a partir do file_title, pra `categoria_cvm`/`tipo_cvm`/
# `especie_cvm` terem os mesmos valores nas duas fontes — é o que permite
# filtrar e listar sem saber de onde veio o documento. Cada regra:
# (prefixo do título sem acento/case, categoria, tipo, espécie). Tipo e
# espécie fixos aqui; None = a CVM deixa vazio ou não há como saber.
CAT_COMUNICADO = "Comunicado ao Mercado"
CAT_ASSEMBLEIA = "Assembleia"
CAT_REUNIAO = "Reunião da Administração"
CAT_FINANCEIRO = "Dados Econômico-Financeiros"
CAT_RJ = "Informações de Companhias em Recuperação Judicial ou Extrajudicial"
TIPO_OUTROS_COMUNICADOS = "Outros Comunicados Não Considerados Fatos Relevantes"
TIPO_APRESENTACAO = "Apresentações a analistas/agentes do mercado"

_REGRAS = (
    # Assembleia: tipo = sigla da CVM (AGE/AGO/AGO/E/AGDEB); espécie vem do
    # sufixo do título ("AGE - Edital de Convocação" -> "Edital de Convocação")
    ("agoe -", CAT_ASSEMBLEIA, "AGO/E", None),
    ("age -", CAT_ASSEMBLEIA, "AGE", None),
    ("ago -", CAT_ASSEMBLEIA, "AGO", None),
    ("ata de agd", CAT_ASSEMBLEIA, "AGDEB", "Ata"),
    ("ata de rca", CAT_REUNIAO, "Conselho de Administração", "Ata"),
    ("ata de reuniao do conselho fiscal", CAT_REUNIAO, "Conselho Fiscal", "Ata"),
    ("comunicado ao mercado", CAT_COMUNICADO, None, None),  # tipo em _tipo_comunicado
    ("fato relevante", "Fato Relevante", None, None),
    ("aviso aos acionistas", "Aviso aos Acionistas", None, None),  # tipo em _tipo_aviso
    # Apresentação (deck de resultado, institucional, Investor Day) é
    # Comunicado ao Mercado na CVM, não Dados Econômico-Financeiros
    ("apresentacao", CAT_COMUNICADO, TIPO_APRESENTACAO, None),
    ("release de resultado", CAT_FINANCEIRO, "Press-release", None),
    ("itr -", CAT_FINANCEIRO, "Demonstrações Financeiras Intermediárias", None),
    ("demonstracoes financeiras intermediarias", CAT_FINANCEIRO, "Demonstrações Financeiras Intermediárias", None),
    ("dfp", CAT_FINANCEIRO, "Demonstrações Financeiras Anuais Completas", None),
    ("demonstracoes financeiras anuais", CAT_FINANCEIRO, "Demonstrações Financeiras Anuais Completas", None),
    ("relatorio de agente fiduciario", CAT_FINANCEIRO, "Relatório de Agente Fiduciário", None),
    ("relatorio integrado", "Relato Integrado", None, None),
    ("peticao inicial", CAT_RJ, "Petição Inicial", None),
    ("despacho", CAT_RJ, "Outros documentos", None),
    ("recuperacao judicial", CAT_RJ, None, None),
    ("calendario de eventos", "Calendário de Eventos Corporativos", None, None),
    ("estatuto social", "Estatuto Social", None, None),
    ("codigo de conduta", "Código de Conduta", None, None),
    ("formulario de referencia", "Formulário de Referência", None, None),
    ("fre", "Formulário de Referência", None, None),
    # GOV = os dados do Informe do Código Brasileiro de Governança
    # Corporativa (CSV prática a prática); "Informe de Governança" = o
    # relatório em PDF do mesmo informe. Categoria própria: o IPE da CVM não
    # tem equivalente (ver docs/ingestao.md).
    ("informe de governanca", "Informe de Governança", None, None),
    ("gov", "Informe de Governança", None, None),
)
# Sem regra de propósito (a CVM/IPE não tem categoria pra isso, então ficam
# fora do filtro por categoria_cvm, como o site institucional): transcrição
# de vídeo de resultado, Formulário Cadastral, Relatório de Transparência e
# Igualdade Salarial.

_SEPARADOR_TITULO_RE = re.compile(r"\s+-\s+")


def _sem_acento(texto: str) -> str:
    return sem_acento(texto).casefold()


_SIGLA_ASSEMBLEIA_RE = re.compile(r"^(agoe|age|ago|agd)\s*-\s*")


def eh_planilha_de_resultados(file_title: str) -> bool:
    return _sem_acento(file_title).startswith("planilha de resultados")


def arquivar_planilha(doc: dict) -> str:
    """Guarda o .xlsx como `<entrega>_<sha256[:8]>.xlsx` em PLANILHAS_DIR,
    só se esse conteúdo ainda não existe. Devolve "nova", "repetida" ou
    "falha". Fora do pipeline de embedding: só forma o histórico pra
    desenhar a ingestão estruturada depois (ver docs/ingestao.md)."""
    try:
        resp = requests.get(doc["file_url"], headers=HEADERS, timeout=120)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"FALHA {doc['file_title']} ({doc['file_url']}): {e}")
        return "falha"
    hash_curto = hashlib.sha256(resp.content).hexdigest()[:8]
    PLANILHAS_DIR.mkdir(parents=True, exist_ok=True)
    if any(PLANILHAS_DIR.glob(f"*_{hash_curto}.xlsx")):
        return "repetida"
    entrega = (doc.get("file_published_date") or doc.get("file_date") or "")[:10] or date.today().isoformat()
    destino = PLANILHAS_DIR / f"{entrega}_{hash_curto}.xlsx"
    destino.write_bytes(resp.content)
    print(f"Planilha de Resultados arquivada: {destino.name} ({len(resp.content) // 1024} KB)")
    return "nova"


def deve_pular(file_title: str) -> bool:
    # "AGOE - Manual para participação": a sigla da assembleia vem antes do
    # tipo de documento, então tira ela pra comparar o prefixo
    alvo = _SIGLA_ASSEMBLEIA_RE.sub("", _sem_acento(file_title))
    return any(alvo.startswith(prefixo) for prefixo in TITULOS_EXCLUIDOS)


def _tipo_comunicado(assunto: str) -> str:
    # A CVM classifica esclarecimento de notícia como resposta a
    # questionamento CVM/B3; o resto é "outros comunicados"
    if _sem_acento(assunto).startswith("esclarecimento"):
        return "Esclarecimentos sobre questionamentos da CVM/B3"
    return TIPO_OUTROS_COMUNICADOS


def _tipo_aviso(assunto: str) -> str:
    alvo = _sem_acento(assunto)
    if alvo.startswith("data prevista"):
        return "Data prevista para a assembleia"
    if alvo.startswith("aumento"):
        return "Aumento de capital por subscrição privada deliberado em RCA"
    return "Outros avisos"


def classificar(file_title: str) -> dict:
    """file_title -> campos no mesmo formato do índice IPE
    (`categoria`, `tipo`, `especie`, `assunto`), vazio nos que a CVM
    deixaria vazio. Sem regra: só `assunto` (título), sem `categoria`."""
    alvo = _sem_acento(file_title)
    partes = _SEPARADOR_TITULO_RE.split(file_title, maxsplit=1)
    sufixo = partes[1].strip() if len(partes) > 1 else ""
    for prefixo, categoria, tipo, especie in _REGRAS:
        if not alvo.startswith(prefixo):
            continue
        if categoria == CAT_COMUNICADO and tipo is None:
            tipo = _tipo_comunicado(sufixo)
        elif categoria == "Aviso aos Acionistas":
            tipo = _tipo_aviso(sufixo)
        if categoria == CAT_ASSEMBLEIA and especie is None:
            especie = sufixo or None
        # Comunicado/Fato/Aviso: o assunto da CVM é só o tema, sem o prefixo
        # da categoria ("Apresentação - X" não tem prefixo de categoria: fica
        # o título inteiro, como nos demais)
        prefixo_e_categoria = alvo.startswith(_sem_acento(categoria))
        assunto = sufixo if prefixo_e_categoria and categoria in (CAT_COMUNICADO, "Fato Relevante", "Aviso aos Acionistas") and sufixo else file_title
        if alvo == "gov":
            assunto = "Informe do Código Brasileiro de Governança Corporativa - práticas adotadas (GOV)"
        campos = {"categoria": categoria, "tipo": tipo, "especie": especie, "assunto": assunto}
        return {k: v for k, v in campos.items() if v}
    return {"assunto": file_title}


def slug(texto: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", sem_acento(texto)).strip("_").lower()


def nome_arquivo(doc: dict) -> str:
    data = (doc.get("file_published_date") or doc.get("file_date") or "")[:10]
    partes = [data, slug(doc["file_title"]), doc["download_link_id"][:8]]
    return f"{'_'.join(p for p in partes if p)}.pdf"


def baixar_ano(ano: int) -> list[dict]:
    payload = {"year": str(ano), "categories": [CATEGORIA_MZIQ], "language": IDIOMA, "published": True}
    resp = requests.post(URL_META, json=payload, headers=HEADERS, timeout=60)
    if resp.status_code != 200:
        print(f"RI central {ano}: sem dados ({resp.status_code})")
        return []
    dados = resp.json()
    docs = dados.get("data", {}).get("document_metas", [])
    return [d for d in docs if d.get("language_code") == IDIOMA]


def salvar_indice(indice: dict) -> None:
    INDICE_FILE.write_text(json.dumps(indice, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Índice RI-central: {len(indice)} documentos em {INDICE_FILE}")


def main() -> dict:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    indice: dict = {}
    todos_docs: list[dict] = []
    inicio_publicacao = f"{ANO_INICIAL}-01-01"
    vistos: set[str] = set()
    for ano in range(ANO_INICIAL - 1, date.today().year + 1):
        docs = baixar_ano(ano)
        if ano < ANO_INICIAL:
            docs = [
                d
                for d in docs
                if (d.get("file_published_date") or "")[:10] >= inicio_publicacao
                and classificar(d["file_title"]).get("categoria") != "Formulário de Referência"
            ]
        docs = [d for d in docs if d["download_link_id"] not in vistos]
        vistos.update(d["download_link_id"] for d in docs)
        print(f"RI central {ano}: {len(docs)} documentos")
        todos_docs.extend(docs)

    salvos, pulados, falhas = 0, 0, 0
    for doc in todos_docs:
        if eh_planilha_de_resultados(doc["file_title"]):
            arquivar_planilha(doc)
        if deve_pular(doc["file_title"]):
            pulados += 1
            continue

        destino = PDF_DIR / nome_arquivo(doc)
        chave = destino.stem
        # A API não diz o formato: o GOV vem como CSV (Content-Type
        # text/csv), então a extensão real só se sabe no download. Um CSV já
        # baixado fica com .csv ao lado do nome .pdf que a API sugeriria.
        if destino.with_suffix(".csv").exists():
            destino = destino.with_suffix(".csv")
        indice[chave] = {
            **classificar(doc["file_title"]),
            "titulo": doc["file_title"],
            "data_entrega": (doc.get("file_published_date") or "")[:10],
            "data_referencia": (doc.get("file_date") or "")[:10],
            "protocolo": doc["download_link_id"],
        }

        if destino.exists():
            salvos += 1
            continue
        try:
            resp = requests.get(doc["file_url"], headers=HEADERS, timeout=120)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"FALHA {doc['file_title']} ({doc['file_url']}): {e}")
            falhas += 1
            continue
        if "csv" in resp.headers.get("Content-Type", "").lower():
            destino = destino.with_suffix(".csv")
        destino.write_bytes(resp.content)
        salvos += 1
        time.sleep(0.2)

    salvar_indice(indice)
    print(f"Total: {salvos} documentos salvos em {PDF_DIR}, {pulados} pulados (baixo valor), {falhas} falharam")
    return {"ri_central_salvos": salvos, "ri_central_pulados": pulados, "ri_central_falhas": falhas}


if __name__ == "__main__":
    main()
