"""Leitura do ATS (Firebird). SOMENTE consultas SELECT: este módulo nunca grava no ATS.

Para um dia, devolve as "partes" do caixa: cada PDV faturado vira uma linha por forma
de pagamento recebida no balcão. Onde o ATS guarda cada forma (conferido em out/2026):

- Dinheiro      ENCEFAT.VLRDINHEIRO
- Cartão        ENCEFAT.VLRCONVENIO (o ATS registra cartão como convênio; VLRCARTAO fica 0)
- Depósito/PIX  PAGAMENTODIGITAL.VALOR com STATUS = 2 (carteira digital, desde ~21/09/2026;
                antes disso o depósito entrava como dinheiro)
- Cheque        ENCEFAT.VLRCHEQUE + VLRCHPREDATADO
- A prazo       ENCEFAT.VLRRECEBER: venda faturada dos vendedores, não passa pelo caixa

A soma das formas fecha com PEDIDOC.TOTALPEDIDO; o que sobrar (ou vier de outra
coluna, como ticket ou financeira) aparece como "outros" para ninguém deixar passar.
"""

import os
import threading
import time
from datetime import date

from . import config
from .formatos import para_centavos


class ErroATS(RuntimeError):
    pass


def _conectar(emp):
    import firebirdsql

    dados = config.EMPRESAS[emp]
    return firebirdsql.connect(
        host=dados["host"],
        port=config.FB_PORTA,
        database=dados["banco"],
        user=config.FB_USUARIO,
        password=config.FB_SENHA,
        charset=config.FB_CHARSET,
        timeout=15,
        readonly=True,
    )


def _select(cur, sql, params=()):
    if not sql.lstrip().upper().startswith("SELECT"):
        raise RuntimeError("Este sistema só pode fazer consultas SELECT no ATS.")
    cur.execute(sql, params)
    return cur.fetchall()


def _s(v):
    return "" if v is None else str(v).strip()


def _hora(v):
    texto = _s(v)
    return texto[:5] if texto else ""


def montar_partes(vendas, digitais):
    """Transforma as linhas do ATS em partes do caixa (função pura, testada à parte).

    vendas: dicts com pdv, hora, vendedor, total, dinheiro, convenio, cartao, cheque,
            chpre, receber, outros (centavos).
    digitais: {pdv: centavos} da carteira digital.
    """
    partes, a_prazo, pdvs = [], 0, 0
    for v in vendas:
        formas = {
            "dinheiro": v["dinheiro"],
            "cartao": v["convenio"] + v["cartao"],
            "deposito": digitais.get(v["pdv"], 0),
            "cheque": v["cheque"] + v["chpre"],
        }
        identificado = sum(formas.values()) + v["receber"] + v["outros"]
        formas["outros"] = v["outros"] + (v["total"] - identificado)
        a_prazo += v["receber"]
        no_caixa = False
        for forma, valor in formas.items():
            if valor != 0:
                no_caixa = True
                partes.append(
                    {
                        "pdv": v["pdv"],
                        "forma": forma,
                        "valor": valor,
                        "hora": v["hora"],
                        "vendedor": v["vendedor"],
                        "total": v["total"],
                    }
                )
        pdvs += no_caixa
    partes.sort(key=lambda p: (p["hora"], p["pdv"], p["forma"]))
    return {"partes": partes, "a_prazo": a_prazo, "pdvs": pdvs}


