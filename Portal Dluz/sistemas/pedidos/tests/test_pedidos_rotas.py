import gc
import io
import re
import sqlite3
from pathlib import Path

import pytest
from werkzeug.datastructures import FileStorage

from app import arquivos, auth, create_app, notificacoes, servico
from app import db as db_module
from app.workflow import ACOES, STATUS
from conftest import SENHA_TESTE, ClienteHttps

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
XSS = "<script>alert(1)</script>"
TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"


# --- apoio -------------------------------------------------------------------


@pytest.fixture
def pessoas(criar_usuario_teste):
    return {
        "solicitante": criar_usuario_teste("solicitante"),
        "outro": criar_usuario_teste("solicitante"),
        "gestor1": criar_usuario_teste("gestor"),
        "gestor2": criar_usuario_teste("gestor"),
        "fiscal": criar_usuario_teste("fiscal"),
        "admin": criar_usuario_teste("admin"),
    }


@pytest.fixture
def logar(app, entrar):
    """logar(usuario) -> cliente de teste novo, já autenticado como esse usuário."""

    def _logar(usuario):
        cliente = app.test_client()
        assert entrar(cliente, usuario.email).status_code == 302
        return cliente

    return _logar


def consulta(app, sql, parametros=()):
    with app.app_context():
        return [dict(l) for l in db_module.get_db().execute(sql, parametros).fetchall()]


def pedido_no_banco(app, pedido_id):
    (linha,) = consulta(app, "SELECT * FROM pedidos WHERE id = ?", (pedido_id,))
    return linha


def criar(app, solicitante, status="pendente", **campos):
    """Cria um pedido pelo serviço (com PDF de verdade) e, se pedido, força o status."""
    dados = {"cliente": "Madeireira Boa Vista", "valor": "1.234,56", "descricao": "Compra de madeira"}
    dados.update(campos)
    with app.app_context():
        db = db_module.get_db()
        usuario = db.execute("SELECT * FROM usuarios WHERE id = ?", (solicitante.id,)).fetchone()
        pedido_id = servico.criar_pedido(
            db, usuario, arquivo=FileStorage(stream=io.BytesIO(PDF), filename="pedido.pdf"), **dados
        )
        if status != "pendente":
            db.execute("UPDATE pedidos SET status = ? WHERE id = ?", (status, pedido_id))
    return pedido_id


def ids_da_lista(resposta):
    return [int(n) for n in re.findall(r'href="/pedidos/(\d+)"', resposta.get_data(as_text=True))]


def corpo(resposta):
    return resposta.get_data(as_text=True)


def enviar_novo(navegador, **campos):
    dados = {
        "cliente": "Madeireira Boa Vista",
        "valor": "1.234,56",
        "descricao": "Compra de madeira",
        "arquivo": (io.BytesIO(PDF), "pedido.pdf"),
    }
    dados.update(campos)
    return navegador.post("/pedidos/novo", data=dados, content_type="multipart/form-data")


def hoje():
    return servico.hoje_sp().isoformat()


# --- visibilidade ------------------------------------------------------------


def test_solicitante_ve_so_os_proprios_pedidos(app, pessoas, logar):
    meu = criar(app, pessoas["solicitante"])
    alheio = criar(app, pessoas["outro"])

    ids = ids_da_lista(logar(pessoas["solicitante"]).get("/"))

    assert ids == [meu]
    assert alheio not in ids


def test_solicitante_nao_abre_pedido_de_outro(app, pessoas, logar):
    alheio = criar(app, pessoas["outro"])
    cliente = logar(pessoas["solicitante"])

    resposta = cliente.get(f"/pedidos/{alheio}")

    assert resposta.status_code == 404  # 404, não 403: não revela que o pedido existe
    assert "Madeireira" not in corpo(resposta)


def test_solicitante_nao_baixa_pdf_de_outro(app, pessoas, logar):
    alheio = criar(app, pessoas["outro"])
    assert logar(pessoas["solicitante"]).get(f"/pedidos/{alheio}/pdf").status_code == 404


def test_solicitante_nao_age_sobre_pedido_de_outro(app, pessoas, logar):
    alheio = criar(app, pessoas["outro"], status="aprovado")
    cliente = logar(pessoas["solicitante"])

    resposta = cliente.post(f"/pedidos/{alheio}/confirmar_envio", data={"data_pedido": hoje()})

    assert resposta.status_code == 404
    assert pedido_no_banco(app, alheio)["status"] == "aprovado"


@pytest.mark.parametrize("perfil", ["gestor1", "fiscal", "admin"])
def test_gestor_fiscal_e_admin_veem_todos(app, pessoas, logar, perfil):
    a = criar(app, pessoas["solicitante"])
    b = criar(app, pessoas["outro"])
    cliente = logar(pessoas[perfil])

    assert sorted(ids_da_lista(cliente.get("/"))) == [a, b]
    assert cliente.get(f"/pedidos/{a}").status_code == 200
    assert cliente.get(f"/pedidos/{b}").status_code == 200
    with cliente.get(f"/pedidos/{b}/pdf") as pdf:  # fecha o arquivo servido
        assert pdf.status_code == 200


@pytest.mark.parametrize(
    "metodo, caminho",
    [
        ("get", "/"),
        ("get", "/pedidos/novo"),
        ("post", "/pedidos/novo"),
        ("get", "/pedidos/1"),
        ("post", "/pedidos/1/aprovar"),
        ("get", "/pedidos/1/pdf"),
        ("get", "/avisos"),
        ("post", "/avisos/1/lido"),
    ],
)
def test_anonimo_vai_para_o_login(client, metodo, caminho):
    resposta = getattr(client, metodo)(caminho)
    assert resposta.status_code == 302
    assert resposta.headers["Location"].endswith("/login")


def test_ids_acoes_e_filtros_desconhecidos_nao_dao_500(app, pessoas, logar):
    pid = criar(app, pessoas["solicitante"])
    gestor = logar(pessoas["gestor1"])

    assert gestor.get("/pedidos/99999").status_code == 404
    assert gestor.get("/pedidos/99999/pdf").status_code == 404
    assert gestor.post("/pedidos/99999/aprovar").status_code == 404
    assert gestor.post(f"/pedidos/{pid}/explodir").status_code == 404
    assert gestor.post(f"/pedidos/{pid}/APROVAR").status_code == 404
    assert gestor.get("/pedidos/abc").status_code == 404
    assert gestor.post("/avisos/99999/lido").status_code == 404
    resposta = gestor.get("/?status=inexistente")
    assert resposta.status_code == 200
    assert ids_da_lista(resposta) == [pid]
    assert gestor.get("/?status=").status_code == 200
    assert gestor.get("/?status=pendente&status=x%27%20OR%201=1").status_code == 200


# --- lista -------------------------------------------------------------------


def test_lista_mostra_colunas_mais_recente_primeiro_e_esconde_arquivados(app, pessoas, logar):
    primeiro = criar(app, pessoas["solicitante"], cliente="Cliente Um", valor="10,00")
    segundo = criar(app, pessoas["solicitante"], cliente="Cliente Dois", valor="1.234,56")
    arquivado = criar(app, pessoas["solicitante"], status="arquivado")

    resposta = logar(pessoas["fiscal"]).get("/")
    html = corpo(resposta)

    assert ids_da_lista(resposta) == [segundo, primeiro]
    assert arquivado not in ids_da_lista(resposta)
    assert f"Novo Pedido - {segundo}" in html
    assert "Cliente Dois" in html
    assert re.search(r'<td class="num"[^>]*>R\$ 1\.234,56</td>', html)
    assert "Pendente" in html
    assert "<th" in html and "Valor" in html


