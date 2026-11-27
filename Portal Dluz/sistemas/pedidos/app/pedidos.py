"""Telas e rotas de pedidos e avisos (spec, seções 3, 4, 6, 8 e 9).

As rotas são finas: decidem quem pode ver o quê, leem o formulário, chamam o
serviço e traduzem as exceções dele em mensagens e códigos HTTP. O solicitante
só enxerga os próprios pedidos; o pedido de outro responde 404 (e não 403),
para não revelar que ele existe.
"""

import logging
import re
import sqlite3

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from flask_wtf.csrf import CSRFError

from . import arquivos, notificacoes, servico, workflow
from .arquivos import PdfInvalido
from .auth import login_required, perfil_required
from .db import get_db, transacao
from .formatos import ACAO_LABEL, STATUS_LABEL, formatar_brl, formatar_data, formatar_instante
from .servico import DadosInvalidos, DataInvalida, JaDecidido, PedidoNaoEncontrado, ValorInvalido

log = logging.getLogger(__name__)
bp = Blueprint("pedidos", __name__)

LIMITE_LISTA = 200
LIMITE_AVISOS = 100
MSG_ARQUIVO_NAO_GRAVADO = "Não foi possível gravar o arquivo. Tente novamente."
MSG_ACAO_INDISPONIVEL = "Esta ação não está mais disponível para este pedido."
MSG_CSRF = "Sua sessão expirou ou o formulário é inválido. Volte, recarregue a página e tente de novo."
MSG_METODO = "Método não permitido."
MSG_ERRO_INTERNO = "Algo deu errado do nosso lado. Tente de novo em alguns instantes."

# ação -> campos do formulário que ela lê (o resto do que vier é ignorado)
CAMPOS_DA_ACAO = {
    "aprovar": ("comentario",),
    "reprovar": ("comentario",),
    "confirmar_envio": ("data_pedido",),
    "informar_nf": ("numero_nf",),
    "marcar_chegada": ("data_chegada",),
    "arquivar": (),
}
CONFIRMACAO_DA_ACAO = {
    "aprovar": "Pedido aprovado.",
    "reprovar": "Pedido reprovado.",
    "confirmar_envio": "Envio confirmado.",
    "informar_nf": "Nota fiscal informada.",
    "marcar_chegada": "Chegada registrada.",
    "arquivar": "Pedido arquivado.",
}

_INTEIRO = re.compile(r"[0-9]{1,9}")


# --- configuração (filtros, contador de avisos, páginas de erro) --------------


def configurar(app):
    app.add_template_filter(formatar_brl, "brl")
    app.add_template_filter(formatar_data, "data")
    app.add_template_filter(formatar_instante, "instante")
    app.jinja_env.globals.update(
        status_label=STATUS_LABEL,
        acao_label=ACAO_LABEL,
        titulo_pedido=workflow.titulo_pedido,
    )
    app.context_processor(_contador_de_avisos)
    app.register_error_handler(403, lambda _erro: _pagina_erro(
        "Acesso negado", "Você não tem permissão para fazer isso.", 403))
    app.register_error_handler(404, lambda _erro: _pagina_erro(
        "Não encontrado", "Não encontramos o que você procurou.", 404))
    app.register_error_handler(413, lambda _erro: _pagina_erro(
        "Arquivo grande demais", f"Arquivo maior que {app.config['MAX_PDF_MB']} MB.", 413))
    app.register_error_handler(CSRFError, lambda _erro: _pagina_erro("Formulário inválido", MSG_CSRF, 400))
    app.register_error_handler(405, _metodo_nao_permitido)
    app.register_error_handler(500, _erro_interno)
    app.register_error_handler(sqlite3.OperationalError, _erro_do_banco)


def _contador_de_avisos():
    usuario = g.get("usuario")
    if usuario is None:
        return {}
    quantos = get_db().execute(
        "SELECT COUNT(*) FROM avisos WHERE usuario_id = ? AND lido = 0", (usuario["id"],)
    ).fetchone()[0]
    return {"avisos_nao_lidos": quantos}


