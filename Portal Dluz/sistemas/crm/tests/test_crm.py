import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import auth, db, formatos, pontos, regras, sincronizar  # noqa: E402

HOJE = date(2026, 10, 26)


def dia(n):
    return (HOJE - timedelta(days=n)).isoformat()


@pytest.fixture()
def conn(tmp_path):
    c = db.conectar(tmp_path / "t.sqlite")
    db.criar_esquema(c)
    yield c
    c.close()


def cliente(c, codigo, nome="CLIENTE TESTE", fone="5577999000001", seg="007", nasc=None, ult=None):
    c.execute("INSERT INTO cliente VALUES ('ELE', ?, ?, ?, ?, ?, ?, 1)", (codigo, nome, fone, seg, ult, nasc))


def venda(c, codigo, cli, n, total=500.0, vend="0020"):
    c.execute("INSERT INTO venda VALUES ('ELE', ?, ?, ?, ?, 'MAILSON', ?, NULL)", (codigo, dia(n), cli, vend, total))


def profissional(c, nome="ELETRICISTA UM", cli=None, inicio=None):
    cur = c.execute(
        "INSERT INTO profissional (nome, telefone, inicio_programa, criado_em) "
        "VALUES (?, '5577999111111', ?, 'x')", (nome, inicio or dia(400)),
    )
    if cli:
        pontos.vincular_cliente(c, cur.lastrowid, "ELE", cli)
    return cur.lastrowid


# ── formatos ────────────────────────────────────────────────────────────────
def test_normalizar_fone():
    assert formatos.normalizar_fone("", "(77) 99961-4370") == "5577999614370"
    assert formatos.normalizar_fone("7799614370") == "557799614370"
    assert formatos.normalizar_fone("12345") == ""
    assert formatos.normalizar_fone("5577999614370") == "5577999614370"


def test_primeiro_nome_e_modelo():
    assert formatos.primeiro_nome("GILMAR BRITO (ELETRICISTA)") == "Gilmar"
    assert formatos.renderizar("Oi {nome} {inexistente}!", nome="Ana") == "Oi Ana !"


def test_brl():
    assert formatos.brl(1234.5) == "R$ 1.234,50"


# ── pontos ──────────────────────────────────────────────────────────────────
def test_pontos_um_por_cento_e_nao_duplica(conn):
    cliente(conn, "1")
    venda(conn, "V1", "1", 3, 1000)
    pid = profissional(conn)
    pontos.atribuir(conn, "ELE", "V1", pid, "balcao")
    pontos.atribuir(conn, "ELE", "V1", pid, "balcao")
    assert pontos.saldo(conn, pid) == 10.0


def test_venda_anterior_ao_programa_nao_gera_pontos(conn):
    cliente(conn, "1")
    venda(conn, "V1", "1", 50, 1000)
    pid = profissional(conn, inicio=dia(10))
    pontos.atribuir(conn, "ELE", "V1", pid, "balcao")
    assert pontos.saldo(conn, pid) == 0
    assert conn.execute("SELECT COUNT(*) FROM atribuicao").fetchone()[0] == 1  # conta para o histórico


def test_reatribuir_move_os_pontos_e_desfazer_remove(conn):
    cliente(conn, "1")
    venda(conn, "V1", "1", 3, 1000)
    a, b = profissional(conn, "A"), profissional(conn, "B")
    pontos.atribuir(conn, "ELE", "V1", a, "x")
    pontos.atribuir(conn, "ELE", "V1", b, "x")
    assert pontos.saldo(conn, a) == 0 and pontos.saldo(conn, b) == 10
    pontos.atribuir(conn, "ELE", "V1", None, "x")
    assert pontos.saldo(conn, b) == 0


def test_resgate_nao_passa_do_saldo(conn):
    cliente(conn, "1")
    venda(conn, "V1", "1", 3, 1000)
    pid = profissional(conn)
    pontos.atribuir(conn, "ELE", "V1", pid, "x")
    with pytest.raises(ValueError):
        pontos.resgatar(conn, pid, 10.01, "x")
    pontos.resgatar(conn, pid, 4, "x")
    assert pontos.saldo(conn, pid) == 6


def test_compras_proprias_sao_atribuidas_sozinhas(conn):
    cliente(conn, "9", seg="039")
    venda(conn, "V1", "9", 2, 200)
    pid = profissional(conn, cli="9")
    assert pontos.atribuir_compras_proprias(conn) == 1
    assert pontos.atribuir_compras_proprias(conn) == 0
    assert pontos.saldo(conn, pid) == 2.0


def test_venda_cancelada_remove_pontos(conn):
    cliente(conn, "1")
    venda(conn, "V1", "1", 3, 1000)
    pid = profissional(conn)
    pontos.atribuir(conn, "ELE", "V1", pid, "x")
    conn.execute("DELETE FROM venda")
    pontos.remover_pontos_de_vendas_canceladas(conn, "ELE")
    assert pontos.saldo(conn, pid) == 0


