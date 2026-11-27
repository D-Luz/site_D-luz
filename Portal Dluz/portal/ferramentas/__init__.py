"""Registro das ferramentas do portal.

Cada ferramenta descreve o formulário (campos) e uma função `executar(ctx)` que
chama o script original. Para acrescentar uma ferramenta nova, crie a função
num dos módulos desta pasta e registre-a com `@ferramenta(...)`.
"""

from dataclasses import dataclass, field
from datetime import date, datetime

GRUPOS = [
    ("vendas", "Vendas e orçamentos"),
    ("financeiro", "Financeiro"),
    ("estoque", "Estoque e compras"),
    ("bancos", "Bancos e fiscal"),
    ("paineis", "Painéis"),
]

AREAS_DESCRICAO = {
    "vendas": "Orçamentos, vendas do PDV e a proposta comercial em PDF.",
    "financeiro": "Contas a pagar e a receber, acompanhamento semanal e receita do ano.",
    "estoque": "Curva ABC, posição de estoque e custo da última nota de compra.",
    "bancos": "Extratos bancários, Pix do Inter e notas fiscais em XML.",
    "paineis": "Painel ATS do grupo.",
}

# Ícone de cada área, ferramenta e sistema (desenhos em templates/_icones.html)
ICONES = {
    "vendas": "etiqueta", "financeiro": "carteira", "estoque": "caixa-pacote", "bancos": "banco", "paineis": "painel",
    "pedidos": "lista-check", "crm": "conversa", "caixa": "dinheiro",
    "orcamentos-baixados": "prancheta", "orcamentos-vendedor": "pessoas", "imprimir-orcamento": "impressora",
    "venda-mensal": "linha", "vendas-pdv": "carrinho", "somatorio-pdv": "calculadora",
    "planilha-orcamento": "planilha", "proposta-orcamento": "documento",
    "titulos": "setas", "receber-em-aberto": "ampulheta", "acompanhamento-semanal": "calendario",
    "receita-mensal-comparativo": "barras",
    "analise-estoque": "camadas", "posicao-estoque": "caixa-pacote", "ultima-nf-compra": "recibo",
    "ultima-nf-compra-fiscal": "carimbo", "validar-preconf": "escudo",
    "pix-inter": "raio", "consolidar-extratos": "banco", "nfe-excel": "codigo",
    "painel-ats": "painel",
}

REGISTRO = {}


@dataclass
class Campo:
    nome: str
    rotulo: str
    tipo: str = "texto"  # texto | numero | data | empresa | escolha | marcar | arquivo | arquivos | itens
    obrigatorio: bool = True
    padrao: object = None
    opcoes: object = field(default_factory=list)  # [(valor, rótulo)] ou função que devolve a lista
    ajuda: str = ""
    aceita: str = ""  # extensões para upload, ex.: ".pdf"


@dataclass
class Ferramenta:
    id: str
    nome: str
    grupo: str
    descricao: str
    campos: list
    executar: object
    origem: str = ""  # script original em que se baseia
    detalhes: str = ""


def opcoes_de(campo):
    """Lista de opções do campo (algumas são montadas na hora, como os vendedores)."""
    return list(campo.opcoes() if callable(campo.opcoes) else campo.opcoes)


def ferramenta(id, nome, grupo, descricao, campos=(), origem="", detalhes=""):
    def registrar(func):
        REGISTRO[id] = Ferramenta(id, nome, grupo, descricao, list(campos), func, origem, detalhes)
        return func

    return registrar


# Opções de empresa mais usadas
EMP_TODAS = [("ELE", "Dluz Elétrica"), ("COR", "Cor e Cia"), ("ILU", "Dluz Iluminação")]
EMP_ELE_ILU = [("ELE", "Dluz Elétrica"), ("ILU", "Dluz Iluminação")]
TODAS = "TODAS"


def primeiro_do_mes():
    return date.today().replace(day=1).isoformat()


def hoje():
    return date.today().isoformat()


def para_data(texto):
    return datetime.strptime(texto, "%Y-%m-%d").date()


def carregar_todas():
    # A ordem dos imports é a ordem em que aparecem nas telas.
    from . import vendas, financeiro, estoque, bancos, paineis, impressao  # noqa: F401, I001

    return REGISTRO
