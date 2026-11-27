"""Telas do portal: início, ferramentas, tarefas, painéis e configurações."""

import json
import mimetypes
import re
import socket
from datetime import datetime

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   send_file, url_for)
from werkzeug.utils import secure_filename

from . import auth, config, db, erp, tarefas
from .ferramentas import AREAS_DESCRICAO, GRUPOS, ICONES, carregar_todas, opcoes_de
from .integracao import SISTEMAS

bp = Blueprint("rotas", __name__)

FERRAMENTAS = carregar_todas()
PAINEIS = ("painel-ats",)

# Páginas geradas (painéis, tabelas) rodam isoladas: o "sandbox" sem allow-same-origin
# dá a elas uma origem própria, sem acesso aos cookies e às telas do portal.
CSP_GERADO = (
    "sandbox allow-scripts allow-downloads allow-popups allow-modals allow-forms; "
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src data: https://fonts.gstatic.com; img-src data: blob:; frame-ancestors 'self'"
)
RE_TAREFA = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")


def _valor_padrao(campo):
    return campo.padrao() if callable(campo.padrao) else (campo.padrao or "")


@bp.app_context_processor
def globais():
    return {
        "SISTEMAS": SISTEMAS,
        "GRUPOS": GRUPOS,
        "AREAS_DESCRICAO": AREAS_DESCRICAO,
        "ICONES": ICONES,
        "NOMES_AREAS": dict(GRUPOS),
        "FERRAMENTAS": FERRAMENTAS,
        "valor_padrao": _valor_padrao,
        "opcoes_de": opcoes_de,
        "sistema_ok": lambda n: n in current_app.config.get("SISTEMAS_MONTADOS", set()),
    }


def _tarefa_ou_404(tid):
    if not RE_TAREFA.match(tid or ""):
        abort(404)
    t = db.get_db().execute(
        "SELECT t.*, u.nome AS usuario_nome FROM tarefa t JOIN usuario u ON u.id = t.usuario_id WHERE t.id = ?", (tid,)
    ).fetchone()
    if t is None:
        abort(404)
    if t["usuario_id"] != g.usuario["id"] and not g.usuario["admin"] and t["ferramenta"] not in PAINEIS:
        abort(403)
    return t


def _pasta(tid):
    return tarefas.pasta_tarefas(current_app.config["DATA_DIR"]) / tid


def _ultimos_paineis():
    conn = db.get_db()
    saida = []
    for fid in PAINEIS:
        t = conn.execute(
            "SELECT * FROM tarefa WHERE ferramenta = ? AND situacao = 'ok' AND pagina IS NOT NULL "
            "ORDER BY terminada_em DESC LIMIT 1", (fid,)
        ).fetchone()
        if t is not None and (_pasta(t["id"]) / t["pagina"]).exists():
            saida.append((FERRAMENTAS[fid], t))
    return saida


@bp.route("/")
def inicio():
    recentes = db.get_db().execute(
        "SELECT * FROM tarefa WHERE usuario_id = ? ORDER BY criada_em DESC LIMIT 5", (g.usuario["id"],)
    ).fetchall()
    return render_template("inicio.html", recentes=recentes, paineis=_ultimos_paineis())


@bp.route("/ferramentas")
def ferramentas():
    return render_template("ferramentas.html")


@bp.route("/area/<gid>")
def area(gid):
    nomes = dict(GRUPOS)
    if gid not in nomes:
        abort(404)
    if gid == "paineis":
        return redirect(url_for("rotas.paineis"))
    return render_template("area.html", gid=gid, nome_area=nomes[gid])


@bp.route("/ferramentas/<fid>", methods=["GET", "POST"])
def ferramenta(fid):
    f = FERRAMENTAS.get(fid)
    if f is None:
        abort(404)
    valores = {}
    if request.method == "POST":
        erros, params, arquivos = _ler_formulario(f)
        if not erros:
            tid = tarefas.nova_tarefa(current_app._get_current_object(), f, g.usuario, params, arquivos)
            return redirect(url_for("rotas.tarefa", tid=tid))
        for e in erros:
            flash(e, "erro")
        valores = {k: v for k, v in request.form.items() if k != "csrf"}
    projeto = None
    if fid == "proposta-orcamento" and request.args.get("de"):
        projeto = _projeto_de_tarefa(request.args["de"])
    return render_template("ferramenta.html", f=f, valores=valores, projeto=projeto)


