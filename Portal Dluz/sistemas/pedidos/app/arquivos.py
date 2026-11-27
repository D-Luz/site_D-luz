"""Validação e armazenamento seguro do PDF anexado ao pedido.

O arquivo é gravado em ``DATA_DIR/pdfs/`` com um nome gerado pelo sistema
(32 caracteres hexadecimais + ``.pdf``). O nome que o usuário enviou é só um
texto para exibição: nunca vira caminho em disco, o que elimina nomes
inválidos, acentos, barras e o limite de 255 caracteres.
"""

import os
import re
import unicodedata
import uuid
from pathlib import Path

from flask import current_app

EXTENSAO = ".pdf"
ASSINATURA = b"%PDF-"
NOME_PADRAO = "arquivo.pdf"
TAMANHO_MAX_NOME = 255
TAMANHO_BLOCO = 64 * 1024

_BIDI = frozenset("\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
_NOME_GERADO = re.compile(r"[0-9a-f]{32}\.pdf")


class PdfInvalido(Exception):
    """Arquivo recusado; ``str(exc)`` é a mensagem pronta para mostrar ao usuário."""


def _pasta_pdfs():
    return Path(current_app.config["DATA_DIR"]) / "pdfs"


def _invisivel(caractere):
    """Controle (inclui NUL, CR, LF, tab) ou marca bidirecional que disfarça o nome."""
    return unicodedata.category(caractere) == "Cc" or caractere in _BIDI


def _limpar_nome(nome):
    """Reduz o nome enviado a um texto seguro para exibição (até 255 caracteres)."""
    nome = re.split(r"[/\\]", nome)[-1]
    nome = "".join(c for c in nome if not _invisivel(c)).strip()
    if not nome.lower().endswith(EXTENSAO):
        return nome  # o chamador recusa; mantém o texto só para a checagem
    raiz, extensao = nome[: -len(EXTENSAO)].strip(), nome[-len(EXTENSAO) :]
    if not raiz:
        return NOME_PADRAO
    return raiz[: TAMANHO_MAX_NOME - len(extensao)] + extensao


def _ler_limitado(stream, limite):
    """Lê no máximo ``limite + 1`` bytes: o byte extra revela que passou do limite."""
    partes, total = [], 0
    while total <= limite:
        bloco = stream.read(min(TAMANHO_BLOCO, limite + 1 - total))
        if not bloco:
            break
        partes.append(bloco)
        total += len(bloco)
    return b"".join(partes)


def salvar_pdf(arquivo):
    """Valida e grava o PDF; devolve ``(nome_gerado, nome_original)``.

    Levanta ``PdfInvalido`` se não houver arquivo, se a extensão não for
    ``.pdf``, se passar do limite, se estiver vazio ou se o conteúdo não
    começar com ``%PDF-``. O Content-Type enviado pelo cliente é ignorado.
    """
    if arquivo is None or not arquivo.filename:
        raise PdfInvalido("Selecione um arquivo PDF.")
    nome_original = _limpar_nome(arquivo.filename)
    if not nome_original.lower().endswith(EXTENSAO):
        raise PdfInvalido("O arquivo precisa ter a extensão .pdf.")

    limite_mb = current_app.config["MAX_PDF_MB"]
    conteudo = _ler_limitado(arquivo.stream, limite_mb * 1024 * 1024)
    if len(conteudo) > limite_mb * 1024 * 1024:
        raise PdfInvalido(f"O arquivo é maior que o limite de {limite_mb} MB.")
    if not conteudo:
        raise PdfInvalido("O arquivo está vazio.")
    if not conteudo.startswith(ASSINATURA):
        raise PdfInvalido("O arquivo não é um PDF válido.")

    pasta = _pasta_pdfs()
    pasta.mkdir(parents=True, exist_ok=True)
    nome_gerado = uuid.uuid4().hex + EXTENSAO
    temporario = pasta / f".tmp-{uuid.uuid4().hex}"
    try:
        with open(temporario, "xb") as saida:
            saida.write(conteudo)
            saida.flush()
            os.fsync(saida.fileno())
        os.replace(temporario, pasta / nome_gerado)
    except BaseException:
        temporario.unlink(missing_ok=True)
        raise
    return nome_gerado, nome_original


def caminho_pdf(nome_gerado):
    """Caminho do PDF em disco; só aceita nomes gerados por ``salvar_pdf``."""
    if not isinstance(nome_gerado, str) or not _NOME_GERADO.fullmatch(nome_gerado):
        raise PdfInvalido("Nome de arquivo inválido.")
    pasta = _pasta_pdfs()
    caminho = pasta / nome_gerado
    if caminho.resolve().parent != pasta.resolve():
        raise PdfInvalido("Nome de arquivo inválido.")
    return caminho


def pdf_existe(nome_gerado):
    """True só se o nome é válido e o arquivo ainda existe no disco."""
    try:
        return caminho_pdf(nome_gerado).is_file()
    except PdfInvalido:
        return False
