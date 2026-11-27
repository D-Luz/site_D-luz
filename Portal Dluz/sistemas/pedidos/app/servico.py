"""Serviço de pedidos: criação e transições de estado (spec, seções 4, 5 e 8).

Toda mudança de estado acontece numa única transação que reúne a atualização
do pedido, a linha do histórico e os avisos. A decisão do gestor é uma
atualização condicionada ao status esperado: se dois gestores agirem juntos,
só um altera o pedido e o outro recebe ``JaDecidido``.

As mensagens das exceções já estão em português, prontas para a tela.
"""

import re
import unicodedata
from contextlib import suppress
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import arquivos, notificacoes, workflow
from .db import agora_utc, transacao
from .formatos import formatar_instante

FUSO = ZoneInfo("America/Sao_Paulo")
ANO_MINIMO = 2000
VALOR_MAXIMO_CENTAVOS = 99_999_999_999  # R$ 999.999.999,99
CLIENTE_MAX = 200
DESCRICAO_MAX = 2000
COMENTARIO_MAX = 1000
NF_MAX = 30
MSG_REENVIO_INVALIDO = "O pedido anterior não pode ser usado como reenvio."

# Valor em reais: milhares com ponto (grupos de três) e centavos com vírgula.
_VALOR = re.compile(r"(?P<reais>[1-9][0-9]{0,2}(?:\.[0-9]{3})+|[1-9][0-9]*|0)(?:,(?P<centavos>[0-9]{1,2}))?")
_PONTO_DECIMAL = re.compile(r"[0-9]+\.[0-9]{1,2}")
_DATA_ISO = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_DATA_BR = re.compile(r"([0-9]{2})/([0-9]{2})/([0-9]{4})")

# ação -> coluna de pedidos que ela preenche (nomes fixos, nunca vindos do usuário)
CAMPO_DA_ACAO = {
    "confirmar_envio": "data_pedido",
    "informar_nf": "numero_nf",
    "marcar_chegada": "data_chegada",
}


class ValorInvalido(ValueError):
    """Valor em reais ilegível, zero, negativo ou grande demais."""


class DataInvalida(ValueError):
    """Data ilegível, inexistente, anterior a 2000 ou futura."""


class DadosInvalidos(ValueError):
    """Qualquer outro dado recusado (motivo, nota fiscal, cliente, descrição...)."""


class PedidoNaoEncontrado(LookupError):
    """Não existe pedido com o id informado."""


class JaDecidido(Exception):
    """Outro gestor decidiu antes; ``decisor`` é o nome e ``quando`` o instante ISO UTC."""

    def __init__(self, decisor, quando):
        self.decisor = decisor
        self.quando = quando
        super().__init__(f"Este pedido já foi decidido por {decisor} em {formatar_instante(quando)}.")


# --- relógio e conversões ----------------------------------------------------


def hoje_sp():
    """Data de hoje em São Paulo: o relógio de todas as validações de data."""
    return datetime.now(FUSO).date()


def parse_valor_brl(texto):
    """'R$ 1.234,56' -> 123456 (centavos, sempre inteiros).

    Aceita ponto só como separador de milhares (grupos de três) e vírgula como
    decimal, com 1 ou 2 dígitos. Ponto decimal ('12.5') é recusado por ser
    ambíguo com milhares.
    """
    if not isinstance(texto, str):
        raise ValorInvalido("Informe o valor do pedido.")
    bruto = texto.strip()
    if bruto[:2].upper() == "R$":
        bruto = bruto[2:].strip()
    if not bruto:
        raise ValorInvalido("Informe o valor do pedido.")
    if bruto.startswith("-"):
        raise ValorInvalido("O valor do pedido não pode ser negativo.")
    if _PONTO_DECIMAL.fullmatch(bruto):
        raise ValorInvalido(
            "Use vírgula para separar os centavos (ex.: 12,50); o ponto é só para milhares."
        )
    partes = _VALOR.fullmatch(bruto) if len(bruto) <= 30 else None
    if partes is None:
        raise ValorInvalido("Valor inválido. Use o formato 1.234,56 (até 2 casas depois da vírgula).")
    reais = int(partes["reais"].replace(".", ""))
    centavos = int((partes["centavos"] or "").ljust(2, "0"))
    total = reais * 100 + centavos
    if total == 0:
        raise ValorInvalido("O valor do pedido deve ser maior que zero.")
    if total > VALOR_MAXIMO_CENTAVOS:
        raise ValorInvalido("O valor do pedido não pode passar de R$ 999.999.999,99.")
    return total


