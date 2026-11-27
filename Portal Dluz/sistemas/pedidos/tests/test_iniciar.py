"""scripts/iniciar.py: servidor de produção (Waitress) atrás do Tailscale Funnel.

Os testes usam um Waitress de verdade em 127.0.0.1, porta 0 (o sistema escolhe),
numa thread, e o fecham no fim. O cabeçalho X-Forwarded-* é enviado à mão, como o
Funnel faz.
"""

import http.client
import importlib.util
import io
import logging
import logging.handlers
import re
import socket
import threading
import urllib.parse
from pathlib import Path

import pytest
from waitress import wasyncore
from werkzeug.datastructures import FileStorage

from app import auth
from app import config as config_mod
from app import create_app
from app import db as db_module
from app.servico import criar_pedido
from conftest import SENHA_TESTE

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "iniciar.py"
HOST_PUBLICO = "pedidos.exemplo.ts.net"
CABECALHOS_FUNNEL = {
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": HOST_PUBLICO,
    "X-Forwarded-For": "203.0.113.7",
}
REFERER_CERTO = {"Referer": f"https://{HOST_PUBLICO}/login", "Origin": f"https://{HOST_PUBLICO}"}


@pytest.fixture
def iniciar():
    spec = importlib.util.spec_from_file_location("iniciar_script", SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture(autouse=True)
def logging_limpo():
    """Cada teste começa e termina sem handlers que o iniciar.py tenha deixado."""
    raiz = logging.getLogger()
    antes = list(raiz.handlers)
    nivel = raiz.level
    yield
    for handler in list(raiz.handlers):
        if handler not in antes:
            raiz.removeHandler(handler)
            handler.close()
    raiz.setLevel(nivel)


def parar_o_loop(servidor, thread):
    """Encerra o loop do Waitress que roda em outra thread, de dentro dele.

    Fechar os sockets de fora faria o select() da thread do loop falhar com
    EBADF; pelo trigger, quem fecha é o próprio loop, que então termina sozinho.
    """
    servidor.trigger.pull_trigger(lambda: wasyncore.close_all(servidor._map, ignore_all=True))
    thread.join(timeout=10)
    assert not thread.is_alive(), "a thread do Waitress não terminou"


class Servidor:
    """Um Waitress real rodando numa thread, com um mini cliente HTTP."""

    def __init__(self, iniciar, aplicacao):
        self.iniciar = iniciar
        self.servidor = iniciar.criar_servidor(aplicacao, porta=0)
        self.porta = self.servidor.effective_port
        self.thread = threading.Thread(target=self.servidor.run, daemon=True)
        self.thread.start()

    def pedir(self, metodo, caminho, cabecalhos=None, dados=None):
        corpo = None
        cabecalhos = dict(cabecalhos or {})
        cabecalhos["Connection"] = "close"
        if dados is not None:
            corpo = urllib.parse.urlencode(dados)
            cabecalhos["Content-Type"] = "application/x-www-form-urlencoded"
        conexao = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=10)
        try:
            conexao.request(metodo, caminho, body=corpo, headers=cabecalhos)
            resposta = conexao.getresponse()
            return resposta.status, resposta.headers, resposta.read().decode("utf-8")
        finally:
            conexao.close()

    def parar(self):
        parar_o_loop(self.servidor, self.thread)
        self.iniciar.parar_servidor(self.servidor)
        # nada de worker threads do Waitress sobrando
        assert self.servidor.task_dispatcher.threads == set()


@pytest.fixture
def abrir_servidor(iniciar):
    abertos = []

    def abrir(aplicacao):
        servidor = Servidor(iniciar, aplicacao)
        abertos.append(servidor)
        return servidor

    yield abrir
    for servidor in abertos:
        servidor.parar()


@pytest.fixture
def app_real(tmp_path):
    """O app de verdade, com CSRF ligado e o endereço público do Funnel."""
    return create_app({"DATA_DIR": tmp_path / "data", "BASE_URL": f"https://{HOST_PUBLICO}"})


@pytest.fixture
def servidor(abrir_servidor, app_real):
    return abrir_servidor(app_real)


