import logging
import smtplib
import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

from app import db as db_module
from app import notificacoes
from app.notificacoes import (
    Notificador,
    NotificadorDesligado,
    NotificadorSmtp,
    destinatarios,
    disparar_envio_em_segundo_plano,
    enviar_pendentes,
    notificador_da_config,
    registrar_avisos,
)

EVENTOS = ("criar", "aprovar", "reprovar", "confirmar_envio", "informar_nf", "marcar_chegada")


@pytest.fixture
def cenario(db, criar_usuario_teste):
    """Solicitante, dois gestores, um fiscal, um admin e um pedido do solicitante."""
    usuarios = {
        "solicitante": criar_usuario_teste("solicitante"),
        "gestor1": criar_usuario_teste("gestor"),
        "gestor2": criar_usuario_teste("gestor"),
        "fiscal": criar_usuario_teste("fiscal"),
        "admin": criar_usuario_teste("admin"),
        "outro": criar_usuario_teste("solicitante"),
    }
    pedido_id = inserir_pedido(db, usuarios["solicitante"].id)
    return usuarios, pedido(db, pedido_id)


def inserir_pedido(db, solicitante_id, **campos):
    dados = {
        "cliente": "Madeireira Boa Vista",
        "valor_centavos": 123456,
        "descricao": "Compra de madeira",
        "pdf_arquivo": "a" * 32 + ".pdf",
        "pdf_nome_original": "pedido.pdf",
        "status": "pendente",
        "solicitante_id": solicitante_id,
        "data_pedido": "2026-10-20",
        "numero_nf": "NF-998877",
        "data_chegada": "2026-10-25",
        "criado_em": db_module.agora_utc(),
        "atualizado_em": db_module.agora_utc(),
    }
    dados.update(campos)
    colunas = ", ".join(dados)
    marcas = ", ".join("?" for _ in dados)
    cursor = db.execute(f"INSERT INTO pedidos ({colunas}) VALUES ({marcas})", tuple(dados.values()))
    return cursor.lastrowid


def pedido(db, pedido_id):
    return db.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()


def avisos(db, **filtros):
    consulta = "SELECT * FROM avisos"
    if filtros:
        consulta += " WHERE " + " AND ".join(f"{k} = ?" for k in filtros)
    return db.execute(consulta + " ORDER BY id", tuple(filtros.values())).fetchall()


def aviso_de(db, usuario):
    (linha,) = avisos(db, usuario_id=usuario.id)
    return linha


def desativar(db, usuario):
    db.execute("UPDATE usuarios SET ativo = 0 WHERE id = ?", (usuario.id,))


# --- destinatários ----------------------------------------------------------


@pytest.mark.parametrize(
    "evento, esperado",
    [
        ("criar", ["solicitante", "gestor1", "gestor2"]),
        ("aprovar", ["solicitante", "fiscal"]),
        ("reprovar", ["solicitante"]),
        ("confirmar_envio", ["fiscal"]),
        ("informar_nf", ["solicitante"]),
        ("marcar_chegada", ["solicitante"]),
        ("arquivar", []),
    ],
)
def test_destinatarios_por_evento(db, cenario, evento, esperado):
    usuarios, ped = cenario
    assert destinatarios(db, evento, ped) == [usuarios[nome].id for nome in esperado]


def test_admin_e_outros_solicitantes_nunca_recebem(db, cenario):
    usuarios, ped = cenario
    for evento in EVENTOS:
        ids = destinatarios(db, evento, ped)
        assert usuarios["admin"].id not in ids
        assert usuarios["outro"].id not in ids


def test_solicitante_vem_primeiro_e_demais_por_id(db, cenario, criar_usuario_teste):
    usuarios, ped = cenario
    gestor3 = criar_usuario_teste("gestor")
    ids = destinatarios(db, "criar", ped)
    assert ids[0] == usuarios["solicitante"].id
    assert ids[1:] == sorted(ids[1:])
    assert ids[-1] == gestor3.id


def test_usuario_inativo_nao_recebe(db, cenario):
    usuarios, ped = cenario
    desativar(db, usuarios["gestor1"])
    desativar(db, usuarios["fiscal"])
    assert destinatarios(db, "criar", ped) == [usuarios["solicitante"].id, usuarios["gestor2"].id]
    assert destinatarios(db, "aprovar", ped) == [usuarios["solicitante"].id]
    assert destinatarios(db, "confirmar_envio", ped) == []


def test_solicitante_inativo_nao_recebe(db, cenario):
    usuarios, ped = cenario
    desativar(db, usuarios["solicitante"])
    assert destinatarios(db, "reprovar", ped) == []
    assert destinatarios(db, "criar", ped) == [usuarios["gestor1"].id, usuarios["gestor2"].id]


def test_destinatarios_sem_duplicados(db, criar_usuario_teste):
    gestor = criar_usuario_teste("gestor")
    outro = criar_usuario_teste("gestor")
    ped = pedido(db, inserir_pedido(db, gestor.id))  # o gestor também é o solicitante
    assert destinatarios(db, "criar", ped) == [gestor.id, outro.id]