def parse_data(texto, *, permitir_futuro=False):
    """'2026-10-23' ou '23/10/2026' -> date. Recusa futuro (salvo permitir_futuro) e anos < 2000."""
    if not isinstance(texto, str) or not texto.strip():
        raise DataInvalida("Informe a data.")
    bruto = texto.strip()
    if (iso := _DATA_ISO.fullmatch(bruto)) is not None:
        ano, mes, dia = (int(g) for g in iso.groups())
    elif (br := _DATA_BR.fullmatch(bruto)) is not None:
        dia, mes, ano = (int(g) for g in br.groups())
    else:
        raise DataInvalida("Data inválida. Use o formato dd/mm/aaaa.")
    try:
        data = date(ano, mes, dia)
    except ValueError:
        raise DataInvalida("Essa data não existe no calendário.") from None
    if ano < ANO_MINIMO:
        raise DataInvalida(f"A data não pode ser anterior ao ano {ANO_MINIMO}.")
    if not permitir_futuro and data > hoje_sp():
        raise DataInvalida("A data não pode ser futura.")
    return data


# --- validação de textos -----------------------------------------------------


def _texto(valor, rotulo, *, maximo, vazio=None, varias_linhas=False):
    """Texto aparado, sem caracteres de controle, de até ``maximo`` caracteres.

    ``vazio`` é a mensagem para texto em branco; sem ela, o campo é opcional.

    Quebras de linha de navegador (CRLF, CR) viram LF. Só campos de várias
    linhas aceitam LF e tab; nos demais qualquer controle é recusado.
    """
    if valor is None:
        valor = ""
    if not isinstance(valor, str):
        raise DadosInvalidos(f"{rotulo} inválido.")
    if varias_linhas:
        valor = valor.replace("\r\n", "\n").replace("\r", "\n")
    valor = valor.strip()
    permitidos = "\n\t" if varias_linhas else ""
    if any(unicodedata.category(c) == "Cc" and c not in permitidos for c in valor):
        raise DadosInvalidos(f"{rotulo} contém caracteres inválidos.")
    if vazio and not valor:
        raise DadosInvalidos(vazio)
    if len(valor) > maximo:
        raise DadosInvalidos(f"{rotulo} deve ter no máximo {maximo} caracteres.")
    return valor


# --- criação -----------------------------------------------------------------


def pedido_para_reenvio(db, usuario_id, pedido_id):
    """O pedido que o solicitante `usuario_id` pode reenviar, ou None.

    Vale o pedido reprovado dele, inclusive depois de arquivado: arquivar não
    desfaz a reprovação. Para o arquivado, decide a última decisão do
    histórico (aprovar ou reprovar, maior id).
    """
    return db.execute(
        "SELECT p.* FROM pedidos p WHERE p.id = ? AND p.solicitante_id = ?"
        " AND (p.status = 'reprovado' OR (p.status = 'arquivado' AND ("
        "SELECT h.acao FROM historico h WHERE h.pedido_id = p.id"
        " AND h.acao IN ('aprovar', 'reprovar') ORDER BY h.id DESC LIMIT 1) = 'reprovar'))",
        (pedido_id, usuario_id),
    ).fetchone()


def _validar_reenvio(db, usuario, reenvio_de):
    if reenvio_de is None:
        return
    if isinstance(reenvio_de, bool) or not isinstance(reenvio_de, int):
        raise DadosInvalidos("Pedido anterior inválido.")
    # Uma só mensagem para os três motivos: inexistente, de outro solicitante ou
    # não reprovado. Diferenciá-los revelaria a quem não pode ver o pedido que ele existe.
    if pedido_para_reenvio(db, usuario["id"], reenvio_de) is None:
        raise DadosInvalidos(MSG_REENVIO_INVALIDO)


def criar_pedido(db, usuario, cliente, valor, descricao, arquivo, reenvio_de=None):
    """Registra um pedido pendente e devolve o id.

    Valida tudo antes de tocar no disco ou no banco. Se a transação falhar, o
    PDF recém-gravado é apagado.
    """
    if usuario["perfil"] != "solicitante":
        raise workflow.NaoAutorizado("Só o solicitante pode criar pedidos.")
    cliente = _texto(cliente, "Cliente", maximo=CLIENTE_MAX, vazio="Informe o cliente.")
    descricao = _texto(
        descricao, "Descrição", maximo=DESCRICAO_MAX, vazio="Informe a descrição.", varias_linhas=True
    )
    centavos = parse_valor_brl(valor)
    _validar_reenvio(db, usuario, reenvio_de)

    nome_gerado, nome_original = arquivos.salvar_pdf(arquivo)
    try:
        with transacao():
            agora = agora_utc()
            pedido_id = db.execute(
                "INSERT INTO pedidos (cliente, valor_centavos, descricao, pdf_arquivo,"
                " pdf_nome_original, status, solicitante_id, reenvio_de, criado_em, atualizado_em)"
                " VALUES (?, ?, ?, ?, ?, 'pendente', ?, ?, ?, ?)",
                (cliente, centavos, descricao, nome_gerado, nome_original,
                 usuario["id"], reenvio_de, agora, agora),
            ).lastrowid
            db.execute(
                "INSERT INTO historico (pedido_id, usuario_id, acao, status_anterior,"
                " status_novo, comentario, criado_em)"
                " VALUES (?, ?, 'criar', NULL, 'pendente', NULL, ?)",
                (pedido_id, usuario["id"], agora),
            )
            novo = db.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
            notificacoes.registrar_avisos(db, "criar", novo)
    except BaseException:
        # Fora do bloco da transação (que já fez o rollback): some com o PDF órfão.
        with suppress(OSError):
            arquivos.caminho_pdf(nome_gerado).unlink(missing_ok=True)
        raise
    return pedido_id


