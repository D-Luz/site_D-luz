"""Servidor de produção: python scripts/iniciar.py

Sobe o sistema com o Waitress em 127.0.0.1 (porta 8000, ou a de PEDIDOS_PORTA)
e fica rodando até Ctrl+C. No Windows quem chama é a tarefa "Pedidos - Servidor"
do Agendador de Tarefas, por meio de scripts/iniciar_pedidos.bat.

O sistema fica atrás do Tailscale Funnel, que termina o HTTPS e repassa as
requisições para http://127.0.0.1:8000 com os cabeçalhos X-Forwarded-*.
Detalhes e instalação: scripts/instalar_windows.md.

Variáveis de ambiente lidas aqui: PEDIDOS_PORTA (padrão 8000) e as de
app/config.py (PEDIDOS_DATA_DIR, PEDIDOS_BASE_URL, PEDIDOS_EMAIL_MODE...).
PEDIDOS_DATA_DIR é OBRIGATÓRIA: sem ela o script se recusa a subir (e não cria
nada), para nunca abrir por engano um banco vazio dentro da pasta do programa.

O registro (log) vai para a tela e para DATA_DIR/logs/pedidos.log (gira em
1 MB, guardando 5 arquivos). Nunca registra a chave secreta nem a senha do SMTP.
Código de saída: 0 ao encerrar normalmente, 1 se não conseguiu iniciar.
"""

import argparse
import logging
import logging.handlers
import os
import platform
import sqlite3
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

# Permite rodar o script direto da raiz do projeto, sem instalar nada.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import waitress  # noqa: E402
from waitress import wasyncore  # noqa: E402

from app import create_app  # noqa: E402
from app.config import MSG_SEM_DATA_DIR, RAIZ, carregar, data_dir_definido  # noqa: E402

# O servidor escuta SOMENTE neste endereço, sem opção para mudar: só o Tailscale
# Funnel (que roda neste mesmo PC) pode falar com ele. Escutar em 0.0.0.0 abriria
# o sistema, sem HTTPS, para toda a rede local.
HOST = "127.0.0.1"
PORTA_PADRAO = 8000
THREADS = 8
# O Waitress só aceita cabeçalhos X-Forwarded-* de quem conecta de HOST (o Funnel)
# e apaga os demais; sem isso ele os descarta e o app nunca saberia que é HTTPS.
CABECALHOS_DE_PROXY = ("x-forwarded-for", "x-forwarded-proto", "x-forwarded-host")
# Folga acima do limite do app, para o Flask ainda responder com a página de erro
# em português; corpos maiores que isso o Waitress recusa sem nem guardá-los.
FOLGA_DO_CORPO = 10 * 1024 * 1024

LOG_TAMANHO = 1024 * 1024
LOG_ARQUIVOS = 5
FORMATO_LOG = "%(asctime)s %(levelname)s %(message)s"

log = logging.getLogger("pedidos")

# Hosts que indicam acesso direto, pelo próprio PC (sem passar pelo Funnel).
HOSTS_LOCAIS = frozenset({"127.0.0.1", "localhost", "[::1]"})


def _nome_do_host(host):
    """Host sem a porta, em minúsculas: 'Exemplo.com:443' -> 'exemplo.com'; '[::1]:80' -> '[::1]'."""
    host = host.strip().lower()
    if host.startswith("["):
        return host[: host.find("]") + 1] or host
    return host.rsplit(":", 1)[0] if ":" in host else host


