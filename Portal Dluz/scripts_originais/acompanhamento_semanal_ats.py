#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Acompanhamento Semanal — ATS / Resulth Business · Grupo Dluz
==============================================================
Roda o acompanhamento nas três empresas (Dluz Elétrica, Cor e Cia,
Dluz Iluminação) em paralelo e consolida num único Excel, para seis
períodos:

  A) Semana anterior completa (segunda a sábado)
  B) Mesma semana do ano anterior (-364 dias, mantém segunda x segunda)
  C) Mês atual — MTD (dia 01 até hoje)
  D) Mesmo mês do ano anterior — MTD (dia 01 até o mesmo dia, ano -1)
  E) Ano atual — YTD (01/01 até hoje)
  F) Mesmo período do ano anterior — YTD (01/01 até o mesmo dia, ano -1)

Cada período é calculado e filtrado de forma independente (não é uma
classificação mutuamente exclusiva por linha) — senão uma venda da
"semana anterior" ficaria de fora do total do "mês atual", já que a
semana está contida no mês.

Conteúdo:
  · Receita por forma de pagamento — uma aba "Consolidado" + uma por
    empresa, com os 6 períodos e subtotais à vista/prazo/total.
  · Liquidações da semana (A/B) — detalhado, linha por transação, com
    nome do cliente (mesmo formato do relatório nativo já validado).
  · Liquidações do MTD/YTD (C/D/E/F) — agrupadas por cliente (soma de
    bruto, juros, desconto, recebido e quantidade de documentos), já
    que o volume de transações no mês/ano inteiro inviabiliza o
    detalhe linha a linha numa planilha.

As datas são calculadas em Python e passadas como parâmetros bindados
ao driver — não depende de CURRENT_TIMESTAMP/CURRENT_DATE no SQL.

Uso:
    python3 acompanhamento_semanal_ats.py [-o saida.xlsx] [--data AAAA-MM-DD]

