"""Autenticação, sessão e controle de perfis (spec, seções 3 e 7).

Login é a única proteção do sistema, que fica exposto na internet. Por isso:
a mensagem de erro é sempre a mesma, o usuário é relido do banco a cada
requisição (desativar vale na hora) e a conta trava após falhas seguidas.
"""

import functools
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, portal
from .workflow import PERFIS

bp = Blueprint("auth", __name__)

MENSAGEM_LOGIN = "E-mail ou senha incorretos."

# Quem precisa trocar a senha só pode ver estas telas (e o estático).
ENDPOINTS_LIVRES_NA_TROCA = frozenset({"auth.login", "auth.sair", "auth.trocar_senha"})

# Chaves guardadas na sessão (cookie assinado): quem é, quando entrou e uma
# impressão da senha vigente.
SESSAO_USUARIO = "usuario_id"
SESSAO_LOGIN_EM = "login_em"
SESSAO_IMPRESSAO = "impressao_senha"

# Hash de uma senha qualquer: serve para gastar o mesmo tempo quando o e-mail
# não existe (ou a conta não pode entrar), sem revelar isso pelo tempo de resposta.
_HASH_FALSO = generate_password_hash("senha-falsa-para-igualar-o-tempo")


class ErroUsuario(ValueError):
    """Dados inválidos ao criar um usuário (a mensagem é para o administrador)."""


def _email_plausivel(email):
    if email.count("@") != 1:
        return False
    local, dominio = email.split("@")
    return bool(local) and bool(dominio)


def _agora():
    """Relógio da sessão e do login; os testes o substituem para mover o tempo."""
    return datetime.now(timezone.utc)


def _impressao_da_senha(senha_hash):
    """HMAC do hash da senha com a chave secreta: vai no cookie sem expor o hash.

    Mudou a senha (pelo próprio usuário ou por redefinição do administrador),
    mudou a impressão, e as demais sessões abertas deixam de valer.
    """
    chave = current_app.config["SECRET_KEY"].encode()
    return hmac.new(chave, senha_hash.encode(), hashlib.sha256).hexdigest()


def _sessao_expirada():
    """True se o login tem mais que SESSION_LIFETIME (prazo absoluto, não deslizante)."""
    login_em = session.get(SESSAO_LOGIN_EM)
    if not isinstance(login_em, int) or isinstance(login_em, bool):
        return True  # ausente ou ilegível (ex.: cookie de antes deste formato)
    idade = _agora().timestamp() - login_em
    return idade > current_app.config["SESSION_LIFETIME"]


def criar_usuario(nome, email, senha, perfil, trocar_senha=True):
    """Cria o usuário e devolve o id. Levanta ErroUsuario se algo não servir."""
    nome = (nome or "").strip()
    email = (email or "").strip().lower()
    minimo = current_app.config["MIN_SENHA"]
    if not nome:
        raise ErroUsuario("Informe o nome do usuário.")
    if not _email_plausivel(email):
        raise ErroUsuario("Informe um e-mail válido.")
    if perfil not in PERFIS:
        raise ErroUsuario(f"Perfil inválido: escolha um entre {', '.join(PERFIS)}.")
    if len(senha or "") < minimo:
        raise ErroUsuario(f"A senha deve ter pelo menos {minimo} caracteres.")

    senha_hash = generate_password_hash(senha)
    conexao = db.get_db()
    with db.transacao():
        # A verificação e o INSERT ficam no mesmo BEGIN IMMEDIATE: ninguém
        # cadastra o mesmo e-mail entre os dois.
        if conexao.execute("SELECT 1 FROM usuarios WHERE email = ?", (email,)).fetchone():
            raise ErroUsuario("Já existe um usuário com este e-mail.")
        cursor = conexao.execute(
            "INSERT INTO usuarios (nome, email, senha_hash, perfil, trocar_senha, criado_em)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (nome, email, senha_hash, perfil, int(bool(trocar_senha)), db.agora_utc()),
        )
        return cursor.lastrowid


def _bloqueada(usuario, agora):
    """True se a conta está dentro do prazo de bloqueio."""
    return bool(usuario["bloqueado_ate"]) and datetime.fromisoformat(usuario["bloqueado_ate"]) > agora


