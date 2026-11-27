"""Firebird de mentira para testar as ferramentas sem o ATS.

Lê os apelidos das colunas do SELECT (… AS "NOME") e devolve algumas linhas
com valores plausíveis pelo nome da coluna (datas, valores, códigos, textos).
Não confere a lógica das consultas — isso só o ATS de verdade confere —, mas
exercita todo o caminho: formulário → tarefa → script original → Excel/HTML.
"""

import random
import re
from datetime import date, datetime, timedelta

LINHAS = 12


def _colunas(sql):
    s = re.sub(r"--[^\n]*", "", sql)
    # o SELECT principal é o último fora de parênteses (depois de WITH ... AS (...))
    nivel, inicios = 0, []
    for i, ch in enumerate(s):
        if ch == "(":
            nivel += 1
        elif ch == ")":
            nivel -= 1
        elif nivel == 0 and s[i:i + 6].upper() == "SELECT" and (i == 0 or not s[i - 1].isalnum()):
            inicios.append(i + 6)
    corpo = ""
    if inicios:
        resto, nivel = s[inicios[-1]:], 0
        for i, ch in enumerate(resto):
            if ch == "(":
                nivel += 1
            elif ch == ")":
                nivel -= 1
            elif nivel == 0 and resto[i:i + 4].upper() == "FROM" and not resto[i - 1].isalnum():
                corpo = resto[:i]
                break
    if re.match(r"\s*FIRST\s+\d+", corpo, re.I):
        corpo = re.sub(r"^\s*FIRST\s+\d+", "", corpo, flags=re.I)
    corpo = re.sub(r"^\s*DISTINCT\b", "", corpo, flags=re.I)
    partes, nivel, atual = [], 0, ""
    for ch in corpo:
        if ch == "(":
            nivel += 1
        elif ch == ")":
            nivel -= 1
        if ch == "," and nivel == 0:
            partes.append(atual)
            atual = ""
        else:
            atual += ch
    partes.append(atual)
    nomes = []
    for p in partes:
        p = p.strip()
        if not p:
            continue
        m = re.search(r'\bAS\s+"([^"]+)"\s*$', p, re.I) or re.search(r"\bAS\s+(\w+)\s*$", p, re.I)
        if m:
            nomes.append(m.group(1))
        else:
            nomes.append(re.split(r"[.\s]", p)[-1].strip('"') or f"COL{len(nomes)}")
    return nomes


NUMERICAS = ("VALOR", "VLR", "TOTAL", "PRECO", "PREÇO", "CUSTO", "QTD", "QUANT", "ESTOQUE", "DESCONTO", "ICMS", "IPI",
             "PIS", "COFINS", "FRETE", "ENCARGO", "TROCO", "ACRESC", "TROCA", "DINHEIRO", "CH ", "CONVENIO", "CART",
             "CONTAS", "PAGO", "TÍTULO", "TITULO", "SALDO", "JUROS", "VISTA", "BRUTO", "LIQ", "ALIQ", "BASE", "PERC",
             "PRECONF", "VENDIDO")


def _valor(nome, i):
    n = nome.upper()
    hoje = date.today()
    if any(k in n for k in ("DATA", "DT_", "VENC", "EMISS", "MOVIMENTO", "ENTRADA", "ULTIMA_VENDA", "ULTIMA VENDA", "VALIDADE")) \
            and not any(k in n for k in ("QTD", "VLR", "PRECO", "PREÇO", "CUSTO", "VALOR", "FRETE")):
        return datetime.combine(hoje - timedelta(days=(i * 9) % 300), datetime.min.time())
    if n in ("ANO",):
        return hoje.year - (i % 2)
    if n in ("MES",):
        return 1 + i % 12
    if any(k in n for k in NUMERICAS) and not n.startswith("COD"):
        return round(random.Random(hash((n, i))).uniform(1, 900), 2)
    if any(k in n for k in ("COD", "NO PDV", "NO PEDIDO", "DOCUMENTO", "NUMERO", "NF", "ORC")) and "VLR" not in n:
        return str(1000 + i % 7).zfill(8 if "PEDIDO" in n or "ORC" in n else 6)
    if any(k in n for k in ("SITUACAO", "SITUAÇÃO")):
        return ["Aberto", "Parcial", "Pago"][i % 3] if "'" not in n else 1
    if "STATUS" in n:
        return ["Baixado", "Não baixado"][i % 2]
    if "VENDEDOR" in n:
        return ["ANDRE RAMOS", "MAILSON", "MARCIEL", "MARCOS"][i % 4]
    if "REGIME" in n:
        return "simples"
    return f"{nome.title()} {i % 5}"


class _Cursor:
    def __init__(self):
        self.description = None
        self._linhas = []

    def execute(self, sql, params=None):
        nomes = _colunas(sql)
        self.description = [(n, None, None, None, None, None, None) for n in nomes]
        self._linhas = [tuple(_valor(n, i) for n in nomes) for i in range(LINHAS)]
        return self

    def fetchall(self):
        return list(self._linhas)

    def fetchone(self):
        return self._linhas[0] if self._linhas else None

    def fetchmany(self, n=1):
        r, self._linhas = self._linhas[:n], self._linhas[n:]
        return r

    def close(self):
        pass

    def __iter__(self):
        return iter(self._linhas)


class _Conexao:
    def cursor(self):
        return _Cursor()

    def close(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass


def conectar(*a, **k):
    return _Conexao()


def instalar():
    import firebirdsql

    firebirdsql.connect = conectar