Sem argumentos, salva "Acompanhamento_semanal_<inicio>_a_<fim>.xlsx" na
pasta do script. --data serve para reprocessar um dia passado (usa essa
data como se fosse "hoje" ao calcular os períodos).
"""

import argparse
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import firebirdsql
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

# ══════════════════════════════════════════════════════════════
#  CONEXÕES — mesmas três bases do Extracao_somatorio_v9.py
# ══════════════════════════════════════════════════════════════
EMPRESAS = {
    "Dluz Elétrica":   {"HOST": "192.168.0.41",  "DATABASE": r"D:\DLUZR\BANCO\DLUZR25.FB"},
    "Cor e Cia":       {"HOST": "192.168.0.200", "DATABASE": r"D:\CorCiaR\BANCO\CORCIAR25.FB"},
    "Dluz Iluminação": {"HOST": "192.168.0.250", "DATABASE": r"D:\ILUMIR\BANCO\ILUMIR.FB"},
}
USER, PASSWORD, PORT, CHARSET = "SYSDBA", "masterkey", 3050, "WIN1252"

# ══════════════════════════════════════════════════════════════
#  IDENTIDADE VISUAL GRUPO DLUZ (Excel)
# ══════════════════════════════════════════════════════════════
GRAFITE    = "241F18"
AMBAR_SOFT = "F0E5CD"
FONTE_MONO = "Consolas"
FMT_BR     = '#,##0.00'
FMT_INT    = '#,##0'


# ══════════════════════════════════════════════════════════════
#  PERÍODOS — cada um é uma janela [ini, lim) independente
# ══════════════════════════════════════════════════════════════
def periodos_definidos(hoje: date):
    seg_desta_semana = hoje - timedelta(days=hoje.weekday())  # weekday(): segunda=0
    ini_semana_atual = seg_desta_semana - timedelta(days=7)
    lim_semana_atual = ini_semana_atual + timedelta(days=6)           # domingo 00h, usar "<"
    ini_semana_ano_ant = ini_semana_atual - timedelta(days=364)
    lim_semana_ano_ant = lim_semana_atual - timedelta(days=364)

    ini_mtd_atual = hoje.replace(day=1)
    lim_mtd_atual = hoje + timedelta(days=1)                          # inclui hoje

    try:
        mesmo_dia_ano_ant = hoje.replace(year=hoje.year - 1)
    except ValueError:                                                # 29/fev sem par no ano anterior
        mesmo_dia_ano_ant = date(hoje.year - 1, 2, 28)
    ini_mtd_ano_ant = mesmo_dia_ano_ant.replace(day=1)
    lim_mtd_ano_ant = mesmo_dia_ano_ant + timedelta(days=1)

    ini_ytd_atual   = date(hoje.year, 1, 1)
    lim_ytd_atual   = lim_mtd_atual
    ini_ytd_ano_ant = date(hoje.year - 1, 1, 1)
    lim_ytd_ano_ant = lim_mtd_ano_ant

    return {
        "A) Semana anterior":                  (ini_semana_atual,   lim_semana_atual),
        "B) Mesma semana ano anterior":        (ini_semana_ano_ant, lim_semana_ano_ant),
        "C) Mes atual (MTD)":                  (ini_mtd_atual,      lim_mtd_atual),
        "D) Mesmo mes ano anterior (MTD)":     (ini_mtd_ano_ant,    lim_mtd_ano_ant),
        "E) Ano atual (YTD)":                  (ini_ytd_atual,      lim_ytd_atual),
        "F) Mesmo periodo ano anterior (YTD)": (ini_ytd_ano_ant,    lim_ytd_ano_ant),
    }


# Períodos do "lado atual" (ano corrente) e do "lado ano anterior" —
# cada lado é buscado no banco com uma única consulta (faixa que já
# cobre semana + mês + ano daquele lado), e os períodos individuais
# são recortados em Python a partir do resultado.
PERIODOS_ATUAL   = ["A) Semana anterior", "C) Mes atual (MTD)", "E) Ano atual (YTD)"]
PERIODOS_ANO_ANT = ["B) Mesma semana ano anterior", "D) Mesmo mes ano anterior (MTD)",
                     "F) Mesmo periodo ano anterior (YTD)"]

# Períodos cujas liquidações vão detalhadas (linha por transação)
PERIODOS_LIQ_DETALHE = {"A) Semana anterior": "liq_atual",
                         "B) Mesma semana ano anterior": "liq_ano_ant"}
# Períodos cujas liquidações vão agrupadas por cliente (MTD/YTD)
PERIODOS_LIQ_AGRUPADO = {"C) Mes atual (MTD)": "liq_atual",
                          "D) Mesmo mes ano anterior (MTD)": "liq_ano_ant",
                          "E) Ano atual (YTD)": "liq_atual",
                          "F) Mesmo periodo ano anterior (YTD)": "liq_ano_ant"}


def _limites(periodos, labels):
    """Faixa [ini, lim) que cobre todos os períodos de um lado (semana ⊂ mês ⊂ ano)."""
    inis = [periodos[l][0] for l in labels]
    lims = [periodos[l][1] for l in labels]
    return min(inis), max(lims)


# ══════════════════════════════════════════════════════════════
#  SQL — parametrizado (datas como bind params, sem CURRENT_TIMESTAMP).
#  Sem CASE de período: cada consulta traz a faixa bruta de um "lado"
#  (atual ou ano anterior); a classificação por período é feita em
#  pandas, permitindo janelas sobrepostas (semana dentro do mês, etc.).
# ══════════════════════════════════════════════════════════════
SQL_VENDAS = """
SELECT
  c.DATAPEDIDO                   AS DATAPEDIDO,
  COALESCE(e.VLRDINHEIRO,0)      AS VLRDINHEIRO,
  COALESCE(e.VLRCARTAO,0)        AS VLRCARTAO,
  COALESCE(e.VLRCHEQUE,0)        AS VLRCHEQUE,
  COALESCE(e.VLRCONVENIO,0)      AS VLRCONVENIO,
  COALESCE(e.VLRCHPREDATADO,0)   AS VLRCHPREDATADO,
  COALESCE(e.VLRRECEBER,0)       AS VLRRECEBER,
  COALESCE(c.TOTALPEDIDO,0)      AS TOTALPEDIDO
