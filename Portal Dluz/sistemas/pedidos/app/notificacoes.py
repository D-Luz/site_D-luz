"""Avisos no sistema e envio de e-mail (spec, seção 8).

Todo evento do pedido gera avisos na tabela ``avisos``. O e-mail é só um
segundo canal: ``enviar_pendentes`` lê os avisos ainda não enviados e entrega
cada um por um ``Notificador``. Falha no e-mail nunca bloqueia nada; o aviso
fica marcado como ``erro`` e é reenviado depois de uma espera, até
MAX_TENTATIVAS_EMAIL tentativas.
"""

import json
import logging
import os
import smtplib
import ssl
import threading
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Protocol, runtime_checkable

from flask import current_app

from . import workflow
from .db import agora_utc, get_db, transacao
from .formatos import formatar_brl, formatar_data

log = logging.getLogger(__name__)

# evento -> (o solicitante recebe?, perfis que também recebem)
REGRAS = {
    "criar": (True, ("gestor",)),
    "aprovar": (True, ("fiscal",)),
    "reprovar": (True, ()),
    "confirmar_envio": (False, ("fiscal",)),
    "informar_nf": (True, ()),
    "marcar_chegada": (True, ()),
    "arquivar": (False, ()),  # só encerra o pedido: ninguém precisa ser avisado
}

# evento -> frase curta que acompanha o título no assunto
ASSUNTOS = {
    "criar": "novo pedido registrado",
    "aprovar": "pedido aprovado",
    "reprovar": "pedido reprovado",
    "confirmar_envio": "envio confirmado",
    "informar_nf": "nota fiscal informada",
    "marcar_chegada": "chegada registrada",
}

# (evento, papel do destinatário) -> texto do aviso. Os {campos} vêm de _montar_corpo.
TEXTOS = {
    ("criar", "solicitante"): "Seu pedido foi registrado e aguarda a decisão dos gestores.",
    ("criar", "gestor"): "Há um novo pedido aguardando a sua decisão: aprovar ou reprovar.",
    ("aprovar", "solicitante"): (
        "Seu pedido foi aprovado. Confirme o envio do pedido no sistema para "
        "que o fiscal possa seguir com o faturamento."
    ),
    ("aprovar", "fiscal"): (
        "O pedido foi aprovado. Os dados e o PDF já estão disponíveis no sistema; "
        "aguarde o faturamento até o solicitante confirmar o envio."
    ),
    ("reprovar", "solicitante"): "Seu pedido foi reprovado.\nMotivo: {motivo}",
    ("confirmar_envio", "fiscal"): (
        "O envio do pedido foi confirmado (data do pedido: {data_pedido}). "
        "Aguarde o faturamento e informe a nota fiscal quando ela for emitida."
    ),
    ("informar_nf", "solicitante"): "A nota fiscal {numero_nf} foi informada para o seu pedido.",
    ("marcar_chegada", "solicitante"): (
        "A chegada do pedido foi registrada em {data_chegada}. O pedido está concluído."
    ),
}

SEM_MOTIVO = "Sem motivo registrado."

# Teto de e-mails por passada (consulta); a chamada repete as passadas enquanto houver trabalho.
LOTE_MAXIMO = 50
# Um aviso em 'erro' volta a ser tentado depois de ESPERA_EMAIL_MIN x tentativas
# minutos (15, 30, 45, 60). Depois da 5ª falha ele fica em 'erro' para sempre:
# um endereço errado ou uma conta bloqueada não ficam sendo tentados sem fim (o
# aviso continua na caixa de avisos do sistema).
MAX_TENTATIVAS_EMAIL = 5
ESPERA_EMAIL_MIN = 15
# Só uma entrega por vez no processo: duas threads não enviam o mesmo aviso duas vezes.
_trava_envio = threading.Lock()
# Marcado por quem foi barrado pela trava: quem envia deve consultar de novo.
_ha_trabalho = threading.Event()


def _agora():
    """Relógio das tentativas de e-mail; os testes o substituem para mover o tempo."""
    return datetime.now(timezone.utc)


def _regra(evento):
    try:
        return REGRAS[evento]
    except KeyError:
        raise ValueError(f"Evento desconhecido: {evento!r}") from None


def _destinos(db, evento, pedido):
    """[(id, nome, papel)] de quem recebe o aviso: solicitante primeiro."""
    com_solicitante, perfis = _regra(evento)
    destinos = []
    if com_solicitante:
        linha = db.execute(
            "SELECT id, nome FROM usuarios WHERE id = ? AND ativo = 1",
            (pedido["solicitante_id"],),
        ).fetchone()
        if linha:
            destinos.append((linha["id"], linha["nome"], "solicitante"))
    if perfis:
        marcas = ", ".join("?" for _ in perfis)
        linhas = db.execute(
            f"SELECT id, nome, perfil FROM usuarios WHERE ativo = 1 AND perfil IN ({marcas})"
            " ORDER BY id",
            perfis,
        ).fetchall()
        ja_incluidos = {d[0] for d in destinos}
        destinos += [(l["id"], l["nome"], l["perfil"]) for l in linhas if l["id"] not in ja_incluidos]
    return destinos


