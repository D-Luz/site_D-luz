import importlib.util
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app import admin, auth, create_app
from app import config as config_mod
from app import db as db_module
from app.workflow import PERFIS
from conftest import SENHA_TESTE, ClienteHttps

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "criar_admin.py"
ALFABETO_PROIBIDO = set("0O1lI")


# --- apoio -------------------------------------------------------------------


@pytest.fixture
def admin_logado(client, entrar, criar_usuario_teste):
    """Administrador já autenticado no client."""
    adm = criar_usuario_teste("admin")
    assert entrar(client, adm.email).status_code == 302
    return adm


def _local(resposta):
    return urlsplit(resposta.headers["Location"]).path


def _linha(app, usuario_id):
    with app.app_context():
        linha = (
            db_module.get_db()
            .execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,))
            .fetchone()
        )
        return dict(linha) if linha else None


def _por_email(app, email):
    with app.app_context():
        linha = (
            db_module.get_db()
            .execute("SELECT * FROM usuarios WHERE email = ?", (email,))
            .fetchone()
        )
        return dict(linha) if linha else None


def _total_usuarios(app):
    with app.app_context():
        return db_module.get_db().execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]


def _senha_mostrada(resposta):
    html = resposta.get_data(as_text=True)
    achado = re.search(r'<code id="senha-provisoria">([^<]+)</code>', html)
    assert achado, "a página não mostra a senha provisória"
    return achado.group(1)


def _criar(client, **campos):
    dados = {"nome": "Beto", "email": "beto@exemplo.com", "perfil": "gestor"}
    dados.update(campos)
    return client.post("/admin/usuarios", data=dados)


def _carregar_script():
    spec = importlib.util.spec_from_file_location("criar_admin_script", SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# --- acesso ------------------------------------------------------------------


def test_admin_ve_a_lista_de_usuarios(client, admin_logado):
    resposta = client.get("/admin/usuarios")
    assert resposta.status_code == 200
    html = resposta.get_data(as_text=True)
    assert admin_logado.nome in html
    assert admin_logado.email in html
    assert "Ativo" in html


@pytest.mark.parametrize("perfil", ["solicitante", "gestor", "fiscal"])
def test_so_admin_acessa_a_administracao(client, entrar, criar_usuario_teste, perfil):
    usuario = criar_usuario_teste(perfil)
    alvo = criar_usuario_teste("gestor")
    assert entrar(client, usuario.email).status_code == 302

    assert client.get("/admin/usuarios").status_code == 403
    assert _criar(client).status_code == 403
    assert client.post(f"/admin/usuarios/{alvo.id}/ativo", data={"ativo": "0"}).status_code == 403
    assert client.post(f"/admin/usuarios/{alvo.id}/senha").status_code == 403


def test_anonimo_vai_para_o_login_nas_quatro_rotas(app, client, criar_usuario_teste):
    alvo = criar_usuario_teste("gestor")
    antes = _linha(app, alvo.id)
    respostas = [
        client.get("/admin/usuarios"),
        _criar(client),
        client.post(f"/admin/usuarios/{alvo.id}/ativo", data={"ativo": "0"}),
        client.post(f"/admin/usuarios/{alvo.id}/senha"),
    ]
    for resposta in respostas:
        assert resposta.status_code == 302
        assert _local(resposta) == "/login"
    assert _linha(app, alvo.id) == antes
    assert _total_usuarios(app) == 1


# --- criar -------------------------------------------------------------------


def test_admin_cria_usuario_com_troca_obrigatoria(app, client, admin_logado):
    resposta = _criar(client, nome="  Beto Souza ", email="Beto@Exemplo.com", perfil="fiscal")
    assert resposta.status_code == 200

    criado = _por_email(app, "beto@exemplo.com")
    assert criado["nome"] == "Beto Souza"
    assert criado["perfil"] == "fiscal"
    assert criado["ativo"] == 1
    assert criado["trocar_senha"] == 1

    senha = _senha_mostrada(resposta)
    assert len(senha) == 14
    assert not set(senha) & ALFABETO_PROIBIDO
    assert senha not in criado["senha_hash"]
    assert auth.check_password_hash(criado["senha_hash"], senha)
    assert "Beto Souza" in resposta.get_data(as_text=True)


def test_senha_provisoria_e_aleatoria_e_atende_o_minimo(app, client, admin_logado):
    senhas = {_senha_mostrada(_criar(client, email=f"u{n}@exemplo.com")) for n in range(8)}
    assert len(senhas) == 8
    for senha in senhas:
        assert len(senha) >= app.config["MIN_SENHA"]
        assert any(c.isdigit() for c in senha)
        assert any(c.islower() for c in senha)
        assert any(c.isupper() for c in senha)


def test_senha_provisoria_respeita_minimo_configurado_maior(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATA_DIR": tmp_path / "data-min",
            "WTF_CSRF_ENABLED": False,
            "MIN_SENHA": 20,
        }
    )
    with app.app_context():
        assert len(admin._senha_provisoria()) >= 20


