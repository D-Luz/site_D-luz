"""Login, sessão e proteção CSRF. Cada ação no CRM fica registrada com o nome de quem a fez."""

import functools
import hmac
import secrets
import time

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, portal

bp = Blueprint("auth", __name__)

MENSAGEM_LOGIN = "Usuário ou senha incorretos."
_HASH_FALSO = generate_password_hash("senha-falsa-para-igualar-o-tempo")

# Tentativas falhas por endereço: 6 erros em 10 minutos bloqueiam por 10 minutos.
_falhas = {}
LIMITE, JANELA = 6, 600


def _bloqueado(ip):
    agora = time.time()
    recentes = [t for t in _falhas.get(ip, []) if agora - t < JANELA]
    _falhas[ip] = recentes
    return len(recentes) >= LIMITE


def criar_usuario(conn, login, nome, senha, admin=False):
    login = (login or "").strip().lower()
    if not login or not (nome or "").strip():
        raise ValueError("Informe login e nome.")
    if len(senha or "") < 8:
        raise ValueError("A senha precisa ter pelo menos 8 caracteres.")
    conn.execute(
        "INSERT INTO usuario (login, nome, senha_hash, admin) VALUES (?, ?, ?, ?)",
        (login, nome.strip(), generate_password_hash(senha), 1 if admin else 0),
    )
    conn.commit()


def token_csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def carregar_usuario():
    g.usuario = None
    uid = session.get("uid")
    if uid:
        g.usuario = db.get_db().execute("SELECT * FROM usuario WHERE id = ? AND ativo = 1", (uid,)).fetchone()


def exigir_login():
    """before_request: tudo exige login, exceto a tela de login e o estático."""
    if portal.ativo():
        return portal.exigir_login()
    if request.endpoint in (None, "static", "auth.login"):
        return None
    carregar_usuario()
    if g.usuario is None:
        return redirect(url_for("auth.login", proximo=request.path))
    return None


def checar_csrf():
    if request.method == "POST":
        esperado = session.get("csrf", "")
        recebido = request.form.get("csrf", "")
        if not esperado or not hmac.compare_digest(esperado, recebido):
            abort(400, "Sessão expirada. Volte e tente de novo.")


def admin_obrigatorio(f):
    @functools.wraps(f)
    def envolto(*a, **k):
        if not g.usuario or not g.usuario["admin"]:
            abort(403)
        return f(*a, **k)

    return envolto


@bp.route("/login", methods=["GET", "POST"])
def login():
    if portal.ativo():
        return redirect(current_app.config["PORTAL_ENTRAR"])
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if _bloqueado(ip):
            flash("Muitas tentativas. Aguarde alguns minutos.", "erro")
            return render_template("login.html"), 429
        u = db.get_db().execute(
            "SELECT * FROM usuario WHERE login = ? AND ativo = 1", ((request.form.get("login") or "").strip().lower(),)
        ).fetchone()
        ok = check_password_hash(u["senha_hash"] if u else _HASH_FALSO, request.form.get("senha", ""))
        if u and ok:
            session.clear()
            session["uid"] = u["id"]
            session.permanent = True
            token_csrf()
            destino = request.args.get("proximo") or ""
            return redirect(destino if destino.startswith("/") and not destino.startswith("//") else url_for("rotas.hoje"))
        _falhas.setdefault(ip, []).append(time.time())
        flash(MENSAGEM_LOGIN, "erro")
    return render_template("login.html")


@bp.route("/sair", methods=["POST"])
def sair():
    session.clear()
    if portal.ativo():
        return redirect(current_app.config["PORTAL_SAIR"])
    return redirect(url_for("auth.login"))
