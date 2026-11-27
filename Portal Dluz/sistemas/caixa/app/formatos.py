"""Valores em reais (centavos inteiros) e datas no padrão brasileiro."""

import re
from datetime import date, datetime


class ValorInvalido(ValueError):
    pass


def ler_valor(texto):
    """'1.234,56', '1234,56', '1234.56', 'R$ 83' -> centavos (int). Levanta ValorInvalido."""
    t = (texto or "").strip().replace("R$", "").replace(" ", "")
    if not t:
        raise ValorInvalido("Informe o valor.")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")  # 1.234 = mil duzentos e trinta e quatro
    if not re.fullmatch(r"\d+(\.\d{1,2})?", t):
        raise ValorInvalido(f"Valor inválido: {texto!r}. Use, por exemplo, 1.234,56.")
    reais, _, cent = t.partition(".")
    valor = int(reais) * 100 + int((cent + "00")[:2])
    if valor <= 0:
        raise ValorInvalido("O valor precisa ser maior que zero.")
    return valor


def para_centavos(numero):
    """Valor do ATS (float/Decimal/None) -> centavos, arredondando."""
    return int(round(float(numero or 0) * 100))


def brl(centavos, sinal=False):
    """123456 -> 'R$ 1.234,56'. Com sinal=True, positivos ganham '+'."""
    v = int(centavos or 0)
    reais, resto = divmod(abs(v), 100)
    texto = f"{reais:,}".replace(",", ".") + f",{resto:02d}"
    if v < 0:
        return f"−{texto}"
    return f"+{texto}" if sinal and v > 0 else texto


def data_br(valor):
    if not valor:
        return ""
    try:
        return date.fromisoformat(str(valor)[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return str(valor)


def hora(valor):
    """'2026-10-26 14:32:05' -> '14:32'."""
    texto = str(valor or "")
    return texto[11:16] if len(texto) >= 16 else texto[:5]


def momento(valor):
    """'2026-10-26 14:32:05' -> '26/10/2026 14:32:05'."""
    texto = str(valor or "")
    try:
        return datetime.fromisoformat(texto).strftime("%d/%m/%Y %H:%M:%S")
    except ValueError:
        return texto


DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


def dia_semana(valor):
    try:
        return DIAS[date.fromisoformat(str(valor)[:10]).weekday()]
    except ValueError:
        return ""