def test_evento_desconhecido_levanta_erro(db, cenario):
    _, ped = cenario
    with pytest.raises(ValueError, match="explodir"):
        destinatarios(db, "explodir", ped)
    with pytest.raises(ValueError, match="explodir"):
        registrar_avisos(db, "explodir", ped)


# --- registrar_avisos -------------------------------------------------------


def test_registrar_avisos_grava_uma_linha_por_destinatario(db, cenario):
    usuarios, ped = cenario
    ids = registrar_avisos(db, "criar", ped)

    linhas = avisos(db)
    assert ids == [linha["id"] for linha in linhas]
    assert [linha["usuario_id"] for linha in linhas] == destinatarios(db, "criar", ped)
    for linha in linhas:
        assert linha["pedido_id"] == ped["id"]
        assert linha["lido"] == 0
        assert linha["criado_em"].endswith("+00:00")


def test_arquivar_nao_cria_avisos(db, cenario):
    _, ped = cenario
    assert registrar_avisos(db, "arquivar", ped) == []
    assert avisos(db) == []


def test_aviso_contem_titulo_e_link_do_pedido(db, cenario):
    usuarios, ped = cenario
    for evento in EVENTOS:
        db.execute("DELETE FROM avisos")
        registrar_avisos(db, evento, ped)
        for linha in avisos(db):
            titulo = f"Novo Pedido - {ped['id']}"
            assert linha["assunto"].startswith(f"{titulo}: ")
            assert titulo in linha["corpo"]
            assert "Madeireira Boa Vista" in linha["corpo"]
            assert "R$ 1.234,56" in linha["corpo"]
            assert f"http://localhost:8000/pedidos/{ped['id']}" in linha["corpo"]


def test_link_usa_base_url_sem_barra_dupla(app, db, cenario):
    _, ped = cenario
    app.config["BASE_URL"] = "https://pedidos.exemplo.com.br/"
    registrar_avisos(db, "reprovar", ped)
    (linha,) = avisos(db)
    assert f"https://pedidos.exemplo.com.br/pedidos/{ped['id']}" in linha["corpo"]
    assert "br//pedidos" not in linha["corpo"]


def test_assunto_difere_por_evento(db, cenario):
    _, ped = cenario
    assuntos = set()
    for evento in EVENTOS:
        db.execute("DELETE FROM avisos")
        registrar_avisos(db, evento, ped)
        assuntos.add(avisos(db)[0]["assunto"])
    assert len(assuntos) == len(EVENTOS)


# Frases que identificam a orientação dada a cada papel em cada evento.
ORIENTACAO = [
    ("criar", "solicitante", ["foi registrado", "gestores"]),
    ("criar", "gestor1", ["aprovar ou reprovar"]),
    ("criar", "gestor2", ["aprovar ou reprovar"]),
    ("aprovar", "solicitante", ["foi aprovado", "confirme o envio"]),
    ("aprovar", "fiscal", ["foi aprovado", "aguarde o faturamento"]),
    ("reprovar", "solicitante", ["foi reprovado"]),
    ("confirmar_envio", "fiscal", ["envio", "20/10/2026", "faturamento"]),
    ("informar_nf", "solicitante", ["nota fiscal", "NF-998877"]),
    ("marcar_chegada", "solicitante", ["chegada", "25/10/2026"]),
]


@pytest.mark.parametrize("evento, papel, frases", ORIENTACAO)
def test_corpo_orienta_cada_papel(db, cenario, evento, papel, frases):
    usuarios, ped = cenario
    registrar_avisos(db, evento, ped)
    corpo = aviso_de(db, usuarios[papel])["corpo"]
    for frase in frases:
        assert frase.lower() in corpo.lower(), (evento, papel, frase)


def test_corpo_difere_conforme_o_papel(db, cenario):
    usuarios, ped = cenario
    registrar_avisos(db, "aprovar", ped)
    para_solicitante = aviso_de(db, usuarios["solicitante"])["corpo"]
    para_fiscal = aviso_de(db, usuarios["fiscal"])["corpo"]
    assert para_solicitante != para_fiscal
    assert "confirme o envio" not in para_fiscal.lower()
    assert "aguarde o faturamento" not in para_solicitante.lower()


def test_reprovar_inclui_o_motivo_mais_recente(db, cenario):
    usuarios, ped = cenario
    for comentario in ("Motivo antigo", "Preço acima do combinado"):
        db.execute(
            "INSERT INTO historico (pedido_id, usuario_id, acao, status_anterior, status_novo,"
            " comentario, criado_em) VALUES (?, ?, 'reprovar', 'pendente', 'reprovado', ?, ?)",
            (ped["id"], usuarios["gestor1"].id, comentario, db_module.agora_utc()),
        )
    # Um comentário de outra ação ou de outro pedido não conta.
    db.execute(
        "INSERT INTO historico (pedido_id, usuario_id, acao, status_novo, comentario, criado_em)"
        " VALUES (?, ?, 'aprovar', 'aprovado', 'Outro assunto', ?)",
        (ped["id"], usuarios["gestor1"].id, db_module.agora_utc()),
    )
    registrar_avisos(db, "reprovar", ped)
    corpo = aviso_de(db, usuarios["solicitante"])["corpo"]
    assert "Preço acima do combinado" in corpo
    assert "Motivo antigo" not in corpo
    assert "Outro assunto" not in corpo


