"""scripts/testar_email.py: manda um e-mail de teste com as variáveis PEDIDOS_SMTP_*."""

import importlib.util
import smtplib
import socket
import ssl
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "testar_email.py"
SENHA = "chave-smtp-super-secreta-123"
AMBIENTE = {
    "PEDIDOS_SMTP_HOST": "smtp-relay.exemplo.com",
    "PEDIDOS_SMTP_PORTA": "587",
    "PEDIDOS_SMTP_USUARIO": "login@smtp-exemplo.com",
    "PEDIDOS_SMTP_SENHA": SENHA,
    "PEDIDOS_SMTP_REMETENTE": "pedidos@avisos.exemplo.com",
}


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("testar_email_script", SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class SmtpFalso:
    """Substitui smtplib.SMTP: registra as chamadas, não toca na rede."""

    instancias = []
    erro_em = {}  # nome da etapa -> exceção a levantar

    def __init__(self, host, porta, timeout=None):
        if "init" in SmtpFalso.erro_em:
            raise SmtpFalso.erro_em["init"]
        self.chamadas = [("init", host, porta, timeout)]
        self.mensagem = None
        SmtpFalso.instancias.append(self)

    def _etapa(self, nome, *args):
        self.chamadas.append((nome, *args))
        if nome in SmtpFalso.erro_em:
            raise SmtpFalso.erro_em[nome]

    def starttls(self, **kwargs):
        self._etapa("starttls")

    def login(self, usuario, senha):
        self._etapa("login", usuario, senha)

    def send_message(self, mensagem):
        self.mensagem = mensagem
        self._etapa("send_message")

    def quit(self):
        self._etapa("quit")

    def close(self):
        self.chamadas.append(("close",))


@pytest.fixture(autouse=True)
def smtp_falso(monkeypatch):
    SmtpFalso.instancias = []
    SmtpFalso.erro_em = {}
    monkeypatch.setattr(smtplib, "SMTP", SmtpFalso)
    return SmtpFalso


def rodar(script, capsys, argv=("alguem@empresa.com",), ambiente=None):
    codigo = script.main(list(argv), environ=dict(AMBIENTE if ambiente is None else ambiente))
    saida = capsys.readouterr()
    return codigo, saida.out, saida.err


def tudo(saida):
    return saida[1] + saida[2]


# ---- caminho feliz --------------------------------------------------------


def test_envia_um_e_mail_de_teste_e_diz_que_deu_certo(script, capsys, smtp_falso):
    codigo, saida, erro = rodar(script, capsys)

    assert codigo == 0, erro
    (smtp,) = smtp_falso.instancias
    assert [c[0] for c in smtp.chamadas] == ["init", "starttls", "login", "send_message", "quit"]
    assert smtp.chamadas[0][1:3] == ("smtp-relay.exemplo.com", 587)
    assert smtp.chamadas[2][1:] == ("login@smtp-exemplo.com", SENHA)
    assert smtp.mensagem["To"] == "alguem@empresa.com"
    assert smtp.mensagem["From"] == "pedidos@avisos.exemplo.com"
    assert "Teste" in smtp.mensagem["Subject"]
    assert "alguem@empresa.com" in saida
    assert "enviado" in saida.lower()


def test_o_corpo_do_teste_explica_o_que_e_e_nao_traz_segredos(script, capsys, smtp_falso):
    rodar(script, capsys)
    (smtp,) = smtp_falso.instancias
    corpo = smtp.mensagem.get_content()
    assert "teste" in corpo.lower()
    assert "smtp-relay.exemplo.com" in corpo
    assert SENHA not in corpo


def test_nunca_mostra_a_senha_nem_em_caso_de_sucesso_nem_de_erro(script, capsys, smtp_falso):
    saida = rodar(script, capsys)
    assert SENHA not in tudo(saida)

    smtp_falso.erro_em["login"] = smtplib.SMTPAuthenticationError(535, b"Authentication failed")
    saida = rodar(script, capsys)
    assert SENHA not in tudo(saida)


def test_mostra_a_configuracao_usada_sem_a_senha(script, capsys):
    _, saida, _ = rodar(script, capsys)
    assert "smtp-relay.exemplo.com" in saida
    assert "587" in saida
    assert "login@smtp-exemplo.com" in saida
    assert "pedidos@avisos.exemplo.com" in saida


def test_porta_padrao_587_quando_a_variavel_nao_existe(script, capsys, smtp_falso):
    ambiente = {k: v for k, v in AMBIENTE.items() if k != "PEDIDOS_SMTP_PORTA"}
    codigo, _, _ = rodar(script, capsys, ambiente=ambiente)
    assert codigo == 0
    assert smtp_falso.instancias[0].chamadas[0][2] == 587


# ---- variáveis e argumentos -----------------------------------------------


@pytest.mark.parametrize("faltando", ["PEDIDOS_SMTP_HOST", "PEDIDOS_SMTP_REMETENTE"])
def test_variavel_obrigatoria_ausente_diz_qual_e_nao_conecta(script, capsys, smtp_falso, faltando):
    ambiente = {k: v for k, v in AMBIENTE.items() if k != faltando}
    codigo, saida, erro = rodar(script, capsys, ambiente=ambiente)
    assert codigo == 1
    assert faltando in saida + erro
    assert smtp_falso.instancias == []


def test_porta_que_nao_e_numero_e_recusada(script, capsys, smtp_falso):
    codigo, saida, erro = rodar(script, capsys, ambiente={**AMBIENTE, "PEDIDOS_SMTP_PORTA": "abc"})
    assert codigo == 1
    assert "PEDIDOS_SMTP_PORTA" in saida + erro
    assert smtp_falso.instancias == []


@pytest.mark.parametrize("destino", ["", "semarroba", "a@b@c.com", "com espaco@x.com", "x@y.com\nBcc: z@w.com", "@x.com", "x@"])
def test_destino_invalido_e_recusado_sem_conectar(script, capsys, smtp_falso, destino):
    codigo, saida, erro = rodar(script, capsys, argv=[destino])
    assert codigo == 1
    assert "e-mail" in (saida + erro).lower()
    assert smtp_falso.instancias == []


def test_sem_destino_o_argparse_explica_como_usar(script, capsys):
    with pytest.raises(SystemExit) as saindo:
        script.main([], environ=dict(AMBIENTE))
    assert saindo.value.code == 2
    assert "destino" in capsys.readouterr().err.lower()


def test_sem_usuario_e_senha_avisa_mas_tenta_sem_login(script, capsys, smtp_falso):
    ambiente = {k: v for k, v in AMBIENTE.items() if k not in ("PEDIDOS_SMTP_USUARIO", "PEDIDOS_SMTP_SENHA")}
    codigo, saida, erro = rodar(script, capsys, ambiente=ambiente)
    assert codigo == 0
    assert "login" not in [c[0] for c in smtp_falso.instancias[0].chamadas]
    assert "PEDIDOS_SMTP_USUARIO" in saida + erro  # o aviso de que está sem login


def test_avisa_quando_o_modo_do_sistema_ainda_nao_e_smtp(script, capsys):
    _, saida, _ = rodar(script, capsys, ambiente={**AMBIENTE, "PEDIDOS_EMAIL_MODE": "desligado"})
    assert "PEDIDOS_EMAIL_MODE" in saida
    assert "smtp" in saida


def test_nao_avisa_do_modo_quando_ja_e_smtp(script, capsys):
    _, saida, _ = rodar(script, capsys, ambiente={**AMBIENTE, "PEDIDOS_EMAIL_MODE": "smtp"})
    assert "PEDIDOS_EMAIL_MODE" not in saida


# ---- diagnóstico dos erros -------------------------------------------------

ERROS = [
    pytest.param(
        "login",
        smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed"),
        ["PEDIDOS_SMTP_USUARIO", "PEDIDOS_SMTP_SENHA"],
        id="login-recusado",
    ),
    pytest.param(
        "send_message",
        smtplib.SMTPSenderRefused(550, b"sender not verified", "pedidos@avisos.exemplo.com"),
        ["PEDIDOS_SMTP_REMETENTE", "pedidos@avisos.exemplo.com"],
        id="remetente-recusado",
    ),
    pytest.param(
        "send_message",
        smtplib.SMTPRecipientsRefused({"alguem@empresa.com": (550, b"no such user")}),
        ["alguem@empresa.com"],
        id="destinatario-recusado",
    ),
    pytest.param(
        "init",
        socket.gaierror(-2, "Name or service not known"),
        ["PEDIDOS_SMTP_HOST", "smtp-relay.exemplo.com"],
        id="host-nao-existe",
    ),
    pytest.param(
        "init",
        TimeoutError("timed out"),
        ["587", "firewall"],
        id="timeout",
    ),
    pytest.param(
        "init",
        ConnectionRefusedError(61, "Connection refused"),
        ["PEDIDOS_SMTP_PORTA", "587"],
        id="conexao-recusada",
    ),
    pytest.param(
        "starttls",
        ssl.SSLError("certificate verify failed"),
        ["TLS"],
        id="tls",
    ),
    pytest.param(
        "starttls",
        smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server."),
        ["PEDIDOS_SMTP_PORTA", "587"],
        id="sem-starttls",
    ),
    pytest.param(
        "send_message",
        smtplib.SMTPServerDisconnected("Connection unexpectedly closed"),
        ["conexão"],
        id="desconectou",
    ),
    pytest.param(
        "send_message",
        smtplib.SMTPDataError(554, b"message rejected"),
        ["554"],
        id="rejeitada",
    ),
]


@pytest.mark.parametrize("etapa, excecao, deve_conter", ERROS)
def test_cada_erro_vira_mensagem_em_portugues_com_o_que_conferir(
    script, capsys, smtp_falso, etapa, excecao, deve_conter
):
    smtp_falso.erro_em[etapa] = excecao

    codigo, saida, erro = rodar(script, capsys)

    texto = saida + erro
    assert codigo == 1
    assert "Traceback" not in texto
    assert "não foi enviado" in texto.lower() or "nao foi enviado" in texto.lower()
    for trecho in deve_conter:
        assert trecho.lower() in texto.lower(), f"faltou {trecho!r} em: {texto}"


def test_erro_inesperado_nao_gera_traceback_e_nao_vaza_a_senha(script, capsys, smtp_falso):
    smtp_falso.erro_em["send_message"] = RuntimeError(f"algo estranho com {SENHA}")
    codigo, saida, erro = rodar(script, capsys)
    texto = saida + erro
    assert codigo == 1
    assert "Traceback" not in texto
    assert SENHA not in texto


def test_o_servidor_e_fechado_mesmo_quando_o_envio_falha(script, capsys, smtp_falso):
    smtp_falso.erro_em["send_message"] = smtplib.SMTPDataError(554, b"rejeitada")
    rodar(script, capsys)
    (smtp,) = smtp_falso.instancias
    assert smtp.chamadas[-1][0] in ("quit", "close")


# ---- integração com o resto do projeto --------------------------------------


def test_usa_o_mesmo_notificador_que_o_sistema(script):
    """O teste só vale se exercitar o código que o sistema usa de verdade."""
    from app import notificacoes

    assert script.notificador_da_config is notificacoes.notificador_da_config


def test_nao_precisa_de_pedidos_data_dir_nem_de_banco(script, capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("PEDIDOS_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    codigo, _, _ = rodar(script, capsys)
    assert codigo == 0
    assert list(tmp_path.iterdir()) == []  # nada criado
