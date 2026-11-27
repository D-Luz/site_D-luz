"""Estoque e compras."""

import calendar
from datetime import date, datetime

import pandas as pd

from ..tarefas import ErroAmigavel
from . import EMP_TODAS, TODAS, Campo, ferramenta, hoje, para_data
from ._comum import aviso_falhas, do_script, em_paralelo, juntar, na_pasta, siglas
from ._saida import nome_arquivo, pagina_tabela, salvar_excel


@ferramenta(
    "analise-estoque",
    "Análise de estoque (Curva ABC × XYZ)",
    "estoque",
    "Classifica todos os produtos ativos por Curva ABC × XYZ, com estoque de segurança, estoque mínimo, "
    "sugestão de pedido, cobertura, tendência, capital parado, custo líquido da última NF e meses sem giro.",
    [Campo("empresa", "Empresa", "empresa", opcoes=EMP_TODAS, padrao="ELE"),
     Campo("lead_time", "Prazo de reposição (meses)", "numero", padrao="2",
           ajuda="Tempo médio entre pedir e receber a mercadoria. Usado no estoque de segurança."),
     Campo("fornecedor", "Só deste fornecedor (opcional)", "texto", obrigatorio=False,
           ajuda="Parte do nome. Em branco = todos."),
     Campo("grupos", "Arquivo de grupos (opcional)", "arquivo", obrigatorio=False, aceita=".xlsx,.xls,.csv",
           ajuda="Planilha com colunas Codigo e Grupo para analisar produtos equivalentes como um só."),
     Campo("tres_lojas", "Incluir aba com o estoque das 3 lojas", "marcar", obrigatorio=False,
           ajuda="Soma o estoque de cada produto nas três empresas (pela descrição). Demora mais."),
     Campo("usar_cache", "Reaproveitar a extração de hoje, se houver", "marcar", obrigatorio=False, padrao="1")],
    origem="Analise Estoque - compras/ats analise estoque_V5.py",
    detalhes="Período analisado: últimos 12 meses. A extração de cada empresa fica guardada no dia "
             "(para não consultar o ATS de novo a cada filtro).",
)
def analise_estoque(ctx):
    mod = ctx.script("analise_estoque.py")
    mod.CACHE_DIR = str(ctx.data_dir / "cache_estoque")
    try:
        lead = float(str(ctx.params.get("lead_time") or "2").replace(",", "."))
    except ValueError:
        raise ErroAmigavel("Prazo de reposição inválido.") from None
    sigla = siglas(ctx.params["empresa"], ["ELE", "COR", "ILU"])[0]
    usar_cache = bool(ctx.params.get("usar_cache"))

    def obter(empresa):
        if usar_cache:
            c = mod.carregar_cache(empresa)
            if c is not None:
                print(f"{empresa}: usando a extração de hoje às {c[3]:%H:%M}.")
                return c[:3]
        print(f"{empresa}: consultando o ATS (estoque, vendas de 12 meses e últimas NFs)...")
        dados = mod.extrair_dados(empresa)
        mod.salvar_cache(empresa, *dados)
        return dados

    (empresa, _), = do_script(mod.EMPRESAS, [sigla])
    df_estoque, df_vendas, df_ultima = obter(empresa)
    if df_vendas.empty:
        raise ErroAmigavel("Nenhuma movimentação encontrada no período.")
    arquivos_grupos = ctx.entradas("grupos")
    if arquivos_grupos:
        mapa = mod.carregar_grupos(str(arquivos_grupos[0]))
        print(f"Agrupando {len(mapa)} código(s) em {len(set(mapa.values()))} grupo(s).")
        df_estoque, df_vendas, df_ultima = mod.aplicar_grupos(df_estoque, df_vendas, mapa, df_ultima)
    print("Calculando a curva ABC × XYZ...")
    df, _meses = mod.calcular_abc_xyz(df_vendas, df_estoque, lead, df_ultima)

    termo = (ctx.params.get("fornecedor") or "").strip().lower()
    if termo and "Fornecedor" in df.columns:
        df = df[df["Fornecedor"].astype(str).str.lower().str.contains(termo, regex=False)]
        if df.empty:
            raise ErroAmigavel(f"Nenhum produto do fornecedor “{termo}”.")

    renomear = {
        "media": "media_mensal_qtd", "valor_mensal": "valor_mensal_venda", "desvio_padrao": "desvio_padrão",
        "classe": "classe_ABC_XYZ", "z": "fator_Z", "intermitente": "demanda_irregular",
        "estoq_seguranca": "estoq_segurança", "qnt_em_falta": "qtd_em_falta",
        "meses_sem_movimento": "meses_sem_ultima_venda", "sem_giro": "sem_giro_periodo",
        "Custo Liquido": "custo_liquido_ult_nf", "Preco de Venda": "preco_venda", "pedido_valor": "pedido_valor_rs",
        "capital_parado": "capital_parado_rs", "media_recente": "media_3m_recente",
        "tendencia_pct": "tendencia_3m_vs_12m_pct", "Ultima Entrada": "data_ultima_entrada_nf",
        "meses_desde_entrada": "meses_desde_ultima_entrada", "produto_novo": "produto_novo_sem_giro",
    }
    exp = df.rename(columns={k: v for k, v in renomear.items() if k in df.columns})
    abas = {
        "Análise Completa": exp,
        "Pedidos de Compra": exp[exp["pedido"] > 0],
        "Curva A": exp[exp["classe_ABC_XYZ"].astype(str).str.startswith("A", na=False)],
        "Demanda Irregular": exp[exp["demanda_irregular"] == True],  # noqa: E712
    }
    sem_giro = exp[exp["sem_giro_periodo"] == True]  # noqa: E712
    if len(sem_giro):
        abas["Sem Giro"] = sem_giro
    if ctx.params.get("tres_lojas"):
        raw = {}
        for nome in mod.EMPRESAS:
            try:
                raw[nome] = (df_estoque, df_vendas, df_ultima) if nome == empresa and not arquivos_grupos else obter(nome)
            except Exception as exc:
                print(f"✗ {nome}: {exc}")
        abas["Grupo (3 Empresas)"] = mod.consolidar_grupo(raw)
    moeda = ["valor_mensal_venda", "custo_liquido_ult_nf", "preco_venda", "pedido_valor_rs", "capital_parado_rs"]
    base = nome_arquivo(f"Analise_Estoque_{empresa}_{datetime.now():%Y%m%d_%H%M}")
    salvar_excel(ctx.arquivo(base + ".xlsx"), abas, moeda=moeda)
    pagina_tabela(ctx.arquivo(base + ".html"), exp, f"Análise de estoque — {empresa}",
                  f"Curva ABC × XYZ · prazo de reposição {lead:g} mês(es)",
                  moeda=moeda, somar=["pedido_valor_rs", "capital_parado_rs", "valor_mensal_venda"],
                  filtros=[c for c in ("classe_ABC_XYZ", "Fornecedor", "Curva") if c in exp.columns],
                  cartoes=[("Sugestão de compra", "pedido_valor_rs"), ("Capital parado", "capital_parado_rs")])
    ctx.pagina = base + ".html"
    ctx.resumo = (f"{len(exp)} produto(s) · {len(abas['Pedidos de Compra'])} com sugestão de pedido · "
                  f"{len(sem_giro)} sem giro.")


