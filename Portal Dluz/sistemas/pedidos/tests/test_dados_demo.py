"""scripts/dados_demo.py: dados de demonstração só para testes locais."""

import importlib.util
from pathlib import Path

import pytest

from app import arquivos, create_app
from app import db as db_module
from app.workflow import STATUS

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dados_demo.py"

# O script é só de desenvolvimento e não vai para o PC do sistema (guia, passo 2):
# lá estes testes aparecem como "skipped".
pytestmark = pytest.mark.skipif(
    not SCRIPT.exists(), reason="scripts/dados_demo.py não é copiado para o PC do sistema"
)
EMAILS = {
    "admin@demo.local": "admin",
    "pedidos@demo.local": "solicitante",
    "gestor1@demo.local": "gestor",
    "gestor2@demo.local": "gestor",
    "fiscal@demo.local": "fiscal",
}


@pytest.fixture
def demo(tmp_path, monkeypatch):
    """O módulo do script, apontado para uma pasta de dados temporária."""
    monkeypatch.setenv("PEDIDOS_DATA_DIR", str(tmp_path / "demo"))
    spec = importlib.util.spec_from_file_location("dados_demo_script", SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo, tmp_path / "demo"


def consultar(data_dir, sql, parametros=()):
    app = create_app({"DATA_DIR": data_dir})
    with app.app_context():
        return [dict(l) for l in db_module.get_db().execute(sql, parametros).fetchall()]


def test_cria_usuarios_e_um_pedido_por_status(demo, capsys):
    modulo, data_dir = demo

    assert modulo.main([]) == 0

    usuarios = {u["email"]: u for u in consultar(data_dir, "SELECT * FROM usuarios")}
    assert {email: u["perfil"] for email, u in usuarios.items()} == EMAILS
    assert all(u["trocar_senha"] == 0 and u["ativo"] == 1 for u in usuarios.values())

    pedidos = consultar(data_dir, "SELECT * FROM pedidos")
    assert len(pedidos) == 8
    por_status = {s: [p for p in pedidos if p["status"] == s] for s in STATUS}
    assert {s: len(p) for s, p in por_status.items()} == {
        "pendente": 2, "aprovado": 1, "reprovado": 1, "enviado": 1,
        "faturado": 1, "recebido": 1, "arquivado": 1,
    }
    assert any(len(p["cliente"]) > 60 for p in pedidos)
    assert any(p["descricao"].count("\n") >= 2 for p in pedidos)
    (reprovado,) = por_status["reprovado"]
    motivo = consultar(
        data_dir, "SELECT comentario FROM historico WHERE pedido_id = ? AND acao = 'reprovar'", (reprovado["id"],)
    )
    assert motivo[0]["comentario"]
    assert all(p["solicitante_id"] == usuarios["pedidos@demo.local"]["id"] for p in pedidos)

    app = create_app({"DATA_DIR": data_dir})
    with app.app_context():
        assert all(arquivos.pdf_existe(p["pdf_arquivo"]) for p in pedidos)
        for p in pedidos:
            assert arquivos.caminho_pdf(p["pdf_arquivo"]).read_bytes().startswith(b"%PDF-1.4")

    saida = capsys.readouterr().out
    assert modulo.SENHA_DEMO in saida
    assert saida.count(modulo.SENHA_DEMO) == 1  # a senha é impressa uma só vez
    assert "SOMENTE" in saida and "produção" in saida


def test_senha_demo_entra_e_nao_pede_troca(demo):
    modulo, data_dir = demo
    modulo.main([])

    app = create_app({"DATA_DIR": data_dir, "WTF_CSRF_ENABLED": False})
    cliente = app.test_client()
    resposta = cliente.post(
        "/login", data={"email": "pedidos@demo.local", "senha": modulo.SENHA_DEMO},
        base_url="https://localhost",
    )
    assert resposta.status_code == 302 and resposta.headers["Location"] == "/"
    assert len(modulo.SENHA_DEMO) >= app.config["MIN_SENHA"]


def test_recusa_rodar_duas_vezes_sem_forcar(demo, capsys):
    modulo, data_dir = demo
    assert modulo.main([]) == 0
    capsys.readouterr()

    assert modulo.main([]) == 1

    erro = capsys.readouterr().err
    assert "--forcar" in erro
    assert len(consultar(data_dir, "SELECT id FROM pedidos")) == 8
    assert len(consultar(data_dir, "SELECT id FROM usuarios")) == 5


def test_recusa_se_ja_houver_qualquer_usuario(demo, capsys):
    modulo, data_dir = demo
    app = create_app({"DATA_DIR": data_dir})
    with app.app_context():
        from app import auth

        auth.criar_usuario("Real", "real@empresa.com", "senha-longa-123", "admin", trocar_senha=False)

    assert modulo.main([]) == 1
    assert "--forcar" in capsys.readouterr().err
    assert len(consultar(data_dir, "SELECT id FROM usuarios")) == 1


def test_forcar_reaproveita_usuarios_e_acrescenta_pedidos(demo):
    modulo, data_dir = demo
    modulo.main([])

    assert modulo.main(["--forcar"]) == 0

    assert len(consultar(data_dir, "SELECT id FROM usuarios")) == 5
    assert len(consultar(data_dir, "SELECT id FROM pedidos")) == 16


def test_help_funciona_e_nao_cria_nada(demo, capsys):
    modulo, data_dir = demo

    with pytest.raises(SystemExit) as saida:
        modulo.main(["--help"])

    assert saida.value.code == 0
    ajuda = capsys.readouterr().out
    assert "--forcar" in ajuda and "produção" in ajuda
    assert not (data_dir / "pedidos.db").exists()


@pytest.mark.parametrize("flag", ["--force", "--forcar=1", "-x", "forcar"])
def test_argumento_desconhecido_e_erro_e_nao_cria_nada(demo, capsys, flag):
    modulo, data_dir = demo

    with pytest.raises(SystemExit) as saida:
        modulo.main([flag])

    assert saida.value.code == 2
    assert "usage" in capsys.readouterr().err.lower()
    assert not (data_dir / "pedidos.db").exists()


def test_email_fica_desligado_mesmo_com_smtp_no_ambiente(demo, monkeypatch):
    modulo, data_dir = demo
    monkeypatch.setenv("PEDIDOS_EMAIL_MODE", "smtp")

    # SMTP no ambiente parece produção: só roda com a flag explícita.
    assert modulo.main(["--aceito-risco-de-producao"]) == 0

    avisos = consultar(data_dir, "SELECT email_status FROM avisos")
    assert avisos and {a["email_status"] for a in avisos} == {"desligado"}


def _usuario_real(data_dir):
    app = create_app({"DATA_DIR": data_dir})
    with app.app_context():
        from app import auth

        auth.criar_usuario("Real", "real@empresa.com", "senha-longa-123", "admin", trocar_senha=False)


def test_forcar_com_usuario_real_pede_sim_e_cancela_sem_ele(demo, monkeypatch, capsys):
    modulo, data_dir = demo
    _usuario_real(data_dir)

    for resposta in ("sim", "", "s", "nao"):
        monkeypatch.setattr("builtins.input", lambda _prompt, resposta=resposta: resposta)
        assert modulo.main(["--forcar"]) == 1

    erro = capsys.readouterr().err
    assert "NÃO são de demonstração" in erro and "NUNCA" in erro and "Cancelado" in erro
    assert len(consultar(data_dir, "SELECT id FROM usuarios")) == 1
    assert consultar(data_dir, "SELECT id FROM pedidos") == []


def test_forcar_com_usuario_real_sem_terminal_cancela(demo, monkeypatch):
    modulo, data_dir = demo
    _usuario_real(data_dir)

    def sem_entrada(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", sem_entrada)
    assert modulo.main(["--forcar"]) == 1
    assert len(consultar(data_dir, "SELECT id FROM usuarios")) == 1


def test_forcar_com_usuario_real_segue_ao_digitar_sim(demo, monkeypatch):
    modulo, data_dir = demo
    _usuario_real(data_dir)
    monkeypatch.setattr("builtins.input", lambda _prompt: "SIM")

    assert modulo.main(["--forcar"]) == 0

    assert len(consultar(data_dir, "SELECT id FROM usuarios")) == 6
    assert len(consultar(data_dir, "SELECT id FROM pedidos")) == 8


def test_forcar_so_com_usuarios_demo_nao_pergunta_nada(demo, monkeypatch):
    modulo, _ = demo
    modulo.main([])

    def nao_deve_perguntar(_prompt):
        raise AssertionError("não devia pedir confirmação")

    monkeypatch.setattr("builtins.input", nao_deve_perguntar)
    assert modulo.main(["--forcar"]) == 0


# --- recusa em ambiente com cara de produção --------------------------------


@pytest.mark.parametrize(
    "variavel, valor",
    [
        ("PEDIDOS_BASE_URL", "https://pc-pedidos.tail1234.ts.net"),
        ("PEDIDOS_BASE_URL", "  HTTPS://pc-pedidos.tail1234.ts.net"),
        ("PEDIDOS_EMAIL_MODE", "smtp"),
    ],
)
def test_recusa_em_ambiente_de_producao(demo, monkeypatch, capsys, variavel, valor):
    modulo, data_dir = demo
    monkeypatch.setenv(variavel, valor)

    assert modulo.main([]) == 1

    erro = capsys.readouterr().err
    assert "produção" in erro and variavel in erro
    assert "--aceito-risco-de-producao" in erro
    assert not data_dir.exists()  # recusa antes de criar qualquer coisa


def test_flag_explicita_deixa_rodar_em_ambiente_de_producao(demo, monkeypatch):
    modulo, data_dir = demo
    monkeypatch.setenv("PEDIDOS_BASE_URL", "https://pc-pedidos.tail1234.ts.net")

    assert modulo.main(["--aceito-risco-de-producao"]) == 0
    assert len(consultar(data_dir, "SELECT id FROM usuarios")) == 5


def test_base_url_local_por_http_nao_e_producao(demo, monkeypatch):
    modulo, _ = demo
    monkeypatch.setenv("PEDIDOS_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("PEDIDOS_EMAIL_MODE", "desligado")
    assert modulo.main([]) == 0
