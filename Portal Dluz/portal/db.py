"""Banco SQLite do portal: usuários, tarefas (execuções das ferramentas) e ajustes."""

import json
import sqlite3
from datetime import datetime

from flask import current_app, g

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuario (
    id            INTEGER PRIMARY KEY,
    login         TEXT NOT NULL UNIQUE,
    nome          TEXT NOT NULL,
    email         TEXT,
    senha_hash    TEXT NOT NULL,
    admin         INTEGER NOT NULL DEFAULT 0,
    ativo         INTEGER NOT NULL DEFAULT 1,
    trocar_senha  INTEGER NOT NULL DEFAULT 1,
    criado_em     TEXT NOT NULL,
    ultimo_acesso TEXT
);

CREATE TABLE IF NOT EXISTS tarefa (
    id            TEXT PRIMARY KEY,
    ferramenta    TEXT NOT NULL,
    usuario_id    INTEGER NOT NULL REFERENCES usuario(id),
    parametros    TEXT NOT NULL,
    situacao      TEXT NOT NULL,            -- fila | rodando | ok | erro
    criada_em     TEXT NOT NULL,
    iniciada_em   TEXT,
    terminada_em  TEXT,
    log           TEXT NOT NULL DEFAULT '',
    erro          TEXT,
    resumo        TEXT,
    pagina        TEXT                      -- arquivo .html para mostrar na tela
);
CREATE INDEX IF NOT EXISTS tarefa_usuario ON tarefa(usuario_id, criada_em);

CREATE TABLE IF NOT EXISTS ajuste (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
"""


def agora():
    return datetime.now().isoformat(timespec="seconds")


def conectar(caminho):
    conn = sqlite3.connect(caminho, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db():
    if "db" not in g:
        g.db = conectar(current_app.config["DB_PATH"])
    return g.db


def fechar(_erro=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_app(app):
    with conectar(app.config["DB_PATH"]) as conn:
        conn.executescript(ESQUEMA)
    app.teardown_appcontext(fechar)


def ler_ajuste(conn, chave, padrao=None):
    linha = conn.execute("SELECT valor FROM ajuste WHERE chave = ?", (chave,)).fetchone()
    return json.loads(linha["valor"]) if linha else padrao


def gravar_ajuste(conn, chave, valor):
    conn.execute(
        "INSERT INTO ajuste (chave, valor) VALUES (?, ?) "
        "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
        (chave, json.dumps(valor, ensure_ascii=False)),
    )
    conn.commit()
