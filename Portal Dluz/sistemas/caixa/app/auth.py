"""Login, sessão, CSRF, papéis e cadastro de usuários.

Papéis: caixa (lança e fecha o dia), financeiro (aprova, confere e reabre) e
admin (tudo, mais usuários). Um usuário pode ficar preso a uma empresa.
"""

import functools
import hmac
import secrets
import sqlite3
import time

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import config, db, portal

bp = Blueprint("auth", __name__)

MENSAGEM_LOGIN = "Usuário ou senha incorretos."
MIN_SENHA = 8
_HASH_FALSO = generate_password_hash("senha-falsa-para-igualar-o-tempo")

# Tentativas falhas por endereço: 6 erros em 10 minutos bloqueiam por 10 minutos.
_falhas = {}
LIMITE, JANELA = 6, 600

# Sem 0/O, 1/l/I: a senha provisória é lida na tela e repassada de boca.
_ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def _bloqueado(ip):
    agora = time.time()
    recentes = [t for t in _falhas.get(ip, []) if agora - t < JANELA]
    _falhas[ip] = recentes
    return len(recentes) >= LIMITE


def senha_provisoria():
    return "".join(secrets.choice(_ALFABETO) for _ in range(10))


def criar_usuario(conn, login, nome, senha, papel, empresa=None, trocar_senha=True):
    login = (login or "").strip().lower()
    nome = (nome or "").strip()
    if not login or not nome:
        raise ValueError("Informe login e nome.")
    if papel not in config.PAPEIS:
        raise ValueError("Papel inválido.")
    if empresa and empresa not in config.EMPRESAS:
        raise ValueError("Empresa inválida.")
    if len(senha or "") < MIN_SENHA:
        raise ValueError(f"A senha precisa ter pelo menos {MIN_SENHA} caracteres.")
    try:
        conn.execute(
            "INSERT INTO usuario (login, nome, senha_hash, papel, empresa, trocar_senha, criado_em) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (login, nome, generate_password_hash(senha), papel, empresa or None, int(trocar_senha), db.agora()),
        )
    except sqlite3.IntegrityError:
        raise ValueError("Já existe um usuário com esse login.") from None
    conn.commit()


def token_csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def exigir_login():
    """before_request: tudo exige login, exceto a tela de login e o estático."""
    if portal.ativo():
        return portal.exigir_login()
    g.usuario = None
    uid = session.get("uid")
    if uid:
        g.usuario = db.get_db().execute("SELECT * FROM usuario WHERE id = ? AND ativo = 1", (uid,)).fetchone()
        if g.usuario is None:
            session.clear()
    if request.endpoint in (None, "static", "auth.login"):
        return None
    if g.usuario is None:
        return redirect(url_for("auth.login", proximo=request.full_path.rstrip("?")))
    if g.usuario["trocar_senha"] and request.endpoint not in ("auth.trocar_senha", "auth.sair"):
        return redirect(url_for("auth.trocar_senha"))
    return None


def checar_csrf():
    if request.method == "POST":
        esperado = session.get("csrf", "")
        recebido = request.form.get("csrf", "")
        if not esperado or not hmac.compare_digest(esperado, recebido):
            abort(400, "Sessão expirada. Volte, recarregue a página e tente de novo.")


def pode(acao, usuario=None):
    """Permissões por papel: 'lancar', 'fechar', 'aprovar', 'conferir', 'reabrir', 'usuarios'."""
    usuario = usuario or g.get("usuario")
    if usuario is None:
        return False
    papel = usuario["papel"]
    if papel == "admin":
        return True
    if acao in ("lancar", "fechar"):
        return papel in ("caixa", "financeiro")
    if acao in ("aprovar", "conferir", "reabrir"):
        return papel == "financeiro"
    return False


def empresas_permitidas(usuario=None):
    usuario = usuario or g.get("usuario")
    if usuario is not None and usuario["empresa"]:
        return [usuario["empresa"]]
    return list(config.EMPRESAS)


def exige(acao):
    def decorador(f):
        @functools.wraps(f)
        def envolto(*a, **k):
            if not pode(acao):
                abort(403)
            return f(*a, **k)

        return envolto

    return decorador


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
            if not destino.startswith("/") or destino.startswith("//"):
                destino = url_for("rotas.inicio")
            return redirect(destino)
        _falhas.setdefault(ip, []).append(time.time())
        flash(MENSAGEM_LOGIN, "erro")
    return render_template("login.html")


@bp.route("/sair", methods=["POST"])
def sair():
    session.clear()
    if portal.ativo():
        return redirect(current_app.config["PORTAL_SAIR"])
    return redirect(url_for("auth.login"))


@bp.route("/senha", methods=["GET", "POST"])
def trocar_senha():
    if portal.ativo():
        return redirect(current_app.config["PORTAL_SENHA"])
    if request.method == "POST":
        atual = request.form.get("atual", "")
        nova = request.form.get("nova", "")
        if not check_password_hash(g.usuario["senha_hash"], atual):
            flash("A senha atual está incorreta.", "erro")
        elif len(nova) < MIN_SENHA:
            flash(f"A nova senha precisa ter pelo menos {MIN_SENHA} caracteres.", "erro")
        elif nova != request.form.get("confirmacao", ""):
            flash("A nova senha e a confirmação não conferem.", "erro")
        elif nova == atual:
            flash("A nova senha precisa ser diferente da atual.", "erro")
        else:
            conn = db.get_db()
            conn.execute(
                "UPDATE usuario SET senha_hash = ?, trocar_senha = 0 WHERE id = ?",
                (generate_password_hash(nova), g.usuario["id"]),
            )
            conn.commit()
            flash("Senha alterada.", "ok")
            return redirect(url_for("rotas.inicio"))
    return render_template("senha.html")


@bp.route("/usuarios", methods=["GET", "POST"])
@exige("usuarios")
def usuarios():
    conn = db.get_db()
    provisoria = None
    if request.method == "POST":
        acao = request.form.get("acao")
        alvo = request.form.get("id", type=int)
        try:
            if acao == "criar":
                senha = senha_provisoria()
                criar_usuario(
                    conn,
                    request.form.get("login"),
                    request.form.get("nome"),
                    senha,
                    request.form.get("papel"),
                    request.form.get("empresa") or None,
                )
                provisoria = {"login": request.form.get("login", "").strip().lower(), "senha": senha}
                provisoria["portal"] = portal.gravar_login(
                    provisoria["login"], (request.form.get("nome") or "").strip(), senha)
            elif acao == "ativo" and alvo:
                if alvo == g.usuario["id"]:
                    raise ValueError("Você não pode desativar a própria conta.")
                conn.execute("UPDATE usuario SET ativo = 1 - ativo WHERE id = ?", (alvo,))
                conn.commit()
            elif acao == "senha" and alvo:
                senha = senha_provisoria()
                conn.execute(
                    "UPDATE usuario SET senha_hash = ?, trocar_senha = 1 WHERE id = ?",
                    (generate_password_hash(senha), alvo),
                )
                conn.commit()
                login_alvo = conn.execute("SELECT login, nome FROM usuario WHERE id = ?", (alvo,)).fetchone()
                provisoria = {"login": login_alvo["login"] if login_alvo else "?", "senha": senha}
                if login_alvo:
                    provisoria["portal"] = portal.gravar_login(login_alvo["login"], login_alvo["nome"], senha)
        except ValueError as erro:
            flash(str(erro), "erro")
    lista = conn.execute("SELECT * FROM usuario ORDER BY ativo DESC, nome COLLATE NOCASE").fetchall()
    return render_template("usuarios.html", lista=lista, provisoria=provisoria)
