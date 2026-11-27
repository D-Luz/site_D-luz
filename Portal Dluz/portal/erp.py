"""Conexões do ATS (Firebird) num lugar só.

Cada script original trazia os IPs, caminhos e a senha escritos no código, cada
um num formato. Aqui fica a configuração única (editável em Configurações) e
`aplicar_no_script`, que troca nos módulos carregados os valores que vieram
escritos pelos valores configurados.
"""

import copy

from . import config, db

CHAVE_AJUSTE = "erp_empresas"


def empresas(conn):
    """{sigla: {nome, host, real, fiscal}} — o padrão com os ajustes por cima."""
    atual = copy.deepcopy(config.EMPRESAS_PADRAO)
    for sigla, valores in (db.ler_ajuste(conn, CHAVE_AJUSTE, {}) or {}).items():
        if sigla in atual:
            atual[sigla].update({k: v for k, v in valores.items() if k in ("host", "real", "fiscal") and v})
    return atual


def salvar(conn, valores):
    db.gravar_ajuste(conn, CHAVE_AJUSTE, valores)


def _trocas(emp):
    """Valor escrito no script -> valor configurado (hosts e caminhos de banco)."""
    trocas = {}
    for sigla, padrao in config.EMPRESAS_PADRAO.items():
        atual = emp[sigla]
        for campo in ("host", "real", "fiscal"):
            trocas[padrao[campo].lower()] = atual[campo]
    return trocas


def _trocar(valor, trocas):
    if isinstance(valor, dict):
        return {k: _trocar(v, trocas) for k, v in valor.items()}
    if isinstance(valor, str):
        return trocas.get(valor.lower(), valor)
    return valor


def aplicar_no_script(mod, emp):
    """Injeta host, banco, usuário e senha configurados num script carregado."""
    if isinstance(getattr(mod, "EMPRESAS", None), dict):
        mod.EMPRESAS = _trocar(mod.EMPRESAS, _trocas(emp))
    if hasattr(mod, "USER"):
        mod.USER = config.fb_usuario()
    if hasattr(mod, "PASSWORD"):
        mod.PASSWORD = config.fb_senha()
    if hasattr(mod, "PORT"):
        mod.PORT = config.FB_PORTA
    return mod


def chave_da_empresa(nome):
    """Sigla (ELE/COR/ILU) a partir do nome usado num script ('Dluz Elétrica (Real)', 'D.LUZ Iluminação'...)."""
    n = (nome or "").lower()
    if "cor" in n and "cia" in n:
        return "COR"
    if "ilumina" in n:
        return "ILU"
    if "tric" in n:
        return "ELE"
    return None
