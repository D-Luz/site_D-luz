"""Conexão SQLite, criação do esquema e leitura de configurações."""

import sqlite3
from pathlib import Path

from flask import current_app, g

from . import config

SCHEMA = Path(__file__).with_name("schema.sql")


def conectar(caminho):
    conn = sqlite3.connect(str(caminho))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def criar_esquema(conn):
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    for chave, valor in config.PADROES.items():
        conn.execute("INSERT OR IGNORE INTO config (chave, valor) VALUES (?, ?)", (chave, valor))
    for tipo, texto in config.MODELOS.items():
        conn.execute("INSERT OR IGNORE INTO modelo_mensagem (tipo, texto) VALUES (?, ?)", (tipo, texto))
    conn.commit()


def get_db():
    if "db" not in g:
        g.db = conectar(current_app.config["DB_PATH"])
    return g.db


def _fechar(_erro=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_app(app):
    app.teardown_appcontext(_fechar)
    conn = conectar(app.config["DB_PATH"])
    try:
        criar_esquema(conn)
    finally:
        conn.close()


def cfg(conn):
    """Todas as configurações como dicionário (valores em texto)."""
    return {r["chave"]: r["valor"] for r in conn.execute("SELECT chave, valor FROM config")}


def cfg_num(c, chave):
    return float(c.get(chave) or config.PADROES[chave])
