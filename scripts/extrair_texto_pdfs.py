#!/usr/bin/env python
"""Extrai texto de todos os PDFs (institucionais + CVM + Central) pro corpus RAG,
e o CSV do Informe de Governança (GOV) da Central.

Pula PDFs da CVM cuja categoria (prefixo do nome do arquivo, ver
baixar_cvm.py:nome_arquivo) é ruído regulatório/jurídico sem valor semântico
pra um chatbot institucional: negociação de valores mobiliários por
insiders, contratos de indenidade, escrituras de debênture e regimentos
internos de conselho/comitê.
"""
import csv
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError, PdfReadError, WrongPasswordError

from cbrag.arquivos import MAX_QUARENTENAS, QUARENTENA_SUBDIR, registrar_quarentena

ROOT = Path(__file__).resolve().parents[1]
PDF_DIRS = [ROOT / "data/pdfs", ROOT / "data/cvm", ROOT / "data/ri_central"]
OUT_DIR = ROOT / "data/corpus_pdf"
# PDF corrompido sai da pasta de origem pra `<pasta>/_quarentena/`: o glob
# de extração não desce em subpasta, e o coletor, que só baixa o que ainda
# não existe, volta a baixar o arquivo no próximo run. Sem isso o arquivo
# quebrado ficava no lugar e falhava todo dia sem nunca ser baixado de novo.
# Depois de MAX_QUARENTENAS (cbrag/arquivos.py) o coletor desiste: o arquivo
# vem quebrado da fonte. Cada quarentena vira uma linha em QUARENTENA_LOG.
QUARENTENA_LOG = ROOT / "data/logs/quarentena.jsonl"
# PDF protegido por senha também levanta PdfReadError, mas o arquivo está
# íntegro: rebaixar não muda nada, então fica como falha comum.
_ERROS_DE_SENHA = (FileNotDecryptedError, WrongPasswordError)

# Prefixo do slug de categoria (após a data) nos nomes gerados por
# baixar_cvm.py. Só se aplica a data/cvm — data/pdfs não tem essas categorias.
# data/ri_central não precisa de exclusão equivalente aqui: baixar_ri_mziq.py
# já pula título de baixo valor (TITULOS_EXCLUIDOS) antes de salvar o PDF.
CATEGORIAS_EXCLUIDAS = re.compile(
    r"^\d{4}_\d{2}_\d{2}_("
    r"valores_mobili_rios_negociados_e_detidos"
    r"|contratos_de_indenidade"
    r"|escrituras_e_aditamentos_de_deb_ntures"
    r"|regimento_interno"
    r"|termo_de_emiss_o_de_nota_comercial"
    r")"
)


def deve_pular(caminho: Path) -> bool:
    return bool(CATEGORIAS_EXCLUIDAS.match(caminho.name))


# Testado com pdfplumber (detecção por linha e por texto) na tabela de DRE
# real de um release CVM: a tabela não tem grade visível no PDF, então
# detecção por linha não acha a área e detecção por texto junta a página
# inteira (prosa incluída) numa "tabela" só, fragmentando frase em palavra
# solta — piorava o texto em vez de melhorar. Mitigação ficou pro prompt do
# agente (main.py), não na extração.
def extrair_texto(caminho: Path) -> str:
    leitor = PdfReader(caminho)
    paginas = [pagina.extract_text() or "" for pagina in leitor.pages]
    return "\n\n".join(paginas).strip()


def sem_trailer(caminho: Path) -> bool:
    """PDF íntegro termina com `%%EOF` (às vezes seguido de espaço ou lixo
    curto). Ausente no último KB = arquivo cortado ou com o fim zerado,
    qualquer que seja a exceção que o pypdf levantou. Erro de I/O ao ler
    (arquivo removido ou bloqueado no meio do run) não é prova de corrupção:
    devolve False e o arquivo conta como falha comum."""
    try:
        with caminho.open("rb") as f:
            f.seek(max(0, caminho.stat().st_size - 1024))
            return b"%%EOF" not in f.read()
    except OSError:
        return False


def corrompido(caminho: Path, erro: Exception) -> bool:
    if isinstance(erro, _ERROS_DE_SENHA):
        return False
    return isinstance(erro, PdfReadError) or sem_trailer(caminho)


