"""Gravação de arquivo baixado pela coleta.

Os coletores só baixam o que ainda não existe em disco (`destino.exists()`),
então um arquivo gravado pela metade nunca mais é baixado: falha na extração
todo run. Foi o caso de um PDF da Central com o fim zerado (gravação
interrompida, não download truncado: o urllib3 2.x já recusa corpo menor que
o `Content-Length`). Gravar num arquivo temporário e renomear no fim faz o
destino existir só quando está completo.
"""
import os
from pathlib import Path

# Subpasta, dentro de cada pasta de origem, pra onde vai o arquivo que a
# extração não consegue ler (ver scripts/extrair_texto_pdfs.py). Fica dentro
# da pasta de origem porque é ela que está montada no serviço ingest.
QUARENTENA_SUBDIR = "_quarentena"

# Quantas vezes o mesmo arquivo pode ir pra quarentena antes de o coletor
# parar de baixá-lo. A primeira cobre a gravação interrompida, que o
# rebaixamento resolve; a segunda indica que o arquivo já vem quebrado da
# fonte, e baixar de novo todo dia só repetiria a falha. Apagar o contador
# em `_quarentena/` libera um novo download.
MAX_QUARENTENAS = 2

_SUFIXO_PARCIAL = ".parcial"
_SUFIXO_CONTADOR = ".quarentenas"


def gravar_atomico(destino: Path, dados: bytes) -> None:
    """Grava `dados` em `destino` via arquivo temporário ao lado e `os.replace`.
    Se o processo cair no meio, sobra só o `.parcial`, que nenhum glob de
    extração casa e que a próxima gravação sobrescreve. Em erro de escrita
    (disco cheio, EIO), o temporário é removido antes de propagar."""
    temporario = destino.with_name(destino.name + _SUFIXO_PARCIAL)
    try:
        with temporario.open("wb") as f:
            f.write(dados)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporario, destino)
    except BaseException:
        # Falha na limpeza não pode mascarar o erro original (disco cheio, EIO).
        try:
            temporario.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _contador(destino: Path) -> Path:
    return destino.parent / QUARENTENA_SUBDIR / (destino.name + _SUFIXO_CONTADOR)


def quarentenas(destino: Path) -> int:
    """Quantas vezes o arquivo de `destino` já foi pra quarentena. Contador
    ausente, ilegível ou com conteúdo inválido conta como 0: um problema no
    contador não pode derrubar a coleta."""
    try:
        return int(_contador(destino).read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0


def registrar_quarentena(destino: Path) -> int:
    """Incrementa o contador de quarentenas de `destino` e devolve o total."""
    total = quarentenas(destino) + 1
    _contador(destino).write_text(str(total), encoding="utf-8")
    return total


def limpar_quarentena(destino: Path) -> None:
    """Remove o contador e a cópia quarentenada de `destino`. Chamado quando
    o arquivo volta a ser extraído com sucesso: a quarentena anterior foi
    resolvida pelo novo download e não deve contar para uma desistência
    futura."""
    pasta = destino.parent / QUARENTENA_SUBDIR
    for resto in (_contador(destino), pasta / destino.name):
        try:
            resto.unlink(missing_ok=True)
        except OSError:
            pass


def desistido(destino: Path) -> bool:
    """True se o arquivo já foi pra quarentena MAX_QUARENTENAS vezes: o
    coletor não deve baixá-lo de novo."""
    return quarentenas(destino) >= MAX_QUARENTENAS
