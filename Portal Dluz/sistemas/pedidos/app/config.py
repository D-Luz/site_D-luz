"""Configuração padrão e leitura de variáveis de ambiente.

Toda chave pode ser trocada por uma variável de ambiente chamada
``PEDIDOS_`` + chave (ex.: ``PEDIDOS_DATA_DIR``). Os ``overrides`` passados
para ``create_app`` vencem as variáveis de ambiente.

Outras variáveis PEDIDOS_* que não passam por aqui: as credenciais do e-mail
(``PEDIDOS_SMTP_HOST``, ``_PORTA``, ``_USUARIO``, ``_SENHA`` e ``_REMETENTE``,
lidas em ``notificacoes.py``), ``PEDIDOS_PORTA`` (porta do servidor, lida por
``scripts/iniciar.py``) e ``PEDIDOS_BACKUP_DESTINO`` (lida por
``scripts/backup_pedidos.bat``).
"""

import os
from pathlib import Path

PREFIXO_ENV = "PEDIDOS_"
RAIZ = Path(__file__).resolve().parent.parent

PADROES = {
    "DATA_DIR": RAIZ / "data",
    "SECRET_KEY": None,  # variável de ambiente ou DATA_DIR/secret.key
    "MAX_PDF_MB": 20,
    "BASE_URL": "http://localhost:8000",
    "EMAIL_MODE": "desligado",  # desligado | smtp
    "SESSION_LIFETIME": 8 * 3600,  # segundos
    "LOGIN_MAX_FALHAS": 5,
    "BLOQUEIO_MIN": 15,
    "MIN_SENHA": 10,
    "COOKIE_INSECURE": False,  # só para rodar localmente por http
    # Dentro do Portal Dluz o login é o do portal (veja portal.py).
    "PORTAL": False,
    "PORTAL_ENTRAR": "/entrar",
    "PORTAL_SAIR": "/sair",
    "PORTAL_SENHA": "/senha",
}

MODOS_EMAIL = ("desligado", "smtp")

# Os scripts de operação (iniciar, criar_admin, backup) exigem PEDIDOS_DATA_DIR:
# sem ela usariam a pasta data/ do próprio programa e, por engano (por exemplo
# uma tarefa que não enxerga a variável), criariam ou copiariam um banco no
# lugar errado.
MSG_SEM_DATA_DIR = (
    "Erro: a variável de ambiente PEDIDOS_DATA_DIR não está definida.\n"
    "Ela diz onde ficam o banco e os PDFs (no Windows: C:\\PedidosDados). "
    "Defina-a como variável do SISTEMA (veja scripts\\instalar_windows.md, "
    "passo 4) e reinicie o PC; para um teste rápido, num PowerShell: "
    '$env:PEDIDOS_DATA_DIR = "C:\\PedidosDados". '
    "Nada foi criado."
)


def data_dir_definido(environ=None):
    """True se PEDIDOS_DATA_DIR está definida e não está em branco."""
    environ = os.environ if environ is None else environ
    return bool((environ.get(PREFIXO_ENV + "DATA_DIR") or "").strip())


def _converter(chave, texto):
    padrao = PADROES[chave]
    if isinstance(padrao, bool):
        return texto.strip().lower() in ("1", "true", "sim", "yes")
    if isinstance(padrao, int):
        return int(texto)
    if isinstance(padrao, Path):
        return Path(texto)
    return texto


def carregar(overrides=None):
    """Devolve o dicionário de configuração: padrões < ambiente < overrides."""
    config = dict(PADROES)
    for chave in PADROES:
        valor = os.environ.get(PREFIXO_ENV + chave)
        if valor is not None:
            config[chave] = _converter(chave, valor)
    config.update(overrides or {})

    config["DATA_DIR"] = Path(config["DATA_DIR"])
    if config["EMAIL_MODE"] not in MODOS_EMAIL:
        raise ValueError(f"EMAIL_MODE deve ser um de {MODOS_EMAIL}, não {config['EMAIL_MODE']!r}")
    return config