FROM PEDIDOC c
LEFT JOIN ENCEFAT e ON c.CODPEDIDO = e.CODPEDIDO AND c.TIPOPEDIDO = e.TIPOPEDIDO
WHERE c.TIPOPEDIDO = 55
  AND c.DATAPEDIDO >= ? AND c.DATAPEDIDO < ?
"""

SQL_LIQUIDACOES = """
SELECT
  TRIM(cli.NOME)                                           AS CLIENTE,
  m.TIPODOCTO                                              AS TD,
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
  COALESCE(m.VALORMOV,0)                                   AS VLR_RECEBIDO,
  m.NUMDOCPGTO                                             AS DOC_PGTO,
  m.USUARIO                                                AS USUARIO
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
    ("1. Dinheiro",          "VLRDINHEIRO",     "A vista"),
    ("2. Cartao",            "VLRCARTAO",       "A vista"),
    ("3. Cheque a vista",    "VLRCHEQUE",       "A vista"),
    ("4. Convenio",          "VLRCONVENIO",     "A vista"),
    ("5. Cheque pre-datado", "VLRCHPREDATADO",  "A prazo"),
    ("6. Contas a receber",  "VLRRECEBER",      "A prazo"),
]
COLS_VISTA = ("VLRDINHEIRO", "VLRCARTAO", "VLRCHEQUE", "VLRCONVENIO")
COLS_PRAZO = ("VLRCHPREDATADO", "VLRRECEBER")
COLS_LIQ_VALOR = ["VLR_BRUTO", "JUROS", "DESCONTO", "VLR_RECEBIDO"]


# ══════════════════════════════════════════════════════════════
#  EXTRAÇÃO — mesmo idiom do projeto: cur.execute() + fetchall()
# ══════════════════════════════════════════════════════════════
def _consultar(con, sql, params):
    cur = con.cursor()
    try:
        cur.execute(sql, params)
        colunas = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=colunas)
    finally:
        cur.close()


def consultar_empresa(nome, cfg, periodos):
    """Busca as faixas 'atual' e 'ano anterior' (vendas + liquidações) numa empresa."""
    ini_atual, lim_atual = _limites(periodos, PERIODOS_ATUAL)
    ini_ano_ant, lim_ano_ant = _limites(periodos, PERIODOS_ANO_ANT)

    con = firebirdsql.connect(
        host=cfg["HOST"], database=cfg["DATABASE"],
        port=PORT, user=USER, password=PASSWORD, charset=CHARSET,
    )
    try:
        dados = {
            "vendas_atual":   _consultar(con, SQL_VENDAS, (ini_atual.strftime("%Y-%m-%d"), lim_atual.strftime("%Y-%m-%d"))),
            "vendas_ano_ant": _consultar(con, SQL_VENDAS, (ini_ano_ant.strftime("%Y-%m-%d"), lim_ano_ant.strftime("%Y-%m-%d"))),
            "liq_atual":      _consultar(con, SQL_LIQUIDACOES, (ini_atual.strftime("%Y-%m-%d"), lim_atual.strftime("%Y-%m-%d"))),
            "liq_ano_ant":    _consultar(con, SQL_LIQUIDACOES, (ini_ano_ant.strftime("%Y-%m-%d"), lim_ano_ant.strftime("%Y-%m-%d"))),
        }
    finally:
        con.close()

    for df in dados.values():
        if not df.empty:
            df.insert(0, "EMPRESA", nome)
    return dados