def test_reprovar_sem_motivo_registrado(db, cenario):
    usuarios, ped = cenario
    registrar_avisos(db, "reprovar", ped)
    assert "Sem motivo registrado" in aviso_de(db, usuarios["solicitante"])["corpo"]


def test_reprovar_com_comentario_vazio_cai_no_texto_padrao(db, cenario):
    usuarios, ped = cenario
    db.execute(
        "INSERT INTO historico (pedido_id, usuario_id, acao, status_anterior, status_novo,"
        " comentario, criado_em) VALUES (?, ?, 'reprovar', 'pendente', 'reprovado', NULL, ?)",
        (ped["id"], usuarios["gestor1"].id, db_module.agora_utc()),
    )
    registrar_avisos(db, "reprovar", ped)
    assert "Sem motivo registrado" in aviso_de(db, usuarios["solicitante"])["corpo"]


def test_corpo_nao_mistura_dados_de_outro_pedido(db, cenario):
    usuarios, ped = cenario
    outro = pedido(db, inserir_pedido(db, usuarios["solicitante"].id, cliente="Cliente Y"))
    registrar_avisos(db, "criar", ped)
    for linha in avisos(db):
        assert "Cliente Y" not in linha["corpo"]
        assert linha["pedido_id"] == ped["id"] != outro["id"]


def test_email_desligado_marca_desligado(db, cenario):
    _, ped = cenario
    registrar_avisos(db, "criar", ped)
    assert {linha["email_status"] for linha in avisos(db)} == {"desligado"}


def test_email_smtp_marca_pendente(app, db, cenario):
    _, ped = cenario
    app.config["EMAIL_MODE"] = "smtp"
    registrar_avisos(db, "criar", ped)
    assert {linha["email_status"] for linha in avisos(db)} == {"pendente"}


def test_registrar_avisos_junta_se_a_transacao_externa(db, cenario):
    """Se a transação de fora desfaz, os avisos somem junto (mesma transação)."""
    _, ped = cenario
    with pytest.raises(RuntimeError):
        with db_module.transacao():
            registrar_avisos(db, "criar", ped)
            assert len(avisos(db)) == 3
            raise RuntimeError("desfaz")
    assert avisos(db) == []


# --- enviar_pendentes -------------------------------------------------------


class NotificadorFalso:
    def __init__(self, falhar_para=()):
        self.falhar_para = set(falhar_para)
        self.enviados = []
        self.chamadas = []

    def enviar(self, destino, assunto, corpo):
        self.chamadas.append(destino)
        if destino in self.falhar_para:
            raise ConnectionError("servidor fora do ar")
        self.enviados.append((destino, assunto, corpo))


@pytest.fixture
def smtp_pendentes(app, db, cenario):
    """Cenário com EMAIL_MODE=smtp e os 3 avisos de 'criar' pendentes."""
    usuarios, ped = cenario
    app.config["EMAIL_MODE"] = "smtp"
    registrar_avisos(db, "criar", ped)
    return usuarios, ped


class RelogioEmail:
    def __init__(self):
        self.agora = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)

    def avancar(self, **tempo):
        self.agora += timedelta(**tempo)


@pytest.fixture
def relogio(monkeypatch):
    """Relógio do envio de e-mail, para andar com o tempo do reenvio."""
    relogio = RelogioEmail()
    monkeypatch.setattr(notificacoes, "_agora", lambda: relogio.agora)
    return relogio


def status_por_usuario(db):
    return {
        linha["usuario_id"]: linha["email_status"]
        for linha in db.execute("SELECT usuario_id, email_status FROM avisos")
    }


def test_enviar_pendentes_envia_e_marca_enviado(db, smtp_pendentes):
    usuarios, _ = smtp_pendentes
    notificador = NotificadorFalso()

    assert enviar_pendentes(db, notificador) == 3

    destinos = [d for d, _, _ in notificador.enviados]
    assert destinos == [usuarios[n].email for n in ("solicitante", "gestor1", "gestor2")]
    for linha in avisos(db):
        assert linha["email_status"] == "enviado"
    # O assunto e o corpo enviados são os do aviso gravado.
    primeiro = avisos(db)[0]
    assert notificador.enviados[0][1:] == (primeiro["assunto"], primeiro["corpo"])


def test_falha_de_email_marca_erro_e_nao_levanta(db, smtp_pendentes):
    usuarios, _ = smtp_pendentes
    notificador = NotificadorFalso(falhar_para={usuarios["gestor1"].email})

    assert enviar_pendentes(db, notificador) == 2  # não levanta

    status = status_por_usuario(db)
    assert status[usuarios["gestor1"].id] == "erro"
    assert status[usuarios["solicitante"].id] == "enviado"
    assert status[usuarios["gestor2"].id] == "enviado"  # continuou depois da falha
    assert len(notificador.chamadas) == 3


