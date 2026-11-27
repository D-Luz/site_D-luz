import re
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import firebird_falso  # noqa: E402

firebird_falso.instalar()

from portal import auth, create_app, db  # noqa: E402


@pytest.fixture(scope="session")
def app(tmp_path_factory):
    app = create_app(tmp_path_factory.mktemp("dados"))
    with app.app_context():
        conn = db.get_db()
        auth.criar_usuario(conn, "admin", "Ana Admin", "senha12345", admin=True, email="ana@dluz.test", trocar_senha=False)
        auth.criar_usuario(conn, "marcos", "Marcos Balcão", "senha12345", trocar_senha=False)
        auth.criar_usuario(conn, "novato", "Novato", "senha12345")
    return app


def _token(resp):
    m = re.search(r'name="csrf" value="([^"]+)"', resp.get_data(as_text=True))
    return m.group(1) if m else None


class Navegador:
    def __init__(self, app, login=None):
        self.c = app.test_client()
        self.csrf = None
        if login:
            tok = _token(self.c.get("/entrar"))
            self.c.post("/entrar", data={"csrf": tok, "login": login, "senha": "senha12345"})
            self.csrf = _token(self.c.get("/senha"))

    def get(self, *a, **k):
        return self.c.get(*a, **k)

    def post(self, url, data=None, **k):
        data = dict(data or {})
        data.setdefault("csrf", self.csrf)
        return self.c.post(url, data=data, **k)


@pytest.fixture
def admin(app):
    return Navegador(app, "admin")


@pytest.fixture
def marcos(app):
    return Navegador(app, "marcos")


@pytest.fixture
def anonimo(app):
    return Navegador(app)
