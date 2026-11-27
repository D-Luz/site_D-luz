"""Vendas e orçamentos."""

from datetime import date

import firebirdsql
import pandas as pd

from .. import db
from ..tarefas import ErroAmigavel
from . import EMP_ELE_ILU, EMP_TODAS, TODAS, Campo, ferramenta, hoje, para_data, primeiro_do_mes
from ._comum import aviso_falhas, do_script, em_paralelo, juntar, na_pasta, siglas
from ._saida import nome_arquivo, pagina_tabela, salvar_excel

PERIODO = [
    Campo("inicio", "De", "data", padrao=primeiro_do_mes),
    Campo("fim", "Até", "data", padrao=hoje),
]


ACOMPANHADOS = "__acompanhados__"
VENDEDORES_PADRAO = ("ANDRE RAMOS", "MAILSON", "MARCIEL", "MARCOS")  # os acompanhados do script original
CHAVE_VENDEDORES = "vendedores_conhecidos"


def _opcoes_vendedor():
    """Opções do campo Vendedor: todos, os acompanhados e cada nome já visto nos relatórios."""
    conhecidos = set(VENDEDORES_PADRAO)
    try:
        conhecidos |= set(db.ler_ajuste(db.get_db(), CHAVE_VENDEDORES, []) or [])
    except RuntimeError:  # fora de uma requisição (sem banco aberto)
        pass
    return [("", "Todos os vendedores"),
            (ACOMPANHADOS, "Só os acompanhados (André Ramos, Mailson, Marciel e Marcos)")] + [
        (n, n.title()) for n in sorted(conhecidos)]


def _lembrar_vendedores(ctx, nomes):
    """Guarda os vendedores que apareceram, para a lista do formulário."""
    novos = {n for n in nomes.dropna().astype(str).str.strip() if n}
    conn = db.conectar(ctx.data_dir / "portal.sqlite")
    try:
        atuais = set(db.ler_ajuste(conn, CHAVE_VENDEDORES, []) or [])
        if not novos <= atuais:
            db.gravar_ajuste(conn, CHAVE_VENDEDORES, sorted(atuais | novos))
    finally:
        conn.close()


def _periodo(ctx):
    ini, fim = para_data(ctx.params["inicio"]), para_data(ctx.params["fim"])
    if fim < ini:
        raise ErroAmigavel("A data final é anterior à inicial.")
    return ini, fim


@ferramenta(
    "orcamentos-baixados",
    "Orçamentos baixados e não baixados",
    "vendas",
    "Lista os orçamentos do período e mostra quais já viraram pedido de venda (e o número do pedido).",
    [Campo("empresa", "Empresa", "empresa", opcoes=EMP_ELE_ILU, padrao="ELE"), *PERIODO],
    origem="orcamentos_nao_baixados.py",
)
def orcamentos_baixados(ctx):
    mod = ctx.script("orcamentos_nao_baixados.py")
    ini, fim = _periodo(ctx)
    (_, cfg), = do_script(mod.EMPRESAS, siglas(ctx.params["empresa"], ["ELE", "ILU"]))
    print(f"Consultando {cfg['nome']} de {ini:%d/%m/%Y} a {fim:%d/%m/%Y}...")
    df = mod.buscar_orcamentos(cfg, ini, fim)
    if df.empty:
        raise ErroAmigavel("Nenhum orçamento encontrado para esse período.")
    baixados = int((df["STATUS"] == "Baixado").sum())
    nao = int((df["STATUS"] == "Não baixado").sum())
    base = nome_arquivo(f"orcamentos_{cfg['nome']}_{ini}_a_{fim}")
    salvar_excel(ctx.arquivo(base + ".xlsx"), {"Orçamentos": df})
    pagina_tabela(ctx.arquivo(base + ".html"), df, f"Orçamentos — {cfg['nome']}",
                  f"{ini:%d/%m/%Y} a {fim:%d/%m/%Y} · {baixados} baixado(s), {nao} não baixado(s)",
                  filtros=["STATUS"] + [c for c in df.columns if "VENDEDOR" in str(c).upper()][:1])
    ctx.pagina = base + ".html"
    ctx.resumo = f"{len(df)} orçamento(s): {baixados} baixado(s) e {nao} não baixado(s)."


