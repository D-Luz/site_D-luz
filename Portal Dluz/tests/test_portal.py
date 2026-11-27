"""Testes do portal: login único, permissões, sistemas montados e telas."""

import re
import sqlite3


def test_anonimo_vai_para_a_entrada(anonimo):
    r = anonimo.get("/")
    assert r.status_code == 302 and r.headers["Location"].startswith("/entrar")
    for url in ("/pedidos/", "/crm/", "/caixa/", "/ferramentas/titulos"):
        r = anonimo.get(url)
        assert r.status_code == 302 and "/entrar" in r.headers["Location"], url


def test_senha_errada_nao_entra(app):
    from conftest import Navegador

    n = Navegador(app)
    tok = re.search(r'name="csrf" value="([^"]+)"', n.get("/entrar").get_data(as_text=True)).group(1)
    r = n.c.post("/entrar", data={"csrf": tok, "login": "admin", "senha": "errada"})
    assert "incorretos" in r.get_data(as_text=True)
    assert n.get("/").status_code == 302


def test_post_sem_csrf_recusado(admin):
    r = admin.c.post("/usuarios", data={"acao": "criar", "login": "x", "nome": "X"})
    assert r.status_code == 400


def test_primeiro_acesso_exige_troca_de_senha(app):
    from conftest import Navegador

    n = Navegador(app, "novato")
    r = n.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/senha")
    r = n.get("/crm/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/senha")


def test_telas_do_portal(admin):
    for url in ("/", "/ferramentas", "/area/vendas", "/area/financeiro", "/area/estoque", "/area/bancos",
                "/paineis", "/tarefas", "/usuarios", "/configuracoes", "/senha"):
        assert admin.get(url).status_code == 200, url


def test_todas_as_ferramentas_abrem(admin):
    from portal.ferramentas import REGISTRO

    assert len(REGISTRO) == 21
    for fid in REGISTRO:
        r = admin.get(f"/ferramentas/{fid}")
        assert r.status_code == 200, fid


def test_usuario_comum_sem_administracao(marcos):
    assert marcos.get("/usuarios").status_code == 403
    assert marcos.get("/configuracoes").status_code == 403
    assert marcos.get("/ferramentas/titulos").status_code == 200


def test_login_unico_nos_tres_sistemas(admin, app):
    for url in ("/pedidos/", "/crm/", "/caixa/historico"):
        r = admin.get(url)
        assert r.status_code == 200, url
        assert b"dluz-portal-barra" in r.data
    pasta = app.config["DATA_DIR"]
    assert sqlite3.connect(pasta / "crm" / "crm.sqlite").execute(
        "SELECT admin FROM usuario WHERE login = 'admin'").fetchone() == (1,)
    assert sqlite3.connect(pasta / "caixa" / "caixa.sqlite").execute(
        "SELECT papel FROM usuario WHERE login = 'admin'").fetchone() == ("admin",)
    assert sqlite3.connect(pasta / "pedidos" / "pedidos.db").execute(
        "SELECT perfil FROM usuarios WHERE email = 'ana@dluz.test'").fetchone() == ("admin",)


def test_perfil_basico_nos_sistemas(marcos, app):
    assert marcos.get("/crm/").status_code == 200
    assert marcos.get("/crm/config").status_code == 403
    assert marcos.get("/caixa/usuarios").status_code == 403
    assert marcos.get("/pedidos/").status_code == 200
    pasta = app.config["DATA_DIR"]
    assert sqlite3.connect(pasta / "pedidos" / "pedidos.db").execute(
        "SELECT perfil FROM usuarios WHERE email = 'marcos@portal.local'").fetchone() == ("solicitante",)


def test_cabecalho_falso_nao_entra_no_sistema(anonimo):
    r = anonimo.get("/crm/", headers={"dluz.portal.usuario": "admin", "X-Dluz-Portal-Usuario": "admin"})
    assert r.status_code == 302 and "/entrar" in r.headers["Location"]


def test_login_e_senha_dos_sistemas_vao_para_o_portal(admin):
    assert admin.get("/crm/login").headers["Location"] == "/entrar"
    assert admin.get("/caixa/senha").headers["Location"] == "/senha"
    assert admin.get("/pedidos/trocar-senha").headers["Location"] == "/senha"


def test_sair_do_portal_sai_dos_sistemas(app):
    from conftest import Navegador

    n = Navegador(app, "marcos")
    assert n.get("/crm/").status_code == 200
    n.get("/sair")
    assert n.get("/crm/").status_code == 302


def test_desativar_usuario_derruba_a_sessao(app):
    from conftest import Navegador
    from portal import auth, db

    with app.app_context():
        auth.criar_usuario(db.get_db(), "temp", "Temp", "senha12345", trocar_senha=False)
        uid = db.get_db().execute("SELECT id FROM usuario WHERE login = 'temp'").fetchone()[0]
    n = Navegador(app, "temp")
    assert n.get("/").status_code == 200
    a = Navegador(app, "admin")
    a.post("/usuarios", {"acao": "ativo", "id": uid})
    assert n.get("/").status_code == 302


