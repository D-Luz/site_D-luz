import os
import json
import html as html_lib
import firebirdsql
import pandas as pd
import warnings
import requests
import webbrowser
from datetime import date, datetime, timedelta

# Silencia aviso sobre ligacoes nativas do pandas
warnings.filterwarnings('ignore', message='pandas only supports SQLAlchemy')

# ═══════════════════════════════════════════════════════════════
# 1. CONFIGURACOES GERAIS E MULTI-EMPRESAS (APENAS REAIS)
# ═══════════════════════════════════════════════════════════════
ARQUIVO_BASE_MANUAL = 'Base gestão separada.xlsx'

# Uma senha diferente para cada aba do painel — altere aqui ou via env var
SENHAS_ABAS = {
    'pagar':   os.environ.get('DASH_SENHA_PAGAR',   'pagar2026'),
    'receber': os.environ.get('DASH_SENHA_RECEBER', 'dluz2026'),
    'gerais':  os.environ.get('DASH_SENHA_GERAIS',  'gerais2026'),
}

EMPRESAS = {
    "Dluz Elétrica (Real)": {"HOST": "192.168.0.41", "DATABASE": r"D:\DLUZR\BANCO\DLUZR25.FB", "DISPLAY": "Dluz Elétrica"},
    "Cor e Cia (Real)": {"HOST": "192.168.0.200", "DATABASE": r"D:\CorCiaR\BANCO\CORCIAR25.FB", "DISPLAY": "Cor e Cia"},
    "Dluz Iluminação (Real)": {"HOST": "192.168.0.250", "DATABASE": r"D:\ILUMIR\BANCO\ILUMIR.FB", "DISPLAY": "Dluz Iluminação"}
}

USER     = 'SYSDBA'
PASSWORD = os.environ.get('FB_PASSWORD', 'masterkey')   # <- mova para variável de ambiente
PORT     = 3050
CHARSET  = 'WIN1252'

# Netlify (recomendado mover para variável de ambiente)
NETLIFY_TOKEN = os.environ.get('NETLIFY_TOKEN', '')  # token removido do código (portal não publica no Netlify)
NETLIFY_SITE_ID = os.environ.get('NETLIFY_SITE_ID', '')

# Query: Contas a Receber
SQL_RECEBER = """
SELECT
    d.DT_VENCIMENTO AS "VENCIMENTO_RAW", d.DT_EMISSAO AS "EMISSÃO_RAW",
    d.CODDOCTO AS "DOCUMENTO", d.CODCLIENTE AS "CÓD. CLIENTE", c.NOME AS "CLIENTE",
    d.CODVENDEDOR AS "CÓD. VENDEDOR", v.NOME AS "VENDEDOR",
    COALESCE(d.VALORDOCTO, 0) AS "VALOR TÍTULO", COALESCE(d.VALORPAGO, 0) AS "VALOR PAGO",
    CASE WHEN d.SITUACAO = 1 THEN 'Aberto' WHEN d.SITUACAO = 3 THEN 'Parcial' ELSE CAST(d.SITUACAO AS VARCHAR(10)) END AS "SITUAÇÃO"
FROM DOCUREC d
LEFT JOIN CLIENTE c ON d.CODCLIENTE = c.CODCLIENTE
LEFT JOIN VENDEND v ON d.CODVENDEDOR = v.CODVENDEDOR
WHERE d.SITUACAO IN (1, 3) ORDER BY c.NOME, d.DT_VENCIMENTO
"""

# Query: Contas a Pagar
SQL_PAGAR = """
SELECT
    d.DT_VENCIMENTO AS "VENCIMENTO_RAW", d.DT_EMISSAO AS "EMISSÃO_RAW",
    d.DOCTOFORNEC AS "DOCUMENTO", d.CODFORNEC AS "CÓD. FORNEC.", f.NOME AS "FORNECEDOR",
    COALESCE(d.VALORDOCTO, 0) AS "VALOR TÍTULO", COALESCE(d.VALORPAGO, 0) AS "VALOR PAGO",
    CASE WHEN d.SITUACAO = 1 THEN 'Aberto' WHEN d.SITUACAO = 3 THEN 'Parcial' ELSE CAST(d.SITUACAO AS VARCHAR(10)) END AS "SITUAÇÃO"
FROM DOCUPAG d LEFT JOIN FORNECE f ON d.CODFORNEC = f.CODFORNEC
WHERE d.SITUACAO IN (1, 3) ORDER BY f.NOME, d.DT_VENCIMENTO
"""

# ═══════════════════════════════════════════════════════════════
# 1b. ACOMPANHAMENTO — alimenta as 3 subabas de "Dados Gerais"
#     Mesmas consultas do Extracoes/acompanhamento_semanal_ats.py, com
#     6 períodos independentes (A-F): semana anterior, mês atual (MTD)
#     e ano atual (YTD), cada um contra o mesmo período do ano anterior.
#     As consultas trazem a faixa bruta de cada "lado" (atual/ano
#     anterior) sem CASE de período — a classificação por período é
#     feita em pandas, permitindo janelas sobrepostas (semana ⊂ mês ⊂
#     ano). As datas são bindadas, contornando a limitação do Firebird
#     Dialect 1 com CURRENT_DATE/DATE.
# ═══════════════════════════════════════════════════════════════
SQL_VENDAS = """
SELECT
  c.DATAPEDIDO                   AS DATAPEDIDO,
  COALESCE(e.VLRDINHEIRO,0)      AS VLRDINHEIRO,
  COALESCE(e.VLRCARTAO,0)        AS VLRCARTAO,
  COALESCE(e.VLRCHEQUE,0)        AS VLRCHEQUE,
  COALESCE(e.VLRCONVENIO,0)      AS VLRCONVENIO,
  COALESCE(e.VLRCHPREDATADO,0)   AS VLRCHPREDATADO,
  COALESCE(e.VLRRECEBER,0)       AS VLRRECEBER,
  COALESCE(c.TOTALPEDIDO,0)      AS TOTALPEDIDO,
  -- "Outras formas": parte do pedido que não caiu em nenhuma das 6 colunas
  -- acima (ex.: faturados com ENCEFAT zerada ou sem ENCEFAT).
  COALESCE(c.TOTALPEDIDO,0) - (COALESCE(e.VLRDINHEIRO,0) + COALESCE(e.VLRCARTAO,0)
      + COALESCE(e.VLRCHEQUE,0) + COALESCE(e.VLRCONVENIO,0)
      + COALESCE(e.VLRCHPREDATADO,0) + COALESCE(e.VLRRECEBER,0)) AS VLROUTRAS
FROM PEDIDOC c
LEFT JOIN ENCEFAT e ON c.CODPEDIDO = e.CODPEDIDO AND c.TIPOPEDIDO = e.TIPOPEDIDO
WHERE c.TIPOPEDIDO = 55
  AND c.FATURADO = 'S'          -- exclui pré-vendas abertas ('N') e cancelados ('X')
  AND c.DATAPEDIDO >= ? AND c.DATAPEDIDO < ?
"""

SQL_LIQUIDACOES = """
SELECT
  TRIM(cli.NOME)                                           AS CLIENTE,
  TRIM(m.CODDOCTO)                                         AS DOCUMENTO,
  d.DT_EMISSAO                                             AS EMISSAO,
  m.DT_VENCIMENTO                                          AS VENCIMENTO,
  m.DT_MOVIMENTO                                           AS DT_LIQUIDACAO,
  CASE WHEN m.TIPOMOV = '05' THEN 'LIQ. VALOR'
       ELSE COALESCE(f.DESCFORMA, '(sem forma)') END       AS FORMA_PGTO,
  COALESCE(m.VALORMOV,0) + COALESCE(m.VALORDESC,0)
                         - COALESCE(m.JUROSMULTA,0)        AS VLR_BRUTO,
  COALESCE(m.JUROSMULTA,0)                                 AS JUROS,
  COALESCE(m.VALORDESC,0)                                  AS DESCONTO,
  COALESCE(m.VALORMOV,0)                                   AS VLR_RECEBIDO
FROM MOVIREC m
LEFT JOIN CLIENTE cli ON cli.CODCLIENTE = m.CODCLIENTE
LEFT JOIN FORPGTO f   ON f.CODFORMA     = m.CODFORMAPGTO
LEFT JOIN DOCUREC d   ON d.CODEMPRESA = m.CODEMPRESA
                     AND d.TIPODOCTO  = m.TIPODOCTO
                     AND d.CODDOCTO   = m.CODDOCTO
                     AND d.CODCLIENTE = m.CODCLIENTE
WHERE m.TIPOMOV IN ('04', '05')
  AND m.ESTORNADO = 'N'
  AND m.DT_MOVIMENTO >= ? AND m.DT_MOVIMENTO < ?
"""

# (rótulo, coluna, natureza) — mesma ordem/numeração do .sql original
FORMAS = [
    ("1. Dinheiro",           "VLRDINHEIRO",     "À vista"),
    ("2. Cartão",             "VLRCARTAO",       "À vista"),
    ("3. Cheque à vista",     "VLRCHEQUE",       "À vista"),
    ("4. Convênio",           "VLRCONVENIO",     "À vista"),
    ("5. Cheque pré-datado",  "VLRCHPREDATADO",  "A prazo"),
    ("6. Contas a receber",   "VLRRECEBER",      "A prazo"),
]
COLS_VISTA = ("VLRDINHEIRO", "VLRCARTAO", "VLRCHEQUE", "VLRCONVENIO")
COLS_PRAZO = ("VLRCHPREDATADO", "VLRRECEBER")

OUTRAS    = "7. Outras formas de pagamento"
SUB_VISTA = "8. SUBTOTAL À VISTA"
SUB_PRAZO = "9. SUBTOTAL A PRAZO"
TOTAL_REC = "10. RECEITA TOTAL"
LIQUID    = "11. Liquidações (contas a receber)"
REC_VISTA = "12. RECEBIMENTOS À VISTA"

# (rótulo, natureza) na ordem em que aparecem na tabela
LINHAS_RECEITA = ([(rot, nat) for rot, _, nat in FORMAS]
                  + [(OUTRAS, "À vista"), (SUB_VISTA, "Subtotal"), (SUB_PRAZO, "Subtotal"),
                     (TOTAL_REC, "Total"), (LIQUID, "Recebimento"), (REC_VISTA, "Total")])

# Cada subaba de "Dados Gerais" é um par de janelas (atual x ano anterior).
# Chaves A-F replicam os rótulos do acompanhamento_semanal_ats.py.
SUBABAS_GERAIS = [
    ("semana", "A", "B", "Semana anterior", "Mesma semana — ano anterior"),
    ("mes",    "C", "D", "Mês atual (MTD)", "Mesmo mês — ano anterior"),
    ("ano",    "E", "F", "Ano atual (YTD)", "Mesmo período — ano anterior"),
]


def opcoes_empresas(opcao_todas=None):
    """Gera as <option> dos seletores de empresa a partir de EMPRESAS,
    evitando duplicar a lista de empresas em vários pontos do HTML.
    `opcao_todas` (ex.: 'Empresas') adiciona uma opção vazia no topo."""
    opts = []
    if opcao_todas is not None:
        opts.append(f'<option value="">{opcao_todas}</option>')
    for nome, cfg in EMPRESAS.items():
        opts.append(f'<option value="{nome}">{cfg["DISPLAY"]}</option>')
    return "".join(opts)


def formatar_codigo(valor):
    """Normaliza o código do cliente para texto, removendo o '.0' que o
    pandas adiciona quando a coluna é lida como float (por causa de nulos)."""
    if pd.isna(valor):
        return ''
    texto = str(valor).strip()
    if texto.endswith('.0'):
        texto = texto[:-2]
    return texto


def formatar_vendedor(codigo, nome):
    nome = '' if pd.isna(nome) else str(nome).strip()
    if nome:
        return nome
    codigo = formatar_codigo(codigo)
    return f"Cód. {codigo}" if codigo else "-"


def formatar_moeda(valor):
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def esc(valor):
    """Escapa texto vindo do banco antes de injetar no HTML."""
    if valor is None or (not isinstance(valor, str) and pd.isna(valor)):
        return ''
    return html_lib.escape(str(valor).strip())


def periodos_definidos(hoje):
    """Os 6 períodos do acompanhamento — cada um é uma janela [ini, lim)
    independente (limite exclusivo, por isso o SQL usa '<'):
      A) Semana anterior completa (segunda a sábado)
      B) Mesma semana do ano anterior (-364 dias = 52 semanas exatas)
      C) Mês atual — MTD (dia 01 até hoje)
      D) Mesmo mês do ano anterior — MTD (dia 01 até o mesmo dia, ano -1)
      E) Ano atual — YTD (01/01 até hoje)
      F) Mesmo período do ano anterior — YTD (01/01 até o mesmo dia, ano -1)
    """
    seg_desta_semana = hoje - timedelta(days=hoje.weekday())   # weekday(): segunda=0
    ini_semana_atual = seg_desta_semana - timedelta(days=7)
    lim_semana_atual = ini_semana_atual + timedelta(days=6)
    ini_semana_ano_ant = ini_semana_atual - timedelta(days=364)
    lim_semana_ano_ant = lim_semana_atual - timedelta(days=364)

    ini_mtd_atual = hoje.replace(day=1)
    lim_mtd_atual = hoje + timedelta(days=1)                   # inclui hoje

    try:
        mesmo_dia_ano_ant = hoje.replace(year=hoje.year - 1)
    except ValueError:                                         # 29/fev sem par no ano anterior
        mesmo_dia_ano_ant = date(hoje.year - 1, 2, 28)
    ini_mtd_ano_ant = mesmo_dia_ano_ant.replace(day=1)
    lim_mtd_ano_ant = mesmo_dia_ano_ant + timedelta(days=1)

    ini_ytd_atual   = date(hoje.year, 1, 1)
    lim_ytd_atual   = lim_mtd_atual
    ini_ytd_ano_ant = date(hoje.year - 1, 1, 1)
    lim_ytd_ano_ant = lim_mtd_ano_ant

    return {
        "A": (ini_semana_atual,   lim_semana_atual),
        "B": (ini_semana_ano_ant, lim_semana_ano_ant),
        "C": (ini_mtd_atual,      lim_mtd_atual),
        "D": (ini_mtd_ano_ant,    lim_mtd_ano_ant),
        "E": (ini_ytd_atual,      lim_ytd_atual),
        "F": (ini_ytd_ano_ant,    lim_ytd_ano_ant),
    }


