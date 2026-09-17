"""Lista determinística dos documentos CVM mais recentes de uma categoria
(fato relevante, comunicado ao mercado, ata...), lendo a metadata que
`scripts/preparar_knowledge.py` grava em `data/knowledge/_metadata.json`.

Por quê: `buscar_conhecimento` é BM25 por termo — "último"/"mais recente"
não tem âncora lexical nenhuma, então "qual foi o último fato relevante?"
devolvia um documento diferente a cada tentativa (ver STATE.md, fase 4).
Pergunta de ordem no tempo se resolve ordenando por data, não por
similaridade — mesmo princípio de `dados_financeiros.py` e
`composicao_conselho.py`.
"""
import re
import unicodedata

from cbrag.knowledge_config import KNOWLEDGE_DIR, _ler_metadata_sidecar

# Valores exatos de `categoria_cvm` (CSV IPE da CVM) que valem listar.
CATEGORIAS = [
    "Fato Relevante",
    "Comunicado ao Mercado",
    "Assembleia",
    "Reunião da Administração",
    "Aviso aos Acionistas",
    "Dados Econômico-Financeiros",
    "Informações de Companhias em Recuperação Judicial ou Extrajudicial",
    "Calendário de Eventos Corporativos",
]

QUANTIDADE_MAX = 15
DOCS_COM_TRECHO = 2
TAMANHO_TRECHO = 1500
TAMANHO_ASSUNTO = 200

_CABECALHO_RE = re.compile(r"^\[Fonte: .*\]\s*$", re.MULTILINE)


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento).casefold().strip()


def resolver_categoria(pedido: str) -> str | None:
    """Casa o que o LLM escreveu ("fatos relevantes", "comunicado") com o
    valor exato da CVM. Tolerante de propósito: parâmetro `Literal` faria o
    pydantic rejeitar antes do código rodar e queimar uma iteração do agent."""
    alvo = _normalizar(pedido).rstrip("s")
    if not alvo:
        return None
    if alvo in ("rj", "recuperacao judicial", "recuperação judicial"):
        return "Informações de Companhias em Recuperação Judicial ou Extrajudicial"
    for categoria in CATEGORIAS:
        base = _normalizar(categoria)
        if alvo == base or alvo in base or base.startswith(alvo):
            return categoria
    # "fatos relevantes" -> "fato relevante": compara palavra a palavra sem plural
    for categoria in CATEGORIAS:
        palavras = {p.rstrip("s") for p in _normalizar(categoria).split()}
        if all(p.rstrip("s") in palavras for p in alvo.split()):
            return categoria
    return None


def _assunto(info: dict) -> str:
    assunto = info.get("assunto") or info.get("tipo_cvm") or "(sem assunto)"
    assunto = re.sub(r"\s*\|\|\s*", "; ", assunto)
    assunto = re.sub(r"\s+", " ", assunto).strip()
    if len(assunto) > TAMANHO_ASSUNTO:
        assunto = assunto[:TAMANHO_ASSUNTO].rstrip() + "…"
    return assunto


def _trecho(nome_arquivo: str) -> str | None:
    caminho = KNOWLEDGE_DIR / nome_arquivo
    if not caminho.exists():
        return None
    texto = _CABECALHO_RE.sub("", caminho.read_text(encoding="utf-8"))
    texto = re.sub(r"\s+", " ", texto).strip()
    if len(texto) > TAMANHO_TRECHO:
        texto = texto[:TAMANHO_TRECHO].rstrip() + "…"
    return texto or None


def listar_documentos_recentes(
    categoria: str,
    quantidade: int = 5,
    ano: int | None = None,
) -> str | None:
    """Texto pronto: os `quantidade` documentos mais recentes da categoria
    (opcionalmente só de `ano`), mais recente primeiro, com trecho do corpo
    dos primeiros. `None` se a categoria não casou ou não há documento."""
    categoria_cvm = resolver_categoria(categoria)
    if categoria_cvm is None:
        return None

    docs = [
        (nome, info)
        for nome, info in _ler_metadata_sidecar().items()
        if info.get("categoria_cvm") == categoria_cvm
        and (ano is None or (info.get("data_iso") or "")[:4] == str(ano))
    ]
    if not docs:
        return None

    docs.sort(key=lambda par: par[1].get("data_ordinal") or 0, reverse=True)
    quantidade = max(1, min(quantidade, QUANTIDADE_MAX))
    selecionados = docs[:quantidade]

    filtro_ano = f" em {ano}" if ano is not None else ""
    linhas = [
        f"Documentos da categoria \"{categoria_cvm}\"{filtro_ano} — "
        f"{len(selecionados)} mais recentes de {len(docs)} (fonte: CVM/IPE, "
        f"ordenado pela data de entrega à CVM, mais recente primeiro; a base "
        f"cobre essa categoria até {docs[0][1].get('data_iso')} — não existe "
        f"documento mais novo que esse na base):"
    ]
    for nome, info in selecionados:
        tipo = info.get("tipo_cvm")
        tipo_txt = f" — {tipo}" if tipo and tipo != info.get("assunto") else ""
        linhas.append(f"- {info.get('data_iso')}{tipo_txt} — {_assunto(info)}")

    for nome, info in selecionados[:DOCS_COM_TRECHO]:
        trecho = _trecho(nome)
        if trecho:
            linhas.append(f"\nTrecho do documento de {info.get('data_iso')} ({_assunto(info)}):\n{trecho}")

    return "\n".join(linhas)
