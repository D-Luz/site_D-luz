"""Telas: caixa do dia, encerramento, impressão e histórico."""

from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import caixa as svc
from . import conferencia, config, db, erp
from .auth import empresas_permitidas, exige, pode
from .formatos import ValorInvalido

bp = Blueprint("rotas", __name__)


def _hoje():
    return date.today().isoformat()


def _empresa_valida(empresa):
    if empresa not in config.EMPRESAS:
        abort(404)
    if empresa not in empresas_permitidas():
        abort(403)
    return empresa


def _caixa(caixa_id):
    cx = svc.por_id(db.get_db(), caixa_id)
    if cx is None:
        abort(404)
    _empresa_valida(cx["empresa"])
    return cx


def _url_dia(cx):
    return url_for("rotas.dia", empresa=cx["empresa"], data=cx["data"])


def _executar(funcao, *args):
    """Roda uma operação do caixa e transforma recusas em mensagem na tela."""
    try:
        funcao(db.get_db(), *args)
        return True
    except (svc.ErroCaixa, ValorInvalido) as erro:
        flash(str(erro), "erro")
        return False


def _leitura(cx, forcar=False):
    """Partes do ATS: ao vivo com o caixa aberto, a foto do fechamento depois."""
    foto = svc.snapshot(cx)
    if foto is not None:
        return foto, None
    try:
        return erp.ler_dia(cx["empresa"], cx["data"], forcar=forcar), None
    except erp.ErroATS as erro:
        return {"partes": [], "a_prazo": 0, "pdvs": 0, "lido_em": None}, str(erro)


def _contexto(cx, forcar=False):
    conn = db.get_db()
    lancs = svc.lancamentos(conn, cx["id"])
    leitura, erro_ats = _leitura(cx, forcar)
    res = conferencia.resumo(lancs, leitura["partes"], cx["saldo_inicial"], cx["saldo_contado"])
    nomes = {u["id"]: u["nome"] for u in conn.execute("SELECT id, nome FROM usuario")}
    return {
        "cx": cx,
        "empresa": cx["empresa"],
        "data": cx["data"],
        "registro": svc.historico(conn, cx["id"]),
        "lancs": lancs,
        "leitura": leitura,
        "erro_ats": erro_ats,
        "res": res,
        "por_lanc": res["conciliacao"]["por_lanc"],
        "nomes": nomes,
        "editando": request.args.get("editar", type=int),
    }


@bp.route("/")
def inicio():
    return redirect(url_for("rotas.dia", empresa=empresas_permitidas()[0], data=_hoje()))


@bp.route("/caixa/<empresa>/<data>")
def dia(empresa, data):
    _empresa_valida(empresa)
    try:
        date.fromisoformat(data)
    except ValueError:
        abort(404)
    conn = db.get_db()
    cx = svc.obter(conn, empresa, data)
    if cx is None:
        return render_template(
            "abrir.html",
            empresa=empresa,
            data=data,
            futuro=data > _hoje(),
            saldo=svc.saldo_sugerido(conn, empresa, data),
        )
    return render_template("caixa.html", **_contexto(cx))


@bp.route("/ir", methods=["GET"])
def ir():
    """Formulário de troca de empresa/data no topo da tela."""
    empresa = request.args.get("empresa") or empresas_permitidas()[0]
    data = request.args.get("data") or _hoje()
    return redirect(url_for("rotas.dia", empresa=empresa, data=data))


@bp.route("/caixa/abrir", methods=["POST"])
@exige("lancar")
def abrir():
    empresa = _empresa_valida(request.form.get("empresa"))
    data = request.form.get("data", "")
    try:
        date.fromisoformat(data)
    except ValueError:
        abort(400)
    if data > _hoje():
        flash("Não dá para abrir o caixa de um dia que ainda não chegou.", "erro")
    else:
        _executar(svc.abrir, empresa, data, request.form.get("saldo_inicial"), g.usuario)
    return redirect(url_for("rotas.dia", empresa=empresa, data=data))