def limites_janela(periodos, chaves):
    """Faixa [ini, lim) que cobre todos os períodos de um lado (semana ⊂ mês ⊂ ano),
    para buscar no banco numa única consulta por lado."""
    inis = [periodos[k][0] for k in chaves]
    lims = [periodos[k][1] for k in chaves]
    return min(inis), max(lims)


def consultar(con, sql, params):
    """Consulta parametrizada — o pd.read_sql do projeto não passa binds."""
    cur = con.cursor()
    try:
        cur.execute(sql, params)
        colunas = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=colunas)
    finally:
        cur.close()


def fatiar_por_data(df, coluna, ini, lim):
    """Recorta `df` para as linhas com `coluna` dentro de [ini, lim)."""
    if df is None or df.empty:
        return df.iloc[0:0] if df is not None else pd.DataFrame()
    dt = pd.to_datetime(df[coluna])
    return df.loc[(dt >= pd.Timestamp(ini)) & (dt < pd.Timestamp(lim))]


def montar_receita(df_atual, janela_atual, df_anoant, janela_anoant,
                   liq_atual=None, liq_anoant=None):
    """Reduz as vendas brutas de um par de janelas (atual x ano anterior) a
    {rótulo: (valor_atual, valor_ano_anterior)} para as 6 formas de
    pagamento, "outras formas", os 2 subtotais, a receita total, as
    liquidações do período e os recebimentos à vista (subtotal à vista +
    liquidações). "Outras formas" compõe o subtotal à vista, logo
    receita total = à vista + a prazo."""
    def soma(df, janela, coluna):
        sub = fatiar_por_data(df, 'DATAPEDIDO', *janela)
        return float(sub[coluna].sum()) if sub is not None and not sub.empty else 0.0

    vals = {rotulo: (soma(df_atual, janela_atual, col), soma(df_anoant, janela_anoant, col))
            for rotulo, col, _ in FORMAS}
    # "outras formas" entra no à vista (decisão do usuário)
    vals[SUB_VISTA] = (sum(soma(df_atual, janela_atual, c) for c in COLS_VISTA + ('VLROUTRAS',)),
                       sum(soma(df_anoant, janela_anoant, c) for c in COLS_VISTA + ('VLROUTRAS',)))
    vals[SUB_PRAZO] = (sum(soma(df_atual, janela_atual, c) for c in COLS_PRAZO),
                       sum(soma(df_anoant, janela_anoant, c) for c in COLS_PRAZO))
    vals[OUTRAS] = (soma(df_atual, janela_atual, 'VLROUTRAS'),
                    soma(df_anoant, janela_anoant, 'VLROUTRAS'))
    vals[TOTAL_REC] = (soma(df_atual, janela_atual, 'TOTALPEDIDO'),
                       soma(df_anoant, janela_anoant, 'TOTALPEDIDO'))

    def soma_liq(df, janela):
        sub = fatiar_por_data(df, 'DT_LIQUIDACAO', *janela)
        return float(sub['VLR_RECEBIDO'].sum()) if sub is not None and not sub.empty else 0.0

    vals[LIQUID] = (soma_liq(liq_atual, janela_atual), soma_liq(liq_anoant, janela_anoant))
    vals[REC_VISTA] = (vals[SUB_VISTA][0] + vals[LIQUID][0],
                       vals[SUB_VISTA][1] + vals[LIQUID][1])
    # arredonda em centavos: somas de floats deixam resíduo (~1e-9) que viraria
    # "+1e15 %" na coluna de variação quando o valor real é zero
    return {k: (round(va, 2), round(vb, 2)) for k, (va, vb) in vals.items()}


def montar_html_receita(receita_por_empresa):
    """Tabela de receita por forma de pagamento — linhas .ger-row carregam
    data-por-empresa ("Empresa A=atual|anterior;Empresa B=...", mesmo
    formato compacto do data-codigos) para o filtro recalcular no browser
    sem recarregar a página. Devolve (html, total_atual, total_anoant, texto_variação)."""
    html = ""
    for rotulo, natureza in LINHAS_RECEITA:
        partes, tot_a, tot_b = [], 0.0, 0.0
        for emp_nome in EMPRESAS:
            va, vb = receita_por_empresa.get(emp_nome, {}).get(rotulo, (0.0, 0.0))
            partes.append(f"{emp_nome}={va:.2f}|{vb:.2f}")
            tot_a += va
            tot_b += vb

        if rotulo == TOTAL_REC:
            classe_linha, marca_total = "ger-row total-row", ' data-total="1"'
        elif rotulo == REC_VISTA:
            classe_linha, marca_total = "ger-row total-row", ''
        elif natureza == "Subtotal":
            classe_linha, marca_total = "ger-row subtotal-row", ''
        else:
            classe_linha, marca_total = "ger-row", ''

        dif = tot_a - tot_b
        sinal = "+" if dif >= 0 else "−"
        cls_var = "" if abs(dif) < 0.005 else (" var-up" if dif > 0 else " var-down")
        if abs(tot_b) < 0.005:
            pct_txt, cls_pct = ("—" if abs(tot_a) < 0.005 else "novo"), ""
        else:
            pct = dif / tot_b * 100
            pct_txt = f"{sinal}{abs(pct):.1f}".replace(".", ",") + "%"
            cls_pct = "" if abs(pct) < 0.05 else cls_var

        html += (
            f'<tr class="{classe_linha}"{marca_total} data-por-empresa="{esc(";".join(partes))}">'
            f'<td class="ger-forma">{esc(rotulo)}</td>'
            f'<td><span class="tag-grupo">{esc(natureza)}</span></td>'
            f'<td class="num-cell ger-atual">{formatar_moeda(tot_a)}</td>'
            f'<td class="num-cell ger-anterior">{formatar_moeda(tot_b)}</td>'
            f'<td class="num-cell ger-dif{cls_var}">{sinal}{formatar_moeda(abs(dif))}</td>'
            f'<td class="num-cell ger-pct{cls_pct}">{pct_txt}</td>'
            f'</tr>'
        )

    tot_a = sum(receita_por_empresa.get(e, {}).get(TOTAL_REC, (0.0, 0.0))[0] for e in EMPRESAS)
    tot_b = sum(receita_por_empresa.get(e, {}).get(TOTAL_REC, (0.0, 0.0))[1] for e in EMPRESAS)
    dif = tot_a - tot_b
    var_txt = ("+" if dif >= 0 else "−") + f"R$ {formatar_moeda(abs(dif))}"
    if tot_b:
        var_txt += (" (" + ("+" if dif >= 0 else "−")
                    + f"{abs(dif / tot_b * 100):.1f}".replace(".", ",") + "%)")
    return html, tot_a, tot_b, var_txt


def montar_html_liq_semana(dados_gerais, janela_atual, janela_anoant, id_prefixo):
    """Liquidações linha a linha (semana), agrupadas por cliente (linha-resumo
    expansível, mesmo padrão da lista de Contas a Receber). Cliente ordenado
    pelo total recebido (maiores primeiro); dentro do grupo, mais recente primeiro."""
    partes = []
    for dados in dados_gerais.values():
        for df, janela, periodo in ((dados['l_atual'], janela_atual, 'atual'),
                                     (dados['l_anoant'], janela_anoant, 'anoant')):
            sub = fatiar_por_data(df, 'DT_LIQUIDACAO', *janela)
            if sub is None or sub.empty:
                continue
            sub = sub.copy()
            sub['PERIODO'] = periodo
            partes.append(sub)

    if not partes:
        return ""

    df_liq = pd.concat(partes, ignore_index=True)
    for coluna in ('DT_LIQUIDACAO', 'VENCIMENTO', 'EMISSAO'):
        df_liq[coluna] = pd.to_datetime(df_liq[coluna], errors='coerce')

    def fmt_data(v):
        return '-' if pd.isna(v) else v.strftime('%d/%m/%Y')

    grupos = sorted(df_liq.groupby('CLIENTE'), key=lambda g: (-g[1]['VLR_RECEBIDO'].sum(), g[0] or ''))

    html = ""
    for idx, (cliente, grupo_df) in enumerate(grupos, start=1):
        tot_recebido = grupo_df['VLR_RECEBIDO'].sum()
        n_liq = len(grupo_df)
        empresas_txt = ", ".join(EMPRESAS[e]['DISPLAY'] for e in sorted(grupo_df['EMPRESA'].unique()))
        detail_id = f"{id_prefixo}_{idx}"

        html += f"""
        <tr class="liqc-row" onclick="toggleDetails(this, '{detail_id}')">
            <td><span class="chevron">▾</span><span class="client-name">{esc(cliente) or '(sem nome)'}</span></td>
            <td><span class="tag-grupo">{esc(empresas_txt)}</span></td>
            <td class="num-cell liqc-n">{n_liq}</td>
            <td class="num-cell liqc-total">R$ {formatar_moeda(tot_recebido)}</td>
        </tr>
        <tr id="{detail_id}" class="details-row" style="display:none;"><td colspan="4"><div class="details-inner"><table class="details-table">
        <thead><tr><th>Liquidação</th><th>Empresa</th><th>Documento</th><th>Forma Pgto.</th><th>Vencimento</th><th>Bruto</th><th>Juros</th><th>Desconto</th><th>Recebido</th></tr></thead><tbody>
        """
        for _, row in grupo_df.sort_values('DT_LIQUIDACAO', ascending=False).iterrows():
            html += (
                f'<tr class="liq-row" data-empresa="{esc(row["EMPRESA"])}"'
                f' data-periodo="{esc(row["PERIODO"])}" data-qtd="1"'
                f' data-bruto="{row["VLR_BRUTO"]}" data-juros="{row["JUROS"]}"'
                f' data-desconto="{row["DESCONTO"]}" data-recebido="{row["VLR_RECEBIDO"]}">'
                f'<td><strong>{fmt_data(row["DT_LIQUIDACAO"])}</strong></td>'
                f'<td>{esc(EMPRESAS[row["EMPRESA"]]["DISPLAY"])}</td>'
                f'<td class="cod-cell">{esc(row["DOCUMENTO"]) or "-"}</td>'
                f'<td>{esc(row["FORMA_PGTO"]) or "-"}</td>'
                f'<td>{fmt_data(row["VENCIMENTO"])}</td>'
                f'<td class="num-cell">{formatar_moeda(row["VLR_BRUTO"])}</td>'
                f'<td class="num-cell">{formatar_moeda(row["JUROS"])}</td>'
                f'<td class="num-cell">{formatar_moeda(row["DESCONTO"])}</td>'
                f'<td class="num-cell saldo-recebido">{formatar_moeda(row["VLR_RECEBIDO"])}</td>'
                f'</tr>'
            )
        html += "</tbody></table></div></td></tr>"

    return html


def montar_html_liq_agrupado(dados_gerais, janela_atual, janela_anoant, id_prefixo):
    """Liquidações do mês/ano: volume alto demais para detalhe por documento
    (um ano inteiro de liquidações inflaria o HTML), então cada empresa entra
    já agregada por cliente (soma de bruto/juros/desconto/recebido + qtde de
    documentos — mesmo critério do acompanhamento_semanal_ats.py). O grupo
    por cliente reúne as empresas; o detalhe expandido mostra a quebra por
    empresa e período (atual/ano anterior)."""
    partes = []
    for nome_empresa, dados in dados_gerais.items():
        for df, janela, periodo in ((dados['l_atual'], janela_atual, 'atual'),
                                     (dados['l_anoant'], janela_anoant, 'anoant')):
            sub = fatiar_por_data(df, 'DT_LIQUIDACAO', *janela)
            if sub is None or sub.empty:
                continue
            agrupado = (
                sub.groupby('CLIENTE', dropna=False)
                   .agg(QTD_DOCUMENTOS=('DOCUMENTO', 'count'),
                        VLR_BRUTO=('VLR_BRUTO', 'sum'),
                        JUROS=('JUROS', 'sum'),
                        DESCONTO=('DESCONTO', 'sum'),
                        VLR_RECEBIDO=('VLR_RECEBIDO', 'sum'))
                   .reset_index()
            )
            agrupado.insert(0, 'EMPRESA', nome_empresa)
            agrupado.insert(1, 'PERIODO', periodo)
            partes.append(agrupado)

    if not partes:
        return ""

    df_agr = pd.concat(partes, ignore_index=True)
    grupos = sorted(df_agr.groupby('CLIENTE'), key=lambda g: (-g[1]['VLR_RECEBIDO'].sum(), g[0] or ''))
    periodo_txt = {'atual': 'Atual', 'anoant': 'Ano anterior'}

    html = ""
    for idx, (cliente, grupo_df) in enumerate(grupos, start=1):
        tot_recebido = grupo_df['VLR_RECEBIDO'].sum()
        tot_docs = int(grupo_df['QTD_DOCUMENTOS'].sum())
        empresas_txt = ", ".join(EMPRESAS[e]['DISPLAY'] for e in sorted(grupo_df['EMPRESA'].unique()))
        detail_id = f"{id_prefixo}_{idx}"

        html += f"""
        <tr class="liqc-row" onclick="toggleDetails(this, '{detail_id}')">
            <td><span class="chevron">▾</span><span class="client-name">{esc(cliente) or '(sem nome)'}</span></td>
            <td><span class="tag-grupo">{esc(empresas_txt)}</span></td>
            <td class="num-cell liqc-n">{tot_docs}</td>
            <td class="num-cell liqc-total">R$ {formatar_moeda(tot_recebido)}</td>
        </tr>
        <tr id="{detail_id}" class="details-row" style="display:none;"><td colspan="4"><div class="details-inner"><table class="details-table">
        <thead><tr><th>Empresa</th><th>Período</th><th>Qtde. Doc.</th><th>Bruto</th><th>Juros</th><th>Desconto</th><th>Recebido</th></tr></thead><tbody>
        """
        for _, row in grupo_df.sort_values(['PERIODO', 'EMPRESA']).iterrows():
            html += (
                f'<tr class="liq-row" data-empresa="{esc(row["EMPRESA"])}"'
                f' data-periodo="{esc(row["PERIODO"])}" data-qtd="{int(row["QTD_DOCUMENTOS"])}"'
                f' data-bruto="{row["VLR_BRUTO"]}" data-juros="{row["JUROS"]}"'
                f' data-desconto="{row["DESCONTO"]}" data-recebido="{row["VLR_RECEBIDO"]}">'
                f'<td><strong>{esc(EMPRESAS[row["EMPRESA"]]["DISPLAY"])}</strong></td>'
                f'<td>{periodo_txt.get(row["PERIODO"], row["PERIODO"])}</td>'
                f'<td class="num-cell">{int(row["QTD_DOCUMENTOS"])}</td>'
                f'<td class="num-cell">{formatar_moeda(row["VLR_BRUTO"])}</td>'
                f'<td class="num-cell">{formatar_moeda(row["JUROS"])}</td>'
                f'<td class="num-cell">{formatar_moeda(row["DESCONTO"])}</td>'
                f'<td class="num-cell saldo-recebido">{formatar_moeda(row["VLR_RECEBIDO"])}</td>'
                f'</tr>'
            )
        html += "</tbody></table></div></td></tr>"

    return html


