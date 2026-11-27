"""Cada ferramenta roda de ponta a ponta com o Firebird de mentira (ou com arquivos de amostra)."""

import json
import time
from datetime import date
from pathlib import Path

import pytest

AMOSTRAS = Path(__file__).parent / "amostras"
HOJE = date.today().isoformat()
INICIO = date.today().replace(day=1).isoformat()

CASOS = {
    "orcamentos-baixados": {"empresa": "ELE", "inicio": INICIO, "fim": HOJE},
    "orcamentos-vendedor": {"empresa": "TODAS", "vendedor": "MARCOS", "inicio": INICIO, "fim": HOJE},
    "imprimir-orcamento": {"empresa": "ILU", "numero": "1234"},
    "venda-mensal": {"desde": "2025-01-01"},
    "vendas-pdv": {"empresa": "TODAS", "inicio": INICIO, "fim": HOJE},
    "somatorio-pdv": {"empresa": "COR", "inicio": INICIO, "fim": HOJE},
    "planilha-orcamento": {},
    "titulos": {"modo": "pagar", "empresa": "TODAS", "inicio": INICIO, "fim": HOJE},
    "receber-em-aberto": {},
    "acompanhamento-semanal": {"data": HOJE},
    "receita-mensal-comparativo": {},
    "posicao-estoque": {"empresa": "ELE", "banco": "real", "data": "2026-09-30"},
    "ultima-nf-compra": {"empresa": "TODAS", "data": HOJE},
    "ultima-nf-compra-fiscal": {"empresa": "ILU", "data": HOJE},
    "validar-preconf": {},
    "painel-ats": {},
    "analise-estoque": {"empresa": "COR", "lead_time": "2", "usar_cache": ""},
    "proposta-orcamento": {
        "cliente": "Construtora Teste", "vendedor": "Marcos", "contato": "", "data": "28/10/2026",
        "itens": json.dumps([{"ambiente": "Sala", "p_desc": "Painel LED 24W", "p_qtd": 4, "p_vunit": 59.9,
                              "s_desc": "Instalação", "s_qtd": 4, "s_vunit": 20}]),
        "avista": "1", "avista_modo": "valor_final", "valor_avista": "300,00", "parcelado": "1", "parcelas": "10",
    },
}
ARQUIVOS = {
    "pix-inter": ({}, [("pdfs", "dluz_inter_outubro.pdf")]),
    "consolidar-extratos": ({"formato": "ambos"}, [("pdfs", "dluz_inter_outubro.pdf")]),
    "nfe-excel": ({}, [("xmls", "nfe_56782.xml")]),
}


def _esperar(nav, tid, limite=180):
    fim = time.time() + limite
    while time.time() < fim:
        st = nav.get(f"/tarefas/{tid}/estado").json
        if st["situacao"] in ("ok", "erro"):
            return st
        time.sleep(0.5)
    raise AssertionError("tarefa não terminou")


def _enviar(nav, fid, campos, arquivos=()):
    dados = dict(campos)
    for campo, nome in arquivos:
        dados.setdefault(campo, []).append((open(AMOSTRAS / nome, "rb"), nome))
    r = nav.post(f"/ferramentas/{fid}", dados, content_type="multipart/form-data")
    assert r.status_code == 302, r.get_data(as_text=True)[:2000]
    return r.headers["Location"].rsplit("/", 1)[-1]


def test_cobertura_de_todas_as_ferramentas(app):
    from portal.ferramentas import REGISTRO

    assert set(REGISTRO) == set(CASOS) | set(ARQUIVOS)


