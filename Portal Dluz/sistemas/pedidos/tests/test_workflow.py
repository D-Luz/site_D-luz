import itertools

import pytest

from app import workflow
from app.workflow import (
    ACOES,
    PERFIS,
    STATUS,
    NaoAutorizado,
    TransicaoInvalida,
    WorkflowError,
    acoes_disponiveis,
    proximo_status,
)

# Tabela esperada, escrita à mão a partir da seção 4 da spec e independente
# da implementação: (status, ação) -> (novo status, perfis que podem fazer).
ESPERADO = {
    ("pendente", "aprovar"): ("aprovado", {"gestor", "admin"}),
    ("pendente", "reprovar"): ("reprovado", {"gestor", "admin"}),
    ("aprovado", "confirmar_envio"): ("enviado", {"solicitante"}),
    ("enviado", "informar_nf"): ("faturado", {"fiscal", "admin"}),
    ("faturado", "marcar_chegada"): ("recebido", {"fiscal", "admin"}),
    ("recebido", "arquivar"): ("arquivado", {"fiscal", "admin"}),
    ("reprovado", "arquivar"): ("arquivado", {"fiscal", "admin"}),
}


def resultado_esperado(status, acao, perfil, eh_dono):
    """Devolve o novo status ou a exceção esperada, sem usar o módulo testado."""
    if (status, acao) not in ESPERADO:
        return TransicaoInvalida
    novo, perfis = ESPERADO[(status, acao)]
    if perfil not in perfis:
        return NaoAutorizado
    if acao == "confirmar_envio" and not eh_dono:
        return NaoAutorizado
    return novo


def test_constantes():
    assert STATUS == (
        "pendente", "aprovado", "reprovado", "enviado",
        "faturado", "recebido", "arquivado",
    )
    assert PERFIS == ("solicitante", "gestor", "fiscal", "admin")
    assert ACOES == (
        "aprovar", "reprovar", "confirmar_envio",
        "informar_nf", "marcar_chegada", "arquivar",
    )


def test_excecoes_compartilham_base():
    assert issubclass(TransicaoInvalida, WorkflowError)
    assert issubclass(NaoAutorizado, WorkflowError)
    assert not issubclass(TransicaoInvalida, NaoAutorizado)
    assert not issubclass(NaoAutorizado, TransicaoInvalida)


@pytest.mark.parametrize(
    "status, acao, perfil, eh_dono, novo",
    [
        ("pendente", "aprovar", "gestor", False, "aprovado"),
        ("pendente", "reprovar", "gestor", False, "reprovado"),
        ("aprovado", "confirmar_envio", "solicitante", True, "enviado"),
        ("enviado", "informar_nf", "fiscal", False, "faturado"),
        ("enviado", "informar_nf", "admin", False, "faturado"),
        ("faturado", "marcar_chegada", "fiscal", False, "recebido"),
        ("faturado", "marcar_chegada", "admin", False, "recebido"),
        ("recebido", "arquivar", "fiscal", False, "arquivado"),
        ("recebido", "arquivar", "admin", False, "arquivado"),
        ("reprovado", "arquivar", "fiscal", False, "arquivado"),
        ("reprovado", "arquivar", "admin", False, "arquivado"),
    ],
)
def test_transicoes_da_spec(status, acao, perfil, eh_dono, novo):
    assert proximo_status(status, acao, perfil, eh_dono) == novo


def test_estado_errado_levanta_transicao_invalida():
    with pytest.raises(TransicaoInvalida):
        proximo_status("enviado", "aprovar", "gestor")


def test_acao_fora_do_estado_vem_antes_da_checagem_de_perfil():
    # Perfil também está errado, mas o estado é checado primeiro.
    with pytest.raises(TransicaoInvalida):
        proximo_status("enviado", "aprovar", "solicitante")


def test_perfil_errado_levanta_nao_autorizado():
    with pytest.raises(NaoAutorizado):
        proximo_status("pendente", "aprovar", "solicitante")
    with pytest.raises(NaoAutorizado):
        proximo_status("enviado", "informar_nf", "gestor")


