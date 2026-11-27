import base64
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import pytest

from app import auth, create_app
from app import db as db_module
from conftest import SENHA_TESTE, ClienteHttps

MENSAGEM_GENERICA = "E-mail ou senha incorretos."


def _registrar_rotas_de_teste(app):
    """Rotas descartáveis: as de verdade só chegam em tarefas posteriores."""

    @app.route("/_protegida")
    @auth.login_required
    def protegida():
        return "ok"

    @app.route("/_so_gestor")
    @auth.perfil_required("gestor")
    def so_gestor():
        return "ok"


@pytest.fixture
def app(app):
    _registrar_rotas_de_teste(app)
    return app


def _local(resposta):
    return urlsplit(resposta.headers["Location"]).path


def _usuario_no_banco(app, usuario_id):
    with app.app_context():
        return dict(
            db_module.get_db()
            .execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,))
            .fetchone()
        )


def _atualizar(app, usuario_id, **campos):
    colunas = ", ".join(f"{c} = ?" for c in campos)
    with app.app_context():
        db_module.get_db().execute(
            f"UPDATE usuarios SET {colunas} WHERE id = ?", (*campos.values(), usuario_id)
        )


AGORA = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


# --- criar_usuario -----------------------------------------------------------


def test_senha_curta_e_recusada(db):
    with pytest.raises(auth.ErroUsuario, match="10 caracteres"):
        auth.criar_usuario("Ana", "ana@exemplo.com", "123456789", "gestor")
    assert db.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 0


def test_senha_de_dez_caracteres_e_aceita_e_guardada_com_hash(db):
    usuario_id = auth.criar_usuario("Ana", "ana@exemplo.com", "1234567890", "gestor")
    linha = db.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
    assert "1234567890" not in linha["senha_hash"]
    assert linha["trocar_senha"] == 1  # padrão: senha provisória
    assert linha["ativo"] == 1