def test_criar_usuario_gera_senha_provisoria(admin):
    r = admin.post("/usuarios", {"acao": "criar", "login": "Joana", "nome": "Joana Caixa", "email": ""})
    assert "Senha provisória de <b>joana</b>" in r.get_data(as_text=True)


def test_configuracoes_mudam_os_bancos(admin, app):
    from portal import erp

    dados = {"csrf": admin.csrf}
    for sigla in ("ELE", "COR", "ILU"):
        dados.update({f"{sigla}_host": "", f"{sigla}_real": "", f"{sigla}_fiscal": ""})
    dados["ELE_host"] = "10.0.0.9"
    admin.c.post("/configuracoes", data=dados)
    with app.app_context():
        from portal import db

        emp = erp.empresas(db.get_db())
    assert emp["ELE"]["host"] == "10.0.0.9" and emp["ILU"]["host"] == "192.168.0.250"
    import sys

    assert sys.modules["dluz_sis_crm.config"].EMPRESAS["ELE"]["host"] == "10.0.0.9"
    dados["ELE_host"] = ""
    admin.c.post("/configuracoes", data=dados)


def test_area_inexistente(admin):
    assert admin.get("/area/nada").status_code == 404
    assert admin.get("/area/paineis").headers["Location"].endswith("/paineis")


def test_ferramentas_retiradas(admin):
    for fid in ("historico-nf-compra", "painel-receber-pagar", "pre-venda-matricial"):
        assert admin.get(f"/ferramentas/{fid}").status_code == 404


def _senha_da_pagina(html, padrao):
    m = re.search(padrao, html)
    assert m, html[:3000]
    return m.group(1)


def test_usuario_criado_no_pedidos_entra_no_portal(admin, app):
    from conftest import Navegador

    pagina = admin.get("/pedidos/admin/usuarios").get_data(as_text=True)
    tok = re.search(r'name="csrf_token" value="([^"]+)"', pagina).group(1)
    r = admin.c.post("/pedidos/admin/usuarios", data={
        "csrf_token": tok, "nome": "Pedro Pedidos", "email": "Pedro@Dluz.test", "perfil": "solicitante"})
    html = r.get_data(as_text=True)
    assert "Para entrar no Portal Dluz" in html
    senha = _senha_da_pagina(html, r'<code id="senha-provisoria">([^<]+)</code>')
    n = Navegador(app)
    tok = re.search(r'name="csrf" value="([^"]+)"', n.get("/entrar").get_data(as_text=True)).group(1)
    r = n.c.post("/entrar", data={"csrf": tok, "login": "pedro@dluz.test", "senha": senha})
    assert r.status_code == 302 and r.headers["Location"] == "/"
    assert n.get("/").headers["Location"].endswith("/senha")  # senha provisória: troca obrigatória


def test_usuario_criado_no_caixa_entra_no_portal(admin, app):
    from conftest import Navegador

    pagina = admin.get("/caixa/usuarios").get_data(as_text=True)
    tok = re.search(r'name="csrf" value="([^"]+)"', pagina).group(1)
    r = admin.c.post("/caixa/usuarios", data={
        "csrf": tok, "acao": "criar", "login": "carla", "nome": "Carla Caixa", "papel": "caixa", "empresa": ""})
    html = r.get_data(as_text=True)
    assert "também entra no Portal Dluz" in html
    senha = _senha_da_pagina(html, r'<code class="num">([^<]+)</code>')
    n = Navegador(app)
    tok = re.search(r'name="csrf" value="([^"]+)"', n.get("/entrar").get_data(as_text=True)).group(1)
    assert n.c.post("/entrar", data={"csrf": tok, "login": "carla", "senha": senha}).headers["Location"] == "/"


def test_excluir_usuario(admin, app):
    from conftest import Navegador
    from portal import auth, db

    with app.app_context():
        auth.criar_usuario(db.get_db(), "apagar", "Apagar Já", "senha12345", trocar_senha=False)
        uid = db.get_db().execute("SELECT id FROM usuario WHERE login = 'apagar'").fetchone()[0]
    n = Navegador(app, "apagar")
    assert n.get("/crm/").status_code == 200  # cria o cadastro no CRM
    pasta = app.config["DATA_DIR"]
    assert sqlite3.connect(pasta / "crm" / "crm.sqlite").execute(
        "SELECT COUNT(*) FROM usuario WHERE login = 'apagar'").fetchone() == (1,)
    assert "Excluir Apagar Já?" in admin.get(f"/usuarios/{uid}/excluir").get_data(as_text=True)
    admin.post(f"/usuarios/{uid}/excluir")
    with app.app_context():
        assert db.get_db().execute("SELECT 1 FROM usuario WHERE id = ?", (uid,)).fetchone() is None
    assert sqlite3.connect(pasta / "crm" / "crm.sqlite").execute(
        "SELECT COUNT(*) FROM usuario WHERE login = 'apagar'").fetchone() == (0,)
    assert n.get("/").status_code == 302


def test_nao_exclui_a_propria_conta(admin, app):
    from portal import db

    with app.app_context():
        uid = db.get_db().execute("SELECT id FROM usuario WHERE login = 'admin'").fetchone()[0]
    admin.post(f"/usuarios/{uid}/excluir")
    assert admin.get("/").status_code == 200