def test_filtro_por_status_inclui_arquivados_quando_pedido(app, pessoas, logar):
    pendente = criar(app, pessoas["solicitante"])
    aprovado = criar(app, pessoas["solicitante"], status="aprovado")
    arquivado = criar(app, pessoas["solicitante"], status="arquivado")
    cliente = logar(pessoas["fiscal"])

    assert ids_da_lista(cliente.get("/?status=aprovado")) == [aprovado]
    assert ids_da_lista(cliente.get("/?status=arquivado")) == [arquivado]
    assert sorted(ids_da_lista(cliente.get("/"))) == [pendente, aprovado]


def test_lista_vazia_tem_mensagem(pessoas, logar):
    assert "Nenhum pedido" in corpo(logar(pessoas["gestor1"]).get("/"))


def test_lista_limita_a_200_pedidos(app, pessoas, logar):
    with app.app_context():
        db = db_module.get_db()
        for _ in range(205):
            db.execute(
                "INSERT INTO pedidos (cliente, valor_centavos, descricao, pdf_arquivo,"
                " pdf_nome_original, solicitante_id, criado_em, atualizado_em)"
                " VALUES ('C', 100, 'D', ?, 'a.pdf', ?, ?, ?)",
                ("a" * 32 + ".pdf", pessoas["solicitante"].id, db_module.agora_utc(), db_module.agora_utc()),
            )
    assert len(ids_da_lista(logar(pessoas["gestor1"]).get("/"))) == 200


def _atalho(html, rotulo):
    achado = re.search(
        rf'<a href="([^"]*)">{re.escape(rotulo)}</a>\s*<span class="contagem">(\d+)</span>', html
    )
    return (achado.group(1), int(achado.group(2))) if achado else None


def test_atalhos_por_perfil(app, pessoas, logar):
    criar(app, pessoas["solicitante"])  # pendente
    criar(app, pessoas["outro"])  # pendente
    criar(app, pessoas["solicitante"], status="aprovado")
    criar(app, pessoas["outro"], status="aprovado")
    criar(app, pessoas["outro"], status="enviado")
    criar(app, pessoas["solicitante"], status="enviado")
    criar(app, pessoas["outro"], status="enviado")
    criar(app, pessoas["solicitante"], status="faturado")
    criar(app, pessoas["solicitante"], status="arquivado")

    gestor = corpo(logar(pessoas["gestor1"]).get("/"))
    assert _atalho(gestor, "A decidir") == ("/?status=pendente", 2)
    assert _atalho(gestor, "Aguardando nota") is None

    fiscal = corpo(logar(pessoas["fiscal"]).get("/"))
    assert _atalho(fiscal, "Aguardando nota") == ("/?status=enviado", 3)
    assert _atalho(fiscal, "Aguardando chegada") == ("/?status=faturado", 1)
    assert _atalho(fiscal, "A decidir") is None

    solicitante = corpo(logar(pessoas["solicitante"]).get("/"))
    # só os próprios, sem os arquivados
    assert _atalho(solicitante, "Meus pedidos") == ("/", 4)
    assert _atalho(solicitante, "Aguardando meu envio") == ("/?status=aprovado", 1)
    assert _atalho(solicitante, "A decidir") is None

    # o administrador também decide: ganha "A decidir", sem os atalhos do fiscal e do solicitante
    admin = corpo(logar(pessoas["admin"]).get("/"))
    assert _atalho(admin, "A decidir") == _atalho(gestor, "A decidir")
    for rotulo in ("Aguardando nota", "Aguardando chegada", "Meus pedidos"):
        assert _atalho(admin, rotulo) is None


def test_atalho_leva_ao_filtro_certo(app, pessoas, logar):
    pendente = criar(app, pessoas["solicitante"])
    criar(app, pessoas["solicitante"], status="aprovado")
    gestor = logar(pessoas["gestor1"])

    destino, _ = _atalho(corpo(gestor.get("/")), "A decidir")

    assert ids_da_lista(gestor.get(destino)) == [pendente]


# --- novo pedido -------------------------------------------------------------


def test_formulario_de_novo_pedido(pessoas, logar):
    resposta = logar(pessoas["solicitante"]).get("/pedidos/novo")
    html = corpo(resposta)

    assert resposta.status_code == 200
    assert 'enctype="multipart/form-data"' in html
    assert 'name="csrf_token"' in html
    for campo in ("cliente", "valor", "descricao", "arquivo"):
        assert f'name="{campo}"' in html
    assert 'accept=".pdf,application/pdf"' in html
    assert "ex.: 1.234,56" in html
    assert 'name="reenvio_de"' not in html


@pytest.mark.parametrize("perfil", ["gestor1", "fiscal", "admin"])
def test_so_solicitante_cria_pedido(app, pessoas, logar, perfil):
    cliente = logar(pessoas[perfil])

    assert cliente.get("/pedidos/novo").status_code == 403
    assert enviar_novo(cliente).status_code == 403
    assert consulta(app, "SELECT id FROM pedidos") == []


def test_criar_pedido_redireciona_ao_detalhe_com_confirmacao(app, pessoas, logar):
    cliente = logar(pessoas["solicitante"])

    resposta = enviar_novo(cliente)
    assert resposta.status_code == 302
    (pedido,) = consulta(app, "SELECT * FROM pedidos")
    assert resposta.headers["Location"].endswith(f"/pedidos/{pedido['id']}")
    assert pedido["status"] == "pendente"
    assert pedido["valor_centavos"] == 123456
    assert pedido["solicitante_id"] == pessoas["solicitante"].id

    html = corpo(cliente.get(resposta.headers["Location"]))
    assert "Pedido enviado para aprovação" in html
    assert f"Novo Pedido - {pedido['id']}" in html
    # a confirmação de recebimento também vai para a caixa de avisos
    assert any(a["usuario_id"] == pessoas["solicitante"].id for a in consulta(app, "SELECT * FROM avisos"))


@pytest.mark.parametrize(
    "campos, mensagem",
    [
        ({"valor": "abc"}, "Valor inválido"),
        ({"valor": ""}, "Informe o valor"),
        ({"cliente": "   "}, "Informe o cliente"),
        ({"descricao": ""}, "Informe a descrição"),
        ({"arquivo": (io.BytesIO(b"texto"), "pedido.pdf")}, "não é um PDF válido"),
        ({"arquivo": (io.BytesIO(PDF), "pedido.exe")}, "extensão .pdf"),
        ({"reenvio_de": "abc"}, "Pedido anterior inválido"),
        ({"reenvio_de": "٣"}, "Pedido anterior inválido"),
        ({"reenvio_de": "99999"}, "não pode ser usado como reenvio"),
    ],
)
def test_erro_de_validacao_reexibe_o_formulario_com_400(app, pessoas, logar, campos, mensagem):
    cliente = logar(pessoas["solicitante"])
    base = {"cliente": "Cliente Mantido", "descricao": "Descrição mantida", "valor": "10,00"}
    base.update(campos)

    resposta = enviar_novo(cliente, **base)
    html = corpo(resposta)

    assert resposta.status_code == 400
    assert mensagem in html
    assert 'role="alert"' in html
    assert consulta(app, "SELECT id FROM pedidos") == []
    if "cliente" not in campos:
        assert 'value="Cliente Mantido"' in html
    if "descricao" not in campos:
        assert "Descrição mantida" in html
    assert 'type="file"' in html and "value=\"pedido" not in html


def test_formulario_sem_arquivo_da_erro_400(pessoas, logar):
    resposta = logar(pessoas["solicitante"]).post(
        "/pedidos/novo",
        data={"cliente": "C", "valor": "10,00", "descricao": "D"},
        content_type="multipart/form-data",
    )
    assert resposta.status_code == 400
    assert "Selecione um arquivo PDF" in corpo(resposta)


