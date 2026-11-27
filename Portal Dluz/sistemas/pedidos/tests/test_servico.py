import io
import threading
from datetime import date, datetime, timedelta, timezone

import pytest
from werkzeug.datastructures import FileStorage

from app import notificacoes, servico, workflow
from app.db import get_db
from app.arquivos import PdfInvalido
from app.servico import (
    DadosInvalidos,
    DataInvalida,
    JaDecidido,
    PedidoNaoEncontrado,
    ValorInvalido,
    aplicar_acao,
    criar_pedido,
    hoje_sp,
    parse_data,
    parse_valor_brl,
)

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
HOJE = date(2026, 10, 23)
HOJE_REAL = servico.hoje_sp  # guardado antes de o fixture trocar a função


@pytest.fixture(autouse=True)
def relogio_fixo(monkeypatch):
    monkeypatch.setattr(servico, "hoje_sp", lambda: HOJE)


# --- auxiliares ---------------------------------------------------------------


def arquivo(conteudo=PDF, nome="pedido.pdf"):
    return FileStorage(stream=io.BytesIO(conteudo), filename=nome)


def linha_usuario(db, usuario):
    """O serviço recebe a linha do banco (como g.usuario), não o dataclass dos testes."""
    return db.execute("SELECT * FROM usuarios WHERE id = ?", (usuario.id,)).fetchone()


@pytest.fixture
def cenario(db, criar_usuario_teste):
    """Linhas de usuário por papel: um solicitante, outro solicitante, dois gestores, fiscal e admin."""
    papeis = {
        "solicitante": criar_usuario_teste("solicitante"),
        "outro": criar_usuario_teste("solicitante"),
        "gestor1": criar_usuario_teste("gestor"),
        "gestor2": criar_usuario_teste("gestor"),
        "fiscal": criar_usuario_teste("fiscal"),
        "admin": criar_usuario_teste("admin"),
    }
    return {nome: linha_usuario(db, u) for nome, u in papeis.items()}


def novo_pedido(db, usuario, **campos):
    dados = {
        "cliente": "Madeireira Boa Vista",
        "valor": "1.234,56",
        "descricao": "Compra de madeira",
        "arquivo": arquivo(),
    }
    dados.update(campos)
    return criar_pedido(db, usuario, **dados)


def pedido(db, pedido_id):
    return db.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()


def historico(db, pedido_id=None):
    consulta = "SELECT * FROM historico"
    parametros = ()
    if pedido_id is not None:
        consulta += " WHERE pedido_id = ?"
        parametros = (pedido_id,)
    return db.execute(consulta + " ORDER BY id", parametros).fetchall()


def contar(db, tabela):
    return db.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]


def pdfs_em_disco(app):
    return sorted(p.name for p in (app.config["DATA_DIR"] / "pdfs").iterdir())


def forcar_status(db, pedido_id, status):
    db.execute("UPDATE pedidos SET status = ? WHERE id = ?", (status, pedido_id))


# --- parse_valor_brl ------------------------------------------------------------


@pytest.mark.parametrize(
    "texto, centavos",
    [
        ("1.234,56", 123456),
        ("10", 1000),
        ("0,5", 50),
        ("10,5", 1050),
        ("10,50", 1050),
        ("0,01", 1),
        ("0,50", 50),
        ("1.234", 123400),
        ("1.234.567", 123456700),
        ("R$ 1.234,56", 123456),
        ("R$1.234,56", 123456),
        ("r$ 10", 1000),
        ("  10  ", 1000),
        ("  R$   10,5  ", 1050),
        ("999.999.999,99", 99_999_999_999),
        ("999999999", 99_999_999_900),
        ("1234", 123400),
    ],
)
def test_parse_valor_brl_aceita(texto, centavos):
    resultado = parse_valor_brl(texto)
    assert resultado == centavos
    assert type(resultado) is int


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "   ",
        "R$",
        "R$ ",
        "0",
        "0,00",
        "0,0",
        "0.000",
        "-5",
        "-0,50",
        "R$ -5",
        "+5",
        "abc",
        "10 reais",
        "1e3",
        "12,345",
        "1,2,3",
        "10,5,0",
        "12.5",
        "12.50",
        "1.23,45",
        "1234.567",
        "1.2345",
        ".5",
        ",50",
        "1.234,",
        "0.500",
        "0.123",
        "01.234",
        "000.001",
        "0001",
        "00,50",
        "007",
        "10,",
        "1 000",
        "١٢٣",  # dígitos árabes: int() aceitaria, o sistema não
        "1.000.000.000,00",
        "1000000000",
        "999.999.999,991",
        "9" * 40,
        None,
        12.5,
        1000,
    ],
)
def test_parse_valor_brl_recusa(texto):
    with pytest.raises(ValorInvalido):
        parse_valor_brl(texto)


def test_parse_valor_brl_pede_virgula_quando_usam_ponto_decimal():
    with pytest.raises(ValorInvalido, match="vírgula"):
        parse_valor_brl("12.5")
    with pytest.raises(ValorInvalido, match="vírgula"):
        parse_valor_brl("12.50")


def test_valor_invalido_e_um_value_error():
    assert issubclass(ValorInvalido, ValueError)
    assert issubclass(DataInvalida, ValueError)
    assert issubclass(DadosInvalidos, ValueError)
    assert issubclass(PedidoNaoEncontrado, LookupError)