def test_admin_aprova_e_reprova_como_o_gestor():
    assert proximo_status("pendente", "aprovar", "admin") == "aprovado"
    assert proximo_status("pendente", "reprovar", "admin") == "reprovado"
    for acao in ("aprovar", "reprovar"):
        assert proximo_status("pendente", acao, "admin") == proximo_status("pendente", acao, "gestor")


@pytest.mark.parametrize("perfil", ["solicitante", "fiscal"])
def test_solicitante_e_fiscal_continuam_sem_aprovar_nem_reprovar(perfil):
    for acao in ("aprovar", "reprovar"):
        with pytest.raises(NaoAutorizado):
            proximo_status("pendente", acao, perfil, eh_dono=True)


def test_confirmar_envio_exige_dono():
    with pytest.raises(NaoAutorizado):
        proximo_status("aprovado", "confirmar_envio", "solicitante", eh_dono=False)
    with pytest.raises(NaoAutorizado):
        proximo_status("aprovado", "confirmar_envio", "solicitante")
    assert proximo_status("aprovado", "confirmar_envio", "solicitante", True) == "enviado"


@pytest.mark.parametrize("perfil", ["gestor", "fiscal", "admin"])
def test_outros_perfis_nao_confirmam_envio_nem_sendo_donos(perfil):
    with pytest.raises(NaoAutorizado):
        proximo_status("aprovado", "confirmar_envio", perfil, eh_dono=True)


@pytest.mark.parametrize(
    "status, acao",
    [
        ("enviado", "informar_nf"),
        ("faturado", "marcar_chegada"),
        ("recebido", "arquivar"),
        ("reprovado", "arquivar"),
    ],
)
def test_admin_faz_o_que_o_fiscal_faz(status, acao):
    assert proximo_status(status, acao, "admin") == proximo_status(status, acao, "fiscal")


@pytest.mark.parametrize("eh_dono", [False, True])
@pytest.mark.parametrize("perfil", PERFIS)
@pytest.mark.parametrize("acao", ACOES)
@pytest.mark.parametrize("status", STATUS)
def test_produto_cartesiano_completo(status, acao, perfil, eh_dono):
    esperado = resultado_esperado(status, acao, perfil, eh_dono)
    if isinstance(esperado, type):
        with pytest.raises(esperado):
            proximo_status(status, acao, perfil, eh_dono)
    else:
        assert proximo_status(status, acao, perfil, eh_dono) == esperado


def test_cartesiano_cobre_todas_as_combinacoes():
    # Garante que a tabela esperada não ficou fora de sincronia com as constantes.
    assert len(list(itertools.product(STATUS, ACOES, PERFIS, (False, True)))) == 7 * 6 * 4 * 2
    assert set(ESPERADO) <= set(itertools.product(STATUS, ACOES))


def test_arquivado_nao_aceita_nenhuma_acao():
    for acao, perfil, eh_dono in itertools.product(ACOES, PERFIS, (False, True)):
        with pytest.raises(TransicaoInvalida):
            proximo_status("arquivado", acao, perfil, eh_dono)


# ---- entradas desconhecidas ----

def test_acao_desconhecida():
    with pytest.raises(TransicaoInvalida):
        proximo_status("pendente", "excluir", "gestor")


def test_status_desconhecido():
    with pytest.raises(TransicaoInvalida):
        proximo_status("cancelado", "aprovar", "gestor")


def test_perfil_desconhecido_em_transicao_valida():
    with pytest.raises(NaoAutorizado):
        proximo_status("pendente", "aprovar", "visitante")


def test_perfil_desconhecido_em_transicao_invalida_prioriza_estado():
    with pytest.raises(TransicaoInvalida):
        proximo_status("enviado", "aprovar", "visitante")