def buscar_todas_empresas(periodos):
    """Consulta as três empresas em paralelo; tolera falha parcial."""
    resultados, falhas = {}, []
    with ThreadPoolExecutor(max_workers=len(EMPRESAS)) as pool:
        futuros = {
            pool.submit(consultar_empresa, nome, cfg, periodos): nome
            for nome, cfg in EMPRESAS.items()
        }
        for fut in as_completed(futuros):
            nome = futuros[fut]
            try:
                resultados[nome] = fut.result()
            except Exception as exc:
                falhas.append((nome, str(exc)))
    return resultados, falhas


def _concat_nonempty(dfs):
    dfs = [d for d in dfs if d is not None and not d.empty]
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


# ══════════════════════════════════════════════════════════════
#  PARTE 1 — RECEITA POR FORMA DE PAGAMENTO, para os 6 períodos
# ══════════════════════════════════════════════════════════════
def montar_receita(dados, periodos):
    """`dados` precisa ter as chaves vendas_atual/vendas_ano_ant já filtráveis por data."""
    linhas = []
    for label, (ini, lim) in periodos.items():
        lado = "vendas_atual" if label in PERIODOS_ATUAL else "vendas_ano_ant"
        df = dados[lado]
        if not df.empty:
            dt = pd.to_datetime(df["DATAPEDIDO"])
            grupo = df.loc[(dt >= pd.Timestamp(ini)) & (dt < pd.Timestamp(lim))]
        else:
            grupo = df

        if grupo.empty:
            valores = {c: 0.0 for c in COLS_VISTA + COLS_PRAZO}
            total = 0.0
        else:
            valores = {c: grupo[c].sum() for c in COLS_VISTA + COLS_PRAZO}
            total = grupo["TOTALPEDIDO"].sum()

        for rotulo, col, natureza in FORMAS:
            linhas.append((label, rotulo, natureza, valores[col]))
        linhas.append((label, "7. SUBTOTAL A VISTA", "Subtotal", sum(valores[c] for c in COLS_VISTA)))
        linhas.append((label, "8. SUBTOTAL A PRAZO", "Subtotal", sum(valores[c] for c in COLS_PRAZO)))
        linhas.append((label, "9. RECEITA TOTAL",    "Total",    total))

    return pd.DataFrame(linhas, columns=["PERIODO", "FORMA_PAGAMENTO", "NATUREZA", "VALOR"])


# ══════════════════════════════════════════════════════════════
#  PARTE 2 — LIQUIDAÇÕES: detalhado (semana) e agrupado por cliente (MTD/YTD)
# ══════════════════════════════════════════════════════════════
def liquidacoes_semana(resultados, periodos):
    partes = []
    for empresa, dados in resultados.items():
        for label, chave in PERIODOS_LIQ_DETALHE.items():
            df = dados[chave]
            if df.empty:
                continue
            ini, lim = periodos[label]
            dt = pd.to_datetime(df["DT_LIQUIDACAO"])
            sub = df.loc[(dt >= pd.Timestamp(ini)) & (dt < pd.Timestamp(lim))].copy()
            if sub.empty:
                continue
            sub.insert(1, "PERIODO", label)
            partes.append(sub)

    if not partes:
        return pd.DataFrame(columns=[
            "EMPRESA", "PERIODO", "CLIENTE", "TD", "DOCUMENTO", "EMISSAO",
            "VENCIMENTO", "DT_LIQUIDACAO", "FORMA_PGTO", "VLR_BRUTO",
            "JUROS", "DESCONTO", "VLR_RECEBIDO", "DOC_PGTO", "USUARIO",
        ])
    return pd.concat(partes, ignore_index=True).sort_values(
        ["PERIODO", "DT_LIQUIDACAO", "EMPRESA"], ignore_index=True)


