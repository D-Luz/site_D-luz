"""Pedidos, CRM e Caixa dentro do portal, num endereço só.

Cada sistema continua sendo a aplicação Flask original (com seu banco, suas
regras e seus testes), carregada aqui e servida sob um prefixo: /pedidos,
/crm e /caixa. Antes de repassar a requisição, o portal confere o login dele
e grava quem é o usuário em `environ["dluz.portal.usuario"]` — cada sistema lê
essa chave (veja app/portal.py de cada um) em vez de pedir login de novo.
As páginas HTML dos sistemas ganham no topo a barra de navegação do portal.
"""

import functools
import html
import importlib.util
import os
import sqlite3
import sys
import threading
import time
import traceback
from urllib.parse import quote

from werkzeug.utils import redirect as redirecionar

from . import auth, config, db, erp

CHAVE_ENVIRON = "dluz.portal.usuario"

SISTEMAS = {
    "pedidos": {"prefixo": "/pedidos", "titulo": "Aprovação de Pedidos", "curto": "Pedidos", "pasta": "pedidos",
                "descricao": "Do pedido com PDF até a chegada da mercadoria: aprovação do gestor, nota fiscal e histórico."},
    "crm": {"prefixo": "/crm", "titulo": "CRM Dluz", "curto": "CRM", "pasta": "crm",
            "descricao": "Fila diária de contatos por WhatsApp, eletricistas e pontos, orçamentos em aberto e painel."},
    "caixa": {"prefixo": "/caixa", "titulo": "Conferência de Caixa", "curto": "Caixa", "pasta": "caixa",
              "descricao": "Lançamentos do caixa conferidos com o ATS na hora, fechamento do dia e histórico."},
}