def test_falha_ao_gravar_o_arquivo_da_mensagem_amigavel_com_500(app, pessoas, logar, monkeypatch):
    def sem_espaco(_arquivo):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(arquivos, "salvar_pdf", sem_espaco)
    cliente = logar(pessoas["solicitante"])

    resposta = enviar_novo(cliente, cliente="Cliente Mantido")
    html = corpo(resposta)

    assert resposta.status_code == 500
    assert "Não foi possível gravar o arquivo. Tente novamente." in html
    assert "No space left" not in html and "Traceback" not in html
    assert 'value="Cliente Mantido"' in html
    assert consulta(app, "SELECT id FROM pedidos") == []


def test_reenvio_preenche_o_formulario_e_liga_ao_pedido_anterior(app, pessoas, logar):
    anterior = criar(app, pessoas["solicitante"], status="reprovado", cliente="Cliente Antigo", valor="99,90")
    cliente = logar(pessoas["solicitante"])

    html = corpo(cliente.get(f"/pedidos/novo?reenvio_de={anterior}"))

    assert f"Reenvio do Novo Pedido - {anterior}" in html
    assert 'value="Cliente Antigo"' in html
    assert 'value="99,90"' in html
    assert "Compra de madeira" in html
    assert re.search(rf'<input type="hidden" name="reenvio_de" value="{anterior}">', html)

    resposta = enviar_novo(cliente, reenvio_de=str(anterior))
    assert resposta.status_code == 302
    novo = consulta(app, "SELECT * FROM pedidos ORDER BY id DESC LIMIT 1")[0]
    assert novo["id"] != anterior and novo["reenvio_de"] == anterior
    assert f"Novo Pedido - {anterior}" in corpo(cliente.get(f"/pedidos/{novo['id']}"))


def test_reenvio_de_pedido_alheio_ou_nao_reprovado_nao_preenche(app, pessoas, logar):
    alheio = criar(app, pessoas["outro"], status="reprovado", cliente="Segredo S.A.")
    pendente = criar(app, pessoas["solicitante"], cliente="Cliente Pendente")
    cliente = logar(pessoas["solicitante"])

    for alvo in (alheio, pendente, 99999):
        html = corpo(cliente.get(f"/pedidos/novo?reenvio_de={alvo}"))
        assert "Segredo S.A." not in html and "Cliente Pendente" not in html
        assert "Reenvio do" not in html
        assert 'name="reenvio_de"' not in html
    assert cliente.get("/pedidos/novo?reenvio_de=abc").status_code == 200


# O cliente de teste do werkzeug guarda em arquivo temporário (e não fecha) qualquer
# corpo multipart acima de 500 kB; o aviso vem do cliente, não do aplicativo.
@pytest.mark.filterwarnings("ignore::ResourceWarning")
@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_upload_acima_do_limite_da_413_em_portugues(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path / "d", "WTF_CSRF_ENABLED": False, "MAX_PDF_MB": 1})
    app.test_client_class = ClienteHttps
    with app.app_context():
        auth.criar_usuario("Sol", "sol@exemplo.com", SENHA_TESTE, "solicitante", trocar_senha=False)
    cliente = app.test_client()
    cliente.post("/login", data={"email": "sol@exemplo.com", "senha": SENHA_TESTE})

    assert app.config["MAX_CONTENT_LENGTH"] == 2 * 1024 * 1024
    grande = PDF + b"0" * (3 * 1024 * 1024)
    resposta = enviar_novo(cliente, arquivo=(io.BytesIO(grande), "grande.pdf"))
    assert resposta.status_code == 413
    assert "Arquivo maior que 1 MB" in corpo(resposta)

    # logo acima do limite do PDF, mas abaixo do teto da requisição: erro de validação normal
    um_pouco = PDF + b"0" * (1024 * 1024)
    resposta = enviar_novo(cliente, arquivo=(io.BytesIO(um_pouco), "quase.pdf"))
    assert resposta.status_code == 400
    assert "maior que o limite de 1 MB" in corpo(resposta)
    del resposta
    gc.collect()  # o aviso do cliente de teste sai aqui, sob o filtro acima, e não no teste seguinte


def test_limite_da_requisicao_acompanha_o_limite_do_pdf(app):
    assert app.config["MAX_CONTENT_LENGTH"] == (app.config["MAX_PDF_MB"] + 1) * 1024 * 1024


# --- detalhe -----------------------------------------------------------------


def test_detalhe_mostra_os_dados_do_pedido(app, pessoas, logar):
    pid = criar(app, pessoas["solicitante"], descricao="Linha 1\nLinha 2")
    with app.app_context():
        db_module.get_db().execute(
            "UPDATE pedidos SET status = 'recebido', data_pedido = '2026-10-20',"
            " numero_nf = 'NF-77', data_chegada = '2026-10-25' WHERE id = ?",
            (pid,),
        )

    html = corpo(logar(pessoas["fiscal"]).get(f"/pedidos/{pid}"))

    assert f"Novo Pedido - {pid}" in html
    assert "Recebido" in html
    assert "Madeireira Boa Vista" in html
    assert "R$ 1.234,56" in html
    assert "Linha 1<br>" in html and "Linha 2" in html
    assert pessoas["solicitante"].nome in html
    assert "20/10/2026" in html and "25/10/2026" in html
    assert "NF-77" in html
    assert f'href="/pedidos/{pid}/pdf"' in html and "pedido.pdf" in html
    assert "Histórico" in html


def test_detalhe_omite_campos_ainda_vazios(app, pessoas, logar):
    pid = criar(app, pessoas["solicitante"])
    html = corpo(logar(pessoas["fiscal"]).get(f"/pedidos/{pid}"))
    assert "Nota fiscal" not in html and "Chegada" not in html


def test_pdf_abre_com_content_type_pdf_e_requer_login(app, pessoas, logar, client):
    pid = criar(app, pessoas["solicitante"])

    anonimo = client.get(f"/pedidos/{pid}/pdf")
    assert anonimo.status_code == 302 and anonimo.headers["Location"].endswith("/login")

    with logar(pessoas["solicitante"]).get(f"/pedidos/{pid}/pdf") as resposta:
        assert resposta.status_code == 200
        assert resposta.mimetype == "application/pdf"
        assert resposta.data == PDF
        assert resposta.headers["Cache-Control"] == "private, no-store"
        assert resposta.headers["Content-Disposition"].startswith("inline")
        assert "pedido.pdf" in resposta.headers["Content-Disposition"]


def test_pdf_ausente_no_disco_mostra_aviso_e_nao_erro_500(app, pessoas, logar):
    pid = criar(app, pessoas["solicitante"])
    with app.app_context():
        nome = db_module.get_db().execute("SELECT pdf_arquivo FROM pedidos").fetchone()[0]
        arquivos.caminho_pdf(nome).unlink()
    cliente = logar(pessoas["gestor1"])

    detalhe = cliente.get(f"/pedidos/{pid}")
    assert detalhe.status_code == 200
    assert "Arquivo PDF não encontrado no servidor" in corpo(detalhe)
    assert f'href="/pedidos/{pid}/pdf"' not in corpo(detalhe)

    pdf = cliente.get(f"/pedidos/{pid}/pdf")
    assert pdf.status_code == 404
    assert "Arquivo PDF não encontrado" in corpo(pdf)
    assert "Traceback" not in corpo(pdf)


