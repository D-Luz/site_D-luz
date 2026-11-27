import os
import secrets
from datetime import timedelta

from flask import Flask
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from . import admin, auth, config, db, pedidos

csrf = CSRFProtect()

CABECALHOS_DE_SEGURANCA = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; object-src 'none'; "
        "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    ),
}


TAMANHO_MINIMO_CHAVE = 32


def _ler_chave(caminho):
    chave = caminho.read_text(encoding="ascii").strip()
    if len(chave) < TAMANHO_MINIMO_CHAVE:
        raise RuntimeError(
            f"{caminho} está vazio ou corrompido (mínimo de {TAMANHO_MINIMO_CHAVE} "
            "caracteres). Apague o arquivo para gerar uma chave nova; isso encerra "
            "as sessões abertas."
        )
    return chave


def _chave_secreta(data_dir):
    """Lê a chave de DATA_DIR/secret.key, gerando-a na primeira execução.

    A criação é atômica (O_EXCL) e já com permissão 0600: sem janela em que
    outro usuário leia o arquivo, e dois processos nunca gravam chaves
    diferentes (quem perde a corrida lê a chave de quem ganhou).
    """
    caminho = data_dir / "secret.key"
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        descritor = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _ler_chave(caminho)
    chave = secrets.token_hex(32)
    with os.fdopen(descritor, "w", encoding="ascii") as arquivo:
        arquivo.write(chave)
    return chave


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.update(config.carregar(overrides))

    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = _chave_secreta(app.config["DATA_DIR"])

    app.config.update(
        PERMANENT_SESSION_LIFETIME=timedelta(seconds=app.config["SESSION_LIFETIME"]),
        SESSION_COOKIE_SECURE=not app.config["COOKIE_INSECURE"],
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # O prazo de 8 h é absoluto (hora do login na sessão), não deslizante.
        SESSION_REFRESH_EACH_REQUEST=False,
        # O token CSRF vale tanto quanto a sessão (8 h), não só 1 h.
        WTF_CSRF_TIME_LIMIT=None,
        # Um megabyte além do PDF máximo, para caber os demais campos do formulário;
        # acima disso a requisição é recusada com 413 antes de ser lida.
        MAX_CONTENT_LENGTH=(app.config["MAX_PDF_MB"] + 1) * 1024 * 1024,
    )

    # Atrás do Tailscale Funnel: reconhece HTTPS, host e IP reais.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    csrf.init_app(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(pedidos.bp)
    pedidos.configurar(app)
    app.before_request(auth.carregar_usuario)

    app.teardown_appcontext(db.fechar_db)
    with app.app_context():
        db.init_db()

    @app.after_request
    def cabecalhos_de_seguranca(resposta):
        for nome, valor in CABECALHOS_DE_SEGURANCA.items():
            resposta.headers.setdefault(nome, valor)
        return resposta

    return app
