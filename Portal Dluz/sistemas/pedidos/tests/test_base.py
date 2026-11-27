import sqlite3
import sys

import pytest

from app import create_app
from app import db as db_module


def test_schema_cria_as_quatro_tabelas(db):
    nomes = {
        linha["name"]
        for linha in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert nomes == {"usuarios", "pedidos", "historico", "avisos"}


def test_foreign_keys_ativas(db):
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO historico (pedido_id, usuario_id, acao, status_novo, criado_em)"
            " VALUES (999, 999, 'criar', 'pendente', ?)",
            (db_module.agora_utc(),),
        )


AVISOS_DO_ESQUEMA_ANTIGO = """
CREATE TABLE avisos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id    INTEGER NOT NULL REFERENCES usuarios (id),
    pedido_id     INTEGER REFERENCES pedidos (id),
    assunto       TEXT NOT NULL,
    corpo         TEXT NOT NULL,
    lido          INTEGER NOT NULL DEFAULT 0,
    email_status  TEXT NOT NULL DEFAULT 'pendente',
    criado_em     TEXT NOT NULL
);
INSERT INTO avisos (usuario_id, assunto, corpo, email_status, criado_em)
    VALUES (1, 'Assunto', 'Corpo', 'erro', '2026-10-23T12:00:00+00:00');
"""


def _colunas(app, tabela):
    with app.app_context():
        return [linha["name"] for linha in db_module.get_db().execute(f"PRAGMA table_info({tabela})")]


def test_banco_do_esquema_antigo_ganha_as_colunas_de_tentativa_de_email(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    antigo = sqlite3.connect(data / "pedidos.db")
    antigo.executescript(AVISOS_DO_ESQUEMA_ANTIGO)
    antigo.close()

    app = create_app({"DATA_DIR": data})
    assert {"email_tentativas", "email_ultima_tentativa"} <= set(_colunas(app, "avisos"))
    with app.app_context():
        linha = db_module.get_db().execute("SELECT * FROM avisos").fetchone()
    assert (linha["email_tentativas"], linha["email_ultima_tentativa"]) == (0, None)

    create_app({"DATA_DIR": data})  # de novo: nada a acrescentar, nada quebra
    assert _colunas(app, "avisos").count("email_tentativas") == 1


def _inserir_usuario(db, email="ana@exemplo.com"):
    db.execute(
        "INSERT INTO usuarios (nome, email, senha_hash, perfil, criado_em)"
        " VALUES ('Ana', ?, 'x', 'gestor', ?)",
        (email, db_module.agora_utc()),
    )


def _contar_por_outra_conexao(app):
    """Conta usuários por uma segunda conexão: só enxerga o que foi commitado."""
    conexao = sqlite3.connect(app.config["DATA_DIR"] / "pedidos.db")
    try:
        return conexao.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]
    finally:
        conexao.close()


def test_transacao_faz_commit_no_sucesso(app, db):
    with db_module.transacao():
        _inserir_usuario(db)
        assert _contar_por_outra_conexao(app) == 0  # ainda não commitado
    assert _contar_por_outra_conexao(app) == 1


def test_transacao_faz_rollback_em_erro(app, db):
    with pytest.raises(RuntimeError):
        with db_module.transacao():
            _inserir_usuario(db)
            raise RuntimeError("falha no meio da transação")
    assert db.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 0
    assert _contar_por_outra_conexao(app) == 0


def test_transacao_aninhada_so_commita_no_bloco_externo(app, db):
    with db_module.transacao():
        _inserir_usuario(db, "a@exemplo.com")
        with db_module.transacao():
            _inserir_usuario(db, "b@exemplo.com")
        assert _contar_por_outra_conexao(app) == 0  # o bloco interno não commitou
        _inserir_usuario(db, "c@exemplo.com")
    assert _contar_por_outra_conexao(app) == 3


def test_erro_no_bloco_interno_desfaz_o_trabalho_do_externo(app, db):
    with pytest.raises(RuntimeError):
        with db_module.transacao():
            _inserir_usuario(db, "a@exemplo.com")
            with db_module.transacao():
                _inserir_usuario(db, "b@exemplo.com")
                raise RuntimeError("falha no bloco interno")
    assert _contar_por_outra_conexao(app) == 0