def _projeto_de_tarefa(tid):
    """Reabre uma proposta gerada antes (o .json salvo junto do PDF)."""
    t = _tarefa_ou_404(tid)
    if t["ferramenta"] != "proposta-orcamento":
        return None
    jsons = sorted(_pasta(tid).glob("*.json"))
    if not jsons:
        return None
    try:
        return json.loads(jsons[0].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _ler_formulario(f):
    erros, params, arquivos = [], {}, {}
    for c in f.campos:
        if c.tipo in ("arquivo", "arquivos"):
            enviados = [a for a in request.files.getlist(c.nome) if a and a.filename]
            if c.tipo == "arquivo":
                enviados = enviados[:1]
            aceitas = [e.strip().lower() for e in c.aceita.split(",") if e.strip()]
            lista = []
            for a in enviados:
                nome = secure_filename(a.filename) or "arquivo"
                if aceitas and not nome.lower().endswith(tuple(aceitas)):
                    erros.append(f"{c.rotulo}: envie arquivos {', '.join(aceitas)} ({a.filename} não serve).")
                    continue
                lista.append((nome, a))
            if c.obrigatorio and not lista and not any(c.rotulo in e for e in erros):
                erros.append(f"{c.rotulo}: envie pelo menos um arquivo.")
            arquivos[c.nome] = lista
            continue
        valor = (request.form.get(c.nome) or "").strip()
        if c.tipo == "marcar":
            params[c.nome] = "1" if valor else ""
            continue
        if not valor:
            if c.obrigatorio:
                erros.append(f"Preencha: {c.rotulo}.")
            params[c.nome] = ""
            continue
        if c.tipo in ("empresa", "escolha") and valor not in [str(v) for v, _ in opcoes_de(c)]:
            erros.append(f"{c.rotulo}: opção inválida.")
        elif c.tipo == "data":
            try:
                datetime.strptime(valor, "%Y-%m-%d")
            except ValueError:
                erros.append(f"{c.rotulo}: data inválida.")
        elif c.tipo == "numero" and not re.fullmatch(r"\d+([.,]\d+)?", valor):
            erros.append(f"{c.rotulo}: informe um número.")
        elif c.tipo == "texto" and len(valor) > 500:
            erros.append(f"{c.rotulo}: texto longo demais.")
        params[c.nome] = valor
    return erros, params, arquivos


@bp.route("/tarefas")
def lista_tarefas():
    conn = db.get_db()
    todas = g.usuario["admin"] and request.args.get("todas") == "1"
    sql = ("SELECT t.*, u.nome AS usuario_nome FROM tarefa t JOIN usuario u ON u.id = t.usuario_id "
           + ("" if todas else "WHERE t.usuario_id = ? ") + "ORDER BY t.criada_em DESC LIMIT 300")
    linhas = conn.execute(sql, () if todas else (g.usuario["id"],)).fetchall()
    return render_template("tarefas.html", linhas=linhas, todas=todas)


@bp.route("/tarefas/<tid>")
def tarefa(tid):
    t = _tarefa_ou_404(tid)
    return render_template("tarefa.html", t=t, f=FERRAMENTAS.get(t["ferramenta"]),
                           arquivos=tarefas.arquivos_gerados(_pasta(tid)),
                           params=json.loads(t["parametros"] or "{}"))


@bp.route("/tarefas/<tid>/estado")
def tarefa_estado(tid):
    t = _tarefa_ou_404(tid)
    log = tarefas.log_ao_vivo(tid) if t["situacao"] in ("fila", "rodando") else None
    return jsonify(situacao=t["situacao"], log=log if log is not None else t["log"], erro=t["erro"])


def _arquivo(tid, nome):
    _tarefa_ou_404(tid)
    pasta = _pasta(tid)
    caminho = (pasta / nome).resolve()
    if caminho.parent != pasta.resolve() or not caminho.is_file() or nome.startswith("."):
        abort(404)
    return caminho


@bp.route("/tarefas/<tid>/arquivo/<path:nome>")
def baixar(tid, nome):
    return send_file(_arquivo(tid, nome), as_attachment=True, download_name=nome)


@bp.route("/tarefas/<tid>/ver/<path:nome>")
def ver(tid, nome):
    caminho = _arquivo(tid, nome)
    tipo = mimetypes.guess_type(caminho.name)[0] or "application/octet-stream"
    if tipo not in ("text/html", "application/pdf", "image/jpeg", "image/png"):
        abort(404)
    resp = send_file(caminho, mimetype=tipo, as_attachment=False, max_age=0)
    if tipo == "text/html":
        resp.headers["Content-Security-Policy"] = CSP_GERADO
    return resp


@bp.route("/paineis")
def paineis():
    return render_template("paineis.html", paineis=_ultimos_paineis())


@bp.route("/configuracoes", methods=["GET", "POST"])
@auth.admin_obrigatorio
def configuracoes():
    conn = db.get_db()
    if request.method == "POST":
        novos = {}
        for sigla in config.EMPRESAS_PADRAO:
            novos[sigla] = {campo: (request.form.get(f"{sigla}_{campo}") or "").strip()
                            for campo in ("host", "real", "fiscal")}
        erp.salvar(conn, novos)
        from .integracao import aplicar_erp_nos_sistemas

        aplicar_erp_nos_sistemas(erp.empresas(conn))
        flash("Configurações salvas. Valem para as próximas execuções.", "ok")
        return redirect(url_for("rotas.configuracoes"))
    return render_template("configuracoes.html", empresas=erp.empresas(conn), padrao=config.EMPRESAS_PADRAO,
                           erros=current_app.config.get("SISTEMAS_ERROS", {}))


@bp.route("/configuracoes/testar", methods=["POST"])
@auth.admin_obrigatorio
def testar_conexao():
    resultado = {}
    for sigla, e in erp.empresas(db.get_db()).items():
        try:
            with socket.create_connection((e["host"], config.FB_PORTA), timeout=3):
                resultado[sigla] = "ok"
        except OSError as exc:
            resultado[sigla] = str(exc) or "sem resposta"
    return jsonify(resultado)


@bp.app_errorhandler(403)
def proibido(_e):
    return render_template("erro.html", titulo="Sem permissão",
                           mensagem="Seu usuário não tem acesso a esta página."), 403


@bp.app_errorhandler(404)
def nao_encontrado(_e):
    return render_template("erro.html", titulo="Página não encontrada",
                           mensagem="O endereço não existe ou o arquivo já foi apagado."), 404


@bp.app_errorhandler(413)
def grande_demais(_e):
    return render_template("erro.html", titulo="Arquivo grande demais",
                           mensagem="O envio passou de 200 MB. Envie menos arquivos por vez."), 413