def _cookie(cabecalhos):
    sessao = [v for v in cabecalhos.get_all("Set-Cookie") or [] if v.startswith("session=")]
    assert sessao, "o servidor não mandou o cookie de sessão"
    return sessao[-1].split(";", 1)[0]


def _token(html):
    achado = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert achado, "a página não traz o token CSRF"
    return achado.group(1)


def _usuario_com_pedido(app):
    with app.app_context():
        usuario_id = auth.criar_usuario(
            "Sol Icitante", "sol@exemplo.com", SENHA_TESTE, "solicitante", trocar_senha=False
        )
        usuario = db_module.get_db().execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
        arquivo = FileStorage(stream=io.BytesIO(b"%PDF-1.4\n%%EOF\n"), filename="p.pdf")
        criar_pedido(db_module.get_db(), usuario, "Cliente Funnel Ltda", "1.234,56", "Material", arquivo)
    return "sol@exemplo.com"


# --- criar_servidor -----------------------------------------------------------


def test_login_abre_sem_cabecalhos_de_proxy(servidor):
    status, cabecalhos, html = servidor.pedir("GET", "/login")
    assert status == 200
    assert "Entrar" in html
    assert cabecalhos["Content-Security-Policy"]


def test_servidor_so_escuta_em_loopback(servidor, iniciar):
    assert servidor.servidor.effective_host == "127.0.0.1"
    # nenhuma outra interface: conectar pelo IP da rede local deve falhar
    ip_local = socket.gethostbyname(socket.gethostname())
    if not ip_local.startswith("127."):
        with pytest.raises(OSError):
            socket.create_connection((ip_local, servidor.porta), timeout=2).close()


@pytest.mark.parametrize("host", ["0.0.0.0", "", "::", "192.168.0.10", "localhost"])
def test_criar_servidor_recusa_qualquer_host_que_nao_seja_loopback(iniciar, app_real, host):
    with pytest.raises(ValueError, match="127.0.0.1"):
        iniciar.criar_servidor(app_real, host=host, porta=0)


def test_criar_servidor_configura_threads_e_proxy_confiavel(servidor):
    adj = servidor.servidor.adj
    assert adj.threads == 8
    assert adj.trusted_proxy == "127.0.0.1"
    assert adj.trusted_proxy_headers == {"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"}
    assert adj.clear_untrusted_proxy_headers is True


def test_corpo_gigante_e_barrado_pelo_waitress(servidor, app_real):
    # limite do app + folga: acima disso o Waitress recusa sem nem entregar ao Flask
    limite = servidor.servidor.adj.max_request_body_size
    assert limite > app_real.config["MAX_CONTENT_LENGTH"]
    assert limite <= app_real.config["MAX_CONTENT_LENGTH"] + 16 * 1024 * 1024


# --- o que o Waitress entrega ao WSGI (cabeçalhos do Funnel) --------------------


def test_waitress_entrega_https_host_e_ip_do_funnel(abrir_servidor):
    visto = {}

    def eco(environ, start_response):
        for chave in ("wsgi.url_scheme", "REMOTE_ADDR", "HTTP_HOST", "HTTP_X_FORWARDED_PROTO"):
            visto[chave] = environ.get(chave)
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]

    servidor = abrir_servidor(eco)
    servidor.pedir("GET", "/", CABECALHOS_FUNNEL)
    assert visto["wsgi.url_scheme"] == "https"
    assert visto["REMOTE_ADDR"] == "203.0.113.7"
    assert visto["HTTP_HOST"] == HOST_PUBLICO


def test_sem_cabecalhos_o_waitress_entrega_http_e_loopback(abrir_servidor):
    visto = {}

    def eco(environ, start_response):
        visto.update(esquema=environ["wsgi.url_scheme"], ip=environ["REMOTE_ADDR"])
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]

    abrir_servidor(eco).pedir("GET", "/")
    assert visto == {"esquema": "http", "ip": "127.0.0.1"}


# --- ponta a ponta atrás do proxy: cookie Secure e CSRF estrito -------------------


