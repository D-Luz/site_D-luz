"""CRM do Grupo Dluz — fábrica da aplicação."""

import os
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


def create_app(pasta_dados=None):
    app = Flask(__name__)
    pasta = Path(pasta_dados) if pasta_dados else config.pasta_dados()
    app.config.update(
        DB_PATH=pasta / "crm.sqlite",
        SECRET_KEY=_segredo(pasta),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Em rede local sem HTTPS, defina CRM_COOKIE_INSECURE=1.
        SESSION_COOKIE_SECURE=not os.environ.get("CRM_COOKIE_INSECURE"),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=1024 * 1024,
    )
    db.init_app(app)

    app.jinja_env.filters.update(
        brl=formatos.brl, data_br=formatos.data_br, fone=formatos.fone_legivel, nome1=formatos.primeiro_nome
    )
    app.jinja_env.globals.update(csrf_token=auth.token_csrf, TIPOS=config.TIPOS, EMPRESAS=config.EMPRESAS)

    app.before_request(auth.exigir_login)
    app.before_request(auth.checar_csrf)

    @app.after_request
    def cabecalhos(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
            "form-action 'self'; frame-ancestors 'none'"
        )
        return resp

    from . import rotas

    app.register_blueprint(auth.bp)
    app.register_blueprint(rotas.bp)
    return app
