"""Portal Dluz — fábrica da aplicação."""

import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask

from . import auth, config, db, formatos


def _segredo(pasta):
    arq = Path(pasta) / "segredo.key"
    if not arq.exists():
        arq.write_text(secrets.token_hex(32))
        try:
            arq.chmod(0o600)
        except OSError:
            pass
    return arq.read_text().strip()


def create_app(pasta_dados=None, montar_sistemas=True):
    app = Flask(__name__)
    pasta = Path(pasta_dados) if pasta_dados else config.pasta_dados()
    pasta.mkdir(parents=True, exist_ok=True)
    app.config.update(
        DATA_DIR=pasta,
        DB_PATH=pasta / "portal.sqlite",
        SECRET_KEY=_segredo(pasta),
        SESSION_COOKIE_NAME="dluz_sessao",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.https(),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=config.SESSAO_HORAS),
        MAX_CONTENT_LENGTH=200 * 1024 * 1024,  # extratos em PDF podem ser grandes
        SISTEMAS_MONTADOS=set(),
        SISTEMAS_ERROS={},
    )
    db.init_app(app)

    app.jinja_env.filters.update(brl=formatos.brl, momento=formatos.momento, tamanho=formatos.tamanho)
    app.jinja_env.globals.update(csrf_token=auth.token_csrf)

    app.before_request(auth.carregar_usuario)
    app.before_request(auth.checar_csrf)

    @app.after_request
    def cabecalhos(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        # As páginas geradas (painéis, tabelas) são mostradas num <iframe> do próprio portal.
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'",
        )
        return resp

    from . import rotas

    app.register_blueprint(auth.bp)
    app.register_blueprint(rotas.bp)

    if montar_sistemas:
        from . import integracao

        integracao.montar(app)
    return app