def test_reenvio_envia_os_com_erro(db, smtp_pendentes, relogio):
    usuarios, _ = smtp_pendentes
    enviar_pendentes(db, NotificadorFalso(falhar_para={usuarios["gestor1"].email}))

    relogio.avancar(minutes=15)  # depois da espera da 1ª falha
    notificador = NotificadorFalso()
    assert enviar_pendentes(db, notificador) == 1

    assert notificador.chamadas == [usuarios["gestor1"].email]
    assert set(status_por_usuario(db).values()) == {"enviado"}


def test_segunda_chamada_nao_reenvia_os_enviados(db, smtp_pendentes):
    enviar_pendentes(db, NotificadorFalso())
    notificador = NotificadorFalso()
    assert enviar_pendentes(db, notificador) == 0
    assert notificador.chamadas == []


def test_enviar_pendentes_ignora_desligado_e_enviado(app, db, cenario):
    _, ped = cenario
    registrar_avisos(db, "criar", ped)  # EMAIL_MODE padrão: 'desligado'
    notificador = NotificadorFalso()
    assert enviar_pendentes(db, notificador) == 0
    assert notificador.chamadas == []
    assert {linha["email_status"] for linha in avisos(db)} == {"desligado"}


def test_enviar_pendentes_envia_fora_de_transacao(db, smtp_pendentes):
    """Rede nunca segura o bloqueio de escrita do SQLite."""
    from flask import g

    niveis = []

    class Espiao:
        def enviar(self, destino, assunto, corpo):
            niveis.append(g.get("transacao_nivel", 0))

    enviar_pendentes(db, Espiao())
    assert niveis == [0, 0, 0]


def test_enviar_pendentes_outra_conexao_pode_escrever_durante_o_envio(app, db, smtp_pendentes):
    import sqlite3

    resultados = []

    class Espiao:
        def enviar(self, destino, assunto, corpo):
            outra = sqlite3.connect(db_module.caminho_banco(), timeout=0.2, isolation_level=None)
            try:
                outra.execute("BEGIN IMMEDIATE")
                outra.execute("COMMIT")
                resultados.append("ok")
            except sqlite3.OperationalError as erro:
                resultados.append(str(erro))
            finally:
                outra.close()

    enviar_pendentes(db, Espiao())
    assert resultados == ["ok", "ok", "ok"]


def test_enviar_pendentes_trata_erro_de_smtplib_como_erro_de_envio(db, smtp_pendentes):
    """Erros do smtplib (não só de socket) viram 'erro' em vez de propagar."""

    class Quebrado:
        def enviar(self, destino, assunto, corpo):
            raise smtplib.SMTPAuthenticationError(535, b"credenciais recusadas")

    assert enviar_pendentes(db, Quebrado()) == 0
    assert set(status_por_usuario(db).values()) == {"erro"}


def _inserir_avisos(db, usuario_id, quantidade, email_status="pendente"):
    for n in range(quantidade):
        db.execute(
            "INSERT INTO avisos (usuario_id, assunto, corpo, email_status, criado_em)"
            " VALUES (?, ?, ?, ?, ?)",
            (usuario_id, f"Assunto {n}", "Corpo", email_status, db_module.agora_utc()),
        )


class _Resultado:
    def __init__(self, linhas):
        self._linhas = linhas

    def fetchall(self):
        return self._linhas


class DbEspiao:
    """Conta quantas linhas cada consulta de avisos devolveu (uma por passada)."""

    def __init__(self, db):
        self.db = db
        self.passadas = []

    def execute(self, sql, parametros=()):
        cursor = self.db.execute(sql, parametros)
        if sql.lstrip().startswith("SELECT"):
            linhas = cursor.fetchall()
            self.passadas.append(len(linhas))
            return _Resultado(linhas)
        return cursor


def test_uma_chamada_envia_todos_em_passadas_de_no_maximo_50(app, db, cenario):
    usuarios, _ = cenario
    app.config["EMAIL_MODE"] = "smtp"
    _inserir_avisos(db, usuarios["solicitante"].id, 60)
    notificador = NotificadorFalso()
    espiao = DbEspiao(db)

    # Antes do laço, a chamada parava em 50 e os 10 restantes esperavam outra ação.
    assert enviar_pendentes(espiao, notificador) == 60
    assert len(notificador.chamadas) == 60
    assert espiao.passadas == [50, 10]
    assert enviar_pendentes(db, notificador) == 0


def test_120_pendentes_saem_numa_chamada_em_tres_passadas(app, db, cenario):
    usuarios, _ = cenario
    app.config["EMAIL_MODE"] = "smtp"
    _inserir_avisos(db, usuarios["solicitante"].id, 120)
    espiao = DbEspiao(db)

    assert enviar_pendentes(espiao, NotificadorFalso()) == 120
    assert espiao.passadas == [50, 50, 20]
    assert db.execute("SELECT COUNT(*) FROM avisos WHERE email_status != 'enviado'").fetchone()[0] == 0


