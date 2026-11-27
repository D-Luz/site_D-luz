"""Regras de estado do pedido (spec, seções 3 e 4).

Módulo puro: não conhece banco, Flask nem datas. Só diz qual é o próximo
status de um pedido e quem pode dar cada passo.
"""

from types import MappingProxyType

STATUS = (
    "pendente", "aprovado", "reprovado", "enviado",
    "faturado", "recebido", "arquivado",
)
PERFIS = ("solicitante", "gestor", "fiscal", "admin")
ACOES = (
    "aprovar", "reprovar", "confirmar_envio",
    "informar_nf", "marcar_chegada", "arquivar",
)

# (status atual, ação) -> (novo status, perfis permitidos)
TRANSICOES = MappingProxyType({
    ("pendente", "aprovar"): ("aprovado", frozenset({"gestor", "admin"})),
    ("pendente", "reprovar"): ("reprovado", frozenset({"gestor", "admin"})),
    ("aprovado", "confirmar_envio"): ("enviado", frozenset({"solicitante"})),
    ("enviado", "informar_nf"): ("faturado", frozenset({"fiscal", "admin"})),
    ("faturado", "marcar_chegada"): ("recebido", frozenset({"fiscal", "admin"})),
    ("recebido", "arquivar"): ("arquivado", frozenset({"fiscal", "admin"})),
    ("reprovado", "arquivar"): ("arquivado", frozenset({"fiscal", "admin"})),
})

# Ações que só o solicitante dono do pedido pode fazer.
EXIGEM_DONO = frozenset({"confirmar_envio"})


class WorkflowError(Exception):
    """Base das falhas de transição de estado."""


class TransicaoInvalida(WorkflowError):
    """A ação não existe ou não se aplica ao status atual do pedido."""


class NaoAutorizado(WorkflowError):
    """O perfil (ou quem não é dono do pedido) não pode fazer esta ação."""


def proximo_status(status, acao, perfil, eh_dono=False):
    """Devolve o novo status ou levanta TransicaoInvalida / NaoAutorizado."""
    transicao = TRANSICOES.get((status, acao))
    if transicao is None:
        raise TransicaoInvalida(
            f"A ação '{acao}' não se aplica a um pedido com status '{status}'."
        )
    novo_status, perfis = transicao
    if perfil not in perfis:
        raise NaoAutorizado(
            f"O perfil '{perfil}' não pode executar a ação '{acao}'."
        )
    if acao in EXIGEM_DONO and not eh_dono:
        raise NaoAutorizado(
            f"Só o dono do pedido pode executar a ação '{acao}'."
        )
    return novo_status


def acoes_disponiveis(status, perfil, eh_dono=False):
    """Ações que `proximo_status` aceitaria, na ordem de ACOES."""
    disponiveis = []
    for acao in ACOES:
        try:
            proximo_status(status, acao, perfil, eh_dono)
        except WorkflowError:
            continue
        disponiveis.append(acao)
    return disponiveis


def titulo_pedido(pedido_id):
    """Título que identifica o pedido em telas, avisos e e-mails."""
    return f"Novo Pedido - {pedido_id}"