class VigiaDoProxy:
    """Middleware WSGI que confere, uma vez por processo, se o Funnel manda HTTPS.

    Sem ``X-Forwarded-Proto: https`` o app acha que está em http: o Flask-WTF
    pula a conferência estrita do Referer e os links saem em http, e o login
    ainda funciona, então o defeito passaria despercebido. Por isso:

    * pedido para um host que NÃO é do próprio PC, em http e sem
      X-Forwarded-Proto: um WARNING (só o primeiro);
    * primeiro pedido com X-Forwarded-Proto=https: um INFO de confirmação.

    Nunca registra valores de cabeçalhos, cookies ou corpo. Depois dos dois
    registros, cada requisição custa só um teste de dois booleanos.
    """

    def __init__(self, app):
        self.app = app
        self._trava = threading.Lock()
        self._avisou = False
        self._confirmou = False

    def __call__(self, environ, start_response):
        if not (self._avisou and self._confirmou):
            try:
                self._observar(environ)
            except Exception:  # o vigia jamais pode derrubar uma requisição
                pass
        return self.app(environ, start_response)

    def _observar(self, environ):
        proto = environ.get("HTTP_X_FORWARDED_PROTO")
        if proto and proto.strip().lower() == "https":
            if not self._confirmou:
                with self._trava:
                    primeira, self._confirmou = not self._confirmou, True
                if primeira:
                    log.info("Primeira requisição via proxy HTTPS recebida (X-Forwarded-Proto=https)")
            return
        if self._avisou or proto or environ.get("wsgi.url_scheme") != "http":
            return
        host = environ.get("HTTP_HOST")
        if not host or _nome_do_host(host) in HOSTS_LOCAIS:
            return
        with self._trava:
            primeira, self._avisou = not self._avisou, True
        if primeira:
            log.warning(
                "ATENÇÃO: chegou um pedido por um endereço que não é deste PC, em http e "
                "sem X-Forwarded-Proto. O proxy (Tailscale Funnel) não está avisando que "
                "é HTTPS, então as proteções de HTTPS (conferência estrita do Referer dos "
                "formulários, links https) ficam reduzidas. Confira 'tailscale funnel "
                "status' e a versão do Tailscale."
            )


def criar_servidor(app, host=HOST, porta=PORTA_PADRAO):
    """Cria (sem rodar) o servidor Waitress; ``servidor.run()`` fica em loop.

    ``porta=0`` deixa o sistema escolher uma porta livre (usado nos testes);
    a escolhida fica em ``servidor.effective_port``.
    """
    if host != HOST:
        raise ValueError(
            f"O servidor só pode escutar em {HOST}: o acesso de fora é feito "
            "pelo Tailscale Funnel, que roda neste mesmo computador."
        )
    opcoes = {}
    config = getattr(app, "config", None)
    if config is not None and config.get("MAX_CONTENT_LENGTH"):
        opcoes["max_request_body_size"] = config["MAX_CONTENT_LENGTH"] + FOLGA_DO_CORPO
    return waitress.create_server(
        VigiaDoProxy(app),
        host=host,
        port=porta,
        threads=THREADS,
        trusted_proxy=HOST,
        trusted_proxy_headers=set(CABECALHOS_DE_PROXY),
        clear_untrusted_proxy_headers=True,
        **opcoes,
    )


def parar_servidor(servidor):
    """Para os workers e fecha o socket e as conexões abertas do servidor."""
    servidor.task_dispatcher.shutdown()
    servidor.close()
    mapa = getattr(servidor, "_map", None)
    if mapa is not None:
        wasyncore.close_all(mapa, ignore_all=True)


def ler_porta(ambiente=None):
    """Porta de PEDIDOS_PORTA (1 a 65535; padrão 8000). ValueError se não servir."""
    ambiente = os.environ if ambiente is None else ambiente
    texto = ambiente.get("PEDIDOS_PORTA")
    if texto is None:
        return PORTA_PADRAO
    try:
        porta = int(texto.strip())
    except ValueError:
        porta = 0
    if not 1 <= porta <= 65535:
        raise ValueError(
            f"PEDIDOS_PORTA deve ser um número de 1 a 65535 (valor recebido: {texto!r})."
        )
    return porta


def configurar_logs(pasta):
    """Liga o registro em ``pasta/pedidos.log`` e na tela; devolve os handlers criados."""
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    formato = logging.Formatter(FORMATO_LOG)
    arquivo = logging.handlers.RotatingFileHandler(
        pasta / "pedidos.log", maxBytes=LOG_TAMANHO, backupCount=LOG_ARQUIVOS, encoding="utf-8"
    )
    handlers = [arquivo]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    for handler in handlers:
        handler.setFormatter(formato)
        raiz.addHandler(handler)
    return handlers


def desligar_logs(handlers):
    raiz = logging.getLogger()
    for handler in handlers:
        raiz.removeHandler(handler)
        handler.close()