def test_aviso_que_chega_durante_o_envio_sai_sem_novo_gatilho(app, db, smtp_pendentes):
    usuarios, _ = smtp_pendentes
    dentro, liberar = threading.Event(), threading.Event()
    enviados, resultado = [], []

    class Lento:
        def enviar(self, destino, assunto, corpo):
            enviados.append(destino)
            if len(enviados) == 1:
                dentro.set()
                assert liberar.wait(5)

    def primeira():
        with app.app_context():
            resultado.append(enviar_pendentes(db_module.get_db(), Lento()))

    fio = threading.Thread(target=primeira)
    fio.start()
    try:
        assert dentro.wait(5)
        _inserir_avisos(db, usuarios["outro"].id, 1)  # chega com o lote já lido
        barrado = NotificadorFalso()
        assert enviar_pendentes(db, barrado) == 0
        assert barrado.chamadas == []
    finally:
        liberar.set()
        fio.join(5)

    assert not fio.is_alive()
    assert resultado == [4]  # os 3 do lote + o que chegou durante o envio
    assert usuarios["outro"].email in enviados
    assert status_por_usuario(db)[usuarios["outro"].id] == "enviado"


def test_pedido_que_chega_entre_a_ultima_consulta_e_a_liberacao_refaz_a_passada(app, db, smtp_pendentes):
    original = notificacoes._enviar_tudo
    chamadas = []

    def espiao(*args):
        enviados = original(*args)
        chamadas.append(enviados)
        if len(chamadas) == 1:
            notificacoes._ha_trabalho.set()  # outro pedido chegou na fresta antes de soltar a trava
        return enviados

    notificacoes._enviar_tudo = espiao
    try:
        assert enviar_pendentes(db, NotificadorFalso()) == 3
    finally:
        notificacoes._enviar_tudo = original
    assert chamadas == [3, 0]  # a trava foi retomada e a consulta repetida
    assert not notificacoes._trava_envio.locked()


def test_aviso_que_sempre_falha_e_tentado_uma_vez_por_chamada(app, db, cenario, relogio):
    usuarios, _ = cenario
    app.config["EMAIL_MODE"] = "smtp"
    _inserir_avisos(db, usuarios["solicitante"].id, 55)  # todos falham
    _inserir_avisos(db, usuarios["gestor1"].id, 3)
    notificador = NotificadorFalso(falhar_para={usuarios["solicitante"].email})

    assert enviar_pendentes(db, notificador) == 3  # termina, e os demais saem
    assert notificador.chamadas.count(usuarios["solicitante"].email) == 55
    assert notificador.chamadas.count(usuarios["gestor1"].email) == 3

    relogio.avancar(minutes=15)  # vencida a espera da 1ª falha
    assert enviar_pendentes(db, notificador) == 0  # próxima chamada: mais uma tentativa de cada
    assert notificador.chamadas.count(usuarios["solicitante"].email) == 110
    assert status_por_usuario(db)[usuarios["solicitante"].id] == "erro"


def test_avisos_com_erro_nao_impedem_o_envio_dos_novos(app, db, cenario):
    """Endereço que sempre falha não pode ocupar as 50 vagas e travar os demais."""
    usuarios, _ = cenario
    _inserir_avisos(db, usuarios["solicitante"].id, 50, email_status="erro")
    _inserir_avisos(db, usuarios["gestor1"].id, 1)
    notificador = NotificadorFalso()

    enviar_pendentes(db, notificador)

    assert notificador.chamadas[0] == usuarios["gestor1"].email
    assert status_por_usuario(db)[usuarios["gestor1"].id] == "enviado"


def test_segunda_chamada_simultanea_retorna_zero_sem_enviar(app, db, smtp_pendentes):
    dentro, liberar = threading.Event(), threading.Event()
    resultado = []

    class Lento:
        def enviar(self, destino, assunto, corpo):
            dentro.set()
            assert liberar.wait(5)

    def primeira():
        with app.app_context():
            resultado.append(enviar_pendentes(db_module.get_db(), Lento()))

    fio = threading.Thread(target=primeira)
    fio.start()
    try:
        assert dentro.wait(5)
        segundo = NotificadorFalso()
        assert enviar_pendentes(db, segundo) == 0
        assert segundo.chamadas == []
    finally:
        liberar.set()
        fio.join(5)
    assert resultado == [3]


def test_trava_do_envio_e_liberada_mesmo_se_o_banco_falha():
    class BancoQuebrado:
        def execute(self, *args, **kwargs):
            raise RuntimeError("banco indisponível")

    with pytest.raises(RuntimeError):
        enviar_pendentes(BancoQuebrado(), NotificadorFalso())
    assert not notificacoes._trava_envio.locked()


# --- tentativas e espera entre elas -------------------------------------------


def _aviso(db, aviso_id):
    return db.execute("SELECT * FROM avisos WHERE id = ?", (aviso_id,)).fetchone()


def _um_pendente(db, usuario):
    _inserir_avisos(db, usuario.id, 1)
    return db.execute("SELECT MAX(id) FROM avisos").fetchone()[0]


