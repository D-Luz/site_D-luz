"""Telas do CRM."""

import sqlite3
from datetime import date, timedelta

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import config, pontos, regras, sincronizar
from .auth import admin_obrigatorio
from .db import cfg, get_db
from .formatos import normalizar_fone

bp = Blueprint("rotas", __name__)

RESULTADOS = {
    "conversou": "Conversou",
    "sem_resposta": "Sem resposta",
    "recusou": "Sem interesse",
    "numero_errado": "Número errado",
}


@bp.app_context_processor
def contexto():
    sinc = sincronizar.ultima(get_db()) if g.get("usuario") else None
    return {"usuario": g.get("usuario"), "ultima_sinc": sinc}


# ── Fila do dia ─────────────────────────────────────────────────────────────
@bp.route("/")
def hoje():
    tarefas = regras.fila_hoje(get_db())
    filtro = request.args.get("tipo", "")
    contagem = {}
    for t in tarefas:
        contagem[t["tipo"]] = contagem.get(t["tipo"], 0) + 1
    if filtro in config.TIPOS:
        tarefas = [t for t in tarefas if t["tipo"] == filtro]
    return render_template("hoje.html", tarefas=tarefas, contagem=contagem, filtro=filtro, resultados=RESULTADOS)


@bp.route("/contato", methods=["POST"])
def registrar_contato():
    f = request.form
    if f.get("tipo") not in config.TIPOS or f.get("resultado") not in RESULTADOS or not f.get("chave"):
        abort(400)
    get_db().execute(
        "INSERT INTO contato (tipo, chave, alvo_nome, empresa, cliente_codigo, profissional_id, resultado, obs, usuario, criado_em) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f["tipo"], f["chave"], f.get("nome", ""), f.get("empresa") or None, f.get("cliente_codigo") or None,
            int(f["profissional_id"]) if f.get("profissional_id") else None,
            f["resultado"], (f.get("obs") or "").strip()[:500], g.usuario["nome"], pontos.agora(),
        ),
    )
    get_db().commit()
    return redirect(url_for("rotas.hoje", tipo=f.get("filtro", "")))


# ── Painel ──────────────────────────────────────────────────────────────────
@bp.route("/painel")
def painel():
    return render_template("painel.html", m=regras.painel(get_db()))


# ── Vendas: quem trouxe a venda? ────────────────────────────────────────────
@bp.route("/vendas", methods=["GET", "POST"])
def vendas():
    conn = get_db()
    if request.method == "POST":
        salvas = 0
        validos = {r["id"] for r in conn.execute("SELECT id FROM profissional WHERE ativo = 1")}
        for chave, valor in request.form.items():
            if not chave.startswith("p:"):
                continue
            _, emp, venda = chave.split(":", 2)
            if emp not in config.EMPRESAS:
                continue
            atual = conn.execute(
                "SELECT profissional_id FROM atribuicao WHERE empresa = ? AND venda = ?", (emp, venda)
            ).fetchone()
            novo = int(valor) if valor.isdigit() and int(valor) in validos else None
            if (atual["profissional_id"] if atual else None) == novo:
                continue
            pontos.atribuir(conn, emp, venda, novo, g.usuario["nome"])
            salvas += 1
        conn.commit()
        flash(f"{salvas} venda(s) atualizada(s).", "ok")
        return redirect(url_for("rotas.vendas", dia=request.form.get("dia", "")))

    dia = request.args.get("dia") or date.today().isoformat()
    lista = conn.execute(
        "SELECT v.*, c.nome AS cliente_nome, a.profissional_id FROM venda v "
        "LEFT JOIN cliente c ON c.empresa = v.empresa AND c.codigo = v.cliente "
        "LEFT JOIN atribuicao a ON a.empresa = v.empresa AND a.venda = v.codigo "
        "WHERE v.data = ? ORDER BY v.empresa, v.codigo",
        (dia,),
    ).fetchall()
    profs = conn.execute("SELECT id, nome FROM profissional WHERE ativo = 1 ORDER BY nome").fetchall()
    ontem = (date.fromisoformat(dia) - timedelta(days=1)).isoformat()
    amanha = (date.fromisoformat(dia) + timedelta(days=1)).isoformat()
    return render_template("vendas.html", lista=lista, profs=profs, dia=dia, ontem=ontem, amanha=amanha,
                           anonimo=config.cliente_anonimo)