# --- parse_data / hoje_sp -------------------------------------------------------


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("2026-10-23", date(2026, 10, 23)),
        ("23/10/2026", date(2026, 10, 23)),
        ("  23/10/2026  ", date(2026, 10, 23)),
        ("  2026-10-23\n", date(2026, 10, 23)),
        ("01/01/2000", date(2000, 1, 1)),
        ("2024-02-29", date(2024, 2, 29)),
        ("22/10/2026", date(2026, 10, 22)),
    ],
)
def test_parse_data_aceita(texto, esperado):
    assert parse_data(texto) == esperado


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "   ",
        None,
        20261023,
        "20261023",
        "23-10-2026",
        "2026/10/23",
        "23/10/26",
        "2026-1-5",
        "5/1/2026",
        "amanhã",
        "31/02/2026",
        "2026-02-30",
        "2025-02-29",
        "00/10/2026",
        "23/13/2026",
        "24/10/2026",  # amanhã
        "2026-10-24",
        "01/01/2027",
        "31/12/1999",  # antes do ano 2000
        "0001-01-01",
        "23/10/2026 10:00",
        "２０２６-10-23",  # dígitos de largura total
    ],
)
def test_parse_data_recusa(texto):
    with pytest.raises(DataInvalida):
        parse_data(texto)


def test_parse_data_futuro_so_com_permissao():
    assert parse_data("24/10/2026", permitir_futuro=True) == date(2026, 10, 24)
    assert parse_data("2030-01-01", permitir_futuro=True) == date(2030, 1, 1)
    with pytest.raises(DataInvalida):
        parse_data("1999-12-31", permitir_futuro=True)  # o piso de 2000 continua valendo


def test_hoje_sp_e_a_data_de_sao_paulo():
    hoje = HOJE_REAL()
    utc = datetime.now(timezone.utc).date()
    assert type(hoje) is date
    assert abs(hoje - utc) <= timedelta(days=1)


# --- criar_pedido ---------------------------------------------------------------


def test_criar_pedido_grava_pendente_historico_e_avisos(app, db, cenario):
    solicitante = cenario["solicitante"]
    pedido_id = criar_pedido(
        db, solicitante, "  Madeireira Boa Vista  ", "R$ 1.234,56", "  Compra de madeira\nlote 2  ", arquivo(nome="c:\\x\\ped.pdf")
    )

    linha = pedido(db, pedido_id)
    assert linha["status"] == "pendente"
    assert linha["cliente"] == "Madeireira Boa Vista"
    assert linha["valor_centavos"] == 123456
    assert linha["descricao"] == "Compra de madeira\nlote 2"
    assert linha["solicitante_id"] == solicitante["id"]
    assert linha["reenvio_de"] is None
    assert linha["pdf_nome_original"] == "ped.pdf"
    assert pdfs_em_disco(app) == [linha["pdf_arquivo"]]
    assert linha["criado_em"] == linha["atualizado_em"]
    assert linha["data_pedido"] is None and linha["numero_nf"] is None and linha["data_chegada"] is None

    (h,) = historico(db, pedido_id)
    assert (h["acao"], h["status_anterior"], h["status_novo"], h["usuario_id"]) == (
        "criar", None, "pendente", solicitante["id"],
    )
    assert h["criado_em"] == linha["criado_em"]

    avisados = [a["usuario_id"] for a in db.execute("SELECT * FROM avisos ORDER BY id")]
    assert avisados == [solicitante["id"], cenario["gestor1"]["id"], cenario["gestor2"]["id"]]


def test_titulo_usa_o_id(db, cenario):
    primeiro = novo_pedido(db, cenario["solicitante"])
    segundo = novo_pedido(db, cenario["solicitante"])
    assert (primeiro, segundo) == (1, 2)
    assert workflow.titulo_pedido(primeiro) == "Novo Pedido - 1"
    assert workflow.titulo_pedido(segundo) == "Novo Pedido - 2"
    assuntos = [a["assunto"] for a in db.execute("SELECT assunto FROM avisos ORDER BY id")]
    assert assuntos[0].startswith("Novo Pedido - 1:")
    assert assuntos[-1].startswith("Novo Pedido - 2:")


@pytest.mark.parametrize("perfil", ["gestor1", "fiscal", "admin"])
def test_so_solicitante_cria_pedido(app, db, cenario, perfil):
    with pytest.raises(workflow.NaoAutorizado):
        novo_pedido(db, cenario[perfil])
    assert contar(db, "pedidos") == 0
    assert contar(db, "historico") == 0
    assert pdfs_em_disco(app) == []


@pytest.mark.parametrize(
    "campos, trecho",
    [
        ({"cliente": ""}, "cliente"),
        ({"cliente": "   "}, "cliente"),
        ({"cliente": "x" * 201}, "cliente"),
        ({"cliente": "  " + "x" * 201 + "  "}, "cliente"),
        ({"cliente": "Fulano\nSicrano"}, "cliente"),
        ({"cliente": "Fulano\x00"}, "cliente"),
        ({"cliente": None}, "cliente"),
        ({"descricao": ""}, "descrição"),
        ({"descricao": " \n\t "}, "descrição"),
        ({"descricao": "x" * 2001}, "descrição"),
        ({"descricao": "ok\x00ok"}, "descrição"),
        ({"descricao": "ok\x1bok"}, "descrição"),
        ({"descricao": None}, "descrição"),
        ({"valor": "abc"}, None),
        ({"valor": ""}, None),
        ({"valor": "0"}, None),
    ],
)
def test_dados_invalidos_nao_deixam_rastro(app, db, cenario, campos, trecho):
    esperado = ValorInvalido if "valor" in campos else DadosInvalidos
    with pytest.raises(esperado) as erro:
        novo_pedido(db, cenario["solicitante"], **campos)
    if trecho:
        assert trecho in str(erro.value).lower()
    assert contar(db, "pedidos") == 0
    assert contar(db, "historico") == 0
    assert contar(db, "avisos") == 0
    assert pdfs_em_disco(app) == []


