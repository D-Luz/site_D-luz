"""Relatório mensal comparativo (ano anterior x ano atual) — linhas 8 a 12 da
tabela "Receita por forma de pagamento" do painel ATS:

  8. Subtotal à vista      (dinheiro + cartão + cheque à vista + convênio + outras formas)
  9. Subtotal a prazo      (cheque pré-datado + contas a receber)
 10. Receita total         (à vista + a prazo)
 11. Liquidações           (valor recebido nas liquidações de contas a receber)
 12. Recebimentos à vista  (subtotal à vista + liquidações)

Reaproveita as consultas e a regra de cálculo do gerar_dashboard_ats-11.py,
então os números batem com o painel. Gera um .xlsx com uma aba consolidada,
uma aba por empresa e uma aba de notas.
"""
import calendar
import importlib.util
import os
import warnings
from datetime import date, datetime, timedelta

import firebirdsql
import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

warnings.filterwarnings('ignore', message='pandas only supports SQLAlchemy')

PASTA = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_SAIDA = os.path.join(PASTA, "Receita_mensal_comparativo.xlsx")

_spec = importlib.util.spec_from_file_location(
    "dash", os.path.join(PASTA, "gerar_dashboard_ats.py"))
dash = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dash)

MESES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
         "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]

DADOS = ("vista", "prazo", "liq")        # vêm do banco; total e receb são fórmulas

# ── identidade visual do Grupo Dluz ──
CREME, SUPERFICIE, GRAFITE, AMBAR, VERMELHO = "F7F3EA", "FFFDF8", "241F18", "946517", "B3261E"
AMBAR_SUAVE = "EFE3C8"
FONTE_NUM = "Consolas"


def buscar_empresa(config, ini, lim):
    bind = (ini.strftime('%Y-%m-%d'), lim.strftime('%Y-%m-%d'))
    con = firebirdsql.connect(host=config["HOST"], database=config["DATABASE"],
                              port=dash.PORT, user=dash.USER, password=dash.PASSWORD,
                              charset=dash.CHARSET)
    try:
        vendas = dash.consultar(con, dash.SQL_VENDAS, bind)
        liq = dash.consultar(con, dash.SQL_LIQUIDACOES, bind)
    finally:
        con.close()
    return vendas, liq


def resumir(vendas, liq, periodos):
    """Reduz as consultas brutas de uma empresa a:
      mensal[chave][(ano, mes)] -> valor do mês fechado/corrente (mês inteiro)
      janelas[nome][chave][ano] -> valor de uma janela com o mesmo intervalo de dias nos
                                   2 anos: 'ytd' (jan até hoje) e 'mtd' (dia 01 até hoje)
    para as chaves 'vista', 'prazo', 'liq' (total e receb são derivados na planilha)."""
    mensal = {k: {} for k in DADOS}
    janelas = {j: {k: {} for k in DADOS} for j in ("ytd", "mtd")}

    colunas_vista = list(dash.COLS_VISTA) + ['VLROUTRAS']
    colunas_prazo = list(dash.COLS_PRAZO)
    if not vendas.empty:
        v = vendas.copy()
        v['DT'] = pd.to_datetime(v['DATAPEDIDO'])
        for c in colunas_vista + colunas_prazo:
            v[c] = v[c].astype(float)
        v['vista'] = v[colunas_vista].sum(axis=1)
        v['prazo'] = v[colunas_prazo].sum(axis=1)
    else:
        v = pd.DataFrame(columns=['DT', 'vista', 'prazo'])

    if not liq.empty:
        l = liq.copy()
        l['DT'] = pd.to_datetime(l['DT_LIQUIDACAO'])
        l['liq'] = l['VLR_RECEBIDO'].astype(float)
    else:
        l = pd.DataFrame(columns=['DT', 'liq'])

    fontes = {'vista': v, 'prazo': v, 'liq': l}
    for chave, df in fontes.items():
        if df.empty:
            continue
        for (ano, mes), valor in df.groupby([df['DT'].dt.year, df['DT'].dt.month])[chave].sum().items():
            mensal[chave][(int(ano), int(mes))] = float(valor)

    pares = {"ytd": (periodos['E'], periodos['F']), "mtd": (periodos['C'], periodos['D'])}
    for nome, (atual, anterior) in pares.items():
        for (ini, lim) in (atual, anterior):
            for chave, df in fontes.items():
                janelas[nome][chave][ini.year] = (
                    0.0 if df.empty else
                    float(df.loc[(df['DT'] >= pd.Timestamp(ini)) & (df['DT'] < pd.Timestamp(lim))][chave].sum()))
    return mensal, janelas


