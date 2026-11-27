import re
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import auth, conferencia, create_app, db, erp  # noqa: E402
from app import caixa as svc  # noqa: E402
from app.formatos import ValorInvalido, brl, ler_valor  # noqa: E402

DIA = "2026-10-23"


def parte(pdv, forma, valor, hora="10:00"):
    return {"pdv": pdv, "forma": forma, "valor": valor, "hora": hora, "vendedor": "CAIXA", "total": valor}


def lanc(i, forma, valor, pdv=None, aprovado=None):
    return {"id": i, "forma": forma, "valor": valor, "pdv_vinculado": pdv, "aprovado_por": aprovado}


# ── formatos ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "texto, centavos",
    [("83", 8300), ("1.234,56", 123456), ("1234,5", 123450), ("1234.56", 123456), ("1.234", 123400),
     ("R$ 820,00", 82000), ("0,01", 1), (" 15 ", 1500)],
)
def test_ler_valor(texto, centavos):
    assert ler_valor(texto) == centavos


@pytest.mark.parametrize("texto", ["", "abc", "0", "0,00", "1,2,3", "-5", "12,345"])
def test_ler_valor_invalido(texto):
    with pytest.raises(ValorInvalido):
        ler_valor(texto)


def test_brl():
    assert brl(123456) == "1.234,56"
    assert brl(-5400) == "−54,00"
    assert brl(5400, sinal=True) == "+54,00"
    assert brl(0) == "0,00"


# ── leitura do ATS ──────────────────────────────────────────────────────────
def venda(pdv, total, **formas):
    v = {"pdv": pdv, "hora": "10:00", "vendedor": "X", "total": total}
    for chave in ("dinheiro", "convenio", "cartao", "cheque", "chpre", "receber", "outros"):
        v[chave] = formas.get(chave, 0)
    return v


def test_montar_partes_formas_e_a_prazo():
    r = erp.montar_partes(
        [
            venda("1", 8300, dinheiro=8300),
            venda("2", 12000, convenio=12000),
            venda("3", 20000),  # PIX
            venda("4", 50000, receber=50000),  # a prazo: fora do caixa
            venda("5", 10000, dinheiro=4000, convenio=6000),  # pagamento misto
            venda("6", 7000, cheque=5000, chpre=2000),
        ],
        {"3": 20000},
    )
    formas = {(p["pdv"], p["forma"]): p["valor"] for p in r["partes"]}
    assert formas == {
        ("1", "dinheiro"): 8300, ("2", "cartao"): 12000, ("3", "deposito"): 20000,
        ("5", "dinheiro"): 4000, ("5", "cartao"): 6000, ("6", "cheque"): 7000,
    }
    assert r["a_prazo"] == 50000
    assert r["pdvs"] == 5


def test_montar_partes_sobra_vira_outros():
    r = erp.montar_partes([venda("9", 10000, dinheiro=7000)], {})
    assert {(p["forma"], p["valor"]) for p in r["partes"]} == {("dinheiro", 7000), ("outros", 3000)}


# ── conciliação ─────────────────────────────────────────────────────────────
def test_conciliar_por_valor_e_forma():
    partes = [parte("A", "dinheiro", 8300, "09:00"), parte("B", "deposito", 82000), parte("C", "cartao", 12000)]
    lancs = [lanc(1, "dinheiro", 8300), lanc(2, "deposito", 82000), lanc(3, "cartao", 12000)]
    c = conferencia.conciliar(lancs, partes)
    assert {k: (v["situacao"], v["pdv"]) for k, v in c["por_lanc"].items()} == {
        1: ("ok", "A"), 2: ("ok", "B"), 3: ("ok", "C")
    }
    assert c["pdvs_sem_lanc"] == []


def test_conciliar_mesma_forma_errada_nao_casa():
    c = conferencia.conciliar([lanc(1, "dinheiro", 12000)], [parte("C", "cartao", 12000)])
    assert c["por_lanc"][1]["situacao"] == "sem_pdv"
    assert [p["pdv"] for p in c["pdvs_sem_lanc"]] == ["C"]


def test_conciliar_valores_repetidos_casam_um_a_um():
    partes = [parte("A", "dinheiro", 5000), parte("B", "dinheiro", 5000)]
    c = conferencia.conciliar([lanc(1, "dinheiro", 5000), lanc(2, "dinheiro", 5000), lanc(3, "dinheiro", 5000)], partes)
    assert [c["por_lanc"][i]["situacao"] for i in (1, 2, 3)] == ["ok", "ok", "sem_pdv"]
    assert {c["por_lanc"][1]["pdv"], c["por_lanc"][2]["pdv"]} == {"A", "B"}


