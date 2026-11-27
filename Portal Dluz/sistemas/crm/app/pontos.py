"""Atribuição de vendas a eletricistas e livro de pontos (1 ponto = R$ 1,00)."""

from datetime import datetime

from . import config
from .db import cfg, cfg_num


def agora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def saldo(conn, profissional_id):
    return conn.execute(
        "SELECT COALESCE(SUM(valor), 0) FROM pontos WHERE profissional_id = ?", (profissional_id,)
    ).fetchone()[0]


def _acumular(conn, empresa, venda, profissional_id, usuario):
    """Gera (ou refaz) os pontos de uma venda atribuída. Só vale a partir do início do programa."""
    conn.execute(
        "DELETE FROM pontos WHERE tipo = 'acumulo' AND empresa = ? AND venda = ?", (empresa, venda)
    )
    linha = conn.execute(
        "SELECT v.data, v.total, p.inicio_programa FROM venda v, profissional p "
        "WHERE v.empresa = ? AND v.codigo = ? AND p.id = ?",
        (empresa, venda, profissional_id),
    ).fetchone()
    if linha is None or linha["data"] < linha["inicio_programa"]:
        return
    pct = cfg_num(cfg(conn), "pontos_percentual")
    valor = round(linha["total"] * pct / 100, 2)
    if valor <= 0:
        return
    conn.execute(
        "INSERT INTO pontos (profissional_id, tipo, valor, empresa, venda, descricao, usuario, criado_em) "
        "VALUES (?, 'acumulo', ?, ?, ?, ?, ?, ?)",
        (profissional_id, valor, empresa, venda, f"Venda {venda} ({pct:g}%)", usuario, agora()),
    )


def atribuir(conn, empresa, venda, profissional_id, usuario, origem="manual"):
    """Vincula a venda ao eletricista (ou desfaz, se profissional_id for None)."""
    if profissional_id is None:
        conn.execute("DELETE FROM atribuicao WHERE empresa = ? AND venda = ?", (empresa, venda))
        conn.execute(
            "DELETE FROM pontos WHERE tipo = 'acumulo' AND empresa = ? AND venda = ?", (empresa, venda)
        )
        return
    conn.execute(
        "INSERT INTO atribuicao (empresa, venda, profissional_id, origem, usuario, criado_em) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (empresa, venda) DO UPDATE SET profissional_id = excluded.profissional_id, "
        "origem = excluded.origem, usuario = excluded.usuario, criado_em = excluded.criado_em",
        (empresa, venda, profissional_id, origem, usuario, agora()),
    )
    _acumular(conn, empresa, venda, profissional_id, usuario)


def atribuir_do_erp(conn):
    """Vendas em que o próprio ATS indica o eletricista (COMPPEDIDOC.CODPROF).

    Vale mais que a compra no cadastro próprio e não sobrescreve o que uma pessoa
    já corrigiu à mão no CRM (origem 'manual').
    """
    novas = conn.execute(
        "SELECT v.empresa, v.codigo, pe.profissional_id FROM venda v "
        "JOIN profissional_erp pe ON pe.empresa = v.empresa AND pe.codprof = v.codprof "
        "JOIN profissional p ON p.id = pe.profissional_id AND p.ativo = 1 "
        "LEFT JOIN atribuicao a ON a.empresa = v.empresa AND a.venda = v.codigo "
        "WHERE v.codprof IS NOT NULL AND (a.venda IS NULL OR (a.origem <> 'manual' AND a.profissional_id <> pe.profissional_id))"
    ).fetchall()
    for r in novas:
        atribuir(conn, r["empresa"], r["codigo"], r["profissional_id"], "sistema", origem="erp")
    return len(novas)


def vincular_codprof(conn, profissional_id, empresa, codprof):
    if not empresa or not (codprof or "").strip():
        return
    conn.execute(
        "INSERT OR IGNORE INTO profissional_erp (profissional_id, empresa, codprof) VALUES (?, ?, ?)",
        (profissional_id, empresa, codprof.strip()),
    )


def atribuir_compras_proprias(conn):
    """Compras feitas no próprio cadastro do eletricista contam para ele automaticamente."""
    novas = conn.execute(
        "SELECT v.empresa, v.codigo, p.id FROM profissional p "
        "JOIN profissional_cliente pc ON pc.profissional_id = p.id "
        "JOIN venda v ON v.empresa = pc.empresa AND v.cliente = pc.cliente_codigo "
        "LEFT JOIN atribuicao a ON a.empresa = v.empresa AND a.venda = v.codigo "
        "WHERE p.ativo = 1 AND a.venda IS NULL"
    ).fetchall()
    for r in novas:
        atribuir(conn, r["empresa"], r["codigo"], r["id"], "sistema", origem="cliente")
    return len(novas)


def remover_pontos_de_vendas_canceladas(conn, empresa):
    """Venda que sumiu do ERP (cancelada) deixa de render pontos. Devolução parcial é ajuste manual.

    Só roda depois de uma leitura completa da empresa; o histórico importado (2 anos)
    é bem maior que o tempo de existência do programa, então nada válido some.
    """
    cur = conn.execute(
        "DELETE FROM pontos WHERE tipo = 'acumulo' AND empresa = ? "
        "AND NOT EXISTS (SELECT 1 FROM venda v WHERE v.empresa = pontos.empresa AND v.codigo = pontos.venda)",
        (empresa,),
    )
    conn.execute(
        "DELETE FROM atribuicao WHERE empresa = ? AND NOT EXISTS "
        "(SELECT 1 FROM venda v WHERE v.empresa = atribuicao.empresa AND v.codigo = atribuicao.venda)",
        (empresa,),
    )
    return cur.rowcount


def resgatar(conn, profissional_id, valor, usuario, descricao=""):
    if valor <= 0:
        raise ValueError("O valor do resgate deve ser maior que zero.")
    if valor > saldo(conn, profissional_id) + 0.005:
        raise ValueError("O resgate é maior que o saldo disponível.")
    conn.execute(
        "INSERT INTO pontos (profissional_id, tipo, valor, descricao, usuario, criado_em) "
        "VALUES (?, 'resgate', ?, ?, ?, ?)",
        (profissional_id, -round(valor, 2), descricao or "Resgate no balcão", usuario, agora()),
    )


def ajustar(conn, profissional_id, valor, motivo, usuario):
    if not motivo.strip():
        raise ValueError("Informe o motivo do ajuste.")
    conn.execute(
        "INSERT INTO pontos (profissional_id, tipo, valor, descricao, usuario, criado_em) "
        "VALUES (?, 'ajuste', ?, ?, ?, ?)",
        (profissional_id, round(valor, 2), motivo.strip(), usuario, agora()),
    )


def vincular_cliente(conn, profissional_id, empresa, cliente_codigo):
    """Liga um cadastro de cliente do ERP ao eletricista (um cadastro pertence a um só eletricista)."""
    if not empresa or not (cliente_codigo or "").strip():
        return
    conn.execute(
        "INSERT OR IGNORE INTO profissional_cliente (profissional_id, empresa, cliente_codigo) VALUES (?, ?, ?)",
        (profissional_id, empresa, cliente_codigo.strip()),
    )


def chave_nome(nome):
    """Nome sem apelidos entre parênteses, para reconhecer o mesmo eletricista em duas lojas."""
    import re

    return " ".join(re.sub(r"\(.*?\)", " ", (nome or "").upper()).split())


def nivel(total_12m, c):
    if total_12m >= cfg_num(c, "nivel_a"):
        return "A"
    if total_12m >= cfg_num(c, "nivel_b"):
        return "B"
    return "C"