# ── Eletricistas ────────────────────────────────────────────────────────────
@bp.route("/profissionais")
def profissionais():
    resumo = regras.resumo_profissionais(get_db(), date.today())
    return render_template("profissionais.html", resumo=resumo)


def _dados_profissional(f):
    nome = (f.get("nome") or "").strip()
    if not nome:
        raise ValueError("Informe o nome.")
    return {
        "nome": nome,
        "telefone": normalizar_fone(f.get("telefone", "")),
        "cpf": "".join(ch for ch in f.get("cpf", "") if ch.isdigit()),
        "nascimento": (f.get("nascimento") or None),
        "obs": (f.get("obs") or "").strip(),
    }


def _vinculo_do_form(f):
    emp, cod = f.get("empresa"), (f.get("cliente_codigo") or "").strip()
    return (emp, cod) if emp in config.EMPRESAS and cod else (None, None)


@bp.route("/profissionais/novo", methods=["GET", "POST"])
def profissional_novo():
    if request.method == "POST":
        try:
            d = _dados_profissional(request.form)
        except ValueError as e:
            flash(str(e), "erro")
            return render_template("profissional_form.html", p=request.form, novo=True), 400
        cur = get_db().execute(
            "INSERT INTO profissional (nome, telefone, cpf, nascimento, obs, inicio_programa, criado_em) "
            "VALUES (:nome, :telefone, :cpf, :nascimento, :obs, :ini, :agora)",
            {**d, "ini": date.today().isoformat(), "agora": pontos.agora()},
        )
        pontos.vincular_cliente(get_db(), cur.lastrowid, *_vinculo_do_form(request.form))
        pontos.atribuir_compras_proprias(get_db())
        get_db().commit()
        flash("Eletricista cadastrado.", "ok")
        return redirect(url_for("rotas.profissional", pid=cur.lastrowid))
    return render_template("profissional_form.html", p={}, novo=True)


@bp.route("/profissionais/importar", methods=["GET", "POST"])
def profissionais_importar():
    conn = get_db()
    segs = [s.strip() for s in cfg(conn)["segmentos_eletricista"].split(",") if s.strip()]
    if request.method == "POST":
        novos = unidos = 0
        for item in request.form.getlist("sel"):
            origem, emp, codigo = item.split(":", 2)
            if emp not in config.EMPRESAS:
                continue
            if origem == "cli":
                r = conn.execute("SELECT * FROM cliente WHERE empresa = ? AND codigo = ?", (emp, codigo)).fetchone()
                dados = (r["nome"], r["telefone"], "", r["nascimento"], codigo) if r else None
            else:
                r = conn.execute("SELECT * FROM erp_profissional WHERE empresa = ? AND codigo = ?", (emp, codigo)).fetchone()
                dados = (r["nome"], r["telefone"], r["cpf"], r["nascimento"], "") if r else None
            if not dados:
                continue
            # Mesmo nome já cadastrado (ex.: o eletricista tem cadastro nas duas lojas): junta, não duplica.
            existente = next(
                (p for p in conn.execute("SELECT id, nome FROM profissional")
                 if pontos.chave_nome(p["nome"]) == pontos.chave_nome(dados[0])),
                None,
            )
            if existente:
                pid = existente["id"]
                unidos += 1
            else:
                pid = conn.execute(
                    "INSERT INTO profissional (nome, telefone, cpf, nascimento, inicio_programa, criado_em) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (dados[0], dados[1], dados[2], dados[3], date.today().isoformat(), pontos.agora()),
                ).lastrowid
                novos += 1
            if origem == "cli":
                pontos.vincular_cliente(conn, pid, emp, codigo)
            else:
                pontos.vincular_codprof(conn, pid, emp, codigo)
        pontos.atribuir_do_erp(conn)
        pontos.atribuir_compras_proprias(conn)
        conn.commit()
        flash(f"{novos} eletricista(s) novo(s); {unidos} cadastro(s) unido(s) a quem já existia.", "ok")
        return redirect(url_for("rotas.profissionais"))

    ja = {(r["empresa"], r["cliente_codigo"]) for r in conn.execute("SELECT empresa, cliente_codigo FROM profissional_cliente")}
    marcas = ",".join("?" * len(segs)) or "''"
    cli = [
        r for r in conn.execute(
            f"SELECT * FROM cliente WHERE segmento IN ({marcas}) AND ativo = 1 ORDER BY ult_venda DESC, nome", segs
        ) if (r["empresa"], r["codigo"]) not in ja
    ]
    erp = conn.execute("SELECT * FROM erp_profissional WHERE ativo = 1 ORDER BY nome").fetchall()
    return render_template("profissionais_importar.html", cli=cli, erp=erp)


