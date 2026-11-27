"""Painéis HTML do ATS. Antes eram publicados no Netlify; agora ficam no próprio portal."""

import shutil

from . import Campo, ferramenta
from ._comum import na_pasta

NOME_BASE = "base_gestao_separada.xlsx"

CAMPO_BASE = Campo(
    "base", "Base gestão separada (opcional)", "arquivo", obrigatorio=False, aceita=".xlsx",
    ajuda="Planilha manual com os lançamentos de fora do ATS. Envie uma vez: o portal guarda a última enviada.",
)


def _base_manual(ctx):
    """Caminho da 'Base gestão separada.xlsx': a enviada agora ou a última guardada."""
    guardada = ctx.data_dir / NOME_BASE
    enviadas = ctx.entradas("base")
    if enviadas:
        shutil.copyfile(enviadas[0], guardada)
        print("Base gestão separada atualizada.")
    if guardada.exists():
        return str(guardada)
    print("Sem a Base gestão separada: o painel sai só com os dados do ATS.")
    return str(ctx.pasta / "_sem_base.xlsx")  # caminho inexistente: o script segue sem a base


def _gerar(ctx, arquivo, nome_saida):
    mod = ctx.script(arquivo)
    mod.ARQUIVO_BASE_MANUAL = _base_manual(ctx)
    mod.publicar_no_netlify = lambda *a, **k: None  # publicação substituída pelo portal
    with na_pasta(ctx.pasta):
        gerado = mod.extrair_e_gerar_html()
    (ctx.pasta / gerado).rename(ctx.pasta / nome_saida)
    ctx.pagina = nome_saida


@ferramenta(
    "painel-ats",
    "Painel ATS — Grupo Dluz",
    "paineis",
    "O painel completo: contas a pagar, contas a receber (vencidos e a vencer, por cliente e vendedor) e dados "
    "gerais (receita por forma de pagamento e liquidações na semana, mês e ano, contra o ano anterior).",
    [CAMPO_BASE],
    origem="Relatorio via site/gerar_dashboard_ats-11.py",
    detalhes="O último painel gerado fica disponível para todos em Painéis, sem precisar gerar de novo. "
             "As senhas por aba do painel continuam valendo (configuráveis pelas variáveis DASH_SENHA_*).",
)
def painel_ats(ctx):
    _gerar(ctx, "gerar_dashboard_ats.py", "painel_ats.html")
    ctx.resumo = "Painel gerado."
