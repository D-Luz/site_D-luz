"""Configuração: pasta de dados, conexões do ERP e valores padrão do CRM."""

import os
from pathlib import Path


class ConfigError(RuntimeError):
    pass


def pasta_dados():
    """Pasta do banco do CRM. Obrigatória, para nunca gravar no lugar errado."""
    valor = os.environ.get("CRM_DATA_DIR")
    if not valor:
        raise ConfigError(
            "Defina CRM_DATA_DIR com a pasta onde o CRM guarda o banco "
            "(ex.: export CRM_DATA_DIR=~/crm-dados)."
        )
    pasta = Path(valor).expanduser()
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


# Conexões do ERP (somente leitura). Mesmos servidores dos outros scripts do grupo.
FB_PORTA = 3050
FB_CHARSET = "WIN1252"
FB_USUARIO = os.environ.get("CRM_FB_USER", "SYSDBA")
FB_SENHA = os.environ.get("CRM_FB_PASSWORD", "masterkey")

EMPRESAS = {
    "ELE": {
        "nome": "Dluz Elétrica",
        "host": "192.168.0.41",
        "banco": "D:\\DLUZR\\BANCO\\DLUZR25.FB",
    },
    "ILU": {
        "nome": "Dluz Iluminação",
        "host": "192.168.0.250",
        "banco": "D:\\ILUMIR\\BANCO\\ILUMIR.FB",
    },
}

# Cliente genérico do PDV ("consumidor"): venda sem ninguém para contatar.
def cliente_anonimo(codigo):
    return not (codigo or "").strip().strip("0")


# Valores padrão (editáveis na tela de configurações).
PADROES = {
    "loja": "Dluz",
    "pontos_percentual": "1.0",       # % da compra devolvido ao eletricista em pontos
    "nivel_a": "8000",                # compras nos últimos 12 meses para nível A
    "nivel_b": "2500",                # ... para nível B (calibrado com as compras no próprio nome, jun/2026)
    "pos_venda_minimo": "100",        # compra mínima para pedir avaliação
    "parado_minimo": "300",           # histórico mínimo para entrar na lista de parados
    "parado_por_dia": "15",           # máximo de clientes parados sugeridos por dia
    "orcamento_dias": "14",           # até quantos dias um orçamento segue na fila
    "segmentos_eletricista": "039",   # códigos de segmento do ERP (separados por vírgula)
    "janela_vendas_dias": "730",      # quanto histórico de vendas importar
}

MODELOS = {
    "ORC": (
        "Olá {nome}, tudo bem? Aqui é da {loja}. Passando para saber se você conseguiu "
        "avaliar o orçamento de {valor} que montamos. Posso ajustar algum item ou condição?"
    ),
    "POS": (
        "Olá {nome}, aqui é da {loja}. Queria saber se deu tudo certo com o material "
        "que você levou e se ficou faltando alguma coisa para a obra. Qualquer coisa estamos à disposição."
    ),
    "PARADO": (
        "Olá {nome}, aqui é da {loja}. Faz um tempo que não nos vemos e queria saber como "
        "andam seus projetos. Chegaram novidades em iluminação e material elétrico, posso te mostrar?"
    ),
    "RISCO": (
        "Fala {nome}, aqui é da {loja}. Notei que faz um tempo que você não passa por aqui. "
        "Aconteceu alguma coisa ou ficou faltando algo no atendimento? Quero entender para melhorar."
    ),
    "ACOMP": (
        "Fala {nome}, aqui é da {loja}. Passando para saber como estão as obras e se tem "
        "alguma lista de material para eu já deixar separada para você."
    ),
    "EXTRATO": (
        "Fala {nome}, aqui é da {loja}. Seu saldo no programa de pontos está em {saldo}. "
        "Quer usar na próxima compra? É só avisar que já deixo lançado."
    ),
    "ANIV": (
        "Olá {nome}, aqui é da {loja}. Feliz aniversário! Que seja um ano de muito trabalho e saúde. "
        "Um abraço de toda a equipe."
    ),
}

TIPOS = {
    "ORC": "Orçamento",
    "POS": "Pós-venda",
    "PARADO": "Cliente parado",
    "RISCO": "Eletricista em risco",
    "ACOMP": "Acompanhamento",
    "EXTRATO": "Extrato de pontos",
    "ANIV": "Aniversário",
}