@ferramenta(
    "orcamentos-vendedor",
    "Orçamentos por vendedor",
    "vendas",
    "Relatório gerencial em Excel: resumo por vendedor e detalhe dos orçamentos baixados e não baixados, "
    "com o % de desconto calculado.",
    [Campo("empresa", "Empresa", "empresa", opcoes=[*EMP_ELE_ILU, (TODAS, "Elétrica + Iluminação")], padrao=TODAS),
     Campo("vendedor", "Vendedor", "escolha", obrigatorio=False, padrao=ACOMPANHADOS, opcoes=lambda: _opcoes_vendedor(),
           ajuda="A lista cresce sozinha: cada relatório gerado acrescenta os vendedores que apareceram no período."),
     *PERIODO],
    origem="relatorio_orcamentos_vendedor.py",
)
def orcamentos_vendedor(ctx):
    mod = ctx.script("relatorio_orcamentos_vendedor.py")
    ini, fim = _periodo(ctx)
    alvos = do_script(mod.EMPRESAS, siglas(ctx.params["empresa"], ["ELE", "ILU"]))
    resultados, falhas = em_paralelo(alvos, lambda a: mod.buscar_orcamentos(a[0], a[1], ini, fim))
    df = juntar(resultados, falhas, "Nenhum orçamento encontrado para esse período.")
    total = len(df)
    df["VENDEDOR"] = df["VENDEDOR"].fillna("").str.strip()
    _lembrar_vendedores(ctx, df["VENDEDOR"])
    escolha = (ctx.params.get("vendedor") or "").strip()
    if escolha == ACOMPANHADOS:
        selecionado = df["VENDEDOR"].isin(mod.VENDEDORES_ACOMPANHADOS)
        quem = "dos vendedores acompanhados"
    elif escolha:
        selecionado = df["VENDEDOR"] == escolha
        quem = f"de {escolha}"
    else:
        selecionado = pd.Series(True, index=df.index)
        quem = "de todos os vendedores"
    ignorados = df[~selecionado]
    df, copias = mod.remover_copias_identicas(df[selecionado])
    if df.empty:
        raise ErroAmigavel(f"Nenhum orçamento {quem} nesse período.")
    # Com um vendedor só, listar todos os outros nas observações só polui o relatório.
    obs_ignorados = ignorados if escolha == ACOMPANHADOS else ignorados.iloc[0:0]
    wb = mod.montar_relatorio(df, ini, fim, mod.montar_observacoes(copias, obs_ignorados))
    sufixo = "" if escolha in ("", ACOMPANHADOS) else "_" + nome_arquivo(escolha)
    wb.save(ctx.arquivo(f"relatorio_orcamentos_vendedor{sufixo}_{ini}_a_{fim}.xlsx"))
    ctx.resumo = (f"{len(df)} orçamento(s) {quem} no relatório ({total} no período; {len(copias)} cópia(s) "
                  f"idêntica(s) descartada(s))." + aviso_falhas(falhas))


@ferramenta(
    "imprimir-orcamento",
    "Imprimir orçamento",
    "vendas",
    "Busca um orçamento pelo número e gera o documento no padrão Grupo Dluz, pronto para imprimir ou salvar em PDF.",
    [Campo("empresa", "Empresa", "empresa", opcoes=EMP_ELE_ILU, padrao="ELE"),
     Campo("numero", "Número do orçamento", "numero")],
    origem="Orcamento_imp NOVO.py",
)
def imprimir_orcamento(ctx):
    mod = ctx.script("orcamento_imp.py")
    sigla = siglas(ctx.params["empresa"], ["ELE", "ILU"])[0]
    (_, empresa), = do_script(mod.EMPRESAS, [sigla])
    try:
        numero = int(str(ctx.params["numero"]).strip())
    except ValueError:
        raise ErroAmigavel("Número inválido. Digite apenas dígitos.") from None
    con = firebirdsql.connect(host=empresa["host"], database=empresa["database"], port=mod.PORT,
                              user=mod.USER, password=mod.PASSWORD, charset=mod.CHARSET)
    try:
        numero_fmt = str(numero).zfill(8)
        cur = con.cursor()
        cur.execute(mod.SQL, (numero_fmt,))
        df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
        if df.empty:
            # Desde a atualização do ATS (set/2026) os orçamentos ficam em PEDIDOC/PEDIDOI.
            cur = con.cursor()
            cur.execute(mod.SQL_PEDIDO, (numero_fmt,))
            df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    finally:
        con.close()
    if df.empty:
        raise ErroAmigavel(f"Orçamento {numero} não encontrado na {empresa['nome']}.")
    html = mod.gerar_html(df, empresa["nome"], empresa.get("cnpj", ""))
    nome = f"orcamento_{numero}.html"
    ctx.arquivo(nome).write_text(html, encoding="utf-8")
    ctx.pagina = nome
    ctx.resumo = f"Orçamento {numero} — {empresa['nome']}. Use “Abrir em tela cheia” e imprima (Ctrl+P)."


@ferramenta(
    "venda-mensal",
    "Venda mensal por empresa",
    "vendas",
    "Soma a venda de PDV de cada mês desde 2021 nas três empresas e gera um Excel com gráfico de tendência.",
    [Campo("desde", "A partir de", "data", padrao="2021-01-01")],
    origem="venda_mensal_por_empresa.py",
)
def venda_mensal(ctx):
    mod = ctx.script("venda_mensal_por_empresa.py")
    mod.DATA_INICIO = para_data(ctx.params["desde"]).isoformat()
    with na_pasta(ctx.pasta):
        mod.main()