@bp.route("/caixa/<int:caixa_id>/lancar", methods=["POST"])
@exige("lancar")
def lancar(caixa_id):
    cx = _caixa(caixa_id)
    f = request.form
    if not _executar(svc.lancar, cx, f.get("forma"), f.get("valor"), f.get("obs"), g.usuario):
        # Devolve o que foi digitado para não perder a linha.
        return redirect(
            url_for("rotas.dia", empresa=cx["empresa"], data=cx["data"], forma=f.get("forma"),
                    valor=f.get("valor"), obs=f.get("obs"))
        )
    return redirect(url_for("rotas.dia", empresa=cx["empresa"], data=cx["data"], forma=f.get("forma")))


@bp.route("/caixa/<int:caixa_id>/lanc/<int:lid>/editar", methods=["POST"])
@exige("lancar")
def editar(caixa_id, lid):
    cx = _caixa(caixa_id)
    f = request.form
    _executar(svc.editar, cx, lid, f.get("forma"), f.get("valor"), f.get("obs"), g.usuario)
    return redirect(_url_dia(cx))


@bp.route("/caixa/<int:caixa_id>/lanc/<int:lid>/excluir", methods=["POST"])
@exige("lancar")
def excluir(caixa_id, lid):
    cx = _caixa(caixa_id)
    if _executar(svc.excluir, cx, lid, g.usuario):
        flash("Lançamento excluído (continua no registro de alterações).", "ok")
    return redirect(_url_dia(cx))


@bp.route("/caixa/<int:caixa_id>/lanc/<int:lid>/aprovar", methods=["POST"])
@exige("aprovar")
def aprovar(caixa_id, lid):
    cx = _caixa(caixa_id)
    _executar(svc.aprovar, cx, lid, request.form.get("obs"), g.usuario)
    return redirect(request.form.get("voltar") or _url_dia(cx))


@bp.route("/caixa/<int:caixa_id>/saldo", methods=["POST"])
@exige("lancar")
def saldo(caixa_id):
    cx = _caixa(caixa_id)
    _executar(svc.alterar_saldo_inicial, cx, request.form.get("saldo_inicial"), g.usuario)
    return redirect(_url_dia(cx))


@bp.route("/caixa/<int:caixa_id>/ats", methods=["POST"])
def atualizar_ats(caixa_id):
    cx = _caixa(caixa_id)
    try:
        erp.ler_dia(cx["empresa"], cx["data"], forcar=True)
    except erp.ErroATS as erro:
        flash(str(erro), "erro")
    return redirect(request.form.get("voltar") or _url_dia(cx))


@bp.route("/caixa/<int:caixa_id>/encerrar")
def encerrar(caixa_id):
    cx = _caixa(caixa_id)
    if cx["status"] != "aberto":
        return redirect(_url_dia(cx))
    # O encerramento sempre lê o ATS de novo: alguns PDVs mudam de valor ao longo do dia.
    ctx = _contexto(cx, forcar=True)
    return render_template("encerrar.html", **ctx)


@bp.route("/caixa/<int:caixa_id>/vincular", methods=["POST"])
@exige("lancar")
def vincular(caixa_id):
    cx = _caixa(caixa_id)
    _executar(svc.vincular, cx, request.form.get("lanc", type=int), request.form.get("pdv"), g.usuario)
    return redirect(url_for("rotas.encerrar", caixa_id=cx["id"]))


