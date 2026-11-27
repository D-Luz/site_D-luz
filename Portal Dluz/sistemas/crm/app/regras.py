"""Regras do CRM: a fila de contatos do dia, métricas e níveis dos eletricistas.

Funções puras sobre o banco (sem Flask): a fila é recalculada a cada abertura da
tela, então corrigir uma regra vale na hora e nada fica "preso" em tarefas antigas.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median

from . import config, pontos
from .db import cfg, cfg_num
from .formatos import link_whatsapp, primeiro_nome, renderizar, brl

PRIORIDADE = {"RISCO": 1, "ORC": 2, "ACOMP": 3, "EXTRATO": 4, "PARADO": 5, "POS": 6, "ANIV": 7}


def _d(texto):
    return datetime.strptime(texto[:10], "%Y-%m-%d").date()


def _ultimos_contatos(conn):
    """{(tipo, chave): data do último contato}"""
    return {
        (r["tipo"], r["chave"]): r["dia"]
        for r in conn.execute(
            "SELECT tipo, chave, MAX(substr(criado_em, 1, 10)) AS dia FROM contato GROUP BY tipo, chave"
        )
    }


def _ultimo_contato_profissional(conn):
    return {
        r["profissional_id"]: r["dia"]
        for r in conn.execute(
            "SELECT profissional_id, MAX(substr(criado_em, 1, 10)) AS dia FROM contato "
            "WHERE profissional_id IS NOT NULL GROUP BY profissional_id"
        )
    }


def compras_por_profissional(conn):
    """{profissional_id: [(data, total), ...]} das vendas atribuídas, em ordem de data."""
    out = defaultdict(list)
    for r in conn.execute(
        "SELECT a.profissional_id AS pid, v.data, v.total FROM atribuicao a "
        "JOIN venda v ON v.empresa = a.empresa AND v.codigo = a.venda ORDER BY v.data"
    ):
        out[r["pid"]].append((_d(r["data"]), r["total"]))
    return out


def resumo_profissionais(conn, hoje):
    """Lista de eletricistas com nível, última compra e saldo."""
    c = cfg(conn)
    compras = compras_por_profissional(conn)
    saldos = {
        r["profissional_id"]: r["s"]
        for r in conn.execute("SELECT profissional_id, SUM(valor) AS s FROM pontos GROUP BY profissional_id")
    }
    ano = hoje - timedelta(days=365)
    lista = []
    for p in conn.execute("SELECT * FROM profissional ORDER BY ativo DESC, nome"):
        itens = compras.get(p["id"], [])
        total12 = sum(t for d, t in itens if d >= ano)
        ultima = itens[-1][0] if itens else None
        lista.append(
            {
                "p": p,
                "total_12m": total12,
                "compras_12m": sum(1 for d, _ in itens if d >= ano),
                "ultima": ultima,
                "dias": (hoje - ultima).days if ultima else None,
                "nivel": pontos.nivel(total12, c),
                "saldo": saldos.get(p["id"], 0) or 0,
            }
        )
    return lista


def _modelos(conn):
    return {r["tipo"]: r["texto"] for r in conn.execute("SELECT tipo, texto FROM modelo_mensagem")}


def _tarefa(tipo, modelos, c, **kw):
    nome = kw.pop("nome")
    texto = renderizar(
        modelos.get(tipo, ""),
        nome=primeiro_nome(nome) or "tudo bem",
        loja=c.get("loja", "Dluz"),
        valor=brl(kw.get("valor")),
        saldo=brl(kw.get("saldo")),
        dias=kw.get("dias", ""),
        vendedor=primeiro_nome(kw.get("vendedor_nome", "")),
    )
    t = {
        "tipo": tipo,
        "rotulo": config.TIPOS[tipo],
        "prioridade": PRIORIDADE[tipo],
        "nome": nome,
        "telefone": kw.get("telefone", ""),
        "mensagem": texto,
        "link": link_whatsapp(kw.get("telefone", ""), texto),
        "chave": kw["chave"],
        "detalhe": kw.get("detalhe", ""),
        "valor": kw.get("valor"),
        "empresa": kw.get("empresa"),
        "cliente_codigo": kw.get("cliente_codigo"),
        "profissional_id": kw.get("profissional_id"),
    }
    return t


def fila_hoje(conn, hoje=None):
    """Tarefas de contato do dia, da mais urgente para a menos."""
    hoje = hoje or date.today()
    hoje_s = hoje.isoformat()
    c = cfg(conn)
    modelos = _modelos(conn)
    feitos = _ultimos_contatos(conn)
    feito_prof = _ultimo_contato_profissional(conn)
    tarefas = []

    ligados = {
        (r["empresa"], r["cliente_codigo"])
        for r in conn.execute("SELECT empresa, cliente_codigo FROM profissional_cliente")
    }

    def clientes_com_fone():
        return {
            (r["empresa"], r["codigo"]): r
            for r in conn.execute("SELECT * FROM cliente WHERE telefone <> '' AND ativo = 1")
        }

    cli = clientes_com_fone()

    # 1) Orçamentos em aberto: retorno no 1º, 3º e 7º dia.
    limite = int(cfg_num(c, "orcamento_dias"))
    for o in conn.execute(
        "SELECT * FROM orcamento WHERE situacao = 'aberto' AND data >= ? AND data < ? ORDER BY total DESC",
        ((hoje - timedelta(days=limite)).isoformat(), hoje_s),
    ):
        cliente = cli.get((o["empresa"], o["cliente"]))
        if not cliente:
            continue  # orçamento sem cliente identificado ou sem telefone: ver painel
        idade = (hoje - _d(o["data"])).days
        etapa = 7 if idade >= 7 else 3 if idade >= 3 else 1
        inicio_etapa = (_d(o["data"]) + timedelta(days=etapa)).isoformat()
        chave = f"{o['empresa']}:{o['codigo']}"
        ultimo = feitos.get(("ORC", chave))
        if ultimo and ultimo >= inicio_etapa:
            continue
        tarefas.append(
            _tarefa(
                "ORC", modelos, c, nome=cliente["nome"], telefone=cliente["telefone"], chave=chave,
                detalhe=f"Orçamento {o['codigo']} de {o['vendedor_nome'] or 'vendedor'} há {idade} dia(s) · retorno do {etapa}º dia",
                valor=o["total"], empresa=o["empresa"], cliente_codigo=o["cliente"],
                vendedor_nome=o["vendedor_nome"],
            )
        )

    # 2) Eletricistas: risco, acompanhamento, extrato.
    compras = compras_por_profissional(conn)
    saldos = {
        r["profissional_id"]: r["s"]
        for r in conn.execute("SELECT profissional_id, SUM(valor) AS s FROM pontos GROUP BY profissional_id")
    }
    ano = hoje - timedelta(days=365)
    for p in conn.execute("SELECT * FROM profissional WHERE ativo = 1 AND telefone <> ''"):
        itens = compras.get(p["id"], [])
        datas = [d for d, _ in itens]
        total12 = sum(t for d, t in itens if d >= ano)
        nv = pontos.nivel(total12, c)
        ultimo_geral = feito_prof.get(p["id"])
        base = dict(nome=p["nome"], telefone=p["telefone"], profissional_id=p["id"])

        em_risco = False
        if len(datas) >= 3:
            intervalos = [(b - a).days for a, b in zip(datas, datas[1:]) if (b - a).days > 0]
            if intervalos:
                limite_dias = max(21, 2 * median(intervalos[-10:]))
                dias = (hoje - datas[-1]).days
                if limite_dias < dias <= 365:
                    em_risco = True
                    chave = f"prof:{p['id']}"
                    ult = feitos.get(("RISCO", chave))
                    if not ult or (hoje - _d(ult)).days >= 21:
                        tarefas.append(
                            _tarefa("RISCO", modelos, c, chave=chave, dias=dias,
                                    detalhe=f"Sem comprar há {dias} dias (costuma voltar a cada ~{int(median(intervalos[-10:]))}) · nível {nv}",
                                    valor=total12, **base)
                        )

        if nv == "A" and not em_risco and (not ultimo_geral or (hoje - _d(ultimo_geral)).days >= 14):
            tarefas.append(
                _tarefa("ACOMP", modelos, c, chave=f"prof:{p['id']}",
                        detalhe=f"Nível A ({brl(total12)} em 12 meses) sem contato há 14+ dias", valor=total12, **base)
            )

        mes = hoje.strftime("%Y-%m")
        saldo = saldos.get(p["id"], 0) or 0
        if hoje.day <= 7 and saldo >= 20:
            chave = f"prof:{p['id']}:{mes}"
            if ("EXTRATO", chave) not in feitos:
                tarefas.append(
                    _tarefa("EXTRATO", modelos, c, chave=chave, saldo=saldo,
                            detalhe=f"Saldo de pontos {brl(saldo)}", **base)
                )

    # 3) Clientes parados (não eletricistas): 60 / 120 / 180 dias.
    minimo = cfg_num(c, "parado_minimo")
    desde24 = (hoje - timedelta(days=730)).isoformat()
    candidatos = []
    for r in conn.execute(
        "SELECT empresa, cliente, MAX(data) AS ult, SUM(total) AS tot, COUNT(*) AS n FROM venda "
        "WHERE data >= ? GROUP BY empresa, cliente",
        (desde24,),
    ):
        if config.cliente_anonimo(r["cliente"]) or (r["empresa"], r["cliente"]) in ligados:
            continue
        cliente = cli.get((r["empresa"], r["cliente"]))
        if not cliente:
            continue
        dias = (hoje - _d(r["ult"])).days
        if not 60 <= dias <= 365:
            continue
        if r["tot"] < minimo and r["n"] < 3:
            continue
        chave = f"{r['empresa']}:{r['cliente']}"
        ult = feitos.get(("PARADO", chave))
        if ult and (hoje - _d(ult)).days < 45:
            continue
        candidatos.append((r["tot"], cliente, r, dias, chave))
    candidatos.sort(key=lambda x: -x[0])
    for tot, cliente, r, dias, chave in candidatos[: int(cfg_num(c, "parado_por_dia"))]:
        tarefas.append(
            _tarefa("PARADO", modelos, c, nome=cliente["nome"], telefone=cliente["telefone"], chave=chave, dias=dias,
                    detalhe=f"Última compra há {dias} dias · {r['n']} compra(s), {brl(tot)} em 2 anos",
                    valor=tot, empresa=r["empresa"], cliente_codigo=r["cliente"])
        )

    # 4) Pós-venda: 7 a 14 dias depois de uma compra relevante de cliente identificado.
    ultimas = {}
    for r in conn.execute(
        "SELECT * FROM venda WHERE data >= ? AND data <= ? ORDER BY data",
        ((hoje - timedelta(days=14)).isoformat(), (hoje - timedelta(days=7)).isoformat()),
    ):
        ultimas[(r["empresa"], r["cliente"])] = r
    minimo_pos = cfg_num(c, "pos_venda_minimo")
    for (emp, codigo), v in ultimas.items():
        cliente = cli.get((emp, codigo))
        if not cliente or config.cliente_anonimo(codigo) or (emp, codigo) in ligados or v["total"] < minimo_pos:
            continue
        chave = f"{emp}:{codigo}"
        ult = feitos.get(("POS", chave))
        if ult and (hoje - _d(ult)).days < 90:
            continue
        tarefas.append(
            _tarefa("POS", modelos, c, nome=cliente["nome"], telefone=cliente["telefone"], chave=chave,
                    detalhe=f"Comprou {brl(v['total'])} em {_d(v['data']).strftime('%d/%m')} com {v['vendedor_nome'] or 'vendedor'}",
                    valor=v["total"], empresa=emp, cliente_codigo=codigo, vendedor_nome=v["vendedor_nome"])
        )

    # 5) Aniversários de clientes com compra no último ano e de eletricistas.
    md = hoje.strftime("%m-%d")
    for cliente in conn.execute(
        "SELECT * FROM cliente WHERE telefone <> '' AND ativo = 1 AND substr(nascimento, 6, 5) = ? "
        "AND ult_venda >= ?", (md, ano.isoformat()),
    ):
        chave = f"{cliente['empresa']}:{cliente['codigo']}:{hoje.year}"
        if ("ANIV", chave) in feitos:
            continue
        tarefas.append(
            _tarefa("ANIV", modelos, c, nome=cliente["nome"], telefone=cliente["telefone"], chave=chave,
                    detalhe="Aniversário hoje", empresa=cliente["empresa"], cliente_codigo=cliente["codigo"])
        )
    for p in conn.execute(
        "SELECT * FROM profissional WHERE ativo = 1 AND telefone <> '' AND substr(nascimento, 6, 5) = ?", (md,)
    ):
        chave = f"prof:{p['id']}:{hoje.year}"
        if ("ANIV", chave) in feitos:
            continue
        tarefas.append(
            _tarefa("ANIV", modelos, c, nome=p["nome"], telefone=p["telefone"], chave=chave,
                    detalhe="Aniversário hoje (eletricista)", profissional_id=p["id"])
        )

    tarefas.sort(key=lambda t: (t["prioridade"], -(t["valor"] or 0)))
    return tarefas


def painel(conn, hoje=None):
    """Métricas gerenciais. A primeira é a que destrava todas as outras: quanto da venda é identificável."""
    hoje = hoje or date.today()
    c = cfg(conn)
    d30 = (hoje - timedelta(days=30)).isoformat()
    d90 = (hoje - timedelta(days=90)).isoformat()

    vendas = conn.execute("SELECT empresa, codigo, cliente, total FROM venda WHERE data >= ?", (d30,)).fetchall()
    total = sum(v["total"] for v in vendas)
    ident = sum(v["total"] for v in vendas if not config.cliente_anonimo(v["cliente"]))
    atribuidas = {(r["empresa"], r["venda"]) for r in conn.execute("SELECT empresa, venda FROM atribuicao")}
    com_prof = sum(v["total"] for v in vendas if (v["empresa"], v["codigo"]) in atribuidas)
    fones = {(r["empresa"], r["codigo"]) for r in conn.execute("SELECT empresa, codigo FROM cliente WHERE telefone <> ''")}
    contatavel = sum(
        v["total"] for v in vendas
        if not config.cliente_anonimo(v["cliente"]) and (v["empresa"], v["cliente"]) in fones
    )

    orc = conn.execute(
        "SELECT empresa, cliente, total FROM orcamento WHERE situacao = 'aberto' AND data >= ?",
        ((hoje - timedelta(days=int(cfg_num(c, "orcamento_dias")))).isoformat(),),
    ).fetchall()
    orc_total = sum(o["total"] for o in orc)
    orc_contatavel = sum(o["total"] for o in orc if (o["empresa"], o["cliente"]) in fones and not config.cliente_anonimo(o["cliente"]))

    resumo = resumo_profissionais(conn, hoje)
    ativos = [r for r in resumo if r["p"]["ativo"]]
    compraram_90 = sum(1 for r in ativos if r["ultima"] and r["ultima"].isoformat() >= d90)

    contatos_semana = conn.execute(
        "SELECT COUNT(*) FROM contato WHERE substr(criado_em, 1, 10) >= ?", ((hoje - timedelta(days=7)).isoformat(),)
    ).fetchone()[0]

    # Retorno: contato com resultado "conversou" seguido de compra do mesmo cliente/eletricista em até 14 dias.
    conversas = conn.execute(
        "SELECT * FROM contato WHERE resultado = 'conversou' AND substr(criado_em, 1, 10) >= ?",
        ((hoje - timedelta(days=90)).isoformat(),),
    ).fetchall()
    elegiveis = voltaram = 0
    for ct in conversas:
        dia = _d(ct["criado_em"])
        if (hoje - dia).days < 14:
            continue  # ainda dentro da janela de espera
        elegiveis += 1
        fim = (dia + timedelta(days=14)).isoformat()
        if ct["profissional_id"]:
            achou = conn.execute(
                "SELECT 1 FROM atribuicao a JOIN venda v ON v.empresa = a.empresa AND v.codigo = a.venda "
                "WHERE a.profissional_id = ? AND v.data > ? AND v.data <= ? LIMIT 1",
                (ct["profissional_id"], dia.isoformat(), fim),
            ).fetchone()
        else:
            achou = conn.execute(
                "SELECT 1 FROM venda WHERE empresa = ? AND cliente = ? AND data > ? AND data <= ? LIMIT 1",
                (ct["empresa"], ct["cliente_codigo"], dia.isoformat(), fim),
            ).fetchone()
        voltaram += 1 if achou else 0

    mes = hoje.strftime("%Y-%m")
    emitidos = conn.execute(
        "SELECT COALESCE(SUM(valor), 0) FROM pontos WHERE tipo = 'acumulo' AND substr(criado_em, 1, 7) = ?", (mes,)
    ).fetchone()[0]
    passivo = conn.execute("SELECT COALESCE(SUM(valor), 0) FROM pontos").fetchone()[0]

    def pct(a, b):
        return round(100 * a / b, 1) if b else 0.0

    return {
        "vendas_30d": total,
        "pct_identificado": pct(ident, total),
        "pct_contatavel": pct(contatavel, total),
        "pct_com_profissional": pct(com_prof, total),
        "orc_abertos": len(orc),
        "orc_total": orc_total,
        "pct_orc_contatavel": pct(orc_contatavel, orc_total),
        "prof_ativos": len(ativos),
        "prof_compraram_90d": compraram_90,
        "contatos_semana": contatos_semana,
        "conversas_elegiveis": elegiveis,
        "conversas_com_retorno": voltaram,
        "pct_retorno": pct(voltaram, elegiveis),
        "pontos_emitidos_mes": emitidos,
        "pontos_passivo": passivo,
        "niveis": {n: sum(1 for r in ativos if r["nivel"] == n) for n in "ABC"},
    }
