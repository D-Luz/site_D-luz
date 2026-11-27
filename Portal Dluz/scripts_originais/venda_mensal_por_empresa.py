#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Venda mensal por empresa (ATS ERP — Firebird)
==============================================
Conecta nas 3 bases "Real" do Grupo Dluz, soma a venda de PDV (PEDIDOC/PEDIDOI)
por mês desde 2021 e gera um Excel com uma aba de dados + gráfico de tendência.

Dependências:
    pip install firebirdsql pandas openpyxl
"""

import sys
from datetime import date

import firebirdsql
import pandas as pd
from openpyxl.chart import LineChart, Reference
from openpyxl.utils import get_column_letter

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
    "Cor e Cia": {
        "host": "192.168.0.200",
        "database": "D:\\CorCiaR\\BANCO\\CORCIAR25.FB",
    },
    "Dluz Iluminação": {
        "host": "192.168.0.250",
        "database": "D:\\ILUMIR\\BANCO\\ILUMIR.FB",
    },
}

DATA_INICIO = "2021-01-01"

# TIPOPEDIDO: 55 = venda PDV, 51 = Nota Fiscal.
# Ajuste aqui se precisar incluir NF (51) além do PDV — ver aviso no script SQL irmão.
TIPOS_PEDIDO = (55,)

SQL = f"""
SELECT
    EXTRACT(YEAR  FROM PC.DATAPEDIDO) AS ANO,
    EXTRACT(MONTH FROM PC.DATAPEDIDO) AS MES,
    COUNT(DISTINCT PC.CODEMPRESA || '-' || PC.TIPOPEDIDO || '-' || PC.CODPEDIDO) AS QTD_PEDIDOS,
    SUM(PI.QUANTIDADE * PI.PRECOUNIT - PI.DESCONTOVLR) AS VALOR_VENDIDO
FROM PEDIDOC PC
JOIN PEDIDOI PI
    ON PI.CODEMPRESA = PC.CODEMPRESA
   AND PI.TIPOPEDIDO = PC.TIPOPEDIDO
   AND PI.CODPEDIDO  = PC.CODPEDIDO
WHERE PC.TIPOPEDIDO IN ({",".join("?" * len(TIPOS_PEDIDO))})
  AND PI.CANCELADO = 'N'
  AND PC.DATAPEDIDO >= ?
  AND PC.DATAPEDIDO <  ?
GROUP BY 1, 2
ORDER BY 1, 2
"""


def buscar_empresa(nome: str, cfg: dict) -> pd.DataFrame:
    hoje = date.today()
    amanha = date(hoje.year, hoje.month, hoje.day)
    params = (*TIPOS_PEDIDO, DATA_INICIO, amanha.isoformat())

    con = firebirdsql.connect(
        host=cfg["host"], database=cfg["database"], port=PORT,
        user=USER, password=PASSWORD, charset=CHARSET,
    )
    try:
        cur = con.cursor()
        cur.execute(SQL, params)
        colunas = [d[0] for d in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=colunas)
    finally:
        con.close()

    df.insert(0, "EMPRESA", nome)
    return df


def main() -> None:
    frames = []
    for nome, cfg in EMPRESAS.items():
        print(f"Consultando {nome}...", flush=True)
        try:
            df = buscar_empresa(nome, cfg)
            print(f"  ✔ {len(df)} meses encontrados")
            frames.append(df)
        except Exception as e:
            print(f"  ✗ Falha ao consultar {nome}: {e}", file=sys.stderr)

    if not frames:
        print("Nenhum dado retornado. Verifique conexões.", file=sys.stderr)
        sys.exit(1)

    dados = pd.concat(frames, ignore_index=True)
    dados["ANO"] = dados["ANO"].astype(int)
    dados["MES"] = dados["MES"].astype(int)
    dados["COMPETENCIA"] = dados["ANO"].astype(str) + "-" + dados["MES"].astype(str).str.zfill(2)

    # Tabela pivot: uma linha por competência, uma coluna por empresa
    pivot = dados.pivot_table(
        index="COMPETENCIA", columns="EMPRESA", values="VALOR_VENDIDO",
        aggfunc="sum", fill_value=0,
    ).reset_index().sort_values("COMPETENCIA")

    saida = "venda_mensal_por_empresa.xlsx"
    with pd.ExcelWriter(saida, engine="openpyxl") as writer:
        dados.sort_values(["EMPRESA", "ANO", "MES"]).to_excel(writer, sheet_name="Detalhado", index=False)
        pivot.to_excel(writer, sheet_name="Resumo Mensal", index=False)

        ws = writer.sheets["Resumo Mensal"]
        n_linhas = len(pivot) + 1
        n_colunas = len(pivot.columns)

        chart = LineChart()
        chart.title = "Venda mensal por empresa"
        chart.y_axis.title = "R$"
        chart.x_axis.title = "Mês"
        chart.style = 2

        dados_ref = Reference(ws, min_col=2, max_col=n_colunas, min_row=1, max_row=n_linhas)
        cats_ref = Reference(ws, min_col=1, min_row=2, max_row=n_linhas)
        chart.add_data(dados_ref, titles_from_data=True)
        chart.set_categories(cats_ref)
        chart.width = 26
        chart.height = 12

        ws.add_chart(chart, f"{get_column_letter(n_colunas + 2)}2")

    print(f"\nArquivo gerado: {saida}")


if __name__ == "__main__":
    main()