def _pagina_erro(titulo, mensagem, status):
    return render_template("erro.html", titulo=titulo, mensagem=mensagem), status


def _pagina_erro_minima(status):
    """Página de erro que não consulta o banco nem depende de g.usuario.

    Renderiza direto pelo ambiente Jinja, sem os context processors (o contador
    de avisos consultaria o banco, que pode ser justamente o que falhou).
    """
    pagina = current_app.jinja_env.get_template("erro_minimo.html").render(mensagem=MSG_ERRO_INTERNO)
    return pagina, status


def _erro_interno(_erro):
    # Exceção não tratada: o Flask já a registrou no log (app.logger, com o
    # traceback e sem corpo nem cabeçalhos da requisição) antes de chegar aqui.
    return _pagina_erro_minima(500)


def _erro_do_banco(erro):
    """sqlite3.OperationalError: 503 se o banco está ocupado ("database is locked"), senão 500."""
    current_app.logger.exception("Erro do banco em %s %s", request.method, request.path)
    return _pagina_erro_minima(503 if "locked" in str(erro).lower() else 500)


def _metodo_nao_permitido(erro):
    pagina, status = _pagina_erro("Método não permitido", MSG_METODO, 405)
    # O cabeçalho Allow é obrigatório num 405; o tratador padrão o enviaria.
    return pagina, status, {"Allow": ", ".join(erro.valid_methods or ())}


# --- consultas e visibilidade --------------------------------------------------


def _escopo():
    """Trecho de SQL (sobre `p`, a tabela de pedidos) com o que o usuário pode ver."""
    if g.usuario["perfil"] == "solicitante":
        return "p.solicitante_id = ?", (g.usuario["id"],)
    return "1 = 1", ()


def _pedido_visivel(pedido_id):
    """O pedido com o nome do solicitante, ou 404 se não existe ou não é dele."""
    escopo, parametros = _escopo()
    pedido = get_db().execute(
        "SELECT p.*, u.nome AS solicitante_nome FROM pedidos p"
        " JOIN usuarios u ON u.id = p.solicitante_id"
        f" WHERE p.id = ? AND {escopo}",
        (pedido_id, *parametros),
    ).fetchone()
    if pedido is None:
        abort(404)
    return pedido


def _numero_do_pedido(texto):
    """'12' -> 12; vazio -> None; qualquer outra coisa é dado inválido."""
    texto = (texto or "").strip()
    if not texto:
        return None
    if not _INTEIRO.fullmatch(texto):
        raise DadosInvalidos("Pedido anterior inválido.")
    return int(texto)


def _reenvio_valido(texto):
    """O pedido reprovado (mesmo que arquivado depois) do próprio solicitante que `texto` aponta, ou None."""
    try:
        pedido_id = _numero_do_pedido(texto)
    except DadosInvalidos:
        return None
    if pedido_id is None:
        return None
    return servico.pedido_para_reenvio(get_db(), g.usuario["id"], pedido_id)


def _atalhos(perfil, contagens):
    """[(rótulo, status ou None, quantidade)] das listas rápidas de cada perfil."""
    em_andamento = sum(n for status, n in contagens.items() if status != "arquivado")
    if perfil in ("gestor", "admin"):  # o administrador também decide
        brutos = [("A decidir", "pendente", contagens.get("pendente", 0))]
    elif perfil == "fiscal":
        brutos = [
            ("Aguardando nota", "enviado", contagens.get("enviado", 0)),
            ("Aguardando chegada", "faturado", contagens.get("faturado", 0)),
        ]
    elif perfil == "solicitante":
        brutos = [
            ("Meus pedidos", None, em_andamento),
            ("Aguardando meu envio", "aprovado", contagens.get("aprovado", 0)),
        ]
    else:
        brutos = []
    return [
        {"rotulo": rotulo, "url": url_for("pedidos.lista", status=status), "quantidade": quantidade}
        for rotulo, status, quantidade in brutos
    ]


