"""Formatação e utilidades de texto (telefone, WhatsApp, datas, moeda)."""

import re
from datetime import date, datetime
from urllib.parse import quote


def brl(valor):
    try:
        v = float(valor or 0)
    except (TypeError, ValueError):
        return "R$ 0,00"
    texto = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-R$ {texto}" if v < 0 else f"R$ {texto}"


def data_br(texto):
    if not texto:
        return ""
    try:
        return datetime.strptime(str(texto)[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return str(texto)


def para_data(valor):
    """date/datetime/'AAAA-MM-DD' -> 'AAAA-MM-DD' (ou None)."""
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    return str(valor)[:10]


def primeiro_nome(nome):
    limpo = re.sub(r"\(.*?\)", "", nome or "").strip()
    return limpo.split(" ")[0].title() if limpo else ""


def normalizar_fone(*valores):
    """Primeiro número utilizável (com DDD) em formato 55DDDNNNNNNNNN, ou ''."""
    for v in valores:
        d = re.sub(r"\D", "", v or "").lstrip("0")
        if d.startswith("55") and len(d) in (12, 13):
            return d
        if len(d) in (10, 11):
            return "55" + d
    return ""


def fone_legivel(fone):
    d = re.sub(r"\D", "", fone or "")
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return fone or ""


def link_whatsapp(fone, texto):
    if not fone:
        return None
    return f"https://wa.me/{fone}?text={quote(texto or '')}"


class _Vazio(dict):
    def __missing__(self, chave):
        return ""


def renderizar(modelo, **variaveis):
    """Preenche {nome}, {valor} etc.; variável desconhecida vira vazio."""
    try:
        return modelo.format_map(_Vazio(variaveis))
    except (ValueError, IndexError):
        return modelo
