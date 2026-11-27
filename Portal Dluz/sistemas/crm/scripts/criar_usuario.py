#!/usr/bin/env python3
"""Cria um usuário do CRM. Uso: CRM_DATA_DIR=<pasta> python3 scripts/criar_usuario.py [--admin]"""

import getpass
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import auth, config, db  # noqa: E402


def main():
    pasta = config.pasta_dados()
    print(f"Pasta de dados: {pasta}")
    conn = db.conectar(pasta / "crm.sqlite")
    db.criar_esquema(conn)
    login = input("Login: ")
    nome = input("Nome: ")
    senha = getpass.getpass("Senha (mín. 8 caracteres): ")
    if senha != getpass.getpass("Repita a senha: "):
        sys.exit("As senhas não conferem.")
    try:
        auth.criar_usuario(conn, login, nome, senha, admin="--admin" in sys.argv)
    except (ValueError, sqlite3.IntegrityError) as e:
        sys.exit(f"Não foi possível criar: {e}")
    print("Usuário criado.")


if __name__ == "__main__":
    main()