def liquidacoes_por_cliente(resultados, periodos):
    """Liquidações do MTD/YTD, agregadas por empresa+cliente (volume não cabe em detalhe)."""
    partes = []
    for empresa, dados in resultados.items():
        for label, chave in PERIODOS_LIQ_AGRUPADO.items():
            df = dados[chave]
            if df.empty:
                continue
            ini, lim = periodos[label]
            dt = pd.to_datetime(df["DT_LIQUIDACAO"])
            sub = df.loc[(dt >= pd.Timestamp(ini)) & (dt < pd.Timestamp(lim))]
            if sub.empty:
                continue
            agrupado = (
                sub.groupby("CLIENTE", dropna=False)
                   .agg(QTD_DOCUMENTOS=("DOCUMENTO", "count"),
                        VLR_BRUTO=("VLR_BRUTO", "sum"),
                        JUROS=("JUROS", "sum"),
                        DESCONTO=("DESCONTO", "sum"),
                        VLR_RECEBIDO=("VLR_RECEBIDO", "sum"))
                   .reset_index()
            )
            agrupado.insert(0, "EMPRESA", empresa)
            agrupado.insert(1, "PERIODO", label)
            partes.append(agrupado)

    if not partes:
        return pd.DataFrame(columns=[
            "EMPRESA", "PERIODO", "CLIENTE", "QTD_DOCUMENTOS",
            "VLR_BRUTO", "JUROS", "DESCONTO", "VLR_RECEBIDO",
        ])
    return pd.concat(partes, ignore_index=True).sort_values(
        ["PERIODO", "EMPRESA", "VLR_RECEBIDO"], ascending=[True, True, False],
        ignore_index=True)


# ══════════════════════════════════════════════════════════════
#  EXCEL — identidade visual Grupo Dluz
# ══════════════════════════════════════════════════════════════
def _estilizar_cabecalho(ws, ncols, linha=1):
    for col in range(1, ncols + 1):
        cel = ws.cell(row=linha, column=col)
        cel.fill = PatternFill("solid", fgColor=GRAFITE)
        cel.font = Font(color="FBF7F0", bold=True, name="Segoe UI", size=10)
        cel.alignment = Alignment(horizontal="center", vertical="center")


def _ajustar_larguras(ws, df, largura_min=10, largura_max=45):
    for idx, col in enumerate(df.columns, start=1):
        letra = get_column_letter(idx)
        maior = max([len(str(col))] + [len(str(v)) for v in df[col].astype(str)])
        ws.column_dimensions[letra].width = min(largura_max, max(largura_min, maior + 2))


def _formatar_numericas(ws, df, colunas_br=(), colunas_int=(), linha_ini=2):
    for idx, col in enumerate(df.columns, start=1):
        if col not in colunas_br and col not in colunas_int:
            continue
        letra = get_column_letter(idx)
        fmt = FMT_BR if col in colunas_br else FMT_INT
        for cel in ws[letra][linha_ini - 1:]:
            cel.number_format = fmt
            cel.font = Font(name=FONTE_MONO, size=10)


def _destacar_linhas_totais(ws, df, coluna_chave, marcadores):
    """Deixa em negrito/âmbar as linhas cuja `coluna_chave` começa com um dos `marcadores`."""
    if coluna_chave not in df.columns:
        return
    for i, valor in enumerate(df[coluna_chave], start=2):
        if any(str(valor).startswith(m) for m in marcadores):
            for col in range(1, len(df.columns) + 1):
                cel = ws.cell(row=i, column=col)
                cel.fill = PatternFill("solid", fgColor=AMBAR_SOFT)
                cel.font = Font(bold=True, name=cel.font.name, size=cel.font.size,
                                 color=GRAFITE)


def _escrever_aba(writer, nome_aba, df, colunas_br=(), colunas_int=(),
                   destaque_col=None, destaque_marcadores=()):
    nome_aba = nome_aba[:31]
    df.to_excel(writer, sheet_name=nome_aba, index=False)
    ws = writer.sheets[nome_aba]
    _estilizar_cabecalho(ws, len(df.columns))
    _ajustar_larguras(ws, df)
    _formatar_numericas(ws, df, colunas_br, colunas_int)
    if destaque_col:
        _destacar_linhas_totais(ws, df, destaque_col, destaque_marcadores)
    ws.freeze_panes = "A2"