@bp.route("/caixa/<int:caixa_id>/fechar", methods=["POST"])
@exige("fechar")
def fechar(caixa_id):
    cx = _caixa(caixa_id)
    try:
        leitura = erp.ler_dia(cx["empresa"], cx["data"], forcar=True)
    except erp.ErroATS as erro:
        flash(f"{erro} O dia só fecha com o ATS respondendo; tente de novo em instantes.", "erro")
        return redirect(url_for("rotas.encerrar", caixa_id=cx["id"]))
    f = request.form
    if not _executar(svc.fechar, cx, leitura, f.get("contado"), f.get("justificativa"), g.usuario):
        return redirect(url_for("rotas.encerrar", caixa_id=cx["id"], contado=f.get("contado"),
                                justificativa=f.get("justificativa")))
    flash("Caixa fechado. Imprima e anexe aos pedidos.", "ok")
    return redirect(url_for("rotas.imprimir", caixa_id=cx["id"]))


@bp.route("/caixa/<int:caixa_id>/imprimir")
def imprimir(caixa_id):
    cx = _caixa(caixa_id)
    return render_template("imprimir.html", **_contexto(cx))


@bp.route("/caixa/<int:caixa_id>/reabrir", methods=["POST"])
@exige("reabrir")
def reabrir(caixa_id):
    cx = _caixa(caixa_id)
    if _executar(svc.reabrir, cx, request.form.get("motivo"), g.usuario):
        flash("Caixa reaberto para correção.", "ok")
    return redirect(_url_dia(cx))


@bp.route("/caixa/<int:caixa_id>/conferir", methods=["POST"])
@exige("conferir")
def conferir(caixa_id):
    cx = _caixa(caixa_id)
    if _executar(svc.conferir, cx, request.form.get("obs"), g.usuario):
        flash("Conferência registrada.", "ok")
    return redirect(_url_dia(cx))


@bp.route("/historico")
def historico():
    conn = db.get_db()
    permitidas = empresas_permitidas()
    empresa = request.args.get("empresa") or ""
    status = request.args.get("status") or ""
    mes = request.args.get("mes") or _hoje()[:7]
    filtros, params = ["c.empresa IN (%s)" % ",".join("?" * len(permitidas))], list(permitidas)
    if empresa in permitidas:
        filtros.append("c.empresa = ?")
        params.append(empresa)
    if status in ("aberto", "fechado", "conferido"):
        filtros.append("c.status = ?")
        params.append(status)
    if mes:
        filtros.append("substr(c.data, 1, 7) = ?")
        params.append(mes)
    dias = conn.execute(
        "SELECT c.*, uf.nome AS fechado_nome, uc.nome AS conferido_nome FROM caixa c "
        "LEFT JOIN usuario uf ON uf.id = c.fechado_por LEFT JOIN usuario uc ON uc.id = c.conferido_por "
        f"WHERE {' AND '.join(filtros)} ORDER BY c.data DESC, c.empresa",
        params,
    ).fetchall()
    linhas = []
    for d in dias:
        lancs = svc.lancamentos(conn, d["id"])
        gav = conferencia.gaveta(d["saldo_inicial"], lancs, d["saldo_contado"])
        pendentes = sum(
            1 for l in lancs if config.FORMAS.get(l["forma"], {}).get("aprovacao") and not l["aprovado_por"]
        )
        linhas.append({"d": d, "gaveta": gav, "pendentes": pendentes,
                       "entradas": sum(l["valor"] for l in lancs if config.FORMAS[l["forma"]]["tipo"] == "entrada")})
    return render_template("historico.html", linhas=linhas, empresa=empresa, status=status, mes=mes)


ACOES = {
    "abrir": "Abriu o caixa",
    "lancar": "Lançou",
    "alterar": "Alterou",
    "excluir": "Excluiu",
    "saldo_inicial": "Saldo inicial",
    "vincular": "PDV ligado à mão",
    "aprovar": "Aprovou",
    "fechar": "Fechou o dia",
    "reabrir": "Reabriu",
    "conferir": "Conferiu",
}


@bp.app_context_processor
def globais():
    return {"pode": pode, "FORMAS": config.FORMAS, "EMPRESAS": config.EMPRESAS, "conferencia": conferencia,
            "empresas_permitidas": empresas_permitidas, "hoje": _hoje(), "ACOES": ACOES}