def _carregar_pacote(alias, pasta_app):
    """Importa a pasta app/ de um sistema com outro nome (os três se chamam 'app')."""
    if alias in sys.modules:
        return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(
        alias, pasta_app / "__init__.py", submodule_search_locations=[str(pasta_app)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        for nome in [n for n in sys.modules if n == alias or n.startswith(alias + ".")]:
            del sys.modules[nome]
        raise
    return mod


def _comum(app_sis, prefixo):
    app_sis.config.update(
        PORTAL=True,
        PORTAL_ENTRAR="/entrar",
        PORTAL_SAIR="/sair",
        PORTAL_SENHA="/senha",
        SESSION_COOKIE_NAME="dluz_" + prefixo.strip("/"),
        SESSION_COOKIE_PATH=prefixo,
        SESSION_COOKIE_SECURE=config.https(),
    )
    return app_sis


def _criar_pedidos(pasta_dados):
    pacote = _carregar_pacote("dluz_sis_pedidos", config.SISTEMAS / "pedidos" / "app")
    overrides = {
        "DATA_DIR": config.pasta_do_sistema(pasta_dados, "pedidos"),
        "PORTAL": True,
        "COOKIE_INSECURE": not config.https(),
    }
    base = (os.environ.get("DLUZ_BASE_URL") or "").rstrip("/")
    if base and not os.environ.get("PEDIDOS_BASE_URL"):
        overrides["BASE_URL"] = base + "/pedidos"  # links dos e-mails
    return _comum(pacote.create_app(overrides), "/pedidos")


def _criar_crm(pasta_dados):
    os.environ.setdefault("CRM_FB_USER", config.fb_usuario())
    os.environ.setdefault("CRM_FB_PASSWORD", config.fb_senha())
    pacote = _carregar_pacote("dluz_sis_crm", config.SISTEMAS / "crm" / "app")
    return _comum(pacote.create_app(config.pasta_do_sistema(pasta_dados, "crm")), "/crm")


def _criar_caixa(pasta_dados):
    os.environ.setdefault("CAIXA_FB_USER", config.fb_usuario())
    os.environ.setdefault("CAIXA_FB_PASSWORD", config.fb_senha())
    pacote = _carregar_pacote("dluz_sis_caixa", config.SISTEMAS / "caixa" / "app")
    return _comum(pacote.create_app(config.pasta_do_sistema(pasta_dados, "caixa")), "/caixa")


FABRICAS = {"pedidos": _criar_pedidos, "crm": _criar_crm, "caixa": _criar_caixa}


def aplicar_erp_nos_sistemas(empresas):
    """CRM e Caixa têm a própria lista de bancos do ATS: mantém em dia com Configurações."""
    for alias in ("dluz_sis_crm", "dluz_sis_caixa"):
        cfg = sys.modules.get(alias + ".config")
        if cfg is None:
            continue
        for sigla, dados in cfg.EMPRESAS.items():
            if sigla in empresas:
                dados["host"] = empresas[sigla]["host"]
                dados["banco"] = empresas[sigla]["real"]


def remover_dos_sistemas(data_dir, login, email):
    """Ao excluir um usuário do portal: apaga o cadastro dele em cada sistema.

    Quem já tem histórico num sistema (pedidos, lançamentos de caixa, contatos)
    não pode ser apagado lá sem apagar o histórico junto; nesse caso o cadastro
    fica desativado. Devolve avisos para mostrar ao administrador.
    """
    alvos = [
        ("pedidos", "Pedidos", "pedidos.db", "usuarios", "email", (email or f"{login}@portal.local").lower()),
        ("crm", "CRM", "crm.sqlite", "usuario", "login", login.lower()),
        ("caixa", "Caixa", "caixa.sqlite", "usuario", "login", login.lower()),
    ]
    mantidos = []
    for sistema, rotulo, arquivo, tabela, coluna, valor in alvos:
        caminho = config.pasta_do_sistema(data_dir, sistema) / arquivo
        if not caminho.exists():
            continue
        conn = sqlite3.connect(caminho, timeout=30)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                conn.execute(f"DELETE FROM {tabela} WHERE lower({coluna}) = ?", (valor,))
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                conn.execute(f"UPDATE {tabela} SET ativo = 0 WHERE lower({coluna}) = ?", (valor,))
                conn.commit()
                mantidos.append(rotulo)
        finally:
            conn.close()
    if mantidos:
        return [f"No {', '.join(mantidos)} ele tem histórico: o cadastro lá ficou desativado, e o histórico foi mantido."]
    return []


def montar(app_portal):
    """Carrega os sistemas e põe o despachante na frente do portal. Devolve {nome: erro}."""
    montados, erros = {}, {}
    for nome, info in SISTEMAS.items():
        try:
            montados[info["prefixo"]] = (nome, FABRICAS[nome](app_portal.config["DATA_DIR"]))
        except Exception as exc:
            erros[nome] = f"{type(exc).__name__}: {exc}"
            print(f"[portal] {info['titulo']} não pôde ser carregado:\n{traceback.format_exc()}", file=sys.stderr)
    gravar_login = functools.partial(auth.gravar_login_externo, app_portal.config["DB_PATH"])
    for _nome, app_sis in montados.values():
        app_sis.config["PORTAL_GRAVAR_LOGIN"] = gravar_login
    with app_portal.app_context():
        aplicar_erp_nos_sistemas(erp.empresas(db.get_db()))
    app_portal.config["SISTEMAS_MONTADOS"] = {n for n, _ in montados.values()}
    app_portal.config["SISTEMAS_ERROS"] = erros
    app_portal.wsgi_app = Despachante(app_portal, app_portal.wsgi_app, montados)
    return erros


class Despachante:
    """WSGI: /pedidos, /crm e /caixa vão para os sistemas; o resto, para o portal."""

    def __init__(self, app_portal, wsgi_portal, montados):
        self.app = app_portal
        self.wsgi_portal = wsgi_portal
        self.montados = montados  # {prefixo: (nome, app)}

    def __call__(self, environ, start_response):
        environ.pop(CHAVE_ENVIRON, None)
        caminho = environ.get("PATH_INFO") or "/"
        for prefixo, (nome, app_sis) in self.montados.items():
            if caminho == prefixo or caminho.startswith(prefixo + "/"):
                return self._sistema(prefixo, nome, app_sis, environ, start_response)
        return self.wsgi_portal(environ, start_response)

    def _usuario(self, environ):
        with self.app.request_context(environ):
            u = auth.usuario_da_sessao(db.get_db())
            return None if u is None else dict(u)

    def _sistema(self, prefixo, nome, app_sis, environ, start_response):
        u = self._usuario(environ)
        caminho = environ.get("PATH_INFO") or "/"
        if u is None:
            consulta = environ.get("QUERY_STRING")
            destino = environ.get("SCRIPT_NAME", "") + caminho + (f"?{consulta}" if consulta else "")
            return redirecionar("/entrar?proximo=" + quote(destino, safe="/"))(environ, start_response)
        if u["trocar_senha"]:
            return redirecionar("/senha")(environ, start_response)
        environ[CHAVE_ENVIRON] = {
            "login": u["login"], "nome": u["nome"], "email": u["email"], "admin": bool(u["admin"]),
        }
        environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + prefixo
        environ["PATH_INFO"] = caminho[len(prefixo):] or "/"
        if environ["PATH_INFO"] == "/" and caminho == prefixo:
            # /pedidos -> /pedidos/ (links relativos do sistema contam com a barra)
            return redirecionar(prefixo + "/")(environ, start_response)
        return _com_barra(app_sis, nome, u)(environ, start_response)


def barra_html(atual, usuario):
    itens = [("inicio", "/", "Início"), ("ferramentas", "/ferramentas", "Ferramentas"), ("painel", "/paineis", "Painel")]
    itens += [(n, i["prefixo"] + "/", i["curto"]) for n, i in SISTEMAS.items()]
    links = "".join(
        f'<a href="{h}"{" class=\"atual\"" if n == atual else ""}>{html.escape(t)}</a>' for n, h, t in itens
    )
    return (
        '<link rel="stylesheet" href="/static/barra.css">'
        '<nav class="dluz-portal-barra" aria-label="Portal Dluz">'
        '<a class="marca" href="/"><img src="/static/logo.png" alt="D.Luz"></a>'
        f'<span class="links">{links}</span>'
        f'<span class="quem">{html.escape(usuario["nome"].split(" ")[0])} · <a href="/sair">Sair</a></span>'
        "</nav>"
    ).encode("utf-8")


def _com_barra(app_sis, nome, usuario):
    """Envolve o sistema: nas respostas HTML, insere a barra logo depois de <body>."""

    def wsgi(environ, start_response):
        estado = {}

        def capturar(status, cabecalhos, exc_info=None):
            tipo = next((v for k, v in cabecalhos if k.lower() == "content-type"), "")
            if tipo.startswith("text/html") and not any(k.lower() == "content-encoding" for k, _ in cabecalhos):
                estado["adiar"] = (status, cabecalhos, exc_info)
                return lambda dado: estado.setdefault("pedacos", []).append(dado)
            return start_response(status, cabecalhos, exc_info)

        resposta = app_sis(environ, capturar)
        if "adiar" not in estado:
            return resposta
        try:
            corpo = b"".join(list(estado.get("pedacos", [])) + list(resposta))
        finally:
            if hasattr(resposta, "close"):
                resposta.close()
        status, cabecalhos, exc_info = estado["adiar"]
        i = corpo.find(b"<body")
        if i >= 0:
            fim = corpo.find(b">", i)
            if fim >= 0:
                corpo = corpo[: fim + 1] + barra_html(nome, usuario) + corpo[fim + 1:]
        cabecalhos = [(k, v) for k, v in cabecalhos if k.lower() != "content-length"]
        cabecalhos.append(("Content-Length", str(len(corpo))))
        start_response(status, cabecalhos, exc_info)
        return [corpo]

    return wsgi


def iniciar_sincronizacao_crm(app_portal, intervalo_min=60):
    """Sincroniza o CRM com o ATS de hora em hora, das 7h às 20h (antes: tarefa agendada no Windows)."""
    sinc = sys.modules.get("dluz_sis_crm.sincronizar")
    crm_db = sys.modules.get("dluz_sis_crm.db")
    if sinc is None or crm_db is None:
        return None
    caminho = config.pasta_do_sistema(app_portal.config["DATA_DIR"], "crm") / "crm.sqlite"

    def laco():
        while True:
            if 7 <= time.localtime().tm_hour < 20:
                try:
                    conn = crm_db.conectar(caminho)
                    try:
                        crm_db.criar_esquema(conn)
                        sinc.sincronizar(conn)
                    finally:
                        conn.close()
                except Exception as exc:
                    print(f"[portal] sincronização do CRM falhou: {exc}", file=sys.stderr)
            time.sleep(intervalo_min * 60)

    t = threading.Thread(target=laco, name="sincroniza-crm", daemon=True)
    t.start()
    return t
