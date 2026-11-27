"""Login único do portal, sessão, CSRF e cadastro de usuários.

O mesmo login vale para as ferramentas e para Pedidos, CRM e Caixa: o portal
diz a esses sistemas quem está logado (veja integracao.py).
"""

import functools
import hashlib
import hmac
import secrets
import shutil
import sqlite3
import time

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import config, db

bp = Blueprint("auth", __name__)

MENSAGEM_LOGIN = "Usuário ou senha incorretos."
_HASH_FALSO = generate_password_hash("senha-falsa-para-igualar-o-tempo")

# Tentativas falhas por endereço: 6 erros em 10 minutos bloqueiam por 10 minutos.
_falhas = {}
LIMITE, JANELA = 6, 600

# Sem 0/O, 1/l/I: a senha provisória é lida na tela e repassada de boca.
_ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"

ENDPOINTS_LIVRES = ("static", "auth.entrar", "auth.sair")


def _bloqueado(ip):
    agora = time.time()
    recentes = [t for t in _falhas.get(ip, []) if agora - t < JANELA]
    _falhas[ip] = recentes
    return len(recentes) >= LIMITE


def _impressao(senha_hash):
    """Muda quando a senha muda: trocar a senha derruba as outras sessões."""
    return hashlib.sha256(senha_hash.encode()).hexdigest()[:16]


def senha_provisoria():
    return "".join(secrets.choice(_ALFABETO) for _ in range(10))


def criar_usuario(conn, login, nome, senha, admin=False, email=None, trocar_senha=True):
    login = (login or "").strip().lower()
    nome = (nome or "").strip()
    email = (email or "").strip().lower() or None
    if not login or not nome:
        raise ValueError("Informe login e nome.")
    if any(c.isspace() for c in login):
        raise ValueError("O login não pode ter espaços.")
    if len(senha or "") < config.MIN_SENHA:
        raise ValueError(f"A senha precisa ter pelo menos {config.MIN_SENHA} caracteres.")
    try:
        conn.execute(
            "INSERT INTO usuario (login, nome, email, senha_hash, admin, trocar_senha, criado_em) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (login, nome, email, generate_password_hash(senha), int(bool(admin)), int(trocar_senha), db.agora()),
        )
    except sqlite3.IntegrityError:
        raise ValueError("Já existe um usuário com esse login.") from None
    conn.commit()


def gravar_login_externo(db_path, login, nome, email, senha):
    """Chamado por Pedidos e Caixa quando o administrador cria um usuário ou gera senha nova lá dentro.

    Cria o login do portal com essa senha provisória (ou troca a senha, se o
    login já existir), para a pessoa conseguir entrar pelo portal.
    """
    login = (login or "").strip().lower()
    conn = db.conectar(db_path)
    try:
        existe = conn.execute("SELECT id FROM usuario WHERE login = ?", (login,)).fetchone()
        if existe:
            conn.execute(
                "UPDATE usuario SET senha_hash = ?, trocar_senha = 1, ativo = 1 WHERE id = ?",
                (generate_password_hash(senha), existe["id"]),
            )
            conn.commit()
        else:
            criar_usuario(conn, login, nome, senha, email=email, trocar_senha=True)
    finally:
        conn.close()


def token_csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def usuario_da_sessao(conn):
    """Linha do usuário logado (ou None). Usada também fora das rotas do portal."""
    uid = session.get("uid")
    if not uid:
        return None
    u = conn.execute("SELECT * FROM usuario WHERE id = ? AND ativo = 1", (uid,)).fetchone()
    if u is None or not hmac.compare_digest(session.get("imp", ""), _impressao(u["senha_hash"])):
        return None
    return u


def carregar_usuario():
    """before_request: tudo exige login, exceto a tela de entrada e o estático."""
    g.usuario = usuario_da_sessao(db.get_db())
    if g.usuario is None and session.get("uid"):
        session.clear()
    if request.endpoint in ENDPOINTS_LIVRES or request.endpoint is None:
        return None
    if g.usuario is None:
        return redirect(url_for("auth.entrar", proximo=request.full_path.rstrip("?")))
    if g.usuario["trocar_senha"] and request.endpoint != "auth.trocar_senha":
        return redirect(url_for("auth.trocar_senha"))
    return None


def checar_csrf():
    if request.method == "POST" and request.endpoint not in ("static", None):
        esperado = session.get("csrf", "")
        recebido = request.form.get("csrf", "") or request.headers.get("X-CSRF", "")
        if not esperado or not hmac.compare_digest(esperado, recebido):
            abort(400, "Sessão expirada. Volte, recarregue a página e tente de novo.")


def admin_obrigatorio(f):
    @functools.wraps(f)
    def envolto(*a, **k):
        if not g.usuario or not g.usuario["admin"]:
            abort(403)
        return f(*a, **k)

    return envolto


def _destino_seguro(destino, padrao):
    if destino and destino.startswith("/") and not destino.startswith("//") and "\\" not in destino:
        return destino
    return padrao


def _abrir_sessao(u):
    session.clear()
    session["uid"] = u["id"]
    session["imp"] = _impressao(u["senha_hash"])
    session.permanent = True
    token_csrf()