def test_usuario_criado_entra_com_a_senha_mostrada_e_e_forcado_a_trocar(
    client, app, entrar, admin_logado
):
    senha = _senha_mostrada(_criar(client))

    outro = app.test_client()
    resposta = entrar(outro, "beto@exemplo.com", senha)
    assert resposta.status_code == 302
    assert _local(outro.get("/")) == "/trocar-senha"
    assert _local(outro.get("/admin/usuarios")) == "/trocar-senha"


def test_senha_provisoria_nao_vai_para_o_cookie_nem_para_o_cache(client, admin_logado):
    resposta = _criar(client)
    senha = _senha_mostrada(resposta)

    assert "no-store" in resposta.headers["Cache-Control"]
    for cookie in resposta.headers.getlist("Set-Cookie"):
        assert senha not in cookie
    with client.session_transaction() as sessao:
        assert senha not in repr(dict(sessao))
        assert "_flashes" not in sessao
    # Recarregar a lista não mostra a senha de novo.
    assert senha not in client.get("/admin/usuarios").get_data(as_text=True)


def test_email_duplicado_e_recusado(app, client, admin_logado, criar_usuario_teste):
    existente = criar_usuario_teste("gestor")
    antes = _total_usuarios(app)

    resposta = _criar(client, email=existente.email.upper())
    assert resposta.status_code == 400
    assert "Já existe um usuário com este e-mail." in resposta.get_data(as_text=True)
    assert 'id="senha-provisoria"' not in resposta.get_data(as_text=True)
    assert _total_usuarios(app) == antes


def test_perfil_invalido_e_recusado(app, client, admin_logado):
    antes = _total_usuarios(app)
    for perfil in ("superusuario", "", "ADMIN"):
        resposta = _criar(client, perfil=perfil)
        assert resposta.status_code == 400
        assert "Perfil inválido" in resposta.get_data(as_text=True)
    assert _total_usuarios(app) == antes


def test_dados_invalidos_na_criacao_mostram_erro_e_nao_criam(app, client, admin_logado):
    antes = _total_usuarios(app)
    for campos, trecho in (
        ({"nome": "   "}, "Informe o nome"),
        ({"email": "sem-arroba"}, "e-mail válido"),
    ):
        resposta = _criar(client, **campos)
        assert resposta.status_code == 400
        assert trecho in resposta.get_data(as_text=True)
    resposta = client.post("/admin/usuarios", data={})
    assert resposta.status_code == 400
    assert _total_usuarios(app) == antes


def test_formulario_de_criacao_oferece_os_quatro_perfis(client, admin_logado):
    html = client.get("/admin/usuarios").get_data(as_text=True)
    for perfil in PERFIS:
        assert f'value="{perfil}"' in html