@ferramenta(
    "posicao-estoque",
    "Posição de estoque",
    "estoque",
    "Estoque e custo de cada produto, hoje ou numa data passada (estimada a partir das vendas do PDV), "
    "no banco Real ou no Fiscal.",
    [Campo("empresa", "Empresa", "empresa", opcoes=EMP_TODAS, padrao="ELE"),
     Campo("banco", "Banco", "escolha", opcoes=[("real", "Real"), ("fiscal", "Fiscal")], padrao="real"),
     Campo("data", "Data (opcional)", "data", obrigatorio=False,
           ajuda="Em branco = estoque atual. Com data = histórico estimado nessa data.")],
    origem="Extracoes/Extracao_posicaoestoque.py",
)
def posicao_estoque(ctx):
    mod = ctx.script("Extracao_posicaoestoque.py")
    banco = ctx.params.get("banco") or "real"
    if banco not in ("real", "fiscal"):
        raise ErroAmigavel("Banco inválido.")
    (empresa, _), = do_script(mod.EMPRESAS, siglas(ctx.params["empresa"], ["ELE", "COR", "ILU"]))
    data_ref = para_data(ctx.params["data"]) if ctx.params.get("data") else None
    print(f"Consultando {empresa} (banco {banco})...")
    df = mod.extrair_estoque(empresa, banco, data_ref)
    if df.empty:
        raise ErroAmigavel("Nenhum produto retornado.")
    quando = f"em {data_ref:%d/%m/%Y} (estimado)" if data_ref else f"atual ({date.today():%d/%m/%Y})"
    base = nome_arquivo(f"Estoque_{empresa}_{banco}_{(data_ref or date.today()):%Y%m%d}")
    numericas = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    moeda = [c for c in numericas if any(p in str(c).lower() for p in ("custo", "valor", "total", "preço", "preco"))]
    salvar_excel(ctx.arquivo(base + ".xlsx"), {"Estoque": df}, moeda=moeda)
    pagina_tabela(ctx.arquivo(base + ".html"), df, f"Posição de estoque — {empresa}", f"Banco {banco}, {quando}",
                  moeda=moeda, somar=numericas,
                  filtros=[c for c in df.columns if str(c).lower() in ("grupo", "família", "familia", "fornecedor", "marca")])
    ctx.pagina = base + ".html"
    ctx.resumo = f"{len(df)} produto(s) — estoque {quando}."