def test_envio_com_sucesso_conta_uma_tentativa_e_guarda_a_hora(app, db, cenario, relogio):
    usuarios, _ = cenario
    aviso_id = _um_pendente(db, usuarios["solicitante"])

    assert enviar_pendentes(db, NotificadorFalso()) == 1

    linha = _aviso(db, aviso_id)
    assert linha["email_status"] == "enviado"
    assert linha["email_tentativas"] == 1
    assert linha["email_ultima_tentativa"] == relogio.agora.isoformat()


def test_falha_conta_a_tentativa_e_espera_15_minutos_vezes_as_tentativas(app, db, cenario, relogio):
    usuarios, _ = cenario
    aviso_id = _um_pendente(db, usuarios["solicitante"])
    sempre_falha = NotificadorFalso(falhar_para={usuarios["solicitante"].email})

    enviar_pendentes(db, sempre_falha)  # 1ª tentativa, às 12:00
    assert (_aviso(db, aviso_id)["email_status"], _aviso(db, aviso_id)["email_tentativas"]) == ("erro", 1)
    assert _aviso(db, aviso_id)["email_ultima_tentativa"] == relogio.agora.isoformat()

    relogio.avancar(minutes=14, seconds=59)
    enviar_pendentes(db, sempre_falha)
    assert len(sempre_falha.chamadas) == 1  # ainda na espera de 15 min
    relogio.avancar(seconds=1)
    enviar_pendentes(db, sempre_falha)  # 2ª, às 12:15
    assert len(sempre_falha.chamadas) == 2
    assert _aviso(db, aviso_id)["email_tentativas"] == 2

    relogio.avancar(minutes=29, seconds=59)  # depois de 2 falhas a espera é de 30 min
    enviar_pendentes(db, sempre_falha)
    assert len(sempre_falha.chamadas) == 2
    relogio.avancar(seconds=1)
    enviar_pendentes(db, sempre_falha)
    assert len(sempre_falha.chamadas) == 3

    relogio.avancar(minutes=45)  # e, depois de 3, de 45 min: agora vai e dá certo
    assert enviar_pendentes(db, NotificadorFalso()) == 1
    linha = _aviso(db, aviso_id)
    assert (linha["email_status"], linha["email_tentativas"]) == ("enviado", 4)


def test_depois_da_quinta_falha_o_aviso_fica_em_erro_para_sempre(app, db, cenario, relogio):
    usuarios, _ = cenario
    aviso_id = _um_pendente(db, usuarios["solicitante"])
    sempre_falha = NotificadorFalso(falhar_para={usuarios["solicitante"].email})

    for tentativa in range(1, 6):
        enviar_pendentes(db, sempre_falha)
        assert _aviso(db, aviso_id)["email_tentativas"] == tentativa
        relogio.avancar(minutes=15 * tentativa)
    assert len(sempre_falha.chamadas) == 5

    relogio.avancar(days=30)
    funciona = NotificadorFalso()
    assert enviar_pendentes(db, funciona) == 0
    assert funciona.chamadas == []
    linha = _aviso(db, aviso_id)
    assert (linha["email_status"], linha["email_tentativas"]) == ("erro", 5)


def test_erro_antigo_sem_hora_registrada_pode_ser_tentado(app, db, cenario, relogio):
    usuarios, _ = cenario
    _inserir_avisos(db, usuarios["solicitante"].id, 1, email_status="erro")
    assert enviar_pendentes(db, NotificadorFalso()) == 1


@pytest.mark.parametrize("email_status", ["pendente", "erro"])
def test_usuario_inativo_nao_recebe_email_e_o_aviso_fica_como_esta(
    app, db, cenario, relogio, email_status
):
    usuarios, _ = cenario
    _inserir_avisos(db, usuarios["gestor1"].id, 1, email_status=email_status)
    _inserir_avisos(db, usuarios["gestor2"].id, 1, email_status=email_status)
    desativar(db, usuarios["gestor1"])
    notificador = NotificadorFalso()

    assert enviar_pendentes(db, notificador) == 1

    assert notificador.chamadas == [usuarios["gestor2"].email]
    linha = aviso_de(db, usuarios["gestor1"])
    assert (linha["email_status"], linha["email_tentativas"]) == (email_status, 0)


# --- envio em segundo plano --------------------------------------------------


def test_disparar_nao_faz_nada_com_email_desligado(app, monkeypatch):
    chamadas = []
    monkeypatch.setattr(notificacoes, "enviar_pendentes", lambda *a: chamadas.append(a))
    assert disparar_envio_em_segundo_plano(app) is None
    assert chamadas == []


def test_disparar_em_modo_smtp_envia_numa_thread_daemon(app, monkeypatch):
    app.config["EMAIL_MODE"] = "smtp"
    falso = object()
    chamadas = []

    def fake_enviar(db, notificador):
        chamadas.append((threading.current_thread(), db_module.get_db() is db, notificador))
        return 0

    monkeypatch.setattr(notificacoes, "notificador_da_config", lambda config: falso)
    monkeypatch.setattr(notificacoes, "enviar_pendentes", fake_enviar)

    fio = disparar_envio_em_segundo_plano(app)

    assert isinstance(fio, threading.Thread) and fio.daemon
    fio.join(5)
    assert not fio.is_alive()
    (thread, mesma_conexao, notificador), = chamadas
    assert thread is fio and mesma_conexao and notificador is falso


