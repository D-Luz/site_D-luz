#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ATS — Última NF de Compra por Produto (com impostos)
=====================================================
Para cada produto ativo e não bloqueado, busca a última NF de entrada
(NFENTRC/NFENTRI) com ICMS, ICMS-ST, IPI, PIS, COFINS, frete e preço
de entrada — nas 3 empresas do Grupo Dluz (banco Real).

Compatível com Firebird 2.5 (sem window functions — usa GROUP BY
em duas etapas em vez de ROW_NUMBER() OVER).

Dependências:
    pip install firebirdsql pandas openpyxl
"""

from datetime import datetime

import firebirdsql
import pandas as pd


# ═══════════════════════════════════════════════════════════════
#  CONFIGURAÇÃO DAS EMPRESAS  (banco Real)
# ═══════════════════════════════════════════════════════════════
EMPRESAS = {
    "Dluz Elétrica": {
        "host": "192.168.0.41",
        "database": r"D:\DLUZR\BANCO\DLUZR25.FB",
        "regime": "simples",
    },
    "Cor e Cia": {
        "host": "192.168.0.200",
        "database": r"D:\CorCiaR\BANCO\CORCIAR25.FB",
        "regime": "lucro_real",
    },
    "Dluz Iluminação": {
        "host": "192.168.0.250",
        "database": r"D:\ILUMIR\BANCO\ILUMIR.FB",
        "regime": "simples",
    },
}

# Alíquotas de crédito PIS/COFINS — regime não-cumulativo (Lucro Real)
ALIQ_CRED_PIS = 0.0165      # 1,65%
ALIQ_CRED_COFINS = 0.0760   # 7,60%

PORT = 3050
USER = "SYSDBA"
PASSWORD = "masterkey"
CHARSET = "WIN1252"


# ═══════════════════════════════════════════════════════════════
#  SQL — última NF de compra por produto (ativo e não bloqueado)
# ═══════════════════════════════════════════════════════════════
SQL_ULTIMA_NF = """
WITH ULT_DATA AS (
    SELECT I.CODPROD, MAX(C.DT_ENTRADA) AS DT_MAX
    FROM NFENTRI I
    JOIN NFENTRC C
        ON C.CODEMPRESA = I.CODEMPRESA
       AND C.NUMERONF   = I.NUMERONF
       AND C.CODFORNEC  = I.CODFORNEC
    WHERE C.DT_ENTRADA <= ?
    GROUP BY I.CODPROD
),
ULT_NF AS (
    SELECT UD.CODPROD, UD.DT_MAX, MAX(I.NUMERONF) AS NUMERONF_MAX
    FROM ULT_DATA UD
    JOIN NFENTRI I ON I.CODPROD = UD.CODPROD
    JOIN NFENTRC C
        ON C.CODEMPRESA = I.CODEMPRESA
       AND C.NUMERONF   = I.NUMERONF
       AND C.CODFORNEC  = I.CODFORNEC
       AND C.DT_ENTRADA = UD.DT_MAX
    GROUP BY UD.CODPROD, UD.DT_MAX
),
ESTOQUE_AGG AS (
    -- Agrega por grade (variante) pra ficar 1 linha por produto
    SELECT
        CODEMPRESA,
        CODPROD,
        SUM(ESTOQUE)            AS ESTOQUE_TOTAL,
        SUM(ESTOQUEDISPONIVEL)  AS ESTOQUE_DISPONIVEL,
        MAX(PRECOCUSTO)         AS PRECO_CUSTO_ATUAL
    FROM COMPPROD
    GROUP BY CODEMPRESA, CODPROD
)
SELECT
    P.CODPROD                   AS "Cod_Produto",
    TRIM(P.DESCRICAO)           AS "Descricao",
    TRIM(F.NOMEFANTASIA)        AS "Fornecedor",
    TRIM(I.NUMERONF)            AS "Numero_NF",
    C.SERIE                     AS "Serie",
    C.DT_ENTRADA                AS "Data_Entrada",
    I.QUANTIDADE                AS "Quantidade",
    I.PRECONF                   AS "Preco_Entrada",
    I.FRETE                     AS "Frete_Item",
    C.FRETENF                   AS "Frete_NF_Total",
    COALESCE(I.VLRRATEIOENCARGOS, 0)          AS "Encargos_Acessorios",
    COALESCE(I.DESCVLR, 0) * I.QUANTIDADE     AS "Desconto_Total",
    I.CFOP                      AS "CFOP",
    I.ALIQICM                   AS "Aliq_ICMS",
    I.VLRBASEICMS               AS "Base_ICMS",
    I.VLRICMS                   AS "Valor_ICMS",
    I.VALORSUBSTRIB              AS "Valor_ICMS_ST",
    I.BASEST                     AS "Base_ICMS_ST",
    I.ALIQIPI                    AS "Aliq_IPI",
    I.BASEIPI                    AS "Base_IPI",
    I.VLRIPI                     AS "Valor_IPI",
    I.CSTPIS                     AS "CST_PIS",
    I.ALIQPIS                    AS "Aliq_PIS",
    I.VLRPIS                     AS "Valor_PIS",
    I.CSTCOFINS                  AS "CST_COFINS",
    I.ALIQCOFINS                 AS "Aliq_COFINS",
    I.VLRCOFINS                  AS "Valor_COFINS",
    EA.ESTOQUE_TOTAL             AS "Estoque_Total",
    EA.ESTOQUE_DISPONIVEL        AS "Estoque_Disponivel",
    EA.PRECO_CUSTO_ATUAL         AS "Preco_Custo_Atual_ERP",
    P.PRECO                      AS "Preco_Venda",
    P.LUCRO                      AS "Lucro_Cadastrado_Perc",
    P.CODCLASFIS                 AS "Cod_Classificacao_Fiscal"