def test_um_eletricista_com_cadastro_nas_duas_lojas(conn):
    cliente(conn, "9", seg="039")
    conn.execute("INSERT INTO cliente VALUES ('ILU', '4', 'MESMO', '5577999000001', '039', NULL, NULL, 1)")
    pid = profissional(conn, cli="9")
    pontos.vincular_cliente(conn, pid, "ILU", "4")
    venda(conn, "V1", "9", 2, 100)
    conn.execute("INSERT INTO venda VALUES ('ILU', 'W1', ?, '4', '0020', 'X', 300, NULL)", (dia(2),))
    assert pontos.atribuir_compras_proprias(conn) == 2
    assert pontos.saldo(conn, pid) == 4.0


def test_eletricista_indicado_no_erp_e_atribuido_sem_sobrescrever_manual(conn):
    cliente(conn, "1")
    pid = profissional(conn, "ERP")
    outro = profissional(conn, "OUTRO")
    pontos.vincular_codprof(conn, pid, "ELE", "00007")
    conn.execute("INSERT INTO venda VALUES ('ELE','V1',?, '1','0020','M',1000,'00007')", (dia(2),))
    conn.execute("INSERT INTO venda VALUES ('ELE','V2',?, '1','0020','M',500,'00007')", (dia(2),))
    conn.execute("INSERT INTO venda VALUES ('ELE','V3',?, '1','0020','M',500,NULL)", (dia(2),))
    pontos.atribuir(conn, "ELE", "V2", outro, "balcao")  # correção manual vale mais
    assert pontos.atribuir_do_erp(conn) == 1
    assert pontos.atribuir_do_erp(conn) == 0
    assert pontos.saldo(conn, pid) == 10.0 and pontos.saldo(conn, outro) == 5.0


def test_chave_nome_ignora_apelido():
    assert pontos.chave_nome("JOAO  RAIMUNDO ( ELETRICISTA)") == pontos.chave_nome("Joao Raimundo")


# ── orçamentos ──────────────────────────────────────────────────────────────
def test_marcar_orcamentos():
    orcs = [
        {"codigo": "A", "data": dia(5), "cliente": "1", "vendedor": "0020", "total": 900.0},
        {"codigo": "B", "data": dia(5), "cliente": "1", "vendedor": "0020", "total": 900.0},  # cópia idêntica
        {"codigo": "C", "data": dia(5), "cliente": "2", "vendedor": "0020", "total": 123.0},  # baixado
        {"codigo": "D", "data": dia(5), "cliente": "3", "vendedor": "0020", "total": 77.0},
    ]
    vendas = [{"data": dia(4), "vendedor": "0020", "total": 123.0}]
    s = {o["codigo"]: o["situacao"] for o in sincronizar.marcar_orcamentos(orcs, vendas)}
    assert s == {"A": "aberto", "B": "copia", "C": "provavel_baixado", "D": "aberto"}


# ── fila do dia ─────────────────────────────────────────────────────────────
def tipos(conn):
    return sorted(t["tipo"] for t in regras.fila_hoje(conn, HOJE))


def test_orcamento_entra_na_fila_so_com_cliente_identificado_e_some_apos_contato(conn):
    cliente(conn, "1")
    conn.execute("INSERT INTO orcamento VALUES ('ELE','O1',?, '1','0020','MAILSON',900,'aberto')", (dia(3),))
    conn.execute("INSERT INTO orcamento VALUES ('ELE','O2',?, '00000000','0020','MAILSON',900,'aberto')", (dia(3),))
    assert tipos(conn) == ["ORC"]
    conn.execute("INSERT INTO contato (tipo, chave, resultado, criado_em) VALUES ('ORC','ELE:O1','conversou',?)", (dia(0) + " 10:00:00",))
    assert tipos(conn) == []


def test_cadencia_do_orcamento_volta_na_proxima_etapa(conn):
    cliente(conn, "1")
    conn.execute("INSERT INTO orcamento VALUES ('ELE','O1',?, '1','0020','MAILSON',900,'aberto')", (dia(7),))
    conn.execute("INSERT INTO contato (tipo, chave, resultado, criado_em) VALUES ('ORC','ELE:O1','sem_resposta',?)", (dia(5) + " 10:00:00",))
    assert tipos(conn) == ["ORC"]  # contato foi na etapa do 3º dia; agora é a do 7º


def test_orcamento_baixado_nao_entra(conn):
    cliente(conn, "1")
    conn.execute("INSERT INTO orcamento VALUES ('ELE','O1',?, '1','0020','MAILSON',900,'provavel_baixado')", (dia(3),))
    assert tipos(conn) == []


def test_cliente_parado_e_limites(conn):
    cliente(conn, "1")
    cliente(conn, "2")
    venda(conn, "V1", "1", 100, 800)          # parado, histórico bom
    venda(conn, "V2", "2", 100, 50)           # parado, histórico irrisório
    venda(conn, "V3", "00000000", 100, 5000)  # consumidor
    assert tipos(conn) == ["PARADO"]