def test_limites_de_cliente_e_descricao(db, cenario):
    solicitante = cenario["solicitante"]
    ped = novo_pedido(db, solicitante, cliente="c" * 200, descricao="d" * 2000)
    linha = pedido(db, ped)
    assert len(linha["cliente"]) == 200 and len(linha["descricao"]) == 2000
    # os espaços das pontas não contam
    ped = novo_pedido(db, solicitante, cliente=" " + "c" * 200 + " ", descricao=" " + "d" * 2000 + "\n")
    assert len(pedido(db, ped)["cliente"]) == 200


def test_descricao_aceita_quebras_de_linha_e_tab_e_normaliza_crlf(db, cenario):
    ped = novo_pedido(db, cenario["solicitante"], descricao="linha 1\r\nlinha 2\n\tlinha 3\rlinha 4")
    assert pedido(db, ped)["descricao"] == "linha 1\nlinha 2\n\tlinha 3\nlinha 4"


def test_pdf_invalido_nao_deixa_rastro(app, db, cenario):
    for arq in (arquivo(b"nao e pdf"), arquivo(b""), arquivo(nome="x.txt"), None):
        with pytest.raises(PdfInvalido):
            novo_pedido(db, cenario["solicitante"], arquivo=arq)
    assert contar(db, "pedidos") == 0
    assert contar(db, "historico") == 0
    assert contar(db, "avisos") == 0
    assert pdfs_em_disco(app) == []


def test_reenvio_de_aponta_para_pedido_reprovado_existente(db, cenario):
    solicitante, gestor = cenario["solicitante"], cenario["gestor1"]
    original = novo_pedido(db, solicitante)
    aplicar_acao(db, original, "reprovar", gestor, {"comentario": "Valor acima do orçado."})

    reenvio = novo_pedido(db, solicitante, reenvio_de=original)
    assert pedido(db, reenvio)["reenvio_de"] == original
    assert pedido(db, reenvio)["status"] == "pendente"

    # o pedido original continua reprovado e intacto
    assert pedido(db, original)["status"] == "reprovado"


def test_reenvio_de_recusa_pedido_inexistente(app, db, cenario):
    antes = contar(db, "pedidos")
    with pytest.raises(DadosInvalidos):
        novo_pedido(db, cenario["solicitante"], reenvio_de=999)
    assert contar(db, "pedidos") == antes
    assert pdfs_em_disco(app) == []


def test_reenvio_de_recusa_pedido_que_nao_esta_reprovado(app, db, cenario):
    solicitante = cenario["solicitante"]
    pendente = novo_pedido(db, solicitante)
    aprovado = novo_pedido(db, solicitante)
    aplicar_acao(db, aprovado, "aprovar", cenario["gestor1"], {})
    antes = contar(db, "pedidos")
    for alvo in (pendente, aprovado):
        with pytest.raises(DadosInvalidos):
            novo_pedido(db, solicitante, reenvio_de=alvo)
    assert contar(db, "pedidos") == antes
    assert len(pdfs_em_disco(app)) == antes


def test_reenvio_de_recusa_pedido_de_outro_solicitante(db, cenario):
    original = novo_pedido(db, cenario["outro"])
    aplicar_acao(db, original, "reprovar", cenario["gestor1"], {"comentario": "Não."})
    antes = contar(db, "pedidos")
    with pytest.raises(DadosInvalidos):
        novo_pedido(db, cenario["solicitante"], reenvio_de=original)
    assert contar(db, "pedidos") == antes


def test_reenvio_recusado_diz_a_mesma_coisa_em_qualquer_caso(db, cenario):
    """Inexistente, de outro solicitante e não reprovado têm a mesma mensagem: nada de oráculo."""
    alheio = novo_pedido(db, cenario["outro"])
    aplicar_acao(db, alheio, "reprovar", cenario["gestor1"], {"comentario": "Não."})
    pendente = novo_pedido(db, cenario["solicitante"])
    mensagens = set()
    for alvo in (999, alheio, pendente):
        with pytest.raises(DadosInvalidos) as erro:
            novo_pedido(db, cenario["solicitante"], reenvio_de=alvo)
        mensagens.add(str(erro.value))
    assert mensagens == {"O pedido anterior não pode ser usado como reenvio."}


def test_reenvio_de_aceita_reprovado_que_foi_arquivado(db, cenario):
    # Arquivar um reprovado não apaga a reprovação: o solicitante ainda pode reenviar.
    solicitante = cenario["solicitante"]
    original = novo_pedido(db, solicitante)
    aplicar_acao(db, original, "reprovar", cenario["gestor1"], {"comentario": "Faltou a cotação."})
    aplicar_acao(db, original, "arquivar", cenario["fiscal"], {})
    assert pedido(db, original)["status"] == "arquivado"

    reenvio = novo_pedido(db, solicitante, reenvio_de=original)
    assert pedido(db, reenvio)["reenvio_de"] == original
    assert pedido(db, original)["status"] == "arquivado"


def test_reenvio_de_arquivado_recusa_aprovado_e_alheio_com_a_mesma_mensagem(db, cenario):
    aprovado = novo_pedido(db, cenario["solicitante"])
    aplicar_acao(db, aprovado, "aprovar", cenario["gestor1"], {})
    forcar_status(db, aprovado, "arquivado")  # o caminho normal: enviado, faturado, recebido
    alheio = novo_pedido(db, cenario["outro"])
    aplicar_acao(db, alheio, "reprovar", cenario["gestor1"], {"comentario": "Não."})
    aplicar_acao(db, alheio, "arquivar", cenario["fiscal"], {})
    sem_decisao = novo_pedido(db, cenario["solicitante"])
    forcar_status(db, sem_decisao, "arquivado")

    antes = contar(db, "pedidos")
    mensagens = set()
    for alvo in (aprovado, alheio, sem_decisao):
        with pytest.raises(DadosInvalidos) as erro:
            novo_pedido(db, cenario["solicitante"], reenvio_de=alvo)
        mensagens.add(str(erro.value))
    assert mensagens == {"O pedido anterior não pode ser usado como reenvio."}
    assert contar(db, "pedidos") == antes