def test_nome_com_html_e_escapado(client, admin_logado):
    resposta = _criar(client, nome="<script>alert(1)</script>", email="xss@exemplo.com")
    html = resposta.get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    lista = client.get("/admin/usuarios").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in lista
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in lista


def test_erro_de_criacao_devolve_o_que_foi_digitado_escapado(client, admin_logado):
    resposta = _criar(client, nome='"><b>x</b>', email="invalido")
    html = resposta.get_data(as_text=True)
    assert resposta.status_code == 400
    assert "<b>x</b>" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html


# --- ativar e desativar ------------------------------------------------------


def test_admin_desativa_e_reativa_usuario(app, client, entrar, admin_logado, criar_usuario_teste):
    alvo = criar_usuario_teste("gestor")
    outro = app.test_client()
    assert entrar(outro, alvo.email).status_code == 302
    assert outro.get("/trocar-senha").status_code == 200  # logado

    resposta = client.post(f"/admin/usuarios/{alvo.id}/ativo", data={"ativo": "0"})
    assert resposta.status_code == 302
    assert _local(resposta) == "/admin/usuarios"
    assert _linha(app, alvo.id)["ativo"] == 0
    assert "Inativo" in client.get("/admin/usuarios").get_data(as_text=True)
    # Vale na hora: a sessão aberta do usuário deixa de funcionar e ele não entra.
    assert _local(outro.get("/trocar-senha")) == "/login"
    assert entrar(app.test_client(), alvo.email).status_code == 200

    resposta = client.post(f"/admin/usuarios/{alvo.id}/ativo", data={"ativo": "1"})
    assert resposta.status_code == 302
    assert _linha(app, alvo.id)["ativo"] == 1
    assert entrar(app.test_client(), alvo.email).status_code == 302


def test_admin_nao_desativa_a_si_mesmo(app, client, admin_logado):
    resposta = client.post(f"/admin/usuarios/{admin_logado.id}/ativo", data={"ativo": "0"})
    assert resposta.status_code == 400
    assert "própria conta" in resposta.get_data(as_text=True)
    assert _linha(app, admin_logado.id)["ativo"] == 1
    assert client.get("/admin/usuarios").status_code == 200


def test_nao_desativa_o_ultimo_admin_ativo(app, admin_logado, criar_usuario_teste):
    # Quem pede já foi desativado entre o login e a gravação (corrida entre dois
    # administradores): o alvo é o único admin ativo e precisa continuar ativo.
    fantasma = criar_usuario_teste("admin")
    with app.app_context():
        db_module.get_db().execute("UPDATE usuarios SET ativo = 0 WHERE id = ?", (fantasma.id,))
        erro = admin._alterar_ativo(admin_logado.id, 0, quem_pede=fantasma.id)
    assert erro and "último administrador" in erro
    assert _linha(app, admin_logado.id)["ativo"] == 1


def test_admin_desativa_outro_admin_se_sobra_um(app, client, admin_logado, criar_usuario_teste):
    outro = criar_usuario_teste("admin")
    resposta = client.post(f"/admin/usuarios/{outro.id}/ativo", data={"ativo": "0"})
    assert resposta.status_code == 302
    assert _linha(app, outro.id)["ativo"] == 0


def test_ativo_com_valor_invalido_e_400(app, client, admin_logado, criar_usuario_teste):
    alvo = criar_usuario_teste("gestor")
    for valor in ("2", "", "sim", "true", "-1"):
        resposta = client.post(f"/admin/usuarios/{alvo.id}/ativo", data={"ativo": valor})
        assert resposta.status_code == 400
    assert client.post(f"/admin/usuarios/{alvo.id}/ativo", data={}).status_code == 400
    assert _linha(app, alvo.id)["ativo"] == 1