def test_criar_usuario_normaliza_email(db):
    usuario_id = auth.criar_usuario("Ana", "  Ana@Exemplo.COM ", SENHA_TESTE, "gestor")
    linha = db.execute("SELECT email FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
    assert linha["email"] == "ana@exemplo.com"


@pytest.mark.parametrize(
    "nome, email, perfil",
    [
        ("", "ana@exemplo.com", "gestor"),
        ("   ", "ana@exemplo.com", "gestor"),
        ("Ana", "", "gestor"),
        ("Ana", "ana.exemplo.com", "gestor"),
        ("Ana", "@exemplo.com", "gestor"),
        ("Ana", "ana@", "gestor"),
        ("Ana", "a@b@c", "gestor"),
        ("Ana", "ana@exemplo.com", "chefe"),
        ("Ana", "ana@exemplo.com", ""),
    ],
)
def test_criar_usuario_recusa_dados_invalidos(db, nome, email, perfil):
    with pytest.raises(auth.ErroUsuario):
        auth.criar_usuario(nome, email, SENHA_TESTE, perfil)
    assert db.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 0


def test_criar_usuario_recusa_email_repetido_mesmo_com_caixa_diferente(db):
    auth.criar_usuario("Ana", "ana@exemplo.com", SENHA_TESTE, "gestor")
    with pytest.raises(auth.ErroUsuario, match="Já existe"):
        auth.criar_usuario("Outra Ana", "ANA@exemplo.com", SENHA_TESTE, "fiscal")
    assert db.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 1


def test_erro_usuario_e_um_value_error():
    assert issubclass(auth.ErroUsuario, ValueError)


# --- tentar_login ------------------------------------------------------------


def test_tentar_login_correto_devolve_o_usuario(db, criar_usuario_teste):
    ana = criar_usuario_teste()
    linha = auth.tentar_login(ana.email.upper(), ana.senha)
    assert linha["id"] == ana.id


def test_tentar_login_com_senha_errada_ou_email_inexistente_devolve_none(db, criar_usuario_teste):
    ana = criar_usuario_teste()
    assert auth.tentar_login(ana.email, "senha-errada-123") is None
    assert auth.tentar_login("ninguem@exemplo.com", ana.senha) is None


def test_email_inexistente_ainda_calcula_um_hash_para_igualar_o_tempo(
    db, criar_usuario_teste, monkeypatch
):
    chamadas = []
    original = auth.check_password_hash
    monkeypatch.setattr(
        auth, "check_password_hash", lambda h, s: chamadas.append(h) or original(h, s)
    )
    assert auth.tentar_login("ninguem@exemplo.com", "qualquer-coisa-123") is None
    assert len(chamadas) == 1


def test_usuario_inativo_nao_entra_nem_com_a_senha_certa(app, db, criar_usuario_teste):
    ana = criar_usuario_teste()
    _atualizar(app, ana.id, ativo=0)
    assert auth.tentar_login(ana.email, ana.senha) is None


def test_falha_incrementa_o_contador_e_sucesso_zera(app, db, criar_usuario_teste):
    ana = criar_usuario_teste()
    for _ in range(3):
        assert auth.tentar_login(ana.email, "errada-errada-1", AGORA) is None
    assert _usuario_no_banco(app, ana.id)["falhas_login"] == 3
    assert auth.tentar_login(ana.email, ana.senha, AGORA) is not None
    linha = _usuario_no_banco(app, ana.id)
    assert linha["falhas_login"] == 0
    assert linha["bloqueado_ate"] is None


def test_bloqueio_apos_cinco_falhas(app, db, criar_usuario_teste):
    ana = criar_usuario_teste()
    for _ in range(4):
        auth.tentar_login(ana.email, "errada-errada-1", AGORA)
    assert _usuario_no_banco(app, ana.id)["bloqueado_ate"] is None  # 4 ainda não bloqueia

    auth.tentar_login(ana.email, "errada-errada-1", AGORA)  # 5ª
    assert _usuario_no_banco(app, ana.id)["bloqueado_ate"] == (
        AGORA + timedelta(minutes=15)
    ).isoformat()

    # 6ª tentativa, com a senha CERTA: continua recusada.
    assert auth.tentar_login(ana.email, ana.senha, AGORA + timedelta(minutes=1)) is None
    assert auth.tentar_login(ana.email, ana.senha, AGORA + timedelta(minutes=14, seconds=59)) is None

    # Passados os 15 minutos, volta a funcionar.
    assert auth.tentar_login(ana.email, ana.senha, AGORA + timedelta(minutes=15)) is not None
    linha = _usuario_no_banco(app, ana.id)
    assert linha["falhas_login"] == 0
    assert linha["bloqueado_ate"] is None


def test_tentativas_durante_o_bloqueio_nao_mudam_nem_prorrogam_nada(app, db, criar_usuario_teste):
    ana = criar_usuario_teste()
    for _ in range(5):
        auth.tentar_login(ana.email, "errada-errada-1", AGORA)
    antes = _usuario_no_banco(app, ana.id)
    auth.tentar_login(ana.email, "errada-errada-1", AGORA + timedelta(minutes=5))
    auth.tentar_login(ana.email, ana.senha, AGORA + timedelta(minutes=6))
    depois = _usuario_no_banco(app, ana.id)
    assert (depois["falhas_login"], depois["bloqueado_ate"]) == (
        antes["falhas_login"],
        antes["bloqueado_ate"],
    )


def test_bloqueio_expirado_zera_os_contadores_na_proxima_tentativa(app, db, criar_usuario_teste):
    ana = criar_usuario_teste()
    for _ in range(5):
        auth.tentar_login(ana.email, "errada-errada-1", AGORA)
    depois = AGORA + timedelta(minutes=16)
    assert auth.tentar_login(ana.email, "errada-errada-1", depois) is None
    linha = _usuario_no_banco(app, ana.id)
    assert linha["falhas_login"] == 1  # recomeçou do zero, não do 5
    assert linha["bloqueado_ate"] is None


# --- tentar_login e o bloqueio de escrita do banco ---------------------------
#
# O scrypt leva dezenas de milissegundos. Se rodar dentro de BEGIN IMMEDIATE,
# uma enxurrada de logins (até com e-mails inexistentes) segura o bloqueio de
# escrita do banco inteiro e derruba as demais gravações com "database is locked".

ESPERA = 10  # segundos; só estoura se algo travar de verdade


def _caminho_banco(app):
    return app.config["DATA_DIR"] / "pedidos.db"


def _em_outra_thread(app, funcao, *args):
    """Roda funcao(*args) num contexto de aplicação próprio; devolve (thread, resultados)."""
    resultados = []

    def alvo():
        with app.app_context():
            resultados.append(funcao(*args))

    fio = threading.Thread(target=alvo, daemon=True)
    fio.start()
    return fio, resultados


@pytest.mark.parametrize("caso", ["senha errada", "senha certa", "email inexistente"])
def test_conferencia_da_senha_nao_segura_o_bloqueio_de_escrita(
    app, criar_usuario_teste, monkeypatch, caso
):
    ana = criar_usuario_teste()
    email, senha = {
        "senha errada": (ana.email, "errada-errada-1"),
        "senha certa": (ana.email, ana.senha),
        "email inexistente": ("ninguem@exemplo.com", "qualquer-coisa-123"),
    }[caso]
    dentro, liberar = threading.Event(), threading.Event()
    original = auth.check_password_hash

    def lento(senha_hash, texto):
        dentro.set()
        assert liberar.wait(ESPERA)
        return original(senha_hash, texto)

    monkeypatch.setattr(auth, "check_password_hash", lento)
    fio, resultados = _em_outra_thread(app, auth.tentar_login, email, senha, AGORA)
    try:
        assert dentro.wait(ESPERA)
        # Enquanto o login confere o hash, outra conexão grava sem esperar nada.
        outra = sqlite3.connect(_caminho_banco(app), timeout=0, isolation_level=None)
        try:
            outra.execute("BEGIN IMMEDIATE")
            outra.execute("UPDATE usuarios SET nome = nome WHERE id = ?", (ana.id,))
            outra.execute("COMMIT")
        finally:
            outra.close()
    finally:
        liberar.set()
        fio.join(ESPERA)
    assert not fio.is_alive()
    assert len(resultados) == 1
    assert (resultados[0] is not None) == (caso == "senha certa")


def test_enxurrada_de_logins_anonimos_nao_derruba_as_escritas(app, criar_usuario_teste):
    ana = criar_usuario_teste()
    parar = threading.Event()
    tentativas, erros = [], []

    def inundar():
        with app.app_context():
            while not parar.is_set():
                try:
                    tentativas.append(auth.tentar_login("ninguem@exemplo.com", "qualquer-coisa-123"))
                except Exception as erro:  # registra para o assert do teste principal
                    erros.append(erro)
                    return

    fios = [threading.Thread(target=inundar, daemon=True) for _ in range(4)]
    for fio in fios:
        fio.start()
    try:
        limite = time.monotonic() + ESPERA
        while len(tentativas) < 8 and not erros and time.monotonic() < limite:
            time.sleep(0.01)
        escritor = sqlite3.connect(_caminho_banco(app), timeout=1, isolation_level=None)
        try:
            for n in range(8):
                escritor.execute("BEGIN IMMEDIATE")
                escritor.execute("UPDATE usuarios SET nome = ? WHERE id = ?", (f"Ana {n}", ana.id))
                escritor.execute("COMMIT")
        finally:
            escritor.close()
    finally:
        parar.set()
        for fio in fios:
            fio.join(ESPERA)
    assert erros == []
    assert len(tentativas) >= 8
    assert set(tentativas) == {None}
    assert _usuario_no_banco(app, ana.id)["nome"] == "Ana 7"


@pytest.mark.parametrize("quantas", [4, 6])
def test_senhas_erradas_simultaneas_contam_todas(app, criar_usuario_teste, monkeypatch, quantas):
    ana = criar_usuario_teste()
    barreira = threading.Barrier(quantas)
    original = auth.check_password_hash

    def junto(senha_hash, texto):
        # Todas leram a linha (falhas_login = 0) antes de qualquer uma gravar.
        barreira.wait(ESPERA)
        return original(senha_hash, texto)

    monkeypatch.setattr(auth, "check_password_hash", junto)
    fios = [
        _em_outra_thread(app, auth.tentar_login, ana.email, "errada-errada-1", AGORA)
        for _ in range(quantas)
    ]
    for fio, _ in fios:
        fio.join(ESPERA)
    assert [r for _, resultados in fios for r in resultados] == [None] * quantas
    linha = _usuario_no_banco(app, ana.id)
    # Nenhum incremento se perde; a partir da 5ª a conta trava e o resto não conta.
    assert linha["falhas_login"] == min(quantas, 5)
    esperado = (AGORA + timedelta(minutes=15)).isoformat() if quantas >= 5 else None
    assert linha["bloqueado_ate"] == esperado


@pytest.mark.parametrize("certa", [True, False], ids=["senha certa", "senha errada"])
@pytest.mark.parametrize("mudanca", ["bloqueio", "nova senha", "desativada"])
def test_conta_que_mudou_durante_a_conferencia_nao_entra_nem_conta_falha(
    app, criar_usuario_teste, monkeypatch, mudanca, certa
):
    ana = criar_usuario_teste()
    senha = ana.senha if certa else "errada-de-proposito-1"
    dentro, liberar = threading.Event(), threading.Event()
    original = auth.check_password_hash

    def lento(senha_hash, texto):
        if texto == senha:
            dentro.set()
            assert liberar.wait(ESPERA)
        return original(senha_hash, texto)

    monkeypatch.setattr(auth, "check_password_hash", lento)
    fio, resultados = _em_outra_thread(app, auth.tentar_login, ana.email, senha, AGORA)
    try:
        assert dentro.wait(ESPERA)
        if mudanca == "bloqueio":
            with app.app_context():
                for _ in range(5):
                    assert auth.tentar_login(ana.email, "errada-errada-1", AGORA) is None
        elif mudanca == "nova senha":
            _atualizar(app, ana.id, senha_hash=auth.generate_password_hash("outra-senha-qualquer-1"))
        else:
            _atualizar(app, ana.id, ativo=0)
        antes = _usuario_no_banco(app, ana.id)
    finally:
        liberar.set()
        fio.join(ESPERA)
    assert resultados == [None]
    depois = _usuario_no_banco(app, ana.id)
    assert (depois["falhas_login"], depois["bloqueado_ate"]) == (
        antes["falhas_login"],
        antes["bloqueado_ate"],
    )


def test_login_limpo_nao_grava_nada(db, criar_usuario_teste, monkeypatch):
    ana = criar_usuario_teste()

    def proibida():
        raise AssertionError("login sem falhas anteriores não deve abrir transação")

    monkeypatch.setattr(auth.db, "transacao", proibida)
    antes = db.total_changes
    linha = auth.tentar_login(ana.email, ana.senha, AGORA)
    assert linha["id"] == ana.id
    assert db.total_changes == antes


def test_login_com_falhas_anteriores_zera_o_contador_numa_gravacao(app, db, criar_usuario_teste):
    ana = criar_usuario_teste()
    auth.tentar_login(ana.email, "errada-errada-1", AGORA)
    antes = db.total_changes
    linha = auth.tentar_login(ana.email, ana.senha, AGORA)
    assert (linha["falhas_login"], linha["bloqueado_ate"]) == (0, None)
    assert db.total_changes == antes + 1


# --- login pela web ----------------------------------------------------------


def test_login_correto_abre_sessao(client, entrar, criar_usuario_teste):
    ana = criar_usuario_teste()
    resposta = entrar(client, ana.email, ana.senha)
    assert resposta.status_code == 302
    assert _local(resposta) == "/"
    assert client.get("/_protegida").status_code == 200
    with client.session_transaction() as sessao:
        assert sessao["usuario_id"] == ana.id
        assert sessao.permanent is True


def test_login_nao_aceita_parametro_next(client, entrar, criar_usuario_teste):
    ana = criar_usuario_teste()
    resposta = client.post(
        "/login?next=https://evil.example/",
        data={"email": ana.email, "senha": ana.senha, "next": "https://evil.example/"},
    )
    assert _local(resposta) == "/"
    assert urlsplit(resposta.headers["Location"]).netloc == ""


def test_senha_errada_mostra_mensagem_generica(client, entrar, criar_usuario_teste):
    ana = criar_usuario_teste()
    errada = entrar(client, ana.email, "senha-errada-123")
    inexistente = entrar(client, "ninguem@exemplo.com", "senha-errada-123")
    assert errada.status_code == inexistente.status_code == 200
    assert MENSAGEM_GENERICA in errada.get_data(as_text=True)
    assert MENSAGEM_GENERICA in inexistente.get_data(as_text=True)
    with client.session_transaction() as sessao:
        assert "usuario_id" not in sessao


def _sem_valores(resposta):
    """HTML da resposta sem os value="..." (o e-mail digitado volta no campo)."""
    return re.sub(r'value="[^"]*"', 'value=""', resposta.get_data(as_text=True))


def test_resposta_de_login_nao_revela_inativo_nem_bloqueio(
    app, client, entrar, criar_usuario_teste
):
    ana = criar_usuario_teste()
    inativo = criar_usuario_teste()
    _atualizar(app, inativo.id, ativo=0)

    inexistente = entrar(client, "ninguem@exemplo.com", ana.senha)
    for _ in range(5):
        entrar(client, ana.email, "senha-errada-123")
    bloqueado = entrar(client, ana.email, ana.senha)  # senha certa, conta bloqueada
    desativado = entrar(client, inativo.email, inativo.senha)

    for resposta in (bloqueado, desativado):
        assert resposta.status_code == inexistente.status_code == 200
        assert MENSAGEM_GENERICA in resposta.get_data(as_text=True)
        assert _sem_valores(resposta) == _sem_valores(inexistente)


def test_login_nao_reaproveita_a_sessao_anterior(client, entrar, criar_usuario_teste):
    ana = criar_usuario_teste()
    with client.session_transaction() as sessao:
        sessao["plantado"] = "pelo-atacante"
    cookie_antes = client.get_cookie("session").value
    entrar(client, ana.email, ana.senha)
    with client.session_transaction() as sessao:
        assert "plantado" not in sessao
        assert sessao["usuario_id"] == ana.id
    assert client.get_cookie("session").value != cookie_antes


def test_cookie_de_sessao_tem_flags(client, entrar, criar_usuario_teste):
    ana = criar_usuario_teste()
    resposta = entrar(client, ana.email, ana.senha)
    cookies = resposta.headers.getlist("Set-Cookie")
    cookie = next(c for c in cookies if c.split("=", 1)[0].strip() == "session")
    atributos = {parte.strip().lower() for parte in cookie.split(";")}
    assert "httponly" in atributos
    assert "secure" in atributos
    assert "samesite=lax" in atributos
    expira = parsedate_to_datetime(
        next(p.strip()[len("expires="):] for p in cookie.split(";") if p.strip().lower().startswith("expires="))
    )
    restante = expira - datetime.now(timezone.utc)
    assert timedelta(hours=7, minutes=55) < restante <= timedelta(hours=8)


def test_cabecalhos_de_seguranca_na_pagina_de_login(client):
    resposta = client.get("/login")
    assert resposta.status_code == 200
    assert "default-src 'self'" in resposta.headers["Content-Security-Policy"]
    assert resposta.headers["X-Frame-Options"] == "DENY"


def test_paginas_nao_usam_estilo_nem_script_inline(client, entrar, criar_usuario_teste):
    # A CSP (default-src 'self') bloquearia qualquer um dos dois.
    ana = criar_usuario_teste(trocar_senha=True)
    paginas = [client.get("/login").get_data(as_text=True)]
    entrar(client, ana.email, ana.senha)
    paginas.append(client.get("/trocar-senha").get_data(as_text=True))
    for html in paginas:
        assert "<style" not in html.lower()
        assert "<script" not in html.lower()
        assert not re.search(r"\sstyle\s*=", html, re.IGNORECASE)
        assert not re.search(r"\son\w+\s*=", html, re.IGNORECASE)
        assert 'name="viewport"' in html


# --- logout ------------------------------------------------------------------


def test_sair_exige_post(client, entrar, criar_usuario_teste):
    ana = criar_usuario_teste()
    entrar(client, ana.email, ana.senha)
    assert client.get("/sair").status_code == 405
    assert client.get("/_protegida").status_code == 200  # continua logado


def test_sair_encerra_a_sessao(client, entrar, criar_usuario_teste):
    ana = criar_usuario_teste()
    entrar(client, ana.email, ana.senha)
    resposta = client.post("/sair")
    assert resposta.status_code == 302
    assert _local(resposta) == "/login"
    with client.session_transaction() as sessao:
        assert "usuario_id" not in sessao
    assert _local(client.get("/_protegida")) == "/login"


def test_pagina_logada_tem_botao_de_sair_com_token_csrf(client, entrar, criar_usuario_teste):
    ana = criar_usuario_teste(trocar_senha=True)
    assert 'action="/sair"' not in client.get("/login").get_data(as_text=True)
    entrar(client, ana.email, ana.senha)
    html = client.get("/trocar-senha").get_data(as_text=True)
    assert 'action="/sair"' in html
    assert 'name="csrf_token"' in html


# --- proteção das rotas ------------------------------------------------------


def test_rotas_exigem_login(client):
    resposta = client.get("/_protegida")
    assert resposta.status_code == 302
    assert _local(resposta) == "/login"


def test_perfil_certo_passa_perfil_errado_leva_403_anonimo_vai_para_login(
    client, entrar, criar_usuario_teste
):
    assert _local(client.get("/_so_gestor")) == "/login"

    solicitante = criar_usuario_teste("solicitante")
    entrar(client, solicitante.email, solicitante.senha)
    assert client.get("/_so_gestor").status_code == 403
    client.post("/sair")

    gestor = criar_usuario_teste("gestor")
    entrar(client, gestor.email, gestor.senha)
    assert client.get("/_so_gestor").status_code == 200


def test_usuario_desativado_perde_acesso_na_proxima_requisicao(
    app, client, entrar, criar_usuario_teste
):
    ana = criar_usuario_teste()
    entrar(client, ana.email, ana.senha)
    assert client.get("/_protegida").status_code == 200

    _atualizar(app, ana.id, ativo=0)
    resposta = client.get("/_protegida")
    assert resposta.status_code == 302
    assert _local(resposta) == "/login"
    with client.session_transaction() as sessao:
        assert "usuario_id" not in sessao

    # Reativar não ressuscita a sessão antiga.
    _atualizar(app, ana.id, ativo=1)
    assert _local(client.get("/_protegida")) == "/login"


def test_usuario_apagado_do_banco_perde_acesso(app, client, entrar, criar_usuario_teste):
    ana = criar_usuario_teste()
    entrar(client, ana.email, ana.senha)
    with app.app_context():
        db_module.get_db().execute("DELETE FROM usuarios WHERE id = ?", (ana.id,))
    assert _local(client.get("/_protegida")) == "/login"


def test_perfil_alterado_vale_na_proxima_requisicao(app, client, entrar, criar_usuario_teste):
    ana = criar_usuario_teste("gestor")
    entrar(client, ana.email, ana.senha)
    assert client.get("/_so_gestor").status_code == 200
    _atualizar(app, ana.id, perfil="solicitante")
    assert client.get("/_so_gestor").status_code == 403


def test_usuario_atual_devolve_a_linha_do_banco(app, client, entrar, criar_usuario_teste):
    @app.route("/_quem")
    def quem():
        usuario = auth.usuario_atual()
        return usuario["email"] if usuario else "ninguem"

    ana = criar_usuario_teste()
    assert client.get("/_quem").get_data(as_text=True) == "ninguem"
    entrar(client, ana.email, ana.senha)
    assert client.get("/_quem").get_data(as_text=True) == ana.email


# --- primeiro acesso / troca de senha ----------------------------------------


def test_primeiro_acesso_obriga_trocar_senha(app, client, entrar, criar_usuario_teste):
    @app.route("/_outra")
    def outra():
        return "ok"

    ana = criar_usuario_teste(trocar_senha=True)
    resposta = entrar(client, ana.email, ana.senha)
    assert resposta.status_code == 302  # o login em si funciona

    for caminho in ("/_protegida", "/_so_gestor", "/_outra", "/"):
        resposta = client.get(caminho)
        assert resposta.status_code == 302, caminho
        assert _local(resposta) == "/trocar-senha", caminho

    assert client.get("/trocar-senha").status_code == 200  # a tela de troca abre
    assert client.post("/sair").status_code == 302  # e dá para sair


def _trocar(client, atual=SENHA_TESTE, nova="nova-senha-segura-1", confirmacao=None):
    return client.post(
        "/trocar-senha",
        data={
            "senha_atual": atual,
            "nova_senha": nova,
            "confirmacao": nova if confirmacao is None else confirmacao,
        },
    )


def test_trocar_senha_com_sucesso_libera_o_acesso(app, client, entrar, criar_usuario_teste):
    ana = criar_usuario_teste(trocar_senha=True)
    entrar(client, ana.email, ana.senha)

    resposta = _trocar(client)
    assert resposta.status_code == 302
    assert _local(resposta) == "/"

    assert _usuario_no_banco(app, ana.id)["trocar_senha"] == 0
    assert client.get("/_protegida").status_code == 200  # sem mais redirecionar

    client.post("/sair")
    assert entrar(client, ana.email, ana.senha).status_code == 200  # senha antiga não vale
    assert _local(entrar(client, ana.email, "nova-senha-segura-1")) == "/"


def test_trocar_senha_mantem_o_usuario_logado_com_sessao_permanente(
    client, entrar, criar_usuario_teste
):
    ana = criar_usuario_teste(trocar_senha=True)
    entrar(client, ana.email, ana.senha)
    _trocar(client)
    with client.session_transaction() as sessao:
        assert sessao["usuario_id"] == ana.id
        assert sessao.permanent is True


@pytest.mark.parametrize(
    "dados, trecho",
    [
        ({"atual": "senha-atual-errada"}, "atual"),
        ({"nova": "curta123"}, "10 caracteres"),
        ({"nova": "nova-senha-segura-1", "confirmacao": "outra-coisa-qualquer"}, "não conferem"),
        ({"nova": SENHA_TESTE}, "diferente"),
    ],
)
def test_trocar_senha_recusa_entradas_invalidas(
    app, client, entrar, criar_usuario_teste, dados, trecho
):
    ana = criar_usuario_teste(trocar_senha=True)
    entrar(client, ana.email, ana.senha)
    antes = _usuario_no_banco(app, ana.id)

    resposta = _trocar(client, **dados)

    assert resposta.status_code == 200
    assert trecho in resposta.get_data(as_text=True)
    depois = _usuario_no_banco(app, ana.id)
    assert depois["senha_hash"] == antes["senha_hash"]
    assert depois["trocar_senha"] == 1
    assert _local(client.get("/_protegida")) == "/trocar-senha"


def test_trocar_senha_exige_login(client):
    assert _local(client.get("/trocar-senha")) == "/login"
    assert _local(_trocar(client)) == "/login"


def test_senha_atual_errada_na_troca_conta_para_o_bloqueio(
    app, client, entrar, criar_usuario_teste
):
    # Uma sessão roubada não pode servir para adivinhar a senha sem limite.
    ana = criar_usuario_teste(trocar_senha=True)
    entrar(client, ana.email, ana.senha)
    for _ in range(5):
        _trocar(client, atual="senha-atual-errada")
    assert _usuario_no_banco(app, ana.id)["bloqueado_ate"] is not None

    antes = _usuario_no_banco(app, ana.id)["senha_hash"]
    resposta = _trocar(client)  # agora até a senha certa é recusada
    assert resposta.status_code == 200
    depois = _usuario_no_banco(app, ana.id)
    assert depois["senha_hash"] == antes
    assert depois["trocar_senha"] == 1


# --- prazo absoluto da sessão ------------------------------------------------


class Relogio:
    def __init__(self):
        self.agora = AGORA

    def avancar(self, **tempo):
        self.agora += timedelta(**tempo)


@pytest.fixture
def relogio(monkeypatch):
    relogio = Relogio()
    monkeypatch.setattr(auth, "_agora", lambda: relogio.agora)
    return relogio


def _logado(client):
    with client.session_transaction() as sessao:
        return "usuario_id" in sessao


def test_sessao_com_mais_de_oito_horas_e_recusada_e_limpa(
    client, entrar, criar_usuario_teste, relogio
):
    ana = criar_usuario_teste()
    entrar(client, ana.email, ana.senha)
    relogio.avancar(hours=8, seconds=1)
    resposta = client.get("/_protegida")
    assert resposta.status_code == 302
    assert _local(resposta) == "/login"
    assert not _logado(client)


def test_sessao_um_segundo_antes_das_oito_horas_ainda_vale(
    client, entrar, criar_usuario_teste, relogio
):
    ana = criar_usuario_teste()
    entrar(client, ana.email, ana.senha)
    relogio.avancar(hours=8, seconds=-1)
    assert client.get("/_protegida").status_code == 200


def test_atividade_nao_estende_o_prazo_absoluto(client, entrar, criar_usuario_teste, relogio):
    ana = criar_usuario_teste()
    entrar(client, ana.email, ana.senha)
    for _ in range(4):
        relogio.avancar(hours=1, minutes=50)  # 1h50, 3h40, 5h30, 7h20
        assert client.get("/_protegida").status_code == 200
    relogio.avancar(hours=1)  # 8h20 desde o login, mas só 1h após a última atividade
    assert _local(client.get("/_protegida")) == "/login"


def test_resposta_nao_renova_o_cookie_a_cada_requisicao(
    client, entrar, criar_usuario_teste, relogio
):
    ana = criar_usuario_teste()
    entrar(client, ana.email, ana.senha)
    resposta = client.get("/_protegida")
    cookies = resposta.headers.getlist("Set-Cookie")
    assert not [c for c in cookies if c.split("=", 1)[0].strip() == "session"]


def test_sessao_sem_data_de_login_e_recusada(client, criar_usuario_teste):
    # Formato antigo do cookie: só o usuario_id (sem login_em nem impressão).
    ana = criar_usuario_teste()
    with client.session_transaction() as sessao:
        sessao["usuario_id"] = ana.id
        sessao.permanent = True
    assert _local(client.get("/_protegida")) == "/login"
    assert not _logado(client)


@pytest.mark.parametrize("valor", ["ontem", None, True, 1.5e9, [1]])
def test_sessao_com_data_de_login_ilegivel_e_recusada(
    client, entrar, criar_usuario_teste, relogio, valor
):
    ana = criar_usuario_teste()
    entrar(client, ana.email, ana.senha)
    with client.session_transaction() as sessao:
        sessao["login_em"] = valor
    assert _local(client.get("/_protegida")) == "/login"
    assert not _logado(client)


# --- sessões e troca de senha ------------------------------------------------


def test_trocar_senha_derruba_as_outras_sessoes(app, client, entrar, criar_usuario_teste):
    ana = criar_usuario_teste(trocar_senha=True)
    outro = app.test_client()
    entrar(client, ana.email, ana.senha)
    entrar(outro, ana.email, ana.senha)

    assert _local(_trocar(client)) == "/"

    assert client.get("/_protegida").status_code == 200  # quem trocou continua
    resposta = outro.get("/_protegida")
    assert _local(resposta) == "/login"
    assert not _logado(outro)


def test_trocar_senha_nao_estende_o_prazo_de_oito_horas(
    client, entrar, criar_usuario_teste, relogio
):
    ana = criar_usuario_teste(trocar_senha=True)
    entrar(client, ana.email, ana.senha)
    relogio.avancar(hours=3)
    _trocar(client)
    with client.session_transaction() as sessao:
        assert sessao["login_em"] == int(AGORA.timestamp())
    relogio.avancar(hours=5, seconds=1)  # 8h e 1s desde o login original
    assert _local(client.get("/_protegida")) == "/login"


def test_redefinir_senha_no_banco_derruba_a_sessao(app, client, entrar, criar_usuario_teste):
    # É o que uma redefinição feita pelo administrador faz: reescrever senha_hash.
    ana = criar_usuario_teste()
    entrar(client, ana.email, ana.senha)
    _atualizar(app, ana.id, senha_hash=auth.generate_password_hash("outra-senha-qualquer-1"))
    assert _local(client.get("/_protegida")) == "/login"
    assert not _logado(client)


@pytest.mark.parametrize("impressao", ["0" * 64, "", None, 123])
def test_impressao_adulterada_ou_invalida_e_recusada(
    client, entrar, criar_usuario_teste, impressao
):
    ana = criar_usuario_teste()
    entrar(client, ana.email, ana.senha)
    with client.session_transaction() as sessao:
        sessao["impressao_senha"] = impressao
    assert _local(client.get("/_protegida")) == "/login"
    assert not _logado(client)


def test_sessao_sem_impressao_e_recusada(client, entrar, criar_usuario_teste):
    ana = criar_usuario_teste()
    entrar(client, ana.email, ana.senha)
    with client.session_transaction() as sessao:
        del sessao["impressao_senha"]
    assert _local(client.get("/_protegida")) == "/login"


def test_cookie_nao_expoe_o_hash_da_senha(app, client, entrar, criar_usuario_teste):
    ana = criar_usuario_teste()
    entrar(client, ana.email, ana.senha)
    senha_hash = _usuario_no_banco(app, ana.id)["senha_hash"]
    with client.session_transaction() as sessao:
        impressao = sessao["impressao_senha"]
    assert impressao != senha_hash
    assert senha_hash not in impressao
    assert re.fullmatch(r"[0-9a-f]{64}", impressao)
    carga = client.get_cookie("session").value.split(".")[0]
    carga = base64.urlsafe_b64decode(carga + "=" * (-len(carga) % 4)).decode()
    assert senha_hash not in carga
    assert "scrypt" not in carga


# --- CSRF --------------------------------------------------------------------


@pytest.fixture
def client_csrf(tmp_path):
    app = create_app(
        {"TESTING": True, "DATA_DIR": tmp_path / "data-csrf", "WTF_CSRF_ENABLED": True}
    )
    app.test_client_class = ClienteHttps
    _registrar_rotas_de_teste(app)
    return app, app.test_client()


def _token(client, caminho="/login"):
    html = client.get(caminho).get_data(as_text=True)
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)