@pytest.mark.parametrize("valor", ["1", "x", 1.0, True, -1, 0])
def test_reenvio_de_recusa_tipos_e_valores_estranhos(db, cenario, valor):
    with pytest.raises(DadosInvalidos):
        novo_pedido(db, cenario["solicitante"], reenvio_de=valor)


def test_falha_ao_gravar_aviso_na_criacao_nao_deixa_pedido_nem_arquivo(app, db, cenario, monkeypatch):
    def quebra(db, evento, pedido):
        raise RuntimeError("falha ao gravar aviso")

    monkeypatch.setattr(notificacoes, "registrar_avisos", quebra)
    with pytest.raises(RuntimeError, match="falha ao gravar aviso"):
        novo_pedido(db, cenario["solicitante"])
    assert contar(db, "pedidos") == 0
    assert contar(db, "historico") == 0
    assert contar(db, "avisos") == 0
    assert pdfs_em_disco(app) == []


def test_falha_na_criacao_depois_de_gravar_avisos_desfaz_tudo(app, db, cenario, monkeypatch):
    """O erro vem depois de os avisos já terem sido inseridos: nenhum pode sobrar."""
    original = notificacoes.registrar_avisos

    def grava_e_quebra(db, evento, pedido):
        original(db, evento, pedido)
        raise RuntimeError("depois dos avisos")

    monkeypatch.setattr(notificacoes, "registrar_avisos", grava_e_quebra)
    with pytest.raises(RuntimeError):
        novo_pedido(db, cenario["solicitante"])
    assert (contar(db, "pedidos"), contar(db, "historico"), contar(db, "avisos")) == (0, 0, 0)
    assert pdfs_em_disco(app) == []


# --- aplicar_acao: decisão do gestor -------------------------------------------


def test_aprovar_com_e_sem_comentario(db, cenario):
    solicitante, gestor = cenario["solicitante"], cenario["gestor1"]
    a = novo_pedido(db, solicitante)
    b = novo_pedido(db, solicitante)
    linha = aplicar_acao(db, a, "aprovar", gestor, {"comentario": "  Pode seguir.  "})
    assert linha["status"] == "aprovado"
    assert historico(db, a)[-1]["comentario"] == "Pode seguir."
    aplicar_acao(db, b, "aprovar", gestor, {})
    assert historico(db, b)[-1]["comentario"] is None
    aplicar_acao(db, novo_pedido(db, solicitante), "aprovar", gestor, {"comentario": "   "})
    assert historico(db)[-1]["comentario"] is None


def test_aplicar_acao_devolve_a_linha_atualizada(db, cenario):
    ped = novo_pedido(db, cenario["solicitante"])
    linha = aplicar_acao(db, ped, "aprovar", cenario["gestor1"], {})
    assert linha["id"] == ped
    assert linha["status"] == "aprovado"
    assert linha == pedido(db, ped)


def test_reprovar_exige_motivo(db, cenario):
    ped = novo_pedido(db, cenario["solicitante"])
    gestor = cenario["gestor1"]
    for dados in ({}, {"comentario": ""}, {"comentario": "   \n "}, {"comentario": None}):
        with pytest.raises(DadosInvalidos, match="Informe o motivo da reprovação."):
            aplicar_acao(db, ped, "reprovar", gestor, dados)
    assert pedido(db, ped)["status"] == "pendente"
    assert len(historico(db, ped)) == 1

    linha = aplicar_acao(db, ped, "reprovar", gestor, {"comentario": "  Fora do orçamento.  "})
    assert linha["status"] == "reprovado"
    assert historico(db, ped)[-1]["comentario"] == "Fora do orçamento."


def test_comentario_ate_1000_caracteres(db, cenario):
    gestor, solicitante = cenario["gestor1"], cenario["solicitante"]
    ped = novo_pedido(db, solicitante)
    with pytest.raises(DadosInvalidos):
        aplicar_acao(db, ped, "reprovar", gestor, {"comentario": "m" * 1001})
    with pytest.raises(DadosInvalidos):
        aplicar_acao(db, ped, "aprovar", gestor, {"comentario": "m" * 1001})
    assert pedido(db, ped)["status"] == "pendente"
    aplicar_acao(db, ped, "reprovar", gestor, {"comentario": "m" * 1000})
    assert len(historico(db, ped)[-1]["comentario"]) == 1000


def test_comentario_com_caractere_de_controle_e_recusado(db, cenario):
    ped = novo_pedido(db, cenario["solicitante"])
    with pytest.raises(DadosInvalidos):
        aplicar_acao(db, ped, "aprovar", cenario["gestor1"], {"comentario": "a\x00b"})
    with pytest.raises(DadosInvalidos):
        aplicar_acao(db, ped, "aprovar", cenario["gestor1"], {"comentario": 5})
    assert pedido(db, ped)["status"] == "pendente"


def test_dois_gestores_so_o_primeiro_vale(db, cenario):
    ped = novo_pedido(db, cenario["solicitante"])
    gestor1, gestor2 = cenario["gestor1"], cenario["gestor2"]
    aplicar_acao(db, ped, "aprovar", gestor1, {"comentario": "Ok."})
    primeira = historico(db, ped)[-1]
    avisos_antes = contar(db, "avisos")

    with pytest.raises(JaDecidido) as erro:
        aplicar_acao(db, ped, "reprovar", gestor2, {"comentario": "Não."})

    assert erro.value.decisor == gestor1["nome"]
    assert erro.value.quando == primeira["criado_em"]
    assert gestor1["nome"] in str(erro.value)
    assert pedido(db, ped)["status"] == "aprovado"
    assert [h["id"] for h in historico(db, ped)][-1] == primeira["id"]
    assert len(historico(db, ped)) == 2
    assert contar(db, "avisos") == avisos_antes