def test_conciliar_ligado_a_mao():
    # PDV alterado depois no ATS: ligado à mão, aparece como valor diferente.
    partes = [parte("A", "dinheiro", 18200), parte("B", "deposito", 20000)]
    lancs = [lanc(1, "dinheiro", 12800, pdv="A"), lanc(2, "cartao", 20000, pdv="B"), lanc(3, "dinheiro", 100, pdv="Z")]
    c = conferencia.conciliar(lancs, partes)
    assert c["por_lanc"][1]["situacao"] == "valor_diferente"
    assert c["por_lanc"][2]["situacao"] == "forma_diferente"
    assert c["por_lanc"][3]["situacao"] == "pdv_inexistente"
    assert c["pdvs_sem_lanc"] == []


def test_conciliar_ligacao_manual_tem_prioridade():
    # O lançamento 1 casaria com A pelo valor, mas A foi ligado à mão ao lançamento 2.
    partes = [parte("A", "dinheiro", 5000), parte("B", "dinheiro", 5000)]
    c = conferencia.conciliar([lanc(1, "dinheiro", 5000), lanc(2, "dinheiro", 5000, pdv="A")], partes)
    assert c["por_lanc"][2]["pdv"] == "A" and c["por_lanc"][1]["pdv"] == "B"


def test_resumo_divergencias_gaveta_e_aprovacao():
    partes = [parte("A", "dinheiro", 10000), parte("B", "deposito", 20000)]
    lancs = [
        lanc(1, "dinheiro", 10000),
        lanc(2, "cofre", 6000),
        lanc(3, "dev_credito", 5000),
        lanc(4, "pontos", 1000, aprovado=7),
    ]
    r = conferencia.resumo(lancs, partes, saldo_inicial=21220, saldo_contado=25220)
    assert r["gaveta"]["esperado"] == 21220 + 10000 - 6000
    assert r["gaveta"]["diferenca"] == 0
    assert [l["id"] for l in r["pendentes"]] == [3]
    assert r["divergencias"] == 1  # o PIX B sem lançamento
    totais = {t["forma"]: t["diferenca"] for t in r["totais"]}
    assert totais == {"dinheiro": 0, "cartao": 0, "deposito": -20000, "cheque": 0}


# ── aplicação ───────────────────────────────────────────────────────────────
PARTES = [parte("01000791", "dinheiro", 8300, "09:12"), parte("01000798", "deposito", 82000, "10:40")]


@pytest.fixture()
def app(tmp_path, monkeypatch):
    leituras = {"partes": list(PARTES), "falhar": False}

    def ler_dia(emp, dia, forcar=False):
        if leituras["falhar"]:
            raise erp.ErroATS("ATS fora do ar")
        return {"partes": list(leituras["partes"]), "a_prazo": 0, "pdvs": 2, "lido_em": "18:00:00"}

    monkeypatch.setattr(erp, "ler_dia", ler_dia)
    monkeypatch.setenv("CAIXA_COOKIE_INSECURE", "1")
    a = create_app(tmp_path)
    a.config["TESTING"] = True
    a.leituras = leituras
    conn = db.conectar(a.config["DB_PATH"])
    auth.criar_usuario(conn, "ana", "Ana", "senha-da-ana", "caixa", "ILU", trocar_senha=False)
    auth.criar_usuario(conn, "fin", "Fin", "senha-do-fin", "financeiro", trocar_senha=False)
    auth.criar_usuario(conn, "ele", "Eli", "senha-do-eli", "caixa", "ELE", trocar_senha=False)
    conn.close()
    return a


class Cliente:
    def __init__(self, app, login, senha):
        self.c = app.test_client()
        self.app = app
        self.c.get("/login")
        r = self.c.post("/login", data={"login": login, "senha": senha, "csrf": self.csrf()})
        assert r.status_code == 302, r.data

    def csrf(self):
        with self.c.session_transaction() as s:
            return s.get("csrf") or ""

    def get(self, url):
        return self.c.get(url)

    def post(self, url, **dados):
        dados["csrf"] = self.csrf()
        return self.c.post(url, data=dados)

    def db(self):
        return db.conectar(self.app.config["DB_PATH"])


def abrir(cli, saldo="212,20"):
    cli.post("/caixa/abrir", empresa="ILU", data=DIA, saldo_inicial=saldo)
    return cli.db().execute("SELECT * FROM caixa WHERE empresa='ILU' AND data=?", (DIA,)).fetchone()