@pytest.mark.parametrize("fid", sorted(CASOS) + sorted(ARQUIVOS))
def test_ferramenta_roda(admin, fid):
    campos, arquivos = (CASOS[fid], ()) if fid in CASOS else ARQUIVOS[fid]
    tid = _enviar(admin, fid, campos, arquivos)
    st = _esperar(admin, tid)
    assert st["situacao"] == "ok", (st["erro"], st["log"][-3000:])
    pagina = admin.get(f"/tarefas/{tid}").get_data(as_text=True)
    baixar = [p for p in pagina.split('"') if f"/tarefas/{tid}/arquivo/" in p]
    assert baixar, "nenhum arquivo gerado"
    for url in baixar:
        assert admin.get(url).status_code == 200


def test_formulario_valida_campos(admin):
    r = admin.post("/ferramentas/titulos", {"modo": "x", "empresa": "TODAS", "inicio": "2026-13-01", "fim": ""})
    texto = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Tipo: opção inválida" in texto and "data inválida" in texto and "Preencha" in texto


def test_upload_com_extensao_errada(admin):
    import io

    r = admin.post("/ferramentas/pix-inter", {"pdfs": [(io.BytesIO(b"x"), "virus.exe")]},
                   content_type="multipart/form-data")
    assert "não serve" in r.get_data(as_text=True)


def test_ats_fora_do_ar_da_mensagem_clara(admin, monkeypatch):
    import firebirdsql

    def recusar(*a, **k):
        raise ConnectionRefusedError("[Errno 61] Connection refused")

    monkeypatch.setattr(firebirdsql, "connect", recusar)
    tid = _enviar(admin, "imprimir-orcamento", {"empresa": "ELE", "numero": "10"})
    st = _esperar(admin, tid)
    assert st["situacao"] == "erro" and "Não foi possível conectar ao banco do ATS" in st["erro"]


def test_erp_configurado_chega_ao_script(app):
    from portal import erp
    from portal.carregador import carregar

    emp = {s: dict(v) for s, v in __import__("portal.config", fromlist=["x"]).EMPRESAS_PADRAO.items()}
    emp["ILU"]["host"] = "10.1.1.1"
    emp["ILU"]["real"] = r"E:\NOVO\ILU.FB"
    mod = carregar("relatorio_vendas.py", emp)
    cfg = [v for k, v in mod.EMPRESAS.items() if "Ilumina" in k][0]
    assert cfg["HOST"] == "10.1.1.1" and cfg["DATABASE"] == r"E:\NOVO\ILU.FB"
    assert erp.chave_da_empresa("D.LUZ Iluminação") == "ILU"


def test_arquivo_fora_da_pasta_bloqueado(admin):
    tid = _enviar(admin, "planilha-orcamento", {})
    _esperar(admin, tid)
    assert admin.get(f"/tarefas/{tid}/arquivo/../../portal.sqlite").status_code == 404
    assert admin.get(f"/tarefas/{tid}/arquivo/%2e%2e/%2e%2e/portal.sqlite").status_code == 404


def test_tarefa_de_outro_usuario_protegida(admin, marcos):
    tid = _enviar(admin, "planilha-orcamento", {})
    _esperar(admin, tid)
    assert marcos.get(f"/tarefas/{tid}").status_code == 403


def test_pagina_gerada_isolada(admin):
    tid = _enviar(admin, "orcamentos-baixados", CASOS["orcamentos-baixados"])
    _esperar(admin, tid)
    pagina = admin.get(f"/tarefas/{tid}").get_data(as_text=True)
    ver = [p for p in pagina.split('"') if "/ver/" in p][0]
    r = admin.get(ver)
    assert r.headers["Content-Security-Policy"].startswith("sandbox")


def test_vendedor_todos_e_lista_aprendida(admin):
    tid = _enviar(admin, "orcamentos-vendedor", {"empresa": "ELE", "vendedor": "", "inicio": INICIO, "fim": HOJE})
    st = _esperar(admin, tid)
    assert st["situacao"] == "ok" and "de todos os vendedores" in admin.get(f"/tarefas/{tid}").get_data(as_text=True)
    form = admin.get("/ferramentas/orcamentos-vendedor").get_data(as_text=True)
    assert 'value="MAILSON"' in form and "Todos os vendedores" in form
