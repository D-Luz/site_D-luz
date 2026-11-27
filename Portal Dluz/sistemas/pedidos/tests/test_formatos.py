import pytest

from app.formatos import formatar_brl, formatar_data, formatar_instante


@pytest.mark.parametrize(
    "centavos, esperado",
    [
        (0, "R$ 0,00"),
        (5, "R$ 0,05"),
        (99, "R$ 0,99"),
        (100, "R$ 1,00"),
        (123456, "R$ 1.234,56"),
        (100000000, "R$ 1.000.000,00"),
    ],
)
def test_formatar_brl(centavos, esperado):
    assert formatar_brl(centavos) == esperado


def test_formatar_brl_negativo_nao_quebra():
    assert formatar_brl(-123456) == "-R$ 1.234,56"


@pytest.mark.parametrize(
    "valor, esperado",
    [
        ("2026-10-23", "23/10/2026"),
    ],
)
def test_formatar_data_iso(valor, esperado):
    assert formatar_data(valor) == esperado


def test_formatar_data_vazia_ou_estranha_nao_quebra():
    assert formatar_data(None) == "não informada"
    assert formatar_data("") == "não informada"
    assert formatar_data("ontem") == "ontem"


def test_formatar_data_nao_trata_instante_como_data():
    assert formatar_data("2026-10-23T15:00:00+00:00") == "2026-10-23T15:00:00+00:00"


@pytest.mark.parametrize(
    "valor, esperado",
    [
        ("2026-10-24T01:30:00+00:00", "23/10/2026 22:30"),  # noite em SP, já dia seguinte em UTC
        ("2026-10-23T02:59:00+00:00", "22/10/2026 23:59"),
        ("2026-10-23T03:00:00+00:00", "23/10/2026 00:00"),  # virada do dia em SP (UTC-3)
        ("2026-10-23T15:00:00+00:00", "23/10/2026 12:00"),
        ("2026-10-23T12:00:00-03:00", "23/10/2026 12:00"),  # já com o fuso de SP
        ("2026-10-23T15:00:00", "23/10/2026 12:00"),  # sem fuso: tratado como UTC
        ("2026-10-23T15:00:00Z", "23/10/2026 12:00"),
    ],
)
def test_formatar_instante(valor, esperado):
    assert formatar_instante(valor) == esperado


@pytest.mark.parametrize("valor", ["ontem", "", "2026-13-45T99:00", "23/10/2026"])
def test_formatar_instante_estranho_volta_como_esta(valor):
    assert formatar_instante(valor) == valor


def test_formatar_instante_none_nao_quebra():
    assert formatar_instante(None) == "None"