def test_gestor_que_chega_depois_de_reprovacao_tambem_ve_ja_decidido(db, cenario):
    ped = novo_pedido(db, cenario["solicitante"])
    aplicar_acao(db, ped, "reprovar", cenario["gestor1"], {"comentario": "Não."})
    with pytest.raises(JaDecidido) as erro:
        aplicar_acao(db, ped, "aprovar", cenario["gestor2"], {})
    assert erro.value.decisor == cenario["gestor1"]["nome"]


def test_ja_decidido_aponta_a_decisao_mesmo_com_o_pedido_mais_adiante(db, cenario):
    ped = novo_pedido(db, cenario["solicitante"])
    aplicar_acao(db, ped, "aprovar", cenario["gestor1"], {})
    aplicar_acao(db, ped, "confirmar_envio", cenario["solicitante"], {"data_pedido": "2026-10-22"})
    with pytest.raises(JaDecidido) as erro:
        aplicar_acao(db, ped, "aprovar", cenario["gestor2"], {})
    assert erro.value.decisor == cenario["gestor1"]["nome"]


def test_fiscal_nao_ve_ja_decidido_ao_tentar_aprovar(db, cenario):
    ped = novo_pedido(db, cenario["solicitante"])
    aplicar_acao(db, ped, "aprovar", cenario["gestor1"], {})
    with pytest.raises(workflow.TransicaoInvalida):
        aplicar_acao(db, ped, "aprovar", cenario["fiscal"], {})


def test_corrida_entre_leitura_e_gravacao_vira_ja_decidido(db, cenario, monkeypatch):
    """O outro gestor decide depois da leitura do pedido e antes da nossa gravação."""
    ped = novo_pedido(db, cenario["solicitante"])
    gestor1, gestor2 = cenario["gestor1"], cenario["gestor2"]
    original = workflow.proximo_status

    def decide_no_meio(status, acao, perfil, eh_dono=False):
        resultado = original(status, acao, perfil, eh_dono)
        db.execute("UPDATE pedidos SET status = 'aprovado' WHERE id = ?", (ped,))
        db.execute(
            "INSERT INTO historico (pedido_id, usuario_id, acao, status_anterior, status_novo, criado_em)"
            " VALUES (?, ?, 'aprovar', 'pendente', 'aprovado', '2026-10-23T12:00:00+00:00')",
            (ped, gestor1["id"]),
        )
        return resultado

    monkeypatch.setattr(workflow, "proximo_status", decide_no_meio)
    with pytest.raises(JaDecidido) as erro:
        aplicar_acao(db, ped, "reprovar", gestor2, {"comentario": "Não."})

    assert erro.value.decisor == gestor1["nome"]
    assert erro.value.quando == "2026-10-23T12:00:00+00:00"
    assert pedido(db, ped)["status"] == "aprovado"
    assert [h["acao"] for h in historico(db, ped)] == ["criar", "aprovar"]


def test_mensagem_de_ja_decidido_usa_data_e_hora_de_sao_paulo(db, cenario):
    ped = novo_pedido(db, cenario["solicitante"])
    aplicar_acao(db, ped, "aprovar", cenario["gestor1"], {})
    db.execute("UPDATE historico SET criado_em = '2026-10-24T01:30:00+00:00' WHERE acao = 'aprovar'")
    with pytest.raises(JaDecidido) as erro:
        aplicar_acao(db, ped, "reprovar", cenario["gestor2"], {"comentario": "Não."})
    assert erro.value.quando == "2026-10-24T01:30:00+00:00"
    assert str(erro.value) == f"Este pedido já foi decidido por {cenario['gestor1']['nome']} em 23/10/2026 22:30."


def test_corrida_em_acao_que_nao_e_decisao_vira_transicao_invalida(db, cenario, monkeypatch):
    ped = novo_pedido(db, cenario["solicitante"])
    aplicar_acao(db, ped, "aprovar", cenario["gestor1"], {})
    aplicar_acao(db, ped, "confirmar_envio", cenario["solicitante"], {"data_pedido": "2026-10-22"})
    original = workflow.proximo_status

    def outro_fiscal_chega_antes(status, acao, perfil, eh_dono=False):
        resultado = original(status, acao, perfil, eh_dono)
        db.execute("UPDATE pedidos SET status = 'faturado', numero_nf = 'NF-1' WHERE id = ?", (ped,))
        return resultado

    monkeypatch.setattr(workflow, "proximo_status", outro_fiscal_chega_antes)
    with pytest.raises(workflow.TransicaoInvalida):
        aplicar_acao(db, ped, "informar_nf", cenario["fiscal"], {"numero_nf": "NF-2"})
    assert pedido(db, ped)["numero_nf"] == "NF-1"
    assert len(historico(db, ped)) == 3


