"""Proposta de orçamento em PDF."""

import json
from datetime import datetime

from ..tarefas import ErroAmigavel
from . import Campo, ferramenta

MAX_ITENS = 500


def _numero(texto, padrao=0.0):
    return texto if texto not in (None, "") else padrao


@ferramenta(
    "proposta-orcamento",
    "Proposta de orçamento (PDF)",
    "vendas",
    "Monta a proposta comercial em PDF, por ambiente, com produtos e serviços, condições de pagamento "
    "(à vista com desconto ou valor fechado, parcelado com e sem desconto) e a identidade Grupo Dluz.",
    [Campo("cliente", "Cliente"),
     Campo("vendedor", "Vendedor"),
     Campo("contato", "Contato", obrigatorio=False),
     Campo("data", "Data", padrao=lambda: datetime.now().strftime("%d/%m/%Y")),
     Campo("itens", "Itens", "itens"),
     Campo("avista", "À vista", "marcar", obrigatorio=False, padrao="1"),
     Campo("avista_modo", "Forma do à vista", "escolha", obrigatorio=False, padrao="desconto",
           opcoes=[("desconto", "Desconto (% ou R$)"), ("valor_final", "Valor final (R$)")]),
     Campo("desconto_avista", "Desconto à vista", obrigatorio=False, ajuda="Ex.: 5% ou 150,00"),
     Campo("valor_avista", "Valor final à vista (R$)", obrigatorio=False),
     Campo("parcelado_desconto", "Parcelado com desconto", "marcar", obrigatorio=False),
     Campo("desconto_parcelado", "Desconto no parcelado", obrigatorio=False),
     Campo("parcelas_desconto", "Parcelas (com desconto)", "numero", obrigatorio=False, padrao="3"),
     Campo("parcelado", "Parcelado sem desconto", "marcar", obrigatorio=False),
     Campo("parcelas", "Parcelas (sem desconto)", "numero", obrigatorio=False, padrao="10")],
    origem="Orcamentos/Orcamento_Final_v7.py (Painel de Orçamentos)",
    detalhes="O projeto também sai em .json: use “Carregar projeto” para reabrir e editar depois.",
)
def proposta_orcamento(ctx):
    mod = ctx.script("painel_orcamento.py")
    p = ctx.params
    try:
        brutos = json.loads(p.get("itens") or "[]")
    except json.JSONDecodeError:
        raise ErroAmigavel("Lista de itens inválida.") from None
    if not isinstance(brutos, list) or not brutos:
        raise ErroAmigavel("Inclua pelo menos um item no orçamento.")
    if len(brutos) > MAX_ITENS:
        raise ErroAmigavel(f"Máximo de {MAX_ITENS} itens por orçamento.")
    try:
        itens = [mod.BudgetItem.from_dict(i) for i in brutos if isinstance(i, dict)]
        pag = mod.PaymentCondition(
            incluir_avista=bool(p.get("avista")),
            incluir_desconto_parcelas=bool(p.get("parcelado_desconto")),
            incluir_parcelas_sem_desconto=bool(p.get("parcelado")),
            avista_modo=p.get("avista_modo") or mod.MODO_AVISTA_DESCONTO,
            desconto_avista_texto=p.get("desconto_avista") or "",
            valor_avista_texto=p.get("valor_avista") or "",
            desconto_parcelado_texto=p.get("desconto_parcelado") or "",
            parcelas_desconto=mod.PaymentCondition._normalizar_parcelas(_numero(p.get("parcelas_desconto"), 1)),
            parcelas_sem_desconto=mod.PaymentCondition._normalizar_parcelas(_numero(p.get("parcelas"), 1)),
        )
        pag.validar()
    except mod.NumeroInvalido as exc:
        raise ErroAmigavel(str(exc)) from None
    itens = [i for i in itens if i.ambiente and (i.p_desc or i.s_desc)]
    if not itens:
        raise ErroAmigavel("Cada item precisa de ambiente e de um produto ou serviço.")
    cliente = (p.get("cliente") or "").strip()
    base = f"Orcamento_{mod.limpar_nome_arquivo(cliente)}_{datetime.now():%d-%m-%Y_%H%M}"
    mod.BudgetPDFGenerator().gerar(cliente, p.get("vendedor", ""), p.get("contato", ""), p.get("data", ""),
                                   itens, pag, ctx.arquivo(base + ".pdf"))
    mod.ProjectRepository.salvar(ctx.arquivo(base + ".json"), cliente, p.get("vendedor", ""),
                                 p.get("contato", ""), p.get("data", ""), itens, pag)
    total = sum(i.total for i in itens)
    ctx.pagina = base + ".pdf"
    ctx.resumo = f"{len(itens)} item(ns), total bruto {mod.formatar_moeda(total, True)}."