def _ultima_nf(ctx, arquivo, rotulo):
    mod = ctx.script(arquivo)
    data_ref = datetime.combine(para_data(ctx.params.get("data") or date.today().isoformat()), datetime.min.time())
    escolhidas = siglas(ctx.params["empresa"], ["ELE", "COR", "ILU"])
    alvos = do_script(mod.EMPRESAS, escolhidas)
    resultados, falhas = em_paralelo(alvos, lambda a: mod.extrair_empresa(a[0], data_ref))
    if not resultados:
        raise ErroAmigavel("Nenhuma empresa retornou dados: " + "; ".join(f"{n} ({e})" for n, e in falhas))
    abas = {nome: df.drop(columns=["Empresa"]) for (nome, _), df in resultados}
    caminho = ctx.arquivo(f"Ultima_NF_Compra_{rotulo}_ate_{data_ref:%Y%m%d}_gerado_{datetime.now():%Y%m%d_%H%M}.xlsx")
    with pd.ExcelWriter(caminho, engine="openpyxl") as w:
        for nome, df in abas.items():
            df.to_excel(w, sheet_name=nome[:31], index=False)
    todas = pd.concat([df for _, df in resultados], ignore_index=True)
    pagina_tabela(caminho.with_suffix(".html"), todas, f"Última NF de compra por produto ({rotulo})",
                  f"Compras até {data_ref:%d/%m/%Y}", filtros=["Empresa"])
    ctx.pagina = caminho.with_suffix(".html").name
    ctx.resumo = f"{len(todas)} produto(s) em {len(abas)} empresa(s)." + aviso_falhas(falhas)


CAMPOS_NF = [
    Campo("empresa", "Empresa", "empresa", opcoes=[(TODAS, "Todas as empresas"), *EMP_TODAS], padrao=TODAS),
    Campo("data", "Compras até", "data", padrao=hoje, ajuda="Considera a última NF de entrada até essa data."),
]


@ferramenta(
    "ultima-nf-compra",
    "Última NF de compra por produto (Real)",
    "estoque",
    "Para cada produto ativo, a última NF de entrada com ICMS, ICMS-ST, IPI, PIS, COFINS, frete, preço de "
    "entrada e custo com créditos conforme o regime da empresa. Banco Real.",
    CAMPOS_NF,
    origem="Extracoes/Extracao UEPS/extracao_ultima_nf_compra_v4.py",
)
def ultima_nf_real(ctx):
    _ultima_nf(ctx, "extracao_ultima_nf_compra.py", "Real")


@ferramenta(
    "ultima-nf-compra-fiscal",
    "Última NF de compra por produto (Fiscal)",
    "estoque",
    "O mesmo relatório da última NF de compra, lendo os bancos Fiscais.",
    CAMPOS_NF,
    origem="Extracoes/Extracao UEPS/extracao_ultima_nf_compra_fiscal.py",
)
def ultima_nf_fiscal(ctx):
    _ultima_nf(ctx, "extracao_ultima_nf_compra_fiscal.py", "Fiscal")


@ferramenta(
    "validar-preconf",
    "Validar custo da NF (PRECONF)",
    "estoque",
    "Confere, por três provas independentes, se o campo PRECONF das NFs de entrada é preço unitário ou total da "
    "linha — o que muda o custo usado na análise de estoque. Só leitura.",
    [],
    origem="Analise Estoque - compras/validar_preconf.py",
)
def validar_preconf(ctx):
    mod = ctx.script("validar_preconf.py")
    with na_pasta(ctx.pasta):
        mod.main()
    if not list(ctx.pasta.glob("Validacao_PRECONF_*.xlsx")):
        raise ErroAmigavel("Nenhuma empresa retornou dados. Veja o registro abaixo.")
    ctx.resumo = "Veja a conclusão no registro abaixo e a amostra no Excel."