def test_nome_de_arquivo_corrompido_no_banco_nao_vaza_nem_da_500(app, pessoas, logar):
    pid = criar(app, pessoas["solicitante"])
    with app.app_context():
        db_module.get_db().execute("UPDATE pedidos SET pdf_arquivo = '../../etc/passwd'")
    cliente = logar(pessoas["gestor1"])

    assert cliente.get(f"/pedidos/{pid}").status_code == 200
    assert cliente.get(f"/pedidos/{pid}/pdf").status_code == 404


@pytest.mark.parametrize(
    "quem, status, dono, esperadas",
    [
        ("gestor1", "pendente", False, {"aprovar", "reprovar"}),
        ("gestor1", "aprovado", False, set()),
        ("gestor1", "enviado", False, set()),
        ("solicitante", "pendente", True, set()),
        ("solicitante", "aprovado", True, {"confirmar_envio"}),
        ("solicitante", "reprovado", True, set()),
        ("fiscal", "pendente", False, set()),
        ("fiscal", "aprovado", False, set()),
        ("fiscal", "enviado", False, {"informar_nf"}),
        ("fiscal", "faturado", False, {"marcar_chegada"}),
        ("fiscal", "recebido", False, {"arquivar"}),
        ("fiscal", "reprovado", False, {"arquivar"}),
        ("admin", "pendente", False, {"aprovar", "reprovar"}),
        ("admin", "aprovado", False, set()),
        ("admin", "enviado", False, {"informar_nf"}),
        ("admin", "arquivado", False, set()),
    ],
)
def test_botoes_da_tela_so_do_perfil_e_da_etapa(app, pessoas, logar, quem, status, dono, esperadas):
    dono_do_pedido = pessoas["solicitante"]
    pid = criar(app, dono_do_pedido, status=status)

    html = corpo(logar(pessoas[quem]).get(f"/pedidos/{pid}"))

    presentes = {acao for acao in ACOES if f'action="/pedidos/{pid}/{acao}"' in html}
    assert presentes == esperadas


def test_formularios_de_acao_trazem_csrf_e_os_campos_certos(app, pessoas, logar):
    pendente = criar(app, pessoas["solicitante"])
    aprovado = criar(app, pessoas["solicitante"], status="aprovado")
    enviado = criar(app, pessoas["solicitante"], status="enviado")
    faturado = criar(app, pessoas["solicitante"], status="faturado")

    gestor = corpo(logar(pessoas["gestor1"]).get(f"/pedidos/{pendente}"))
    assert gestor.count('name="csrf_token"') >= 3  # sair + 2 ações
    assert 'name="comentario"' in gestor
    assert re.search(r"<textarea[^>]*name=\"comentario\"[^>]*required", gestor)  # reprovar

    sol = corpo(logar(pessoas["solicitante"]).get(f"/pedidos/{aprovado}"))
    assert re.search(rf'type="date"[^>]*name="data_pedido"[^>]*max="{hoje()}"[^>]*value="{hoje()}"', sol)

    fiscal = logar(pessoas["fiscal"])
    assert re.search(r'name="numero_nf"[^>]*maxlength="30"', corpo(fiscal.get(f"/pedidos/{enviado}")))
    assert re.search(
        rf'type="date"[^>]*name="data_chegada"[^>]*value="{hoje()}"', corpo(fiscal.get(f"/pedidos/{faturado}"))
    )


def test_admin_aprova_e_reprova_pelo_navegador(app, pessoas, logar):
    para_aprovar = criar(app, pessoas["solicitante"])
    para_reprovar = criar(app, pessoas["solicitante"])
    admin = logar(pessoas["admin"])

    assert admin.post(f"/pedidos/{para_aprovar}/aprovar", data={"comentario": "Ok."}).status_code == 302
    assert admin.post(f"/pedidos/{para_reprovar}/reprovar", data={"comentario": "Caro."}).status_code == 302

    aprovado = corpo(admin.get(f"/pedidos/{para_aprovar}"))
    reprovado = corpo(admin.get(f"/pedidos/{para_reprovar}"))
    assert "Aprovado" in aprovado and "Ok." in aprovado
    assert "Reprovado" in reprovado and "Caro." in reprovado
    # reprovar sem motivo continua sendo recusado para o administrador
    outro = criar(app, pessoas["solicitante"])
    assert admin.post(f"/pedidos/{outro}/reprovar", data={"comentario": ""}).status_code == 400


def test_acao_nao_permitida_pelo_post_retorna_403(app, pessoas, logar):
    pendente = criar(app, pessoas["solicitante"])
    aprovado = criar(app, pessoas["solicitante"], status="aprovado")
    enviado = criar(app, pessoas["solicitante"], status="enviado")
    recebido = criar(app, pessoas["solicitante"], status="recebido")

    # sem o botão, o POST à mão também é recusado
    dono = logar(pessoas["solicitante"])
    assert dono.post(f"/pedidos/{pendente}/aprovar").status_code == 403
    assert logar(pessoas["fiscal"]).post(f"/pedidos/{pendente}/aprovar").status_code == 403
    assert logar(pessoas["fiscal"]).post(f"/pedidos/{pendente}/reprovar", data={"comentario": "x"}).status_code == 403
    assert logar(pessoas["gestor1"]).post(f"/pedidos/{aprovado}/confirmar_envio", data={"data_pedido": hoje()}).status_code == 403
    assert dono.post(f"/pedidos/{enviado}/informar_nf", data={"numero_nf": "1"}).status_code == 403
    assert logar(pessoas["gestor1"]).post(f"/pedidos/{recebido}/arquivar").status_code == 403

    assert pedido_no_banco(app, pendente)["status"] == "pendente"
    assert pedido_no_banco(app, aprovado)["status"] == "aprovado"
    assert pedido_no_banco(app, enviado)["status"] == "enviado"
    assert pedido_no_banco(app, recebido)["status"] == "recebido"


def test_reprovado_mostra_o_motivo_em_destaque_e_reenviar_so_para_o_dono(app, pessoas, logar):
    pid = criar(app, pessoas["solicitante"])
    gestor = logar(pessoas["gestor1"])
    gestor.post(f"/pedidos/{pid}/reprovar", data={"comentario": "Valor acima da tabela"})

    dono = corpo(logar(pessoas["solicitante"]).get(f"/pedidos/{pid}"))
    assert re.search(r'class="motivo"[^>]*>.*Valor acima da tabela', dono, re.S)
    assert f'href="/pedidos/novo?reenvio_de={pid}"' in dono

    outros = corpo(gestor.get(f"/pedidos/{pid}"))
    assert "Valor acima da tabela" in outros
    assert "reenvio_de" not in outros


def test_reprovado_e_depois_arquivado_ainda_pode_ser_reenviado(app, pessoas, logar):
    pid = criar(app, pessoas["solicitante"], cliente="Cliente Antigo")
    logar(pessoas["gestor1"]).post(f"/pedidos/{pid}/reprovar", data={"comentario": "Faltou a cotação"})
    logar(pessoas["fiscal"]).post(f"/pedidos/{pid}/arquivar")
    assert pedido_no_banco(app, pid)["status"] == "arquivado"
    dono = logar(pessoas["solicitante"])

    detalhe = corpo(dono.get(f"/pedidos/{pid}"))
    assert "Faltou a cotação" in detalhe
    assert f'href="/pedidos/novo?reenvio_de={pid}"' in detalhe
    assert "reenvio_de" not in corpo(logar(pessoas["fiscal"]).get(f"/pedidos/{pid}"))

    formulario = corpo(dono.get(f"/pedidos/novo?reenvio_de={pid}"))
    assert f"Reenvio do Novo Pedido - {pid}" in formulario
    assert 'value="Cliente Antigo"' in formulario

    resposta = enviar_novo(dono, reenvio_de=str(pid))
    assert resposta.status_code == 302
    novo = consulta(app, "SELECT * FROM pedidos ORDER BY id DESC LIMIT 1")[0]
    assert novo["reenvio_de"] == pid