def test_pos_venda(conn):
    cliente(conn, "1")
    venda(conn, "V1", "1", 8, 400)
    assert tipos(conn) == ["POS"]


def test_eletricista_nao_vira_parado_nem_pos_venda(conn):
    cliente(conn, "9", seg="039")
    profissional(conn, cli="9")
    venda(conn, "V1", "9", 8, 900)
    venda(conn, "V0", "9", 100, 900)
    assert "POS" not in tipos(conn) and "PARADO" not in tipos(conn)


def test_eletricista_em_risco(conn):
    cliente(conn, "9", seg="039")
    pid = profissional(conn, cli="9")
    for i, n in enumerate([100, 85, 70, 55, 40]):  # compra a cada 15 dias; sumiu há 40
        venda(conn, f"V{i}", "9", n, 300)
    pontos.atribuir_compras_proprias(conn)
    t = [x for x in regras.fila_hoje(conn, HOJE) if x["tipo"] == "RISCO"]
    assert len(t) == 1 and t[0]["profissional_id"] == pid and "40 dias" in t[0]["detalhe"]


def test_eletricista_regular_nao_esta_em_risco(conn):
    cliente(conn, "9", seg="039")
    profissional(conn, cli="9")
    for i, n in enumerate([60, 45, 30, 15, 2]):
        venda(conn, f"V{i}", "9", n, 300)
    pontos.atribuir_compras_proprias(conn)
    assert "RISCO" not in tipos(conn)


def test_aniversario(conn):
    cliente(conn, "1", nasc="1980-" + HOJE.strftime("%m-%d"), ult=dia(30))
    assert tipos(conn) == ["ANIV"]


def test_painel_mede_identificacao(conn):
    cliente(conn, "1")
    venda(conn, "V1", "1", 3, 300)
    venda(conn, "V2", "00000000", 3, 700)
    m = regras.painel(conn, HOJE)
    assert m["pct_identificado"] == 30.0 and m["pct_contatavel"] == 30.0


# ── aplicação web ───────────────────────────────────────────────────────────
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_COOKIE_INSECURE", "1")
    from app import create_app

    app = create_app(tmp_path)
    c = sqlite3.connect(str(tmp_path / "crm.sqlite"))
    c.row_factory = sqlite3.Row
    auth.criar_usuario(c, "admin", "Admin", "senha-boa-123", admin=True)
    auth.criar_usuario(c, "balcao", "Balcão", "senha-boa-123")
    c.close()
    return app.test_client()


def entrar(client, login="admin"):
    client.get("/login")
    r = client.post("/login", data={"login": login, "senha": "senha-boa-123", "csrf": csrf(client)})
    assert r.status_code == 302


def csrf(client):
    with client.session_transaction() as s:
        return s["csrf"]


def test_exige_login(client):
    assert client.get("/").status_code == 302
    assert client.get("/painel").status_code == 302


def test_login_errado(client):
    client.get("/login")
    r = client.post("/login", data={"login": "admin", "senha": "errada", "csrf": csrf(client)})
    assert r.status_code == 200 and "incorretos" in r.get_data(as_text=True)


def test_post_sem_csrf_e_recusado(client):
    entrar(client)
    assert client.post("/contato", data={"tipo": "ORC"}).status_code == 400


def test_telas_abrem(client):
    entrar(client)
    for rota in ("/", "/painel", "/vendas", "/profissionais", "/profissionais/novo",
                 "/profissionais/importar", "/orcamentos", "/config"):
        assert client.get(rota).status_code == 200, rota


def test_balcao_nao_acessa_config(client):
    entrar(client, "balcao")
    assert client.get("/config").status_code == 403


def test_fluxo_cadastrar_atribuir_resgatar(client, tmp_path):
    entrar(client)
    r = client.post("/profissionais/novo", data={"csrf": csrf(client), "nome": "Gilmar Teste", "telefone": "(77) 99961-4370"})
    assert r.status_code == 302
    c = sqlite3.connect(str(tmp_path / "crm.sqlite"))
    c.row_factory = sqlite3.Row
    c.execute("INSERT INTO cliente VALUES ('ELE','1','CLI','5577999000001','007',NULL,NULL,1)")
    c.execute("INSERT INTO venda VALUES ('ELE','V1',?, '1','0020','MAILSON',1000,NULL)", (date.today().isoformat(),))
    c.commit()
    r = client.post("/vendas", data={"csrf": csrf(client), "p:ELE:V1": "1", "dia": date.today().isoformat()})
    assert r.status_code == 302
    assert c.execute("SELECT SUM(valor) FROM pontos").fetchone()[0] == 10.0
    r = client.post("/profissionais/1/resgate", data={"csrf": csrf(client), "valor": "4,00"})
    assert r.status_code == 302
    assert c.execute("SELECT SUM(valor) FROM pontos").fetchone()[0] == 6.0
    assert client.get("/profissionais/1").status_code == 200
    c.close()