# --- lista ---------------------------------------------------------------------


@bp.get("/")
@login_required
def lista():
    db = get_db()
    escopo, parametros = _escopo()
    filtro = request.args.get("status")
    if filtro not in workflow.STATUS:
        filtro = None  # valor desconhecido: ignora o filtro, sem erro
    condicao, extra = ("p.status = ?", (filtro,)) if filtro else ("p.status != 'arquivado'", ())
    pedidos = db.execute(
        "SELECT p.id, p.cliente, p.valor_centavos, p.status, p.criado_em FROM pedidos p"
        f" WHERE {escopo} AND {condicao} ORDER BY p.id DESC LIMIT ?",
        (*parametros, *extra, LIMITE_LISTA),
    ).fetchall()
    contagens = {
        linha[0]: linha[1]
        for linha in db.execute(
            f"SELECT p.status, COUNT(*) FROM pedidos p WHERE {escopo} GROUP BY p.status", parametros
        )
    }
    return render_template(
        "lista.html",
        pedidos=pedidos,
        filtro=filtro,
        status_opcoes=workflow.STATUS,
        atalhos=_atalhos(g.usuario["perfil"], contagens),
        limite=LIMITE_LISTA,
    )


# --- novo pedido -----------------------------------------------------------------


def _formulario_novo(status=200, erro=None, valores=None, reenvio=None):
    if valores is None:
        valores = {"cliente": "", "valor": "", "descricao": ""}
        if reenvio is not None:
            valores = {
                "cliente": reenvio["cliente"],
                "valor": formatar_brl(reenvio["valor_centavos"]).removeprefix("R$ "),
                "descricao": reenvio["descricao"],
            }
    pagina = render_template("novo.html", erro=erro, valores=valores, reenvio=reenvio)
    return pagina, status


@bp.route("/pedidos/novo", methods=["GET", "POST"])
@perfil_required("solicitante")
def novo():
    if request.method == "GET":
        return _formulario_novo(reenvio=_reenvio_valido(request.args.get("reenvio_de")))

    form = request.form
    valores = {campo: form.get(campo, "") for campo in ("cliente", "valor", "descricao")}
    texto_reenvio = form.get("reenvio_de", "")
    try:
        pedido_id = servico.criar_pedido(
            get_db(), g.usuario, arquivo=request.files.get("arquivo"),
            reenvio_de=_numero_do_pedido(texto_reenvio), **valores,
        )
    except workflow.NaoAutorizado:
        abort(403)
    except (ValorInvalido, DadosInvalidos, PdfInvalido) as erro:
        return _formulario_novo(400, str(erro), valores, _reenvio_valido(texto_reenvio))
    except OSError:
        log.exception("Falha ao gravar o PDF de um novo pedido")
        return _formulario_novo(500, MSG_ARQUIVO_NAO_GRAVADO, valores, _reenvio_valido(texto_reenvio))

    flash("Pedido enviado para aprovação", "sucesso")
    _disparar_email()
    return redirect(url_for("pedidos.detalhe", pedido_id=pedido_id))


def _disparar_email():
    """Depois de uma mudança gravada: o envio roda em segundo plano, nunca na requisição."""
    notificacoes.disparar_envio_em_segundo_plano(current_app._get_current_object())


# --- detalhe e ações ---------------------------------------------------------------