def test_arquivado_depois_de_aprovado_ou_alheio_nao_serve_de_reenvio(app, pessoas, logar):
    aprovado = criar(app, pessoas["solicitante"], cliente="Cliente Aprovado")
    logar(pessoas["gestor1"]).post(f"/pedidos/{aprovado}/aprovar")
    with app.app_context():
        db_module.get_db().execute("UPDATE pedidos SET status = 'arquivado' WHERE id = ?", (aprovado,))
    alheio = criar(app, pessoas["outro"], cliente="Segredo S.A.")
    logar(pessoas["gestor1"]).post(f"/pedidos/{alheio}/reprovar", data={"comentario": "Não"})
    logar(pessoas["fiscal"]).post(f"/pedidos/{alheio}/arquivar")
    dono = logar(pessoas["solicitante"])

    assert "reenvio_de" not in corpo(dono.get(f"/pedidos/{aprovado}"))
    for alvo in (aprovado, alheio):
        html = corpo(dono.get(f"/pedidos/novo?reenvio_de={alvo}"))
        assert "Reenvio do" not in html and 'name="reenvio_de"' not in html
        resposta = enviar_novo(dono, reenvio_de=str(alvo))
        assert resposta.status_code == 400
        assert "O pedido anterior não pode ser usado como reenvio." in corpo(resposta)
    assert len(consulta(app, "SELECT id FROM pedidos")) == 2


# --- ações -------------------------------------------------------------------


def test_fluxo_completo_pelo_navegador(app, pessoas, logar):
    solicitante = logar(pessoas["solicitante"])
    gestor = logar(pessoas["gestor1"])
    fiscal = logar(pessoas["fiscal"])

    pid = int(enviar_novo(solicitante).headers["Location"].rsplit("/", 1)[1])
    url = f"/pedidos/{pid}"

    resposta = gestor.post(f"{url}/aprovar", data={"comentario": "Ok, pode seguir"}, follow_redirects=True)
    assert resposta.status_code == 200
    assert "Pedido aprovado" in corpo(resposta)

    resposta = solicitante.post(f"{url}/confirmar_envio", data={"data_pedido": hoje()}, follow_redirects=True)
    assert "Envio confirmado" in corpo(resposta)

    resposta = fiscal.post(f"{url}/informar_nf", data={"numero_nf": "NF-4521"}, follow_redirects=True)
    assert "Nota fiscal informada" in corpo(resposta)

    resposta = fiscal.post(f"{url}/marcar_chegada", data={"data_chegada": hoje()}, follow_redirects=True)
    assert "Chegada registrada" in corpo(resposta)

    resposta = fiscal.post(f"{url}/arquivar", follow_redirects=True)
    html = corpo(resposta)
    assert "Pedido arquivado" in html

    pedido = pedido_no_banco(app, pid)
    assert pedido["status"] == "arquivado"
    assert pedido["numero_nf"] == "NF-4521"
    assert pedido["data_pedido"] == hoje() and pedido["data_chegada"] == hoje()

    # histórico completo, do mais antigo para o mais novo
    historico = html[html.index("Histórico"):]
    posicoes = [
        historico.index(rotulo)
        for rotulo in (
            "Pedido criado", "Aprovado", "Envio confirmado",
            "Nota fiscal informada", "Chegada registrada", "Arquivado",
        )
    ]
    assert posicoes == sorted(posicoes)
    assert "Ok, pode seguir" in historico
    assert pessoas["gestor1"].nome in historico and pessoas["fiscal"].nome in historico
    assert "Pendente" in historico and "Aprovado" in historico  # de -> para

    assert ids_da_lista(fiscal.get("/")) == []
    assert ids_da_lista(fiscal.get("/?status=arquivado")) == [pid]


def test_segundo_gestor_ve_mensagem_de_ja_decidido(app, pessoas, logar):
    pid = criar(app, pessoas["solicitante"])
    logar(pessoas["gestor1"]).post(f"/pedidos/{pid}/aprovar")

    resposta = logar(pessoas["gestor2"]).post(
        f"/pedidos/{pid}/reprovar", data={"comentario": "Discordo"}, follow_redirects=True
    )

    assert resposta.status_code == 200
    html = corpo(resposta)
    assert f"Este pedido já foi decidido por {pessoas['gestor1'].nome}" in html
    assert pedido_no_banco(app, pid)["status"] == "aprovado"
    assert len(consulta(app, "SELECT id FROM historico WHERE acao = 'reprovar'")) == 0


def test_acao_que_nao_se_aplica_mais_avisa_e_volta_ao_detalhe(app, pessoas, logar):
    pid = criar(app, pessoas["solicitante"], status="aprovado")
    dono = logar(pessoas["solicitante"])
    dono.post(f"/pedidos/{pid}/confirmar_envio", data={"data_pedido": hoje()})

    resposta = dono.post(f"/pedidos/{pid}/confirmar_envio", data={"data_pedido": hoje()}, follow_redirects=True)

    assert resposta.status_code == 200
    assert "Esta ação não está mais disponível para este pedido." in corpo(resposta)
    assert pedido_no_banco(app, pid)["status"] == "enviado"


@pytest.mark.parametrize(
    "quem, status, acao, dados, mensagem",
    [
        ("gestor1", "pendente", "reprovar", {"comentario": "  "}, "Informe o motivo da reprovação."),
        ("gestor1", "pendente", "aprovar", {"comentario": "x" * 1001}, "no máximo 1000"),
        ("solicitante", "aprovado", "confirmar_envio", {"data_pedido": "2999-01-01"}, "não pode ser futura"),
        ("solicitante", "aprovado", "confirmar_envio", {"data_pedido": "lixo"}, "Data inválida"),
        ("solicitante", "aprovado", "confirmar_envio", {}, "Informe a data"),
        ("fiscal", "enviado", "informar_nf", {"numero_nf": ""}, "Informe o número da nota fiscal."),
        ("fiscal", "enviado", "informar_nf", {"numero_nf": "9" * 31}, "no máximo 30"),
        ("fiscal", "faturado", "marcar_chegada", {"data_chegada": "31/02/2026"}, "não existe"),
    ],
)
def test_dado_invalido_reexibe_o_detalhe_com_400_e_nada_muda(app, pessoas, logar, quem, status, acao, dados, mensagem):
    pid = criar(app, pessoas["solicitante"], status=status)
    cliente = logar(pessoas[quem])

    resposta = cliente.post(f"/pedidos/{pid}/{acao}", data=dados)
    html = corpo(resposta)

    assert resposta.status_code == 400
    assert mensagem in html
    assert f"Novo Pedido - {pid}" in html  # a página do pedido, com os botões
    assert f'action="/pedidos/{pid}/{acao}"' in html
    assert pedido_no_banco(app, pid)["status"] == status
    assert len(consulta(app, "SELECT id FROM historico WHERE pedido_id = ?", (pid,))) == 1  # só o 'criar'


def test_chegada_sem_data_usa_hoje(app, pessoas, logar):
    pid = criar(app, pessoas["solicitante"], status="faturado")
    logar(pessoas["fiscal"]).post(f"/pedidos/{pid}/marcar_chegada", data={"data_chegada": ""})
    assert pedido_no_banco(app, pid)["data_chegada"] == hoje()


def test_acao_le_so_os_campos_da_propria_acao(app, pessoas, logar):
    pid = criar(app, pessoas["solicitante"], status="enviado")
    logar(pessoas["fiscal"]).post(
        f"/pedidos/{pid}/informar_nf",
        data={"numero_nf": "NF-1", "comentario": "não deveria ser lido", "data_chegada": "2000-01-01", "status": "recebido"},
    )
    pedido = pedido_no_banco(app, pid)
    assert pedido["status"] == "faturado" and pedido["data_chegada"] is None
    (linha,) = consulta(app, "SELECT comentario FROM historico WHERE acao = 'informar_nf'")
    assert linha["comentario"] is None