def _post(client, caminho, dados=None, token=None, referer="https://localhost/"):
    """POST como o navegador faz: com o Referer da própria página (exigido em https)."""
    dados = dict(dados or {})
    if token:
        dados["csrf_token"] = token
    return client.post(caminho, data=dados, headers={"Referer": referer})


def test_post_sem_csrf_e_recusado(client_csrf):
    app, client = client_csrf
    with app.app_context():
        ana = auth.criar_usuario(
            "Ana", "ana@exemplo.com", SENHA_TESTE, "gestor", trocar_senha=False
        )
    credenciais = {"email": "ana@exemplo.com", "senha": SENHA_TESTE}

    # Login sem token: 400, e nada de sessão.
    assert _post(client, "/login", credenciais).status_code == 400
    with client.session_transaction() as sessao:
        assert "usuario_id" not in sessao

    # Token certo mas vindo de outro site (Referer): também 400.
    token = _token(client)
    resposta = _post(client, "/login", credenciais, token, referer="https://evil.example/")
    assert resposta.status_code == 400

    # Com token e Referer da própria página, entra.
    assert _post(client, "/login", credenciais, _token(client)).status_code == 302
    assert client.get("/_protegida").status_code == 200

    # Logout sem token é recusado e a sessão continua.
    assert _post(client, "/sair").status_code == 400
    assert client.get("/_protegida").status_code == 200

    # Troca de senha sem token também, e a senha não muda.
    troca = {
        "senha_atual": SENHA_TESTE,
        "nova_senha": "nova-senha-segura-1",
        "confirmacao": "nova-senha-segura-1",
    }
    assert _post(client, "/trocar-senha", troca).status_code == 400
    with app.app_context():
        linha = (
            db_module.get_db()
            .execute("SELECT senha_hash FROM usuarios WHERE id = ?", (ana,))
            .fetchone()
        )
    assert auth.check_password_hash(linha["senha_hash"], SENHA_TESTE)

    # Logout com o token da própria página funciona.
    token = _token(client, "/trocar-senha")
    assert _post(client, "/sair", token=token).status_code == 302
    assert _local(client.get("/_protegida")) == "/login"