def _pode_tentar(usuario, lida, agora):
    """A linha relida ainda é a mesma conta, ativa, desbloqueada e com a senha conferida?"""
    return (
        usuario is not None
        and usuario["ativo"]
        and usuario["senha_hash"] == lida["senha_hash"]
        and not _bloqueada(usuario, agora)
    )


def _limpa(usuario):
    return usuario["falhas_login"] == 0 and usuario["bloqueado_ate"] is None


def tentar_login(email, senha, agora=None):
    """Confere e-mail e senha. Devolve a linha do usuário ou None.

    None cobre e-mail inexistente, senha errada, conta inativa e conta
    bloqueada, sem distinção: quem chama não deve diferenciá-los para o
    visitante. `agora` (datetime com fuso UTC) existe para os testes.

    O hash (scrypt, dezenas de milissegundos) é conferido FORA de transação:
    dentro de BEGIN IMMEDIATE ele seguraria o bloqueio de escrita do banco
    inteiro, e uma enxurrada de logins derrubaria as demais gravações. A
    transação só se abre para gravar o contador, relendo a linha: se ela
    mudou nesse meio-tempo (outra senha, conta desativada ou bloqueada), a
    tentativa vale como recusada e nada é gravado.
    """
    agora = agora or _agora()
    email = (email or "").strip().lower()
    senha = senha or ""
    max_falhas = current_app.config["LOGIN_MAX_FALHAS"]
    conexao = db.get_db()

    lida = conexao.execute("SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone()
    if lida is None or not lida["ativo"] or _bloqueada(lida, agora):
        # Inexistente, inativa ou bloqueada: gasta o mesmo tempo e não grava
        # nada (nem a senha certa vale durante o bloqueio).
        check_password_hash(_HASH_FALSO, senha)
        return None

    senha_certa = check_password_hash(lida["senha_hash"], senha)

    if senha_certa and _limpa(lida):
        # Login limpo: não há contador a zerar, então nada é gravado. Relê só
        # para não aceitar uma conta que mudou durante a conferência do hash.
        atual = conexao.execute("SELECT * FROM usuarios WHERE id = ?", (lida["id"],)).fetchone()
        if not _pode_tentar(atual, lida, agora):
            return None
        if _limpa(atual):
            return atual

    with db.transacao():
        atual = conexao.execute("SELECT * FROM usuarios WHERE id = ?", (lida["id"],)).fetchone()
        if not _pode_tentar(atual, lida, agora):
            return None

        if senha_certa:
            if not _limpa(atual):
                conexao.execute(
                    "UPDATE usuarios SET falhas_login = 0, bloqueado_ate = NULL WHERE id = ?",
                    (atual["id"],),
                )
            return conexao.execute("SELECT * FROM usuarios WHERE id = ?", (atual["id"],)).fetchone()

        # Bloqueio vencido: recomeça a contagem do zero. A contagem parte do
        # valor relido dentro da transação, então tentativas simultâneas não
        # perdem incrementos.
        falhas = (0 if atual["bloqueado_ate"] else atual["falhas_login"]) + 1
        bloqueio = None
        if falhas >= max_falhas:
            bloqueio = (agora + timedelta(minutes=current_app.config["BLOQUEIO_MIN"])).isoformat()
        conexao.execute(
            "UPDATE usuarios SET falhas_login = ?, bloqueado_ate = ? WHERE id = ?",
            (falhas, bloqueio, atual["id"]),
        )
        return None


def usuario_atual():
    """Linha do usuário logado nesta requisição, ou None."""
    return g.get("usuario")


def carregar_usuario():
    """before_request: relê o usuário do banco a cada requisição.

    É isso que faz a desativação (ou a mudança de perfil) valer já na próxima
    requisição, em vez de só quando a sessão expirar.
    """
    if portal.ativo():
        return portal.carregar_usuario()
    g.usuario = None
    if request.endpoint == "static":
        return None
    usuario_id = session.get(SESSAO_USUARIO)
    if usuario_id is None:
        return None
    usuario = None
    if not _sessao_expirada():
        usuario = db.get_db().execute(
            "SELECT * FROM usuarios WHERE id = ? AND ativo = 1", (usuario_id,)
        ).fetchone()
    if usuario is not None:
        impressao = session.get(SESSAO_IMPRESSAO)
        if not isinstance(impressao, str) or not hmac.compare_digest(
            impressao, _impressao_da_senha(usuario["senha_hash"])
        ):
            usuario = None
    if usuario is None:
        session.clear()
        return None
    g.usuario = usuario
    if usuario["trocar_senha"] and request.endpoint not in ENDPOINTS_LIVRES_NA_TROCA:
        return redirect(url_for("auth.trocar_senha"))
    return None


def login_required(vista):
    @functools.wraps(vista)
    def protegida(*args, **kwargs):
        if usuario_atual() is None:
            return redirect(url_for("auth.login"))
        return vista(*args, **kwargs)

    return protegida


def perfil_required(*perfis):
    """Exige login e um dos perfis. Perfil errado: 403; anônimo: vai ao login."""

    def decorador(vista):
        @functools.wraps(vista)
        @login_required
        def protegida(*args, **kwargs):
            if usuario_atual()["perfil"] not in perfis:
                abort(403)
            return vista(*args, **kwargs)

        return protegida

    return decorador


def _abrir_sessao(usuario_id, senha_hash, login_em=None):
    """Grava a sessão. `login_em` só é repassado ao trocar a senha, para que
    isso não estenda o prazo absoluto de SESSION_LIFETIME."""
    if login_em is None:
        login_em = int(_agora().timestamp())
    # clear() antes de gravar: nada da sessão anterior sobrevive ao login.
    session.clear()
    session[SESSAO_USUARIO] = usuario_id
    session[SESSAO_LOGIN_EM] = login_em
    session[SESSAO_IMPRESSAO] = _impressao_da_senha(senha_hash)
    session.permanent = True


@bp.route("/login", methods=["GET", "POST"])
def login():
    if portal.ativo():
        return redirect(current_app.config["PORTAL_ENTRAR"])
    if request.method == "POST":
        email = request.form.get("email", "")
        usuario = tentar_login(email, request.form.get("senha", ""))
        if usuario is None:
            return render_template("login.html", erro=MENSAGEM_LOGIN, email=email)
        _abrir_sessao(usuario["id"], usuario["senha_hash"])
        return redirect("/")
    return render_template("login.html")


@bp.route("/sair", methods=["POST"])
def sair():
    session.clear()
    if portal.ativo():
        return redirect(current_app.config["PORTAL_SAIR"])
    return redirect(url_for("auth.login"))


@bp.route("/trocar-senha", methods=["GET", "POST"])
@login_required
def trocar_senha():
    if portal.ativo():
        return redirect(current_app.config["PORTAL_SENHA"])
    usuario = usuario_atual()
    erro = None
    if request.method == "POST":
        atual = request.form.get("senha_atual", "")
        nova = request.form.get("nova_senha", "")
        confirmacao = request.form.get("confirmacao", "")
        minimo = current_app.config["MIN_SENHA"]
        # Passa por tentar_login: errar a senha atual conta para o bloqueio,
        # então uma sessão roubada não vira um adivinhador de senhas.
        if tentar_login(usuario["email"], atual) is None:
            erro = "A senha atual está incorreta."
        elif len(nova) < minimo:
            erro = f"A nova senha deve ter pelo menos {minimo} caracteres."
        elif nova != confirmacao:
            erro = "A nova senha e a confirmação não conferem."
        elif nova == atual:
            erro = "A nova senha deve ser diferente da atual."
        else:
            novo_hash = generate_password_hash(nova)
            with db.transacao():
                db.get_db().execute(
                    "UPDATE usuarios SET senha_hash = ?, trocar_senha = 0 WHERE id = ?",
                    (novo_hash, usuario["id"]),
                )
            # Esta sessão ganha a impressão nova (as outras ficam inválidas),
            # mas mantém a hora original do login.
            _abrir_sessao(usuario["id"], novo_hash, session.get(SESSAO_LOGIN_EM))
            return redirect("/")
    return render_template("trocar_senha.html", erro=erro)
