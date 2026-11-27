"""Integração com o Portal Dluz (login único).

Quando o sistema roda dentro do portal (config PORTAL=True), quem está logado
vem do portal, numa chave do environ WSGI que só o próprio portal grava (um
cliente não consegue criar chaves do environ; cabeçalhos HTTP viram HTTP_*).
O usuário local é achado pelo e-mail e, na primeira visita, criado com o
perfil "admin" (administrador do portal) ou "solicitante". Depois o perfil
pode ser trocado normalmente em Administração > Usuários.
"""

import secrets

from flask import abort, current_app, g, redirect, request
from werkzeug.security import generate_password_hash

from . import db

CHAVE_ENVIRON = "dluz.portal.usuario"


def ativo():
    return bool(current_app.config.get("PORTAL"))


def email_do(info):
    return (info.get("email") or f"{info['login']}@portal.local").strip().lower()


def gravar_login(nome, email, senha):
    """Cria (ou atualiza a senha d)o login do portal para quem foi cadastrado aqui.

    O login no portal é o próprio e-mail. Só vale quando quem cadastra é
    administrador do portal: um admin só do Pedidos não troca senha de ninguém
    no portal. Devolve True se gravou.
    """
    funcao = current_app.config.get("PORTAL_GRAVAR_LOGIN")
    info = request.environ.get(CHAVE_ENVIRON) or {}
    if not (ativo() and funcao and info.get("admin")):
        return False
    funcao(login=email, nome=nome, email=email, senha=senha)
    return True


def carregar_usuario():
    """Substitui auth.carregar_usuario dentro do portal."""
    g.usuario = None
    if request.endpoint == "static":
        return None
    info = request.environ.get(CHAVE_ENVIRON)
    if not info:
        return redirect(current_app.config.get("PORTAL_ENTRAR", "/entrar"))
    conexao = db.get_db()
    email = email_do(info)
    usuario = conexao.execute("SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone()
    if usuario is None:
        with db.transacao():
            conexao.execute(
                "INSERT OR IGNORE INTO usuarios (nome, email, senha_hash, perfil, trocar_senha, criado_em)"
                " VALUES (?, ?, ?, ?, 0, ?)",
                (info["nome"], email, generate_password_hash(secrets.token_urlsafe(32)),
                 "admin" if info.get("admin") else "solicitante", db.agora_utc()),
            )
        usuario = conexao.execute("SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone()
    if not usuario["ativo"]:
        abort(403)
    g.usuario = usuario
    return None