def test_erro_no_externo_desfaz_bloco_interno_ja_concluido(app, db):
    with pytest.raises(RuntimeError):
        with db_module.transacao():
            with db_module.transacao():
                _inserir_usuario(db, "b@exemplo.com")
            raise RuntimeError("falha depois do bloco interno")
    assert _contar_por_outra_conexao(app) == 0


def test_transacao_pode_ser_usada_de_novo_depois_de_erro(app, db):
    with pytest.raises(RuntimeError):
        with db_module.transacao():
            raise RuntimeError("primeira falha")
    with db_module.transacao():
        _inserir_usuario(db)
    assert _contar_por_outra_conexao(app) == 1


def test_agora_utc_e_iso_8601_com_offset():
    assert db_module.agora_utc().endswith("+00:00")


def test_chave_secreta_gerada_e_reaproveitada(tmp_path):
    config = {"TESTING": True, "DATA_DIR": tmp_path / "data"}
    primeira = create_app(config)
    segunda = create_app(config)
    assert primeira.config["SECRET_KEY"]
    assert primeira.config["SECRET_KEY"] == segunda.config["SECRET_KEY"]
    assert (tmp_path / "data" / "secret.key").exists()


def test_chave_secreta_existente_e_reaproveitada(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "secret.key").write_text("a" * 64, encoding="ascii")
    app = create_app({"DATA_DIR": data})
    assert app.config["SECRET_KEY"] == "a" * 64


@pytest.mark.skipif(sys.platform == "win32", reason="permissões POSIX")
def test_arquivo_da_chave_secreta_so_o_dono_le(tmp_path):
    create_app({"DATA_DIR": tmp_path / "data"})
    modo = (tmp_path / "data" / "secret.key").stat().st_mode & 0o777
    assert modo == 0o600


@pytest.mark.parametrize("conteudo", ["", "  \n", "curta"])
def test_chave_secreta_vazia_ou_curta_e_recusada(tmp_path, conteudo):
    data = tmp_path / "data"
    data.mkdir()
    (data / "secret.key").write_text(conteudo, encoding="ascii")
    with pytest.raises(RuntimeError, match="secret.key"):
        create_app({"DATA_DIR": data})


def test_chave_secreta_vem_da_variavel_de_ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("PEDIDOS_SECRET_KEY", "da-variavel")
    app = create_app({"DATA_DIR": tmp_path / "data"})
    assert app.config["SECRET_KEY"] == "da-variavel"
    assert not (tmp_path / "data" / "secret.key").exists()


def test_variavel_de_ambiente_configura_e_override_vence(tmp_path, monkeypatch):
    monkeypatch.setenv("PEDIDOS_MAX_PDF_MB", "7")
    monkeypatch.setenv("PEDIDOS_BASE_URL", "https://pedidos.exemplo.com")
    app = create_app({"DATA_DIR": tmp_path / "data", "BASE_URL": "https://outro.exemplo.com"})
    assert app.config["MAX_PDF_MB"] == 7
    assert app.config["BASE_URL"] == "https://outro.exemplo.com"


def test_email_mode_invalido_e_recusado(tmp_path):
    with pytest.raises(ValueError):
        create_app({"DATA_DIR": tmp_path / "data", "EMAIL_MODE": "pombo"})


def test_cookie_de_sessao_seguro_por_padrao(app):
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["PERMANENT_SESSION_LIFETIME"].total_seconds() == 8 * 3600


def test_token_csrf_nao_expira_antes_da_sessao(app):
    assert app.config["WTF_CSRF_TIME_LIMIT"] is None


def test_cookie_inseguro_so_com_variavel_de_ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("PEDIDOS_COOKIE_INSECURE", "1")
    app = create_app({"DATA_DIR": tmp_path / "data"})
    assert app.config["SESSION_COOKIE_SECURE"] is False


def test_cabecalhos_de_seguranca(client):
    resposta = client.get("/qualquer-url-que-nao-existe")
    assert resposta.headers["X-Content-Type-Options"] == "nosniff"
    assert resposta.headers["X-Frame-Options"] == "DENY"
    assert resposta.headers["Referrer-Policy"]
    assert "default-src" in resposta.headers["Content-Security-Policy"]