def classificar_data(venc, hoje):
    if pd.isna(venc):
        return 'Sem Vencimento'
    diff_dias = (hoje - venc).days
    if diff_dias < 0:
        return 'A vencer'
    elif diff_dias <= (2 * 365.25):
        return 'Em dia'
    elif diff_dias <= (5 * 365.25):
        return 'Antigo'
    else:
        return 'Prescrito'


def badge_classe(classe):
    if classe == 'A vencer':
        return 'badge-vencer'
    if classe == 'Antigo':
        return 'badge-antigo'
    if classe == 'Prescrito':
        return 'badge-prescrito'
    return 'badge-emdia'


def sit_classe(situacao):
    return 'sit-aberto' if str(situacao).strip() == 'Aberto' else 'sit-parcial'


def publicar_no_netlify(caminho_html):
    """
    Publica via método DIGEST (SHA1) — recomendado pela API do Netlify.
    Diferente do método ZIP, aqui declaramos o caminho '/index.html'
    explicitamente, e o Netlify deduz o Content-Type pela extensão.
    Isso elimina o bug do HTML renderizado como texto.
    """
    import hashlib
    import time

    print("\n🌐 A publicar no Netlify (método digest)...")

    try:
        with open(caminho_html, "rb") as f:
            conteudo = f.read()
        sha1 = hashlib.sha1(conteudo).hexdigest()
        tamanho_mb = len(conteudo) / (1024 * 1024)
        print(f"📄 index.html: {tamanho_mb:.1f} MB | SHA1: {sha1[:12]}...")

        # ── 1) Cria o deploy enviando o digest dos arquivos ──
        api_deploys = f"https://api.netlify.com/api/v1/sites/{NETLIFY_SITE_ID}/deploys"
        headers_json = {
            "Authorization": f"Bearer {NETLIFY_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {"files": {"/index.html": sha1}}
        print("📤 A criar deploy...")
        r = requests.post(api_deploys, headers=headers_json, json=payload, timeout=60)
        if r.status_code not in (200, 201):
            print(f"❌ Erro ao criar deploy ({r.status_code}): {r.text}")
            return

        deploy = r.json()
        deploy_id = deploy.get("id", "")
        required  = deploy.get("required", [])
        site_url  = deploy.get("ssl_url") or deploy.get("url", "N/A")
        print(f"✅ Deploy criado. ID: {deploy_id}")

        # ── 2) Faz upload do arquivo se o Netlify ainda não o tiver ──
        if sha1 in required:
            put_url = f"https://api.netlify.com/api/v1/deploys/{deploy_id}/files/index.html"
            put_headers = {
                "Authorization": f"Bearer {NETLIFY_TOKEN}",
                "Content-Type": "application/octet-stream"
            }
            print("📤 A enviar index.html...")
            pr = requests.put(put_url, headers=put_headers, data=conteudo, timeout=180)
            if pr.status_code not in (200, 201):
                print(f"❌ Erro ao enviar arquivo ({pr.status_code}): {pr.text}")
                return
            print("✅ Arquivo enviado.")
        else:
            print("ℹ️  O Netlify já possui esta versão do arquivo.")

        # ── 3) Aguarda o estado "ready" (máx. 3 min) ──
        print("⏳ Aguardando publicação", end="", flush=True)
        check_url = f"https://api.netlify.com/api/v1/deploys/{deploy_id}"
        get_headers = {"Authorization": f"Bearer {NETLIFY_TOKEN}"}
        max_espera, intervalo, decorrido = 180, 5, 0
        while decorrido < max_espera:
            time.sleep(intervalo)
            decorrido += intervalo
            try:
                cr = requests.get(check_url, headers=get_headers, timeout=15)
                if cr.status_code == 200:
                    estado = cr.json().get("state", "?")
                    print(f"\r⏳ Estado: {estado:<12} ({decorrido}s)", end="", flush=True)
                    if estado == "ready":
                        print("\n\n🎉 Publicado com sucesso!")
                        print(f"🌐 Acesse: {site_url}")
                        print("   (dica: se o browser mostrar versão antiga, use Ctrl+Shift+R)\n")
                        return
                    if estado in ("error", "failed"):
                        print(f"\n❌ Deploy falhou: {cr.json().get('error_message', 'sem detalhes')}")
                        return
            except Exception as poll_err:
                print(f"\n⚠️  Erro ao verificar estado: {poll_err}")

        print(f"\n⚠️  Tempo limite atingido ({max_espera}s).")
        print("   Verifique em https://app.netlify.com/sites")
        print(f"   URL: {site_url}")

    except Exception as e:
        print(f"❌ Erro inesperado: {e}")


def extrair_e_gerar_html():
    print("🚀 A iniciar a extração Geral (Pagar e Receber)...\n" + "-" * 60)

    lista_receber = []
    lista_pagar = []
    dados_gerais = {}   # nome_empresa -> {'v_atual','v_anoant','l_atual','l_anoant'}

    # Períodos do acompanhamento (aba Dados Gerais): A/B semana, C/D mês, E/F ano.
    # Cada "lado" (atual / ano anterior) é buscado numa única consulta que já
    # cobre semana + mês + ano daquele lado; os períodos individuais são
    # recortados em pandas a partir do resultado (ver fatiar_por_data).
    periodos = periodos_definidos(date.today())
    ini_atual_geral, lim_atual_geral = limites_janela(periodos, ('A', 'C', 'E'))
    ini_anoant_geral, lim_anoant_geral = limites_janela(periodos, ('B', 'D', 'F'))
    bind_atual = (ini_atual_geral.strftime('%Y-%m-%d'), lim_atual_geral.strftime('%Y-%m-%d'))
    bind_anoant = (ini_anoant_geral.strftime('%Y-%m-%d'), lim_anoant_geral.strftime('%Y-%m-%d'))
    print("📅 Períodos do acompanhamento:")
    for label, (ini, lim) in periodos.items():
        print(f"   {label}) {ini:%d/%m/%Y} a {(lim - timedelta(days=1)):%d/%m/%Y}")
    print()

    # --- 1. EXTRACAO DOS DADOS ---
    for nome_empresa, config in EMPRESAS.items():
        print(f"🔌 A ler Empresa: {nome_empresa}...")
        try:
            con = firebirdsql.connect(host=config["HOST"], database=config["DATABASE"],
                                      port=PORT, user=USER, password=PASSWORD, charset=CHARSET)
            df_r = pd.read_sql(SQL_RECEBER, con)
            df_p = pd.read_sql(SQL_PAGAR, con)
            # Acompanhamento: uma consulta larga por lado (atual / ano anterior),
            # cobrindo semana + mês + ano — os 3 pares são recortados depois.
            df_v_atual   = consultar(con, SQL_VENDAS, bind_atual)
            df_v_anoant  = consultar(con, SQL_VENDAS, bind_anoant)
            df_l_atual   = consultar(con, SQL_LIQUIDACOES, bind_atual)
            df_l_anoant  = consultar(con, SQL_LIQUIDACOES, bind_anoant)
            con.close()

            if not df_r.empty:
                df_r.insert(0, "EMPRESA", nome_empresa)
                lista_receber.append(df_r)
            if not df_p.empty:
                df_p.insert(0, "EMPRESA", nome_empresa)
                lista_pagar.append(df_p)
            if not df_l_atual.empty:
                df_l_atual.insert(0, "EMPRESA", nome_empresa)
            if not df_l_anoant.empty:
                df_l_anoant.insert(0, "EMPRESA", nome_empresa)

            dados_gerais[nome_empresa] = {
                'v_atual': df_v_atual, 'v_anoant': df_v_anoant,
                'l_atual': df_l_atual, 'l_anoant': df_l_anoant,
            }

            print(f"  ✔ {len(df_r)} Contas a Receber | {len(df_p)} Contas a Pagar "
                  f"| {len(df_v_atual) + len(df_v_anoant)} vendas | "
                  f"{len(df_l_atual) + len(df_l_anoant)} liquidações (acompanhamento).")
        except Exception as e:
            print(f"  ❌ Falha: {e}")

    # --- 2. PROCESSAMENTO RECEBER ---
    html_receber = ""
    if lista_receber:
        df_rec = pd.concat(lista_receber, ignore_index=True)
        if os.path.exists(ARQUIVO_BASE_MANUAL):
            try:
                df_manual = pd.read_excel(ARQUIVO_BASE_MANUAL, engine='openpyxl')
                df_manual['CLIENTE_MATCH'] = df_manual['CLIENTES'].astype(str).str.strip().str.upper()
                df_manual['GRUPO'] = df_manual['GRUPO'].astype(str).str.strip().str.capitalize()
                df_manual = df_manual.drop_duplicates(subset=['CLIENTE_MATCH'])
                df_rec['CLIENTE_MATCH'] = df_rec['CLIENTE'].astype(str).str.strip().str.upper()
                df_rec = pd.merge(df_rec, df_manual[['CLIENTE_MATCH', 'GRUPO']], on='CLIENTE_MATCH', how='left')
                df_rec['GRUPO'] = df_rec['GRUPO'].fillna('Não classificado')
                df_rec.drop(columns=['CLIENTE_MATCH'], inplace=True)
            except Exception:
                df_rec['GRUPO'] = 'Não classificado'
        else:
            df_rec['GRUPO'] = 'Não classificado'

        df_rec['VENCIMENTO'] = pd.to_datetime(df_rec['VENCIMENTO_RAW'])
        df_rec['EMISSÃO'] = pd.to_datetime(df_rec['EMISSÃO_RAW'])
        df_rec['SALDO'] = df_rec['VALOR TÍTULO'] - df_rec['VALOR PAGO']
        hoje = pd.Timestamp(datetime.now().date())
        df_rec['CLASSIFICAÇÃO'] = df_rec['VENCIMENTO'].apply(lambda v: classificar_data(v, hoje))

        client_idx = 0
        for (cliente, grupo), group_df in df_rec.groupby(['CLIENTE', 'GRUPO']):
            client_idx += 1
            tot_titulo = group_df['VALOR TÍTULO'].sum()
            tot_pago = group_df['VALOR PAGO'].sum()
            tot_saldo = group_df['SALDO'].sum()
            if tot_saldo <= 0:
                continue

            # Mapa empresa -> código(s) do cliente naquela empresa.
            # O mesmo cliente costuma ter um código diferente em cada empresa,
            # por isso a busca por código precisa saber a qual empresa se refere.
            cods_por_empresa = {}
            for emp_nome, emp_df in group_df.groupby('EMPRESA'):
                codigos = sorted({
                    formatar_codigo(c) for c in emp_df['CÓD. CLIENTE']
                    if formatar_codigo(c)
                })
                if codigos:
                    cods_por_empresa[emp_nome] = " ".join(codigos)
            # formato compacto p/ atributo: "Empresa A=10 20;Empresa B=33"
            data_codigos = ";".join(f"{e}={c}" for e, c in cods_por_empresa.items())

            html_receber += f"""
            <tr class="client-row" onclick="toggleDetails(this, 'c_{client_idx}')" data-nome="{cliente}" data-grupo="{grupo}" data-codigos="{data_codigos}">
                <td><span class="chevron">▾</span><span class="client-name">{cliente}</span></td>
                <td><span class="tag-grupo">{grupo}</span></td>
                <td class="td-saldo">R$ {formatar_moeda(tot_saldo)}</td>
            </tr>
            <tr id="c_{client_idx}" class="details-row" style="display:none;"><td colspan="3"><div class="details-inner"><table class="details-table">
            <thead><tr><th>Empresa</th><th>Cód. Cliente</th><th>Documento</th><th>Vendedor</th><th>Emissão</th><th>Vencimento</th><th>Status</th><th>Situação</th><th>Título</th><th>Pago</th><th>Saldo</th></tr></thead><tbody>
            """
            for _, row in group_df.sort_values('VENCIMENTO_RAW', ascending=True).iterrows():
                classe = row['CLASSIFICAÇÃO']
                badge = badge_classe(classe)
                sclass = sit_classe(row['SITUAÇÃO'])
                data_v = row['VENCIMENTO'].strftime('%d/%m/%Y') if not pd.isna(row['VENCIMENTO']) else '-'
                data_e = row['EMISSÃO'].strftime('%d/%m/%Y') if not pd.isna(row['EMISSÃO']) else '-'
                cod_cli = formatar_codigo(row['CÓD. CLIENTE'])
                vendedor = formatar_vendedor(row['CÓD. VENDEDOR'], row['VENDEDOR'])
                html_receber += f"""
                <tr class="bill-row" data-empresa="{row['EMPRESA']}" data-status="{classe}" data-titulo="{row['VALOR TÍTULO']}" data-pago="{row['VALOR PAGO']}" data-saldo="{row['SALDO']}">
                    <td><strong>{row['EMPRESA']}</strong></td><td class="cod-cell">{cod_cli or '-'}</td><td>{row['DOCUMENTO']}</td><td>{vendedor}</td><td>{data_e}</td><td><strong>{data_v}</strong></td>
                    <td><span class="badge {badge}">{classe}</span></td><td class="{sclass}">{row['SITUAÇÃO']}</td>
                    <td>{formatar_moeda(row['VALOR TÍTULO'])}</td><td>{formatar_moeda(row['VALOR PAGO'])}</td><td>{formatar_moeda(row['SALDO'])}</td>
                </tr>"""
            html_receber += "</tbody></table></div></td></tr>"

    # --- 3. PROCESSAMENTO PAGAR — lista plana ordenada por vencimento ---
    html_pagar = ""
    pag_titulo_total = pag_pago_total = pag_saldo_total = 0.0

    if lista_pagar:
        df_pag = pd.concat(lista_pagar, ignore_index=True)
        df_pag['VENCIMENTO'] = pd.to_datetime(df_pag['VENCIMENTO_RAW'])
        df_pag['EMISSÃO']    = pd.to_datetime(df_pag['EMISSÃO_RAW'])
        df_pag['SALDO']      = df_pag['VALOR TÍTULO'] - df_pag['VALOR PAGO']
        hoje = pd.Timestamp(datetime.now().date())
        df_pag['CLASSIFICAÇÃO'] = df_pag['VENCIMENTO'].apply(lambda v: classificar_data(v, hoje))

        # ordena por data de vencimento (mais próxima primeiro)
        df_pag = df_pag.sort_values('VENCIMENTO_RAW', ascending=True, na_position='last')

        # totais para os cards KPI (calculados em Python, injetados no HTML)
        pag_titulo_total = df_pag['VALOR TÍTULO'].sum()
        pag_pago_total   = df_pag['VALOR PAGO'].sum()
        pag_saldo_total  = df_pag['SALDO'].sum()

        prev_iso      = None
        prev_display  = None
        day_tit = day_pag = day_sal = day_n = 0

        def flush_subtotal(iso, label, n, t, p, s):
            return (
                f'<tr class="subtotal-row" data-dia="{iso}" data-dia-label="{label}">'
                f'<td colspan="7" class="subtotal-label">{label} &nbsp;·&nbsp; {n} lançamento{"" if n == 1 else "s"}</td>'
                f'<td class="num-cell subtotal-num">{formatar_moeda(t)}</td>'
                f'<td class="num-cell subtotal-num">{formatar_moeda(p)}</td>'
                f'<td class="num-cell subtotal-num">{formatar_moeda(s)}</td>'
                f'</tr>'
            )

        for _, row in df_pag.iterrows():
            classe     = row['CLASSIFICAÇÃO']
            badge      = badge_classe(classe)
            sclass     = sit_classe(row['SITUAÇÃO'])
            data_v     = row['VENCIMENTO'].strftime('%d/%m/%Y') if not pd.isna(row['VENCIMENTO']) else '-'
            data_v_iso = row['VENCIMENTO'].strftime('%Y-%m-%d') if not pd.isna(row['VENCIMENTO']) else '9999-12-31'
            data_e     = row['EMISSÃO'].strftime('%d/%m/%Y')    if not pd.isna(row['EMISSÃO'])    else '-'

            # subtotal do dia anterior quando a data muda
            if prev_iso is not None and data_v_iso != prev_iso:
                html_pagar += flush_subtotal(prev_iso, prev_display, day_n, day_tit, day_pag, day_sal)
                day_tit = day_pag = day_sal = day_n = 0

            prev_iso     = data_v_iso
            prev_display = data_v
            day_tit += row['VALOR TÍTULO']
            day_pag += row['VALOR PAGO']
            day_sal += row['SALDO']
            day_n   += 1

            html_pagar += (
                f'<tr class="pagar-row" data-empresa="{row["EMPRESA"]}" data-data="{data_v_iso}"'
                f' data-titulo="{row["VALOR TÍTULO"]}" data-pago="{row["VALOR PAGO"]}" data-saldo="{row["SALDO"]}">'
                f'<td>{data_v}</td>'
                f'<td>{row["FORNECEDOR"]}</td>'
                f'<td>{row["EMPRESA"]}</td>'
                f'<td>{row["DOCUMENTO"]}</td>'
                f'<td>{data_e}</td>'
                f'<td><span class="badge {badge}">{classe}</span></td>'
                f'<td class="{sclass}">{row["SITUAÇÃO"]}</td>'
                f'<td class="num-cell">{formatar_moeda(row["VALOR TÍTULO"])}</td>'
                f'<td class="num-cell">{formatar_moeda(row["VALOR PAGO"])}</td>'
                f'<td class="num-cell saldo-cell">{formatar_moeda(row["SALDO"])}</td>'
                f'</tr>'
            )

        # subtotal do último dia
        if prev_iso is not None:
            html_pagar += flush_subtotal(prev_iso, prev_display, day_n, day_tit, day_pag, day_sal)

    # --- 3b. DADOS GERAIS — 3 subabas (semana / mês / ano), cada uma com
    # receita por forma de pagamento + liquidações agrupadas por cliente.
    ger = {}
    for suf, chave_a, chave_b, _, _ in SUBABAS_GERAIS:
        janela_a, janela_b = periodos[chave_a], periodos[chave_b]
        receita_por_empresa = {
            nome: montar_receita(d['v_atual'], janela_a, d['v_anoant'], janela_b,
                           d['l_atual'], d['l_anoant'])
            for nome, d in dados_gerais.items()
        }
        html_receita, tot_a, tot_b, var_txt = montar_html_receita(receita_por_empresa)

        if suf == 'semana':
            html_liq = montar_html_liq_semana(dados_gerais, janela_a, janela_b, f"liq_{suf}")
        else:
            html_liq = montar_html_liq_agrupado(dados_gerais, janela_a, janela_b, f"liq_{suf}")

        ger[suf] = {
            'html_receita': html_receita,
            'html_liq': html_liq,
            'tot_a': tot_a, 'tot_b': tot_b, 'var_txt': var_txt,
            'periodo_a_label': f"{janela_a[0]:%d/%m/%Y} a {(janela_a[1] - timedelta(days=1)):%d/%m/%Y}",
            'periodo_b_label': f"{janela_b[0]:%d/%m/%Y} a {(janela_b[1] - timedelta(days=1)):%d/%m/%Y}",
        }

    data_geracao = datetime.now().strftime('%d/%m/%Y às %H:%M')

    # --- 4. TEMPLATE HTML (placeholders simples, sem escape de chaves) ---
    html_template = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Painel ATS — Grupo Dluz</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,500;6..72,600&family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --navy: #0f172a; --navy2: #1e293b; --navy3: #334155;
    --blue: #2563eb; --blue-light: #dbeafe;
    --red: #dc2626; --red-light: #fee2e2;
    --green: #16a34a; --green-light: #dcfce7;
    --orange: #d97706; --orange-light: #fef3c7;
    --gray: #64748b; --gray-light: #f1f5f9; --gray-border: #e2e8f0;
    --white: #ffffff; --bg: #f0f4f8;
    --radius: 12px;
    --shadow: 0 1px 3px rgba(0,0,0,0.07), 0 4px 16px rgba(0,0,0,0.06);
    --mono: 'IBM Plex Mono', monospace; --sans: 'IBM Plex Sans', system-ui, sans-serif;
  }
  html, body { height: 100%; }
  body { font-family: var(--sans); background: var(--bg); color: var(--navy); }

  .app { display: flex; min-height: 100vh; }

  /* ── SIDEBAR (desktop) ── */
  .sidebar {
    width: 250px; background: var(--navy); color: #fff;
    display: flex; flex-direction: column; flex-shrink: 0;
    position: sticky; top: 0; height: 100vh;
  }
  .sidebar-header { padding: 24px 22px; border-bottom: 1px solid #1e293b; }
  .sidebar-header h2 { font-size: 20px; font-weight: 800; letter-spacing: 0.5px; color: #fff; }
  .sidebar-header span { font-size: 11px; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; }
  .sidebar-menu { flex: 1; padding-top: 16px; }
  .sidebar-menu a {
    display: flex; align-items: center; gap: 12px;
    padding: 13px 22px; color: #94a3b8; text-decoration: none;
    font-size: 14px; font-weight: 600; cursor: pointer;
    border-left: 3px solid transparent; transition: all 0.2s;
  }
  .sidebar-menu a:hover { background: #1e293b; color: #e2e8f0; }
  .sidebar-menu a.active { background: #1e293b; color: #fff; border-left-color: var(--blue); }
  .sidebar-menu a .ico { font-size: 16px; }
  .sidebar-menu a.tab-locked::after { content: '🔒'; margin-left: auto; font-size: 11px; opacity: .6; }
  .mobile-nav a.tab-locked { opacity: .55; }
  .sidebar-footer { padding: 16px 22px; font-size: 11px; color: #475569; border-top: 1px solid #1e293b; line-height: 1.5; }

  /* ── MAIN ── */
  .main-content { flex: 1; min-width: 0; overflow-x: hidden; }
  .tab-content { display: none; animation: fadeIn 0.35s ease; }
  .tab-content.active { display: block; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }

  .page-head { background: var(--navy); color: #fff; padding: 22px 28px 20px; }
  .page-head h1 { font-size: clamp(15px, 2.6vw, 20px); font-weight: 800; letter-spacing: -0.3px; }
  .title-wrap { display: flex; flex-direction: column; gap: 5px; margin-bottom: 16px; }
  .update-badge { font-size: 11.5px; font-weight: 600; color: inherit; opacity: .6; }

  .controls { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .search-wrap { position: relative; flex: 2; min-width: 180px; }
  .search-icon { position: absolute; left: 12px; top: 50%; transform: translateY(-50%); color: #64748b; font-size: 14px; pointer-events: none; }
  .pesquisa {
    width: 100%; padding: 10px 12px 10px 36px; font-size: 14px; font-family: var(--sans);
    background: #1e293b; border: 1px solid #334155; border-radius: 8px; color: #fff; outline: none; transition: border-color 0.2s;
  }
  .pesquisa::placeholder { color: #64748b; }
  .pesquisa:focus { border-color: var(--blue); }
  .select-filtro {
    flex: 1; min-width: 130px; padding: 10px 32px 10px 12px; font-size: 13px; font-family: var(--sans); font-weight: 600;
    background: #1e293b; border: 1px solid #334155; border-radius: 8px; color: #e2e8f0; cursor: pointer; outline: none;
    appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='7' viewBox='0 0 12 7'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%2394a3b8' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
    background-repeat: no-repeat; background-position: right 12px center; transition: border-color 0.2s;
  }
  .select-filtro:focus { border-color: var(--blue); }
  .status-group {
    display: flex; align-items: center; gap: 6px; flex: 1 1 360px; min-width: 300px; flex-wrap: wrap;
  }
  .status-option {
    display: inline-flex; align-items: center; gap: 6px; padding: 8px 10px;
    background: #1e293b; border: 1px solid #334155; border-radius: 8px;
    color: #e2e8f0; font-size: 12px; font-weight: 700; cursor: pointer; user-select: none;
  }
  .status-option input { accent-color: var(--blue); width: 14px; height: 14px; }
  .date-group {
    display: flex; align-items: center; gap: 6px; background: #1e293b; border: 1px solid #334155;
    border-radius: 8px; padding: 4px 10px; flex-wrap: wrap;
  }
  .date-group .date-label { font-size: 10px; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; }
  .date-group input[type=date] {
    background: transparent; border: none; color: #e2e8f0; font-family: var(--sans); font-size: 12px; outline: none;
    color-scheme: dark; padding: 5px 2px;
  }
  .date-group span { color: #64748b; font-size: 12px; }

  /* ── GRUPO CÓDIGO DO CLIENTE (ocupa a própria linha p/ não cortar) ── */
  .code-group {
    display: flex; align-items: center; gap: 8px; background: #1e293b; border: 1px solid #334155;
    border-radius: 8px; padding: 4px 8px 4px 12px; flex: 1 1 100%; min-width: 0;
  }
  .code-label { font-size: 10px; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; white-space: nowrap; flex-shrink: 0; }
  .select-cod {
    flex: 0 1 auto; min-width: 110px; padding: 7px 26px 7px 8px; font-size: 12px; font-family: var(--sans); font-weight: 600;
    background: #0f172a; border: 1px solid #334155; border-radius: 6px; color: #e2e8f0; cursor: pointer; outline: none;
    appearance: none; text-overflow: ellipsis;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='7' viewBox='0 0 12 7'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%2394a3b8' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
    background-repeat: no-repeat; background-position: right 8px center; transition: border-color 0.2s;
  }
  .select-cod:focus { border-color: var(--blue); }
  .pesquisa-cod {
    flex: 1 1 120px; min-width: 0; width: 100%; padding: 7px 10px; font-size: 13px; font-family: var(--mono);
    background: #0f172a; border: 1px solid #334155; border-radius: 6px; color: #fff; outline: none; transition: border-color 0.2s;
  }
  .pesquisa-cod::placeholder { color: #64748b; font-family: var(--sans); }
  .pesquisa-cod:focus { border-color: var(--blue); }
  .cod-cell { font-family: var(--mono); font-size: 12px; color: var(--navy2); white-space: nowrap; }

  .content { padding: 20px 16px 28px; max-width: 1400px; margin: 0 auto; }

  /* ── KPI CARDS ── */
  .cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 20px; }
  .card { background: var(--white); border-radius: var(--radius); padding: 18px 20px; box-shadow: var(--shadow); border-top: 4px solid var(--blue); display: flex; flex-direction: column; gap: 6px; }
  .card.c-mid { border-top-color: var(--orange); }
  .card.c-end { border-top-color: var(--red); }
  .card h3 { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.6px; color: var(--gray); }
  .card p { font-family: var(--mono); font-size: clamp(16px, 3.2vw, 22px); font-weight: 500; color: var(--navy); line-height: 1.1; }
  .card.c-end p { color: var(--red); }

  /* ── TABLE ── */
  .table-wrapper { background: var(--white); border-radius: var(--radius); box-shadow: var(--shadow); overflow: hidden; }
  .table-toolbar { padding: 14px 20px; border-bottom: 1px solid var(--gray-border); display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .table-title { font-size: 14px; font-weight: 700; }
  .count-badge { font-size: 12px; font-weight: 600; background: var(--blue-light); color: var(--blue); padding: 3px 10px; border-radius: 20px; white-space: nowrap; }
  .table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  table { width: 100%; border-collapse: collapse; min-width: 480px; }
  th { background: var(--navy); color: #e2e8f0; padding: 12px 16px; text-align: left; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; white-space: nowrap; }
  th:first-child { padding-left: 20px; } th:last-child { padding-right: 20px; }
  td { padding: 14px 16px; border-bottom: 1px solid var(--gray-border); font-size: 14px; vertical-align: middle; }
  td:first-child { padding-left: 20px; } td:last-child { padding-right: 20px; }

  .client-row, .forn-row, .liqc-row { cursor: pointer; transition: background 0.15s; }
  .client-row:hover, .forn-row:hover, .liqc-row:hover { background: #f8fafc; }
  .client-row td:first-child, .forn-row td:first-child, .liqc-row td:first-child { display: flex; align-items: center; gap: 10px; }
  .client-name { font-weight: 700; font-size: 13.5px; }
  .chevron { width: 20px; height: 20px; border-radius: 50%; background: var(--gray-light); display: flex; align-items: center; justify-content: center; flex-shrink: 0; transition: transform 0.2s, background 0.2s; font-size: 9px; color: var(--gray); }
  .client-row.open .chevron, .forn-row.open .chevron, .liqc-row.open .chevron { transform: rotate(180deg); background: var(--blue-light); color: var(--blue); }
  .liqc-n { font-family: var(--mono); font-size: 13px; color: var(--navy2); }
  .liqc-total { font-family: var(--mono); font-size: 13px; font-weight: 700; color: var(--navy); }

  .tag-grupo { display: inline-block; background: var(--gray-light); color: var(--gray); padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 700; white-space: nowrap; }
  .td-titulo, .td-pago { font-family: var(--mono); font-size: 13px; color: var(--navy2); white-space: nowrap; }
  .td-saldo { font-family: var(--mono); font-size: 13px; font-weight: 700; color: var(--red); white-space: nowrap; }

  .details-row td { padding: 0; background: #f8fafc; }
  .details-inner { padding: 12px 16px 16px; overflow-x: auto; }
  .details-table { min-width: 820px; border-radius: 8px; overflow: hidden; border: 1px solid var(--gray-border); }
  .details-table th { background: var(--navy2); color: #94a3b8; font-size: 10px; padding: 9px 12px; }
  .details-table td { font-size: 12.5px; padding: 10px 12px; background: var(--white); border-bottom: 1px solid #f1f5f9; }
  .details-table tr:last-child td { border-bottom: none; }

  .badge { padding: 3px 9px; border-radius: 20px; font-size: 10.5px; font-weight: 700; display: inline-block; white-space: nowrap; min-width: 68px; text-align: center; }
  .badge-vencer { background: var(--blue-light); color: #1d4ed8; }
  .badge-emdia { background: var(--green-light); color: #15803d; }
  .badge-antigo { background: var(--orange-light); color: #b45309; }
  .badge-prescrito { background: var(--red-light); color: #b91c1c; }
  .sit-aberto { color: var(--red); font-weight: 700; font-size: 12px; }
  .sit-parcial { color: var(--orange); font-weight: 700; font-size: 12px; }

  .empty-state { display: none; text-align: center; padding: 44px 24px; color: var(--gray); }
  .empty-state svg { margin-bottom: 12px; opacity: 0.3; }
  .empty-state p { font-size: 14px; font-weight: 600; }

  .placeholder-box { background: var(--white); padding: 48px 24px; text-align: center; border-radius: var(--radius); box-shadow: var(--shadow); }
  .placeholder-box h2 { font-size: 18px; margin-bottom: 8px; }
  .placeholder-box p { color: var(--gray); font-size: 14px; }

  /* ── PAGE HEAD ROW (título + botão) ── */
  .page-head-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 0; }

  /* ── BOTÃO IMPRIMIR ── */
  .print-btn {
    display: inline-flex; align-items: center; gap: 7px;
    padding: 9px 18px; font-family: var(--sans); font-size: 13px; font-weight: 700;
    background: #fff; color: var(--navy); border: none; border-radius: 8px; cursor: pointer;
    transition: background 0.15s, transform 0.1s; white-space: nowrap;
  }
  .print-btn:hover { background: #e2e8f0; }
  .print-btn:active { transform: scale(0.97); }
  .clear-btn {
    padding: 9px 14px; font-family: var(--sans); font-size: 12px; font-weight: 700;
    background: transparent; color: #94a3b8; border: 1px solid #334155; border-radius: 8px;
    cursor: pointer; transition: all 0.15s; white-space: nowrap;
  }
  .clear-btn:hover { background: #1e293b; color: #e2e8f0; }

  /* resumo do filtro — só aparece na impressão */
  .print-info { display: none; }

  /* ── TABELA PLANA PAGAR ── */
  .pagar-table td { font-size: 13px; }
  .num-cell { font-family: var(--mono); font-size: 12.5px; text-align: right; white-space: nowrap; }
  .saldo-cell { color: var(--red); font-weight: 700; }

  /* ── DADOS GERAIS (acompanhamento) ── */
  .ger-subnav { display: flex; gap: 6px; margin-top: 14px; flex-wrap: wrap; }
  .ger-subnav-btn {
    padding: 8px 16px; font-family: var(--sans); font-size: 12.5px; font-weight: 700;
    background: #1e293b; border: 1px solid #334155; border-radius: 20px; color: #94a3b8;
    cursor: pointer; transition: all 0.15s;
  }
  .ger-subnav-btn:hover { background: #334155; color: #e2e8f0; }
  .ger-subnav-btn.active { background: var(--blue); border-color: var(--blue); color: #fff; }
  .ger-subpanel { display: none; }
  .ger-subpanel.active { display: block; }
  .periodo-chips { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
  .periodo-chip {
    display: flex; align-items: baseline; gap: 7px; background: var(--white); border: 1px solid var(--gray-border);
    border-radius: 8px; padding: 8px 12px; white-space: nowrap; box-shadow: var(--shadow);
  }
  .periodo-chip .date-label { font-size: 10px; font-weight: 700; color: var(--gray); text-transform: uppercase; letter-spacing: 0.5px; }
  .periodo-chip strong { font-family: var(--mono); font-size: 12px; font-weight: 600; color: var(--navy); }
  .toolbar-right { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .select-liq {
    padding: 6px 28px 6px 10px; font-family: var(--sans); font-size: 12px; font-weight: 600;
    background: var(--white); border: 1px solid var(--gray-border); border-radius: 7px;
    color: var(--navy2); cursor: pointer; outline: none; appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='7' viewBox='0 0 12 7'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%237c7468' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
    background-repeat: no-repeat; background-position: right 9px center; transition: border-color 0.2s;
  }
  .gerais-table td, .liq-table td { font-size: 13px; }
  .ger-forma { font-weight: 600; font-size: 13px; }
  .gerais-table .subtotal-row .ger-forma, .gerais-table .total-row .ger-forma { font-weight: 700; }
  .total-row td { border-top: 2px solid var(--navy3); border-bottom: none; font-weight: 700; }
  .total-row .num-cell { font-size: 13.5px; font-weight: 700; }
  .var-up { color: #15803d; font-weight: 700; }
  .var-down { color: var(--red); font-weight: 700; }
  .saldo-recebido { font-weight: 700; }
  .stack-tables > .table-wrapper + .table-wrapper { margin-top: 20px; }

  /* ── MOBILE BOTTOM NAV ── */
  .mobile-nav { display: none; }

  /* ── SUBTOTAL POR DIA ── */
  .subtotal-row { background: #f1f5f9; }
  .subtotal-row td { border-top: 2px solid #cbd5e1; border-bottom: 2px solid #cbd5e1; }
  .subtotal-label { font-size: 12px; font-weight: 700; color: var(--navy2); padding-left: 16px !important; }
  .subtotal-num { font-family: var(--mono); font-size: 12px; font-weight: 700; color: var(--navy); }

  /* ── OVERLAY DE LOGIN ── */
  #login-overlay {
    position: fixed; inset: 0; z-index: 9999;
    background: rgba(15,23,42,0.97);
    display: flex; align-items: center; justify-content: center;
  }
  .login-box {
    background: #1e293b; border: 1px solid #334155; border-radius: 16px;
    padding: 40px 36px; width: 320px; text-align: center;
    box-shadow: 0 24px 48px rgba(0,0,0,0.4);
  }
  .login-box h2 { color: #fff; font-size: 20px; font-weight: 800; margin-bottom: 6px; }
  .login-box p  { color: #64748b; font-size: 13px; margin-bottom: 24px; }
  .login-input {
    width: 100%; padding: 12px 14px; font-family: var(--sans); font-size: 15px;
    background: #0f172a; border: 1px solid #334155; border-radius: 8px; color: #fff;
    outline: none; text-align: center; letter-spacing: 3px; margin-bottom: 14px;
    transition: border-color 0.2s;
  }
  .login-input:focus { border-color: var(--blue); }
  .login-btn {
    width: 100%; padding: 12px; font-family: var(--sans); font-size: 14px; font-weight: 700;
    background: var(--blue); color: #fff; border: none; border-radius: 8px; cursor: pointer;
    transition: background 0.2s;
  }
  .login-btn:hover { background: #1d4ed8; }
  .login-erro { color: #f87171; font-size: 12px; font-weight: 600; margin-top: 10px; min-height: 18px; }

  /* ── IMPRESSÃO COMPACTA ── */
  @media print {
    @page { size: landscape; margin: 8mm; }
    * { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
    body { background: white; font-size: 8px; }
    #login-overlay, .sidebar, .mobile-nav, .print-btn, .clear-btn,
    .controls, .table-toolbar { display: none !important; }
    .app { display: block; }
    .main-content { display: block; }
    .tab-content:not(.active) { display: none !important; }
    .tab-content.active { display: block !important; }
    .page-head {
      background: white !important; color: var(--navy) !important;
      padding: 0 0 6px !important; box-shadow: none !important;
    }
    .page-head h1 { font-size: 12px; font-weight: 800; color: var(--navy) !important; margin-bottom: 2px; }
    .title-wrap { margin-bottom: 2px; gap: 1px; }
    .update-badge { font-size: 7px !important; color: var(--navy) !important; opacity: .7; }
    .print-info {
      display: block !important; font-size: 8.5px; font-weight: 600;
      color: #334155; margin: 2px 0 6px; padding-bottom: 4px; border-bottom: 1px solid #cbd5e1;
    }
    .content { padding: 4px 0 0 !important; }
    /* totais do filtro aparecem compactos na impressão */
    .cards { display: grid !important; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 8px; }
    .card { box-shadow: none !important; border: 1px solid #999 !important; border-left: 3px solid var(--blue) !important; padding: 5px 10px !important; border-radius: 3px; flex-direction: column !important; }
    .card.c-mid { border-left-color: var(--orange) !important; }
    .card.c-end { border-left-color: var(--red) !important; }
    .card h3 { font-size: 6.5px !important; margin: 0; }
    .card p { font-size: 11px !important; }
    .table-wrapper { box-shadow: none !important; border: none; }
    table { font-size: 8px !important; min-width: unset; border-collapse: collapse; }
    th {
      background: var(--navy) !important; color: white !important;
      padding: 4px 6px !important; font-size: 7.5px !important;
    }
    td { padding: 3px 6px !important; border-bottom: 1px solid #e2e8f0; }
    .num-cell { font-size: 7.5px !important; }
    .badge {
      font-size: 7px !important; padding: 1px 4px !important;
      background: none !important; color: inherit !important;
      border: 1px solid currentColor; border-radius: 3px;
    }
    .badge-vencer  { color: #1d4ed8 !important; }
    .badge-emdia   { color: #15803d !important; }
    .badge-antigo  { color: #b45309 !important; }
    .badge-prescrito { color: #b91c1c !important; }
    .subtotal-row td { padding: 3px 6px !important; }
    .subtotal-label { font-size: 8px !important; font-weight: 800 !important; }
    .subtotal-num   { font-size: 8px !important; font-weight: 800 !important; }
    .sit-aberto, .sit-parcial { font-size: 7.5px !important; }
  }

  @media (max-width: 768px) {
    .app { display: block; }
    .sidebar { display: none; }
    .main-content { padding-bottom: 76px; }
    .page-head { padding: 16px 16px 14px; }
    .controls { flex-direction: column; }
    .search-wrap, .select-filtro, .status-group { min-width: 100%; flex: unset; width: 100%; }
    .status-option { flex: 1 1 calc(50% - 6px); justify-content: center; }
    .date-group { width: 100%; justify-content: space-between; }
    .code-group { width: 100%; min-width: 100%; flex: unset; }
    .content { padding: 14px 12px 24px; }
    .cards { grid-template-columns: 1fr; gap: 10px; }
    .card { padding: 14px 16px; flex-direction: row; align-items: center; justify-content: space-between; border-top: none; border-left: 4px solid var(--blue); }
    .card.c-mid { border-top: none; border-left-color: var(--orange); }
    .card.c-end { border-top: none; border-left-color: var(--red); }
    .card h3 { font-size: 10px; } .card p { font-size: 16px; }
    th, td { padding: 11px 10px; }
    th:first-child, td:first-child { padding-left: 12px; }
    .client-name { font-size: 12.5px; }

    .mobile-nav {
      display: flex; position: fixed; bottom: 0; left: 0; right: 0; z-index: 200;
      background: var(--navy); border-top: 1px solid #1e293b; box-shadow: 0 -2px 12px rgba(0,0,0,0.2);
    }
    .mobile-nav a {
      flex: 1; display: flex; flex-direction: column; align-items: center; gap: 3px;
      padding: 9px 4px; color: #64748b; text-decoration: none; cursor: pointer; font-size: 10px; font-weight: 600;
    }
    .mobile-nav a .ico { font-size: 19px; }
    .mobile-nav a.active { color: #60a5fa; }
  }
</style>
<style id="dluz-reskin">
  /* Grupo Dluz — Editorial Premium
     Creme quente, grafite profundo, acento âmbar e números tabulares. */
  :root {
    --sans: 'IBM Plex Sans', system-ui, sans-serif;
    --mono: 'IBM Plex Mono', monospace;
    --serif: 'Newsreader', Georgia, serif;
    --bg: #f7f3ea;
    --white: #fffdf8;
    --gray: #7c7468;
    --gray-light: #f0e8da;
    --gray-border: #e9e0d0;
    --navy: #241f18;
    --navy2: #332c22;
    --navy3: #4a4034;
    --blue: #946517;
    --orange: #b88a2c;
    --radius: 15px;
    --shadow: 0 1px 2px rgba(60,46,20,.05), 0 16px 34px -18px rgba(60,46,20,.24);
    --accent: #946517;
    --accent-strong: #74500f;
    --accent-soft-bg: #f0e5cd;
    --accent-soft-text: #74500f;
    --gold2: #b3852a;
    --money: #2c2620;
    --red: #b3261e;
    --red-light: #f7e2de;
    --side-bg: #f0e7d6;
    --side-text: #6b6253;
    --side-text-dim: #a89e88;
    --side-active-bg: #e6dabf;
    --side-active-text: #2c2620;
    --side-border: #e1d6bf;
    --field-bg: #241f18;
    --field-border: #4a4034;
    --field-text: #faf6ef;
    --field-placeholder: #b3a892;
    --header-bg: #241f18;
    --header-text: #fbf7f0;
    --th-bg: #241f18;
    --th-text: #d8cfbd;
    --row-hover: #fbf6ec;
    --detail-bg: #fbf6ec;
    --subtotal-bg: #f3ebda;
    --subtotal-line: #ddd1bb;
    --login-overlay: rgba(36,31,24,.97);
    --login-box-bg: #241f18;
    --login-input-bg: #16120c;
    --login-text: #fbf7f0;
    --bv-bg: #e7edf2;
    --bv-tx: #3a5a78;
    --be-bg: #e4efe1;
    --be-tx: #3d6a3d;
    --ba-bg: #f0e5cd;
    --ba-tx: #7e5413;
    --bp-bg: #f7e2de;
    --bp-tx: #a8281f;
  }
  body {
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }
  .num-cell, .subtotal-num, .card p, .td-titulo, .td-pago, .td-saldo,
  .login-input, .details-table td:last-child {
    font-variant-numeric: tabular-nums;
    font-feature-settings: "tnum" 1;
  }
  .page-head {
    background: var(--header-bg);
    color: var(--header-text);
  }
  .page-head h1,
  .placeholder-box h2,
  .login-box h2 {
    font-family: var(--serif);
    font-weight: 500;
    letter-spacing: 0;
  }
  .page-head h1 {
    color: var(--header-text);
    font-size: clamp(18px, 3vw, 25px);
  }
  .sidebar {
    background: var(--side-bg);
    color: var(--side-text);
    border-right: 1px solid var(--side-border);
  }
  .sidebar-header {
    border-bottom-color: var(--side-border);
  }
  .sidebar-header h2 {
    color: var(--side-active-text);
    font-family: var(--serif);
    font-weight: 600;
    letter-spacing: 0;
  }
  .sidebar-header span {
    color: var(--side-text-dim);
  }
  .sidebar-menu a {
    color: var(--side-text);
  }
  .sidebar-menu a:hover {
    background: var(--side-active-bg);
    color: var(--side-active-text);
  }
  .sidebar-menu a.active {
    background: var(--side-active-bg);
    color: var(--side-active-text);
    border-left-color: var(--accent);
    box-shadow: inset 3px 0 0 var(--accent);
  }
  .sidebar-footer {
    color: var(--side-text-dim);
    border-top-color: var(--side-border);
  }
  .sidebar-footer strong {
    color: var(--side-text) !important;
  }
  .pesquisa, .select-filtro, .date-group, .code-group, .status-option {
    background: var(--field-bg);
    border-color: var(--field-border);
  }
  .ger-subnav-btn {
    background: var(--field-bg);
    border-color: var(--field-border);
    color: var(--field-placeholder);
  }
  .ger-subnav-btn:hover { background: var(--field-border); color: var(--header-text); }
  .ger-subnav-btn.active { background: var(--accent); border-color: var(--accent); color: #fff; }
  .periodo-chip {
    background: var(--white);
    border-color: var(--gray-border);
  }
  .periodo-chip .date-label { color: var(--gray); }
  .periodo-chip strong { color: var(--money); }
  .select-liq {
    background-color: var(--white);
    border-color: var(--gray-border);
    color: var(--money);
  }
  .select-liq:focus { border-color: var(--accent); }
  .var-up { color: var(--be-tx); }
  .var-down { color: var(--red); }
  .total-row td { border-top-color: var(--accent); background: var(--accent-soft-bg); }
  .total-row .ger-forma, .total-row .num-cell { color: var(--accent-soft-text); }
  .saldo-recebido { color: var(--be-tx); }
  .pesquisa, .select-filtro, .date-group input[type=date],
  .select-cod, .pesquisa-cod, .status-option {
    color: var(--field-text);
  }
  .pesquisa::placeholder, .pesquisa-cod::placeholder {
    color: var(--field-placeholder);
  }
  .search-icon, .date-group .date-label, .date-group span, .code-label {
    color: var(--field-placeholder);
  }
  .search-icon {
    display: flex;
    align-items: center;
  }
  .pesquisa:focus, .select-filtro:focus, .select-cod:focus, .pesquisa-cod:focus {
    border-color: var(--accent);
  }
  .select-cod, .pesquisa-cod {
    background: var(--login-input-bg);
    border-color: var(--field-border);
  }
  .status-option {
    transition: background .15s, border-color .15s, color .15s;
  }
  .status-option input {
    accent-color: var(--accent);
  }
  .status-option:has(input:checked) {
    background: var(--accent-soft-bg);
    border-color: var(--accent);
    color: var(--accent-soft-text);
  }
  .print-btn {
    background: var(--accent);
    color: #fff;
    box-shadow: 0 1px 2px rgba(0,0,0,.15);
  }
  .print-btn:hover {
    background: var(--accent-strong);
  }
  .clear-btn {
    color: var(--field-text);
    border-color: var(--field-border);
    background: transparent;
  }
  .clear-btn:hover {
    background: var(--field-border);
    color: var(--header-text);
  }
  th {
    background: var(--th-bg);
    color: var(--th-text);
    letter-spacing: .6px;
  }
  td {
    border-bottom-color: var(--gray-border);
  }
  .table-wrapper {
    border: 1px solid var(--gray-border);
  }
  .table-title {
    color: var(--money);
    font-family: var(--serif);
    font-weight: 500;
    font-size: 17px;
  }
  .client-row:hover, .forn-row:hover, .liqc-row:hover {
    background: var(--row-hover);
  }
  .liqc-n, .liqc-total {
    color: var(--money);
  }
  .details-row td {
    background: var(--detail-bg);
  }
  .details-table th {
    background: var(--th-bg);
    color: var(--th-text);
  }
  .details-table td {
    background: var(--white);
    border-bottom-color: var(--gray-border);
  }
  .chevron {
    background: var(--accent-soft-bg);
    color: var(--accent-soft-text);
  }
  .client-row.open .chevron, .forn-row.open .chevron, .liqc-row.open .chevron {
    background: var(--accent);
    color: #fff;
  }
  .tag-grupo {
    background: var(--gray-light);
    color: var(--gray);
  }
  .card {
    border: 1px solid var(--gray-border);
    border-top: 4px solid var(--accent);
    box-shadow: var(--shadow);
  }
  .card.c-mid {
    border-top-color: var(--gold2);
  }
  .card.c-end {
    border-top-color: var(--red);
  }
  .card h3 {
    color: var(--gray);
  }
  .card p {
    color: var(--money);
    font-size: clamp(18px, 3.4vw, 25px);
    font-weight: 500;
    letter-spacing: 0;
  }
  .card.c-end p {
    color: var(--red);
  }
  .count-badge {
    background: var(--accent-soft-bg);
    color: var(--accent-soft-text);
    font-family: var(--mono);
  }
  .td-titulo, .td-pago, .num-cell {
    color: var(--money);
  }
  .td-saldo, .saldo-cell {
    color: var(--red);
  }
  .badge-vencer {
    background: var(--bv-bg);
    color: var(--bv-tx);
  }
  .badge-emdia {
    background: var(--be-bg);
    color: var(--be-tx);
  }
  .badge-antigo {
    background: var(--ba-bg);
    color: var(--ba-tx);
  }
  .badge-prescrito {
    background: var(--bp-bg);
    color: var(--bp-tx);
  }
  .sit-aberto {
    color: var(--red);
  }
  .sit-parcial {
    color: var(--gold2);
  }
  .subtotal-row {
    background: var(--subtotal-bg);
  }
  .subtotal-row td {
    border-top-color: var(--subtotal-line);
    border-bottom-color: var(--subtotal-line);
  }
  .subtotal-label {
    color: var(--header-text);
  }
  .subtotal-num {
    color: var(--money);
  }
  #login-overlay {
    background: var(--login-overlay);
  }
  .login-box {
    background: var(--login-box-bg);
    border-color: var(--field-border);
  }
  .login-lock {
    width: 54px;
    height: 54px;
    margin: 0 auto 16px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--accent-soft-bg);
    color: var(--accent);
  }
  .login-box h2 {
    color: var(--login-text);
  }
  .login-box p {
    color: var(--field-placeholder);
  }
  .login-input {
    background: var(--login-input-bg);
    border-color: var(--field-border);
    color: var(--login-text);
  }
  .login-input:focus {
    border-color: var(--accent);
  }
  .login-btn {
    background: var(--accent);
  }
  .login-btn:hover {
    background: var(--accent-strong);
  }
  .login-erro {
    color: #fca5a5;
  }
  .mobile-nav {
    background: var(--side-bg);
    border-top-color: var(--side-border);
  }
  .mobile-nav a {
    color: var(--side-text);
  }
  .mobile-nav a.active {
    color: var(--accent);
  }
  .placeholder-box h2 {
    color: var(--money);
  }
  @media (max-width: 768px) {
    .card {
      border-top: 1px solid var(--gray-border);
      border-left: 4px solid var(--accent);
    }
    .card.c-mid {
      border-left-color: var(--gold2);
    }
    .card.c-end {
      border-left-color: var(--red);
    }
  }
</style>
</head>
<body>
<!-- ════ LOGIN ════ -->
<div id="login-overlay">
  <div class="login-box">
    <div class="login-lock">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
        <rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>
      </svg>
    </div>
    <h2>Painel ATS</h2>
    <p id="login-sub">Grupo Dluz · Acesso restrito</p>
    <input type="password" id="senha-input" class="login-input"
           placeholder="••••••••" onkeydown="if(event.key==='Enter') verificarSenha()">
    <button class="login-btn" onclick="verificarSenha()">Entrar</button>
    <div class="login-erro" id="login-erro"></div>
  </div>
</div>

<div class="app">

  <aside class="sidebar">
    <div class="sidebar-header">
      <h2>GRUPO DLUZ</h2>
      <span>BI &amp; Analytics</span>
    </div>
    <nav class="sidebar-menu">
      <a id="nav-pagar" onclick="showTab('pagar')"><svg class="ico" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 17 13.5 8.5 8.5 13.5 2 7"/><polyline points="16 17 22 17 22 11"/></svg> Contas a Pagar</a>
      <a id="nav-receber" class="active" onclick="showTab('receber')"><svg class="ico" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg> Contas a Receber</a>
      <a id="nav-gerais" onclick="showTab('gerais')"><svg class="ico" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg> Dados Gerais</a>
    </nav>
    <div class="sidebar-footer">Atualizado em<br><strong style="color:#94a3b8;">__DATA_GERACAO__</strong></div>
  </aside>

  <div class="main-content">

    <!-- ════ CONTAS A PAGAR ════ -->
    <section id="pagar" class="tab-content">
      <div class="page-head">
        <div class="page-head-row">
          <div class="title-wrap">
            <h1>Contas a Pagar — Aberto / Parcial</h1>
            <span class="update-badge">Atualizado em __DATA_GERACAO__</span>
          </div>
          <button class="print-btn" onclick="window.print()">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>
            Imprimir
          </button>
        </div>
        <div class="controls" style="margin-top:12px;">
          <select id="empPag" class="select-filtro" onchange="applyFiltersPagar()">
            __OPCOES_EMP_PAG__
          </select>
          <div class="date-group">
            <span class="date-label">Vencimento</span>
            <input type="date" id="dtIniPag" onchange="applyFiltersPagar()">
            <span>até</span>
            <input type="date" id="dtFimPag" onchange="applyFiltersPagar()">
          </div>
          <button class="clear-btn" onclick="clearFiltersPagar()">✕ Limpar</button>
        </div>
        <div class="print-info" id="printInfo">Todas as empresas  ·  Todos os vencimentos</div>
      </div>
      <div class="content">
        <div class="cards">
          <div class="card"><h3>Total a Pagar</h3><p id="pag-titulo">__PAG_TITULO__</p></div>
          <div class="card c-mid"><h3>Já Pago / Abatido</h3><p id="pag-pago">__PAG_PAGO__</p></div>
          <div class="card c-end"><h3>Saldo Devedor</h3><p id="pag-saldo">__PAG_SALDO__</p></div>
        </div>
        <div class="table-wrapper">
          <div class="table-toolbar">
            <span class="table-title">Lançamentos ordenados por vencimento</span>
            <span class="count-badge" id="pagToolbar"></span>
          </div>
          <div class="table-scroll">
            <table class="pagar-table">
              <thead><tr>
                <th>Vencimento</th><th>Fornecedor</th><th>Empresa</th>
                <th>Documento</th><th>Emissão</th><th>Status</th>
                <th>Situação</th><th>Título (R$)</th><th>Pago (R$)</th><th>Saldo (R$)</th>
              </tr></thead>
              <tbody>__HTML_PAGAR__</tbody>
            </table>
          </div>
        </div>
      </div>
    </section>

    <!-- ════ CONTAS A RECEBER ════ -->
    <section id="receber" class="tab-content active">
      <div class="page-head">
        <div class="title-wrap">
          <h1>Contas a Receber — Análise de Inadimplência</h1>
          <span class="update-badge">Atualizado em __DATA_GERACAO__</span>
        </div>
        <div class="controls">
          <div class="search-wrap">
            <span class="search-icon"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg></span>
            <input type="text" id="searchRec" class="pesquisa" oninput="applyFiltersReceber()" placeholder="Pesquise por cliente ou grupo...">
          </div>
          <div class="status-group" id="statRecGroup">
            <label class="status-option"><input type="checkbox" value="A vencer" onchange="applyFiltersReceber()">A vencer</label>
            <label class="status-option"><input type="checkbox" value="Em dia" onchange="applyFiltersReceber()">Em dia</label>
            <label class="status-option"><input type="checkbox" value="Antigo" onchange="applyFiltersReceber()">Antigo</label>
            <label class="status-option"><input type="checkbox" value="Prescrito" onchange="applyFiltersReceber()">Prescrito</label>
          </div>
          <select id="empRec" class="select-filtro" onchange="applyFiltersReceber()">
            __OPCOES_EMP_REC__
          </select>
          <!-- Código do cliente: escolha a empresa cujo código será pesquisado -->
          <div class="code-group">
            <span class="code-label">Cód. de</span>
            <select id="codEmpRec" class="select-cod" onchange="applyFiltersReceber()">
              __OPCOES_COD_REC__
            </select>
            <input type="text" id="codRec" class="pesquisa-cod" oninput="applyFiltersReceber()" placeholder="Código do cliente...">
          </div>
        </div>
      </div>
      <div class="content">
        <div class="cards">
          <div class="card"><h3>Total Faturado</h3><p id="rec-titulo">R$ 0,00</p></div>
          <div class="card c-mid"><h3>Abatimentos / Recebido</h3><p id="rec-pago">R$ 0,00</p></div>
          <div class="card c-end"><h3>Saldo Restante</h3><p id="rec-saldo">R$ 0,00</p></div>
        </div>
        <div class="table-wrapper">
          <div class="table-toolbar"><span class="table-title">Clientes</span><span class="count-badge" id="countRec">0 visíveis</span></div>
          <div class="table-scroll">
            <table>
              <thead><tr><th>Nome do Cliente</th><th>Grupo</th><th>Saldo Restante</th></tr></thead>
              <tbody>__HTML_RECEBER__</tbody>
            </table>
          </div>
          <div class="empty-state" id="emptyRec">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
            <p>Nenhum cliente encontrado com os filtros aplicados.</p>
          </div>
        </div>
      </div>
    </section>

    <!-- ════ DADOS GERAIS ════ -->
    <section id="gerais" class="tab-content">
      <div class="page-head">
        <div class="title-wrap">
          <h1>Dados Gerais — Acompanhamento</h1>
          <span class="update-badge">Atualizado em __DATA_GERACAO__</span>
        </div>
        <div class="ger-subnav">
          <button type="button" class="ger-subnav-btn active" id="gersub-semana" onclick="showGerSub('semana')">Semana anterior</button>
          <button type="button" class="ger-subnav-btn" id="gersub-mes" onclick="showGerSub('mes')">Mês</button>
          <button type="button" class="ger-subnav-btn" id="gersub-ano" onclick="showGerSub('ano')">Ano</button>
        </div>
        <div class="controls" style="margin-top:12px;">
          <select id="empGer" class="select-filtro" onchange="applyFiltersGerais()">
            __OPCOES_EMP_GER__
          </select>
        </div>
      </div>
      <div class="content">

        <!-- SEMANA -->
        <div class="ger-subpanel active" id="gerpanel-semana">
          <div class="periodo-chips">
            <div class="periodo-chip"><span class="date-label">Semana anterior</span><strong>__PERIODO_A_SEMANA__</strong></div>
            <div class="periodo-chip"><span class="date-label">Mesma semana — ano anterior</span><strong>__PERIODO_B_SEMANA__</strong></div>
          </div>
          <div class="cards">
            <div class="card"><h3>Receita — Semana Anterior</h3><p id="ger-atual-semana">__GER_ATUAL_SEMANA__</p></div>
            <div class="card c-mid"><h3>Mesma Semana — Ano Anterior</h3><p id="ger-anterior-semana">__GER_ANTERIOR_SEMANA__</p></div>
            <div class="card c-end"><h3>Variação</h3><p id="ger-var-semana">__GER_VAR_SEMANA__</p></div>
          </div>
          <div class="stack-tables">
            <div class="table-wrapper">
              <div class="table-toolbar">
                <span class="table-title">Receita por forma de pagamento</span>
                <span class="count-badge" id="gerToolbar-semana"></span>
              </div>
              <div class="table-scroll">
                <table class="gerais-table">
                  <thead><tr>
                    <th>Forma de Pagamento</th><th>Natureza</th>
                    <th>Semana Anterior (R$)</th><th>Ano Anterior (R$)</th>
                    <th>Variação (R$)</th><th>Variação (%)</th>
                  </tr></thead>
                  <tbody id="gerBody-semana">__HTML_GERAIS_SEMANA__</tbody>
                </table>
              </div>
            </div>
            <div class="table-wrapper">
              <div class="table-toolbar">
                <span class="table-title">Liquidações recebidas</span>
                <div class="toolbar-right">
                  <select id="perLiq-semana" class="select-liq" onchange="applyFiltersGerais()">
                    <option value="atual">Semana anterior</option>
                    <option value="anoant">Mesma semana — ano anterior</option>
                    <option value="">Ambos os períodos</option>
                  </select>
                  <span class="count-badge" id="liqToolbar-semana">0 visíveis</span>
                </div>
              </div>
              <div class="table-scroll">
                <table class="liq-table">
                  <thead><tr><th>Cliente</th><th>Empresa(s)</th><th>Qtde.</th><th>Recebido Total (R$)</th></tr></thead>
                  <tbody id="liqBody-semana">__HTML_LIQ_SEMANA__</tbody>
                </table>
              </div>
              <div class="empty-state" id="emptyLiq-semana">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
                <p>Nenhuma liquidação no período e empresa selecionados.</p>
              </div>
            </div>
          </div>
        </div>

        <!-- MÊS -->
        <div class="ger-subpanel" id="gerpanel-mes">
          <div class="periodo-chips">
            <div class="periodo-chip"><span class="date-label">Mês atual (MTD)</span><strong>__PERIODO_A_MES__</strong></div>
            <div class="periodo-chip"><span class="date-label">Mesmo mês — ano anterior</span><strong>__PERIODO_B_MES__</strong></div>
          </div>
          <div class="cards">
            <div class="card"><h3>Receita — Mês Atual (MTD)</h3><p id="ger-atual-mes">__GER_ATUAL_MES__</p></div>
            <div class="card c-mid"><h3>Mesmo Mês — Ano Anterior</h3><p id="ger-anterior-mes">__GER_ANTERIOR_MES__</p></div>
            <div class="card c-end"><h3>Variação</h3><p id="ger-var-mes">__GER_VAR_MES__</p></div>
          </div>
          <div class="stack-tables">
            <div class="table-wrapper">
              <div class="table-toolbar">
                <span class="table-title">Receita por forma de pagamento</span>
                <span class="count-badge" id="gerToolbar-mes"></span>
              </div>
              <div class="table-scroll">
                <table class="gerais-table">
                  <thead><tr>
                    <th>Forma de Pagamento</th><th>Natureza</th>
                    <th>Mês Atual (R$)</th><th>Ano Anterior (R$)</th>
                    <th>Variação (R$)</th><th>Variação (%)</th>
                  </tr></thead>
                  <tbody id="gerBody-mes">__HTML_GERAIS_MES__</tbody>
                </table>
              </div>
            </div>
            <div class="table-wrapper">
              <div class="table-toolbar">
                <span class="table-title">Liquidações recebidas — agrupadas por cliente</span>
                <div class="toolbar-right">
                  <select id="perLiq-mes" class="select-liq" onchange="applyFiltersGerais()">
                    <option value="atual">Mês atual</option>
                    <option value="anoant">Mesmo mês — ano anterior</option>
                    <option value="">Ambos os períodos</option>
                  </select>
                  <span class="count-badge" id="liqToolbar-mes">0 visíveis</span>
                </div>
              </div>
              <div class="table-scroll">
                <table class="liq-table">
                  <thead><tr><th>Cliente</th><th>Empresa(s)</th><th>Qtde.</th><th>Recebido Total (R$)</th></tr></thead>
                  <tbody id="liqBody-mes">__HTML_LIQ_MES__</tbody>
                </table>
              </div>
              <div class="empty-state" id="emptyLiq-mes">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
                <p>Nenhuma liquidação no período e empresa selecionados.</p>
              </div>
            </div>
          </div>
        </div>

        <!-- ANO -->
        <div class="ger-subpanel" id="gerpanel-ano">
          <div class="periodo-chips">
            <div class="periodo-chip"><span class="date-label">Ano atual (YTD)</span><strong>__PERIODO_A_ANO__</strong></div>
            <div class="periodo-chip"><span class="date-label">Mesmo período — ano anterior</span><strong>__PERIODO_B_ANO__</strong></div>
          </div>
          <div class="cards">
            <div class="card"><h3>Receita — Ano Atual (YTD)</h3><p id="ger-atual-ano">__GER_ATUAL_ANO__</p></div>
            <div class="card c-mid"><h3>Mesmo Período — Ano Anterior</h3><p id="ger-anterior-ano">__GER_ANTERIOR_ANO__</p></div>
            <div class="card c-end"><h3>Variação</h3><p id="ger-var-ano">__GER_VAR_ANO__</p></div>
          </div>
          <div class="stack-tables">
            <div class="table-wrapper">
              <div class="table-toolbar">
                <span class="table-title">Receita por forma de pagamento</span>
                <span class="count-badge" id="gerToolbar-ano"></span>
              </div>
              <div class="table-scroll">
                <table class="gerais-table">
                  <thead><tr>
                    <th>Forma de Pagamento</th><th>Natureza</th>
                    <th>Ano Atual (R$)</th><th>Ano Anterior (R$)</th>
                    <th>Variação (R$)</th><th>Variação (%)</th>
                  </tr></thead>
                  <tbody id="gerBody-ano">__HTML_GERAIS_ANO__</tbody>
                </table>
              </div>
            </div>
            <div class="table-wrapper">
              <div class="table-toolbar">
                <span class="table-title">Liquidações recebidas — agrupadas por cliente</span>
                <div class="toolbar-right">
                  <select id="perLiq-ano" class="select-liq" onchange="applyFiltersGerais()">
                    <option value="atual">Ano atual</option>
                    <option value="anoant">Mesmo período — ano anterior</option>
                    <option value="">Ambos os períodos</option>
                  </select>
                  <span class="count-badge" id="liqToolbar-ano">0 visíveis</span>
                </div>
              </div>
              <div class="table-scroll">
                <table class="liq-table">
                  <thead><tr><th>Cliente</th><th>Empresa(s)</th><th>Qtde.</th><th>Recebido Total (R$)</th></tr></thead>
                  <tbody id="liqBody-ano">__HTML_LIQ_ANO__</tbody>
                </table>
              </div>
              <div class="empty-state" id="emptyLiq-ano">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
                <p>Nenhuma liquidação no período e empresa selecionados.</p>
              </div>
            </div>
          </div>
        </div>

      </div>
    </section>

  </div>
</div>

<!-- ════ NAV MOBILE ════ -->
<nav class="mobile-nav">
  <a id="mnav-pagar" onclick="showTab('pagar')"><svg class="ico" width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 17 13.5 8.5 8.5 13.5 2 7"/><polyline points="16 17 22 17 22 11"/></svg> Pagar</a>
  <a id="mnav-receber" class="active" onclick="showTab('receber')"><svg class="ico" width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg> Receber</a>
  <a id="mnav-gerais" onclick="showTab('gerais')"><svg class="ico" width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg> Gerais</a>
</nav>

<script>
  var NOMES_ABAS = { pagar: 'Contas a Pagar', receber: 'Contas a Receber', gerais: 'Dados Gerais' };

  function activateTab(tabId) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.sidebar-menu a, .mobile-nav a').forEach(el => el.classList.remove('active'));
    document.getElementById(tabId).classList.add('active');
    var d = document.getElementById('nav-' + tabId); if (d) d.classList.add('active');
    var m = document.getElementById('mnav-' + tabId); if (m) m.classList.add('active');
    window.scrollTo(0, 0);
    if (tabId === 'receber') applyFiltersReceber();
    if (tabId === 'pagar') applyFiltersPagar();
    if (tabId === 'gerais') applyFiltersGerais();
  }

  function showTab(tabId) {
    if (!isAuthorized(tabId)) {
      requestPassword(tabId);
      return;
    }
    activateTab(tabId);
  }

  function toggleDetails(rowEl, id) {
    var detailRow = document.getElementById(id);
    var isOpen = detailRow.style.display !== 'none';
    detailRow.style.display = isOpen ? 'none' : 'table-row';
    rowEl.classList.toggle('open', !isOpen);
  }

  function formatMoney(n) { return n.toLocaleString('pt-BR', {minimumFractionDigits: 2, maximumFractionDigits: 2}); }
  function plural(n, s, p) { return n + ' ' + (n === 1 ? s : p); }

  // ── Filtro Contas a Receber ──
  // Verifica se o cliente possui o código pesquisado na empresa escolhida.
  // data-codigos tem o formato "Empresa A=10 20;Empresa B=33".
  function clienteTemCodigo(cRow, codEmp, codBusca) {
    if (codBusca === "") return true;
    var raw = cRow.getAttribute("data-codigos") || "";
    return raw.split(";").some(function(par) {
      if (!par) return false;
      var sep = par.indexOf("=");
      if (sep < 0) return false;
      var emp = par.substring(0, sep);
      var codigos = par.substring(sep + 1).toUpperCase();
      return (codEmp === "" || emp === codEmp) && codigos.indexOf(codBusca) > -1;
    });
  }

  function statusSelecionadosReceber() {
    return Array.prototype.map.call(
      document.querySelectorAll("#statRecGroup input[type='checkbox']:checked"),
      function(el) { return el.value; }
    );
  }

  function applyFiltersReceber() {
    var search = document.getElementById("searchRec").value.toUpperCase();
    var emp = document.getElementById("empRec").value;
    var stats = statusSelecionadosReceber();
    var codEmp = document.getElementById("codEmpRec").value;
    var codBusca = document.getElementById("codRec").value.trim().toUpperCase();
    var gTit = 0, gPag = 0, gSal = 0, vis = 0;

    document.querySelectorAll(".client-row").forEach(function(cRow) {
      var targetId = cRow.getAttribute("onclick").match(/'([^']+)'/)[1];
      var detailRow = document.getElementById(targetId);
      var cTit = 0, cPag = 0, cSal = 0, vBills = 0;

      detailRow.querySelectorAll(".bill-row").forEach(function(bRow) {
        var bEmp = bRow.getAttribute("data-empresa");
        var bStat = bRow.getAttribute("data-status");
        var okStatus = stats.length === 0 || stats.indexOf(bStat) > -1;
        if ((emp === "" || bEmp === emp) && okStatus) {
          bRow.style.display = "";
          cTit += parseFloat(bRow.getAttribute("data-titulo"));
          cPag += parseFloat(bRow.getAttribute("data-pago"));
          cSal += parseFloat(bRow.getAttribute("data-saldo"));
          vBills++;
        } else bRow.style.display = "none";
      });

      var nome = (cRow.getAttribute("data-nome") + " " + cRow.getAttribute("data-grupo")).toUpperCase();
      if (vBills > 0 && nome.indexOf(search) > -1 && clienteTemCodigo(cRow, codEmp, codBusca)) {
        cRow.style.display = "";
        cRow.querySelector(".td-saldo").innerText = "R$ " + formatMoney(cSal);
        gTit += cTit; gPag += cPag; gSal += cSal; vis++;
      } else {
        cRow.style.display = "none"; detailRow.style.display = "none"; cRow.classList.remove('open');
      }
    });

    document.getElementById("rec-titulo").innerText = "R$ " + formatMoney(gTit);
    document.getElementById("rec-pago").innerText = "R$ " + formatMoney(gPag);
    document.getElementById("rec-saldo").innerText = "R$ " + formatMoney(gSal);
    document.getElementById("countRec").innerText = plural(vis, "visível", "visíveis");
    document.getElementById("emptyRec").style.display = vis === 0 ? "block" : "none";
  }

  // ── Filtro Contas a Pagar (empresa + intervalo de datas) ──
  function applyFiltersPagar() {
    var emp   = document.getElementById("empPag").value;
    var dtIni = document.getElementById("dtIniPag").value;
    var dtFim = document.getElementById("dtFimPag").value;
    var gTit = 0, gPag = 0, gSal = 0, vis = 0;
    var dias = {};   // iso -> {t, p, s, n}

    // 1) Filtra as linhas de lançamento e acumula por dia
    document.querySelectorAll(".pagar-row").forEach(function(row) {
      var bEmp  = row.getAttribute("data-empresa");
      var bData = row.getAttribute("data-data");
      var ok = (emp === "" || bEmp === emp)
            && (dtIni === "" || bData >= dtIni)
            && (dtFim === "" || bData <= dtFim);

      if (ok) {
        row.style.display = "";
        var t = parseFloat(row.getAttribute("data-titulo"));
        var p = parseFloat(row.getAttribute("data-pago"));
        var s = parseFloat(row.getAttribute("data-saldo"));
        gTit += t; gPag += p; gSal += s; vis++;
        if (!dias[bData]) dias[bData] = {t: 0, p: 0, s: 0, n: 0};
        dias[bData].t += t; dias[bData].p += p; dias[bData].s += s; dias[bData].n++;
      } else {
        row.style.display = "none";
      }
    });

    // 2) Recalcula/oculta os subtotais conforme os dias visíveis
    document.querySelectorAll(".subtotal-row").forEach(function(sr) {
      var iso = sr.getAttribute("data-dia");
      var d = dias[iso];
      if (d && d.n > 0) {
        sr.style.display = "";
        var nums = sr.querySelectorAll(".subtotal-num");
        nums[0].innerText = formatMoney(d.t);
        nums[1].innerText = formatMoney(d.p);
        nums[2].innerText = formatMoney(d.s);
        var lbl = sr.querySelector(".subtotal-label");
        var diaTxt = sr.getAttribute("data-dia-label");
        lbl.innerText = diaTxt + "  ·  " + d.n + (d.n === 1 ? " lançamento" : " lançamentos");
      } else {
        sr.style.display = "none";
      }
    });

    // 3) Atualiza cards e contador
    document.getElementById("pag-titulo").innerText = "R$ " + formatMoney(gTit);
    document.getElementById("pag-pago").innerText   = "R$ " + formatMoney(gPag);
    document.getElementById("pag-saldo").innerText  = "R$ " + formatMoney(gSal);
    var tb = document.getElementById("pagToolbar");
    if (tb) tb.innerText = plural(vis, "lançamento visível", "lançamentos visíveis");

    // 4) Resumo do filtro (visível na impressão)
    var empNome = emp === "" ? "Todas as empresas" : document.querySelector("#empPag option[value='" + emp + "']").innerText;
    var periodo = "Todos os vencimentos";
    if (dtIni !== "" || dtFim !== "") {
      var fmt = function(d) { if (!d) return "…"; var x = d.split("-"); return x[2] + "/" + x[1] + "/" + x[0]; };
      periodo = "Vencimento: " + fmt(dtIni) + " até " + fmt(dtFim);
    }
    var pi = document.getElementById("printInfo");
    if (pi) pi.innerText = empNome + "  ·  " + periodo + "  ·  " + vis + " lançamentos";
  }

  function clearFiltersPagar() {
    document.getElementById("empPag").value   = "";
    document.getElementById("dtIniPag").value = "";
    document.getElementById("dtFimPag").value = "";
    applyFiltersPagar();
  }

  // ── Dados Gerais: 3 subabas (semana / mês / ano) ──
  var GER_SUF = ['semana', 'mes', 'ano'];

  function showGerSub(suf) {
    document.querySelectorAll('.ger-subpanel').forEach(function(el) { el.classList.remove('active'); });
    document.querySelectorAll('.ger-subnav-btn').forEach(function(el) { el.classList.remove('active'); });
    document.getElementById('gerpanel-' + suf).classList.add('active');
    document.getElementById('gersub-' + suf).classList.add('active');
  }

  // data-por-empresa tem o formato "Empresa A=atual|anterior;Empresa B=...",
  // então o filtro de empresa recalcula os totais sem recarregar a página.
  function somaPorEmpresa(row, emp) {
    var a = 0, b = 0;
    (row.getAttribute("data-por-empresa") || "").split(";").forEach(function(par) {
      if (!par) return;
      var sep = par.indexOf("=");
      if (sep < 0) return;
      if (emp !== "" && par.substring(0, sep) !== emp) return;
      var v = par.substring(sep + 1).split("|");
      a += parseFloat(v[0]) || 0;
      b += parseFloat(v[1]) || 0;
    });
    return [a, b];
  }

  function sinalMoeda(v) { return (v >= 0 ? "+" : "−") + formatMoney(Math.abs(v)); }

  function applyFiltersGerais() {
    var emp = document.getElementById("empGer").value;
    GER_SUF.forEach(function(suf) {
      aplicarReceitaGer(suf, emp);
      aplicarLiqGer(suf, emp);
    });
  }

  function aplicarReceitaGer(suf, emp) {
    var totA = 0, totB = 0;

    document.querySelectorAll("#gerBody-" + suf + " .ger-row").forEach(function(row) {
      var par = somaPorEmpresa(row, emp);
      var a = par[0], b = par[1], dif = a - b;

      row.querySelector(".ger-atual").innerText    = formatMoney(a);
      row.querySelector(".ger-anterior").innerText = formatMoney(b);

      var clsVar = Math.abs(dif) < 0.005 ? "" : (dif > 0 ? " var-up" : " var-down");
      var difCel = row.querySelector(".ger-dif");
      difCel.innerText = sinalMoeda(dif);
      difCel.className = "num-cell ger-dif" + clsVar;

      var pctCel = row.querySelector(".ger-pct");
      if (b === 0) {
        pctCel.innerText = a === 0 ? "—" : "novo";
        pctCel.className = "num-cell ger-pct";
      } else {
        var p = dif / b * 100;
        pctCel.innerText = (p >= 0 ? "+" : "−") + Math.abs(p).toFixed(1).replace(".", ",") + "%";
        pctCel.className = "num-cell ger-pct" + (Math.abs(p) < 0.05 ? "" : clsVar);
      }

      if (row.getAttribute("data-total") === "1") { totA = a; totB = b; }
    });

    var elA = document.getElementById("ger-atual-" + suf);
    var elB = document.getElementById("ger-anterior-" + suf);
    if (elA) elA.innerText = "R$ " + formatMoney(totA);
    if (elB) elB.innerText = "R$ " + formatMoney(totB);

    var dif = totA - totB;
    var txt = (dif >= 0 ? "+" : "−") + "R$ " + formatMoney(Math.abs(dif));
    if (totB !== 0) {
      var p = dif / totB * 100;
      txt += " (" + (p >= 0 ? "+" : "−") + Math.abs(p).toFixed(1).replace(".", ",") + "%)";
    }
    var cardVar = document.getElementById("ger-var-" + suf);
    if (cardVar) {
      cardVar.innerText = txt;
      cardVar.className = Math.abs(dif) < 0.005 ? "" : (dif > 0 ? "var-up" : "var-down");
    }

    var tb = document.getElementById("gerToolbar-" + suf);
    if (tb) tb.innerText = emp === ""
      ? "Todas as empresas"
      : document.querySelector("#empGer option[value='" + emp + "']").innerText;
  }

  // ── Dados Gerais: liquidações recebidas, sumarizadas por cliente ──
  // Cada linha de detalhe (documento ou agregado por empresa, conforme a
  // subaba) traz data-qtd — na semana vale sempre "1" (1 linha = 1
  // documento); no mês/ano vale a quantidade de documentos daquele
  // agregado — assim o contador "Qtde." tem o mesmo significado nas 3 subabas.
  function aplicarLiqGer(suf, emp) {
    var perEl = document.getElementById("perLiq-" + suf);
    var per = perEl ? perEl.value : "";
    var totalQtd = 0, totalReceb = 0, visClientes = 0;

    document.querySelectorAll("#liqBody-" + suf + " .liqc-row").forEach(function(cRow) {
      var targetId = cRow.getAttribute("onclick").match(/'([^']+)'/)[1];
      var detailRow = document.getElementById(targetId);
      var qtd = 0, receb = 0;

      detailRow.querySelectorAll(".liq-row").forEach(function(row) {
        var ok = (emp === "" || row.getAttribute("data-empresa") === emp)
              && (per === "" || row.getAttribute("data-periodo") === per);
        row.style.display = ok ? "" : "none";
        if (ok) {
          qtd += parseFloat(row.getAttribute("data-qtd")) || 0;
          receb += parseFloat(row.getAttribute("data-recebido")) || 0;
        }
      });

      if (qtd > 0) {
        cRow.style.display = "";
        cRow.querySelector(".liqc-n").innerText = qtd;
        cRow.querySelector(".liqc-total").innerText = "R$ " + formatMoney(receb);
        totalQtd += qtd; totalReceb += receb; visClientes++;
      } else {
        cRow.style.display = "none"; detailRow.style.display = "none"; cRow.classList.remove("open");
      }
    });

    var tb = document.getElementById("liqToolbar-" + suf);
    if (tb) tb.innerText = plural(visClientes, "cliente", "clientes") + "  ·  " +
      plural(totalQtd, "liquidação", "liquidações") + "  ·  R$ " + formatMoney(totalReceb);
    var empty = document.getElementById("emptyLiq-" + suf);
    if (empty) empty.style.display = visClientes === 0 ? "block" : "none";
  }

  // ── Autenticação por aba (uma senha diferente para cada uma) ──
  var _SENHAS = __SENHAS_JSON__;
  var pendingTab = null;

  // sessionStorage pode lançar erro (cookies/dados de site bloqueados, modo
  // privado, file:// em alguns navegadores) — sem este fallback a senha certa
  // parecia "não funcionar", pois o erro abortava o desbloqueio da aba.
  var _okMemoria = {};
  function lerAutorizacao(tabId) {
    try { if (sessionStorage.getItem("ats_ok_" + tabId) === "1") return true; } catch (e) {}
    return _okMemoria[tabId] === true;
  }
  function gravarAutorizacao(tabId) {
    _okMemoria[tabId] = true;
    try { sessionStorage.setItem("ats_ok_" + tabId, "1"); } catch (e) {}
  }

  function isAuthorized(tabId) {
    return lerAutorizacao(tabId);
  }

  function updateNavLocks() {
    Object.keys(NOMES_ABAS).forEach(function(t) {
      var locked = !isAuthorized(t);
      ["nav-" + t, "mnav-" + t].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.classList.toggle("tab-locked", locked);
      });
    });
  }

  function requestPassword(tabId) {
    pendingTab = tabId;
    document.getElementById("login-sub").innerText = "Grupo Dluz · " + NOMES_ABAS[tabId];
    document.getElementById("login-erro").innerText = "";
    document.getElementById("senha-input").value = "";
    document.getElementById("login-overlay").style.display = "flex";
    setTimeout(function(){ document.getElementById("senha-input").focus(); }, 50);
  }

  function verificarSenha() {
    var v = document.getElementById("senha-input").value.trim();
    if (pendingTab && v === _SENHAS[pendingTab]) {
      var aba = pendingTab;
      pendingTab = null;
      gravarAutorizacao(aba);
      document.getElementById("login-overlay").style.display = "none";
      updateNavLocks();
      activateTab(aba);
    } else {
      var el = document.getElementById("login-erro");
      el.innerText = "Senha incorreta. Tente novamente.";
      document.getElementById("senha-input").value = "";
      document.getElementById("senha-input").focus();
      setTimeout(function(){ el.innerText = ""; }, 3000);
    }
  }

  window.onload = function() {
    updateNavLocks();
    showTab("receber");
  };
</script>
</body>
</html>"""

    html_final = (html_template
                  .replace("__HTML_PAGAR__",   html_pagar)
                  .replace("__HTML_RECEBER__", html_receber)
                  .replace("__OPCOES_EMP_PAG__", opcoes_empresas("Todas as Empresas"))
                  .replace("__OPCOES_EMP_REC__", opcoes_empresas("Empresas"))
                  .replace("__OPCOES_EMP_GER__", opcoes_empresas("Todas as Empresas"))
                  .replace("__OPCOES_COD_REC__", opcoes_empresas())
                  .replace("__DATA_GERACAO__", data_geracao)
                  .replace("__PAG_TITULO__",   f"R$ {formatar_moeda(pag_titulo_total)}")
                  .replace("__PAG_PAGO__",     f"R$ {formatar_moeda(pag_pago_total)}")
                  .replace("__PAG_SALDO__",    f"R$ {formatar_moeda(pag_saldo_total)}")
                  .replace("__SENHAS_JSON__",
                           json.dumps({k: v.strip() for k, v in SENHAS_ABAS.items()}).replace("</", "<\\/")))

    for suf in ('SEMANA', 'MES', 'ANO'):
        g = ger[suf.lower()]
        html_final = (html_final
            .replace(f"__HTML_GERAIS_{suf}__", g['html_receita'])
            .replace(f"__HTML_LIQ_{suf}__",    g['html_liq'])
            .replace(f"__PERIODO_A_{suf}__",   g['periodo_a_label'])
            .replace(f"__PERIODO_B_{suf}__",   g['periodo_b_label'])
            .replace(f"__GER_ATUAL_{suf}__",    f"R$ {formatar_moeda(g['tot_a'])}")
            .replace(f"__GER_ANTERIOR_{suf}__", f"R$ {formatar_moeda(g['tot_b'])}")
            .replace(f"__GER_VAR_{suf}__",       g['var_txt']))

    nome_arquivo = "index.html"
    with open(nome_arquivo, "w", encoding="utf-8") as file:
        file.write(html_final)
    print(f"\n📄 Arquivo gerado: {nome_arquivo}")
    return nome_arquivo


if __name__ == "__main__":
    ficheiro_gerado = extrair_e_gerar_html()
    if ficheiro_gerado and os.path.exists(ficheiro_gerado):
        caminho_abs = os.path.abspath(ficheiro_gerado)

        # Abre a versão local no navegador para conferência (não gasta banda do Netlify)
        print("\n" + "=" * 60)
        print(f"👀 A abrir versão local para conferência:\n   {caminho_abs}")
        try:
            webbrowser.open("file://" + caminho_abs)
        except Exception as e:
            print(f"   (não foi possível abrir automaticamente: {e})")
            print(f"   Abra manualmente o arquivo acima no seu navegador.")
        print("=" * 60)

        # Pergunta se deseja publicar
        try:
            resposta = input("\n📤 Confira o resultado. Deseja PUBLICAR no Netlify? [s/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            resposta = "n"

        if resposta in ("s", "sim", "y", "yes"):
            publicar_no_netlify(ficheiro_gerado)
        else:
            print("\n✋ Publicação cancelada.")
            print(f"   O arquivo local '{ficheiro_gerado}' está pronto para conferência/uso offline.")
            print("   Nenhuma banda do Netlify foi consumida.")