def test_disparar_engole_e_registra_o_erro_sem_segredos(app, monkeypatch, caplog):
    app.config["EMAIL_MODE"] = "smtp"

    def quebrar(config):
        raise RuntimeError("EMAIL_MODE=smtp exige a variável de ambiente PEDIDOS_SMTP_HOST.")

    monkeypatch.setattr(notificacoes, "notificador_da_config", quebrar)
    with caplog.at_level(logging.WARNING, logger="app.notificacoes"):
        fio = disparar_envio_em_segundo_plano(app)
        fio.join(5)

    assert not fio.is_alive()
    assert any("PEDIDOS_SMTP_HOST" in r.getMessage() for r in caplog.records)
    assert all(r.exc_info is None for r in caplog.records)


# --- NotificadorDesligado ---------------------------------------------------


def test_notificador_desligado_nao_faz_nada():
    notificador = NotificadorDesligado()
    assert notificador.enviar("a@exemplo.com", "assunto", "corpo") is None


def test_notificadores_cumprem_o_protocolo():
    assert isinstance(NotificadorDesligado(), Notificador)
    assert isinstance(NotificadorSmtp("h", 587, "u", "s", "r@exemplo.com"), Notificador)


# --- NotificadorSmtp --------------------------------------------------------


class SmtpFalso:
    """Substitui smtplib.SMTP: registra as chamadas, não toca na rede."""

    instancias = []
    falhar_em = None

    def __init__(self, host, porta, timeout=None):
        self.chamadas = [("init", host, porta, timeout)]
        self.mensagem = None
        SmtpFalso.instancias.append(self)

    def _registrar(self, nome, *args):
        self.chamadas.append((nome, *args))
        if SmtpFalso.falhar_em == nome:
            raise smtplib.SMTPException(f"falha em {nome}")

    def starttls(self, **kwargs):
        self._registrar("starttls")

    def login(self, usuario, senha):
        self._registrar("login", usuario, senha)

    def send_message(self, mensagem):
        self.mensagem = mensagem
        self._registrar("send_message")

    def quit(self):
        self._registrar("quit")

    def close(self):
        self._registrar("close")

    @property
    def nomes(self):
        return [c[0] for c in self.chamadas]


@pytest.fixture
def smtp_falso(monkeypatch):
    SmtpFalso.instancias = []
    SmtpFalso.falhar_em = None
    monkeypatch.setattr(smtplib, "SMTP", SmtpFalso)
    return SmtpFalso


def test_smtp_envia_com_starttls_login_e_cabecalhos(smtp_falso):
    notificador = NotificadorSmtp("smtp.exemplo.com", 587, "robo", "segredo", "pedidos@exemplo.com")
    notificador.enviar("fulano@exemplo.com", "Novo Pedido - 1: aprovado", "Olá, pedido ação.\nLink")

    (conexao,) = smtp_falso.instancias
    assert conexao.chamadas[0] == ("init", "smtp.exemplo.com", 587, 15)
    assert conexao.nomes[:4] == ["init", "starttls", "login", "send_message"]
    assert ("login", "robo", "segredo") in conexao.chamadas
    assert conexao.nomes[-1] in ("quit", "close")

    mensagem = conexao.mensagem
    assert isinstance(mensagem, EmailMessage)
    assert mensagem["From"] == "pedidos@exemplo.com"
    assert mensagem["To"] == "fulano@exemplo.com"
    assert mensagem["Subject"] == "Novo Pedido - 1: aprovado"
    assert mensagem.get_content_type() == "text/plain"
    assert mensagem.get_content_charset() == "utf-8"
    assert mensagem.get_content().strip() == "Olá, pedido ação.\nLink"


def test_smtp_sem_usuario_nao_faz_login(smtp_falso):
    NotificadorSmtp("smtp.exemplo.com", 25, None, None, "pedidos@exemplo.com").enviar("a@b.com", "x", "y")
    (conexao,) = smtp_falso.instancias
    assert "login" not in conexao.nomes
    assert "starttls" in conexao.nomes


def test_smtp_fecha_a_conexao_mesmo_se_o_envio_falha(smtp_falso):
    smtp_falso.falhar_em = "send_message"
    notificador = NotificadorSmtp("h", 587, "u", "s", "r@exemplo.com")
    with pytest.raises(smtplib.SMTPException):
        notificador.enviar("a@b.com", "x", "y")
    (conexao,) = smtp_falso.instancias
    assert conexao.nomes[-1] in ("quit", "close")


def test_smtp_fecha_a_conexao_se_o_login_falha(smtp_falso):
    smtp_falso.falhar_em = "login"
    notificador = NotificadorSmtp("h", 587, "u", "s", "r@exemplo.com")
    with pytest.raises(smtplib.SMTPException):
        notificador.enviar("a@b.com", "x", "y")
    (conexao,) = smtp_falso.instancias
    assert "send_message" not in conexao.nomes
    assert conexao.nomes[-1] in ("quit", "close")