def test_usuario_inexistente_e_404(client, admin_logado):
    assert client.post("/admin/usuarios/9999/ativo", data={"ativo": "0"}).status_code == 404
    assert client.post("/admin/usuarios/9999/senha").status_code == 404
    assert client.post("/admin/usuarios/abc/ativo", data={"ativo": "0"}).status_code == 404


# --- redefinir senha ---------------------------------------------------------


def test_redefinir_senha_liga_troca_obrigatoria(app, client, entrar, admin_logado, criar_usuario_teste):
    alvo = criar_usuario_teste("gestor", trocar_senha=False)
    antigo_hash = _linha(app, alvo.id)["senha_hash"]

    resposta = client.post(f"/admin/usuarios/{alvo.id}/senha")
    assert resposta.status_code == 200
    assert "no-store" in resposta.headers["Cache-Control"]
    senha = _senha_mostrada(resposta)
    assert alvo.email in resposta.get_data(as_text=True)

    linha = _linha(app, alvo.id)
    assert linha["trocar_senha"] == 1
    assert linha["senha_hash"] != antigo_hash
    assert auth.check_password_hash(linha["senha_hash"], senha)
    assert senha not in linha["senha_hash"]

    # A senha antiga deixou de valer; a provisória entra e é forçada a trocar.
    assert entrar(app.test_client(), alvo.email, alvo.senha).status_code == 200
    outro = app.test_client()
    assert entrar(outro, alvo.email, senha).status_code == 302
    assert _local(outro.get("/")) == "/trocar-senha"


def test_redefinir_senha_derruba_a_sessao_aberta_do_usuario(
    app, client, entrar, admin_logado, criar_usuario_teste
):
    alvo = criar_usuario_teste("gestor")
    sessao_do_alvo = app.test_client()
    assert entrar(sessao_do_alvo, alvo.email).status_code == 302
    assert sessao_do_alvo.get("/trocar-senha").status_code == 200  # logado

    assert client.post(f"/admin/usuarios/{alvo.id}/senha").status_code == 200

    assert _local(sessao_do_alvo.get("/trocar-senha")) == "/login"


def test_redefinir_senha_destrava_a_conta(app, client, entrar, admin_logado, criar_usuario_teste):
    alvo = criar_usuario_teste("gestor")
    for _ in range(app.config["LOGIN_MAX_FALHAS"]):
        entrar(app.test_client(), alvo.email, "senha-errada-qualquer")
    assert _linha(app, alvo.id)["bloqueado_ate"] is not None
    assert entrar(app.test_client(), alvo.email).status_code == 200  # nem a certa entra

    senha = _senha_mostrada(client.post(f"/admin/usuarios/{alvo.id}/senha"))

    linha = _linha(app, alvo.id)
    assert linha["falhas_login"] == 0
    assert linha["bloqueado_ate"] is None
    assert entrar(app.test_client(), alvo.email, senha).status_code == 302


def test_admin_nao_redefine_a_propria_senha(app, client, admin_logado):
    antes = _linha(app, admin_logado.id)["senha_hash"]
    resposta = client.post(f"/admin/usuarios/{admin_logado.id}/senha")
    assert resposta.status_code == 400
    assert "Trocar senha" in resposta.get_data(as_text=True)
    assert _linha(app, admin_logado.id)["senha_hash"] == antes
    assert client.get("/admin/usuarios").status_code == 200


def test_pagina_da_lista_nao_expoe_hash_nem_senha(client, admin_logado):
    html = client.get("/admin/usuarios").get_data(as_text=True)
    assert "senha_hash" not in html
    assert "scrypt:" not in html and "pbkdf2:" not in html
    assert "senha-provisoria" not in html


def test_lista_marca_quem_precisa_trocar_a_senha(client, admin_logado, criar_usuario_teste):
    criar_usuario_teste("gestor", trocar_senha=True)
    html = client.get("/admin/usuarios").get_data(as_text=True)
    assert "Troca de senha pendente" in html


# --- CSRF --------------------------------------------------------------------