def _ler_ats(emp, dia):
    try:
        conn = _conectar(emp)
    except Exception as erro:  # rede, servidor desligado, senha...
        raise ErroATS(f"Não foi possível conectar ao ATS da {config.EMPRESAS[emp]['nome']}: {erro}") from erro
    try:
        cur = conn.cursor()
        linhas = _select(
            cur,
            "SELECT c.CODPEDIDO, c.HORA, c.USUARIO, c.TOTALPEDIDO, e.VLRDINHEIRO, e.VLRCONVENIO, "
            "e.VLRCARTAO, e.VLRCHEQUE, e.VLRCHPREDATADO, e.VLRRECEBER, e.VLRTICKET, e.VLRFINANC, "
            "e.VLRBANCO, e.VLRCTF "
            "FROM PEDIDOC c LEFT JOIN ENCEFAT e ON e.CODPEDIDO = c.CODPEDIDO AND e.TIPOPEDIDO = c.TIPOPEDIDO "
            "WHERE c.TIPOPEDIDO = '55' AND c.FATURADO = 'S' AND c.DATAPEDIDO = ?",
            (dia,),
        )
        vendas = [
            {
                "pdv": _s(pdv),
                "hora": _hora(hora),
                "vendedor": _s(usuario),
                "total": para_centavos(total),
                "dinheiro": para_centavos(din),
                "convenio": para_centavos(conv),
                "cartao": para_centavos(cart),
                "cheque": para_centavos(cheq),
                "chpre": para_centavos(chpre),
                "receber": para_centavos(rec),
                "outros": sum(para_centavos(x) for x in (ticket, financ, banco, ctf)),
            }
            for pdv, hora, usuario, total, din, conv, cart, cheq, chpre, rec, ticket, financ, banco, ctf in linhas
        ]
        # Junção em vez de "CODPEDIDO IN (?, ?, ...)": o firebirdsql falha ao preparar
        # consultas com dezenas de parâmetros. PAGAMENTODIGITAL é pequena.
        digitais = {
            _s(pdv): para_centavos(valor)
            for pdv, valor in _select(
                cur,
                "SELECT p.CODPEDIDO, SUM(p.VALOR) FROM PAGAMENTODIGITAL p "
                "JOIN PEDIDOC c ON c.CODPEDIDO = p.CODPEDIDO AND c.TIPOPEDIDO = p.TIPOPEDIDO "
                "WHERE p.TIPOPEDIDO = '55' AND p.STATUS = 2 AND c.FATURADO = 'S' AND c.DATAPEDIDO = ? "
                "GROUP BY p.CODPEDIDO",
                (dia,),
            )
        }
        return montar_partes(vendas, digitais)
    except ErroATS:
        raise
    except Exception as erro:
        raise ErroATS(f"Erro ao ler o ATS da {config.EMPRESAS[emp]['nome']}: {erro}") from erro
    finally:
        conn.close()


def _demo(emp, dia):
    """Dados inventados (CAIXA_ATS_DEMO=1), para ver as telas sem o ATS."""
    base = 1 if emp == "ILU" else 2
    vendas = [
        {"pdv": f"0100{base}791", "hora": "09:12", "vendedor": "CAIXA", "total": 8300, "dinheiro": 8300},
        {"pdv": f"0100{base}798", "hora": "10:40", "vendedor": "MAILSON", "total": 82000},
        {"pdv": f"0100{base}801", "hora": "11:05", "vendedor": "ANDRE", "total": 12000, "convenio": 12000},
        {"pdv": f"0100{base}805", "hora": "13:20", "vendedor": "CAIXA", "total": 18200, "dinheiro": 18200},
        {"pdv": f"0100{base}809", "hora": "14:02", "vendedor": "MARCIEL", "total": 127430, "receber": 127430},
        {"pdv": f"0100{base}812", "hora": "15:48", "vendedor": "CAIXA", "total": 20000},
    ]
    for v in vendas:
        for chave in ("dinheiro", "convenio", "cartao", "cheque", "chpre", "receber", "outros"):
            v.setdefault(chave, 0)
    digitais = {f"0100{base}798": 82000, f"0100{base}812": 20000}
    return montar_partes(vendas, digitais)


_cache = {}
_trava = threading.Lock()


def ler_dia(emp, dia, forcar=False):
    """Partes do caixa de um dia, com cache curto. Levanta ErroATS se o ATS não responder.

    Devolve {"partes", "a_prazo", "pdvs", "lido_em"}.
    """
    if emp not in config.EMPRESAS:
        raise ErroATS(f"Empresa desconhecida: {emp}")
    if isinstance(dia, str):
        dia = date.fromisoformat(dia)
    chave = (emp, dia)
    with _trava:
        guardado = _cache.get(chave)
        if guardado and not forcar and time.monotonic() - guardado[0] < config.ATS_CACHE_SEGUNDOS:
            return guardado[1]
    if os.environ.get("CAIXA_ATS_DEMO"):
        resultado = _demo(emp, dia)
    else:
        resultado = _ler_ats(emp, dia)
    resultado["lido_em"] = time.strftime("%H:%M:%S")
    with _trava:
        _cache[chave] = (time.monotonic(), resultado)
    return resultado


def limpar_cache():
    with _trava:
        _cache.clear()
