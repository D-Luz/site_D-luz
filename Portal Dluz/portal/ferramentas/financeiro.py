"""Financeiro: contas a pagar e a receber, acompanhamento semanal e comparativo mensal."""

from datetime import date, datetime, timedelta

import pandas as pd

from ..tarefas import ErroAmigavel
from . import EMP_TODAS, TODAS, Campo, ferramenta, hoje, para_data, primeiro_do_mes
from ._comum import aviso_falhas, do_script, em_paralelo, juntar, na_pasta, siglas
from ._saida import pagina_tabela, salvar_excel


@ferramenta(
    "titulos",
    "Contas a pagar / a receber (por vencimento)",
    "financeiro",
    "Todos os títulos com vencimento no período, das três empresas, com valor, pago, saldo e situação. "
    "Filtros por empresa e situação, busca por nome.",
    [Campo("modo", "Tipo", "escolha", opcoes=[("pagar", "Contas a pagar"), ("receber", "Contas a receber")],
           padrao="pagar"),
     Campo("empresa", "Empresa", "empresa", opcoes=[(TODAS, "Todas as empresas"), *EMP_TODAS], padrao=TODAS),
     Campo("inicio", "Vencimento de", "data", padrao=primeiro_do_mes),
     Campo("fim", "Vencimento até", "data", padrao=hoje)],
    origem="Extracoes/Extracao_titulos_gui.py (substitui Extracao_cpagar.py e Extracao_creceber.py)",
)
def titulos(ctx):
    mod = ctx.script("Extracao_titulos_gui.py")
    modo = mod.MODOS.get(ctx.params["modo"])
    if modo is None:
        raise ErroAmigavel("Tipo inválido.")
    ini, fim = para_data(ctx.params["inicio"]), para_data(ctx.params["fim"])
    alvos = do_script(mod.EMPRESAS, siglas(ctx.params["empresa"], ["ELE", "COR", "ILU"]))
    resultados, falhas = em_paralelo(alvos, lambda a: mod.consultar_empresa(a[0], a[1], modo["sql"], ini, fim))
    df = juntar(resultados, falhas, "Nenhum título com vencimento no período.")
    chave = pd.to_datetime(df["VENCIMENTO"], format="%d/%m/%Y", errors="coerce")
    df = df.assign(_ord=chave).sort_values(["_ord", "EMPRESA", "NOME"]).drop(columns="_ord").reset_index(drop=True)
    prefixo = "Contas_Pagar" if ctx.params["modo"] == "pagar" else "Contas_Receber"
    base = f"{prefixo}_Unificado_{ini:%Y%m%d}_a_{fim:%Y%m%d}"
    totais = {c: "" for c in df.columns}
    totais["NOME"] = "TOTAIS"
    for c in mod.COLS_NUMERICAS:
        totais[c] = df[c].sum()
    salvar_excel(ctx.arquivo(base + ".xlsx"),
                 {modo["titulo"]: pd.concat([df, pd.DataFrame([totais])], ignore_index=True)},
                 moeda=mod.COLS_NUMERICAS)
    pagina_tabela(ctx.arquivo(base + ".html"), df, modo["titulo"],
                  f"Vencimento de {ini:%d/%m/%Y} a {fim:%d/%m/%Y} · coluna NOME = {modo['pessoa'].lower()}",
                  moeda=mod.COLS_NUMERICAS, somar=mod.COLS_NUMERICAS, filtros=["EMPRESA", "SITUAÇÃO"],
                  cartoes=[("Total dos títulos", "VALOR TÍTULO"), ("Total pago", "VALOR PAGO"), ("Saldo", "SALDO")])
    ctx.pagina = base + ".html"
    ctx.resumo = f"{len(df)} título(s)." + aviso_falhas(falhas)