def destinatarios(db, evento, pedido):
    """Ids dos usuários ativos que recebem o aviso do evento (spec, seção 8)."""
    return [usuario_id for usuario_id, _, _ in _destinos(db, evento, pedido)]


def _motivo_da_reprovacao(db, pedido_id):
    linha = db.execute(
        "SELECT comentario FROM historico WHERE pedido_id = ? AND acao = 'reprovar'"
        " ORDER BY id DESC LIMIT 1",
        (pedido_id,),
    ).fetchone()
    comentario = (linha["comentario"] or "").strip() if linha else ""
    return comentario or SEM_MOTIVO


def _montar_corpo(db, evento, papel, nome, pedido):
    titulo = workflow.titulo_pedido(pedido["id"])
    campos = {
        "data_pedido": formatar_data(pedido["data_pedido"]),
        "data_chegada": formatar_data(pedido["data_chegada"]),
        "numero_nf": pedido["numero_nf"] or "(não informado)",
    }
    if evento == "reprovar":
        campos["motivo"] = _motivo_da_reprovacao(db, pedido["id"])
    link = f"{current_app.config['BASE_URL'].rstrip('/')}/pedidos/{pedido['id']}"
    return (
        f"Olá, {nome}.\n\n"
        f"{TEXTOS[(evento, papel)].format(**campos)}\n\n"
        f"Pedido: {titulo}\n"
        f"Cliente: {pedido['cliente']}\n"
        f"Valor: {formatar_brl(pedido['valor_centavos'])}\n\n"
        f"Abrir pedido: {link}\n"
    )


def registrar_avisos(db, evento, pedido):
    """Grava um aviso por destinatário e devolve os ids criados.

    Entra na transação em andamento, se houver: os avisos nascem e somem junto
    com a mudança de estado que os provocou.
    """
    destinos = _destinos(db, evento, pedido)
    assunto = f"{workflow.titulo_pedido(pedido['id'])}: {ASSUNTOS[evento]}" if destinos else None
    email_status = "pendente" if current_app.config["EMAIL_MODE"] == "smtp" else "desligado"
    ids = []
    with transacao():
        for usuario_id, nome, papel in destinos:
            cursor = db.execute(
                "INSERT INTO avisos (usuario_id, pedido_id, assunto, corpo, email_status, criado_em)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    usuario_id,
                    pedido["id"],
                    assunto,
                    _montar_corpo(db, evento, papel, nome, pedido),
                    email_status,
                    agora_utc(),
                ),
            )
            ids.append(cursor.lastrowid)
    return ids


@runtime_checkable
class Notificador(Protocol):
    def enviar(self, destino: str, assunto: str, corpo: str) -> None: ...


class NotificadorDesligado:
    """Não envia nada: o aviso continua existindo só dentro do sistema."""

    def enviar(self, destino, assunto, corpo):
        return None


class NotificadorSmtp:
    """Envia por SMTP com STARTTLS. Uma conexão por mensagem."""

    TIMEOUT = 15

    def __init__(self, host, porta, usuario, senha, remetente):
        self.host = host
        self.porta = porta
        self.usuario = usuario
        self.senha = senha
        self.remetente = remetente

    def __repr__(self):
        return f"NotificadorSmtp(host={self.host!r}, porta={self.porta!r}, remetente={self.remetente!r})"

    def enviar(self, destino, assunto, corpo):
        # O EmailMessage recusa quebra de linha nos cabeçalhos (ValueError),
        # o que barra injeção de cabeçalhos antes de abrir a conexão.
        mensagem = EmailMessage()
        mensagem["From"] = self.remetente
        mensagem["To"] = destino
        mensagem["Subject"] = assunto
        mensagem.set_content(corpo, charset="utf-8")

        servidor = smtplib.SMTP(self.host, self.porta, timeout=self.TIMEOUT)
        try:
            servidor.starttls(context=ssl.create_default_context())
            if self.usuario:
                servidor.login(self.usuario, self.senha)
            servidor.send_message(mensagem)
        finally:
            try:
                servidor.quit()
            except (smtplib.SMTPException, OSError):
                servidor.close()


def _variavel(environ, nome, obrigatoria=False):
    valor = (environ.get(nome) or "").strip()
    if obrigatoria and not valor:
        raise RuntimeError(f"EMAIL_MODE=smtp exige a variável de ambiente {nome}.")
    return valor or None


def notificador_da_config(config, environ=None):
    """Notificador do modo configurado; as credenciais SMTP vêm do ambiente."""
    if config["EMAIL_MODE"] != "smtp":
        return NotificadorDesligado()
    environ = os.environ if environ is None else environ
    host = _variavel(environ, "PEDIDOS_SMTP_HOST", obrigatoria=True)
    remetente = _variavel(environ, "PEDIDOS_SMTP_REMETENTE", obrigatoria=True)
    porta = _variavel(environ, "PEDIDOS_SMTP_PORTA") or "587"
    try:
        porta = int(porta)
    except ValueError:
        raise RuntimeError("PEDIDOS_SMTP_PORTA deve ser um número inteiro.") from None
    return NotificadorSmtp(
        host,
        porta,
        _variavel(environ, "PEDIDOS_SMTP_USUARIO"),
        _variavel(environ, "PEDIDOS_SMTP_SENHA"),
        remetente,
    )