FROM ULT_NF U
JOIN NFENTRI I
    ON I.CODPROD  = U.CODPROD
   AND I.NUMERONF = U.NUMERONF_MAX
JOIN NFENTRC C
    ON C.CODEMPRESA = I.CODEMPRESA
   AND C.NUMERONF   = I.NUMERONF
   AND C.CODFORNEC  = I.CODFORNEC
   AND C.DT_ENTRADA = U.DT_MAX
JOIN PRODUTO P
    ON P.CODPROD = I.CODPROD
   AND P.ATIVO = 'S'
   AND P.PRODBLOQUEADO = 'N'
LEFT JOIN FORNECE F ON F.CODFORNEC = I.CODFORNEC
LEFT JOIN ESTOQUE_AGG EA
    ON EA.CODEMPRESA = I.CODEMPRESA
   AND EA.CODPROD    = I.CODPROD
ORDER BY P.DESCRICAO
"""


# ═══════════════════════════════════════════════════════════════
#  EXTRAÇÃO
# ═══════════════════════════════════════════════════════════════
def _read_firebird(con, sql: str, params: tuple = ()) -> pd.DataFrame:
    cur = con.cursor()
    try:
        cur.execute(sql, params)
        colunas = [desc[0] for desc in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=colunas)
    finally:
        cur.close()


def consolidar_duplicados(df: pd.DataFrame) -> pd.DataFrame:
    """Consolida múltiplas linhas do mesmo produto numa única linha.

    Um mesmo produto pode aparecer em mais de uma linha da mesma NF
    (grades diferentes, lotes ou correção de preço), cada uma com seu
    preço unitário. Sem tratamento, o relatório devolve duas linhas
    para o produto com custos divergentes e fica ambíguo qual é "o"
    custo. Aqui quantidades e valores são somados e o preço unitário
    passa a ser a média ponderada pela quantidade — mantendo a
    promessa de uma linha por produto.
    """
    if df.empty or not df["Cod_Produto"].duplicated().any():
        return df

    df = df.copy()
    df["_valor_item"] = df["Preco_Entrada"].fillna(0) * df["Quantidade"].fillna(0)

    # Colunas de VALOR (somáveis). Alíquotas, CFOP, CST, textos e dados
    # vindos de PRODUTO/COMPPROD são idênticos nas linhas repetidas —
    # para esses vale o primeiro valor.
    somaveis = [
        "Quantidade", "_valor_item", "Frete_Item", "Desconto_Total",
        "Encargos_Acessorios", "Base_ICMS", "Valor_ICMS", "Valor_ICMS_ST",
        "Base_ICMS_ST", "Base_IPI", "Valor_IPI", "Valor_PIS", "Valor_COFINS",
    ]
    agg = {c: "sum" for c in somaveis if c in df.columns}
    for coluna in df.columns:
        if coluna not in agg and coluna != "Cod_Produto":
            agg[coluna] = "first"

    consolidado = df.groupby("Cod_Produto", as_index=False, sort=False).agg(agg)

    qtd = consolidado["Quantidade"].where(consolidado["Quantidade"] != 0)
    consolidado["Preco_Entrada"] = (consolidado["_valor_item"] / qtd).round(4)
    return consolidado.drop(columns=["_valor_item"])


def enriquecer(df: pd.DataFrame, regime: str = "simples", data_referencia: pd.Timestamp = None) -> pd.DataFrame:
    """Adiciona colunas calculadas de análise.

    regime: "simples" (sem créditos) ou "lucro_real" (créditos de
    ICMS + PIS/COFINS não-cumulativo).
    data_referencia: data usada como "hoje" para calcular a idade da
    compra — por padrão a data atual, mas pode ser a mesma data
    máxima usada para filtrar a última NF, deixando a idade coerente
    com a consulta.
    """
    if df.empty:
        return df

    # ── Idade da última compra ─────────────────────────────────
    df["Data_Entrada"] = pd.to_datetime(df["Data_Entrada"])
    hoje = data_referencia if data_referencia is not None else pd.Timestamp.now().normalize()
    df["Dias_Desde_Compra"] = (hoje - df["Data_Entrada"]).dt.days
    df["Compra_Antiga_24m"] = df["Dias_Desde_Compra"].apply(
        lambda d: "S" if pd.notna(d) and d > 730 else "N"
    )

    # ── Origem da compra pelo 1º dígito do CFOP ────────────────
    # 1xxx = interna (dentro do estado) | 2xxx = interestadual
    # 3xxx = importação
    primeiro_digito = df["CFOP"].astype(str).str.strip().str[0]
    df["Origem_CFOP"] = primeiro_digito.map(
        {"1": "Interna", "2": "Interestadual", "3": "Importação"}
    ).fillna("Outros")

    # ── Custo unitário efetivo ─────────────────────────────────
    # CPC 16 (item 11): o custo de aquisição compreende o preço de
    # compra, os tributos não recuperáveis, o transporte e os demais
    # custos diretamente atribuíveis — deduzidos os descontos
    # comerciais e abatimentos.
    # (Total do item + IPI + ICMS-ST + Frete + Encargos - Desconto) / Qtd
    # Preco_Entrada é o valor UNITÁRIO da NF (validado: PRECONF *
    # QUANTIDADE = VLRBASEICMS em todas as amostras conferidas).
    qtd = df["Quantidade"].where(df["Quantidade"] != 0)
    acrescimos = (
        df["Valor_IPI"].fillna(0)
        + df["Valor_ICMS_ST"].fillna(0)
        + df["Frete_Item"].fillna(0)
        + df["Encargos_Acessorios"].fillna(0)
    )
    desconto = df["Desconto_Total"].fillna(0)
    df["Valor_Total_Item"] = df["Preco_Entrada"].fillna(0) * df["Quantidade"].fillna(0)
    custo_bruto_total = df["Valor_Total_Item"] + acrescimos - desconto
    df["Custo_Unit_Efetivo"] = (custo_bruto_total / qtd).round(4)

    # ── Custo líquido por regime tributário ────────────────────
    if regime == "lucro_real":
        # Créditos recuperáveis (não-cumulativo):
        #  - ICMS "próprio": só existe crédito quando NÃO há ICMS-ST
        #    na operação. Quando há substituição tributária, o
        #    imposto de toda a cadeia (inclusive a operação própria
        #    de revenda) já foi recolhido antecipadamente pelo
        #    substituto — sem crédito na entrada, sem débito na
        #    saída. Isso vale para revenda como mercadoria (perfil
        #    do Grupo Dluz como distribuidor); se algum item for
        #    usado como insumo de industrialização, pode haver
        #    direito a ressarcimento do ICMS-ST — não coberto aqui.
        #  - PIS 1,65% e COFINS 7,60% sobre a base de aquisição,
        #    EXCLUINDO o ICMS destacado (Lei 14.592/2023) e sem
        #    IPI nem ICMS-ST na base. O desconto comercial reduz o
        #    valor da aquisição, então também reduz a base do crédito.
        tem_st = df["Valor_ICMS_ST"].fillna(0) > 0
        vlr_icms = df["Valor_ICMS"].fillna(0).where(~tem_st, 0.0)
        base_pis_cofins = (
            df["Valor_Total_Item"] + df["Frete_Item"].fillna(0)
            - desconto - vlr_icms
        ).clip(lower=0)
        df["Credito_ICMS"] = vlr_icms.round(2)
        df["Credito_PIS"] = (base_pis_cofins * ALIQ_CRED_PIS).round(2)
        df["Credito_COFINS"] = (base_pis_cofins * ALIQ_CRED_COFINS).round(2)
        creditos = df["Credito_ICMS"] + df["Credito_PIS"] + df["Credito_COFINS"]
    else:
        # Simples Nacional: nenhum crédito na entrada
        df["Credito_ICMS"] = 0.0
        df["Credito_PIS"] = 0.0
        df["Credito_COFINS"] = 0.0
        creditos = 0.0

    df["Custo_Unit_Liquido"] = ((custo_bruto_total - creditos) / qtd).round(4)

    # ── Margem bruta sobre o preço de venda cadastrado ─────────
    # (PV - custo) / PV — margem sobre a RECEITA, não sobre o custo.
    # Usa o custo LÍQUIDO (pós-créditos), que é o custo real.
    # ATENÇÃO: é margem ANTES dos tributos sobre a venda — não
    # considera o DAS (Simples) nem os débitos de ICMS/PIS/COFINS
    # (Lucro Real). Por isso a margem de empresas de regimes
    # diferentes não é diretamente comparável.
    preco_venda = df["Preco_Venda"].where(df["Preco_Venda"] > 0)
    df["Margem_Bruta_Sobre_PV_Perc"] = (
        (preco_venda - df["Custo_Unit_Liquido"]) / preco_venda * 100
    ).round(2)

    # ── Reordena: colunas calculadas ao lado das relacionadas ──
    ordem = [
        "Cod_Produto", "Descricao", "Cod_Classificacao_Fiscal", "Fornecedor",
        "Numero_NF", "Serie", "Data_Entrada",
        "Dias_Desde_Compra", "Compra_Antiga_24m",
        "Quantidade", "Preco_Entrada", "Valor_Total_Item",
        "Desconto_Total", "Encargos_Acessorios",
        "Custo_Unit_Efetivo",
        "Credito_ICMS", "Credito_PIS", "Credito_COFINS",
        "Custo_Unit_Liquido",
        "Frete_Item", "Frete_NF_Total",
        "CFOP", "Origem_CFOP",
        "Aliq_ICMS", "Base_ICMS", "Valor_ICMS",
        "Valor_ICMS_ST", "Base_ICMS_ST",
        "Aliq_IPI", "Base_IPI", "Valor_IPI",
        "CST_PIS", "Aliq_PIS", "Valor_PIS",
        "CST_COFINS", "Aliq_COFINS", "Valor_COFINS",
        "Estoque_Total", "Estoque_Disponivel", "Preco_Custo_Atual_ERP",
        "Preco_Venda", "Lucro_Cadastrado_Perc", "Margem_Bruta_Sobre_PV_Perc",
    ]
    return df[[c for c in ordem if c in df.columns]]


def pedir_data_referencia() -> datetime:
    """Pergunta ao usuário a data máxima de referência para a 'última compra'.
    Enter em branco = hoje (comportamento original, sem filtro de data)."""
    while True:
        bruto = input(
            "Data de referência máxima para a última compra (DD/MM/AAAA, "
            "Enter = hoje): "
        ).strip()
        if not bruto:
            return datetime.now()
        try:
            return datetime.strptime(bruto, "%d/%m/%Y")
        except ValueError:
            print("  ✗ Data inválida. Use o formato DD/MM/AAAA.")


def extrair_empresa(nome_empresa: str, data_referencia: datetime) -> pd.DataFrame:
    cfg = EMPRESAS[nome_empresa]
    print(f"  Conectando em {nome_empresa} ({cfg['host']})...")
    con = firebirdsql.connect(
        host=cfg["host"],
        database=cfg["database"],
        port=PORT,
        user=USER,
        password=PASSWORD,
        charset=CHARSET,
    )
    try:
        df = _read_firebird(
            con, SQL_ULTIMA_NF, params=(data_referencia.strftime("%Y-%m-%d"),)
        )
    finally:
        con.close()
    df = consolidar_duplicados(df)
    df = enriquecer(
        df,
        regime=cfg.get("regime", "simples"),
        data_referencia=pd.Timestamp(data_referencia).normalize(),
    )
    df.insert(0, "Empresa", nome_empresa)
    print(f"  → {len(df)} produtos")
    return df


def main():
    print("Extraindo última NF de compra por produto — Grupo Dluz\n")
    data_referencia = pedir_data_referencia()
    print(f"  Referência: compras até {data_referencia.strftime('%d/%m/%Y')}\n")

    resultados = {}
    erros = []
    for nome_empresa in EMPRESAS:
        try:
            resultados[nome_empresa] = extrair_empresa(nome_empresa, data_referencia)
        except Exception as ex:
            erros.append(f"{nome_empresa}: {ex}")
            print(f"  ERRO: {ex}")

    if not resultados:
        print("\nNenhuma empresa retornou dados. Abortando.")
        return

    ts_ref = data_referencia.strftime("%Y%m%d")
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    nome_arquivo = f"Ultima_NF_Compra_ate_{ts_ref}_gerado_{ts}.xlsx"

    with pd.ExcelWriter(nome_arquivo, engine="openpyxl") as writer:
        for nome_empresa, df in resultados.items():
            # Remove a coluna "Empresa" (redundante, já que cada aba já é a empresa)
            df_aba = df.drop(columns=["Empresa"])
            # Nome de aba no Excel tem limite de 31 caracteres e não aceita
            # alguns caracteres especiais
            nome_aba = nome_empresa[:31]
            df_aba.to_excel(writer, sheet_name=nome_aba, index=False)

    total_geral = sum(len(df) for df in resultados.values())
    print(f"\nTotal geral: {total_geral} produtos em {len(resultados)} aba(s)")
    print(f"Arquivo gerado: {nome_arquivo}")

    if erros:
        print("\nAtenção — falhas em algumas empresas:")
        for e in erros:
            print(f"  - {e}")


if __name__ == "__main__":
    main()
