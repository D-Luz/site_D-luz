#!/usr/bin/env python3
"""Cria um administrador do portal (o primeiro usuário).

Uso: DLUZ_DATA_DIR=<pasta> python scripts/criar_admin.py
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from portal import auth, config, db  # noqa: E402

if __name__ == "__main__":
    pasta = config.pasta_dados()
    print(f"Pasta de dados: {pasta}")
    caminho = pasta / "portal.sqlite"
    conn = db.conectar(caminho)
    conn.executescript(db.ESQUEMA)
    nome = input("Nome: ").strip()
    login = input("Login (sem espaços): ").strip()
    email = input("E-mail (opcional): ").strip()
    senha = getpass.getpass("Senha (mínimo 8): ")
    if senha != getpass.getpass("Repita a senha: "):
        sys.exit("As senhas não conferem. Nada foi criado.")
    try:
        auth.criar_usuario(conn, login, nome, senha, admin=True, email=email, trocar_senha=False)
    except ValueError as erro:
        sys.exit(f"Erro: {erro}")
    print(f"Administrador '{login.lower()}' criado.")
