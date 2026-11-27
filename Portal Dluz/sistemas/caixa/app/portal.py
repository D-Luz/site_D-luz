"""Integração com o Portal Dluz (login único).

Dentro do portal (config PORTAL=True), quem está logado vem do portal, numa
chave do environ WSGI que só o portal grava. O usuário local é achado pelo
login e, na primeira visita, criado com o papel "admin" (administrador do
portal) ou "caixa". O papel e a empresa podem ser trocados em Usuários.
"""

import secrets

from flask import abort, current_app, g, redirect, request
from werkzeug.security import generate_password_hash

from . import db

CHAVE_ENVIRON = "dluz.portal.usuario"


def ativo():
    return bool(current_app.config.get("PORTAL"))


def exigir_login():
    """Substitui auth.exigir_login dentro do portal."""
    g.usuario = None
    if request.endpoint in (None, "static"):
        return None
    info = request.environ.get(CHAVE_ENVIRON)
    if not info:
        return redirect(current_app.config.get("PORTAL_ENTRAR", "/entrar"))
    conn = db.get_db()
    login = info["login"].strip().lower()
    u = conn.execute("SELECT * FROM usuario WHERE login = ?", (login,)).fetchone()
    if u is None:
        conn.execute(
            "INSERT OR IGNORE INTO usuario (login, nome, senha_hash, papel, trocar_senha, criado_em) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (login, info["nome"], generate_password_hash(secrets.token_urlsafe(32)),
             "admin" if info.get("admin") else "caixa", db.agora()),
        )
        conn.commit()
        u = conn.execute("SELECT * FROM usuario WHERE login = ?", (login,)).fetchone()
    if not u["ativo"]:
        abort(403)
    g.usuario = u
    return None


def gravar_login(login, nome, senha):
    """Cria (ou atualiza a senha d)o login do portal para quem foi cadastrado aqui.

    Só vale quando quem cadastra é administrador do portal. Devolve True se gravou.
    """
    funcao = current_app.config.get("PORTAL_GRAVAR_LOGIN")
    info = request.environ.get(CHAVE_ENVIRON) or {}
    if not (ativo() and funcao and info.get("admin")):
        return False
    funcao(login=login, nome=nome, email=None, senha=senha)
    return True