# --- e-mail em segundo plano -------------------------------------------------


@pytest.fixture
def disparos(monkeypatch):
    chamadas = []
    monkeypatch.setattr(notificacoes, "disparar_envio_em_segundo_plano", lambda app: chamadas.append(app))
    return chamadas


def test_criar_e_agir_disparam_o_envio_de_email(app, pessoas, logar, disparos):
    solicitante = logar(pessoas["solicitante"])
    pid = int(enviar_novo(solicitante).headers["Location"].rsplit("/", 1)[1])
    assert len(disparos) == 1 and disparos[0] is app

    logar(pessoas["gestor1"]).post(f"/pedidos/{pid}/aprovar")
    assert len(disparos) == 2


def test_falhas_nao_disparam_o_envio(app, pessoas, logar, disparos):
    pid = criar(app, pessoas["solicitante"])
    enviar_novo(logar(pessoas["solicitante"]), valor="abc")
    logar(pessoas["gestor1"]).post(f"/pedidos/{pid}/reprovar", data={"comentario": ""})
    logar(pessoas["fiscal"]).post(f"/pedidos/{pid}/aprovar")
    assert disparos == []


def test_disparo_real_em_modo_smtp_comeca_uma_thread(app, pessoas, logar, monkeypatch):
    import threading

    app.config["EMAIL_MODE"] = "smtp"
    threads, chamadas = [], []
    original = notificacoes.disparar_envio_em_segundo_plano

    def registrar(a):
        fio = original(a)
        threads.append(fio)
        return fio

    monkeypatch.setattr(notificacoes, "disparar_envio_em_segundo_plano", registrar)
    monkeypatch.setattr(notificacoes, "notificador_da_config", lambda config: "notificador-falso")
    monkeypatch.setattr(notificacoes, "enviar_pendentes", lambda db, n: chamadas.append(n) or 0)

    enviar_novo(logar(pessoas["solicitante"]))
    for fio in threads:
        fio.join(5)

    assert len(threads) == 1 and isinstance(threads[0], threading.Thread)
    assert chamadas == ["notificador-falso"]


# --- avisos e menu -----------------------------------------------------------


def test_avisos_listam_e_marcam_como_lido(app, pessoas, logar):
    pid = criar(app, pessoas["solicitante"])
    gestor = logar(pessoas["gestor1"])

    html = corpo(gestor.get("/avisos"))
    assert "Avisos (1)" in html
    assert f"Novo Pedido - {pid}: novo pedido registrado" in html
    assert f'href="/pedidos/{pid}"' in html
    assert "Há um novo pedido aguardando a sua decisão" in html
    assert 'class="aviso-item nao-lido"' in html
    (aviso,) = consulta(app, "SELECT id FROM avisos WHERE usuario_id = ?", (pessoas["gestor1"].id,))

    resposta = gestor.post(f"/avisos/{aviso['id']}/lido")
    assert resposta.status_code == 302 and resposta.headers["Location"].endswith("/avisos")

    html = corpo(gestor.get("/avisos"))
    assert "Avisos (0)" in html
    assert "nao-lido" not in html


def test_nao_marca_aviso_de_outro_usuario(app, pessoas, logar):
    criar(app, pessoas["solicitante"])
    (do_gestor,) = consulta(app, "SELECT id FROM avisos WHERE usuario_id = ?", (pessoas["gestor1"].id,))

    resposta = logar(pessoas["outro"]).post(f"/avisos/{do_gestor['id']}/lido")

    assert resposta.status_code == 404
    assert consulta(app, "SELECT lido FROM avisos WHERE id = ?", (do_gestor["id"],))[0]["lido"] == 0


def test_avisos_so_do_usuario_logado(app, pessoas, logar):
    criar(app, pessoas["solicitante"], cliente="Cliente Um")
    html = corpo(logar(pessoas["fiscal"]).get("/avisos"))
    assert "Nenhum aviso" in html and "Novo Pedido" not in html


def test_avisos_limitados_a_100_mais_novos_primeiro(app, pessoas, logar):
    with app.app_context():
        db = db_module.get_db()
        for n in range(1, 106):
            db.execute(
                "INSERT INTO avisos (usuario_id, assunto, corpo, email_status, criado_em)"
                " VALUES (?, ?, 'c', 'desligado', ?)",
                (pessoas["gestor1"].id, f"Assunto-{n:03d}", db_module.agora_utc()),
            )
    html = corpo(logar(pessoas["gestor1"]).get("/avisos"))
    assert html.count('class="aviso-item') == 100
    assert "Assunto-105" in html and "Assunto-006" in html and "Assunto-005" not in html
    assert html.index("Assunto-105") < html.index("Assunto-006")


def test_menu_por_perfil(pessoas, logar):
    esperado = {
        "solicitante": {"Novo pedido": True, "Usuários": False},
        "gestor1": {"Novo pedido": False, "Usuários": False},
        "fiscal": {"Novo pedido": False, "Usuários": False},
        "admin": {"Novo pedido": False, "Usuários": True},
    }
    for quem, itens in esperado.items():
        html = corpo(logar(pessoas[quem]).get("/"))
        assert 'href="/"' in html and "Avisos (0)" in html and 'action="/sair"' in html
        for rotulo, deve in itens.items():
            assert (f">{rotulo}</a>" in html) is deve, (quem, rotulo)


def test_contador_de_avisos_aparece_em_todas_as_telas(app, pessoas, logar):
    pid = criar(app, pessoas["solicitante"])
    gestor = logar(pessoas["gestor1"])
    for caminho in ("/", f"/pedidos/{pid}", "/avisos"):
        assert "Avisos (1)" in corpo(gestor.get(caminho))


# --- horário e texto ---------------------------------------------------------


def test_horario_do_historico_exibido_em_sao_paulo(app, pessoas, logar):
    pid = criar(app, pessoas["solicitante"])
    with app.app_context():
        db_module.get_db().execute(
            "UPDATE historico SET criado_em = '2026-10-23T15:00:00+00:00' WHERE pedido_id = ?", (pid,)
        )
    html = corpo(logar(pessoas["fiscal"]).get(f"/pedidos/{pid}"))
    assert "23/10/2026 12:00" in html
    assert "2026-10-23T15:00" not in html


def test_texto_com_html_aparece_escapado(app, pessoas, logar):
    pid = criar(app, pessoas["solicitante"], cliente=XSS, descricao=f"{XSS}\nsegunda linha {XSS}")
    gestor = logar(pessoas["gestor1"])
    gestor.post(f"/pedidos/{pid}/reprovar", data={"comentario": XSS})
    with app.app_context():
        db_module.get_db().execute(
            "UPDATE pedidos SET pdf_nome_original = ?, numero_nf = ? WHERE id = ?",
            (f"{XSS}.pdf", XSS, pid),
        )
    anterior = criar(app, pessoas["solicitante"], status="reprovado", cliente=XSS, descricao=XSS)

    paginas = [
        gestor.get("/"),
        gestor.get(f"/pedidos/{pid}"),
        logar(pessoas["solicitante"]).get(f"/pedidos/{pid}"),
        gestor.get("/avisos"),
        logar(pessoas["solicitante"]).get("/avisos"),
        logar(pessoas["solicitante"]).get(f"/pedidos/novo?reenvio_de={anterior}"),
        enviar_novo(logar(pessoas["solicitante"]), cliente=XSS, descricao=XSS, valor="x"),
        gestor.post(f"/pedidos/{pid}/aprovar", data={"comentario": XSS}),
        logar(pessoas["fiscal"]).get("/?status=reprovado"),
    ]
    for pagina in paginas:
        assert "<script>" not in corpo(pagina)
        assert "</script>" not in corpo(pagina)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in corpo(paginas[0])  # lista: cliente
    detalhe = corpo(paginas[1])
    assert detalhe.count("&lt;script&gt;alert(1)&lt;/script&gt;") >= 5  # cliente, descrição x2, motivo, nome do PDF, NF
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in corpo(paginas[3])  # avisos: corpo com o motivo
    assert "&lt;script&gt;" in corpo(paginas[5])  # formulário de reenvio pré-preenchido
    assert "&lt;script&gt;" in corpo(paginas[6])  # formulário reexibido após erro