def quarentenar(caminho: Path, erro: Exception) -> Path:
    """Move `caminho` pra `<pasta>/_quarentena/`, com o mesmo nome (se o
    arquivo volta quebrado da fonte, a cópia nova substitui a anterior em vez
    de acumular), conta a quarentena e registra a falha em QUARENTENA_LOG.
    Devolve o caminho novo.

    OSError antes do move propaga (o arquivo continua no lugar). Depois do
    move, falha no contador ou no log só gera aviso: o arquivo já saiu da
    pasta de origem e não pode ser reportado como se estivesse lá."""
    agora = datetime.now(timezone.utc)
    pasta = caminho.parent / QUARENTENA_SUBDIR
    pasta.mkdir(exist_ok=True)
    destino = pasta / caminho.name
    tamanho = caminho.stat().st_size
    caminho.replace(destino)
    try:
        total = registrar_quarentena(caminho)
        QUARENTENA_LOG.parent.mkdir(parents=True, exist_ok=True)
        with QUARENTENA_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "gravado_em": agora.isoformat(),
                "arquivo": caminho.name,
                "origem": caminho.parent.name,
                "quarentena": str(destino.relative_to(ROOT)),
                "bytes": tamanho,
                "erro": f"{type(erro).__name__}: {erro}",
                "quarentenas": total,
                "desistido": total >= MAX_QUARENTENAS,
            }, ensure_ascii=False) + "\n")
    except OSError as erro_registro:
        print(f"AVISO quarentena de {caminho.name} sem registro completo: {erro_registro}")
    return destino


_ADOTADA = {"S": "Sim", "N": "Não", "NA": "Não se aplica"}


def extrair_csv_governanca(caminho: Path) -> str:
    """Informe do Código Brasileiro de Governança Corporativa (GOV na Central,
    CSV `;`): uma linha por prática, com a resposta (S/N/NA) e a explicação
    da companhia. Vira uma página de texto por prática pra o chunker não
    separar a prática da resposta."""
    leitor = csv.DictReader(io.StringIO(caminho.read_text(encoding="utf-8-sig")), delimiter=";")
    # o cabeçalho do CSV vem com espaço depois do ";" ("; Código CVM")
    leitor.fieldnames = [nome.strip() for nome in leitor.fieldnames or []]
    linhas = list(leitor)
    if not linhas:
        return ""
    cab = linhas[0]
    blocos = [
        f"Informe do Código Brasileiro de Governança Corporativa - {cab.get('Denominação Social', '').strip()} "
        f"(exercício social de {cab.get('Início Exercício Social', '').strip()} a "
        f"{cab.get('Fim Exercício Social', '').strip()}, versão {cab.get('Versão', '').strip()})"
    ]
    for linha in linhas:
        # o CSV traz a sequência literal "\n" (barra + n) dentro do campo
        pratica = (linha.get("Capítulo - Princípio - Prática") or "").replace("\\n", "\n").strip()
        adotada = (linha.get("Opção") or "").strip()
        explicacao = (linha.get("Explicação") or "").replace("\\n", "\n").strip()
        bloco = f"Prática {pratica}\nAdotada: {_ADOTADA.get(adotada, adotada)}"
        if explicacao:
            bloco += f"\nExplicação: {explicacao}"
        blocos.append(bloco)
    return "\n\n".join(blocos)


def main() -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    extraidos, pulados, falhas, quarentenados = 0, 0, 0, 0

    for pdf_dir in PDF_DIRS:
        for caminho in sorted(pdf_dir.glob("*.pdf")):
            if deve_pular(caminho):
                pulados += 1
                continue

            destino = OUT_DIR / (caminho.stem + ".txt")
            if destino.exists():
                extraidos += 1
                continue

            try:
                texto = extrair_texto(caminho)
            except Exception as e:
                if not corrompido(caminho, e):
                    print(f"FALHA {caminho.name}: {e}")
                    falhas += 1
                    continue
                # Quarentena não pode derrubar o run: sem permissão ou sem
                # disco, o arquivo fica no lugar e conta como falha comum.
                try:
                    destino_q = quarentenar(caminho, e)
                except OSError as erro_q:
                    print(f"FALHA {caminho.name}: {e} (quarentena falhou: {erro_q})")
                    falhas += 1
                    continue
                print(f"QUARENTENA {caminho.name}: {e} -> {destino_q.relative_to(ROOT)}")
                quarentenados += 1
                continue

            if not texto:
                print(f"VAZIO (provavelmente scan/imagem, sem OCR): {caminho.name}")

            destino.write_text(texto, encoding="utf-8")
            extraidos += 1

    for caminho in sorted((ROOT / "data/ri_central").glob("*.csv")):
        destino = OUT_DIR / (caminho.stem + ".txt")
        if destino.exists():
            extraidos += 1
            continue
        try:
            destino.write_text(extrair_csv_governanca(caminho), encoding="utf-8")
            extraidos += 1
        except Exception as e:
            print(f"FALHA {caminho.name}: {e}")
            falhas += 1

    print(
        f"Total: {extraidos} extraídos, {pulados} pulados (categoria excluída), "
        f"{falhas} falharam, {quarentenados} em quarentena (corrompidos; rebaixados no próximo run até "
        f"{MAX_QUARENTENAS} quarentenas, ver data/logs/quarentena.jsonl)"
    )
    return {
        "pdf_extraidos": extraidos,
        "pdf_pulados": pulados,
        "pdf_falhas": falhas,
        "pdf_quarentena": quarentenados,
    }


if __name__ == "__main__":
    main()
