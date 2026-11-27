"""Configuração do portal, lida de variáveis de ambiente DLUZ_*.

DLUZ_DATA_DIR       pasta dos dados (banco do portal, arquivos gerados e,
                    por padrão, os bancos de Pedidos, CRM e Caixa). Obrigatória.
DLUZ_HTTPS          1 quando o portal estiver atrás de HTTPS (cookies "Secure").
DLUZ_FB_USER        usuário do Firebird (padrão SYSDBA).
DLUZ_FB_PASSWORD    senha do Firebird (padrão masterkey).
PEDIDOS_DATA_DIR,
CRM_DATA_DIR,
CAIXA_DATA_DIR      opcionais: reaproveitam os dados de uma instalação antiga
                    de cada sistema em vez de DLUZ_DATA_DIR/<sistema>.
"""

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SCRIPTS = RAIZ / "scripts_originais"
SISTEMAS = RAIZ / "sistemas"


class ConfigError(RuntimeError):
    pass


def pasta_dados():
    valor = (os.environ.get("DLUZ_DATA_DIR") or "").strip()
    if not valor:
        raise ConfigError(
            "Defina DLUZ_DATA_DIR com a pasta onde o portal guarda os dados "
            "(no Windows, por exemplo: setx DLUZ_DATA_DIR C:\\PortalDluzDados /M)."
        )
    pasta = Path(valor).expanduser()
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def pasta_do_sistema(pasta, sistema):
    """Pasta de dados de Pedidos/CRM/Caixa: a variável antiga, se houver, senão uma subpasta."""
    antiga = (os.environ.get(f"{sistema.upper()}_DATA_DIR") or "").strip()
    destino = Path(antiga).expanduser() if antiga else Path(pasta) / sistema
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def https():
    return (os.environ.get("DLUZ_HTTPS") or "").strip().lower() in ("1", "true", "sim")


FB_PORTA = 3050
FB_CHARSET = "WIN1252"


def fb_usuario():
    return os.environ.get("DLUZ_FB_USER", "SYSDBA")


def fb_senha():
    return os.environ.get("DLUZ_FB_PASSWORD", "masterkey")


# Bancos do ATS, como estavam escritos nos scripts. A tela "Configurações"
# permite trocar host e caminho sem mexer em código.
EMPRESAS_PADRAO = {
    "ELE": {
        "nome": "Dluz Elétrica",
        "host": "192.168.0.41",
        "real": r"D:\DLUZR\BANCO\DLUZR25.FB",
        "fiscal": r"D:\DLUZF\BANCO\DLUZF25.FB",
    },
    "COR": {
        "nome": "Cor e Cia",
        "host": "192.168.0.200",
        "real": r"D:\CorCiaR\BANCO\CORCIAR25.FB",
        "fiscal": r"D:\CorCiaF\BANCO\CORCIAF25.FB",
    },
    "ILU": {
        "nome": "Dluz Iluminação",
        "host": "192.168.0.250",
        "real": r"D:\ILUMIR\BANCO\ILUMIR.FB",
        "fiscal": r"D:\ILUMIF\BANCO\ILUMIF.FB",
    },
}

MIN_SENHA = 8
SESSAO_HORAS = 12