def somar_resumos(resumos):
    mensal = {k: {} for k in DADOS}
    janelas = {j: {k: {} for k in DADOS} for j in ("ytd", "mtd")}
    for m, jan in resumos:
        for k in DADOS:
            for chave, val in m[k].items():
                mensal[k][chave] = mensal[k].get(chave, 0.0) + val
            for j in janelas:
                for ano, val in jan[j][k].items():
                    janelas[j][k][ano] = janelas[j][k].get(ano, 0.0) + val
    return mensal, janelas


# ═══════════════════════ planilha ═══════════════════════
# Um bloco por mês (e um de acumulado no topo), cada um com as linhas 8 a 12 —
# mesmo desenho da tabela do painel: Linha | ano anterior | ano atual | variação R$ | variação %.
LINHAS = [
    ("vista", "8. SUBTOTAL À VISTA"),
    ("prazo", "9. SUBTOTAL A PRAZO"),
    ("total", "10. RECEITA TOTAL"),
    ("liq",   "11. LIQUIDAÇÕES (CONTAS A RECEBER)"),
    ("receb", "12. RECEBIMENTOS À VISTA"),
]
ALTURA_BLOCO = 9            # título + cabeçalho + 5 linhas + 2 de respiro
LINHA_INI_BLOCOS = 5


def estilos():
    borda = Side(style="thin", color="D9CFB8")
    return {
        "titulo": Font(name="Georgia", size=16, bold=True, color=GRAFITE),
        "sub": Font(name="Calibri", size=10, color="6B6254"),
        "bloco": Font(name="Georgia", size=12, bold=True, color=AMBAR),
        "cab": Font(name="Calibri", size=10, bold=True, color="F7F3EA"),
        "cab_fill": PatternFill("solid", fgColor=GRAFITE),
        "num": Font(name=FONTE_NUM, size=10, color="2C2620"),
        "num_b": Font(name=FONTE_NUM, size=10, bold=True, color=GRAFITE),
        "sub_fill": PatternFill("solid", fgColor="F3ECDC"),
        "tot_fill": PatternFill("solid", fgColor=AMBAR_SUAVE),
        "linha": Border(bottom=borda),
        "linha_tot": Border(top=Side(style="medium", color=AMBAR), bottom=borda),
    }


