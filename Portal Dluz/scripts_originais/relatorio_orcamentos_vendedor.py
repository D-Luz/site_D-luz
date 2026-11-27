#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Relatório de orçamentos por vendedor — baixados vs. não baixados
==================================================================
(ATS ERP — Firebird / Grupo Dluz)

Pergunta o período (e a empresa, ou ambas) e gera um único Excel formatado
com um resumo por vendedor e o detalhamento (orçamentos baixados e não
baixados) de cada um, nos moldes do relatório gerencial manual do grupo —
mas tudo numa aba só.

"Baixado" = o orçamento já gerou um pedido de venda (PEDIDOC.CODORC aponta
para ele). "% Desc." no detalhamento é calculado:
(Total Orçamento - Valor Venda) / Total Orçamento — o ERP não grava esse
percentual em nenhum campo (DESCONTOPERC vem zerado), só o desconto em valor.

Só entram os vendedores de VENDEDORES_ACOMPANHADOS. Cópias idênticas de um
mesmo orçamento (mesmo vendedor, cliente, valor e dia) são descartadas: um
orçamento baixado nunca é descartado e, havendo um baixado no grupo, as cópias
não baixadas somem; sem baixado, fica só a de menor código. O que foi
descartado é listado em "Observações" no fim do relatório.

Dependências:
    pip install firebirdsql pandas openpyxl
"""

import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import firebirdsql
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties

# ── Configuração das empresas ───────────────────────────────────
PORT = 3050
CHARSET = "WIN1252"
USER = "SYSDBA"
PASSWORD = "masterkey"

EMPRESAS = {
    "Dluz Elétrica": {
        "host": "192.168.0.41",
        "database": "D:\\DLUZR\\BANCO\\DLUZR25.FB",
    },
    "Dluz Iluminação": {
        "host": "192.168.0.250",
        "database": "D:\\ILUMIR\\BANCO\\ILUMIR.FB",
    },
}

# Vendedores acompanhados no relatório (nomes como estão na VENDEND).
VENDEDORES_ACOMPANHADOS = ("ANDRE RAMOS", "MAILSON", "MARCIEL", "MARCOS")

# ── Identidade visual Grupo Dluz ────────────────────────────────
COR_AMBAR = "946517"
COR_GRAFITE = "241F18"
COR_CREME = "F7F3EA"
COR_VERMELHO = "B3261E"
COR_BRANCO = "FFFFFF"
COR_BORDA = "C9C0AE"

FONTE_TITULO = "Georgia"
FONTE_TEXTO = "Calibri"
FONTE_MONO = "Consolas"

NUM_COLUNAS_GRID = 8  # largura máxima usada por qualquer tabela do relatório

# Larguras fixas das 8 colunas (A..H), escolhidas para servir tanto ao resumo
# (Vendedor, Empresa, Qtds, Valores...) quanto às tabelas de detalhe (Nº, Cód.
# Orç., Cliente, Total, Data, Valor Venda, PDV, % Desc.).
LARGURAS_COLUNAS = [16, 17, 34, 18, 18, 18, 14, 14]

SQL_ORCAMENTOS = """
SELECT
    O.CODEMPRESA,
    O.CODORC,
    O.DATAORCAMENTO,
    O.CODCLIENTE,
    O.NOMECLIENTE,
    O.CODVENDEDOR,
    V.NOME AS VENDEDOR,
    O.TOTALORCAMENTO,
    O.VALIDADE
FROM ORCCABE O
LEFT JOIN VENDEND V
    ON V.CODVENDEDOR = O.CODVENDEDOR
WHERE O.DATAORCAMENTO >= ?
  AND O.DATAORCAMENTO <  ?
  AND (O.CANCELADO IS NULL OR O.CANCELADO <> 'S')
