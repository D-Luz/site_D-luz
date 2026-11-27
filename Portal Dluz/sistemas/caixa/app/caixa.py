"""Operações do caixa no banco. Cada mudança grava uma linha no histórico (tira-teima)."""

import json
from contextlib import contextmanager

from . import conferencia, config, db
from .formatos import brl, ler_valor


class ErroCaixa(ValueError):
    """Operação recusada; a mensagem é para quem está usando o sistema."""


@contextmanager
def _transacao(conn):
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()


def registrar(conn, caixa_id, usuario_id, acao, detalhe="", lancamento_id=None):
    conn.execute(
        "INSERT INTO historico (caixa_id, lancamento_id, usuario_id, acao, detalhe, em) VALUES (?, ?, ?, ?, ?, ?)",
        (caixa_id, lancamento_id, usuario_id, acao, detalhe, db.agora()),
    )


def _descrever(forma, valor, obs=""):
    texto = f"{conferencia.rotulo_forma(forma)} {brl(valor)}"
    return f"{texto} ({obs})" if obs else texto


# ── leitura ─────────────────────────────────────────────────────────────────
def obter(conn, empresa, data):
    return conn.execute("SELECT * FROM caixa WHERE empresa = ? AND data = ?", (empresa, data)).fetchone()


def por_id(conn, caixa_id):
    return conn.execute("SELECT * FROM caixa WHERE id = ?", (caixa_id,)).fetchone()


def lancamentos(conn, caixa_id, excluidos=False):
    linhas = conn.execute(
        "SELECT l.*, u.nome AS criado_nome, a.nome AS aprovado_nome, m.nome AS alterado_nome "
        "FROM lancamento l JOIN usuario u ON u.id = l.criado_por "
        "LEFT JOIN usuario a ON a.id = l.aprovado_por LEFT JOIN usuario m ON m.id = l.alterado_por "
        "WHERE l.caixa_id = ? AND (? OR l.excluido = 0) ORDER BY l.criado_em, l.id",
        (caixa_id, int(excluidos)),
    ).fetchall()
    return [dict(l) for l in linhas]


def historico(conn, caixa_id):
    return conn.execute(
        "SELECT h.*, u.nome AS usuario_nome FROM historico h JOIN usuario u ON u.id = h.usuario_id "
        "WHERE h.caixa_id = ? ORDER BY h.id",
        (caixa_id,),
    ).fetchall()


def saldo_sugerido(conn, empresa, data):
    """Saldo contado no último dia fechado antes desta data (o fundo que ficou na gaveta)."""
    linha = conn.execute(
        "SELECT saldo_contado FROM caixa WHERE empresa = ? AND data < ? AND saldo_contado IS NOT NULL "
        "ORDER BY data DESC LIMIT 1",
        (empresa, data),
    ).fetchone()
    return linha["saldo_contado"] if linha else 0


def snapshot(caixa):
    return json.loads(caixa["snapshot"]) if caixa["snapshot"] else None


# ── abertura e lançamentos ──────────────────────────────────────────────────
def abrir(conn, empresa, data, saldo_texto, usuario):
    if empresa not in config.EMPRESAS:
        raise ErroCaixa("Empresa inválida.")
    saldo = ler_valor(saldo_texto) if (saldo_texto or "").strip() not in ("", "0", "0,00") else 0
    with _transacao(conn):
        if obter(conn, empresa, data):
            raise ErroCaixa("O caixa desse dia já foi aberto.")
        cur = conn.execute(
            "INSERT INTO caixa (empresa, data, saldo_inicial, aberto_por, aberto_em) VALUES (?, ?, ?, ?, ?)",
            (empresa, data, saldo, usuario["id"], db.agora()),
        )
        registrar(conn, cur.lastrowid, usuario["id"], "abrir", f"Saldo inicial {brl(saldo)}")
    return cur.lastrowid


def _exigir_aberto(caixa):
    if caixa["status"] != "aberto":
        raise ErroCaixa("Este caixa está fechado. Para corrigir, o financeiro precisa reabri-lo.")


def _validar(forma, valor_texto, obs):
    info = config.FORMAS.get(forma)
    if info is None:
        raise ErroCaixa("Escolha a forma.")
    valor = ler_valor(valor_texto)
    obs = (obs or "").strip()
    if (info.get("obs") or info.get("aprovacao")) and not obs:
        raise ErroCaixa(f"{info['rotulo']}: escreva uma observação (o que foi, para quem).")
    return valor, obs[:300]


