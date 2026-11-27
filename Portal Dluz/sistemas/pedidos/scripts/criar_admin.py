"""Administradores do sistema.

Criar um administrador (o primeiro, ou um reserva):

    python scripts/criar_admin.py

Redefinir a senha de um administrador que a esqueceu (a conta é destravada e
as sessões abertas dele caem):

    python scripts/criar_admin.py --redefinir

Em ambos os casos o script pede os dados no terminal (a senha não aparece na
tela) e usa o mesmo banco do sistema, o da pasta PEDIDOS_DATA_DIR. Essa
variável é OBRIGATÓRIA: sem ela o script se recusa a rodar (código 1), em vez
de criar um banco novo na pasta data/ do programa. A pasta usada aparece na
tela antes de qualquer pergunta. Só redefine senha de administrador; as dos
demais usuários são redefinidas por um administrador, na tela Usuários.
"""

import argparse
import getpass
import sys
from pathlib import Path

# Permite rodar o script direto da raiz do projeto, sem instalar nada.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app, db  # noqa: E402
from app.auth import ErroUsuario, criar_usuario  # noqa: E402
from app.config import MSG_SEM_DATA_DIR, carregar, data_dir_definido  # noqa: E402


def criar_admin(nome: str, email: str, senha: str) -> int:
    """Cria o administrador (sem troca de senha forçada) e devolve o id.

    Levanta ErroUsuario se o e-mail já existe ou se os dados não servem.
    """
    app = create_app()
    with app.app_context():
        return criar_usuario(nome, email, senha, "admin", trocar_senha=False)


def redefinir_senha_admin(email: str, senha: str) -> None:
    """Troca a senha de um administrador existente e ativo, numa só transação.

    A conta é destravada (falhas_login = 0, bloqueado_ate = NULL) e fica sem
    troca de senha pendente. Como o hash muda, as sessões abertas desse
    usuário deixam de valer. Levanta ErroUsuario se o e-mail não existe, se
    não é de um administrador ativo ou se a senha é curta demais.
    """
    app = create_app()
    with app.app_context():
        email = (email or "").strip().lower()
        minimo = app.config["MIN_SENHA"]
        if len(senha or "") < minimo:
            raise ErroUsuario(f"A senha deve ter pelo menos {minimo} caracteres.")
        novo_hash = generate_password_hash(senha)
        with db.transacao() as conexao:
            usuario = conexao.execute(
                "SELECT id, perfil, ativo FROM usuarios WHERE email = ?", (email,)
            ).fetchone()
            if usuario is None:
                raise ErroUsuario(f"Não encontrei nenhum usuário com o e-mail {email}.")
            if usuario["perfil"] != "admin":
                raise ErroUsuario(
                    f"{email} não é administrador. A senha dos outros usuários é "
                    "redefinida por um administrador, na tela Usuários."
                )
            if not usuario["ativo"]:
                raise ErroUsuario(
                    f"{email} está desativado. Peça a outro administrador para "
                    "reativá-lo na tela Usuários."
                )
            conexao.execute(
                "UPDATE usuarios SET senha_hash = ?, trocar_senha = 0, falhas_login = 0,"
                " bloqueado_ate = NULL WHERE id = ?",
                (novo_hash, usuario["id"]),
            )


def _pedir_senha_nova() -> str:
    senha = getpass.getpass("Senha: ")
    confirmacao = getpass.getpass("Repita a senha: ")
    if senha != confirmacao:
        print("Erro: as senhas não conferem.", file=sys.stderr)
        sys.exit(1)
    return senha


def _redefinir() -> None:
    email = input("E-mail do administrador: ").strip()
    senha = _pedir_senha_nova()
    try:
        redefinir_senha_admin(email, senha)
    except ErroUsuario as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        sys.exit(1)
    print(f"Senha de {email.lower()} redefinida. Já é possível entrar com a nova senha.")


def _criar() -> None:
    nome = input("Nome: ").strip()
    email = input("E-mail: ").strip()
    senha = _pedir_senha_nova()
    try:
        criar_admin(nome, email, senha)
    except ErroUsuario as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        sys.exit(1)
    print(f"Administrador {email.lower()} criado. Já é possível entrar no sistema.")


def main(argv=None):
    """``argv`` são os argumentos da linha de comando (sem o nome do script).

    ``None`` equivale a nenhum argumento (modo criar); quem roda o script
    passa ``sys.argv[1:]``.
    """
    analisador = argparse.ArgumentParser(
        description="Cria um administrador ou redefine a senha de um existente."
    )
    analisador.add_argument(
        "--redefinir",
        action="store_true",
        help="redefine a senha de um administrador existente, em vez de criar um novo",
    )
    argumentos = analisador.parse_args([] if argv is None else argv)
    if not data_dir_definido():
        print(MSG_SEM_DATA_DIR, file=sys.stderr)
        sys.exit(1)
    try:
        pasta = carregar()["DATA_DIR"]
    except ValueError as erro:
        print(f"Erro de configuração: {erro}", file=sys.stderr)
        sys.exit(1)
    print(f"Pasta de dados: {pasta}")
    if argumentos.redefinir:
        _redefinir()
    else:
        _criar()


if __name__ == "__main__":
    main(sys.argv[1:])