# --- CSRF e CSP --------------------------------------------------------------


def test_post_sem_csrf_e_recusado_em_todas_as_rotas_de_pedidos(tmp_path):
    from app import auth

    app = create_app({"TESTING": True, "DATA_DIR": tmp_path / "data-csrf", "WTF_CSRF_ENABLED": True})
    app.test_client_class = ClienteHttps
    cliente = app.test_client()
    with app.app_context():
        auth.criar_usuario("Sol", "sol@exemplo.com", SENHA_TESTE, "solicitante", trocar_senha=False)
        auth.criar_usuario("Ges", "ges@exemplo.com", SENHA_TESTE, "gestor", trocar_senha=False)
    referer = {"Referer": "https://localhost/"}

    def token(html):
        return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)

    resposta = cliente.post(
        "/login",
        data={"email": "sol@exemplo.com", "senha": SENHA_TESTE, "csrf_token": token(corpo(cliente.get("/login")))},
        headers=referer,
    )
    assert resposta.status_code == 302

    # com token, o solicitante cria o pedido (formulário real)
    formulario = token(corpo(cliente.get("/pedidos/novo")))
    sem_token = cliente.post(
        "/pedidos/novo",
        data={"cliente": "C", "valor": "10,00", "descricao": "D", "arquivo": (io.BytesIO(PDF), "p.pdf")},
        content_type="multipart/form-data",
        headers=referer,
    )
    assert sem_token.status_code == 400
    assert consulta(app, "SELECT id FROM pedidos") == []
    com_token = cliente.post(
        "/pedidos/novo",
        data={"cliente": "C", "valor": "10,00", "descricao": "D", "arquivo": (io.BytesIO(PDF), "p.pdf"),
              "csrf_token": formulario},
        content_type="multipart/form-data",
        headers=referer,
    )
    assert com_token.status_code == 302
    pid = consulta(app, "SELECT id FROM pedidos")[0]["id"]

    # gestor: ações e avisos sem token são recusados
    gestor = app.test_client()
    gestor.post(
        "/login",
        data={"email": "ges@exemplo.com", "senha": SENHA_TESTE, "csrf_token": token(corpo(gestor.get("/login")))},
        headers=referer,
    )
    for acao in ACOES:
        assert gestor.post(f"/pedidos/{pid}/{acao}", data={"comentario": "x"}, headers=referer).status_code == 400
    (aviso,) = consulta(app, "SELECT id FROM avisos WHERE usuario_id = (SELECT id FROM usuarios WHERE perfil = 'gestor')")
    assert gestor.post(f"/avisos/{aviso['id']}/lido", headers=referer).status_code == 400
    assert pedido_no_banco(app, pid)["status"] == "pendente"
    assert consulta(app, "SELECT lido FROM avisos WHERE id = ?", (aviso["id"],))[0]["lido"] == 0

    # com o token da própria tela de detalhe, a ação passa
    detalhe = corpo(gestor.get(f"/pedidos/{pid}"))
    assert gestor.post(
        f"/pedidos/{pid}/aprovar", data={"csrf_token": token(detalhe)}, headers=referer
    ).status_code == 302
    assert pedido_no_banco(app, pid)["status"] == "aprovado"


def test_templates_nao_tem_estilo_nem_script_embutido():
    """A política de segurança (CSP) só aceita recursos do próprio servidor."""
    arquivos_html = sorted(TEMPLATES.glob("*.html"))
    assert len(arquivos_html) >= 8
    for caminho in arquivos_html:
        texto = caminho.read_text(encoding="utf-8").lower()
        for proibido in ("style=", "<style", "<script", "onclick=", "onsubmit="):
            assert proibido not in texto, f"{caminho.name} contém {proibido!r}"


# Nada que desligue o escape automático: nem nos templates nem no código Python.
ESCAPE_DESLIGADO = re.compile(r"\|\s*safe\b|\bMarkup\s*\(|\{%-?\s*autoescape\b", re.IGNORECASE)


def test_nada_desliga_o_escape_automatico():
    fontes = sorted(TEMPLATES.glob("*.html")) + sorted(TEMPLATES.parent.glob("*.py"))
    assert len(fontes) >= 18
    for caminho in fontes:
        for numero, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), 1):
            assert not ESCAPE_DESLIGADO.search(linha), f"{caminho.name}:{numero}: {linha.strip()}"


def test_status_e_acoes_tem_rotulo_em_portugues():
    from app.formatos import ACAO_LABEL, STATUS_LABEL

    assert set(STATUS_LABEL) == set(STATUS)
    assert set(ACAO_LABEL) == set(ACOES) | {"criar"}
    assert STATUS_LABEL["pendente"] == "Pendente"


# --- visual (Task 9): marcação que o estilo.css espera ------------------------

ESTILO = TEMPLATES.parent / "static" / "estilo.css"
LINK_ESTILO = '<link rel="stylesheet" href="/static/estilo.css">'
VIEWPORT = '<meta name="viewport" content="width=device-width, initial-scale=1">'
CLASSES_DE_STATUS = (
    "status-pendente", "status-aprovado", "status-reprovado",
    "status-enviado", "status-faturado", "status-recebido", "status-arquivado",
)
MSG_CSRF = "Sua sessão expirou ou o formulário é inválido. Volte, recarregue a página e tente de novo."


def test_paginas_tem_viewport_e_folha_de_estilo(app, pessoas, logar, client):
    pid = criar(app, pessoas["solicitante"])
    solicitante = logar(pessoas["solicitante"])
    paginas = {
        "login": client.get("/login"),
        "lista": solicitante.get("/"),
        "novo": solicitante.get("/pedidos/novo"),
        "detalhe": solicitante.get(f"/pedidos/{pid}"),
        "avisos": solicitante.get("/avisos"),
        "trocar_senha": solicitante.get("/trocar-senha"),
        "admin_usuarios": logar(pessoas["admin"]).get("/admin/usuarios"),
        "erro 404": solicitante.get("/nao-existe"),
    }
    for nome, resposta in paginas.items():
        html = corpo(resposta)
        assert resposta.status_code in (200, 404), nome
        assert VIEWPORT in html, nome
        assert '<html lang="pt-BR"' in html, nome
        assert LINK_ESTILO in html, nome


def test_todo_template_estende_a_base():
    # erro_minimo.html é a exceção de propósito: a página do erro 500/503 não pode
    # depender da base (que usa g.usuario e o contador de avisos, que consulta o banco).
    for caminho in sorted(TEMPLATES.glob("*.html")):
        if caminho.name in ("base.html", "_macros.html", "erro_minimo.html"):
            continue
        assert caminho.read_text(encoding="utf-8").startswith('{% extends "base.html" %}'), caminho.name
    minimo = (TEMPLATES / "erro_minimo.html").read_text(encoding="utf-8")
    for trecho in (VIEWPORT, '<html lang="pt-BR"', "url_for('static', filename='estilo.css')"):
        assert trecho in minimo
    assert "g.usuario" not in minimo and "extends" not in minimo


