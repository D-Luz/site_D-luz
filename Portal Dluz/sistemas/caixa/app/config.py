"""Configuração: pasta de dados, conexões do ATS e formas de pagamento."""

import os
from pathlib import Path


class ConfigError(RuntimeError):
    pass


def pasta_dados():
    """Pasta do banco do caixa. Obrigatória, para nunca gravar no lugar errado."""
    valor = os.environ.get("CAIXA_DATA_DIR")
    if not valor:
        raise ConfigError(
            "Defina CAIXA_DATA_DIR com a pasta onde o sistema guarda o banco "
            "(ex.: set CAIXA_DATA_DIR=C:\\CaixaDados)."
        )
    pasta = Path(valor).expanduser()
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


# Conexões do ATS (somente leitura). Mesmos servidores dos outros sistemas do grupo.
FB_PORTA = 3050
FB_CHARSET = "WIN1252"
FB_USUARIO = os.environ.get("CAIXA_FB_USER", "SYSDBA")
FB_SENHA = os.environ.get("CAIXA_FB_PASSWORD", "masterkey")

EMPRESAS = {
    "ILU": {
        "nome": "Dluz Iluminação",
        "host": "192.168.0.250",
        "banco": "D:\\ILUMIR\\BANCO\\ILUMIR.FB",
    },
    "ELE": {
        "nome": "Dluz Elétrica",
        "host": "192.168.0.41",
        "banco": "D:\\DLUZR\\BANCO\\DLUZR25.FB",
    },
}

# Formas de lançamento. "ats": é conferida contra o ATS; "aprovacao": não existe no
# ATS e precisa ser aprovada com observação; "obs": a observação é obrigatória.
FORMAS = {
    "dinheiro": {"rotulo": "Dinheiro", "tipo": "entrada", "ats": True},
    "cartao": {"rotulo": "Cartão", "tipo": "entrada", "ats": True},
    "deposito": {"rotulo": "Depósito/PIX", "tipo": "entrada", "ats": True},
    "cheque": {"rotulo": "Cheque", "tipo": "entrada", "ats": True},
    "dev_credito": {"rotulo": "Dev. crédito", "tipo": "entrada", "aprovacao": True},
    "pontos": {"rotulo": "Troca de pontos", "tipo": "entrada", "aprovacao": True},
    "cofre": {"rotulo": "Retirada p/ cofre", "tipo": "saida"},
    "pagamento": {"rotulo": "Pagamento", "tipo": "saida", "obs": True},
    "outra_saida": {"rotulo": "Outra saída", "tipo": "saida", "obs": True},
}
FORMAS_ATS = ("dinheiro", "cartao", "deposito", "cheque")

# Forma que aparece só do lado do ATS: valor de um PDV que não é de nenhuma forma
# conhecida (ticket, financeira...) ou que não fecha com o total da venda.
ROTULO_OUTROS = "Outra forma no ATS"

# Leitura do ATS guardada por este tempo (segundos) para não consultar o servidor a
# cada tecla; o botão "Atualizar ATS" ignora o cache.
ATS_CACHE_SEGUNDOS = 60

PAPEIS = {
    "caixa": "Caixa",
    "financeiro": "Financeiro",
    "admin": "Administrador",
}