def _preparar_console():
    # O console do Windows pode não saber algum caractere; melhor trocá-lo por "?"
    # do que derrubar o registro.
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def _dentro_de(pasta, base):
    pasta, base = pasta.resolve(), Path(base).resolve()
    return pasta == base or base in pasta.parents


def registrar_partida(config, porta):
    """Escreve no log como o sistema está configurado (nunca segredos) e avisa dos riscos."""
    log.info("Aprovação de Pedidos iniciando")
    log.info("Python %s em %s", platform.python_version(), platform.platform())
    log.info("Pasta de dados: %s", config["DATA_DIR"])
    log.info("EMAIL_MODE: %s", config["EMAIL_MODE"])
    log.info("BASE_URL: %s", config["BASE_URL"])

    if _dentro_de(Path(config["DATA_DIR"]), RAIZ):
        log.warning(
            "ATENÇÃO: a pasta de dados (%s) está dentro da pasta do programa (%s). "
            "Atualizar ou apagar o programa levaria o banco junto; use uma pasta "
            "separada, como C:\\PedidosDados, em PEDIDOS_DATA_DIR.",
            config["DATA_DIR"],
            RAIZ,
        )
    if config["COOKIE_INSECURE"]:
        log.warning(
            "ATENÇÃO: PEDIDOS_COOKIE_INSECURE está ligado. O cookie de login passa a "
            "viajar sem HTTPS. Use somente em teste local e desligue antes de publicar."
        )
    if urlsplit(config["BASE_URL"]).scheme != "https":
        log.warning(
            "ATENÇÃO: PEDIDOS_BASE_URL (%s) não começa com https://. Os links dos "
            "e-mails e avisos não vão funcionar fora deste computador; coloque o "
            "endereço do Tailscale Funnel.",
            config["BASE_URL"],
        )
    if config["EMAIL_MODE"] == "desligado":
        log.info(
            "AVISO (informativo): o envio de e-mail está desligado; os avisos "
            "aparecem somente dentro do sistema."
        )


def _executar(porta):
    try:
        app = create_app()
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as erro:
        log.error("Não consegui preparar o sistema: %s", erro)
        return 1
    except Exception:
        log.exception("Erro inesperado ao preparar o sistema")
        return 1

    registrar_partida(app.config, porta)
    try:
        servidor = criar_servidor(app, HOST, porta)
    except OSError as erro:
        log.error(
            "Não consegui abrir a porta %s em %s (%s). Talvez o servidor já esteja "
            "rodando ou outro programa use essa porta; feche-o ou troque PEDIDOS_PORTA.",
            porta,
            HOST,
            erro,
        )
        return 1
    except Exception:
        log.exception("Erro inesperado ao criar o servidor")
        return 1

    log.info("Escutando em http://%s:%s (só este computador; o acesso externo é pelo Funnel)", HOST, porta)
    log.info("Servidor pronto. Para encerrar, pressione Ctrl+C.")
    try:
        servidor.run()
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("O servidor parou por um erro inesperado")
        return 1
    finally:
        parar_servidor(servidor)
    log.info("Servidor encerrado.")
    return 0


def _argumentos(argv):
    analisador = argparse.ArgumentParser(
        description=(
            f"Sobe o sistema de aprovação de pedidos em {HOST} (só este computador). "
            f"A porta é a de PEDIDOS_PORTA (padrão {PORTA_PADRAO})."
        )
    )
    return analisador.parse_args(argv)


def main(argv=None) -> int:
    _argumentos(sys.argv[1:] if argv is None else argv)
    _preparar_console()
    if not data_dir_definido():
        # Sem esta variável o sistema usaria a pasta data/ do próprio programa e
        # criaria um banco vazio no lugar errado. Melhor não subir.
        print(MSG_SEM_DATA_DIR, file=sys.stderr)
        return 1
    try:
        porta = ler_porta()
        config = carregar()
    except ValueError as erro:
        print(f"Erro de configuração: {erro}", file=sys.stderr)
        return 1
    try:
        handlers = configurar_logs(config["DATA_DIR"] / "logs")
    except OSError as erro:
        print(f"Erro: não consegui criar o registro em {config['DATA_DIR'] / 'logs'} ({erro}).", file=sys.stderr)
        return 1
    try:
        return _executar(porta)
    finally:
        desligar_logs(handlers)


if __name__ == "__main__":
    sys.exit(main())