@ferramenta(
    "vendas-pdv",
    "Vendas item a item (PDV)",
    "vendas",
    "Cada item vendido no PDV no período, com desconto do pedido rateado entre os itens, vendedor, cliente e "
    "forma de pagamento. Filtros por empresa, vendedor e forma de pagamento.",
    [Campo("empresa", "Empresa", "empresa", opcoes=[(TODAS, "Todas as empresas"), *EMP_TODAS], padrao=TODAS),
     *PERIODO],
    origem="Extracoes/relatorio_vendas.py",
)
def vendas_pdv(ctx):
    mod = ctx.script("relatorio_vendas.py")
    ini, fim = _periodo(ctx)
    alvos = do_script(mod.EMPRESAS, siglas(ctx.params["empresa"], ["ELE", "COR", "ILU"]))

    def buscar(alvo):
        nome, cfg = alvo
        df = mod.buscar_vendas(cfg["HOST"], cfg["DATABASE"], ini, fim)
        if not df.empty:
            df.insert(0, "EMPRESA", nome.split(" (")[0])
        return df

    resultados, falhas = em_paralelo(alvos, buscar)
    df = juntar(resultados, falhas, "Nenhuma venda encontrada no período.")
    colunas = ["EMPRESA"] + [c for c in mod.COLUNAS_VENDAS if c in df.columns]
    df = df[colunas + [c for c in df.columns if c not in colunas]]
    base = f"RelatorioVendas_{ini:%Y%m%d}_a_{fim:%Y%m%d}"
    somar = list(mod.COLUNAS_SOMAR_VENDAS)
    salvar_excel(ctx.arquivo(base + ".xlsx"), {"Vendas": df}, moeda=[c for c in somar if c != "QUANTIDADE"])
    pagina_tabela(ctx.arquivo(base + ".html"), df, "Vendas item a item (PDV)", f"{ini:%d/%m/%Y} a {fim:%d/%m/%Y}",
                  moeda=[c for c in somar if c != "QUANTIDADE"], somar=somar,
                  filtros=["EMPRESA", "VENDEDOR", "FORMA DE PAGAMENTO"],
                  cartoes=[("Valor bruto", "VALOR BRUTO"), ("Desconto", "DESCONTO"), ("Valor líquido", "VALOR LIQUIDO")])
    ctx.pagina = base + ".html"
    ctx.resumo = f"{len(df)} item(ns) vendido(s)." + aviso_falhas(falhas)


@ferramenta(
    "somatorio-pdv",
    "Somatório do PDV",
    "vendas",
    "Cada venda do PDV com o valor de cada forma de pagamento (dinheiro, cheque, convênio, cartão/Pix, contas a "
    "receber, troco...). Totais por coluna e filtros por empresa e vendedor.",
    [Campo("empresa", "Empresa", "empresa", opcoes=[(TODAS, "Todas as empresas"), *EMP_TODAS], padrao=TODAS),
     *PERIODO],
    origem="Extracoes/Extracao somatorio/Extracao_somatorio_v10.py",
)
def somatorio_pdv(ctx):
    mod = ctx.script("extracao_somatorio.py")
    ini, fim = _periodo(ctx)
    alvos = do_script(mod.EMPRESAS, siglas(ctx.params["empresa"], ["ELE", "COR", "ILU"]))
    resultados, falhas = em_paralelo(alvos, lambda a: mod.consultar_empresa(a[0], a[1], ini, fim))
    df = juntar(resultados, falhas, "Nenhuma venda de PDV no período.")
    df = df.sort_values(["DATA", "EMPRESA", "NO PDV"], ignore_index=True)
    df["DATA"] = pd.to_datetime(df["DATA"]).dt.strftime("%d/%m/%Y")
    base = f"SomatorioPDV_{ini:%Y%m%d}_a_{fim:%Y%m%d}"
    totais = {c: "" for c in df.columns}
    totais["CLIENTE"] = "TOTAIS"
    for c in mod.COLS_NUMERICAS:
        totais[c] = df[c].sum()
    salvar_excel(ctx.arquivo(base + ".xlsx"), {"Somatório PDV": pd.concat([df, pd.DataFrame([totais])], ignore_index=True)},
                 moeda=mod.COLS_NUMERICAS)
    pagina_tabela(ctx.arquivo(base + ".html"), df, "Somatório do PDV", f"{ini:%d/%m/%Y} a {fim:%d/%m/%Y}",
                  moeda=mod.COLS_NUMERICAS, somar=mod.COLS_NUMERICAS, filtros=["EMPRESA", "VENDEDOR"],
                  cartoes=[("Total PDV", "TOTAL PDV"), ("Vendas à vista", "Vendas a vista"),
                           ("Cartão + Pix", "CART. DIG."), ("Contas a receber", "CONTAS REC")])
    ctx.pagina = base + ".html"
    ctx.resumo = f"{len(df)} venda(s) de PDV." + aviso_falhas(falhas)


@ferramenta(
    "planilha-orcamento",
    "Planilha-modelo de orçamento",
    "vendas",
    "Gera a planilha Excel de orçamento com aba de lançamento para o vendedor e a aba de impressão já formatada.",
    [],
    origem="Orcamentos/Planilha_orcamento.py",
)
def planilha_orcamento(ctx):
    mod = ctx.script("Planilha_orcamento.py")
    with na_pasta(ctx.pasta):
        mod.criar_orcamento_inteligente()
    ctx.resumo = f"Planilha gerada em {date.today():%d/%m/%Y}."