@ferramenta(
    "receber-em-aberto",
    "Contas a receber em aberto",
    "financeiro",
    "Todos os títulos a receber em aberto ou pagos em parte, das três empresas, com vendedor e status "
    "(vencido, vence hoje, a vencer).",
    [],
    origem="Extracoes/extracao_contas_receber.py",
)
def receber_em_aberto(ctx):
    mod = ctx.script("extracao_contas_receber.py")
    with na_pasta(ctx.pasta):
        mod.extrair_contas_receber()
    gerados = sorted(ctx.pasta.glob("Contas_Receber_*.xlsx"))
    if not gerados:
        raise ErroAmigavel("Nenhum título retornado. Veja o registro abaixo.")
    df = pd.read_excel(gerados[-1])
    df = df[df["EMPRESA"] != "TOTAIS GERAIS --->"]
    nome = gerados[-1].stem + ".html"
    pagina_tabela(ctx.arquivo(nome), df, "Contas a receber em aberto", f"Posição de {date.today():%d/%m/%Y}",
                  moeda=["TÍTULO", "PAGO", "SALDO"], somar=["TÍTULO", "PAGO", "SALDO"],
                  filtros=["EMPRESA", "VENDEDOR", "STATUS", "SITUAÇÃO"],
                  cartoes=[("Total dos títulos", "TÍTULO"), ("Pago", "PAGO"), ("Saldo a receber", "SALDO")])
    ctx.pagina = nome
    ctx.resumo = f"{len(df)} título(s) em aberto."


@ferramenta(
    "acompanhamento-semanal",
    "Acompanhamento semanal",
    "financeiro",
    "Receita por forma de pagamento e liquidações nas três empresas, comparando semana anterior, mês e ano "
    "com o mesmo período do ano passado. Um Excel consolidado.",
    [Campo("data", "Considerar como hoje", "data", padrao=hoje,
           ajuda="Normalmente hoje. Mude para refazer o relatório de uma semana passada.")],
    origem="Extracoes/acompanhamento_semanal_ats.py",
)
def acompanhamento_semanal(ctx):
    mod = ctx.script("acompanhamento_semanal_ats.py")
    base = para_data(ctx.params["data"])
    periodos = mod.periodos_definidos(base)
    for rotulo, (ini, lim) in periodos.items():
        print(f"  {rotulo:<38} {ini:%d/%m/%Y} a {(lim - timedelta(days=1)):%d/%m/%Y}")
    print("Consultando as três empresas em paralelo...")
    resultados, falhas = mod.buscar_todas_empresas(periodos)
    if not resultados:
        raise ErroAmigavel("Não foi possível consultar nenhuma empresa: "
                           + "; ".join(f"{n} ({e})" for n, e in falhas))
    receita_por_empresa = {nome: mod.montar_receita(dados, periodos) for nome, dados in resultados.items()}
    consolidado = {
        chave: mod._concat_nonempty([dados[chave] for dados in resultados.values()])
        for chave in ("vendas_atual", "vendas_ano_ant", "liq_atual", "liq_ano_ant")
    }
    receita = mod.montar_receita(consolidado, periodos)
    ini_sem, lim_sem = periodos["A) Semana anterior"]
    caminho = ctx.arquivo(f"Acompanhamento_semanal_{ini_sem:%Y%m%d}_a_{(lim_sem - timedelta(days=1)):%Y%m%d}.xlsx")
    mod.exportar_excel(caminho, receita_por_empresa, receita, mod.liquidacoes_semana(resultados, periodos),
                       mod.liquidacoes_por_cliente(resultados, periodos), periodos)
    total = receita.loc[receita["FORMA_PAGAMENTO"] == "9. RECEITA TOTAL"]
    linhas = []
    for _, r in total.iterrows():
        valor = f"{r['VALOR']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        linhas.append(f"{r['PERIODO']}: R$ {valor}")
        print(f"  {r['PERIODO']:<38} R$ {valor}")
    ctx.resumo = "Receita total — " + " · ".join(linhas) + aviso_falhas(list(falhas))


@ferramenta(
    "receita-mensal-comparativo",
    "Receita mensal: ano passado × este ano",
    "financeiro",
    "Subtotal à vista, a prazo, receita total, liquidações e recebimentos à vista, mês a mês, comparando com o "
    "ano anterior. Excel com aba consolidada, uma por empresa, gráficos e notas.",
    [],
    origem="Relatorio via site/relatorio_mensal_comparativo.py",
    detalhes="Usa as mesmas consultas e regras do Painel ATS, então os números batem com ele.",
)
def receita_mensal(ctx):
    mod = ctx.script("relatorio_mensal_comparativo.py")
    from .. import erp

    erp.aplicar_no_script(mod.dash, ctx.empresas)  # o script carrega o painel por conta própria
    mod.ARQUIVO_SAIDA = str(ctx.arquivo(f"Receita_mensal_comparativo_{datetime.now():%Y%m%d}.xlsx"))
    mod.main()