def test_decisao_simultanea_de_dois_gestores_em_threads(app, db, cenario):
    ped = novo_pedido(db, cenario["solicitante"])
    gestor1, gestor2 = cenario["gestor1"]["id"], cenario["gestor2"]["id"]
    barreira = threading.Barrier(2, timeout=10)
    resultados = {}

    def decidir(chave, usuario_id, acao, dados):
        try:
            with app.app_context():
                conexao = get_db()
                usuario = conexao.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
                barreira.wait()
                resultados[chave] = aplicar_acao(conexao, ped, acao, usuario, dados)
        except BaseException as erro:  # o teste confere o tipo depois
            resultados[chave] = erro

    threads = [
        threading.Thread(target=decidir, args=("g1", gestor1, "aprovar", {"comentario": "Ok."})),
        threading.Thread(target=decidir, args=("g2", gestor2, "reprovar", {"comentario": "Não."})),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)

    vencedores = {k: v for k, v in resultados.items() if not isinstance(v, BaseException)}
    perdedores = {k: v for k, v in resultados.items() if isinstance(v, BaseException)}
    assert len(vencedores) == 1 and len(perdedores) == 1, resultados
    (chave_vencedor,) = vencedores
    (erro,) = perdedores.values()
    assert isinstance(erro, JaDecidido), repr(erro)
    nome_vencedor = cenario["gestor1" if chave_vencedor == "g1" else "gestor2"]["nome"]
    assert erro.decisor == nome_vencedor

    decisoes = db.execute(
        "SELECT * FROM historico WHERE pedido_id = ? AND acao IN ('aprovar', 'reprovar')", (ped,)
    ).fetchall()
    assert len(decisoes) == 1
    assert erro.quando == decisoes[0]["criado_em"]
    assert pedido(db, ped)["status"] == decisoes[0]["status_novo"]
    # só os avisos de 'criar' (3) e da decisão que valeu (aprovar: 2 / reprovar: 1)
    esperado = 3 + (2 if decisoes[0]["acao"] == "aprovar" else 1)
    assert contar(db, "avisos") == esperado


# --- aplicar_acao: fluxo completo ----------------------------------------------


def test_fluxo_completo_registra_historico_em_cada_passo(db, cenario):
    s, g, f, adm = cenario["solicitante"], cenario["gestor1"], cenario["fiscal"], cenario["admin"]
    ped = novo_pedido(db, s)
    passos = [
        ("aprovar", g, {"comentario": "Aprovado."}, "pendente", "aprovado"),
        ("confirmar_envio", s, {"data_pedido": "22/10/2026"}, "aprovado", "enviado"),
        ("informar_nf", f, {"numero_nf": " NF-123 "}, "enviado", "faturado"),
        ("marcar_chegada", adm, {"data_chegada": "2026-10-23"}, "faturado", "recebido"),
        ("arquivar", f, {}, "recebido", "arquivado"),
    ]
    contagem_avisos = []
    for acao, usuario, dados, anterior, novo in passos:
        linha = aplicar_acao(db, ped, acao, usuario, dados)
        assert linha["status"] == novo
        contagem_avisos.append(contar(db, "avisos"))

    linha = pedido(db, ped)
    assert linha["status"] == "arquivado"
    assert linha["data_pedido"] == "2026-10-22"
    assert linha["numero_nf"] == "NF-123"
    assert linha["data_chegada"] == "2026-10-23"

    registros = historico(db, ped)
    esperado = [("criar", None, "pendente", s["id"])] + [
        (acao, anterior, novo, usuario["id"]) for acao, usuario, _, anterior, novo in passos
    ]
    assert [(h["acao"], h["status_anterior"], h["status_novo"], h["usuario_id"]) for h in registros] == esperado
    assert registros[1]["comentario"] == "Aprovado."
    assert all(h["criado_em"] for h in registros)
    # avisos acumulados: criar 3; aprovar +2; envio +1; nf +1; chegada +1; arquivar +0
    assert contagem_avisos == [5, 6, 7, 8, 8]


