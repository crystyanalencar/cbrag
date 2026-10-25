"""Normalização de texto compartilhada — usada pra casar string do LLM ou
de fonte externa (título de documento, nome de assunto) contra um
vocabulário fixo. NFKD tira acento sem virar caractere separado; o resto
(colapsar espaço, casefold, strip) fica em `normalizar`, porque nem todo
uso precisa de tudo (ex.: `baixar_ri_mziq.classificar` só quer o acento
fora, sem mexer em espaço)."""
import re
import unicodedata


def sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()


def normalizar(texto: str) -> str:
    return re.sub(r"\s+", " ", sem_acento(texto)).casefold().strip()