@pytest.mark.parametrize("valor", [None, "", "PENDENTE", 3])
def test_valores_estranhos_nao_estouram_outra_excecao(valor):
    with pytest.raises(WorkflowError):
        proximo_status(valor, "aprovar", "gestor")
    with pytest.raises(WorkflowError):
        proximo_status("pendente", valor, "gestor")
    with pytest.raises(WorkflowError):
        proximo_status("pendente", "aprovar", valor)


def test_mensagens_em_portugues_e_informativas():
    with pytest.raises(TransicaoInvalida) as exc:
        proximo_status("enviado", "aprovar", "gestor")
    assert "enviado" in str(exc.value) and "aprovar" in str(exc.value)

    with pytest.raises(NaoAutorizado) as exc:
        proximo_status("pendente", "aprovar", "solicitante")
    assert "solicitante" in str(exc.value) and "aprovar" in str(exc.value)

    with pytest.raises(NaoAutorizado) as exc:
        proximo_status("aprovado", "confirmar_envio", "solicitante")
    assert "dono" in str(exc.value)


def test_proximo_status_e_deterministico_e_nao_muda_o_estado():
    antes = dict(workflow.TRANSICOES)
    primeiro = proximo_status("pendente", "aprovar", "gestor")
    segundo = proximo_status("pendente", "aprovar", "gestor")
    assert primeiro == segundo == "aprovado"
    assert workflow.TRANSICOES == antes


# ---- acoes_disponiveis ----

ACOES_ESPERADAS = {
    "pendente": {"gestor": ["aprovar", "reprovar"], "admin": ["aprovar", "reprovar"]},
    "aprovado": {"solicitante": ["confirmar_envio"]},  # só o dono
    "reprovado": {"fiscal": ["arquivar"], "admin": ["arquivar"]},
    "enviado": {"fiscal": ["informar_nf"], "admin": ["informar_nf"]},
    "faturado": {"fiscal": ["marcar_chegada"], "admin": ["marcar_chegada"]},
    "recebido": {"fiscal": ["arquivar"], "admin": ["arquivar"]},
    "arquivado": {},
}


@pytest.mark.parametrize("perfil", PERFIS)
@pytest.mark.parametrize("status", STATUS)
def test_acoes_disponiveis(status, perfil):
    esperado = ACOES_ESPERADAS[status].get(perfil, [])
    if status == "aprovado" and perfil == "solicitante":
        assert acoes_disponiveis(status, perfil, eh_dono=True) == ["confirmar_envio"]
        assert acoes_disponiveis(status, perfil, eh_dono=False) == []
        assert acoes_disponiveis(status, perfil) == []
    else:
        for eh_dono in (False, True):
            assert acoes_disponiveis(status, perfil, eh_dono) == esperado


@pytest.mark.parametrize("eh_dono", [False, True])
@pytest.mark.parametrize("perfil", PERFIS)
@pytest.mark.parametrize("status", STATUS)
def test_acoes_disponiveis_coincide_com_proximo_status(status, perfil, eh_dono):
    aceitas = []
    for acao in ACOES:
        try:
            proximo_status(status, acao, perfil, eh_dono)
        except WorkflowError:
            continue
        aceitas.append(acao)
    assert acoes_disponiveis(status, perfil, eh_dono) == aceitas


def test_acoes_disponiveis_status_ou_perfil_desconhecido():
    assert acoes_disponiveis("cancelado", "gestor") == []
    assert acoes_disponiveis("pendente", "visitante") == []


def test_acoes_disponiveis_devolve_lista_nova():
    lista = acoes_disponiveis("pendente", "gestor")
    lista.append("lixo")
    assert acoes_disponiveis("pendente", "gestor") == ["aprovar", "reprovar"]


def test_modulo_nao_depende_de_flask_nem_sqlite():
    import ast
    import inspect

    arvore = ast.parse(inspect.getsource(workflow))
    importados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importados.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            importados.add(no.module.split(".")[0])
    assert not importados & {"flask", "sqlite3", "datetime"}


def test_titulo_pedido():
    assert workflow.titulo_pedido(7) == "Novo Pedido - 7"
    assert workflow.titulo_pedido(1234) == "Novo Pedido - 1234"