def test_post_sem_csrf_e_recusado_nas_rotas_de_admin(tmp_path):
    app = create_app(
        {"TESTING": True, "DATA_DIR": tmp_path / "data-csrf", "WTF_CSRF_ENABLED": True}
    )
    app.test_client_class = ClienteHttps
    client = app.test_client()
    with app.app_context():
        auth.criar_usuario("Adm", "adm@exemplo.com", SENHA_TESTE, "admin", trocar_senha=False)
        alvo = auth.criar_usuario("Alvo", "alvo@exemplo.com", SENHA_TESTE, "gestor", trocar_senha=False)

    pagina = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', pagina).group(1)
    referer = {"Referer": "https://localhost/"}
    resposta = client.post(
        "/login",
        data={"email": "adm@exemplo.com", "senha": SENHA_TESTE, "csrf_token": token},
        headers=referer,
    )
    assert resposta.status_code == 302

    antes = _linha(app, alvo)
    posts = [
        ("/admin/usuarios", {"nome": "X", "email": "x@exemplo.com", "perfil": "admin"}),
        (f"/admin/usuarios/{alvo}/ativo", {"ativo": "0"}),
        (f"/admin/usuarios/{alvo}/senha", {}),
    ]
    for caminho, dados in posts:
        assert client.post(caminho, data=dados, headers=referer).status_code == 400
    assert _linha(app, alvo) == antes
    assert _por_email(app, "x@exemplo.com") is None

    # Com o token da página de administração, a mesma ação passa.
    lista = client.get("/admin/usuarios").get_data(as_text=True)
    assert 'action="/admin/usuarios"' in lista
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', lista).group(1)
    resposta = client.post(
        f"/admin/usuarios/{alvo}/ativo",
        data={"ativo": "0", "csrf_token": token},
        headers=referer,
    )
    assert resposta.status_code == 302
    assert _linha(app, alvo)["ativo"] == 0


# --- scripts/criar_admin.py --------------------------------------------------


@pytest.fixture
def dados_do_script(app, monkeypatch):
    """Aponta o script para o banco do app de teste."""
    monkeypatch.setenv("PEDIDOS_DATA_DIR", str(app.config["DATA_DIR"]))
    return _carregar_script()


def test_criar_admin_cria_administrador_que_consegue_entrar(app, client, entrar, dados_do_script):
    usuario_id = dados_do_script.criar_admin("Chefe", "Chefe@Exemplo.com", "senha-longa-do-chefe")

    linha = _linha(app, usuario_id)
    assert linha["perfil"] == "admin"
    assert linha["trocar_senha"] == 0
    assert linha["email"] == "chefe@exemplo.com"
    resposta = entrar(client, "chefe@exemplo.com", "senha-longa-do-chefe")
    assert resposta.status_code == 302
    assert client.get("/admin/usuarios").status_code == 200


def test_criar_admin_recusa_email_duplicado(app, dados_do_script):
    dados_do_script.criar_admin("Chefe", "chefe@exemplo.com", "senha-longa-do-chefe")
    with pytest.raises(auth.ErroUsuario, match="Já existe"):
        dados_do_script.criar_admin("Outro", "CHEFE@exemplo.com", "outra-senha-longa-1")
    assert _total_usuarios(app) == 1


def test_criar_admin_recusa_senha_curta(app, dados_do_script):
    with pytest.raises(auth.ErroUsuario, match="10 caracteres"):
        dados_do_script.criar_admin("Chefe", "chefe@exemplo.com", "curta")
    assert _total_usuarios(app) == 0