def test_login_completo_atras_do_funnel(servidor, app_real):
    email = _usuario_com_pedido(app_real)

    status, cabecalhos, html = servidor.pedir("GET", "/login", CABECALHOS_FUNNEL)
    assert status == 200
    cookie = _cookie(cabecalhos)
    set_cookie = [v for v in cabecalhos.get_all("Set-Cookie") if v.startswith("session=")][-1]
    assert "Secure" in set_cookie and "HttpOnly" in set_cookie and "SameSite=Lax" in set_cookie

    status, cabecalhos, _ = servidor.pedir(
        "POST",
        "/login",
        {**CABECALHOS_FUNNEL, **REFERER_CERTO, "Cookie": cookie},
        {"email": email, "senha": SENHA_TESTE, "csrf_token": _token(html)},
    )
    assert status == 302
    assert cabecalhos["Location"] == "/"
    cookie = _cookie(cabecalhos)

    status, _, html = servidor.pedir("GET", "/", {**CABECALHOS_FUNNEL, "Cookie": cookie})
    assert status == 200
    assert "Cliente Funnel Ltda" in html


def test_login_com_referer_de_outro_site_e_recusado(servidor, app_real):
    email = _usuario_com_pedido(app_real)
    _, cabecalhos, html = servidor.pedir("GET", "/login", CABECALHOS_FUNNEL)

    status, _, pagina = servidor.pedir(
        "POST",
        "/login",
        {
            **CABECALHOS_FUNNEL,
            "Referer": "https://site-malicioso.exemplo.com/login",
            "Origin": "https://site-malicioso.exemplo.com",
            "Cookie": _cookie(cabecalhos),
        },
        {"email": email, "senha": SENHA_TESTE, "csrf_token": _token(html)},
    )
    assert status == 400
    assert "Formulário inválido" in pagina
    assert "Sua sessão expirou ou o formulário é inválido" in pagina


def test_login_com_host_encaminhado_diferente_do_referer_e_recusado(servidor, app_real):
    email = _usuario_com_pedido(app_real)
    _, cabecalhos, html = servidor.pedir("GET", "/login", CABECALHOS_FUNNEL)

    status, _, pagina = servidor.pedir(
        "POST",
        "/login",
        {
            **CABECALHOS_FUNNEL,
            "X-Forwarded-Host": "outro-endereco.ts.net",
            **REFERER_CERTO,
            "Cookie": _cookie(cabecalhos),
        },
        {"email": email, "senha": SENHA_TESTE, "csrf_token": _token(html)},
    )
    assert status == 400
    assert "Formulário inválido" in pagina


def test_login_sem_token_csrf_e_recusado(servidor, app_real):
    email = _usuario_com_pedido(app_real)
    _, cabecalhos, _ = servidor.pedir("GET", "/login", CABECALHOS_FUNNEL)
    status, _, _ = servidor.pedir(
        "POST",
        "/login",
        {**CABECALHOS_FUNNEL, **REFERER_CERTO, "Cookie": _cookie(cabecalhos)},
        {"email": email, "senha": SENHA_TESTE},
    )
    assert status == 400


# --- main: configuração, logs e avisos ----------------------------------------------


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    """Ambiente de produção razoável, com pasta de dados temporária."""
    dados = tmp_path / "dados"
    monkeypatch.setenv("PEDIDOS_DATA_DIR", str(dados))
    monkeypatch.setenv("PEDIDOS_BASE_URL", f"https://{HOST_PUBLICO}")
    monkeypatch.setenv("PEDIDOS_SECRET_KEY", "chave-secreta-que-nao-pode-vazar-0123456789")
    return dados


class ServidorFalso:
    """Substitui o Waitress em main(): registra o que foi pedido e já 'termina'."""

    def __init__(self, interromper=False):
        self.interromper = interromper
        self.rodou = False
        self.parado = False

    def run(self):
        self.rodou = True
        if self.interromper:
            raise KeyboardInterrupt


@pytest.fixture
def falso(iniciar, monkeypatch):
    criados = []

    def criar(app, host="127.0.0.1", porta=8000):
        servidor = ServidorFalso()
        servidor.app, servidor.host, servidor.porta = app, host, porta
        criados.append(servidor)
        return servidor

    monkeypatch.setattr(iniciar, "criar_servidor", criar)
    monkeypatch.setattr(iniciar, "parar_servidor", lambda s: setattr(s, "parado", True))
    return criados