def lancar(conn, caixa, forma, valor_texto, obs, usuario):
    _exigir_aberto(caixa)
    valor, obs = _validar(forma, valor_texto, obs)
    with _transacao(conn):
        cur = conn.execute(
            "INSERT INTO lancamento (caixa_id, forma, valor, obs, criado_por, criado_em) VALUES (?, ?, ?, ?, ?, ?)",
            (caixa["id"], forma, valor, obs, usuario["id"], db.agora()),
        )
        registrar(conn, caixa["id"], usuario["id"], "lancar", _descrever(forma, valor, obs), cur.lastrowid)
    return cur.lastrowid


def _lanc(conn, caixa, lid):
    l = conn.execute(
        "SELECT * FROM lancamento WHERE id = ? AND caixa_id = ? AND excluido = 0", (lid, caixa["id"])
    ).fetchone()
    if l is None:
        raise ErroCaixa("Lançamento não encontrado.")
    return l


def editar(conn, caixa, lid, forma, valor_texto, obs, usuario):
    _exigir_aberto(caixa)
    antes = _lanc(conn, caixa, lid)
    valor, obs = _validar(forma, valor_texto, obs)
    if (forma, valor, obs) == (antes["forma"], antes["valor"], antes["obs"]):
        return
    with _transacao(conn):
        # Mudou o que foi aprovado: a aprovação deixa de valer.
        conn.execute(
            "UPDATE lancamento SET forma = ?, valor = ?, obs = ?, alterado_por = ?, alterado_em = ?, "
            "aprovado_por = NULL, aprovado_em = NULL, aprovacao_obs = NULL WHERE id = ?",
            (forma, valor, obs, usuario["id"], db.agora(), lid),
        )
        detalhe = f"{_descrever(antes['forma'], antes['valor'], antes['obs'])} → {_descrever(forma, valor, obs)}"
        registrar(conn, caixa["id"], usuario["id"], "alterar", detalhe, lid)


def excluir(conn, caixa, lid, usuario):
    _exigir_aberto(caixa)
    l = _lanc(conn, caixa, lid)
    with _transacao(conn):
        conn.execute(
            "UPDATE lancamento SET excluido = 1, alterado_por = ?, alterado_em = ? WHERE id = ?",
            (usuario["id"], db.agora(), lid),
        )
        registrar(conn, caixa["id"], usuario["id"], "excluir", _descrever(l["forma"], l["valor"], l["obs"]), lid)


def alterar_saldo_inicial(conn, caixa, saldo_texto, usuario):
    _exigir_aberto(caixa)
    texto = (saldo_texto or "").strip()
    saldo = 0 if texto in ("", "0", "0,00") else ler_valor(texto)
    if saldo == caixa["saldo_inicial"]:
        return
    with _transacao(conn):
        conn.execute("UPDATE caixa SET saldo_inicial = ? WHERE id = ?", (saldo, caixa["id"]))
        registrar(
            conn, caixa["id"], usuario["id"], "saldo_inicial", f"{brl(caixa['saldo_inicial'])} → {brl(saldo)}"
        )


def vincular(conn, caixa, lid, pdv, usuario):
    """Liga (ou desliga, com pdv vazio) um lançamento a um PDV escolhido à mão."""
    _exigir_aberto(caixa)
    l = _lanc(conn, caixa, lid)
    pdv = (pdv or "").strip() or None
    if pdv == l["pdv_vinculado"]:
        return
    with _transacao(conn):
        conn.execute("UPDATE lancamento SET pdv_vinculado = ? WHERE id = ?", (pdv, lid))
        detalhe = f"{_descrever(l['forma'], l['valor'])} ligado ao PDV {pdv}" if pdv else (
            f"{_descrever(l['forma'], l['valor'])} desligado do PDV {l['pdv_vinculado']}"
        )
        registrar(conn, caixa["id"], usuario["id"], "vincular", detalhe, lid)