def _pagina_do_pedido(pedido_id, erro=None, status=200):
    db = get_db()
    pedido = _pedido_visivel(pedido_id)
    historico = db.execute(
        "SELECT h.*, u.nome AS usuario_nome FROM historico h"
        " JOIN usuarios u ON u.id = h.usuario_id WHERE h.pedido_id = ? ORDER BY h.id",
        (pedido_id,),
    ).fetchall()
    eh_dono = pedido["solicitante_id"] == g.usuario["id"]
    decisoes = [h for h in historico if h["acao"] in ("aprovar", "reprovar")]
    reprovado = pedido["status"] == "reprovado" or (
        pedido["status"] == "arquivado" and decisoes and decisoes[-1]["acao"] == "reprovar"
    )
    motivo = None
    if reprovado:
        reprovacoes = [h["comentario"] for h in decisoes if h["acao"] == "reprovar"]
        motivo = (reprovacoes[-1] if reprovacoes else None) or "Sem motivo registrado."
    reenviavel = eh_dono and servico.pedido_para_reenvio(db, g.usuario["id"], pedido_id) is not None
    pagina = render_template(
        "detalhe.html",
        pedido=pedido,
        historico=historico,
        motivo=motivo,
        eh_dono=eh_dono,
        reenviavel=reenviavel,
        acoes=workflow.acoes_disponiveis(pedido["status"], g.usuario["perfil"], eh_dono),
        pdf_ok=arquivos.pdf_existe(pedido["pdf_arquivo"]),
        hoje=servico.hoje_sp().isoformat(),
        erro=erro,
    )
    return pagina, status


@bp.get("/pedidos/<int:pedido_id>")
@login_required
def detalhe(pedido_id):
    return _pagina_do_pedido(pedido_id)


@bp.post("/pedidos/<int:pedido_id>/<acao>")
@login_required
def executar_acao(pedido_id, acao):
    if acao not in workflow.ACOES:
        abort(404)
    _pedido_visivel(pedido_id)  # antes de tudo: quem não pode ver não descobre nada
    dados = {campo: request.form.get(campo) for campo in CAMPOS_DA_ACAO[acao]}
    try:
        servico.aplicar_acao(get_db(), pedido_id, acao, g.usuario, dados)
    except workflow.NaoAutorizado:
        abort(403)
    except PedidoNaoEncontrado:
        abort(404)
    except JaDecidido as erro:
        flash(str(erro), "erro")
    except workflow.TransicaoInvalida:
        flash(MSG_ACAO_INDISPONIVEL, "erro")
    except (ValorInvalido, DataInvalida, DadosInvalidos) as erro:
        return _pagina_do_pedido(pedido_id, erro=str(erro), status=400)
    else:
        flash(CONFIRMACAO_DA_ACAO[acao], "sucesso")
        _disparar_email()
    return redirect(url_for("pedidos.detalhe", pedido_id=pedido_id))


@bp.get("/pedidos/<int:pedido_id>/pdf")
@login_required
def pdf(pedido_id):
    pedido = _pedido_visivel(pedido_id)
    try:
        caminho = arquivos.caminho_pdf(pedido["pdf_arquivo"])
        resposta = send_file(
            caminho,
            mimetype="application/pdf",
            as_attachment=False,
            download_name=pedido["pdf_nome_original"],
        )
    except (PdfInvalido, OSError):
        return _pagina_erro("Arquivo não encontrado", "Arquivo PDF não encontrado.", 404)
    resposta.headers["Cache-Control"] = "private, no-store"
    return resposta


# --- avisos --------------------------------------------------------------------------


@bp.get("/avisos")
@login_required
def avisos():
    linhas = get_db().execute(
        "SELECT id, pedido_id, assunto, corpo, lido, criado_em FROM avisos"
        " WHERE usuario_id = ? ORDER BY id DESC LIMIT ?",
        (g.usuario["id"], LIMITE_AVISOS),
    ).fetchall()
    return render_template("avisos.html", avisos=linhas)


@bp.post("/avisos/<int:aviso_id>/lido")
@login_required
def aviso_lido(aviso_id):
    with transacao():
        cursor = get_db().execute(
            "UPDATE avisos SET lido = 1 WHERE id = ? AND usuario_id = ?", (aviso_id, g.usuario["id"])
        )
    if cursor.rowcount == 0:
        abort(404)
    return redirect(url_for("pedidos.avisos"))