def _log(dados):
    return (dados / "logs" / "pedidos.log").read_text(encoding="utf-8")


def test_main_sobe_em_loopback_na_porta_padrao(iniciar, ambiente, falso):
    assert iniciar.main([]) == 0
    assert (falso[0].host, falso[0].porta) == ("127.0.0.1", 8000)
    assert falso[0].rodou and falso[0].parado


def test_main_usa_a_porta_do_ambiente(iniciar, ambiente, falso, monkeypatch):
    monkeypatch.setenv("PEDIDOS_PORTA", "8123")
    assert iniciar.main([]) == 0
    assert falso[0].porta == 8123


@pytest.mark.parametrize("valor", ["abc", "", "0", "-1", "70000", "80.5"])
def test_main_recusa_porta_invalida(iniciar, ambiente, falso, monkeypatch, capsys, valor):
    monkeypatch.setenv("PEDIDOS_PORTA", valor)
    assert iniciar.main([]) == 1
    erro = capsys.readouterr().err
    assert "PEDIDOS_PORTA" in erro
    assert falso == []  # nem tentou subir


def test_main_registra_informacoes_de_partida_sem_segredos(iniciar, ambiente, falso, monkeypatch, capsys):
    monkeypatch.setenv("PEDIDOS_EMAIL_MODE", "smtp")
    monkeypatch.setenv("PEDIDOS_SMTP_SENHA", "senha-smtp-super-secreta")
    monkeypatch.setenv("PEDIDOS_SMTP_USUARIO", "robo@exemplo.com")
    assert iniciar.main([]) == 0
    texto = _log(ambiente)
    saida = capsys.readouterr()
    for onde in (texto, saida.out, saida.err):
        assert "chave-secreta-que-nao-pode-vazar" not in onde
        assert "senha-smtp-super-secreta" not in onde
    assert "Python" in texto
    assert str(ambiente) in texto
    assert "8000" in texto
    assert "smtp" in texto
    assert f"https://{HOST_PUBLICO}" in texto
    assert "Python" in saida.out  # o console mostra o mesmo


def test_main_sem_avisos_quando_tudo_esta_certo(iniciar, ambiente, falso, monkeypatch):
    monkeypatch.setenv("PEDIDOS_EMAIL_MODE", "smtp")
    assert iniciar.main([]) == 0
    assert "WARNING" not in _log(ambiente)


def test_main_avisa_cookie_inseguro(iniciar, ambiente, falso, monkeypatch, capsys):
    monkeypatch.setenv("PEDIDOS_EMAIL_MODE", "smtp")
    monkeypatch.setenv("PEDIDOS_COOKIE_INSECURE", "1")
    assert iniciar.main([]) == 0
    assert "COOKIE_INSECURE" in capsys.readouterr().out
    log = _log(ambiente)
    assert "WARNING" in log and "COOKIE_INSECURE" in log


def test_main_avisa_base_url_sem_https(iniciar, ambiente, falso, monkeypatch, capsys):
    monkeypatch.setenv("PEDIDOS_EMAIL_MODE", "smtp")
    monkeypatch.setenv("PEDIDOS_BASE_URL", "http://localhost:8000")
    assert iniciar.main([]) == 0
    assert "BASE_URL" in capsys.readouterr().out
    log = _log(ambiente)
    assert "WARNING" in log and "BASE_URL" in log


def test_main_avisa_que_email_esta_desligado(iniciar, ambiente, falso, capsys):
    assert iniciar.main([]) == 0  # EMAIL_MODE padrão: desligado
    assert "desligado" in capsys.readouterr().out
    assert "desligado" in _log(ambiente)


def test_ctrl_c_encerra_limpo(iniciar, ambiente, monkeypatch, capsys):
    servidores = []

    def criar(app, host="127.0.0.1", porta=8000):
        servidores.append(ServidorFalso(interromper=True))
        return servidores[0]

    parados = []
    monkeypatch.setattr(iniciar, "criar_servidor", criar)
    monkeypatch.setattr(iniciar, "parar_servidor", parados.append)
    assert iniciar.main([]) == 0
    assert parados == servidores
    assert "Traceback" not in capsys.readouterr().err
    assert "encerrado" in _log(ambiente).lower()


