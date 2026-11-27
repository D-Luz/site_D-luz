"""Integração com o Portal Dluz (login único).

Dentro do portal (config PORTAL=True), quem está logado vem do portal, numa
chave do environ WSGI que só o portal grava. O usuário local é achado pelo
login e, na primeira visita, criado como administrador (se for administrador
do portal) ou balcão.
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
            "INSERT OR IGNORE INTO usuario (login, nome, senha_hash, admin) VALUES (?, ?, ?, ?)",
            (login, info["nome"], generate_password_hash(secrets.token_urlsafe(32)), 1 if info.get("admin") else 0),
        )
        conn.commit()
        u = conn.execute("SELECT * FROM usuario WHERE login = ?", (login,)).fetchone()
    if not u["ativo"]:
        abort(403)
    g.usuario = u
    return None
