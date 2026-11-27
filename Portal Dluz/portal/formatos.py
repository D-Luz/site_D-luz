"""Filtros de exibição (padrão brasileiro)."""

from datetime import datetime


def brl(valor):
    try:
        v = float(valor or 0)
    except (TypeError, ValueError):
        return ""
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def momento(texto):
    """'2026-10-28T09:15:02' -> '28/10/2026 09:15'."""
    if not texto:
        return ""
    try:
        return datetime.fromisoformat(str(texto)).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return str(texto)


def tamanho(n):
    n = float(n or 0)
    for unidade in ("B", "KB", "MB", "GB"):
        if n < 1024 or unidade == "GB":
            return f"{n:.0f} {unidade}" if unidade == "B" else f"{n:.1f} {unidade}".replace(".", ",")
        n /= 1024