def escrever_aba(ws, titulo, mensal, janelas, hoje, periodos):
    st = estilos()
    ano_atual, ano_ant = hoje.year, hoje.year - 1
    mes_atual = hoje.month
    ini_e, lim_e = periodos['E']
    ini_f, lim_f = periodos['F']
    ini_c, lim_c = periodos['C']
    ini_d, lim_d = periodos['D']

    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = AMBAR
    for col, larg in zip("ABCDE", (40, 20, 20, 20, 14)):
        ws.column_dimensions[col].width = larg

    ws["A1"] = titulo
    ws["A1"].font = st["titulo"]
    ws["A2"] = (f"Receita por forma de pagamento — {ano_ant} x {ano_atual}, mês a mês  ·  "
                f"gerado em {datetime.now():%d/%m/%Y às %H:%M}")
    ws["A2"].font = st["sub"]
    ws["A3"] = (f"{MESES[mes_atual - 1]}/{ano_atual} é parcial: é comparado com o mesmo intervalo de dias de "
                f"{ano_ant} (dia 01 a {hoje:%d}).")
    ws["A3"].font = st["sub"]

    # (título do bloco, valores {chave: (ano_ant, ano_atual)})
    blocos = [(f"ACUMULADO DO ANO  ·  {ini_f:%d/%m} a {(lim_f - timedelta(days=1)):%d/%m/%Y}  x  "
               f"{ini_e:%d/%m} a {(lim_e - timedelta(days=1)):%d/%m/%Y}",
               {k: (janelas["ytd"][k].get(ano_ant, 0.0), janelas["ytd"][k].get(ano_atual, 0.0)) for k in DADOS})]
    for mes in range(1, mes_atual + 1):
        if mes < mes_atual:
            titulo_bloco = f"{MESES[mes - 1].upper()}  ·  {ano_ant} x {ano_atual}"
            vals = {k: (mensal[k].get((ano_ant, mes), 0.0), mensal[k].get((ano_atual, mes), 0.0)) for k in DADOS}
        else:
            titulo_bloco = (f"{MESES[mes - 1].upper()} (PARCIAL)  ·  {ini_d:%d/%m} a {(lim_d - timedelta(days=1)):%d/%m/%Y}"
                            f"  x  {ini_c:%d/%m} a {(lim_c - timedelta(days=1)):%d/%m/%Y}")
            vals = {k: (janelas["mtd"][k].get(ano_ant, 0.0), janelas["mtd"][k].get(ano_atual, 0.0)) for k in DADOS}
        blocos.append((titulo_bloco, vals))

    for i, (titulo_bloco, vals) in enumerate(blocos):
        r0 = LINHA_INI_BLOCOS + i * ALTURA_BLOCO
        r_cab, r1 = r0 + 1, r0 + 2
        linha_de = {chave: r1 + j for j, (chave, _) in enumerate(LINHAS)}

        ws.cell(r0, 1, titulo_bloco).font = st["bloco"]
        for col, texto in enumerate(["Linha", ano_ant, ano_atual, "Variação (R$)", "Variação (%)"], start=1):
            c = ws.cell(r_cab, col, texto)
            c.font, c.fill = st["cab"], st["cab_fill"]
            c.alignment = Alignment(horizontal="left" if col == 1 else "right", vertical="center")
        ws.row_dimensions[r_cab].height = 20

        for chave, rotulo in LINHAS:
            r = linha_de[chave]
            ws.cell(r, 1, rotulo)
            if chave in DADOS:
                ws.cell(r, 2, round(vals[chave][0], 2))
                ws.cell(r, 3, round(vals[chave][1], 2))
            else:                      # total = 8 + 9 ; receb = 8 + 11
                outra = "prazo" if chave == "total" else "liq"
                for col in "BC":
                    ws[f"{col}{r}"] = f"={col}{linha_de['vista']}+{col}{linha_de[outra]}"
            ws.cell(r, 4, f"=C{r}-B{r}")
            ws.cell(r, 5, f'=IF(B{r}=0,"n/d",D{r}/B{r})')

            destaque = chave in ("total", "receb")
            subtotal = chave in ("vista", "prazo")
            for col in range(1, 6):
                c = ws.cell(r, col)
                c.font = (Font(name="Calibri", size=10, bold=destaque or subtotal, color="2C2620")
                          if col == 1 else (st["num_b"] if destaque or subtotal else st["num"]))
                c.alignment = Alignment(horizontal="left" if col == 1 else "right", vertical="center")
                c.number_format = {2: '#,##0.00', 3: '#,##0.00', 4: '+#,##0.00;-#,##0.00;0.00',
                                   5: '+0.0%;-0.0%;0.0%'}.get(col, 'General')
                c.border = st["linha_tot"] if destaque else st["linha"]
                if destaque:
                    c.fill = st["tot_fill"]
                elif subtotal:
                    c.fill = st["sub_fill"]

        ws.conditional_formatting.add(
            f"D{r1}:E{r1 + 4}", CellIsRule(operator="lessThan", formula=["0"], font=Font(color=VERMELHO)))

    ws.freeze_panes = "A5"


