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
import re
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
PDF_DIRS = [ROOT / "data/pdfs", ROOT / "data/cvm", ROOT / "data/ri_central"]
OUT_DIR = ROOT / "data/corpus_pdf"

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
    extraidos, pulados, falhas = 0, 0, 0

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
                print(f"FALHA {caminho.name}: {e}")
                falhas += 1
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

    print(f"Total: {extraidos} extraídos, {pulados} pulados (categoria excluída), {falhas} falharam")
    return {"pdf_extraidos": extraidos, "pdf_pulados": pulados, "pdf_falhas": falhas}


if __name__ == "__main__":
    main()