@pytest.mark.parametrize("argumentos", [[], ["--redefinir"]], ids=["criar", "redefinir"])
@pytest.mark.parametrize("valor", [None, "", "   "])
def test_script_recusa_sem_pedidos_data_dir(app, monkeypatch, capsys, tmp_path, argumentos, valor):
    modulo = _carregar_script()
    monkeypatch.delenv("PEDIDOS_DATA_DIR", raising=False)
    if valor is not None:
        monkeypatch.setenv("PEDIDOS_DATA_DIR", valor)
    projeto = tmp_path / "projeto"
    projeto.mkdir()
    monkeypatch.setitem(config_mod.PADROES, "DATA_DIR", projeto / "data")

    def nao_pode(*_args, **_kwargs):
        raise AssertionError("não devia perguntar nem criar nada antes de recusar")

    monkeypatch.setattr("builtins.input", nao_pode)
    monkeypatch.setattr(modulo, "create_app", nao_pode)
    with pytest.raises(SystemExit) as saida:
        modulo.main(argumentos)
    assert saida.value.code == 1
    erro = capsys.readouterr().err
    assert "PEDIDOS_DATA_DIR" in erro and "Traceback" not in erro
    assert list(projeto.iterdir()) == []


@pytest.mark.parametrize("argumentos", [[], ["--redefinir"]], ids=["criar", "redefinir"])
def test_script_mostra_a_pasta_de_dados_antes_de_perguntar(app, dados_do_script, monkeypatch, capsys, argumentos):
    def parar(_prompt=""):
        print("<pergunta>")
        raise EOFError

    monkeypatch.setattr("builtins.input", parar)
    with pytest.raises(EOFError):
        dados_do_script.main(argumentos)
    saida = capsys.readouterr().out
    assert f"Pasta de dados: {app.config['DATA_DIR']}" in saida
    assert saida.index("Pasta de dados") < saida.index("<pergunta>")


def _simular_terminal(monkeypatch, modulo, respostas, senhas):
    entradas = iter(respostas)
    segredos = iter(senhas)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(entradas))
    monkeypatch.setattr(modulo.getpass, "getpass", lambda _prompt="": next(segredos))


def test_script_pergunta_os_dados_e_cria_o_admin(app, dados_do_script, monkeypatch, capsys):
    _simular_terminal(
        monkeypatch,
        dados_do_script,
        ["Chefe", "chefe@exemplo.com"],
        ["senha-longa-do-chefe", "senha-longa-do-chefe"],
    )
    dados_do_script.main()
    saida = capsys.readouterr().out
    assert "senha-longa-do-chefe" not in saida
    assert _por_email(app, "chefe@exemplo.com")["perfil"] == "admin"


def test_script_sai_com_erro_se_a_confirmacao_nao_confere(
    app, dados_do_script, monkeypatch, capsys
):
    _simular_terminal(
        monkeypatch,
        dados_do_script,
        ["Chefe", "chefe@exemplo.com"],
        ["senha-longa-do-chefe", "senha-diferente-123"],
    )
    with pytest.raises(SystemExit) as saida:
        dados_do_script.main()
    assert saida.value.code == 1
    assert "não conferem" in capsys.readouterr().err
    assert _total_usuarios(app) == 0


def test_script_sai_com_erro_em_email_duplicado(app, dados_do_script, monkeypatch, capsys):
    dados_do_script.criar_admin("Chefe", "chefe@exemplo.com", "senha-longa-do-chefe")
    _simular_terminal(
        monkeypatch,
        dados_do_script,
        ["Outro", "chefe@exemplo.com"],
        ["outra-senha-longa-1", "outra-senha-longa-1"],
    )
    with pytest.raises(SystemExit) as saida:
        dados_do_script.main()
    assert saida.value.code == 1
    assert "Já existe um usuário" in capsys.readouterr().err
    assert _total_usuarios(app) == 1


def test_script_sai_com_erro_em_senha_curta(app, dados_do_script, monkeypatch, capsys):
    _simular_terminal(monkeypatch, dados_do_script, ["Chefe", "chefe@exemplo.com"], ["curta", "curta"])
    with pytest.raises(SystemExit) as saida:
        dados_do_script.main()
    assert saida.value.code == 1
    assert "10 caracteres" in capsys.readouterr().err