def test_main_nao_deixa_handlers_de_log_abertos(iniciar, ambiente, falso):
    antes = list(logging.getLogger().handlers)
    iniciar.main([])
    iniciar.main([])
    assert logging.getLogger().handlers == antes


def test_log_gira_em_1mb_com_5_arquivos(iniciar, ambiente, falso, monkeypatch):
    capturados = []
    original = iniciar.configurar_logs

    def espiar(pasta):
        handlers = original(pasta)
        capturados.extend(handlers)
        return handlers

    monkeypatch.setattr(iniciar, "configurar_logs", espiar)
    iniciar.main([])
    rotativo = [h for h in capturados if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(rotativo) == 1
    assert rotativo[0].maxBytes == 1024 * 1024
    assert rotativo[0].backupCount == 5
    assert rotativo[0].encoding == "utf-8"
    assert Path(rotativo[0].baseFilename) == ambiente / "logs" / "pedidos.log"


def test_main_porta_ocupada_da_erro_claro(iniciar, ambiente, monkeypatch, capsys):
    ocupante = socket.socket()
    ocupante.bind(("127.0.0.1", 0))
    ocupante.listen()

    def criar_na_porta_ocupada(app, host="127.0.0.1", porta=8000):
        # o mesmo bind que o Waitress faz; o erro é o do sistema operacional
        # (o Waitress em si deixa recursos soltos quando o bind falha)
        with socket.socket() as tentativa:
            tentativa.bind((host, porta))

    monkeypatch.setattr(iniciar, "criar_servidor", criar_na_porta_ocupada)
    try:
        monkeypatch.setenv("PEDIDOS_PORTA", str(ocupante.getsockname()[1]))
        assert iniciar.main([]) == 1
    finally:
        ocupante.close()
    saida = capsys.readouterr()
    assert "porta" in (saida.out + saida.err).lower()
    assert "Traceback" not in saida.out + saida.err
    assert "porta" in _log(ambiente).lower()


def test_main_configuracao_invalida_da_erro_claro(iniciar, ambiente, monkeypatch, capsys):
    monkeypatch.setenv("PEDIDOS_EMAIL_MODE", "foguete")
    assert iniciar.main([]) == 1
    erro = capsys.readouterr().err
    assert "EMAIL_MODE" in erro
    assert "Traceback" not in erro


def test_main_de_verdade_sobe_atende_e_encerra_no_ctrl_c(iniciar, ambiente, monkeypatch):
    """main() com o Waitress real; o 'Ctrl+C' vem da própria rodada de teste."""
    reais = []
    original = iniciar.criar_servidor

    class ComCtrlC:
        def __init__(self, servidor):
            self.servidor = servidor
            self.thread = threading.Thread(target=servidor.run, daemon=True)

        def __getattr__(self, nome):
            return getattr(self.servidor, nome)

        def run(self):
            self.thread.start()
            conexao = http.client.HTTPConnection("127.0.0.1", self.servidor.effective_port, timeout=10)
            conexao.request("GET", "/login", headers={"Connection": "close"})
            assert conexao.getresponse().status == 200
            conexao.close()
            parar_o_loop(self.servidor, self.thread)  # o loop de verdade roda no main()
            raise KeyboardInterrupt

    def criar(app, host="127.0.0.1", porta=8000):
        servidor = ComCtrlC(original(app, host, 0))
        reais.append(servidor)
        return servidor

    monkeypatch.setattr(iniciar, "criar_servidor", criar)
    parar_original = iniciar.parar_servidor
    monkeypatch.setattr(iniciar, "parar_servidor", lambda s: parar_original(s.servidor))
    assert iniciar.main([]) == 0
    assert not reais[0].thread.is_alive()
    assert reais[0].servidor.task_dispatcher.threads == set()


# --- PEDIDOS_DATA_DIR obrigatória e pasta de dados dentro do projeto --------------------


@pytest.mark.parametrize("valor", [None, "", "   "])
def test_main_recusa_iniciar_sem_pedidos_data_dir(iniciar, falso, monkeypatch, capsys, valor):
    monkeypatch.delenv("PEDIDOS_DATA_DIR", raising=False)
    if valor is not None:
        monkeypatch.setenv("PEDIDOS_DATA_DIR", valor)

    def nao_pode(*_args, **_kwargs):
        raise AssertionError("não devia criar nada antes de recusar")

    monkeypatch.setattr(iniciar, "configurar_logs", nao_pode)
    monkeypatch.setattr(iniciar, "create_app", nao_pode)
    assert iniciar.main([]) == 1
    saida = capsys.readouterr()
    assert "PEDIDOS_DATA_DIR" in saida.err
    assert "variáve" in saida.err
    assert "Traceback" not in saida.err
    assert saida.out == ""
    assert falso == []


def test_main_sem_pedidos_data_dir_nao_cria_a_pasta_data(iniciar, falso, monkeypatch, tmp_path):
    projeto = tmp_path / "projeto"
    projeto.mkdir()
    monkeypatch.delenv("PEDIDOS_DATA_DIR", raising=False)
    monkeypatch.setitem(config_mod.PADROES, "DATA_DIR", projeto / "data")
    assert iniciar.main([]) == 1
    assert list(projeto.iterdir()) == []


def test_main_avisa_se_a_pasta_de_dados_fica_dentro_do_projeto(iniciar, falso, monkeypatch, tmp_path, capsys):
    projeto = tmp_path / "projeto"
    monkeypatch.setattr(iniciar, "RAIZ", projeto)
    monkeypatch.setenv("PEDIDOS_DATA_DIR", str(projeto / "data"))
    monkeypatch.setenv("PEDIDOS_BASE_URL", f"https://{HOST_PUBLICO}")
    monkeypatch.setenv("PEDIDOS_EMAIL_MODE", "smtp")
    assert iniciar.main([]) == 0
    log = (projeto / "data" / "logs" / "pedidos.log").read_text(encoding="utf-8")
    assert "WARNING" in log and "dentro da pasta do programa" in log
    assert "dentro da pasta do programa" in capsys.readouterr().out


def test_main_nao_avisa_pasta_de_dados_fora_do_projeto(iniciar, ambiente, falso, monkeypatch):
    monkeypatch.setenv("PEDIDOS_EMAIL_MODE", "smtp")
    assert iniciar.main([]) == 0
    assert "dentro da pasta do programa" not in _log(ambiente)


def test_escutando_so_aparece_depois_de_o_servidor_abrir_a_porta(iniciar, ambiente, monkeypatch):
    def falha(app, host="127.0.0.1", porta=8000):
        raise OSError(98, "Address already in use")

    monkeypatch.setattr(iniciar, "criar_servidor", falha)
    assert iniciar.main([]) == 1
    assert "Escutando em" not in _log(ambiente)


def test_escutando_aparece_quando_o_servidor_abriu(iniciar, ambiente, falso):
    assert iniciar.main([]) == 0
    assert "Escutando em http://127.0.0.1:8000" in _log(ambiente)


# --- vigia do proxy: Funnel sem X-Forwarded-Proto ------------------------------------------


def _chamar(app, host="pedidos.exemplo.ts.net", esquema="http", encaminhado=None):
    environ = {"HTTP_HOST": host, "wsgi.url_scheme": esquema, "REQUEST_METHOD": "GET", "PATH_INFO": "/"}
    if encaminhado is not None:
        environ["HTTP_X_FORWARDED_PROTO"] = encaminhado
    corpo = app(environ, lambda *_: None)
    return corpo


def _eco_ok(environ, start_response):
    start_response("200 OK", [])
    return [b"ok"]


def _mensagens(caplog, nivel):
    return [r.getMessage() for r in caplog.records if r.name == "pedidos" and r.levelno == nivel]


def test_vigia_avisa_uma_vez_host_publico_em_http_sem_proto_encaminhado(iniciar, caplog):
    caplog.set_level(logging.INFO, logger="pedidos")
    app = iniciar.VigiaDoProxy(_eco_ok)
    assert _chamar(app) == [b"ok"]  # a requisição segue normalmente
    _chamar(app)
    _chamar(app, host="outro.ts.net:8443")
    avisos = _mensagens(caplog, logging.WARNING)
    assert len(avisos) == 1
    assert "X-Forwarded-Proto" in avisos[0]
    assert "pedidos.exemplo.ts.net" not in avisos[0]  # nenhum valor de cabeçalho no log
    assert _mensagens(caplog, logging.INFO) == []


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:8000", "localhost", "LOCALHOST:8000", "[::1]", "[::1]:8000"])
def test_vigia_nao_avisa_para_loopback(iniciar, caplog, host):
    caplog.set_level(logging.INFO, logger="pedidos")
    _chamar(iniciar.VigiaDoProxy(_eco_ok), host=host)
    assert _mensagens(caplog, logging.WARNING) == []
    assert _mensagens(caplog, logging.INFO) == []


def test_vigia_nao_avisa_e_confirma_uma_vez_com_https_encaminhado(iniciar, caplog):
    caplog.set_level(logging.INFO, logger="pedidos")
    app = iniciar.VigiaDoProxy(_eco_ok)
    _chamar(app, esquema="https", encaminhado="https")
    _chamar(app, esquema="https", encaminhado="https")
    assert _mensagens(caplog, logging.WARNING) == []
    confirmacoes = _mensagens(caplog, logging.INFO)
    assert confirmacoes == ["Primeira requisição via proxy HTTPS recebida (X-Forwarded-Proto=https)"]


def test_vigia_avisa_e_depois_confirma_cada_um_uma_vez(iniciar, caplog):
    caplog.set_level(logging.INFO, logger="pedidos")
    app = iniciar.VigiaDoProxy(_eco_ok)
    _chamar(app)
    _chamar(app, esquema="https", encaminhado="https")
    _chamar(app)
    _chamar(app, esquema="https", encaminhado="https")
    assert len(_mensagens(caplog, logging.WARNING)) == 1
    assert len(_mensagens(caplog, logging.INFO)) == 1


def test_vigia_nao_quebra_a_requisicao_com_environ_estranho(iniciar):
    app = iniciar.VigiaDoProxy(_eco_ok)
    assert app({"REQUEST_METHOD": "GET"}, lambda *_: None) == [b"ok"]
    assert app({"HTTP_HOST": None, "wsgi.url_scheme": "http"}, lambda *_: None) == [b"ok"]


def test_vigia_e_seguro_com_varias_threads(iniciar, caplog):
    caplog.set_level(logging.INFO, logger="pedidos")
    app = iniciar.VigiaDoProxy(_eco_ok)
    fios = [threading.Thread(target=_chamar, args=(app,)) for _ in range(20)]
    for fio in fios:
        fio.start()
    for fio in fios:
        fio.join()
    assert len(_mensagens(caplog, logging.WARNING)) == 1


def test_waitress_real_avisa_host_publico_sem_proto_e_confirma_com_funnel(servidor, caplog):
    caplog.set_level(logging.INFO, logger="pedidos")
    # Host público por http, como seria um Funnel que esquece o X-Forwarded-Proto
    servidor.pedir("GET", "/login", {"Host": HOST_PUBLICO})
    servidor.pedir("GET", "/login", {"Host": HOST_PUBLICO})
    assert len(_mensagens(caplog, logging.WARNING)) == 1
    assert _mensagens(caplog, logging.INFO) == []
    # agora um Funnel correto
    servidor.pedir("GET", "/login", CABECALHOS_FUNNEL)
    servidor.pedir("GET", "/login", CABECALHOS_FUNNEL)
    assert len(_mensagens(caplog, logging.WARNING)) == 1
    assert len(_mensagens(caplog, logging.INFO)) == 1


def test_waitress_real_nao_avisa_com_funnel_correto_nem_em_loopback(servidor, caplog):
    caplog.set_level(logging.INFO, logger="pedidos")
    servidor.pedir("GET", "/login")  # Host 127.0.0.1:porta
    servidor.pedir("GET", "/login", {"Host": "localhost"})
    assert _mensagens(caplog, logging.WARNING) == []
    servidor.pedir("GET", "/login", CABECALHOS_FUNNEL)
    assert _mensagens(caplog, logging.WARNING) == []