def test_login_exigido(app):
    r = app.test_client().get(f"/caixa/ILU/{DIA}")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_csrf_obrigatorio(app):
    ana = Cliente(app, "ana", "senha-da-ana")
    r = ana.c.post("/caixa/abrir", data={"empresa": "ILU", "data": DIA})
    assert r.status_code == 400


def test_fluxo_completo(app):
    ana = Cliente(app, "ana", "senha-da-ana")
    cx = abrir(ana)
    assert cx["saldo_inicial"] == 21220
    base = f"/caixa/{cx['id']}"
    ana.post(f"{base}/lancar", forma="dinheiro", valor="83")
    ana.post(f"{base}/lancar", forma="deposito", valor="820,00")
    ana.post(f"{base}/lancar", forma="cofre", valor="100")
    ana.post(f"{base}/lancar", forma="dev_credito", valor="50", obs="devolução PDV 655")

    tela = ana.get(f"/caixa/ILU/{DIA}").get_data(as_text=True)
    assert "PDV 01000791" in tela and "PDV 01000798" in tela
    assert "aguardando aprovação" in tela

    # Fecha: gaveta esperada = 212,20 + 83 − 100 = 195,20
    r = ana.post(f"{base}/fechar", contado="195,20", justificativa="")
    assert r.status_code == 302 and "imprimir" in r.headers["Location"]
    cx = ana.db().execute("SELECT * FROM caixa WHERE id=?", (cx["id"],)).fetchone()
    assert cx["status"] == "fechado" and cx["divergencias"] == 0 and cx["saldo_contado"] == 19520
    assert "01000798" in cx["snapshot"]

    # Caixa não lança mais nem confere.
    ana.post(f"{base}/lancar", forma="dinheiro", valor="1")
    assert ana.db().execute("SELECT COUNT(*) FROM lancamento WHERE excluido=0").fetchone()[0] == 4
    assert ana.post(f"{base}/conferir", obs="").status_code == 403

    fin = Cliente(app, "fin", "senha-do-fin")
    fin.post(f"{base}/conferir", obs="")
    assert ana.db().execute("SELECT status FROM caixa").fetchone()[0] == "fechado"  # falta aprovar
    lid = ana.db().execute("SELECT id FROM lancamento WHERE forma='dev_credito'").fetchone()[0]
    fin.post(f"{base}/lanc/{lid}/aprovar", obs="conferi a devolução")
    fin.post(f"{base}/conferir", obs="")
    assert ana.db().execute("SELECT status FROM caixa").fetchone()[0] == "conferido"

    impressao = fin.get(f"{base}/imprimir").get_data(as_text=True)
    assert "Sem divergências" in impressao and "conferi a devolução" in impressao
    hist = fin.get("/historico?mes=2026-10").get_data(as_text=True)
    assert "conferido" in hist


def test_fechar_com_divergencia_exige_justificativa(app):
    ana = Cliente(app, "ana", "senha-da-ana")
    cx = abrir(ana, saldo="0")
    base = f"/caixa/{cx['id']}"
    ana.post(f"{base}/lancar", forma="dinheiro", valor="38")  # devia ser 83
    ana.post(f"{base}/fechar", contado="38", justificativa="")
    assert ana.db().execute("SELECT status FROM caixa").fetchone()[0] == "aberto"
    pagina = ana.get(f"{base}/encerrar").get_data(as_text=True)
    assert "PDVs sem lançamento" in pagina and "nenhum PDV com esse valor" in pagina

    ana.post(f"{base}/fechar", contado="38", justificativa="cliente pagou 38, PDV será corrigido")
    cx = ana.db().execute("SELECT * FROM caixa").fetchone()
    assert cx["status"] == "fechado" and cx["divergencias"] == 3  # lançamento + 2 PDVs


def test_fechar_sem_ats_e_recusado(app):
    ana = Cliente(app, "ana", "senha-da-ana")
    cx = abrir(ana)
    app.leituras["falhar"] = True
    ana.post(f"/caixa/{cx['id']}/fechar", contado="212,20", justificativa="x")
    assert ana.db().execute("SELECT status FROM caixa").fetchone()[0] == "aberto"
    # A tela do dia continua abrindo, com aviso.
    assert "ATS indisponível" in ana.get(f"/caixa/ILU/{DIA}").get_data(as_text=True)