def test_valores_monetarios_usam_classe_num(app, pessoas, logar):
    pid = criar(app, pessoas["solicitante"], valor="1.234,56")
    fiscal = logar(pessoas["fiscal"])
    valor = r'class="num"[^>]*>\s*R\$ 1\.234,56\s*<'

    assert re.search(valor, corpo(fiscal.get("/")))
    assert re.search(valor, corpo(fiscal.get(f"/pedidos/{pid}")))


def test_lista_traz_rotulos_para_o_modo_cartao(app, pessoas, logar):
    criar(app, pessoas["solicitante"])
    html = corpo(logar(pessoas["fiscal"]).get("/"))

    for rotulo in ("Pedido", "Cliente", "Valor", "Status", "Data"):
        assert f'data-label="{rotulo}"' in html
    assert re.search(r'<span class="status status-pendente">Pendente</span>', html)


def test_estilo_css_servido(client):
    with client.get("/static/estilo.css") as resposta:  # fecha o arquivo servido
        css = resposta.get_data(as_text=True)

    assert resposta.status_code == 200
    assert resposta.headers["Content-Type"].startswith("text/css")
    for token in ("--creme: #f7f3ea", "--superficie: #fffdf8", "--grafite: #241f18",
                  "--texto: #2c2620", "--ambar: #946517", "--ambar-forte: #74500f", "--vermelho: #b3261e"):
        assert token in css
    assert ":root" in css and "--fonte-mono" in css and "--fonte-serif" in css
    for classe in CLASSES_DE_STATUS:
        assert f".{classe}" in css
    assert "prefers-reduced-motion" in css and ":focus-visible" in css


def test_estilo_css_nao_usa_recursos_externos():
    css = ESTILO.read_text(encoding="utf-8")

    assert "@import" not in css
    assert not re.search(r"url\(\s*[\"']?(https?:)?//", css, re.IGNORECASE)
    assert "http" not in css.lower()


def test_csrf_invalido_mostra_pagina_em_portugues(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path / "data-csrf", "WTF_CSRF_ENABLED": True})
    app.test_client_class = ClienteHttps

    resposta = app.test_client().post("/login", data={"email": "a@b.com", "senha": "x"})
    html = corpo(resposta)

    assert resposta.status_code == 400
    assert MSG_CSRF in html
    assert LINK_ESTILO in html and "Bad Request" not in html


def test_metodo_nao_permitido_mostra_pagina_em_portugues(client):
    resposta = client.get("/sair")
    html = corpo(resposta)

    assert resposta.status_code == 405
    assert "Método não permitido." in html
    assert LINK_ESTILO in html and "Method Not Allowed" not in html


# --- erro interno e banco ocupado ---------------------------------------------

MSG_ERRO_INTERNO = "Algo deu errado do nosso lado. Tente de novo em alguns instantes."


@pytest.fixture
def app_sem_propagar(tmp_path):
    """App de teste em que exceções não sobem até o pytest (como em produção)."""
    app = create_app(
        {"TESTING": True, "DATA_DIR": tmp_path / "data-500", "WTF_CSRF_ENABLED": False,
         "PROPAGATE_EXCEPTIONS": False}
    )
    app.test_client_class = ClienteHttps

    @app.get("/_quebra")
    def quebra():
        raise RuntimeError("segredo-interno-do-servidor")

    @app.get("/_banco/<tipo>")
    def banco(tipo):
        # Fecha a conexão antes: se a página de erro consultasse o banco, quebraria de novo.
        db_module.get_db().close()
        mensagem = "database is locked" if tipo == "ocupado" else "disk I/O error"
        raise sqlite3.OperationalError(mensagem)

    return app


def _logado_em(app):
    with app.app_context():
        auth.criar_usuario("Ana", "ana@exemplo.com", SENHA_TESTE, "solicitante", trocar_senha=False)
    cliente = app.test_client()
    assert cliente.post("/login", data={"email": "ana@exemplo.com", "senha": SENHA_TESTE}).status_code == 302
    return cliente


@pytest.mark.parametrize("logado", [False, True], ids=["anonimo", "logado"])
def test_erro_interno_mostra_pagina_em_portugues_sem_detalhes(app_sem_propagar, caplog, logado):
    cliente = _logado_em(app_sem_propagar) if logado else app_sem_propagar.test_client()

    resposta = cliente.get("/_quebra")
    html = corpo(resposta)

    assert resposta.status_code == 500
    assert MSG_ERRO_INTERNO in html
    assert LINK_ESTILO in html
    for proibido in ("Traceback", "segredo-interno", "RuntimeError", "Internal Server Error", 'File "'):
        assert proibido not in html
    assert "<script" not in html.lower() and not re.search(r"\sstyle\s*=", html)
    assert "segredo-interno-do-servidor" in caplog.text  # o detalhe vai para o log, não para a tela


@pytest.mark.parametrize("tipo, status", [("ocupado", 503), ("disco", 500)])
def test_erro_do_banco_mostra_a_mesma_pagina_sem_consultar_o_banco(
    app_sem_propagar, caplog, tipo, status
):
    cliente = _logado_em(app_sem_propagar)

    resposta = cliente.get(f"/_banco/{tipo}")
    html = corpo(resposta)

    assert resposta.status_code == status
    assert MSG_ERRO_INTERNO in html
    assert "database is locked" not in html and "OperationalError" not in html
    assert "OperationalError" in caplog.text


# --- link para trocar a senha -------------------------------------------------


@pytest.mark.parametrize("perfil", ["solicitante", "gestor1", "fiscal", "admin"])
def test_paginas_logadas_tem_link_para_trocar_a_senha(pessoas, logar, client, perfil):
    assert 'href="/trocar-senha"' not in corpo(client.get("/login"))
    cliente = logar(pessoas[perfil])
    for caminho in ("/", "/avisos"):
        assert 'href="/trocar-senha"' in corpo(cliente.get(caminho)), caminho


# --- PDF pelo formulário (Review Focus 5) -------------------------------------


@pytest.mark.parametrize(
    "arquivo, status, mensagem",
    [
        ((io.BytesIO(b""), "pedido.pdf"), 400, "O arquivo está vazio."),
        ((io.BytesIO(PDF), "NOTA.PDF"), 302, None),
        (None, 400, "Selecione um arquivo PDF."),
        ((io.BytesIO(b"PK\x03\x04 planilha disfarcada"), "pedido.pdf"), 400, "O arquivo não é um PDF válido."),
    ],
    ids=["vazio", "extensao-maiuscula", "sem-campo", "nao-e-pdf"],
)
def test_pdf_no_formulario_de_novo_pedido(app, pessoas, logar, arquivo, status, mensagem):
    cliente = logar(pessoas["solicitante"])
    dados = {"cliente": "Madeireira Boa Vista", "valor": "1.234,56", "descricao": "Compra de madeira"}
    if arquivo is not None:
        dados["arquivo"] = arquivo

    resposta = cliente.post("/pedidos/novo", data=dados, content_type="multipart/form-data")

    assert resposta.status_code == status
    pedidos = consulta(app, "SELECT * FROM pedidos")
    if status == 302:
        (pedido,) = pedidos
        assert resposta.headers["Location"].endswith(f"/pedidos/{pedido['id']}")
        assert pedido["pdf_nome_original"] == "NOTA.PDF"
        assert pedido["pdf_arquivo"].endswith(".pdf")
    else:
        assert mensagem in corpo(resposta)
        assert pedidos == []
        assert list((app.config["DATA_DIR"] / "pdfs").iterdir()) == []
