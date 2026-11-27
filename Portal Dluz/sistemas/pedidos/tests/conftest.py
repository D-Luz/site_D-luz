import itertools
import os
from dataclasses import dataclass

import pytest
from flask.testing import FlaskClient

from app import auth, create_app
from app import db as db_module


class ClienteHttps(FlaskClient):
    """Cliente de teste que fala https: o cookie de sessão é Secure."""

    def open(self, *args, **kwargs):
        kwargs.setdefault("base_url", "https://localhost")
        return super().open(*args, **kwargs)


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch):
    """Isola os testes de qualquer PEDIDOS_* definido no shell de quem roda."""
    for nome in [n for n in os.environ if n.startswith("PEDIDOS_")]:
        monkeypatch.delenv(nome)


@pytest.fixture
def app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATA_DIR": tmp_path / "data",
            "WTF_CSRF_ENABLED": False,
        }
    )
    app.test_client_class = ClienteHttps
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    with app.app_context():
        yield db_module.get_db()


SENHA_TESTE = "senha-de-teste-123"


@dataclass(frozen=True)
class UsuarioTeste:
    id: int
    nome: str
    email: str
    senha: str
    perfil: str


@pytest.fixture
def criar_usuario_teste(app):
    """Cria um usuário já com a senha trocada (a menos que se peça o contrário)."""
    contador = itertools.count(1)

    def criar(perfil="gestor", trocar_senha=False, email=None, senha=SENHA_TESTE):
        n = next(contador)
        nome = f"Usuario {n}"
        email = email or f"usuario{n}@exemplo.com"
        with app.app_context():
            usuario_id = auth.criar_usuario(nome, email, senha, perfil, trocar_senha=trocar_senha)
        return UsuarioTeste(usuario_id, nome, email, senha, perfil)

    return criar


@pytest.fixture
def entrar():
    """Faz login pelo formulário: entrar(client, email, senha) -> resposta."""

    def _entrar(client, email, senha=SENHA_TESTE):
        return client.post("/login", data={"email": email, "senha": senha})

    return _entrar