# --- transições --------------------------------------------------------------


def _carregar(db, pedido_id):
    pedido = db.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if pedido is None:
        raise PedidoNaoEncontrado(f"Pedido {pedido_id} não encontrado.")
    return pedido


def _ja_decidido(db, pedido_id):
    """JaDecidido com a decisão mais recente do pedido, ou None se não houver."""
    linha = db.execute(
        "SELECT u.nome, h.criado_em FROM historico h JOIN usuarios u ON u.id = h.usuario_id"
        " WHERE h.pedido_id = ? AND h.acao IN ('aprovar', 'reprovar')"
        " ORDER BY h.id DESC LIMIT 1",
        (pedido_id,),
    ).fetchone()
    return JaDecidido(linha["nome"], linha["criado_em"]) if linha else None


def _validar_dados(acao, dados):
    """Devolve (comentario ou None, valor do campo da ação ou None); levanta se algo for inválido."""
    dados = dados or {}
    comentario = _texto(dados.get("comentario"), "Comentário", maximo=COMENTARIO_MAX, varias_linhas=True)
    if acao == "reprovar" and not comentario:
        raise DadosInvalidos("Informe o motivo da reprovação.")

    valor = None
    if acao == "confirmar_envio":
        try:
            valor = parse_data(dados.get("data_pedido")).isoformat()
        except DataInvalida as erro:
            raise DataInvalida(f"Data do pedido: {erro}") from None
    elif acao == "informar_nf":
        valor = _texto(
            dados.get("numero_nf"), "Número da nota fiscal", maximo=NF_MAX, vazio="Informe o número da nota fiscal."
        )
    elif acao == "marcar_chegada":
        bruto = dados.get("data_chegada")
        if bruto is None or (isinstance(bruto, str) and not bruto.strip()):
            valor = hoje_sp().isoformat()
        else:
            try:
                valor = parse_data(bruto).isoformat()
            except DataInvalida as erro:
                raise DataInvalida(f"Data de chegada: {erro}") from None
    return comentario or None, valor


def aplicar_acao(db, pedido_id, acao, usuario, dados):
    """Executa uma ação sobre o pedido e devolve a linha atualizada.

    Ordem: carrega o pedido, pergunta ao workflow se a ação é permitida,
    valida os dados e só então abre a transação, que atualiza o pedido
    (condicionado ao status lido), grava o histórico e registra os avisos.
    """
    pedido = _carregar(db, pedido_id)
    eh_dono = pedido["solicitante_id"] == usuario["id"]
    try:
        novo_status = workflow.proximo_status(pedido["status"], acao, usuario["perfil"], eh_dono)
    except workflow.TransicaoInvalida:
        # Quem decide (gestor ou administrador) e chega depois da decisão de
        # outro: diz quem decidiu, em vez de um erro genérico de transição.
        if acao in ("aprovar", "reprovar") and usuario["perfil"] in ("gestor", "admin"):
            ja_decidido = _ja_decidido(db, pedido_id)
            if ja_decidido is not None:
                raise ja_decidido from None
        raise
    comentario, valor = _validar_dados(acao, dados)

    campo = CAMPO_DA_ACAO.get(acao)
    with transacao():
        agora = agora_utc()
        atribuicao = f", {campo} = ?" if campo else ""
        parametros = [novo_status, agora] + ([valor] if campo else [])
        cursor = db.execute(
            f"UPDATE pedidos SET status = ?, atualizado_em = ?{atribuicao} WHERE id = ? AND status = ?",
            (*parametros, pedido_id, pedido["status"]),
        )
        if cursor.rowcount == 0:
            # Outro processo mudou o pedido entre a leitura e a gravação.
            atual = _carregar(db, pedido_id)
            if acao in ("aprovar", "reprovar") and atual["status"] != "pendente":
                ja_decidido = _ja_decidido(db, pedido_id)
                if ja_decidido is not None:
                    raise ja_decidido
            raise workflow.TransicaoInvalida(
                f"O pedido mudou para '{atual['status']}' enquanto a ação '{acao}' era processada."
            )
        db.execute(
            "INSERT INTO historico (pedido_id, usuario_id, acao, status_anterior,"
            " status_novo, comentario, criado_em) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pedido_id, usuario["id"], acao, pedido["status"], novo_status, comentario, agora),
        )
        atualizado = _carregar(db, pedido_id)
        notificacoes.registrar_avisos(db, acao, atualizado)
    return atualizado