def escrever_graficos(ws, mensal, hoje, escopo="consolidado"):
    """Aba com a tabela-base (valores do consolidado) e 2 gráficos de linhas,
    ano anterior x ano atual: receita total (linha 10) e recebimentos à vista (linha 12).
    O mês corrente fica de fora de 2026 por ser parcial (a linha despencaria sem motivo);
    2025 aparece inteiro."""
    st = estilos()
    ano_atual, ano_ant = hoje.year, hoje.year - 1
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = AMBAR
    for col, larg in zip("ABCDE", (14, 22, 22, 26, 26)):
        ws.column_dimensions[col].width = larg

    ws["A1"] = f"Grupo Dluz — Gráficos ({escopo})"
    ws["A1"].font = st["titulo"]
    ws["A2"] = (f"{MESES[hoje.month - 1]}/{ano_atual} não entra no gráfico por ser mês parcial; "
                f"meses sem dado ficam em branco.")
    ws["A2"].font = st["sub"]

    cab = ["Mês", f"Receita total {ano_ant}", f"Receita total {ano_atual}",
           f"Recebimentos à vista {ano_ant}", f"Recebimentos à vista {ano_atual}"]
    r_cab = 4
    for col, texto in enumerate(cab, start=1):
        c = ws.cell(r_cab, col, texto)
        c.font, c.fill = st["cab"], st["cab_fill"]
        c.alignment = Alignment(horizontal="left" if col == 1 else "right", vertical="center", wrap_text=True)
    ws.row_dimensions[r_cab].height = 32

    def valor(chave, ano, mes):
        v = lambda k: mensal[k].get((ano, mes), 0.0)
        return v("vista") + (v("prazo") if chave == "total" else v("liq"))

    for mes in range(1, 13):
        r = r_cab + mes
        ws.cell(r, 1, MESES[mes - 1][:3])
        ws.cell(r, 2, round(valor("total", ano_ant, mes), 2))
        ws.cell(r, 4, round(valor("receb", ano_ant, mes), 2))
        if mes < hoje.month:            # só meses fechados de 2026
            ws.cell(r, 3, round(valor("total", ano_atual, mes), 2))
            ws.cell(r, 5, round(valor("receb", ano_atual, mes), 2))
        for col in range(1, 6):
            c = ws.cell(r, col)
            c.font = Font(name="Calibri", size=10, color="2C2620") if col == 1 else st["num"]
            c.alignment = Alignment(horizontal="left" if col == 1 else "right")
            c.number_format = "#,##0.00"
            c.border = st["linha"]

    def grafico(titulo, col_ant, ancora):
        ch = LineChart()
        ch.title = titulo
        ch.height, ch.width = 10.5, 24
        ch.y_axis.title = "R$"
        ch.y_axis.number_format = "#,##0"
        ch.y_axis.majorGridlines.spPr = GraphicalProperties(ln=None)
        ch.x_axis.delete = False
        ch.y_axis.delete = False
        ch.legend.position = "b"
        ch.display_blanks = "gap"
        dados = Reference(ws, min_col=col_ant, max_col=col_ant + 1, min_row=r_cab, max_row=r_cab + 12)
        ch.add_data(dados, titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=r_cab + 1, max_row=r_cab + 12))
        for serie, cor, tracejado in zip(ch.series, ("8A7F6B", AMBAR), ("dash", None)):
            serie.smooth = False
            serie.graphicalProperties.line.solidFill = cor
            serie.graphicalProperties.line.width = 28000
            if tracejado:
                serie.graphicalProperties.line.dashStyle = tracejado
            serie.marker.symbol = "circle"
            serie.marker.size = 6
            serie.marker.graphicalProperties = GraphicalProperties(solidFill=cor)
            serie.marker.graphicalProperties.line.solidFill = cor
        ws.add_chart(ch, ancora)

    grafico(f"Receita total — {escopo} — {ano_ant} x {ano_atual}", 2, "G4")
    grafico(f"Recebimentos à vista — {escopo} — {ano_ant} x {ano_atual}", 4, "G27")