def aprovar(conn, caixa, lid, obs, usuario):
    if caixa["status"] == "conferido":
        raise ErroCaixa("Este caixa já foi conferido.")
    l = _lanc(conn, caixa, lid)
    if not config.FORMAS.get(l["forma"], {}).get("aprovacao"):
        raise ErroCaixa("Só dev. crédito e troca de pontos passam por aprovação.")
    obs = (obs or "").strip()
    if not obs:
        raise ErroCaixa("Escreva a observação da aprovação.")
    with _transacao(conn):
        conn.execute(
            "UPDATE lancamento SET aprovado_por = ?, aprovado_em = ?, aprovacao_obs = ? WHERE id = ?",
            (usuario["id"], db.agora(), obs[:300], lid),
        )
        registrar(conn, caixa["id"], usuario["id"], "aprovar", f"{_descrever(l['forma'], l['valor'])}: {obs}", lid)


# ── fechamento e conferência ────────────────────────────────────────────────
def fechar(conn, caixa, leitura, contado_texto, justificativa, usuario):
    """Fecha o dia com a leitura do ATS deste momento (guardada no snapshot)."""
    _exigir_aberto(caixa)
    texto = (contado_texto or "").strip()
    if not texto:
        raise ErroCaixa("Informe o dinheiro contado na gaveta.")
    contado = 0 if texto in ("0", "0,00") else ler_valor(texto)
    lancs = lancamentos(conn, caixa["id"])
    res = conferencia.resumo(lancs, leitura["partes"], caixa["saldo_inicial"], contado)
    justificativa = (justificativa or "").strip()
    if res["divergencias"] and not justificativa:
        raise ErroCaixa(
            f"Há {res['divergencias']} divergência(s). Corrija ou explique na justificativa antes de fechar."
        )
    foto = {
        "lido_em": leitura.get("lido_em"),
        "partes": leitura["partes"],
        "a_prazo": leitura.get("a_prazo", 0),
        "pdvs": leitura.get("pdvs", 0),
    }
    with _transacao(conn):
        conn.execute(
            "UPDATE caixa SET status = 'fechado', saldo_contado = ?, justificativa = ?, snapshot = ?, "
            "divergencias = ?, fechado_por = ?, fechado_em = ? WHERE id = ?",
            (contado, justificativa, json.dumps(foto), res["divergencias"], usuario["id"], db.agora(), caixa["id"]),
        )
        detalhe = f"Contado {brl(contado)}; esperado {brl(res['gaveta']['esperado'])}; {res['divergencias']} divergência(s)"
        if justificativa:
            detalhe += f". Justificativa: {justificativa}"
        registrar(conn, caixa["id"], usuario["id"], "fechar", detalhe)
    return res


def reabrir(conn, caixa, motivo, usuario):
    if caixa["status"] == "aberto":
        raise ErroCaixa("Este caixa já está aberto.")
    motivo = (motivo or "").strip()
    if not motivo:
        raise ErroCaixa("Escreva o motivo da reabertura.")
    with _transacao(conn):
        conn.execute(
            "UPDATE caixa SET status = 'aberto', snapshot = NULL, divergencias = NULL, fechado_por = NULL, "
            "fechado_em = NULL, conferido_por = NULL, conferido_em = NULL, obs_financeiro = NULL WHERE id = ?",
            (caixa["id"],),
        )
        registrar(conn, caixa["id"], usuario["id"], "reabrir", motivo)


def conferir(conn, caixa, obs, usuario):
    if caixa["status"] != "fechado":
        raise ErroCaixa("Só um caixa fechado pode ser conferido.")
    pendentes = [
        l for l in lancamentos(conn, caixa["id"])
        if config.FORMAS.get(l["forma"], {}).get("aprovacao") and not l["aprovado_por"]
    ]
    if pendentes:
        raise ErroCaixa("Aprove os lançamentos de dev. crédito e troca de pontos antes de conferir.")
    obs = (obs or "").strip()
    if caixa["divergencias"] and not obs:
        raise ErroCaixa("O dia fechou com divergência: escreva a observação da conferência.")
    with _transacao(conn):
        conn.execute(
            "UPDATE caixa SET status = 'conferido', conferido_por = ?, conferido_em = ?, obs_financeiro = ? "
            "WHERE id = ?",
            (usuario["id"], db.agora(), obs, caixa["id"]),
        )
        registrar(conn, caixa["id"], usuario["id"], "conferir", obs)