def test_smtp_fecha_mesmo_se_o_quit_falha(smtp_falso):
    smtp_falso.falhar_em = "quit"
    NotificadorSmtp("h", 587, "u", "s", "r@exemplo.com").enviar("a@b.com", "x", "y")
    (conexao,) = smtp_falso.instancias
    assert "close" in conexao.nomes


def test_smtp_nao_deixa_quebra_de_linha_virar_cabecalho(smtp_falso):
    notificador = NotificadorSmtp("h", 587, "u", "s", "r@exemplo.com")
    with pytest.raises(ValueError):
        notificador.enviar("a@b.com", "assunto\nBcc: intruso@exemplo.com", "corpo")


def test_smtp_nao_imprime_credenciais(smtp_falso, capsys, caplog):
    smtp_falso.falhar_em = "login"
    with pytest.raises(smtplib.SMTPException):
        NotificadorSmtp("h", 587, "robo", "segredo-secreto", "r@exemplo.com").enviar("a@b.com", "x", "y")
    saida = capsys.readouterr()
    assert "segredo-secreto" not in saida.out + saida.err + caplog.text


def test_repr_do_notificador_smtp_nao_mostra_a_senha():
    assert "segredo-secreto" not in repr(NotificadorSmtp("h", 587, "u", "segredo-secreto", "r@exemplo.com"))


# --- notificador_da_config --------------------------------------------------

AMBIENTE_SMTP = {
    "PEDIDOS_SMTP_HOST": "smtp.exemplo.com",
    "PEDIDOS_SMTP_PORTA": "2525",
    "PEDIDOS_SMTP_USUARIO": "robo",
    "PEDIDOS_SMTP_SENHA": "segredo",
    "PEDIDOS_SMTP_REMETENTE": "pedidos@exemplo.com",
}


def test_config_desligada_devolve_notificador_desligado():
    assert isinstance(notificador_da_config({"EMAIL_MODE": "desligado"}, environ=AMBIENTE_SMTP), NotificadorDesligado)


def test_config_smtp_monta_o_notificador_a_partir_do_ambiente(smtp_falso):
    notificador = notificador_da_config({"EMAIL_MODE": "smtp"}, environ=AMBIENTE_SMTP)
    assert isinstance(notificador, NotificadorSmtp)
    notificador.enviar("a@b.com", "x", "y")
    (conexao,) = smtp_falso.instancias
    assert conexao.chamadas[0] == ("init", "smtp.exemplo.com", 2525, 15)
    assert ("login", "robo", "segredo") in conexao.chamadas
    assert conexao.mensagem["From"] == "pedidos@exemplo.com"


def test_config_smtp_porta_padrao_587_e_usuario_opcional(smtp_falso):
    ambiente = {"PEDIDOS_SMTP_HOST": "h", "PEDIDOS_SMTP_REMETENTE": "r@exemplo.com"}
    notificador = notificador_da_config({"EMAIL_MODE": "smtp"}, environ=ambiente)
    notificador.enviar("a@b.com", "x", "y")
    (conexao,) = smtp_falso.instancias
    assert conexao.chamadas[0][2] == 587
    assert "login" not in conexao.nomes


@pytest.mark.parametrize("faltando", ["PEDIDOS_SMTP_HOST", "PEDIDOS_SMTP_REMETENTE"])
def test_config_smtp_sem_variavel_obrigatoria_levanta_erro_claro(faltando):
    ambiente = {k: v for k, v in AMBIENTE_SMTP.items() if k != faltando}
    with pytest.raises(RuntimeError, match=faltando):
        notificador_da_config({"EMAIL_MODE": "smtp"}, environ=ambiente)


def test_config_smtp_variavel_vazia_conta_como_faltando():
    ambiente = dict(AMBIENTE_SMTP, PEDIDOS_SMTP_HOST="  ")
    with pytest.raises(RuntimeError, match="PEDIDOS_SMTP_HOST"):
        notificador_da_config({"EMAIL_MODE": "smtp"}, environ=ambiente)


def test_config_smtp_porta_invalida_levanta_erro_claro():
    ambiente = dict(AMBIENTE_SMTP, PEDIDOS_SMTP_PORTA="abc")
    with pytest.raises(RuntimeError, match="PEDIDOS_SMTP_PORTA"):
        notificador_da_config({"EMAIL_MODE": "smtp"}, environ=ambiente)


def test_config_erro_nao_revela_a_senha():
    ambiente = dict(AMBIENTE_SMTP, PEDIDOS_SMTP_HOST="")
    with pytest.raises(RuntimeError) as erro:
        notificador_da_config({"EMAIL_MODE": "smtp"}, environ=ambiente)
    assert "segredo" not in str(erro.value)


def test_config_le_o_ambiente_real_por_padrao(monkeypatch):
    for nome, valor in AMBIENTE_SMTP.items():
        monkeypatch.setenv(nome, valor)
    assert isinstance(notificador_da_config({"EMAIL_MODE": "smtp"}), NotificadorSmtp)

