"""Regras da conferência (funções puras, sem Flask nem banco).

Lançamentos: dicts com id, forma, valor (centavos), pdv_vinculado, aprovado_por.
Partes do ATS: dicts com pdv, forma, valor, hora, vendedor (ver erp.montar_partes).
"""

from . import config

# Situação de cada lançamento depois de cruzar com o ATS.
OK = "ok"
VALOR_DIFERENTE = "valor_diferente"
FORMA_DIFERENTE = "forma_diferente"
PDV_INEXISTENTE = "pdv_inexistente"
SEM_PDV = "sem_pdv"
APROVACAO = "aprovacao"          # dev. crédito / pontos: não vai ao ATS
SAIDA = "saida"

ROTULO_SITUACAO = {
    OK: "ok",
    VALOR_DIFERENTE: "valor diferente",
    FORMA_DIFERENTE: "forma diferente",
    PDV_INEXISTENTE: "PDV não está no ATS",
    SEM_PDV: "sem PDV com esse valor",
}


def rotulo_forma(forma):
    if forma == "outros":
        return config.ROTULO_OUTROS
    return config.FORMAS.get(forma, {}).get("rotulo", forma)


def conciliar(lancamentos, partes):
    """Cruza lançamentos e partes do ATS.

    1. Lançamento ligado à mão a um PDV (pdv_vinculado) fica com aquele PDV, mesmo
       que valor ou forma não batam (aí a diferença aparece).
    2. Os demais procuram, na ordem em que foram digitados, uma parte livre com a
       mesma forma e o mesmo valor (a primeira do dia).
    3. Sobra: lançamento sem PDV e PDV sem lançamento.

    Devolve {"por_lanc": {id: {...}}, "pdvs_sem_lanc": [partes], "pares": [...]}.
    """
    usadas = [False] * len(partes)
    por_lanc = {}

    def livre(cond):
        for i, p in enumerate(partes):
            if not usadas[i] and cond(p):
                usadas[i] = True
                return p
        return None

    conferiveis = []
    for lanc in lancamentos:
        info = config.FORMAS.get(lanc["forma"], {})
        if info.get("tipo") == "saida":
            por_lanc[lanc["id"]] = {"situacao": SAIDA}
        elif info.get("aprovacao"):
            por_lanc[lanc["id"]] = {"situacao": APROVACAO}
        else:
            conferiveis.append(lanc)

    for lanc in conferiveis:
        pdv = (lanc.get("pdv_vinculado") or "").strip()
        if not pdv:
            continue
        parte = livre(lambda p: p["pdv"] == pdv and p["forma"] == lanc["forma"]) or livre(
            lambda p: p["pdv"] == pdv
        )
        if parte is None:
            por_lanc[lanc["id"]] = {"situacao": PDV_INEXISTENTE, "pdv": pdv, "manual": True}
        else:
            if parte["forma"] != lanc["forma"]:
                situacao = FORMA_DIFERENTE
            elif parte["valor"] != lanc["valor"]:
                situacao = VALOR_DIFERENTE
            else:
                situacao = OK
            por_lanc[lanc["id"]] = {"situacao": situacao, "parte": parte, "pdv": pdv, "manual": True}

    for lanc in conferiveis:
        if lanc["id"] in por_lanc:
            continue
        parte = livre(lambda p: p["forma"] == lanc["forma"] and p["valor"] == lanc["valor"])
        if parte is None:
            por_lanc[lanc["id"]] = {"situacao": SEM_PDV}
        else:
            por_lanc[lanc["id"]] = {"situacao": OK, "parte": parte, "pdv": parte["pdv"], "manual": False}

    sobra = [p for i, p in enumerate(partes) if not usadas[i]]
    return {"por_lanc": por_lanc, "pdvs_sem_lanc": sobra}


def totais_por_forma(lancamentos, partes):
    """Lançado × ATS por forma conferível (mais 'outros' se o ATS tiver)."""
    linhas = []
    formas = list(config.FORMAS_ATS)
    if any(p["forma"] == "outros" for p in partes):
        formas.append("outros")
    for forma in formas:
        lancado = sum(l["valor"] for l in lancamentos if l["forma"] == forma)
        ats = sum(p["valor"] for p in partes if p["forma"] == forma)
        linhas.append(
            {"forma": forma, "rotulo": rotulo_forma(forma), "lancado": lancado, "ats": ats, "diferenca": lancado - ats}
        )
    return linhas


def gaveta(saldo_inicial, lancamentos, saldo_contado=None):
    """Dinheiro físico: inicial + entradas em dinheiro − saídas = esperado."""
    dinheiro = sum(l["valor"] for l in lancamentos if l["forma"] == "dinheiro")
    saidas = sum(l["valor"] for l in lancamentos if config.FORMAS.get(l["forma"], {}).get("tipo") == "saida")
    esperado = saldo_inicial + dinheiro - saidas
    return {
        "inicial": saldo_inicial,
        "dinheiro": dinheiro,
        "saidas": saidas,
        "esperado": esperado,
        "contado": saldo_contado,
        "diferenca": None if saldo_contado is None else saldo_contado - esperado,
    }


def resumo(lancamentos, partes, saldo_inicial, saldo_contado=None):
    """Tudo o que as telas precisam, de uma vez."""
    conc = conciliar(lancamentos, partes)
    totais = totais_por_forma(lancamentos, partes)
    gav = gaveta(saldo_inicial, lancamentos, saldo_contado)
    pendentes = [
        l for l in lancamentos if config.FORMAS.get(l["forma"], {}).get("aprovacao") and not l.get("aprovado_por")
    ]
    problemas_lanc = [lid for lid, r in conc["por_lanc"].items() if r["situacao"] not in (OK, SAIDA, APROVACAO)]
    # Se cada lançamento e cada PDV estão ok, os totais por forma também batem.
    divergencias = len(problemas_lanc) + len(conc["pdvs_sem_lanc"]) + (1 if gav["diferenca"] else 0)
    especiais = {
        forma: sum(l["valor"] for l in lancamentos if l["forma"] == forma)
        for forma, info in config.FORMAS.items()
        if info.get("aprovacao")
    }
    return {
        "conciliacao": conc,
        "totais": totais,
        "gaveta": gav,
        "pendentes": pendentes,
        "problemas_lanc": problemas_lanc,
        "divergencias": divergencias,
        "especiais": especiais,
    }