def enviar_pendentes(db, notificador):
    """Envia os avisos 'pendente' ou 'erro' e devolve quantos saíram nesta chamada.

    Quem pode sair: todo 'pendente'; um 'erro' só com menos de
    MAX_TENTATIVAS_EMAIL tentativas e passados ESPERA_EMAIL_MIN x tentativas
    minutos da última (ou sem hora registrada). Aviso de usuário inativo nunca
    sai (e fica como está). Cada tentativa soma 1 em email_tentativas e grava
    a hora em email_ultima_tentativa, dê certo ou não.

    Só uma chamada envia por vez (trava não bloqueante). Quem não consegue a
    trava marca `_ha_trabalho` e devolve 0 na hora: quem está enviando
    refaz a consulta e entrega o que chegou enquanto trabalhava, então nenhum
    aviso fica esperando outra ação do usuário.

    Cada passada lê no máximo LOTE_MAXIMO avisos ('pendente' antes de 'erro',
    para um endereço que sempre falha não ocupar todas as vagas) e a chamada
    repete a passada enquanto a anterior vier cheia ou alguém tiver pedido
    mais trabalho. Um aviso que falhou nesta chamada não é tentado de novo
    nela (ficará 'erro' até a próxima chamada), o que evita laço sem fim.

    O envio (rede) acontece fora de qualquer transação, para não segurar o
    bloqueio de escrita do SQLite; só o registro do resultado é transacional.
    Uma falha marca aquele aviso como 'erro' e segue para o próximo.
    """
    enviados = 0
    falhas = set()
    while True:
        if not _trava_envio.acquire(blocking=False):
            _ha_trabalho.set()
            return enviados
        try:
            enviados += _enviar_tudo(db, notificador, falhas)
        finally:
            _trava_envio.release()
        # Pedido que chegou entre a última consulta e a liberação da trava.
        if not _ha_trabalho.is_set():
            return enviados


def _enviar_tudo(db, notificador, falhas):
    """Passadas de até LOTE_MAXIMO avisos, com a trava já em mãos."""
    enviados = 0
    while True:
        # Limpa antes de consultar: pedido que chegar durante a passada vale outra.
        _ha_trabalho.clear()
        # datetime() dos dois lados: compara instantes, não o texto ISO com fuso.
        linhas = db.execute(
            "SELECT a.id, a.assunto, a.corpo, u.email"
            " FROM avisos a JOIN usuarios u ON u.id = a.usuario_id"
            " WHERE u.ativo = 1"
            " AND (a.email_status = 'pendente' OR (a.email_status = 'erro'"
            " AND a.email_tentativas < ? AND (a.email_ultima_tentativa IS NULL"
            " OR datetime(a.email_ultima_tentativa, '+' || (a.email_tentativas * ?) || ' minutes')"
            " <= datetime(?))))"
            " AND a.id NOT IN (SELECT value FROM json_each(?))"
            " ORDER BY (a.email_status = 'erro'), a.id LIMIT ?",
            (MAX_TENTATIVAS_EMAIL, ESPERA_EMAIL_MIN, _agora().isoformat(),
             json.dumps(sorted(falhas)), LOTE_MAXIMO),
        ).fetchall()
        if not linhas:
            return enviados
        for linha in linhas:
            try:
                notificador.enviar(linha["email"], linha["assunto"], linha["corpo"])
            except Exception as erro:
                resultado = "erro"
                falhas.add(linha["id"])
                log.warning("Falha ao enviar o aviso %s: %s: %s", linha["id"], type(erro).__name__, erro)
            else:
                resultado = "enviado"
                enviados += 1
            tentativa_em = _agora().replace(microsecond=0).isoformat()
            with transacao():
                db.execute(
                    "UPDATE avisos SET email_status = ?, email_tentativas = email_tentativas + 1,"
                    " email_ultima_tentativa = ? WHERE id = ?",
                    (resultado, tentativa_em, linha["id"]),
                )
        if len(linhas) < LOTE_MAXIMO and not _ha_trabalho.is_set():
            return enviados


def disparar_envio_em_segundo_plano(app):
    """Envia os e-mails pendentes numa thread, sem atrasar a resposta ao usuário.

    Só age com EMAIL_MODE=smtp (senão devolve None). Qualquer falha é
    registrada no log, sem credenciais, e engolida: e-mail nunca derruba nada.
    """
    if app.config["EMAIL_MODE"] != "smtp":
        return None

    def trabalho():
        try:
            with app.app_context():
                notificador = notificador_da_config(app.config)
                enviar_pendentes(get_db(), notificador)
        except Exception as erro:
            log.warning("Falha no envio de e-mails em segundo plano: %s: %s", type(erro).__name__, erro)

    fio = threading.Thread(target=trabalho, name="envio-de-avisos", daemon=True)
    fio.start()
    return fio
