"""Administração de usuários (spec, seções 3 e 9, item 6).

Só o perfil admin entra aqui. O administrador nunca digita a senha de outra
pessoa: criar e redefinir geram uma senha provisória no servidor, mostrada uma
única vez na própria resposta (nunca por flash, que iria para o cookie de
sessão) e que obriga a troca no primeiro acesso.
"""

import secrets
import string

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from . import db, portal
from .auth import ErroUsuario, criar_usuario, perfil_required, usuario_atual
from .workflow import PERFIS

bp = Blueprint("admin", __name__, url_prefix="/admin")

# Sem 0/O, 1/l/I: a senha é lida na tela e repassada por outro canal.
_MAIUSCULAS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_MINUSCULAS = "abcdefghijkmnopqrstuvwxyz"
_DIGITOS = "23456789"
_ALFABETO = _MAIUSCULAS + _MINUSCULAS + _DIGITOS
TAMANHO_SENHA_PROVISORIA = 14


@bp.after_request
def sem_cache(resposta):
    """Nada da administração (lista de usuários, senha provisória) fica em cache."""
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


def _senha_provisoria():
    """Senha aleatória (secrets) com maiúscula, minúscula e dígito, no mínimo MIN_SENHA."""
    tamanho = max(TAMANHO_SENHA_PROVISORIA, current_app.config["MIN_SENHA"])
    while True:
        senha = "".join(secrets.choice(_ALFABETO) for _ in range(tamanho))
        if (
            any(c in _MAIUSCULAS for c in senha)
            and any(c in _MINUSCULAS for c in senha)
            and any(c in _DIGITOS for c in senha)
        ):
            return senha


def _alterar_ativo(usuario_id, ativo, quem_pede):
    """Ativa ou desativa. Devolve a mensagem de recusa, ou None se deu certo.

    As checagens ficam dentro da transação: a contagem de administradores ativos
    é relida no momento da gravação, então dois administradores desativando um
    ao outro ao mesmo tempo não deixam o sistema sem nenhum.
    """
    if not ativo and usuario_id == quem_pede:
        return "Você não pode desativar a própria conta."
    conexao = db.get_db()
    with db.transacao():
        alvo = conexao.execute(
            "SELECT perfil, ativo FROM usuarios WHERE id = ?", (usuario_id,)
        ).fetchone()
        if alvo is None:
            return "Usuário não encontrado."
        if not ativo and alvo["perfil"] == "admin" and alvo["ativo"]:
            outros = conexao.execute(
                "SELECT COUNT(*) FROM usuarios WHERE perfil = 'admin' AND ativo = 1 AND id != ?",
                (usuario_id,),
            ).fetchone()[0]
            if outros == 0:
                return "Não é possível desativar o último administrador ativo."
        conexao.execute("UPDATE usuarios SET ativo = ? WHERE id = ?", (int(ativo), usuario_id))
    return None


def _redefinir_senha(usuario_id):
    """Grava uma senha provisória nova, destrava a conta e devolve a senha."""
    senha = _senha_provisoria()
    senha_hash = generate_password_hash(senha)
    with db.transacao() as conexao:
        # Mudar senha_hash derruba as sessões abertas desse usuário (auth.py).
        conexao.execute(
            "UPDATE usuarios SET senha_hash = ?, trocar_senha = 1, falhas_login = 0,"
            " bloqueado_ate = NULL WHERE id = ?",
            (senha_hash, usuario_id),
        )
    return senha


def _usuario_ou_404(usuario_id):
    usuario = db.get_db().execute(
        "SELECT id, nome, email FROM usuarios WHERE id = ?", (usuario_id,)
    ).fetchone()
    if usuario is None:
        abort(404)
    return usuario


def _pagina(status=200, erro=None, provisoria=None, formulario=None):
    usuarios = db.get_db().execute(
        "SELECT id, nome, email, perfil, ativo, trocar_senha FROM usuarios"
        " ORDER BY ativo DESC, nome COLLATE NOCASE, id"
    ).fetchall()
    html = render_template(
        "admin_usuarios.html",
        usuarios=usuarios,
        perfis=PERFIS,
        erro=erro,
        provisoria=provisoria,
        formulario=formulario or {},
        eu=usuario_atual()["id"],
    )
    return html, status


@bp.get("/usuarios")
@perfil_required("admin")
def usuarios():
    return _pagina()


@bp.post("/usuarios")
@perfil_required("admin")
def criar():
    nome = request.form.get("nome", "")
    email = request.form.get("email", "")
    perfil = request.form.get("perfil", "")
    senha = _senha_provisoria()
    try:
        criar_usuario(nome, email, senha, perfil, trocar_senha=True)
    except ErroUsuario as erro:
        return _pagina(
            400, erro=str(erro), formulario={"nome": nome, "email": email, "perfil": perfil}
        )
    no_portal = portal.gravar_login(nome.strip(), email.strip().lower(), senha)
    return _pagina(
        provisoria={"nome": nome.strip(), "email": email.strip().lower(), "senha": senha, "novo": True,
                    "portal": no_portal}
    )


@bp.post("/usuarios/<int:usuario_id>/ativo")
@perfil_required("admin")
def ativo(usuario_id):
    _usuario_ou_404(usuario_id)
    valor = request.form.get("ativo")
    if valor not in ("0", "1"):
        return _pagina(400, erro="Valor inválido para ativar ou desativar.")
    erro = _alterar_ativo(usuario_id, valor == "1", usuario_atual()["id"])
    if erro:
        return _pagina(400, erro=erro)
    flash("Usuário ativado." if valor == "1" else "Usuário desativado.", "sucesso")
    return redirect(url_for("admin.usuarios"))


@bp.post("/usuarios/<int:usuario_id>/senha")
@perfil_required("admin")
def senha(usuario_id):
    alvo = _usuario_ou_404(usuario_id)
    if usuario_id == usuario_atual()["id"]:
        # Redefinir a própria senha derrubaria a sessão logo em seguida.
        return _pagina(400, erro="Para mudar a sua própria senha use a tela Trocar senha.")
    nova = _redefinir_senha(usuario_id)
    no_portal = portal.gravar_login(alvo["nome"], alvo["email"], nova)
    return _pagina(
        provisoria={"nome": alvo["nome"], "email": alvo["email"], "senha": nova, "novo": False, "portal": no_portal}
    )
