"""scripts/criar_admin.py --redefinir: recuperar a senha de um administrador."""

import importlib.util
from pathlib import Path

import pytest

from app import auth
from app import db as db_module
from conftest import SENHA_TESTE

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "criar_admin.py"
NOVA_SENHA = "senha-nova-do-chefe-9"


@pytest.fixture
def script(app, monkeypatch):
    """O módulo do script, apontado para o banco do app de teste."""
    monkeypatch.setenv("PEDIDOS_DATA_DIR", str(app.config["DATA_DIR"]))
    spec = importlib.util.spec_from_file_location("criar_admin_redefinir_script", SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _linha(app, email):
    with app.app_context():
        linha = db_module.get_db().execute("SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone()
        return dict(linha) if linha else None


def _simular_terminal(monkeypatch, modulo, respostas, senhas):
    entradas = iter(respostas)
    segredos = iter(senhas)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(entradas))
    monkeypatch.setattr(modulo.getpass, "getpass", lambda _prompt="": next(segredos))


def test_redefinir_troca_a_senha_e_destrava(app, client, entrar, criar_usuario_teste, script):
    adm = criar_usuario_teste("admin", trocar_senha=True)
    with app.app_context(), db_module.transacao() as conexao:
        conexao.execute(
            "UPDATE usuarios SET falhas_login = 5, bloqueado_ate = '2999-01-01T00:00:00+00:00' WHERE id = ?",
            (adm.id,),
        )
    assert entrar(client, adm.email).status_code == 200  # bloqueado: não entra

    script.redefinir_senha_admin(adm.email.upper(), NOVA_SENHA)

    linha = _linha(app, adm.email)
    assert linha["trocar_senha"] == 0
    assert linha["falhas_login"] == 0
    assert linha["bloqueado_ate"] is None
    assert entrar(client, adm.email, SENHA_TESTE).status_code == 200  # a antiga não vale mais
    assert entrar(client, adm.email, NOVA_SENHA).status_code == 302
    assert client.get("/admin/usuarios").status_code == 200


def test_redefinir_derruba_a_sessao_aberta(app, client, entrar, criar_usuario_teste, script):
    adm = criar_usuario_teste("admin")
    assert entrar(client, adm.email).status_code == 302
    assert client.get("/admin/usuarios").status_code == 200

    script.redefinir_senha_admin(adm.email, NOVA_SENHA)

    resposta = client.get("/admin/usuarios")
    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]


def test_redefinir_recusa_email_inexistente(app, script, criar_usuario_teste):
    criar_usuario_teste("admin")
    with pytest.raises(auth.ErroUsuario, match="Não encontrei"):
        script.redefinir_senha_admin("ninguem@exemplo.com", NOVA_SENHA)


@pytest.mark.parametrize("perfil", ["solicitante", "gestor", "fiscal"])
def test_redefinir_recusa_quem_nao_e_admin(app, script, criar_usuario_teste, perfil):
    usuario = criar_usuario_teste(perfil)
    antes = _linha(app, usuario.email)["senha_hash"]
    with pytest.raises(auth.ErroUsuario, match="não é administrador"):
        script.redefinir_senha_admin(usuario.email, NOVA_SENHA)
    assert _linha(app, usuario.email)["senha_hash"] == antes


def test_redefinir_recusa_admin_desativado(app, script, criar_usuario_teste):
    criar_usuario_teste("admin")
    inativo = criar_usuario_teste("admin")
    with app.app_context(), db_module.transacao() as conexao:
        conexao.execute("UPDATE usuarios SET ativo = 0 WHERE id = ?", (inativo.id,))
    antes = _linha(app, inativo.email)["senha_hash"]
    with pytest.raises(auth.ErroUsuario, match="desativad"):
        script.redefinir_senha_admin(inativo.email, NOVA_SENHA)
    assert _linha(app, inativo.email)["senha_hash"] == antes


def test_redefinir_recusa_senha_curta(app, script, criar_usuario_teste):
    adm = criar_usuario_teste("admin")
    antes = _linha(app, adm.email)["senha_hash"]
    with pytest.raises(auth.ErroUsuario, match="10 caracteres"):
        script.redefinir_senha_admin(adm.email, "curta")
    assert _linha(app, adm.email)["senha_hash"] == antes


def test_main_redefinir_pergunta_e_troca(app, client, entrar, criar_usuario_teste, script, monkeypatch, capsys):
    adm = criar_usuario_teste("admin")
    _simular_terminal(monkeypatch, script, [adm.email], [NOVA_SENHA, NOVA_SENHA])
    script.main(["--redefinir"])
    saida = capsys.readouterr()
    assert NOVA_SENHA not in saida.out + saida.err
    assert "redefinida" in saida.out
    assert entrar(client, adm.email, NOVA_SENHA).status_code == 302


def test_main_redefinir_sai_com_erro_se_a_confirmacao_nao_confere(
    app, criar_usuario_teste, script, monkeypatch, capsys
):
    adm = criar_usuario_teste("admin")
    antes = _linha(app, adm.email)["senha_hash"]
    _simular_terminal(monkeypatch, script, [adm.email], [NOVA_SENHA, "outra-senha-longa-1"])
    with pytest.raises(SystemExit) as saida:
        script.main(["--redefinir"])
    assert saida.value.code == 1
    assert "não conferem" in capsys.readouterr().err
    assert _linha(app, adm.email)["senha_hash"] == antes


def test_main_redefinir_sai_com_erro_se_o_email_nao_e_admin(
    app, criar_usuario_teste, script, monkeypatch, capsys
):
    gestor = criar_usuario_teste("gestor")
    _simular_terminal(monkeypatch, script, [gestor.email], [NOVA_SENHA, NOVA_SENHA])
    with pytest.raises(SystemExit) as saida:
        script.main(["--redefinir"])
    assert saida.value.code == 1
    assert "não é administrador" in capsys.readouterr().err


def test_main_redefinir_sai_com_erro_se_o_email_nao_existe(app, script, monkeypatch, capsys):
    _simular_terminal(monkeypatch, script, ["ninguem@exemplo.com"], [NOVA_SENHA, NOVA_SENHA])
    with pytest.raises(SystemExit) as saida:
        script.main(["--redefinir"])
    assert saida.value.code == 1
    assert "Não encontrei" in capsys.readouterr().err


def test_main_sem_argumentos_continua_criando_o_admin(app, script, monkeypatch):
    _simular_terminal(
        monkeypatch, script, ["Chefe", "chefe@exemplo.com"], ["senha-longa-do-chefe", "senha-longa-do-chefe"]
    )
    script.main()
    assert _linha(app, "chefe@exemplo.com")["perfil"] == "admin"


def test_argumento_desconhecido_e_recusado(script, capsys):
    with pytest.raises(SystemExit) as saida:
        script.main(["--apagar-tudo"])
    assert saida.value.code == 2