def test_avisos_de_cada_passo_chegam_aos_destinatarios_certos(db, cenario):
    s, g, f = cenario["solicitante"], cenario["gestor1"], cenario["fiscal"]
    ped = novo_pedido(db, s)

    def destinos_do_ultimo(n):
        linhas = db.execute("SELECT usuario_id FROM avisos ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return sorted(l["usuario_id"] for l in linhas)

    aplicar_acao(db, ped, "aprovar", g, {})
    assert destinos_do_ultimo(2) == sorted([s["id"], f["id"]])
    aplicar_acao(db, ped, "confirmar_envio", s, {"data_pedido": "2026-10-22"})
    assert destinos_do_ultimo(1) == [f["id"]]
    aplicar_acao(db, ped, "informar_nf", f, {"numero_nf": "1"})
    assert destinos_do_ultimo(1) == [s["id"]]
    aplicar_acao(db, ped, "marcar_chegada", f, {})
    assert destinos_do_ultimo(1) == [s["id"]]


def test_reprovacao_avisa_o_solicitante_com_o_motivo(db, cenario):
    ped = novo_pedido(db, cenario["solicitante"])
    antes = contar(db, "avisos")
    aplicar_acao(db, ped, "reprovar", cenario["gestor1"], {"comentario": "Preço acima da tabela."})
    novos = db.execute("SELECT * FROM avisos WHERE id > ?", (antes,)).fetchall()
    assert [a["usuario_id"] for a in novos] == [cenario["solicitante"]["id"]]
    assert "Preço acima da tabela." in novos[0]["corpo"]


def test_arquivar_pedido_reprovado(db, cenario):
    ped = novo_pedido(db, cenario["solicitante"])
    aplicar_acao(db, ped, "reprovar", cenario["gestor1"], {"comentario": "Não."})
    linha = aplicar_acao(db, ped, "arquivar", cenario["admin"], {})
    assert linha["status"] == "arquivado"
    assert historico(db, ped)[-1]["status_anterior"] == "reprovado"


def test_atualizado_em_muda_e_criado_em_nao(db, cenario):
    ped = novo_pedido(db, cenario["solicitante"])
    db.execute("UPDATE pedidos SET criado_em = '2020-01-01T00:00:00+00:00', atualizado_em = '2020-01-01T00:00:00+00:00'")
    linha = aplicar_acao(db, ped, "aprovar", cenario["gestor1"], {})
    assert linha["criado_em"] == "2020-01-01T00:00:00+00:00"
    assert linha["atualizado_em"] != "2020-01-01T00:00:00+00:00"


# --- aplicar_acao: dados de cada ação ------------------------------------------


def levar_a(db, cenario, status):
    ped = novo_pedido(db, cenario["solicitante"])
    if status == "pendente":
        return ped
    aplicar_acao(db, ped, "aprovar", cenario["gestor1"], {})
    if status == "aprovado":
        return ped
    aplicar_acao(db, ped, "confirmar_envio", cenario["solicitante"], {"data_pedido": "2026-10-22"})
    if status == "enviado":
        return ped
    aplicar_acao(db, ped, "informar_nf", cenario["fiscal"], {"numero_nf": "NF-1"})
    return ped


def test_confirmar_envio_exige_data_valida_e_nao_futura(db, cenario):
    ped = levar_a(db, cenario, "aprovado")
    s = cenario["solicitante"]
    for dados in ({}, {"data_pedido": ""}, {"data_pedido": "ontem"}, {"data_pedido": "24/10/2026"}, {"data_pedido": "31/02/2026"}):
        with pytest.raises(DataInvalida):
            aplicar_acao(db, ped, "confirmar_envio", s, dados)
    assert pedido(db, ped)["status"] == "aprovado"
    assert pedido(db, ped)["data_pedido"] is None
    linha = aplicar_acao(db, ped, "confirmar_envio", s, {"data_pedido": "23/10/2026"})
    assert linha["data_pedido"] == "2026-10-23"


def test_solicitante_que_nao_e_dono_nao_confirma_o_envio(db, cenario):
    ped = levar_a(db, cenario, "aprovado")
    with pytest.raises(workflow.NaoAutorizado):
        aplicar_acao(db, ped, "confirmar_envio", cenario["outro"], {"data_pedido": "2026-10-22"})
    assert pedido(db, ped)["status"] == "aprovado"
    assert len(historico(db, ped)) == 2


def test_informar_nf_exige_numero_ate_30_caracteres(db, cenario):
    ped = levar_a(db, cenario, "enviado")
    f = cenario["fiscal"]
    for dados in (
        {},
        {"numero_nf": ""},
        {"numero_nf": "   "},
        {"numero_nf": None},
        {"numero_nf": "N" * 31},
        {"numero_nf": "12\n34"},
        {"numero_nf": "12\x00"},
        {"numero_nf": 123},
    ):
        with pytest.raises(DadosInvalidos):
            aplicar_acao(db, ped, "informar_nf", f, dados)
    assert pedido(db, ped)["status"] == "enviado"
    assert pedido(db, ped)["numero_nf"] is None
    linha = aplicar_acao(db, ped, "informar_nf", f, {"numero_nf": "N" * 30})
    assert linha["numero_nf"] == "N" * 30
    assert linha["status"] == "faturado"


def test_marcar_chegada_usa_hoje_por_padrao(db, cenario):
    for dados in ({}, {"data_chegada": ""}, {"data_chegada": "   "}, {"data_chegada": None}):
        ped = levar_a(db, cenario, "faturado")
        linha = aplicar_acao(db, ped, "marcar_chegada", cenario["fiscal"], dados)
        assert linha["data_chegada"] == "2026-10-23"
        assert linha["status"] == "recebido"


def test_marcar_chegada_com_data_informada_e_invalida(db, cenario):
    ped = levar_a(db, cenario, "faturado")
    f = cenario["fiscal"]
    with pytest.raises(DataInvalida):
        aplicar_acao(db, ped, "marcar_chegada", f, {"data_chegada": "amanhã"})
    with pytest.raises(DataInvalida):
        aplicar_acao(db, ped, "marcar_chegada", f, {"data_chegada": "2026-10-24"})
    assert pedido(db, ped)["status"] == "faturado"
    linha = aplicar_acao(db, ped, "marcar_chegada", f, {"data_chegada": "20/10/2026"})
    assert linha["data_chegada"] == "2026-10-20"


def test_dados_none_equivale_a_dicionario_vazio(db, cenario):
    ped = levar_a(db, cenario, "pendente")
    assert aplicar_acao(db, ped, "aprovar", cenario["gestor1"], None)["status"] == "aprovado"


def test_comentario_opcional_e_gravado_nas_outras_acoes(db, cenario):
    ped = levar_a(db, cenario, "enviado")
    aplicar_acao(db, ped, "informar_nf", cenario["fiscal"], {"numero_nf": "9", "comentario": "Conferida no ERP."})
    assert historico(db, ped)[-1]["comentario"] == "Conferida no ERP."


# --- aplicar_acao: recusas -----------------------------------------------------


def test_pedido_inexistente(db, cenario):
    with pytest.raises(PedidoNaoEncontrado):
        aplicar_acao(db, 999, "aprovar", cenario["gestor1"], {})


def test_admin_aprova_e_fica_no_historico(db, cenario):
    ped = novo_pedido(db, cenario["solicitante"])
    linha = aplicar_acao(db, ped, "aprovar", cenario["admin"], {"comentario": "Ok."})
    assert linha["status"] == "aprovado"
    ultima = historico(db, ped)[-1]
    assert (ultima["acao"], ultima["usuario_id"], ultima["comentario"]) == (
        "aprovar", cenario["admin"]["id"], "Ok.",
    )


def test_admin_reprova_com_motivo_obrigatorio(db, cenario):
    ped = novo_pedido(db, cenario["solicitante"])
    with pytest.raises(DadosInvalidos, match="Informe o motivo da reprovação."):
        aplicar_acao(db, ped, "reprovar", cenario["admin"], {"comentario": " "})
    assert pedido(db, ped)["status"] == "pendente"
    assert aplicar_acao(db, ped, "reprovar", cenario["admin"], {"comentario": "Caro."})["status"] == "reprovado"


def test_admin_que_chega_depois_do_gestor_ve_quem_decidiu(db, cenario):
    ped = novo_pedido(db, cenario["solicitante"])
    aplicar_acao(db, ped, "aprovar", cenario["gestor1"], {})
    antes = historico(db, ped)
    with pytest.raises(JaDecidido) as erro:
        aplicar_acao(db, ped, "reprovar", cenario["admin"], {"comentario": "Não."})
    assert cenario["gestor1"]["nome"] in str(erro.value)
    assert historico(db, ped) == antes


def test_gestor_que_chega_depois_do_admin_ve_quem_decidiu(db, cenario):
    ped = novo_pedido(db, cenario["solicitante"])
    aplicar_acao(db, ped, "aprovar", cenario["admin"], {})
    with pytest.raises(JaDecidido) as erro:
        aplicar_acao(db, ped, "aprovar", cenario["gestor1"], {})
    assert cenario["admin"]["nome"] in str(erro.value)
    assert [linha["acao"] for linha in historico(db, ped)].count("aprovar") == 1


def test_solicitante_e_fiscal_nao_aprovam(db, cenario):
    ped = novo_pedido(db, cenario["solicitante"])
    for quem in ("solicitante", "fiscal"):
        with pytest.raises(workflow.NaoAutorizado):
            aplicar_acao(db, ped, "aprovar", cenario[quem], {})
    assert pedido(db, ped)["status"] == "pendente"


@pytest.mark.parametrize(
    "acao, usuario, esperado",
    [
        ("aprovar", "solicitante", workflow.NaoAutorizado),
        ("aprovar", "fiscal", workflow.NaoAutorizado),
        ("reprovar", "fiscal", workflow.NaoAutorizado),
        ("confirmar_envio", "solicitante", workflow.TransicaoInvalida),
        ("informar_nf", "fiscal", workflow.TransicaoInvalida),
        ("marcar_chegada", "fiscal", workflow.TransicaoInvalida),
        ("arquivar", "fiscal", workflow.TransicaoInvalida),
        ("inventada", "gestor1", workflow.TransicaoInvalida),
    ],
)
def test_acao_recusada_nao_deixa_rastro(db, cenario, acao, usuario, esperado):
    ped = novo_pedido(db, cenario["solicitante"])
    db.execute("UPDATE pedidos SET atualizado_em = '2020-01-01T00:00:00+00:00'")
    antes = (tuple(pedido(db, ped)), contar(db, "historico"), contar(db, "avisos"))

    dados = {"comentario": "x", "data_pedido": "2026-10-22", "numero_nf": "1"}
    with pytest.raises(esperado):
        aplicar_acao(db, ped, acao, cenario[usuario], dados)

    assert (tuple(pedido(db, ped)), contar(db, "historico"), contar(db, "avisos")) == antes


def test_acao_nao_autorizada_vem_antes_dos_erros_de_dados(db, cenario):
    ped = novo_pedido(db, cenario["solicitante"])
    with pytest.raises(workflow.NaoAutorizado):
        aplicar_acao(db, ped, "reprovar", cenario["fiscal"], {})  # sem motivo, mas o erro é de perfil


def test_transicao_e_historico_na_mesma_transacao(db, cenario, monkeypatch):
    ped = novo_pedido(db, cenario["solicitante"])
    avisos_antes, historico_antes = contar(db, "avisos"), historico(db, ped)

    def quebra(db, evento, pedido):
        raise RuntimeError("falha ao gravar aviso")

    monkeypatch.setattr(notificacoes, "registrar_avisos", quebra)
    with pytest.raises(RuntimeError):
        aplicar_acao(db, ped, "aprovar", cenario["gestor1"], {"comentario": "Ok."})

    assert pedido(db, ped)["status"] == "pendente"
    assert [tuple(h) for h in historico(db, ped)] == [tuple(h) for h in historico_antes]
    assert contar(db, "avisos") == avisos_antes
    # a conexão não ficou presa numa transação: dá para tentar de novo
    monkeypatch.undo()
    assert aplicar_acao(db, ped, "aprovar", cenario["gestor1"], {})["status"] == "aprovado"


def test_aviso_de_reprovacao_ve_o_motivo_gravado_no_historico_da_mesma_transacao(db, cenario, monkeypatch):
    ped = novo_pedido(db, cenario["solicitante"])
    vistos = []
    original = notificacoes.registrar_avisos

    def espia(db, evento, linha):
        ultimo = db.execute(
            "SELECT acao, comentario FROM historico WHERE pedido_id = ? ORDER BY id DESC", (linha["id"],)
        ).fetchone()
        vistos.append(tuple(ultimo))
        vistos.append(linha["status"])
        return original(db, evento, linha)

    monkeypatch.setattr(notificacoes, "registrar_avisos", espia)
    aplicar_acao(db, ped, "reprovar", cenario["gestor1"], {"comentario": "Motivo X."})
    assert vistos == [("reprovar", "Motivo X."), "reprovado"]


def test_servico_nao_envia_email(db, cenario, monkeypatch):
    def proibido(*args, **kwargs):
        raise AssertionError("o serviço não deve enviar e-mail")

    monkeypatch.setattr(notificacoes, "enviar_pendentes", proibido)
    ped = novo_pedido(db, cenario["solicitante"])
    aplicar_acao(db, ped, "aprovar", cenario["gestor1"], {})