@bp.route("/clientes/buscar")
def clientes_buscar():
    q = (request.args.get("q") or "").strip()
    if len(q) < 3:
        return jsonify([])
    rows = get_db().execute(
        "SELECT empresa, codigo, nome, telefone FROM cliente WHERE nome LIKE ? ORDER BY ult_venda DESC LIMIT 12",
        (f"%{q}%",),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@bp.route("/profissionais/<int:pid>", methods=["GET", "POST"])
def profissional(pid):
    conn = get_db()
    p = conn.execute("SELECT * FROM profissional WHERE id = ?", (pid,)).fetchone()
    if p is None:
        abort(404)
    if request.method == "POST":
        try:
            d = _dados_profissional(request.form)
        except ValueError as e:
            flash(str(e), "erro")
            return redirect(url_for("rotas.profissional", pid=pid))
        conn.execute(
            "UPDATE profissional SET nome=:nome, telefone=:telefone, cpf=:cpf, nascimento=:nascimento, "
            "obs=:obs, ativo=:ativo WHERE id=:id",
            {**d, "ativo": 1 if request.form.get("ativo") else 0, "id": pid},
        )
        pontos.atribuir_compras_proprias(conn)
        conn.commit()
        flash("Dados salvos.", "ok")
        return redirect(url_for("rotas.profissional", pid=pid))

    hoje_ = date.today()
    resumo = next(r for r in regras.resumo_profissionais(conn, hoje_) if r["p"]["id"] == pid)
    extrato = conn.execute("SELECT * FROM pontos WHERE profissional_id = ? ORDER BY id DESC LIMIT 60", (pid,)).fetchall()
    compras = conn.execute(
        "SELECT v.*, a.origem FROM atribuicao a JOIN venda v ON v.empresa = a.empresa AND v.codigo = a.venda "
        "WHERE a.profissional_id = ? ORDER BY v.data DESC LIMIT 40", (pid,),
    ).fetchall()
    contatos = conn.execute(
        "SELECT * FROM contato WHERE profissional_id = ? ORDER BY id DESC LIMIT 20", (pid,)
    ).fetchall()
    vinculos = conn.execute(
        "SELECT pc.empresa, pc.cliente_codigo, c.nome FROM profissional_cliente pc "
        "LEFT JOIN cliente c ON c.empresa = pc.empresa AND c.codigo = pc.cliente_codigo "
        "WHERE pc.profissional_id = ? ORDER BY pc.empresa", (pid,),
    ).fetchall()
    return render_template("profissional.html", p=p, r=resumo, extrato=extrato, compras=compras,
                           contatos=contatos, vinculos=vinculos, resultados=RESULTADOS)


@bp.route("/profissionais/<int:pid>/vinculo", methods=["POST"])
def vinculo(pid):
    conn = get_db()
    if conn.execute("SELECT 1 FROM profissional WHERE id = ?", (pid,)).fetchone() is None:
        abort(404)
    emp, cod = _vinculo_do_form(request.form)
    if not emp:
        flash("Escolha um cliente na busca.", "erro")
    elif request.form.get("acao") == "remover":
        conn.execute(
            "DELETE FROM profissional_cliente WHERE profissional_id = ? AND empresa = ? AND cliente_codigo = ?",
            (pid, emp, cod),
        )
        conn.commit()
        flash("Cadastro desvinculado. Vendas já atribuídas continuam com ele.", "ok")
    else:
        dono = conn.execute(
            "SELECT profissional_id FROM profissional_cliente WHERE empresa = ? AND cliente_codigo = ?", (emp, cod)
        ).fetchone()
        if dono and dono["profissional_id"] != pid:
            flash("Esse cadastro já pertence a outro eletricista.", "erro")
        else:
            pontos.vincular_cliente(conn, pid, emp, cod)
            pontos.atribuir_compras_proprias(conn)
            conn.commit()
            flash("Cadastro vinculado: as compras dele passam a contar.", "ok")
    return redirect(url_for("rotas.profissional", pid=pid))


@bp.route("/profissionais/<int:pid>/resgate", methods=["POST"])
def resgate(pid):
    try:
        valor = float((request.form.get("valor") or "0").replace(".", "").replace(",", "."))
        pontos.resgatar(get_db(), pid, valor, g.usuario["nome"], request.form.get("descricao", ""))
        get_db().commit()
        flash("Resgate registrado. Lance o desconto/crédito no ERP.", "ok")
    except ValueError as e:
        flash(str(e), "erro")
    return redirect(url_for("rotas.profissional", pid=pid))


@bp.route("/profissionais/<int:pid>/ajuste", methods=["POST"])
@admin_obrigatorio
def ajuste(pid):
    try:
        valor = float((request.form.get("valor") or "0").replace(".", "").replace(",", "."))
        pontos.ajustar(get_db(), pid, valor, request.form.get("motivo", ""), g.usuario["nome"])
        get_db().commit()
        flash("Ajuste registrado.", "ok")
    except ValueError as e:
        flash(str(e), "erro")
    return redirect(url_for("rotas.profissional", pid=pid))


# ── Orçamentos ──────────────────────────────────────────────────────────────
@bp.route("/orcamentos")
def orcamentos():
    conn = get_db()
    dias = int(float(cfg(conn)["orcamento_dias"]))
    desde = (date.today() - timedelta(days=dias)).isoformat()
    lista = conn.execute(
        "SELECT o.*, c.nome AS cliente_nome, c.telefone FROM orcamento o "
        "LEFT JOIN cliente c ON c.empresa = o.empresa AND c.codigo = o.cliente "
        "WHERE o.situacao = 'aberto' AND o.data >= ? ORDER BY o.total DESC",
        (desde,),
    ).fetchall()
    return render_template("orcamentos.html", lista=lista, anonimo=config.cliente_anonimo, dias=dias)


# ── Configurações e sincronização ───────────────────────────────────────────
@bp.route("/config", methods=["GET", "POST"])
@admin_obrigatorio
def configuracoes():
    conn = get_db()
    if request.method == "POST":
        for chave in config.PADROES:
            if chave in request.form:
                valor = request.form[chave].strip().replace(",", ".") if chave not in ("loja", "segmentos_eletricista") else request.form[chave].strip()
                if chave not in ("loja", "segmentos_eletricista"):
                    try:
                        if float(valor) < 0:
                            raise ValueError
                    except ValueError:
                        flash(f"Valor inválido em «{chave}».", "erro")
                        return redirect(url_for("rotas.configuracoes"))
                conn.execute("UPDATE config SET valor = ? WHERE chave = ?", (valor, chave))
        for tipo in config.MODELOS:
            texto = (request.form.get(f"modelo_{tipo}") or "").strip()
            if texto:
                conn.execute("UPDATE modelo_mensagem SET texto = ? WHERE tipo = ?", (texto, tipo))
        conn.commit()
        flash("Configurações salvas.", "ok")
        return redirect(url_for("rotas.configuracoes"))
    modelos = {r["tipo"]: r["texto"] for r in conn.execute("SELECT tipo, texto FROM modelo_mensagem")}
    return render_template("config.html", c=cfg(conn), modelos=modelos)


@bp.route("/sincronizar", methods=["POST"])
@admin_obrigatorio
def sincronizar_agora():
    resumo = sincronizar.sincronizar(get_db())
    erros = [f"{k}: {v['erro']}" for k, v in resumo.items() if isinstance(v, dict) and "erro" in v]
    if erros:
        flash("Sincronização com falhas — " + " | ".join(erros), "erro")
    else:
        flash("Sincronizado com o ERP.", "ok")
    return redirect(request.referrer or url_for("rotas.hoje"))