ORDER BY O.DATAORCAMENTO
"""

# Numa passada só: o LEFT JOIN direto varre a PEDIDOC inteira por orçamento (minutos).
SQL_PEDIDOS = """
SELECT CODEMPRESAORC, CODORC, CODPEDIDO, TOTALPEDIDO
FROM PEDIDOC
WHERE CODORC IS NOT NULL AND CODORC <> ''
"""


# ── Entrada do usuário ──────────────────────────────────────────

def escolher_empresas() -> dict:
    print("Empresas disponíveis:")
    print("  1 - Dluz Elétrica")
    print("  2 - Dluz Iluminação")
    print("  3 - Ambas")
    escolha = input("Escolha (número, Enter = Ambas): ").strip() or "3"
    if escolha == "1":
        return {"Dluz Elétrica": EMPRESAS["Dluz Elétrica"]}
    if escolha == "2":
        return {"Dluz Iluminação": EMPRESAS["Dluz Iluminação"]}
    return dict(EMPRESAS)


def pedir_data(rotulo: str) -> date:
    while True:
        texto = input(f"Data {rotulo} (DD/MM/AAAA): ").strip()
        try:
            return datetime.strptime(texto, "%d/%m/%Y").date()
        except ValueError:
            print("Data inválida, use o formato DD/MM/AAAA (ex: 01/09/2026).")


# ── Consulta ao banco ───────────────────────────────────────────

def to_int(valor):
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto or not texto.lstrip("-").isdigit():
        return None
    return int(texto)


def desconto_percentual(total_orcamento, total_pedido):
    if pd.isna(total_orcamento) or pd.isna(total_pedido):
        return None
    orcamento = round(float(total_orcamento), 2)
    if not orcamento:
        return None
    return (orcamento - round(float(total_pedido), 2)) / orcamento


def remover_copias_identicas(df: pd.DataFrame) -> tuple:
    # Cópia = mesma empresa, vendedor, cliente, valor e dia; baixado nunca é descartado.
    df = df.sort_values(["EMPRESA", "CODORC"]).reset_index(drop=True)
    chaves = [
        df["EMPRESA"],
        df["CODVENDEDOR"].fillna("").str.strip(),
        df["CODCLIENTE"].fillna("").str.strip(),
        df["NOMECLIENTE"].fillna("").str.strip(),
        df["TOTALORCAMENTO"].astype(float).round(2),
        pd.to_datetime(df["DATAORCAMENTO"]).dt.normalize(),
    ]
    baixado = df["STATUS"] == "Baixado"
    tem_baixado = baixado.groupby(chaves).transform("any")
    posicao = df.groupby(chaves).cumcount()
    descartar = ~baixado & (tem_baixado | (posicao > 0))
    return df[~descartar], df[descartar]


def montar_observacoes(copias: pd.DataFrame, ignorados: pd.DataFrame) -> list:
    linhas = []
    for empresa, grupo in copias.groupby("EMPRESA", sort=True):
        codigos = ", ".join(str(to_int(c)) for c in grupo["CODORC"])
        linhas.append(f"{empresa}: {len(grupo)} cópia(s) idêntica(s) descartada(s) "
                      f"(mesmo vendedor, cliente, valor e dia) — orçamentos {codigos}.")
    if not ignorados.empty:
        contagem = ignorados["VENDEDOR"].fillna("(sem cadastro)").value_counts()
        nomes = ", ".join(f"{nome} ({qtd})" for nome, qtd in contagem.items())
        linhas.append(f"Fora do acompanhamento, não incluídos: {nomes}.")
    return linhas


def buscar_orcamentos(nome_empresa: str, cfg: dict, dt_ini: date, dt_fim: date) -> pd.DataFrame:
    con = firebirdsql.connect(
        host=cfg["host"], database=cfg["database"], port=PORT,
        user=USER, password=PASSWORD, charset=CHARSET,
    )
    try:
        cur = con.cursor()
        cur.execute(SQL_ORCAMENTOS, (dt_ini.isoformat(), (dt_fim + timedelta(days=1)).isoformat()))
        df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
        cur.execute(SQL_PEDIDOS)
        pedidos = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    finally:
        con.close()

    for tabela, colunas in ((df, ["CODEMPRESA", "CODORC"]), (pedidos, ["CODEMPRESAORC", "CODORC"])):
        for coluna in colunas:
            tabela[coluna] = tabela[coluna].str.strip()
    pedidos = pedidos.sort_values("CODPEDIDO").drop_duplicates(["CODEMPRESAORC", "CODORC"])
    df = df.merge(pedidos, how="left", left_on=["CODEMPRESA", "CODORC"],
                  right_on=["CODEMPRESAORC", "CODORC"]).drop(columns="CODEMPRESAORC")
    df["STATUS"] = df["CODPEDIDO"].notna().map({True: "Baixado", False: "Não baixado"})
    df.insert(0, "EMPRESA", nome_empresa)
    return df


# ── Montagem do Excel ───────────────────────────────────────────

def _cabecalho_secao(ws, linha: int, texto: str, fonte: Font) -> int:
    ws.merge_cells(start_row=linha, start_column=1, end_row=linha, end_column=NUM_COLUNAS_GRID)
    c = ws.cell(row=linha, column=1, value=texto)
    c.font = fonte
    c.alignment = Alignment(horizontal="left")
    return linha + 1


def _escrever_tabela(ws, linha: int, titulo: str, fonte_titulo: Font,
                      df_tabela: pd.DataFrame, colspec: list, estilos: dict) -> int:
    n_col = len(colspec)

    ws.merge_cells(start_row=linha, start_column=1, end_row=linha, end_column=n_col)
    c = ws.cell(row=linha, column=1, value=titulo)
    c.font = fonte_titulo
    c.alignment = Alignment(horizontal="center")
    linha += 1

    for col, (rotulo, _fn, _tipo) in enumerate(colspec, start=1):
        c = ws.cell(row=linha, column=col, value=rotulo)
        c.font = estilos["header"]
        c.fill = estilos["fill_header"]
        c.border = estilos["borda"]
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    linha += 1

    colunas_moeda = [i for i, (_, _, tipo) in enumerate(colspec, start=1) if tipo == "moeda"]

    if df_tabela.empty:
        for col in range(1, n_col + 1):
            c = ws.cell(row=linha, column=col, value="—")
            c.font = estilos["texto"]
            c.border = estilos["borda"]
            c.alignment = Alignment(horizontal="center")
        for col in colunas_moeda:
            ws.cell(row=linha, column=col).number_format = '"R$" #,##0.00'
        linha += 1
    else:
        somas = {i: 0.0 for i in colunas_moeda}
        for idx, linha_dados in enumerate(df_tabela.itertuples(index=False), start=1):
            vencido = (getattr(linha_dados, "VALIDADE", None) is not None
                       and linha_dados.VALIDADE.date() < date.today()
                       and getattr(linha_dados, "STATUS", "") == "Não baixado")
            fill = estilos["fill_zebra"] if idx % 2 == 0 else None
            for col, (_rotulo, fn, tipo) in enumerate(colspec, start=1):
                valor = idx if tipo == "seq" else fn(linha_dados)
                c = ws.cell(row=linha, column=col, value=valor)
                c.border = estilos["borda"]
                if fill:
                    c.fill = fill

                if tipo == "texto":
                    c.font = estilos["alerta"] if vencido else estilos["texto"]
                    c.alignment = Alignment(horizontal="left")
                elif tipo == "moeda":
                    c.font = estilos["alerta"] if vencido else estilos["mono"]
                    c.number_format = '"R$" #,##0.00'
                    c.alignment = Alignment(horizontal="right")
                    if valor is not None:
                        somas[col] += valor
                elif tipo == "data":
                    c.font = estilos["alerta"] if vencido else estilos["mono"]
                    c.number_format = "dd/mm/yyyy"
                    c.alignment = Alignment(horizontal="center")
                elif tipo == "percentual":
                    c.font = estilos["alerta"] if vencido else estilos["mono"]
                    c.number_format = "0.0%"
                    c.alignment = Alignment(horizontal="center")
                else:  # "seq" (Nº da linha) / "codigo" (Cód. Orç., PDV)
                    c.font = estilos["alerta"] if vencido else estilos["mono"]
                    c.alignment = Alignment(horizontal="center")
            linha += 1

        ws.merge_cells(start_row=linha, start_column=1, end_row=linha, end_column=min(3, n_col))
        c = ws.cell(row=linha, column=1, value="TOTAL")
        c.font = estilos["mono_bold"]
        c.border = estilos["borda_total"]
        for col in range(2, min(3, n_col) + 1):
            ws.cell(row=linha, column=col).border = estilos["borda_total"]
        for col in range(min(3, n_col) + 1, n_col + 1):
            c = ws.cell(row=linha, column=col)
            c.border = estilos["borda_total"]
            if col in somas:
                c.value = round(somas[col], 2)
                c.number_format = '"R$" #,##0.00'
                c.font = estilos["mono_bold"]
                c.alignment = Alignment(horizontal="right")
        linha += 1

    return linha + 1


def montar_relatorio(df: pd.DataFrame, dt_ini: date, dt_fim: date, observacoes: list) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Relatório"

    lado = Side(style="thin", color=COR_BORDA)
    estilos = {
        "header": Font(name=FONTE_TEXTO, size=10, bold=True, color=COR_BRANCO),
        "fill_header": PatternFill(fill_type="solid", fgColor=COR_AMBAR),
        "fill_zebra": PatternFill(fill_type="solid", fgColor=COR_CREME),
        "texto": Font(name=FONTE_TEXTO, size=10, color=COR_GRAFITE),
        "mono": Font(name=FONTE_MONO, size=10, color=COR_GRAFITE),
        "mono_bold": Font(name=FONTE_MONO, size=10, bold=True, color=COR_GRAFITE),
        "alerta": Font(name=FONTE_MONO, size=10, bold=True, color=COR_VERMELHO),
        "borda": Border(left=lado, right=lado, top=lado, bottom=lado),
        "borda_total": Border(top=Side(style="thin", color=COR_GRAFITE)),
    }

    f_titulo = Font(name=FONTE_TITULO, size=16, bold=True, color=COR_AMBAR)
    f_subtitulo = Font(name=FONTE_TEXTO, size=11, italic=True, color=COR_GRAFITE)
    f_secao = Font(name=FONTE_TITULO, size=13, bold=True, color=COR_AMBAR)
    f_grupo = Font(name=FONTE_TEXTO, size=11, bold=True, color=COR_GRAFITE)
    f_tipo_baixado = Font(name=FONTE_TEXTO, size=10, bold=True, color=COR_AMBAR)
    f_tipo_naobaixado = Font(name=FONTE_TEXTO, size=10, bold=True, color=COR_GRAFITE)

    linha = 1
    ws.merge_cells(start_row=linha, start_column=1, end_row=linha, end_column=NUM_COLUNAS_GRID)
    c = ws.cell(row=linha, column=1, value="RELATÓRIO DE ORÇAMENTOS POR VENDEDOR")
    c.font = f_titulo
    c.alignment = Alignment(horizontal="center")
    linha += 1

    ws.merge_cells(start_row=linha, start_column=1, end_row=linha, end_column=NUM_COLUNAS_GRID)
    c = ws.cell(row=linha, column=1,
                value=f"Período: {dt_ini.strftime('%d/%m/%Y')} a {dt_fim.strftime('%d/%m/%Y')}")
    c.font = f_subtitulo
    c.alignment = Alignment(horizontal="center")
    linha += 2

    grupos = list(df.groupby(["VENDEDOR", "EMPRESA"], sort=True))

    # ── Resumo por vendedor ──────────────────────────────────────
    linha = _cabecalho_secao(ws, linha, "RESUMO POR VENDEDOR", f_secao)

    resumo_headers = ["Vendedor", "Empresa", "Qtd. Baixados", "Valor Baixados",
                       "Qtd. Não Baixados", "Valor Não Baixados", "Total Orçado", "% Conversão"]
    for col, texto in enumerate(resumo_headers, start=1):
        c = ws.cell(row=linha, column=col, value=texto)
        c.font = estilos["header"]
        c.fill = estilos["fill_header"]
        c.border = estilos["borda"]
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    linha += 1

    tot_baix_geral = 0.0
    tot_naobaix_geral = 0.0
    for (vendedor, empresa_nome), grupo in grupos:
        baix = grupo[grupo["STATUS"] == "Baixado"]
        naobaix = grupo[grupo["STATUS"] == "Não baixado"]
        v_baix = round(float(baix["TOTALORCAMENTO"].sum()), 2)
        v_naobaix = round(float(naobaix["TOTALORCAMENTO"].sum()), 2)
        tot_baix_geral += v_baix
        tot_naobaix_geral += v_naobaix
        conversao = v_baix / (v_baix + v_naobaix) if (v_baix + v_naobaix) else 0.0

        celulas = [
            (vendedor, "texto", "left"),
            (empresa_nome, "texto", "left"),
            (len(baix), "mono", "center"),
            (v_baix, "moeda", "right"),
            (len(naobaix), "mono", "center"),
            (v_naobaix, "moeda", "right"),
            (v_baix + v_naobaix, "moeda", "right"),
            (conversao, "percentual", "center"),
        ]
        for col, (valor, tipo, alinh) in enumerate(celulas, start=1):
            c = ws.cell(row=linha, column=col, value=valor)
            c.border = estilos["borda"]
            c.alignment = Alignment(horizontal=alinh)
            if tipo == "texto":
                c.font = estilos["texto"]
            elif tipo == "percentual":
                c.font = estilos["mono"]
                c.number_format = "0.0%"
            else:
                c.font = estilos["mono"]
                if tipo == "moeda":
                    c.number_format = '"R$" #,##0.00'
        linha += 1

    conv_geral = tot_baix_geral / (tot_baix_geral + tot_naobaix_geral) if (tot_baix_geral + tot_naobaix_geral) else 0.0
    ws.merge_cells(start_row=linha, start_column=1, end_row=linha, end_column=2)
    c = ws.cell(row=linha, column=1, value="TOTAL GERAL")
    c.font = estilos["mono_bold"]
    c.border = estilos["borda_total"]
    ws.cell(row=linha, column=2).border = estilos["borda_total"]

    totais_geral = {3: None, 4: tot_baix_geral, 5: None, 6: tot_naobaix_geral,
                    7: tot_baix_geral + tot_naobaix_geral, 8: conv_geral}
    for col in range(3, 9):
        c = ws.cell(row=linha, column=col, value=totais_geral[col])
        c.font = estilos["mono_bold"]
        c.border = estilos["borda_total"]
        if col in (4, 6, 7):
            c.number_format = '"R$" #,##0.00'
            c.alignment = Alignment(horizontal="right")
        elif col == 8:
            c.number_format = "0.0%"
            c.alignment = Alignment(horizontal="center")
        else:
            c.alignment = Alignment(horizontal="center")
    linha += 3

    # ── Detalhamento por vendedor ────────────────────────────────
    linha = _cabecalho_secao(ws, linha, "DETALHAMENTO POR VENDEDOR", f_secao)
    linha += 1

    colunas_baixado = [
        ("Nº", None, "seq"),
        ("Cód. Orç.", lambda r: to_int(r.CODORC), "codigo"),
        ("Cliente", lambda r: r.NOMECLIENTE, "texto"),
        ("Total Orçamento", lambda r: round(float(r.TOTALORCAMENTO), 2), "moeda"),
        ("Data Orçamento", lambda r: r.DATAORCAMENTO, "data"),
        ("Valor Venda", lambda r: round(float(r.TOTALPEDIDO), 2) if pd.notna(r.TOTALPEDIDO) else None, "moeda"),
        ("PDV", lambda r: to_int(r.CODPEDIDO), "codigo"),
        ("% Desc.", lambda r: desconto_percentual(r.TOTALORCAMENTO, r.TOTALPEDIDO), "percentual"),
    ]
    colunas_nao_baixado = [
        ("Nº", None, "seq"),
        ("Cód. Orç.", lambda r: to_int(r.CODORC), "codigo"),
        ("Cliente", lambda r: r.NOMECLIENTE, "texto"),
        ("Total Orçamento", lambda r: round(float(r.TOTALORCAMENTO), 2), "moeda"),
        ("Data Orçamento", lambda r: r.DATAORCAMENTO, "data"),
    ]

    for (vendedor, empresa_nome), grupo in grupos:
        codvendedor = grupo["CODVENDEDOR"].iloc[0]

        ws.merge_cells(start_row=linha, start_column=1, end_row=linha, end_column=NUM_COLUNAS_GRID)
        c = ws.cell(row=linha, column=1, value=f"{vendedor} — {empresa_nome} (cód. {codvendedor})")
        c.font = f_grupo
        linha += 1

        baix = grupo[grupo["STATUS"] == "Baixado"].sort_values(["DATAORCAMENTO", "CODORC"])
        naobaix = grupo[grupo["STATUS"] == "Não baixado"].sort_values(["DATAORCAMENTO", "CODORC"])

        linha = _escrever_tabela(ws, linha, "BAIXADOS", f_tipo_baixado, baix, colunas_baixado, estilos)
        linha = _escrever_tabela(ws, linha, "NÃO BAIXADOS", f_tipo_naobaixado, naobaix, colunas_nao_baixado, estilos)
        linha += 1

    if observacoes:
        linha = _cabecalho_secao(ws, linha, "OBSERVAÇÕES", f_secao)
        for texto in observacoes:
            ws.merge_cells(start_row=linha, start_column=1, end_row=linha, end_column=NUM_COLUNAS_GRID)
            c = ws.cell(row=linha, column=1, value=texto)
            c.font = f_subtitulo
            c.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[linha].height = 15 * (len(texto) // 150 + 1)
            linha += 1

    for i, largura in enumerate(LARGURAS_COLUNAS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = largura
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    return wb


# ── Programa principal ──────────────────────────────────────────

def main() -> None:
    empresas = escolher_empresas()
    dt_ini = pedir_data("inicial")
    dt_fim = pedir_data("final")

    print(f"\nConsultando {', '.join(empresas)} de {dt_ini.strftime('%d/%m/%Y')} "
          f"a {dt_fim.strftime('%d/%m/%Y')}...", flush=True)

    frames = []
    for nome, cfg in empresas.items():
        try:
            frames.append(buscar_orcamentos(nome, cfg, dt_ini, dt_fim))
        except Exception as e:
            print(f"  Falha ao consultar {nome}: {e}", file=sys.stderr)

    if not frames:
        print("Nenhum dado retornado. Verifique a conexão.")
        return

    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        print("Nenhum orçamento encontrado para esse período.")
        return

    total_periodo = len(df)
    df["VENDEDOR"] = df["VENDEDOR"].str.strip()
    acompanhado = df["VENDEDOR"].isin(VENDEDORES_ACOMPANHADOS)
    ignorados = df[~acompanhado]
    df, copias = remover_copias_identicas(df[acompanhado])
    if df.empty:
        print("Nenhum orçamento dos vendedores acompanhados nesse período.")
        return

    print(f"{total_periodo} orçamento(s) no período: {len(ignorados)} de outros vendedores e "
          f"{len(copias)} cópia(s) idêntica(s) descartada(s); {len(df)} no relatório.")
    wb = montar_relatorio(df, dt_ini, dt_fim, montar_observacoes(copias, ignorados))

    nome_arquivo = f"relatorio_orcamentos_vendedor_{dt_ini.isoformat()}_a_{dt_fim.isoformat()}.xlsx"
    candidatos = [
        Path.cwd() / nome_arquivo,
        Path.home() / "Downloads" / nome_arquivo,
        Path.home() / nome_arquivo,
    ]
    salvo = None
    ultimo_erro = None
    for caminho in candidatos:
        try:
            wb.save(caminho)
            salvo = caminho
            break
        except PermissionError as e:
            ultimo_erro = e
            continue

    if salvo is None:
        print(f"\nNão foi possível salvar o Excel em nenhuma pasta testada: {ultimo_erro}")
        print("Provável causa: Acesso Controlado a Pastas do Windows Defender bloqueando o python.exe.")
        print("Libere o python.exe em: Segurança do Windows > Proteção contra vírus e ameaças > "
              "Gerenciar proteção contra ransomware > Acesso controlado a pastas.")
        return

    print(f"\nArquivo gerado: {salvo}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("\nOcorreu um erro:\n")
        traceback.print_exc()
    finally:
        input("\nPressione Enter para sair...")