def exportar_excel(caminho, receita_por_empresa, receita_consolidada,
                    df_liq_semana, df_liq_cliente, periodos):
    with pd.ExcelWriter(caminho, engine="openpyxl") as writer:
        abas_receita = [("Consolidado", receita_consolidada)] + list(receita_por_empresa.items())
        for nome_aba, df in abas_receita:
            _escrever_aba(writer, f"Receita {nome_aba}"[:31], df,
                          colunas_br={"VALOR"}, destaque_col="FORMA_PAGAMENTO",
                          destaque_marcadores=("7. SUBTOTAL", "8. SUBTOTAL", "9. RECEITA"))

        _escrever_aba(writer, "Liquidacoes Semana", df_liq_semana,
                      colunas_br=set(COLS_LIQ_VALOR))

        _escrever_aba(writer, "Liquidacoes MTD-YTD Cliente", df_liq_cliente,
                      colunas_br=set(COLS_LIQ_VALOR), colunas_int={"QTD_DOCUMENTOS"})

        info = pd.DataFrame(
            [[label, ini, lim - timedelta(days=1)] for label, (ini, lim) in periodos.items()],
            columns=["Periodo", "Inicio", "Fim"],
        )
        _escrever_aba(writer, "Periodos", info)


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-o", "--output", type=Path, default=None,
                         help="Caminho do Excel de saída")
    parser.add_argument("--data", type=str, default=None,
                         help="Trata esta data (AAAA-MM-DD) como 'hoje' ao calcular os períodos")
    args = parser.parse_args()

    hoje = (datetime.strptime(args.data, "%Y-%m-%d").date() if args.data else date.today())
    periodos = periodos_definidos(hoje)

    print("Períodos calculados:")
    for label, (ini, lim) in periodos.items():
        print(f"  {label:<38} {ini:%d/%m/%Y} a {(lim - timedelta(days=1)):%d/%m/%Y}")
    print("Consultando as três empresas em paralelo...")

    resultados, falhas = buscar_todas_empresas(periodos)

    if not resultados:
        detalhe = "\n".join(f"  - {n}: {e}" for n, e in falhas)
        print(f"ERRO: não foi possível consultar nenhuma empresa.\n{detalhe}", file=sys.stderr)
        sys.exit(1)

    if falhas:
        for nome, erro in falhas:
            print(f"AVISO: falha ao consultar {nome}: {erro}", file=sys.stderr)

    receita_por_empresa = {
        nome: montar_receita(dados, periodos) for nome, dados in resultados.items()
    }

    dados_consolidado = {
        chave: _concat_nonempty([dados[chave] for dados in resultados.values()])
        for chave in ("vendas_atual", "vendas_ano_ant", "liq_atual", "liq_ano_ant")
    }
    receita_consolidada = montar_receita(dados_consolidado, periodos)

    df_liq_semana = liquidacoes_semana(resultados, periodos)
    df_liq_cliente = liquidacoes_por_cliente(resultados, periodos)

    if args.output:
        caminho = args.output
    else:
        ini_semana, lim_semana = periodos["A) Semana anterior"]
        sufixo = f"{ini_semana:%Y%m%d}_a_{(lim_semana - timedelta(days=1)):%Y%m%d}"
        caminho = Path(__file__).with_name(f"Acompanhamento_semanal_{sufixo}.xlsx")

    exportar_excel(caminho, receita_por_empresa, receita_consolidada,
                    df_liq_semana, df_liq_cliente, periodos)

    total_receita = receita_consolidada.loc[
        receita_consolidada["FORMA_PAGAMENTO"] == "9. RECEITA TOTAL"
    ]
    print("\nReceita total por período (consolidado das empresas OK):")
    for _, row in total_receita.iterrows():
        valor_fmt = f"{row['VALOR']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        print(f"  {row['PERIODO']:<38} R$ {valor_fmt}")

    print(f"\nPlanilha gerada em: {caminho}")


if __name__ == "__main__":
    main()
