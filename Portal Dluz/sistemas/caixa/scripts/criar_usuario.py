#!/usr/bin/env python3
"""Cria um usuário. Uso: CAIXA_DATA_DIR=<pasta> python scripts/criar_usuario.py [--admin]

Os demais usuários podem ser criados pela tela Usuários (administrador).
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import auth, config, db  # noqa: E402


def main():
    pasta = config.pasta_dados()
    print(f"Pasta de dados: {pasta}")
    conn = db.conectar(pasta / "caixa.sqlite")
    db.criar_esquema(conn)
    login = input("Login: ")
    nome = input("Nome: ")
    papel = "admin" if "--admin" in sys.argv else (input("Papel (caixa/financeiro/admin): ").strip() or "caixa")
    senha = getpass.getpass(f"Senha (mín. {auth.MIN_SENHA} caracteres): ")
    if senha != getpass.getpass("Repita a senha: "):
        sys.exit("As senhas não conferem.")
    try:
        auth.criar_usuario(conn, login, nome, senha, papel, trocar_senha=False)
    except ValueError as e:
        sys.exit(f"Não foi possível criar: {e}")
    print("Usuário criado.")


if __name__ == "__main__":
    main()
