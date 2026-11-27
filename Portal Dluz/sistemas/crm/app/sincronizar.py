"""Sincronização: copia do ERP (somente leitura) para o banco do CRM."""

import json
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import config, erp, pontos
from .db import cfg, cfg_num


def marcar_orcamentos(orcamentos, vendas):
    """Classifica os orçamentos (DAVs 'O' em aberto).

    O ERP não liga o DAV ao pedido que o baixou: quando baixa, cria um pedido novo
    com o mesmo vendedor e valor. Por isso "provavel_baixado" = existe venda posterior
    (ou do mesmo dia) do mesmo vendedor com o mesmo total. Cópias idênticas (mesmo
    vendedor, cliente, total e dia) ficam só com a de menor código.
    """
    por_chave = defaultdict(list)
    for v in vendas:
        por_chave[(v["vendedor"], round(v["total"], 2))].append(v["data"])

    grupos = defaultdict(list)
    for o in orcamentos:
        grupos[(o["vendedor"], o["cliente"], round(o["total"], 2), o["data"])].append(o["codigo"])
    menor = {k: min(cods) for k, cods in grupos.items()}

    for o in orcamentos:
        chave = (o["vendedor"], round(o["total"], 2))
        if any(d >= o["data"] for d in por_chave.get(chave, ())):
            o["situacao"] = "provavel_baixado"
        elif o["codigo"] != menor[(o["vendedor"], o["cliente"], round(o["total"], 2), o["data"])]:
            o["situacao"] = "copia"
        else:
            o["situacao"] = "aberto"
    return orcamentos


def gravar_empresa(conn, emp, dados):
    """Substitui o espelho de uma empresa numa transação única."""
    for tabela in ("cliente", "venda", "orcamento", "erp_profissional"):
        conn.execute(f"DELETE FROM {tabela} WHERE empresa = ?", (emp,))
    conn.executemany(
        "INSERT INTO cliente (empresa, codigo, nome, telefone, segmento, ult_venda, nascimento, ativo) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (emp, c["codigo"], c["nome"], c["telefone"], c["segmento"], c["ult_venda"], c["nascimento"], c["ativo"])
            for c in dados["clientes"]
        ],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO venda (empresa, codigo, data, cliente, vendedor, vendedor_nome, total, codprof) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (emp, v["codigo"], v["data"], v["cliente"], v["vendedor"], v["vendedor_nome"], v["total"], v.get("codprof") or None)
            for v in dados["vendas"]
            if v["data"]
        ],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO orcamento (empresa, codigo, data, cliente, vendedor, vendedor_nome, total, situacao) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (emp, o["codigo"], o["data"], o["cliente"], o["vendedor"], o["vendedor_nome"], o["total"], o["situacao"])
            for o in dados["orcamentos"]
            if o["data"]
        ],
    )
    conn.executemany(
        "INSERT INTO erp_profissional (empresa, codigo, nome, telefone, nascimento, cpf, ativo) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (emp, p["codigo"], p["nome"], p["telefone"], p["nascimento"], p["cpf"], p["ativo"])
            for p in dados["profissionais"]
        ],
    )


def sincronizar(conn, empresas=None, fonte=erp, hoje=None):
    """Lê o ERP e atualiza o CRM. Uma empresa que falha não derruba as outras."""
    hoje = hoje or date.today()
    empresas = empresas or list(config.EMPRESAS)
    janela = int(cfg_num(cfg(conn), "janela_vendas_dias"))
    desde = hoje - timedelta(days=janela)
    inicio = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute("INSERT INTO sincronizacao (inicio) VALUES (?)", (inicio,))
    sync_id = cur.lastrowid
    conn.commit()

    resumo, tudo_ok = {}, True
    for emp in empresas:
        try:
            dados = fonte.ler(emp, desde)
            dados["orcamentos"] = marcar_orcamentos(dados["orcamentos"], dados["vendas"])
            gravar_empresa(conn, emp, dados)
            removidos = pontos.remover_pontos_de_vendas_canceladas(conn, emp)
            conn.commit()
            resumo[emp] = {
                "clientes": len(dados["clientes"]),
                "vendas": len(dados["vendas"]),
                "orcamentos": len(dados["orcamentos"]),
                "pontos_removidos": removidos,
            }
        except Exception as erro:  # noqa: BLE001 - qualquer falha de rede/ERP vira aviso
            conn.rollback()
            tudo_ok = False
            resumo[emp] = {"erro": f"{type(erro).__name__}: {erro}"[:300]}

    resumo["indicadas_no_erp_atribuidas"] = pontos.atribuir_do_erp(conn)
    resumo["compras_proprias_atribuidas"] = pontos.atribuir_compras_proprias(conn)
    conn.execute(
        "UPDATE sincronizacao SET fim = ?, ok = ?, resumo = ? WHERE id = ?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 1 if tudo_ok else 0, json.dumps(resumo, ensure_ascii=False), sync_id),
    )
    conn.commit()
    return resumo


def ultima(conn):
    return conn.execute("SELECT * FROM sincronizacao ORDER BY id DESC LIMIT 1").fetchone()
