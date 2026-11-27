"""Conexão SQLite e criação do esquema."""

import sqlite3
from datetime import datetime
from pathlib import Path

from flask import current_app, g

SCHEMA = Path(__file__).with_name("schema.sql")


def agora():
    """Instante atual no relógio do servidor (o PC da loja), com segundos."""
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def conectar(caminho):
    conn = sqlite3.connect(str(caminho), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def criar_esquema(conn):
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
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