def escrever_notas(ws, hoje, periodos):
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 110
    ini_e, lim_e = periodos['E']
    ini_f, lim_f = periodos['F']
    linhas = [
        ("Notas e definições", Font(name="Georgia", size=16, bold=True, color=GRAFITE)),
        ("", None),
        ("Fonte: bancos Firebird do ATS (bases Real) das 3 empresas, mesmas consultas do painel ATS "
         "(gerar_dashboard_ats-11.py).", None),
        ("Vendas: pedidos PDV (TIPOPEDIDO 55) com FATURADO = 'S', por data do pedido. Pré-vendas abertas e "
         "cancelados ficam de fora.", None),
        ("8. Subtotal à vista = dinheiro + cartão + cheque à vista + convênio + outras formas de pagamento.", None),
        ("   \"Outras formas\" = parte do pedido faturado que não caiu em nenhuma das 6 colunas de ENCEFAT "
         "(total do pedido menos as 6 formas).", None),
        ("9. Subtotal a prazo = cheque pré-datado + contas a receber.", None),
        ("10. Receita total = 8 + 9 (equivale à soma do valor dos pedidos faturados).", None),
        ("11. Liquidações = valor recebido (MOVIREC, tipos 04 e 05, não estornados) por data de liquidação, "
         "todas as formas de recebimento.", None),
        ("12. Recebimentos à vista = 8 + 11.", None),
        ("", None),
        (f"Cada bloco compara um mês de {hoje.year - 1} com o mesmo mês de {hoje.year}. O mês atual "
         f"({MESES[hoje.month - 1]}) é parcial e usa o mesmo intervalo de dias nos dois anos. Meses futuros não aparecem.", None),
        (f"Bloco \"Acumulado do ano\": {ini_e:%d/%m/%Y} a {(lim_e - timedelta(days=1)):%d/%m/%Y} contra "
         f"{ini_f:%d/%m/%Y} a {(lim_f - timedelta(days=1)):%d/%m/%Y}.", None),
        ("As linhas 10 e 12 e todas as variações são fórmulas do Excel; os valores das linhas 8, 9 e 11 "
         "vêm do banco.", None),
        ("A aba Consolidado é a soma das 3 empresas. As abas \"Gráficos\" e \"Gráf. <empresa>\" trazem os gráficos de linhas (receita total e recebimentos à vista, ano anterior x atual); o mês corrente fica fora dos gráficos por ser parcial.", None),
    ]
    for i, (texto, fonte) in enumerate(linhas, start=1):
        c = ws.cell(i, 1, texto)
        c.font = fonte or Font(name="Calibri", size=10, color="2C2620")
        c.alignment = Alignment(wrap_text=True, vertical="top")


def main():
    hoje = date.today()
    periodos = dash.periodos_definidos(hoje)
    ini = date(hoje.year - 1, 1, 1)
    lim = hoje + timedelta(days=1)
    print(f"📅 Período extraído: {ini:%d/%m/%Y} a {hoje:%d/%m/%Y}")

    resumos = {}
    for nome, cfg in dash.EMPRESAS.items():
        print(f"🔌 A ler {nome}...")
        try:
            vendas, liq = buscar_empresa(cfg, ini, lim)
        except Exception as e:
            print(f"  ❌ Falha: {e}")
            raise SystemExit("Sem dados de uma das empresas — o consolidado ficaria incompleto. Abortado.")
        resumos[nome] = resumir(vendas, liq, periodos)
        print(f"  ✔ {len(vendas)} vendas | {len(liq)} liquidações")

    wb = Workbook()
    ws = wb.active
    ws.title = "Consolidado"
    mensal, janelas = somar_resumos(list(resumos.values()))
    escrever_aba(ws, "Grupo Dluz — Consolidado", mensal, janelas, hoje, periodos)
    escrever_graficos(wb.create_sheet("Gráficos"), mensal, hoje)
    for nome, (m, c) in resumos.items():
        display = dash.EMPRESAS[nome]["DISPLAY"]
        escrever_aba(wb.create_sheet(display), display, m, c, hoje, periodos)
        escrever_graficos(wb.create_sheet(f"Gráf. {display}"[:31]), m, hoje, escopo=display)
    escrever_notas(wb.create_sheet("Notas"), hoje, periodos)

    wb.save(ARQUIVO_SAIDA)
    print(f"\n📄 Arquivo gerado: {ARQUIVO_SAIDA}")
    return ARQUIVO_SAIDA


if __name__ == "__main__":
    main()