@bp.route("/entrar", methods=["GET", "POST"])
def entrar():
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if _bloqueado(ip):
            flash("Muitas tentativas. Aguarde alguns minutos.", "erro")
            return render_template("entrar.html"), 429
        conn = db.get_db()
        login = (request.form.get("login") or "").strip().lower()
        u = conn.execute("SELECT * FROM usuario WHERE login = ? AND ativo = 1", (login,)).fetchone()
        ok = check_password_hash(u["senha_hash"] if u else _HASH_FALSO, request.form.get("senha", ""))
        if u and ok:
            _abrir_sessao(u)
            conn.execute("UPDATE usuario SET ultimo_acesso = ? WHERE id = ?", (db.agora(), u["id"]))
            conn.commit()
            return redirect(_destino_seguro(request.args.get("proximo"), url_for("rotas.inicio")))
        _falhas.setdefault(ip, []).append(time.time())
        flash(MENSAGEM_LOGIN, "erro")
    return render_template("entrar.html")


@bp.route("/sair", methods=["GET", "POST"])
def sair():
    # GET também sai: o link "Sair" aparece na barra dentro de Pedidos, CRM e
    # Caixa, onde o portal não tem formulário. Sair por engano não causa dano.
    session.clear()
    return redirect(url_for("auth.entrar"))


@bp.route("/senha", methods=["GET", "POST"])
def trocar_senha():
    if request.method == "POST":
        atual = request.form.get("atual", "")
        nova = request.form.get("nova", "")
        if not check_password_hash(g.usuario["senha_hash"], atual):
            flash("A senha atual está incorreta.", "erro")
        elif len(nova) < config.MIN_SENHA:
            flash(f"A nova senha precisa ter pelo menos {config.MIN_SENHA} caracteres.", "erro")
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
            _abrir_sessao(conn.execute("SELECT * FROM usuario WHERE id = ?", (g.usuario["id"],)).fetchone())
            flash("Senha alterada.", "ok")
            return redirect(url_for("rotas.inicio"))
    return render_template("senha.html")


@bp.route("/usuarios", methods=["GET", "POST"])
@admin_obrigatorio
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
                    admin=bool(request.form.get("admin")),
                    email=request.form.get("email"),
                )
                provisoria = {"login": request.form.get("login", "").strip().lower(), "senha": senha}
            elif acao in ("ativo", "admin") and alvo:
                if alvo == g.usuario["id"]:
                    raise ValueError("Você não pode mudar isso na própria conta.")
                conn.execute(f"UPDATE usuario SET {acao} = 1 - {acao} WHERE id = ?", (alvo,))
                conn.commit()
            elif acao == "senha" and alvo:
                senha = senha_provisoria()
                conn.execute(
                    "UPDATE usuario SET senha_hash = ?, trocar_senha = 1 WHERE id = ?",
                    (generate_password_hash(senha), alvo),
                )
                conn.commit()
                linha = conn.execute("SELECT login FROM usuario WHERE id = ?", (alvo,)).fetchone()
                provisoria = {"login": linha["login"] if linha else "?", "senha": senha}
            elif acao == "email" and alvo:
                conn.execute(
                    "UPDATE usuario SET email = ? WHERE id = ?",
                    ((request.form.get("email") or "").strip().lower() or None, alvo),
                )
                conn.commit()
        except ValueError as erro:
            flash(str(erro), "erro")
    lista = conn.execute("SELECT * FROM usuario ORDER BY ativo DESC, nome COLLATE NOCASE").fetchall()
    return render_template("usuarios.html", lista=lista, provisoria=provisoria)


@bp.route("/usuarios/<int:uid>/excluir", methods=["GET", "POST"])
@admin_obrigatorio
def excluir_usuario(uid):
    conn = db.get_db()
    alvo = conn.execute("SELECT * FROM usuario WHERE id = ?", (uid,)).fetchone()
    if alvo is None:
        abort(404)
    if uid == g.usuario["id"]:
        flash("Você não pode excluir a própria conta.", "erro")
        return redirect(url_for("auth.usuarios"))
    qtd = conn.execute("SELECT COUNT(*) FROM tarefa WHERE usuario_id = ?", (uid,)).fetchone()[0]
    if request.method == "GET":
        return render_template("excluir_usuario.html", alvo=alvo, qtd_relatorios=qtd)
    from . import integracao, tarefas

    pasta = tarefas.pasta_tarefas(current_app.config["DATA_DIR"])
    for (tid,) in conn.execute("SELECT id FROM tarefa WHERE usuario_id = ?", (uid,)).fetchall():
        shutil.rmtree(pasta / tid, ignore_errors=True)
    conn.execute("DELETE FROM tarefa WHERE usuario_id = ?", (uid,))
    conn.execute("DELETE FROM usuario WHERE id = ?", (uid,))
    conn.commit()
    avisos = integracao.remover_dos_sistemas(current_app.config["DATA_DIR"], alvo["login"], alvo["email"])
    flash(f"Usuário {alvo['login']} excluído." + (" " + " ".join(avisos) if avisos else ""), "ok")
    return redirect(url_for("auth.usuarios"))
