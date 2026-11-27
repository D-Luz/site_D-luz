"""Conexão SQLite, criação do esquema e transações."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app, g

SCHEMA = Path(__file__).with_name("schema.sql")


def agora_utc():
    """Instante atual em ISO 8601 UTC, com offset (ex.: 2026-10-23T15:00:00+00:00)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def caminho_banco():
    return Path(current_app.config["DATA_DIR"]) / "pedidos.db"


def get_db():
    """Conexão da requisição atual (uma por contexto da aplicação)."""
    if "db" not in g:
        # isolation_level=None: modo autocommit; BEGIN/COMMIT/ROLLBACK são
        # emitidos explicitamente por transacao().
        conexao = sqlite3.connect(caminho_banco(), isolation_level=None)
        conexao.row_factory = sqlite3.Row
        conexao.execute("PRAGMA foreign_keys = ON")
        g.db = conexao
    return g.db


def fechar_db(_erro=None):
    g.pop("transacao_nivel", None)
    conexao = g.pop("db", None)
    if conexao is not None:
        conexao.close()


# Colunas que entraram no schema.sql depois da primeira versão: CREATE TABLE IF
# NOT EXISTS não as cria num banco que já existe, então init_db as acrescenta.
COLUNAS_ACRESCENTADAS = {
    "avisos": (
        ("email_tentativas", "INTEGER NOT NULL DEFAULT 0"),
        ("email_ultima_tentativa", "TEXT"),
    ),
}


def _colunas_que_faltam(conexao):
    faltam = []
    for tabela, colunas in COLUNAS_ACRESCENTADAS.items():
        existentes = {linha[1] for linha in conexao.execute(f"PRAGMA table_info({tabela})")}
        faltam += [(tabela, nome, tipo) for nome, tipo in colunas if nome not in existentes]
    return faltam


def init_db():
    """Cria as pastas de dados, as tabelas que ainda não existem e as colunas novas."""
    data_dir = Path(current_app.config["DATA_DIR"])
    (data_dir / "pdfs").mkdir(parents=True, exist_ok=True)
    conexao = get_db()
    conexao.executescript(SCHEMA.read_text(encoding="utf-8"))
    if not _colunas_que_faltam(conexao):
        return  # o caso comum: nada a mudar e nenhum bloqueio de escrita
    with transacao():
        # Confere de novo já com o bloqueio: outro processo pode ter acabado de acrescentar.
        for tabela, nome, tipo in _colunas_que_faltam(conexao):
            conexao.execute(f"ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}")


@contextmanager
def transacao():
    """Agrupa várias escritas: commit ao final ou rollback se der erro.

    Pode ser aninhada: só o bloco mais externo abre (BEGIN IMMEDIATE, que já
    toma o bloqueio de escrita) e fecha a transação. Um erro em qualquer bloco
    interno se propaga até o externo, que desfaz tudo.
    """
    conexao = get_db()
    nivel = g.get("transacao_nivel", 0)
    if nivel > 0:
        g.transacao_nivel = nivel + 1
        try:
            yield conexao
        finally:
            g.transacao_nivel = nivel
        return

    conexao.execute("BEGIN IMMEDIATE")
    g.transacao_nivel = 1
    try:
        yield conexao
    except BaseException:
        conexao.rollback()
        raise
    else:
        conexao.commit()
    finally:
        g.transacao_nivel = 0