def test_vincular_pdv_alterado_depois(app):
    ana = Cliente(app, "ana", "senha-da-ana")
    cx = abrir(ana, saldo="0")
    base = f"/caixa/{cx['id']}"
    ana.post(f"{base}/lancar", forma="dinheiro", valor="80")
    lid = ana.db().execute("SELECT id FROM lancamento").fetchone()[0]
    ana.post(f"{base}/vincular", lanc=str(lid), pdv="01000791")
    pagina = ana.get(f"{base}/encerrar").get_data(as_text=True)
    assert "valor diferente" in pagina
    acoes = [r[0] for r in ana.db().execute("SELECT acao FROM historico")]
    assert "vincular" in acoes


def test_edicao_e_exclusao_ficam_no_registro(app):
    ana = Cliente(app, "ana", "senha-da-ana")
    cx = abrir(ana)
    base = f"/caixa/{cx['id']}"
    ana.post(f"{base}/lancar", forma="dinheiro", valor="150")
    lid = ana.db().execute("SELECT id FROM lancamento").fetchone()[0]
    ana.post(f"{base}/lanc/{lid}/editar", forma="dinheiro", valor="105", obs="")
    ana.post(f"{base}/lanc/{lid}/excluir")
    conn = ana.db()
    detalhes = [r["detalhe"] for r in conn.execute("SELECT detalhe FROM historico ORDER BY id")]
    assert "Dinheiro 150,00 → Dinheiro 105,00" in detalhes
    assert conn.execute("SELECT excluido FROM lancamento").fetchone()[0] == 1
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM historico")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE historico SET detalhe = 'x'")


def test_obs_obrigatoria_em_saida_e_dev_credito(app):
    ana = Cliente(app, "ana", "senha-da-ana")
    cx = abrir(ana)
    for forma in ("pagamento", "dev_credito", "pontos", "outra_saida"):
        ana.post(f"/caixa/{cx['id']}/lancar", forma=forma, valor="10", obs="")
    assert ana.db().execute("SELECT COUNT(*) FROM lancamento").fetchone()[0] == 0


def test_caixa_presa_a_empresa(app):
    ele = Cliente(app, "ele", "senha-do-eli")
    assert ele.get(f"/caixa/ILU/{DIA}").status_code == 403
    ana = Cliente(app, "ana", "senha-da-ana")
    cx = abrir(ana)
    assert ele.post(f"/caixa/{cx['id']}/lancar", forma="dinheiro", valor="1").status_code == 403


def test_reabrir_desfaz_fechamento(app):
    ana = Cliente(app, "ana", "senha-da-ana")
    cx = abrir(ana, saldo="0")
    base = f"/caixa/{cx['id']}"
    ana.post(f"{base}/fechar", contado="0", justificativa="sem movimento")
    fin = Cliente(app, "fin", "senha-do-fin")
    fin.post(f"{base}/reabrir", motivo="")
    assert ana.db().execute("SELECT status FROM caixa").fetchone()[0] == "fechado"
    fin.post(f"{base}/reabrir", motivo="faltou lançar PIX")
    cx = ana.db().execute("SELECT * FROM caixa").fetchone()
    assert cx["status"] == "aberto" and cx["snapshot"] is None


def test_alterar_lancamento_aprovado_tira_aprovacao(app):
    ana = Cliente(app, "ana", "senha-da-ana")
    cx = abrir(ana)
    base = f"/caixa/{cx['id']}"
    ana.post(f"{base}/lancar", forma="pontos", valor="20", obs="cliente X")
    lid = ana.db().execute("SELECT id FROM lancamento").fetchone()[0]
    Cliente(app, "fin", "senha-do-fin").post(f"{base}/lanc/{lid}/aprovar", obs="ok")
    assert ana.db().execute("SELECT aprovado_por FROM lancamento").fetchone()[0] is not None
    ana.post(f"{base}/lanc/{lid}/editar", forma="pontos", valor="200", obs="cliente X")
    assert ana.db().execute("SELECT aprovado_por FROM lancamento").fetchone()[0] is None


def test_saldo_inicial_sugerido_do_ultimo_fechamento(app):
    ana = Cliente(app, "ana", "senha-da-ana")
    cx = abrir(ana, saldo="0")
    ana.post(f"/caixa/{cx['id']}/fechar", contado="0", justificativa="sem movimento")
    conn = ana.db()
    conn.execute("UPDATE caixa SET saldo_contado = 30000")
    conn.commit()
    pagina = ana.get("/caixa/ILU/2026-10-24").get_data(as_text=True)
    assert re.search(r'value="300,00"', pagina)


def test_paginas_renderizam(app):
    fin = Cliente(app, "fin", "senha-do-fin")
    for url in ("/", f"/caixa/ELE/{DIA}", "/historico", "/senha"):
        r = fin.get(url)
        assert r.status_code in (200, 302), url
    assert fin.get("/usuarios").status_code == 403
