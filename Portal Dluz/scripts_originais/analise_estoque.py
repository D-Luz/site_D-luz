#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ATS — Extração + Análise de Estoque (Curva ABC × XYZ) integrados
=================================================================
Conecta direto no Firebird do ATS, extrai posição de estoque e
movimentação (PDV) de toda a base e roda a análise ABC × XYZ com
estoque de segurança adaptado, cobertura e demanda irregular.

A análise é SEMPRE feita para TODOS os produtos ativos — inclusive os
que não venderam nada no período, que entram com média zero e caem
naturalmente na Curva C. Não há mais uma aba separada de "Sem Giro":
toda linha tem uma coluna "Sem Giro (m)" com há quantos meses foi a
última venda (usando o histórico completo do banco quando disponível,
não só a janela de 12 meses analisada), e uma marcação "sem_giro"
(mesmo critério de antes: nenhuma venda no período + estoque > 0) que
colore a linha na aba "Todos os Produtos". O seletor de fornecedor e a
busca filtram apenas a visualização (cards, matriz e tabelas).
A curva ABC é classificada por VALOR (média mensal × preço de venda);
a curva XYZ e os cálculos de estoque seguem em quantidade (unidades).

Suporta as três empresas do grupo (cada uma com seu banco — veja
EMPRESAS abaixo) e agrupamento de produtos equivalentes: um arquivo
grupos_produtos.xlsx/.csv (colunas Codigo, Grupo) na pasta do script,
ou carregado pelo botão, permite analisar produtos com vários códigos
(marcas/fornecedores diferentes) como um item só.

Também traz:
  • Preço de custo líquido da última NF (com créditos por regime
    tributário) e preço de venda na aba "Todos os Produtos", com
    "Pedido R$" e "Capital Parado" (estoque parado × custo).
  • Coluna "Tendência" — média dos últimos 3 meses vs. 12, sinalizando
    produtos acelerando (▲) ou esfriando (▼).
  • Aba "Grupo (3 Empresas)" — soma o estoque das três lojas por
    descrição normalizada, útil pra checar antes de comprar se o
    produto já existe parado noutra loja.
  • Coluna "Últ. Entrada" — data da última NF de compra. É o que separa
    "produto novo que ainda não teve tempo de girar" (marcado em âmbar
    itálico) de "produto encalhado", ambos com venda zero.
  • Duplo clique num produto abre uma ficha com histórico mensal,
    indicadores e (se agrupado) os códigos que compõem o grupo.
  • Cache em disco por empresa (pasta _cache_estoque/): ao abrir o
    programa, dados de hoje já extraídos são carregados na hora, sem
    esperar o Firebird — "Conectar e Analisar" sempre busca dados
    novos e atualiza o cache.
  • Ao aplicar um filtro (fornecedor e/ou busca), as tabelas ganham
    uma linha de TOTAL ao final com a soma das colunas numéricas.

Dependências:
  pip install firebirdsql passlib pandas numpy openpyxl
"""

import os
import pickle
import re
import unicodedata
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime, date, timedelta
from typing import Optional
import threading

import firebirdsql
import pandas as pd
import numpy as np
import warnings

# Silencia aviso inofensivo do pandas sobre conexões não-SQLAlchemy
warnings.filterwarnings('ignore', message='pandas only supports SQLAlchemy')

# ═══════════════════════════════════════════════════════════════
#  CONFIGURAÇÃO DO BANCO  ← edite aqui
# ═══════════════════════════════════════════════════════════════
# Cada empresa do grupo tem seu próprio servidor/banco ATS
# (mesmas conexões dos scripts de extração em Extracoes/).
EMPRESAS = {
    "Dluz Elétrica": {
        "host": "192.168.0.41",
        "database": r"D:\DLUZR\BANCO\DLUZR25.FB",
        "regime": "simples",
    },
    "Dluz Iluminação": {
        "host": "192.168.0.250",
        "database": r"D:\ILUMIR\BANCO\ILUMIR.FB",
        "regime": "simples",
    },
    "Cor e Cia": {
        "host": "192.168.0.200",
        "database": r"D:\CorCiaR\BANCO\CORCIAR25.FB",
        "regime": "lucro_real",
    },
}
EMPRESA_PADRAO = "Dluz Elétrica"

# Alíquotas de crédito PIS/COFINS — regime não-cumulativo (Lucro Real),
# mesmas do extracao_ultima_nf_compra_v3
ALIQ_CRED_PIS    = 0.0165   # 1,65%
ALIQ_CRED_COFINS = 0.0760   # 7,60%

USER     = 'SYSDBA'
PASSWORD = 'masterkey'
PORT     = 3050
CHARSET  = 'WIN1252'

# Período da movimentação:
#   início = primeiro dia do mês, 12 meses atrás (garante meses inteiros)
#   fim    = hoje (o mês atual entra parcial, marcado com * na visualização)
DATA_INI = date(date.today().year - 1, date.today().month, 1).strftime('%Y-%m-%d')
DATA_FIM = date.today().strftime('%Y-%m-%d')
# ═══════════════════════════════════════════════════════════════

# Um produto sem venda que entrou há até N meses é "novo" (ainda não teve
# tempo de girar), não encalhe — ajuste conforme o giro típico do grupo
MESES_PRODUTO_NOVO = 3.0

# Abreviações de mês em português para a aba de média mensal
MESES_PT = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun',
            'jul', 'ago', 'set', 'out', 'nov', 'dez']

def _fmt_mes(periodo_str: str) -> str:
    """'2025-06' → 'jun/25'"""
    ano, mes = periodo_str.split('-')
    return f"{MESES_PT[int(mes) - 1]}/{ano[2:]}"


# ───────────────────────────────────────────────────────────────
#  CACHE EM DISCO
#  Evita reconsultar o Firebird toda vez que o programa abre — a
#  extração completa (estoque + vendas + última venda + última NF)
#  fica salva localmente e é reaproveitada enquanto for do mesmo dia.
#  O botão "Conectar e Analisar" sempre busca dados novos e reescreve
#  o cache; ele nunca fica "preso" numa versão velha sem o usuário pedir.
# ───────────────────────────────────────────────────────────────
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cache_estoque")
# Suba este número sempre que a extração passar a trazer colunas novas
# (ou mudar o critério das vendas) — caches gravados por versões antigas
# são ignorados, evitando que o programa abra com dados incompletos
# (ex.: sem "Ultima Entrada") ou contando pedidos não faturados como venda
CACHE_VERSAO = 3


def _slug(texto: str) -> str:
    """'Dluz Elétrica' → 'Dluz_Eletrica' (sem acento, sem espaço)."""
    nfkd = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^A-Za-z0-9]+", "_", sem_acento).strip("_")


def _cache_path(empresa: str) -> str:
    return os.path.join(CACHE_DIR, f"cache_{_slug(empresa)}.pkl")


def salvar_cache(empresa: str, df_estoque, df_vendas, df_ultima):
    """Grava a extração da empresa em disco, com timestamp."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        payload = {
            "versao": CACHE_VERSAO,
            "timestamp": datetime.now(),
            "df_estoque": df_estoque, "df_vendas": df_vendas, "df_ultima": df_ultima,
        }
        with open(_cache_path(empresa), "wb") as f:
            pickle.dump(payload, f)
    except Exception:
        pass  # cache é conveniência, não pode derrubar a extração


def carregar_cache(empresa: str):
    """Retorna (df_estoque, df_vendas, df_ultima, timestamp) do cache de
    HOJE, ou None se não houver cache, ele for de um dia anterior ou de
    uma versão antiga do programa (sem as colunas atuais)."""
    path = _cache_path(empresa)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            payload = pickle.load(f)
        if payload.get("versao") != CACHE_VERSAO:
            return None
        ts = payload["timestamp"]
        if ts.date() != date.today():
            return None
        return payload["df_estoque"], payload["df_vendas"], payload["df_ultima"], ts
    except Exception:
        return None


# ───────────────────────────────────────────────────────────────
#  QUERIES
# ───────────────────────────────────────────────────────────────
SQL_ESTOQUE = """
SELECT
    p.CODPROD                                              AS "Cod. do Produto",
    TRIM(p.DESCRICAO)                                      AS "Descricao",
    COALESCE(cp.ESTOQUE, 0)                                AS "estoque_atual",
    COALESCE(cp.PRECOCUSTO, 0)                            AS "Preco de Custo",
    COALESCE(p.PRECO, 0)                                   AS "Preco de Venda",
    TRIM(f.NOME)                                           AS "Fornecedor"
FROM PRODUTO p
LEFT JOIN (
    SELECT CODPROD,
           SUM(ESTOQUE)    AS ESTOQUE,
           MAX(PRECOCUSTO) AS PRECOCUSTO
    FROM COMPPROD
    GROUP BY CODPROD
) cp                 ON cp.CODPROD = p.CODPROD
LEFT JOIN FORNECE f  ON f.CODFORNEC = p.PRINCIPALFORNEC
WHERE p.ATIVO = 'S'
ORDER BY p.DESCRICAO
"""

SQL_MOVIMENTACAO = """
SELECT
    i.CODPROD                          AS "Cod. do Produto",
    TRIM(p.DESCRICAO)                  AS "Descrição",
    i.QUANTIDADE                       AS "Quantidade",
    CAST(c.DATAPEDIDO AS DATE)         AS "Data de Movimento"
FROM PEDIDOI i
JOIN PEDIDOC c      ON c.CODEMPRESA  = i.CODEMPRESA
                   AND c.TIPOPEDIDO  = i.TIPOPEDIDO
                   AND c.CODPEDIDO   = i.CODPEDIDO
JOIN PRODUTO p      ON p.CODPROD     = i.CODPROD
WHERE c.TIPOPEDIDO  = 55
  AND c.FATURADO    = 'S'
  AND i.CANCELADO   = 'N'
  AND c.DATAPEDIDO  BETWEEN ? AND ?
ORDER BY i.CODPROD, c.DATAPEDIDO
"""

# Última venda de cada produto em TODO o histórico (sem limite de data) —
# é o que permite calcular "meses sem movimento" com precisão mesmo para
# itens que só venderam há mais tempo que a janela de 12 meses analisada.
SQL_ULTIMA_VENDA = """
SELECT
    i.CODPROD               AS "Cod. do Produto",
    MAX(c.DATAPEDIDO)       AS "Ultima Venda"
FROM PEDIDOI i
JOIN PEDIDOC c      ON c.CODEMPRESA  = i.CODEMPRESA
                   AND c.TIPOPEDIDO  = i.TIPOPEDIDO
                   AND c.CODPEDIDO   = i.CODPEDIDO
WHERE c.TIPOPEDIDO  = 55
  AND c.FATURADO    = 'S'
  AND i.CANCELADO   = 'N'
GROUP BY i.CODPROD
"""

# Última NF de compra por produto (versão enxuta da consulta do
# extracao_ultima_nf_compra_v3) — traz o necessário para calcular o
# custo unitário líquido: preço de entrada, frete, IPI, ICMS e ICMS-ST.
SQL_ULTIMA_NF_CUSTO = """
WITH ULT_DATA AS (
    SELECT I.CODPROD, MAX(C.DT_ENTRADA) AS DT_MAX
    FROM NFENTRI I
    JOIN NFENTRC C
        ON C.CODEMPRESA = I.CODEMPRESA
       AND C.NUMERONF   = I.NUMERONF
       AND C.CODFORNEC  = I.CODFORNEC
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
)
SELECT
    I.CODPROD           AS "Cod. do Produto",
    C.DT_ENTRADA        AS "Ultima Entrada",
    I.QUANTIDADE        AS "Quantidade",
    I.PRECONF           AS "Preco_Entrada",
    I.FRETE             AS "Frete_Item",
    I.VLRICMS           AS "Valor_ICMS",
    I.VALORSUBSTRIB     AS "Valor_ICMS_ST",
    I.VLRIPI            AS "Valor_IPI"
FROM ULT_NF U
JOIN NFENTRI I
    ON I.CODPROD  = U.CODPROD
   AND I.NUMERONF = U.NUMERONF_MAX
JOIN NFENTRC C
    ON C.CODEMPRESA = I.CODEMPRESA
   AND C.NUMERONF   = I.NUMERONF
   AND C.CODFORNEC  = I.CODFORNEC
   AND C.DT_ENTRADA = U.DT_MAX
"""


# ───────────────────────────────────────────────────────────────
#  EXTRAÇÃO
# ───────────────────────────────────────────────────────────────
def calcular_custo_liquido(df_nf: pd.DataFrame, regime: str = "simples") -> pd.DataFrame:
    """Custo unitário líquido e data da última NF de compra, por produto.

    Mesma lógica do extracao_ultima_nf_compra_v3:
      custo bruto  = total do item + IPI + ICMS-ST + frete do item
      lucro real   = desconta créditos de ICMS (só sem ST na operação)
                     e PIS/COFINS não-cumulativo sobre a base sem ICMS
      simples      = nenhum crédito na entrada
      custo unit.  = (custo bruto − créditos) / quantidade

    Retorna [Cod. do Produto, Custo Liquido, Ultima Entrada]. Itens cuja
    NF tem quantidade zerada ficam sem custo (NaN) mas MANTÊM a data —
    saber quando o produto entrou é útil mesmo sem custo confiável.
    """
    COLS = ["Cod. do Produto", "Custo Liquido", "Ultima Entrada"]
    if df_nf is None or df_nf.empty:
        return pd.DataFrame(columns=COLS)
    df = df_nf.copy()
    for c in ("Quantidade", "Preco_Entrada", "Frete_Item",
              "Valor_ICMS", "Valor_ICMS_ST", "Valor_IPI"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    tem_data = "Ultima Entrada" in df.columns
    if tem_data:
        df["Ultima Entrada"] = pd.to_datetime(df["Ultima Entrada"], errors="coerce")

    # A mesma NF pode ter o produto em mais de uma linha (grades) —
    # totaliza valores por linha antes de somar por produto
    df["_total_item"] = df["Preco_Entrada"] * df["Quantidade"]
    campos = ["Quantidade", "_total_item", "Frete_Item",
              "Valor_ICMS", "Valor_ICMS_ST", "Valor_IPI"]
    agg = df.groupby("Cod. do Produto", as_index=False)[campos].sum()
    if tem_data:
        datas = df.groupby("Cod. do Produto", as_index=False)["Ultima Entrada"].max()
        agg = agg.merge(datas, on="Cod. do Produto", how="left")
    else:
        agg["Ultima Entrada"] = pd.NaT

    qtd = agg["Quantidade"].where(agg["Quantidade"] != 0)
    custo_bruto = (agg["_total_item"] + agg["Valor_IPI"]
                   + agg["Valor_ICMS_ST"] + agg["Frete_Item"])

    if regime == "lucro_real":
        # ICMS próprio só gera crédito quando NÃO há ST na operação;
        # PIS/COFINS sobre a base de aquisição excluindo o ICMS destacado
        tem_st = agg["Valor_ICMS_ST"] > 0
        vlr_icms = agg["Valor_ICMS"].where(~tem_st, 0.0)
        base_pis_cofins = (agg["_total_item"] + agg["Frete_Item"]
                           - vlr_icms).clip(lower=0)
        creditos = vlr_icms + base_pis_cofins * (ALIQ_CRED_PIS + ALIQ_CRED_COFINS)
    else:
        creditos = 0.0

    return pd.DataFrame({
        "Cod. do Produto": agg["Cod. do Produto"],
        "Custo Liquido": ((custo_bruto - creditos) / qtd).round(4),
        "Ultima Entrada": agg["Ultima Entrada"],
    })


def extrair_dados(empresa: str):
    """Conecta no Firebird da empresa e retorna
    (df_estoque, df_vendas, df_ultima_venda).

    df_estoque sai com as colunas "Custo Liquido" — o custo unitário da
    última NF de compra (com impostos/créditos conforme o regime da
    empresa) — e "Ultima Entrada", a data dessa NF. Para produtos sem NF
    de entrada, ou se a consulta de NF falhar, o custo cai para o
    "Preco de Custo" do ERP (COMPPROD) e a data fica vazia.

    df_ultima_venda traz a última data de venda de cada produto em todo
    o histórico do banco (sem o filtro de 12 meses). Se essa consulta
    falhar por qualquer motivo, degrada para um DataFrame vazio — o
    cálculo de "meses sem movimento" cai para a última venda dentro da
    própria janela analisada nesse caso.
    """
    cfg = EMPRESAS[empresa]
    con = firebirdsql.connect(
        host=cfg["host"], database=cfg["database"],
        port=cfg.get("port", PORT),
        user=USER, password=PASSWORD, charset=CHARSET,
    )
    try:
        df_estoque = pd.read_sql(SQL_ESTOQUE, con)
        df_vendas  = pd.read_sql(SQL_MOVIMENTACAO, con, params=(DATA_INI, DATA_FIM))
        try:
            df_ultima = pd.read_sql(SQL_ULTIMA_VENDA, con)
        except Exception:
            df_ultima = pd.DataFrame(columns=["Cod. do Produto", "Ultima Venda"])
        try:
            df_nf = pd.read_sql(SQL_ULTIMA_NF_CUSTO, con)
        except Exception:
            df_nf = pd.DataFrame()
    finally:
        con.close()

    # Custo líquido + data da última NF; custo cai pro preço do ERP
    df_custo = calcular_custo_liquido(df_nf, cfg.get("regime", "simples"))
    df_estoque = df_estoque.merge(df_custo, on="Cod. do Produto", how="left")
    for col in ("Custo Liquido", "Ultima Entrada"):
        if col not in df_estoque.columns:
            df_estoque[col] = np.nan
    df_estoque["Custo Liquido"] = df_estoque["Custo Liquido"].fillna(
        pd.to_numeric(df_estoque.get("Preco de Custo"), errors="coerce"))
    df_estoque["Ultima Entrada"] = pd.to_datetime(
        df_estoque["Ultima Entrada"], errors="coerce")

    return df_estoque, df_vendas, df_ultima


# ───────────────────────────────────────────────────────────────
#  AGRUPAMENTO DE PRODUTOS EQUIVALENTES
#  (mesmo produto com códigos diferentes por marca/fornecedor)
# ───────────────────────────────────────────────────────────────
def _norm_cod(s: pd.Series) -> pd.Series:
    """Normaliza códigos para texto ('123.0' → '123')."""
    return s.astype(str).str.strip().str.replace(r"\.0+$", "", regex=True)


def carregar_grupos(path: str) -> dict:
    """Lê o arquivo de equivalências e retorna {código: nome_do_grupo}.

    Aceita .xlsx/.xls/.csv com uma coluna de Código e uma de Grupo
    (os nomes podem variar; basta conterem 'cod' e 'grup').
    Ex.:  Codigo | Grupo
          10231  | PAINEL LED 24W REDONDO
          10457  | PAINEL LED 24W REDONDO
    """
    if path.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path, sep=None, engine="python")
    achar = lambda parte: next(
        (c for c in df.columns if parte in str(c).strip().lower()), None)
    col_cod, col_grp = achar("cod"), achar("grup")
    if col_cod is None or col_grp is None:
        raise ValueError(
            "O arquivo de grupos precisa de uma coluna de Código e uma de "
            "Grupo (ex.: 'Codigo' e 'Grupo')."
        )
    df = df.dropna(subset=[col_cod, col_grp])
    return dict(zip(_norm_cod(df[col_cod]),
                    df[col_grp].astype(str).str.strip()))


def aplicar_grupos(df_estoque: pd.DataFrame,
                   df_vendas: pd.DataFrame,
                   mapa: dict,
                   df_ultima: Optional[pd.DataFrame] = None):
    """Funde os códigos de cada grupo num único produto-grupo.

    Vendas são somadas mês a mês, estoque é somado e o preço de venda do
    grupo é a média dos preços ponderada pela quantidade vendida no período
    (produtos que não venderam não puxam o preço). A última venda do grupo
    (para "meses sem movimento") é a mais recente entre os membros.
    Retorna (df_e, df_v, df_ultima).
    """
    if not mapa:
        return df_estoque, df_vendas, df_ultima
    ids = {g: f"G{i+1}" for i, g in enumerate(sorted(set(mapa.values())))}

    df_e = df_estoque.copy()
    df_v = df_vendas.copy()
    df_e["_cod"] = _norm_cod(df_e["Cod. do Produto"])
    df_v["_cod"] = _norm_cod(df_v["Cod. do Produto"])
    df_e["_g"] = df_e["_cod"].map(mapa)
    df_v["_g"] = df_v["_cod"].map(mapa)

    # Peso do preço: total vendido por código no período
    vend_tot = df_v.groupby("_cod")["Quantidade"].sum()

    # ── Estoque: agrega os códigos de cada grupo numa linha só
    dentro = df_e["_g"].notna()
    grup = df_e[dentro].copy()
    if not grup.empty:
        desc_col = "Descricao" if "Descricao" in df_e.columns else "Descrição"
        grup["_peso"]  = grup["_cod"].map(vend_tot).fillna(0.0)
        grup["_preco"] = pd.to_numeric(
            grup.get("Preco de Venda", 0), errors="coerce").fillna(0.0)
        grup["_custo"] = pd.to_numeric(
            grup.get("Custo Liquido", np.nan), errors="coerce")
        grup["_entrada"] = pd.to_datetime(
            grup.get("Ultima Entrada", pd.NaT), errors="coerce")
        grup["_est"]   = pd.to_numeric(
            grup["estoque_atual"], errors="coerce").fillna(0.0)
        linhas = []
        for g, sub in grup.groupby("_g"):
            w = sub["_peso"].sum()
            preco = ((sub["_preco"] * sub["_peso"]).sum() / w
                     if w > 0 else sub["_preco"].mean())
            # custo do grupo: média ponderada pelas vendas (ignora membros
            # sem custo conhecido); sem vendas, média simples dos custos
            com_custo = sub.dropna(subset=["_custo"])
            wc = com_custo["_peso"].sum()
            if com_custo.empty:
                custo = np.nan
            elif wc > 0:
                custo = (com_custo["_custo"] * com_custo["_peso"]).sum() / wc
            else:
                custo = com_custo["_custo"].mean()
            forns = (sub["Fornecedor"].dropna().unique().tolist()
                     if "Fornecedor" in sub.columns else [])
            linhas.append({
                "Cod. do Produto": ids[g],
                desc_col: f"{g}  [grupo]",
                "estoque_atual": sub["_est"].sum(),
                "Preco de Venda": round(preco, 2),
                "Custo Liquido": (round(custo, 4) if pd.notna(custo) else np.nan),
                # entrada do grupo = compra mais recente entre as marcas
                "Ultima Entrada": sub["_entrada"].max(),
                "Fornecedor": (forns[0] if len(forns) == 1
                               else f"({len(forns)} fornecedores)"),
            })
        df_e = pd.concat(
            [df_e[~dentro].drop(columns=["_cod", "_g"]), pd.DataFrame(linhas)],
            ignore_index=True,
        )
    else:
        df_e = df_e.drop(columns=["_cod", "_g"])

    # ── Vendas: troca código/descrição dos membros pelo produto-grupo
    m = df_v["_g"].notna()
    # coluna pode vir como int do banco; precisa aceitar códigos "G1", "G2"…
    df_v["Cod. do Produto"] = df_v["Cod. do Produto"].astype(object)
    df_v.loc[m, "Cod. do Produto"] = df_v.loc[m, "_g"].map(ids)
    df_v.loc[m, "Descrição"] = df_v.loc[m, "_g"] + "  [grupo]"
    df_v = df_v.drop(columns=["_cod", "_g"])

    # ── Última venda: para o grupo, usa a mais recente entre os membros
    if df_ultima is not None and not df_ultima.empty:
        df_u = df_ultima.copy()
        df_u["_cod"] = _norm_cod(df_u["Cod. do Produto"])
        df_u["_g"] = df_u["_cod"].map(mapa)
        dentro_u = df_u["_g"].notna()
        df_u["Cod. do Produto"] = df_u["Cod. do Produto"].astype(object)
        if dentro_u.any():
            grupo_u = (df_u[dentro_u].groupby("_g")["Ultima Venda"]
                       .max().reset_index()
                       .rename(columns={"_g": "Cod. do Produto"}))
            grupo_u["Cod. do Produto"] = grupo_u["Cod. do Produto"].map(ids)
            df_u = pd.concat(
                [df_u[~dentro_u].drop(columns=["_cod", "_g"]), grupo_u],
                ignore_index=True,
            )
        else:
            df_u = df_u.drop(columns=["_cod", "_g"])
        df_ultima = df_u

    return df_e, df_v, df_ultima


# ───────────────────────────────────────────────────────────────
#  CONSOLIDAÇÃO DO GRUPO (3 EMPRESAS)
# ───────────────────────────────────────────────────────────────
def consolidar_grupo(raw: dict) -> pd.DataFrame:
    """Soma o estoque de cada produto nas empresas já extraídas em
    `raw` ({empresa: (df_estoque, df_vendas, df_ultima)}).

    Cada banco do ATS tem seu próprio catálogo de CODPROD — não há
    código compartilhado entre as três empresas — então o casamento é
    feito pela DESCRIÇÃO normalizada (maiúsculas, espaços colapsados).
    Isso significa que descrições escritas de forma diferente entre
    lojas (abreviações, ordem das palavras) não vão casar; é uma
    aproximação útil, não uma correspondência garantida.

    Retorna uma linha por descrição, com uma coluna de estoque por
    empresa (0 onde não há dado) e uma coluna "Total", ordenada pelo
    total decrescente.
    """
    partes = []
    for empresa, dados in raw.items():
        if not dados:
            continue
        df_estoque = dados[0]
        if df_estoque is None or df_estoque.empty:
            continue
        desc_col = "Descricao" if "Descricao" in df_estoque.columns else "Descrição"
        d = pd.DataFrame({
            "_desc": df_estoque[desc_col].astype(str).str.strip(),
            "estoque": pd.to_numeric(
                df_estoque["estoque_atual"], errors="coerce").fillna(0),
        })
        d["_chave"] = d["_desc"].str.upper().str.replace(r"\s+", " ", regex=True)
        agg = d.groupby("_chave", as_index=False).agg(
            Descricao=("_desc", "first"), estoque=("estoque", "sum"))
        agg["Empresa"] = empresa
        partes.append(agg)

    colunas_vazio = ["Descrição", *EMPRESAS.keys(), "Total"]
    if not partes:
        return pd.DataFrame(columns=colunas_vazio)

    todas = pd.concat(partes, ignore_index=True)
    pivot = todas.pivot_table(index="_chave", columns="Empresa", values="estoque",
                              aggfunc="sum", fill_value=0.0)
    desc_por_chave = todas.drop_duplicates("_chave").set_index("_chave")["Descricao"]
    pivot.insert(0, "Descrição", pivot.index.map(desc_por_chave))
    for empresa in EMPRESAS:
        if empresa not in pivot.columns:
            pivot[empresa] = 0.0
    pivot = pivot[["Descrição", *EMPRESAS.keys()]].reset_index(drop=True)
    pivot["Total"] = pivot[list(EMPRESAS.keys())].sum(axis=1)
    return pivot.sort_values("Total", ascending=False).reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════
#  ANÁLISE ABC × XYZ
# ═══════════════════════════════════════════════════════════════
Z_FATOR = {
    "AX": 1.65, "AY": 1.96, "AZ": 2.33,
    "BX": 1.28, "BY": 1.65, "BZ": 1.96,
    "CX": 1.04, "CY": 1.28, "CZ": 1.28,
}


def _meses_sem_movimento(cods: pd.Series,
                         df_vendas: pd.DataFrame,
                         df_ultima: Optional[pd.DataFrame]) -> pd.Series:
    """Meses desde a última venda de cada código.

    Usa o histórico completo do banco (df_ultima, sem limite de data)
    quando disponível; para os códigos sem registro ali (consulta
    indisponível, ou item novo), cai para a última venda dentro da
    própria janela analisada — melhor que nada, mas com o mesmo limite
    de precisão que a versão anterior deste script tinha para todos.
    """
    hoje = pd.Timestamp(date.today())
    ultima = pd.Series(pd.NaT, index=cods.index, dtype="datetime64[ns]")

    if df_ultima is not None and not df_ultima.empty:
        mapa_hist = df_ultima.drop_duplicates("Cod. do Produto") \
                             .set_index("Cod. do Produto")["Ultima Venda"]
        mapa_hist = pd.to_datetime(mapa_hist, errors="coerce")
        ultima = cods.map(mapa_hist)

    faltando = ultima.isna()
    if faltando.any() and not df_vendas.empty:
        mapa_janela = df_vendas.groupby("Cod. do Produto")["Data de Movimento"].max()
        ultima_fallback = cods.map(mapa_janela)
        ultima = ultima.where(~faltando, ultima_fallback)

    dias = (hoje - ultima).dt.days
    return (dias / 30.44).round(1)


def calcular_abc_xyz(df_vendas: pd.DataFrame,
                     df_estoque: pd.DataFrame,
                     lead_time: float,
                     df_ultima: Optional[pd.DataFrame] = None):
    """Retorna (analise, meses_info). Calcula sobre TODOS os produtos
    ativos de df_estoque — inclusive os que não venderam nada no
    período, que entram com média zero e caem naturalmente na Curva C.
    """

    # ── Pivot mensal de vendas ───────────────────────────────────
    df_vendas = df_vendas.copy()
    df_vendas["Data de Movimento"] = pd.to_datetime(
        df_vendas["Data de Movimento"], errors="coerce"
    )
    df_vendas = df_vendas.dropna(subset=["Data de Movimento"])
    df_vendas["Mes_Ano"] = df_vendas["Data de Movimento"].dt.to_period("M")

    if df_vendas.empty:
        pivot = pd.DataFrame(columns=["Cod. do Produto"])
        meses_cols = []
    else:
        pivot = pd.pivot_table(
            df_vendas, values="Quantidade",
            index="Cod. do Produto",
            columns="Mes_Ano", aggfunc="sum", fill_value=0,
        )
        # Garante que meses sem nenhuma venda na base também contem como zero
        todos_meses = pd.period_range(
            df_vendas["Mes_Ano"].min(), df_vendas["Mes_Ano"].max(), freq="M"
        )
        pivot = pivot.reindex(columns=todos_meses, fill_value=0).reset_index()
        meses_cols = list(pivot.columns[1:])

    # Separa o mês atual (parcial) dos meses completos para as estatísticas
    mes_atual = pd.Period(date.today(), freq="M")
    meses_stat = [m for m in meses_cols if m != mes_atual]
    if not meses_stat:                      # proteção: se só houver o mês atual
        meses_stat = meses_cols
    n_meses = max(len(meses_stat), 1)

    if meses_stat:
        pivot["media"]         = pivot[meses_stat].mean(axis=1)
        pivot["desvio_padrao"] = pivot[meses_stat].std(axis=1).fillna(0)
    else:
        pivot["media"] = 0.0
        pivot["desvio_padrao"] = 0.0

    # ── Base: TODOS os produtos ativos, com preço, estoque e fornecedor ──
    desc_col = "Descricao" if "Descricao" in df_estoque.columns else "Descrição"
    base = df_estoque.rename(columns={desc_col: "Descrição"}).copy()
    base["estoque_atual"] = pd.to_numeric(
        base["estoque_atual"], errors="coerce").fillna(0)
    base["Preco de Venda"] = pd.to_numeric(
        base.get("Preco de Venda", 0), errors="coerce").fillna(0)
    cols_base = ["Cod. do Produto", "Descrição", "estoque_atual", "Preco de Venda"]
    if "Custo Liquido" in base.columns:
        base["Custo Liquido"] = pd.to_numeric(
            base["Custo Liquido"], errors="coerce")
        cols_base.append("Custo Liquido")
    if "Ultima Entrada" in base.columns:
        base["Ultima Entrada"] = pd.to_datetime(
            base["Ultima Entrada"], errors="coerce")
        cols_base.append("Ultima Entrada")
    if "Fornecedor" in base.columns:
        cols_base.append("Fornecedor")
    base = base[cols_base].drop_duplicates("Cod. do Produto")

    # ── Junta as vendas: quem não vendeu no período entra com média zero ──
    analise = pd.merge(base, pivot, on="Cod. do Produto", how="left")
    for col in (*meses_cols, "media", "desvio_padrao"):
        if col in analise.columns:
            analise[col] = analise[col].fillna(0.0)
    if "Fornecedor" in analise.columns:
        analise["Fornecedor"] = analise["Fornecedor"].fillna("(sem fornecedor)")

    # ── Curva ABC (por VALOR = média mensal × preço de venda) ──
    analise["valor_mensal"] = (analise["media"] * analise["Preco de Venda"]).round(2)
    analise = analise.sort_values("valor_mensal", ascending=False).reset_index(drop=True)
    total = analise["valor_mensal"].sum()
    if total > 0:
        analise["%_acum"] = (analise["valor_mensal"] / total).cumsum()

        def _abc(p):
            if   p <= 0.80: return "A"
            elif p <= 0.95: return "B"
            return "C"
        analise["curva_ABC"] = analise["%_acum"].apply(_abc)
    else:
        analise["curva_ABC"] = "C"

    # ── Curva XYZ (Coeficiente de Variação) ────────────────────
    analise["CV"] = np.where(
        analise["media"] > 0,
        (analise["desvio_padrao"] / analise["media"]).round(3),
        0.0,
    )
    analise["curva_XYZ"] = pd.cut(
        analise["CV"], bins=[-np.inf, 0.5, 1.0, np.inf], labels=["X", "Y", "Z"],
    ).astype(str).replace("nan", "X")

    analise["classe"] = analise["curva_ABC"] + analise["curva_XYZ"]
    analise["z"]      = analise["classe"].map(Z_FATOR).fillna(1.28)

    # ── Demanda intermitente ───────────────────────────────────
    if meses_stat:
        pct_zeros = (analise[meses_stat] == 0).sum(axis=1) / n_meses
    else:
        pct_zeros = 1.0
    analise["intermitente"] = pct_zeros > 0.40

    # ── Tendência (sazonalidade) ────────────────────────────────
    # Compara a média dos últimos 3 meses completos com a média dos 12 —
    # sinaliza produtos acelerando ou esfriando sem exigir um modelo
    # sazonal completo. Sem meses suficientes ou sem venda, fica sem dado.
    janela_recente = meses_stat[-min(3, len(meses_stat)):] if meses_stat else []
    if janela_recente and len(meses_stat) >= 2:
        media_recente = analise[janela_recente].mean(axis=1)
    else:
        media_recente = pd.Series(np.nan, index=analise.index)
    analise["media_recente"] = media_recente.round(2)
    analise["tendencia_pct"] = np.where(
        analise["media"] > 0,
        ((media_recente - analise["media"]) / analise["media"] * 100).round(1),
        np.nan,
    )

    # ── Cobertura ──────────────────────────────────────────────
    analise["cobertura_meses"] = np.where(
        analise["media"] > 0,
        (analise["estoque_atual"] / analise["media"]).round(1),
        np.nan,
    )

    # ── Estoque de segurança e ponto de pedido ─────────────────
    analise["estoq_seguranca"] = (
        analise["z"] * np.sqrt(lead_time) * analise["desvio_padrao"]
    )
    analise["estoq_minimo"] = analise["media"] * lead_time + analise["estoq_seguranca"]
    analise["qnt_em_falta"] = analise["estoq_minimo"] - analise["estoque_atual"]
    analise["pedido"] = analise["qnt_em_falta"].apply(
        lambda x: int(np.ceil(x)) if x > 0 else 0
    )

    # ── Valor do pedido e capital parado em estoque (R$) ────────
    # Usa o custo líquido da última NF (com fallback pro custo do ERP,
    # já resolvido em extrair_dados); sem nenhum custo conhecido, fica NaN.
    if "Custo Liquido" not in analise.columns:
        analise["Custo Liquido"] = np.nan
    analise["pedido_valor"] = (analise["pedido"] * analise["Custo Liquido"]).round(2)
    analise["capital_parado"] = (
        analise["estoque_atual"] * analise["Custo Liquido"]
    ).round(2)

    # ── Meses sem movimento (última venda) ──────────────────────
    analise["meses_sem_movimento"] = _meses_sem_movimento(
        analise["Cod. do Produto"], df_vendas, df_ultima)

    # "Sem giro" = não vendeu NADA no período analisado E tem estoque
    # (mesmo critério da versão anterior — agora é uma coluna/marcação
    # em vez de uma aba separada, calculada para todos os produtos)
    sem_venda_periodo = analise["media"] == 0
    if meses_stat:
        sem_venda_periodo &= (analise[meses_stat] == 0).all(axis=1)
    analise["sem_giro"] = sem_venda_periodo & (analise["estoque_atual"] > 0)

    # ── Última entrada por NF e há quantos meses ela ocorreu ────
    # Sem isso, "nunca vendeu" é ambíguo: pode ser produto NOVO que ainda
    # não teve tempo de girar, ou produto velho encalhado. A data da
    # compra separa os dois casos.
    if "Ultima Entrada" not in analise.columns:
        analise["Ultima Entrada"] = pd.NaT
    analise["Ultima Entrada"] = pd.to_datetime(
        analise["Ultima Entrada"], errors="coerce")
    hoje_ts = pd.Timestamp(date.today())
    analise["meses_desde_entrada"] = (
        (hoje_ts - analise["Ultima Entrada"]).dt.days / 30.44
    ).round(1)
    # Produto novo: entrou há pouco tempo e ainda não vendeu — não é
    # encalhe, é falta de tempo de prateleira
    analise["produto_novo"] = (
        sem_venda_periodo
        & analise["meses_desde_entrada"].notna()
        & (analise["meses_desde_entrada"] <= MESES_PRODUTO_NOVO)
    )

    # ── Limpeza ────────────────────────────────────────────────
    for col in ("media", "desvio_padrao", "CV",
                "estoq_seguranca", "estoq_minimo", "qnt_em_falta"):
        analise[col] = analise[col].round(2)
    analise.columns = [str(c) for c in analise.columns]
    analise = analise.drop(columns=["%_acum"], errors="ignore")

    # Info dos meses para a aba de média mensal (em ordem cronológica)
    mes_atual_str = str(mes_atual)
    meses_info = [
        (str(m), _fmt_mes(str(m)) + ("*" if str(m) == mes_atual_str else ""),
         str(m) == mes_atual_str)
        for m in meses_cols
    ]
    return analise, meses_info


# ═══════════════════════════════════════════════════════════════
#  INTERFACE GRÁFICA
# ═══════════════════════════════════════════════════════════════
class AppIntegrado:

    # Paleta — Grupo Dluz Design System (creme quente + grafite + âmbar)
    COR_FUNDO  = "#F6F0E6"; COR_SUPERFICIE = "#FFFDF8"; COR_BORDA = "#E6DBC8"
    COR_A = "#F1E4C5"; COR_B = "#F8F0DD"; COR_C = "#FFFDF8"; COR_SG = "#EFE7D8"
    COR_PEDIDO = "#C0392B"; COR_OK = "#3A6E47"; COR_AVISO = "#A2761F"
    COR_ACENTO = "#BE8E2E"; COR_ACENTO_ESC = "#876214"
    COR_TEXTO = "#1E1C19"; COR_SUBTEXTO = "#6F665B"
    COR_CABECALHO = "#2B2926"; COR_CABEC_TX = "#EFE7D8"

    COLUNAS = ("Código", "Descrição", "Classe", "Fornecedor",
               "Média/Mês", "Valor/Mês", "Cobertura", "Sem Giro (m)",
               "Est. Atual", "Est. Mínimo", "Pedido", "Demanda")
    # Aba "Pedidos de Compra" ganha o valor do pedido em R$ (unid × custo)
    COLUNAS_PEDIDOS = ("Código", "Descrição", "Classe", "Fornecedor",
                       "Média/Mês", "Valor/Mês", "Cobertura", "Sem Giro (m)",
                       "Est. Atual", "Est. Mínimo", "Pedido", "Pedido R$", "Demanda")
    # Aba "Todos os Produtos" ganha preço de custo (última NF, líquido),
    # preço de venda cadastrado, capital parado em estoque e tendência
    COLUNAS_TODOS = ("Código", "Descrição", "Classe", "Fornecedor",
                     "Média/Mês", "Valor/Mês", "P. Custo", "P. Venda",
                     "Tendência", "Cobertura", "Sem Giro (m)", "Últ. Entrada",
                     "Capital Parado",
                     "Est. Atual", "Est. Mínimo", "Pedido", "Pedido R$", "Demanda")
    COL_WIDTHS = {
        "Código": 60, "Descrição": 190, "Classe": 50, "Fornecedor": 140,
        "Média/Mês": 72, "Valor/Mês": 90, "P. Custo": 78, "P. Venda": 78,
        "Tendência": 82, "Cobertura": 70, "Sem Giro (m)": 88,
        "Últ. Entrada": 92, "Capital Parado": 98, "Est. Atual": 72,
        "Est. Mínimo": 80, "Pedido": 58, "Pedido R$": 88, "Demanda": 82,
    }
    # Colunas somáveis na linha de subtotal (rótulo → coluna no df)
    SUBTOTAL_COLS = {
        "Média/Mês": "media", "Valor/Mês": "valor_mensal",
        "Capital Parado": "capital_parado", "Est. Atual": "estoque_atual",
        "Est. Mínimo": "estoq_minimo", "Pedido": "pedido",
        "Pedido R$": "pedido_valor",
    }
    # Colunas numéricas que podem ter os zeros ocultados (rótulo → coluna no df)
    FILTRO_ZERO_COLS = {
        "Média/Mês": "media", "Valor/Mês": "valor_mensal",
        "P. Custo": "Custo Liquido", "P. Venda": "Preco de Venda",
        "Cobertura": "cobertura_meses", "Sem Giro (m)": "meses_sem_movimento",
        "Capital Parado": "capital_parado", "Est. Atual": "estoque_atual",
        "Est. Mínimo": "estoq_minimo", "Pedido": "pedido",
        "Pedido R$": "pedido_valor",
    }
    # Nessas colunas, célula vazia (NaN) significa "∞" — não é zero, então
    # não deve ser removida quando o usuário pede pra ocultar os zeros
    CAMPOS_INFINITO = {"cobertura_meses", "meses_sem_movimento"}

    # Lead times pré-calculados (em meses)
    LEAD_TIMES = [0.5, 1.0, 1.5, 2.0, 2.5]

    def __init__(self, root):
        self.root = root
        self.root.title("ATS — Análise de Estoque (ABC × XYZ)")
        self.root.geometry("1120x820")
        self.root.minsize(940, 640)

        self.lead_time = tk.DoubleVar(value=2.0)
        self.empresa   = tk.StringVar(value=EMPRESA_PADRAO)
        self.agrupar   = tk.BooleanVar(value=False)
        self.mapa_grupos = {}                # {código: nome_do_grupo}
        self.lbl_grupos_txt = tk.StringVar(value="nenhum arquivo de grupos carregado")
        self.raw   = {}                      # {empresa: (df_estoque, df_vendas, df_ultima)}
        self.cache_timestamps = {}           # {empresa: datetime da última extração/cache}
        self.cache = {}                      # {(empresa, lt, agrupar): (analise, meses)}
        self.meses_info = []                 # [(col, label, is_parcial), ...]
        self.tree_media = None
        self.fornecedores_todos: list = []          # lista atual de fornecedores
        self.fornecedores_selecionados: set = set()  # vazio = sem filtro (todos)
        self.win_fornecedor = None                   # popup de seleção, se aberto
        self.lst_fornecedor = None
        self.filtro_texto = tk.StringVar()
        self.ocultar_zeros: set = set()              # colunas (campo no df) com zero oculto
        self.win_filtro_col = None                   # popup de filtro de colunas, se aberto
        self.vars_ocultar_zero = {}                  # {campo: tk.BooleanVar}
        self.df_resultado: Optional[pd.DataFrame] = None

        self._escolher_fontes()
        self._aplicar_estilo()
        self._construir_interface()
        self._carregar_grupos_padrao()
        self._carregar_cache_inicial()
        self._centralizar()

    # ── Estilo ──────────────────────────────────────────────────
    def _escolher_fontes(self):
        """Fontes da identidade Dluz, com fallback para as instaladas no sistema."""
        import tkinter.font as tkfont
        fams = set(tkfont.families())
        def pick(prefs, padrao):
            return next((f for f in prefs if f in fams), padrao)
        self.F_SERIF = pick(("Newsreader", "Georgia"), "Times New Roman")
        self.F_SANS  = pick(("IBM Plex Sans", "Helvetica Neue", "Helvetica"), "Arial")
        self.F_MONO  = pick(("IBM Plex Mono", "Menlo", "Consolas"), "Courier New")

    def _aplicar_estilo(self):
        st = ttk.Style(self.root)
        try: st.theme_use("clam")
        except Exception: pass
        fundo = self.COR_FUNDO

        st.configure("TFrame", background=fundo)
        st.configure("TLabel", background=fundo, foreground=self.COR_TEXTO,
                     font=(self.F_SANS, 10))
        st.configure("TLabelframe", background=fundo,
                     bordercolor=self.COR_BORDA, relief="solid")
        st.configure("TLabelframe.Label", background=fundo,
                     font=(self.F_SANS, 9, "bold"), foreground=self.COR_SUBTEXTO)
        st.configure("TSeparator", background=self.COR_BORDA)
        st.configure("TRadiobutton", background=fundo, foreground=self.COR_TEXTO,
                     font=(self.F_SANS, 10))
        st.map("TRadiobutton", background=[("active", fundo)])
        st.configure("TCheckbutton", background=fundo, foreground=self.COR_TEXTO,
                     font=(self.F_SANS, 10))
        st.map("TCheckbutton", background=[("active", fundo)])

        st.configure("TButton", font=(self.F_SANS, 10),
                     background=self.COR_SUPERFICIE, foreground=self.COR_TEXTO,
                     bordercolor=self.COR_BORDA, relief="solid", padding=[10, 5])
        st.map("TButton",
               background=[("active", self.COR_B)],
               foreground=[("disabled", self.COR_SUBTEXTO)])
        st.configure("Accent.TButton",
                     background=self.COR_ACENTO, foreground=self.COR_SUPERFICIE,
                     bordercolor=self.COR_ACENTO_ESC,
                     font=(self.F_SANS, 10, "bold"))
        st.map("Accent.TButton",
               background=[("disabled", self.COR_BORDA),
                           ("active", self.COR_ACENTO_ESC)],
               foreground=[("disabled", self.COR_SUBTEXTO)])

        # insertcolor: sem isso o caret herda o systemTextColor do SO —
        # que no modo escuro do macOS é BRANCO e some sobre o campo creme
        st.configure("TEntry", fieldbackground=self.COR_SUPERFICIE,
                     bordercolor=self.COR_BORDA, foreground=self.COR_TEXTO,
                     insertcolor=self.COR_TEXTO)
        st.configure("TCombobox", fieldbackground=self.COR_SUPERFICIE,
                     background=self.COR_SUPERFICIE, bordercolor=self.COR_BORDA,
                     foreground=self.COR_TEXTO, arrowcolor=self.COR_TEXTO)
        st.map("TCombobox",
               fieldbackground=[("readonly", self.COR_SUPERFICIE)],
               foreground=[("disabled", self.COR_SUBTEXTO)])

        st.configure("TNotebook", background=fundo, bordercolor=self.COR_BORDA)
        st.configure("TNotebook.Tab", padding=[12, 5],
                     background=self.COR_SG, foreground=self.COR_SUBTEXTO,
                     font=(self.F_SANS, 9, "bold"))
        st.map("TNotebook.Tab",
               background=[("selected", self.COR_SUPERFICIE)],
               foreground=[("selected", self.COR_TEXTO)])

        st.configure("Treeview", rowheight=25, font=(self.F_SANS, 9),
                     background=self.COR_SUPERFICIE,
                     fieldbackground=self.COR_SUPERFICIE,
                     foreground=self.COR_TEXTO, bordercolor=self.COR_BORDA)
        st.configure("Treeview.Heading", font=(self.F_SANS, 9, "bold"),
                     background=self.COR_CABECALHO, foreground=self.COR_CABEC_TX,
                     relief="flat", padding=[4, 5])
        st.map("Treeview.Heading", background=[("active", self.COR_TEXTO)])
        st.map("Treeview",
               background=[("selected", self.COR_ACENTO)],
               foreground=[("selected", self.COR_SUPERFICIE)])

        st.configure("Horizontal.TProgressbar",
                     background=self.COR_ACENTO, troughcolor=self.COR_BORDA,
                     bordercolor=self.COR_BORDA,
                     lightcolor=self.COR_ACENTO, darkcolor=self.COR_ACENTO)

        self.root.configure(background=fundo)

    # ── Interface ───────────────────────────────────────────────
    def _construir_interface(self):
        main = ttk.Frame(self.root, padding=16)
        main.pack(fill=tk.BOTH, expand=True)

        cab = ttk.Frame(main); cab.pack(fill="x", anchor="w")
        ttk.Label(cab, text="ATS — Análise de Estoque",
                  font=(self.F_SERIF, 19, "bold"),
                  foreground=self.COR_TEXTO).pack(side="left")
        ttk.Label(cab, text="  Curva ABC × XYZ · Grupo Dluz",
                  font=(self.F_SANS, 10),
                  foreground=self.COR_ACENTO_ESC).pack(side="left", anchor="s", pady=(0, 3))
        ttk.Label(main,
                  text="Conecta no Firebird, extrai toda a base e analisa · "
                       "Estoque de segurança · Cobertura · Demanda irregular · "
                       "Meses sem movimento.",
                  foreground=self.COR_SUBTEXTO).pack(anchor="w", pady=(2, 10))
        ttk.Separator(main, orient="horizontal").pack(fill="x", pady=(0, 10))

        # Conexão + configurações
        grp = ttk.LabelFrame(main, text="  Conexão e Configurações  ", padding=10)
        grp.pack(fill="x", pady=(0, 8))

        # Seletor de empresa
        emp = ttk.Frame(grp); emp.pack(fill="x", pady=(0, 6))
        ttk.Label(emp, text="Empresa:",
                  font=(self.F_SANS, 9, "bold")).pack(side="left")
        for nome in EMPRESAS:
            ttk.Radiobutton(emp, text=nome, value=nome,
                            variable=self.empresa,
                            command=self._trocar_empresa).pack(side="left", padx=6)

        info = ttk.Frame(grp); info.pack(fill="x", pady=(0, 6))
        self.lbl_conexao = ttk.Label(info, text=self._texto_conexao(),
                                     foreground=self.COR_SUBTEXTO,
                                     font=(self.F_MONO, 9))
        self.lbl_conexao.pack(anchor="w")

        cfg = ttk.Frame(grp); cfg.pack(fill="x")
        ttk.Label(cfg, text="Lead Time (meses):").pack(side="left")
        for lt in self.LEAD_TIMES:
            ttk.Radiobutton(cfg, text=f"{lt:g}", value=lt,
                            variable=self.lead_time,
                            command=self._trocar_lead_time).pack(side="left", padx=4)
        ttk.Label(cfg, text="— troque para ver o resultado na hora.",
                  foreground=self.COR_SUBTEXTO).pack(side="left", padx=8)

        # Agrupamento de produtos equivalentes
        gr = ttk.Frame(grp); gr.pack(fill="x", pady=(6, 0))
        ttk.Checkbutton(
            gr, text="Agrupar produtos equivalentes (mesmo produto, marcas diferentes)",
            variable=self.agrupar, command=self._toggle_agrupar).pack(side="left")
        ttk.Button(gr, text="Carregar grupos…", width=16,
                   command=self._escolher_arquivo_grupos).pack(side="left", padx=8)
        ttk.Label(gr, textvariable=self.lbl_grupos_txt,
                  foreground=self.COR_SUBTEXTO,
                  font=(self.F_SANS, 8)).pack(side="left")

        # Ação
        action = ttk.Frame(main); action.pack(fill="x", pady=(0, 8))
        self.btn_run = ttk.Button(action, text="▶  Conectar e Analisar",
                                  command=self._iniciar, width=22,
                                  style="Accent.TButton")
        self.btn_run.pack(side="left")
        self.progress = ttk.Progressbar(action, mode="indeterminate", length=160)
        self.progress.pack(side="left", padx=10)
        self.lbl_status = ttk.Label(action, text="Clique para conectar e analisar.",
                                    foreground=self.COR_SUBTEXTO)
        self.lbl_status.pack(side="left")

        ttk.Separator(main, orient="horizontal").pack(fill="x", pady=(0, 8))

        # Dashboard: cards + matriz
        dash = ttk.Frame(main); dash.pack(fill="x", pady=(0, 6))
        self.frm_cards = ttk.Frame(dash); self.frm_cards.pack(side="left", fill="y")
        self._render_cards([("Produtos", "—", self.COR_TEXTO),
                            ("Com Pedido", "—", self.COR_PEDIDO),
                            ("Sem Giro", "—", self.COR_SUBTEXTO),
                            ("Irregulares", "—", self.COR_AVISO),
                            ("Capital Parado", "—", self.COR_ACENTO_ESC)])
        self.frm_matriz = ttk.Frame(dash)
        self.frm_matriz.pack(side="left", padx=(20, 0), fill="y", anchor="n")

        # Filtros de visualização (fornecedor + busca)
        filtro = ttk.Frame(main); filtro.pack(fill="x", pady=(4, 6))
        ttk.Label(filtro, text="🔎  Fornecedor:",
                  font=(self.F_SANS, 9, "bold")).pack(side="left")
        self.btn_fornecedor = ttk.Button(
            filtro, text="Fornecedor: Todos  ▾", width=28,
            command=self._abrir_popup_fornecedor, state="disabled")
        self.btn_fornecedor.pack(side="left", padx=8)
        ttk.Label(filtro, text="Buscar:",
                  font=(self.F_SANS, 9, "bold")).pack(side="left", padx=(10, 0))
        self._campo_busca(filtro, self.filtro_texto, largura=24).pack(
            side="left", padx=8)
        self.filtro_texto.trace_add("write", lambda *_: self._aplicar_filtro())
        self.btn_filtro_col = ttk.Button(
            filtro, text="🚫0  Filtrar colunas  ▾", width=20,
            command=self._abrir_popup_filtro_colunas)
        self.btn_filtro_col.pack(side="left", padx=(10, 0))

        dica = ttk.Frame(main); dica.pack(fill="x", pady=(0, 6))
        ttk.Label(dica,
                  text="vários termos = E, ex.: “painel 24w”  ·  "
                       "(a análise é sempre sobre toda a base)",
                  foreground=self.COR_SUBTEXTO, font=(self.F_SANS, 8)).pack(side="left")

        # Exportação — ancorada no rodapé ANTES do notebook, para nunca
        # ser empurrada para fora da janela quando as tabelas expandem
        exp = ttk.Frame(main); exp.pack(fill="x", side="bottom", pady=(8, 0))
        ttk.Button(exp, text="💾  Exportar CSV",
                   command=lambda: self._exportar("csv")).pack(side="left", padx=(0, 6))
        ttk.Button(exp, text="📊  Exportar Excel",
                   command=lambda: self._exportar("xlsx")).pack(side="left")

        # Notebook
        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill="both", expand=True)
        tab_ped = ttk.Frame(self.notebook); tab_a = ttk.Frame(self.notebook)
        tab_all = ttk.Frame(self.notebook)
        self.tab_media = ttk.Frame(self.notebook)
        tab_grupo = ttk.Frame(self.notebook)
        self.notebook.add(tab_ped, text="  Pedidos de Compra  ")
        self.notebook.add(tab_a,   text="  Curva A  ")
        self.notebook.add(tab_all, text="  Todos os Produtos  ")
        self.notebook.add(self.tab_media, text="  Média Mensal  ")
        self.notebook.add(tab_grupo, text="  Grupo (3 Empresas)  ")
        self.tree_pedidos  = self._montar_tree(tab_ped, self.COLUNAS_PEDIDOS)
        self.tree_curva_a  = self._montar_tree(tab_a)
        self.tree_todos    = self._montar_tree(tab_all, self.COLUNAS_TODOS)
        self._montar_tab_grupo(tab_grupo)

        for tree in (self.tree_pedidos, self.tree_curva_a, self.tree_todos):
            tree.bind("<Double-1>", self._on_double_click_produto)

    # ── Helpers de UI ───────────────────────────────────────────
    def _render_cards(self, dados):
        for w in self.frm_cards.winfo_children():
            w.destroy()
        for titulo, valor, cor in dados:
            borda = tk.Frame(self.frm_cards, bg=self.COR_BORDA, padx=1, pady=1)
            borda.pack(side="left", padx=4)
            card = tk.Frame(borda, bg=self.COR_SUPERFICIE)
            card.pack()
            # Filete superior na cor do indicador (assinatura dos cards Dluz)
            tk.Frame(card, bg=cor, height=3, width=110).pack(fill="x")
            corpo = tk.Frame(card, bg=self.COR_SUPERFICIE, padx=14, pady=6)
            corpo.pack()
            tk.Label(corpo, text=valor, font=(self.F_MONO, 15, "bold"),
                     fg=cor, bg=self.COR_SUPERFICIE).pack()
            tk.Label(corpo, text=titulo.upper(), font=(self.F_SANS, 8),
                     fg=self.COR_SUBTEXTO, bg=self.COR_SUPERFICIE).pack()

    def _render_matriz(self, df):
        for w in self.frm_matriz.winfo_children():
            w.destroy()
        grp = ttk.LabelFrame(self.frm_matriz, text="  Distribuição ABC × XYZ  ", padding=8)
        grp.pack(side="left", anchor="n")
        cores = {"A": self.COR_A, "B": self.COR_B, "C": self.COR_C}
        HF = (self.F_SANS, 9, "bold"); CF = (self.F_MONO, 10)
        PAD = {"padx": 2, "pady": 2, "sticky": "nsew"}; W = 9
        for j, txt in enumerate(("", "X regular", "Y variável", "Z errático")):
            tk.Label(grp, text=txt, bg=self.COR_CABECALHO, fg=self.COR_CABEC_TX,
                     font=HF, width=W, padx=4, pady=4).grid(row=0, column=j, **PAD)
        for i, abc in enumerate(("A", "B", "C")):
            bg = cores[abc]
            tk.Label(grp, text=f"  {abc}  ", bg=bg, fg=self.COR_TEXTO, font=HF,
                     padx=4, pady=4).grid(row=i+1, column=0, **PAD)
            for j, xyz in enumerate(("X", "Y", "Z")):
                cnt = int((df["classe"] == abc + xyz).sum()) if not df.empty else 0
                tk.Label(grp, text=str(cnt), bg=bg, fg=self.COR_TEXTO, font=CF,
                         width=W, pady=4).grid(row=i+1, column=j+1, **PAD)

    def _campo_busca(self, parent, variavel, largura=24) -> tk.Entry:
        """Campo de busca com caret (cursor de texto) sempre visível.

        Usa tk.Entry em vez de ttk.Entry porque só o clássico expõe
        `insertbackground` — no ttk o caret herda a cor do sistema, que
        no modo escuro do macOS é branca e desaparece no campo creme.
        """
        return tk.Entry(
            parent, textvariable=variavel, width=largura,
            font=(self.F_SANS, 10),
            bg=self.COR_SUPERFICIE, fg=self.COR_TEXTO,
            insertbackground=self.COR_TEXTO,   # ← o caret
            insertwidth=2,                     # um pouco mais grosso, fica nítido
            disabledbackground=self.COR_SG, disabledforeground=self.COR_SUBTEXTO,
            selectbackground=self.COR_ACENTO, selectforeground=self.COR_SUPERFICIE,
            relief="solid", borderwidth=1, highlightthickness=1,
            highlightbackground=self.COR_BORDA, highlightcolor=self.COR_ACENTO,
        )

    def _fmt_moeda(self, v) -> str:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "—"
        return "R$ " + f"{v:,.0f}".replace(",", ".")

    def _fmt_data(self, d) -> str:
        """Data curta dd/mm/aa; '—' quando não há registro de entrada."""
        if d is None or pd.isna(d):
            return "—"
        try:
            return pd.Timestamp(d).strftime("%d/%m/%y")
        except (ValueError, TypeError):
            return "—"

    def _fmt_tendencia(self, pct) -> str:
        if pct is None or (isinstance(pct, float) and pd.isna(pct)):
            return "—"
        if pct > 20:
            return f"▲ +{pct:.0f}%"
        if pct < -20:
            return f"▼ {pct:.0f}%"
        sinal = "+" if pct >= 0 else ""
        return f"→ {sinal}{pct:.0f}%"

    def _montar_tree(self, parent, colunas=None):
        colunas = colunas or self.COLUNAS
        frm = ttk.Frame(parent); frm.pack(fill="both", expand=True, padx=2, pady=2)
        tree = ttk.Treeview(frm, columns=colunas, show="headings", height=10)
        vsb = ttk.Scrollbar(frm, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(frm, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        for col in colunas:
            anc = "w" if col in ("Descrição", "Fornecedor") else "center"
            tree.heading(col, text=col, command=lambda c=col: self._ordenar(tree, c))
            tree.column(col, width=self.COL_WIDTHS.get(col, 80), anchor=anc, minwidth=40)
        tree.tag_configure("A", background=self.COR_A)
        tree.tag_configure("B", background=self.COR_B)
        tree.tag_configure("C", background=self.COR_C)
        tree.tag_configure("sem_giro", background=self.COR_SG,
                           foreground=self.COR_SUBTEXTO)
        # Produto novo sem venda: cinza claro como o "sem giro", mas em
        # itálico e texto âmbar — não é encalhe, é falta de tempo
        tree.tag_configure("novo", background=self.COR_B,
                           foreground=self.COR_ACENTO_ESC,
                           font=(self.F_SANS, 9, "italic"))
        tree.tag_configure("pedido", foreground=self.COR_PEDIDO,
                           font=(self.F_SANS, 9, "bold"))
        tree.tag_configure("subtotal", background=self.COR_CABECALHO,
                           foreground=self.COR_CABEC_TX,
                           font=(self.F_SANS, 9, "bold"))
        vsb.pack(side="right", fill="y"); hsb.pack(side="bottom", fill="x")
        tree.pack(fill="both", expand=True)
        return tree

    def _montar_tree_media(self, meses_info):
        """Cria a treeview da aba Média Mensal com uma coluna por mês."""
        for w in self.tab_media.winfo_children():
            w.destroy()

        labels = [lbl for (_, lbl, _) in meses_info]
        cols   = ("Código", "Descrição", "Fornecedor", *labels, "Média")

        topo = ttk.Frame(self.tab_media); topo.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Label(topo,
                  text="Vendas por mês · coluna com * = mês atual (parcial) · "
                       "Média calculada só sobre meses completos.",
                  foreground=self.COR_SUBTEXTO, font=(self.F_SANS, 8)).pack(anchor="w")

        frm  = ttk.Frame(self.tab_media); frm.pack(fill="both", expand=True, padx=2, pady=2)
        tree = ttk.Treeview(frm, columns=cols, show="headings", height=10)
        vsb  = ttk.Scrollbar(frm, orient="vertical",   command=tree.yview)
        hsb  = ttk.Scrollbar(frm, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        for col in cols:
            if   col == "Código":     w, anc = 60,  "center"
            elif col == "Descrição":  w, anc = 200, "w"
            elif col == "Fornecedor": w, anc = 130, "w"
            elif col == "Média":      w, anc = 70,  "center"
            else:                     w, anc = 52,  "center"
            tree.heading(col, text=col, command=lambda c=col: self._ordenar(tree, c))
            tree.column(col, width=w, anchor=anc, minwidth=40)

        tree.tag_configure("media", background=self.COR_SUPERFICIE)
        vsb.pack(side="right", fill="y"); hsb.pack(side="bottom", fill="x")
        tree.pack(fill="both", expand=True)

        self.tree_media = tree
        self.meses_cols_media = [col for (col, _, _) in meses_info]  # nomes reais no df

    def _preencher_media(self, df):
        if self.tree_media is None:
            return
        self.tree_media.delete(*self.tree_media.get_children())
        if df is None or df.empty:
            return
        tem_forn = "Fornecedor" in df.columns
        for _, row in df.iterrows():
            try: cod = int(row["Cod. do Produto"])
            except (ValueError, TypeError): cod = row["Cod. do Produto"]
            valores = [cod, row["Descrição"], row["Fornecedor"] if tem_forn else ""]
            for mcol in self.meses_cols_media:
                v = row.get(mcol, 0)
                valores.append(int(v) if pd.notna(v) else 0)
            valores.append(f"{row['media']:.1f}")
            self.tree_media.insert("", "end", tags=("media",), values=valores)

    # ── Aba "Grupo (3 Empresas)" — estoque consolidado ───────────
    def _montar_tab_grupo(self, parent):
        self.filtro_grupo_texto = tk.StringVar()
        self.df_grupo: Optional[pd.DataFrame] = None

        topo = ttk.Frame(parent); topo.pack(fill="x", padx=4, pady=(6, 2))
        self.btn_grupo = ttk.Button(
            topo, text="🔄  Atualizar Consolidado", width=22,
            command=self._atualizar_grupo, style="Accent.TButton")
        self.btn_grupo.pack(side="left")
        self.progress_grupo = ttk.Progressbar(topo, mode="indeterminate", length=120)
        self.progress_grupo.pack(side="left", padx=10)
        self.lbl_status_grupo = ttk.Label(
            topo,
            text="Soma o estoque das 3 empresas por descrição — útil para "
                 "ver, antes de comprar, se o produto já existe parado em "
                 "outra loja do grupo.",
            foreground=self.COR_SUBTEXTO, wraplength=520)
        self.lbl_status_grupo.pack(side="left", padx=8)

        busca = ttk.Frame(parent); busca.pack(fill="x", padx=4, pady=(0, 6))
        ttk.Label(busca, text="Buscar:",
                  font=(self.F_SANS, 9, "bold")).pack(side="left")
        self._campo_busca(busca, self.filtro_grupo_texto, largura=30).pack(
            side="left", padx=8)
        self.filtro_grupo_texto.trace_add("write", lambda *_: self._preencher_grupo())

        colunas = ("Descrição", *EMPRESAS.keys(), "Total")
        frm = ttk.Frame(parent); frm.pack(fill="both", expand=True, padx=2, pady=2)
        tree = ttk.Treeview(frm, columns=colunas, show="headings", height=10)
        vsb = ttk.Scrollbar(frm, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(frm, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        for col in colunas:
            anc = "w" if col == "Descrição" else "center"
            w = 260 if col == "Descrição" else 130
            tree.heading(col, text=col, command=lambda c=col: self._ordenar(tree, c))
            tree.column(col, width=w, anchor=anc, minwidth=60)
        tree.tag_configure("subtotal", background=self.COR_CABECALHO,
                           foreground=self.COR_CABEC_TX, font=(self.F_SANS, 9, "bold"))
        vsb.pack(side="right", fill="y"); hsb.pack(side="bottom", fill="x")
        tree.pack(fill="both", expand=True)
        self.tree_grupo = tree

    def _atualizar_grupo(self):
        faltando = [e for e in EMPRESAS if e not in self.raw]
        self.btn_grupo.state(["disabled"])
        self.progress_grupo.start(10)
        self.lbl_status_grupo.config(
            text=(f"Conectando e extraindo: {', '.join(faltando)}…" if faltando
                  else "Consolidando dados já carregados…"),
            foreground=self.COR_AVISO)
        threading.Thread(target=self._executar_grupo, args=(faltando,),
                         daemon=True).start()

    def _executar_grupo(self, faltando: list):
        erros = []
        for empresa in faltando:
            try:
                df_estoque, df_vendas, df_ultima = extrair_dados(empresa)
                self.raw[empresa] = (df_estoque, df_vendas, df_ultima)
                self.cache_timestamps[empresa] = datetime.now()
                salvar_cache(empresa, df_estoque, df_vendas, df_ultima)
            except Exception as exc:
                erros.append(f"{empresa}: {exc}")
        self.root.after(0, self._pos_grupo, erros)

    def _pos_grupo(self, erros: list):
        self.progress_grupo.stop()
        self.btn_grupo.state(["!disabled"])
        self.df_grupo = consolidar_grupo(self.raw)
        self._preencher_grupo()
        n_empresas = sum(1 for e in EMPRESAS if e in self.raw)
        txt = f"✔  {n_empresas}/3 empresas carregadas · {len(self.df_grupo)} produtos"
        if erros:
            txt += f"   ·   falhas: {'; '.join(erros)}"
        self.lbl_status_grupo.config(
            text=txt, foreground=self.COR_PEDIDO if erros else self.COR_OK)

    def _preencher_grupo(self):
        tree = self.tree_grupo
        tree.delete(*tree.get_children())
        if self.df_grupo is None or self.df_grupo.empty:
            return
        df = self.df_grupo
        termos = self.filtro_grupo_texto.get().strip().lower().split()
        if termos:
            alvo = df["Descrição"].astype(str).str.lower()
            mask = pd.Series(True, index=df.index)
            for t in termos:
                mask &= alvo.str.contains(t, regex=False)
            df = df[mask]
        for _, row in df.iterrows():
            valores = [row["Descrição"]]
            for empresa in EMPRESAS:
                valores.append(int(row.get(empresa, 0)))
            valores.append(int(row["Total"]))
            tree.insert("", "end", values=valores)
        if termos and not df.empty:
            subtotal = ["TOTAL — " + str(len(df)) + " produtos filtrados"]
            for empresa in EMPRESAS:
                subtotal.append(int(df[empresa].sum()))
            subtotal.append(int(df["Total"].sum()))
            tree.insert("", "end", tags=("subtotal",), values=subtotal)

    # ── Detalhe do produto (duplo clique) ────────────────────────
    def _on_double_click_produto(self, event):
        tree = event.widget
        iid = tree.identify_row(event.y)
        if not iid:
            return
        cod = tree.set(iid, "Código")
        if not cod:
            return
        self._abrir_detalhe_produto(cod)

    def _abrir_detalhe_produto(self, cod_str: str):
        if self.df_resultado is None:
            return
        cod_norm = str(cod_str).strip()
        linhas = self.df_resultado[
            self.df_resultado["Cod. do Produto"].astype(str).str.strip() == cod_norm
        ]
        if linhas.empty:
            return
        row = linhas.iloc[0]

        win = tk.Toplevel(self.root)
        win.title(f"Produto {cod_norm}")
        win.configure(background=self.COR_SUPERFICIE)
        win.transient(self.root)
        win.resizable(False, False)
        win.bind("<Escape>", lambda e: win.destroy())

        cab = ttk.Frame(win); cab.pack(fill="x", padx=14, pady=(14, 4))
        ttk.Label(cab, text=str(row["Descrição"]),
                  font=(self.F_SERIF, 14, "bold"), foreground=self.COR_TEXTO,
                  wraplength=440).pack(anchor="w")
        ttk.Label(cab,
                  text=f"Código {cod_norm}   ·   Classe {row['classe']}   ·   "
                       f"Fornecedor: {row.get('Fornecedor', '—')}",
                  foreground=self.COR_SUBTEXTO, font=(self.F_SANS, 9)
                  ).pack(anchor="w", pady=(2, 0))

        # ── Indicadores ──
        ficha = ttk.LabelFrame(win, text="  Indicadores  ", padding=10)
        ficha.pack(fill="x", padx=14, pady=6)
        pedido_qtd = int(row.get("pedido", 0))
        pedido_r = row.get("pedido_valor", np.nan)
        ped_txt = "— (estoque ok)"
        if pedido_qtd > 0:
            ped_txt = f"{pedido_qtd} un"
            if pd.notna(pedido_r):
                ped_txt += f"   ({self._fmt_moeda(pedido_r)})"
        cob = row.get("cobertura_meses", np.nan)
        msm = row.get("meses_sem_movimento", np.nan)
        entrada = row.get("Ultima Entrada", pd.NaT)
        mde = row.get("meses_desde_entrada", np.nan)
        entrada_txt = self._fmt_data(entrada)
        if pd.notna(mde):
            entrada_txt += f"  ({mde:.1f} m)"
        campos = [
            ("Média/mês", f"{row['media']:.1f} un"),
            ("Valor/mês", self._fmt_moeda(row.get("valor_mensal", 0))),
            ("P. Custo (últ. NF)", self._fmt_moeda(row.get("Custo Liquido", np.nan))),
            ("P. Venda", self._fmt_moeda(row.get("Preco de Venda", np.nan))),
            ("Tendência (3m vs 12m)", self._fmt_tendencia(row.get("tendencia_pct", np.nan))),
            ("Cobertura", f"{cob:.1f} meses" if pd.notna(cob) else "∞"),
            ("Sem giro há", f"{msm:.1f} meses" if pd.notna(msm) else "∞ (sem registro)"),
            ("Última entrada (NF)", entrada_txt),
            ("Estoque atual", f"{int(row['estoque_atual'])} un"),
            ("Estoque mínimo", f"{row['estoq_minimo']:.0f} un"),
            ("Capital parado", self._fmt_moeda(row.get("capital_parado", np.nan))),
            ("Pedido sugerido", ped_txt),
        ]
        for i, (label, valor) in enumerate(campos):
            r, c = divmod(i, 2)
            cel = ttk.Frame(ficha); cel.grid(row=r, column=c, sticky="w",
                                             padx=(0, 28), pady=3)
            ttk.Label(cel, text=label, foreground=self.COR_SUBTEXTO,
                      font=(self.F_SANS, 8)).pack(anchor="w")
            ttk.Label(cel, text=valor, font=(self.F_MONO, 11, "bold"),
                      foreground=self.COR_TEXTO).pack(anchor="w")

        # ── Leitura do "sem giro": produto novo ou encalhado? ──
        if bool(row.get("sem_giro", False)) or bool(row.get("produto_novo", False)):
            if bool(row.get("produto_novo", False)):
                diag = (f"🆕 Produto NOVO — entrou há {mde:.1f} meses e ainda "
                        "não vendeu. Provavelmente falta tempo de prateleira, "
                        "não encalhe.")
                cor = self.COR_ACENTO_ESC
            elif pd.notna(mde):
                diag = (f"⚠ Encalhado — entrou há {mde:.1f} meses "
                        f"({self._fmt_data(entrada)}) e não vendeu no período "
                        "analisado.")
                cor = self.COR_PEDIDO
            else:
                diag = ("⚠ Sem venda no período e sem registro de entrada por "
                        "NF — não dá pra saber se é novo ou encalhado.")
                cor = self.COR_AVISO
            ttk.Label(win, text=diag, foreground=cor, wraplength=440,
                      font=(self.F_SANS, 9)).pack(padx=14, pady=(0, 4), anchor="w")

        # ── Histórico mensal ──
        if self.meses_info:
            hist = ttk.LabelFrame(win, text="  Histórico mensal  ", padding=10)
            hist.pack(fill="x", padx=14, pady=6)
            for j, (col, label, _parcial) in enumerate(self.meses_info):
                v = row.get(col, 0)
                v = int(v) if pd.notna(v) else 0
                cel = ttk.Frame(hist); cel.grid(row=0, column=j, padx=6)
                ttk.Label(cel, text=label, foreground=self.COR_SUBTEXTO,
                          font=(self.F_SANS, 8)).pack()
                ttk.Label(cel, text=str(v), font=(self.F_MONO, 10, "bold"),
                          foreground=self.COR_TEXTO).pack()

        # ── Membros do grupo, se for um produto-grupo agrupado ──
        if cod_norm.startswith("G") and self.mapa_grupos:
            nome_grupo = str(row["Descrição"]).split("[grupo]")[0].strip()
            membros = sorted(c for c, g in self.mapa_grupos.items() if g == nome_grupo)
            if membros:
                grp = ttk.LabelFrame(win, text="  Códigos agrupados neste item  ",
                                     padding=10)
                grp.pack(fill="x", padx=14, pady=6)
                ttk.Label(grp, text=", ".join(membros), wraplength=440,
                          font=(self.F_MONO, 9)).pack(anchor="w")

        ttk.Button(win, text="Fechar", command=win.destroy).pack(pady=(4, 14))

    def _ordenar(self, tree, col):
        # A linha de TOTAL não entra na ordenação — ela fica sempre
        # ancorada no fim (antes ela subia junto com os números)
        todos = list(tree.get_children(""))
        subtotais = [k for k in todos if "subtotal" in tree.item(k, "tags")]
        linhas = [k for k in todos if k not in subtotais]

        dados = [(tree.set(k, col), k) for k in linhas]
        rev = getattr(self, f"_sort_{id(tree)}", {})
        asc = not rev.get(col, False); rev[col] = asc
        setattr(self, f"_sort_{id(tree)}", rev)

        def _conv(v):
            txt = str(v).strip()
            # datas dd/mm/aa (coluna "Últ. Entrada") — ordena
            # cronologicamente, não como texto
            m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{2,4})", txt)
            if m:
                d, mes, a = (int(x) for x in m.groups())
                if a < 100:
                    a += 2000
                return (0, a * 10000 + mes * 100 + d)
            num = txt.replace("R$", "").replace("—", "0").replace("∞", "9999").strip()
            # O ponto é ambíguo: separador de MILHAR em "1.234" (valores)
            # e DECIMAL em "10.5" (média, cobertura). Só grupos de exatos
            # 3 dígitos são milhar — sem isso "1.234" virava 1,234 e
            # ordenava abaixo de "999".
            if re.fullmatch(r"-?\d{1,3}(\.\d{3})+", num):
                num = num.replace(".", "")
            try:
                return (0, float(num.replace(",", ".")))
            except (ValueError, AttributeError):
                return (1, txt.lower())

        dados.sort(key=lambda t: _conv(t[0]), reverse=not asc)
        for idx, (_, k) in enumerate(dados):
            tree.move(k, "", idx)
        for k in subtotais:                      # TOTAL sempre por último
            tree.move(k, "", "end")

    def _centralizar(self):
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        ww, wh = self.root.winfo_width(), self.root.winfo_height()
        self.root.geometry(f"{ww}x{wh}+{(sw-ww)//2}+{(sh-wh)//2}")

    # ── Execução ────────────────────────────────────────────────
    def _carregar_cache_inicial(self):
        """No início, carrega em silêncio o cache de HOJE de qualquer
        empresa disponível — abre o programa já com dados prontos, sem
        esperar o Firebird. 'Conectar e Analisar' sempre busca dados
        novos e substitui o cache."""
        carregadas = []
        for nome_empresa in EMPRESAS:
            cached = carregar_cache(nome_empresa)
            if cached is None:
                continue
            df_estoque, df_vendas, df_ultima, ts = cached
            self.raw[nome_empresa] = (df_estoque, df_vendas, df_ultima)
            self.cache_timestamps[nome_empresa] = ts
            carregadas.append(nome_empresa)
        if not carregadas:
            return
        if self.empresa.get() in carregadas:
            self._carregar_resultado()
            self.lbl_conexao.config(text=self._texto_conexao())
        self.df_grupo = consolidar_grupo(self.raw)
        self._preencher_grupo()
        self.lbl_status_grupo.config(
            text=f"📦 {len(carregadas)}/3 empresas do cache de hoje · "
                 f"{len(self.df_grupo)} produtos. Atualize para completar as "
                 "que faltam.",
            foreground=self.COR_AVISO)
        hora = self.cache_timestamps[carregadas[0]].strftime("%H:%M")
        self.lbl_status.config(
            text=f"📦 Dados em cache de hoje ({hora}) — "
                 f"{', '.join(carregadas)}. Clique em Conectar e Analisar "
                 "para atualizar.",
            foreground=self.COR_AVISO)

    def _texto_conexao(self):
        cfg = EMPRESAS[self.empresa.get()]
        banco = cfg["database"].split(chr(92))[-1]
        ts = self.cache_timestamps.get(self.empresa.get())
        sufixo = f"   ·   dados de {ts.strftime('%H:%M')}" if ts else ""
        return (f"Servidor: {cfg['host']}   ·   Banco: {banco}   "
                f"·   Período: {DATA_INI}  →  {DATA_FIM}{sufixo}")

    def _iniciar(self):
        self.btn_run.state(["disabled"])
        self.btn_fornecedor.state(["disabled"])
        self.progress.start(10)
        self.lbl_status.config(
            text=f"Conectando em {self.empresa.get()} e extraindo dados…",
            foreground=self.COR_AVISO)
        threading.Thread(target=self._executar,
                         args=(self.empresa.get(),), daemon=True).start()

    def _executar(self, empresa):
        try:
            df_estoque, df_vendas, df_ultima = extrair_dados(empresa)
            if df_vendas.empty:
                raise ValueError("Nenhuma movimentação encontrada no período. "
                                 "Verifique as datas ou o tipo de pedido (55).")
            self.raw[empresa] = (df_estoque, df_vendas, df_ultima)
            self.cache_timestamps[empresa] = datetime.now()
            salvar_cache(empresa, df_estoque, df_vendas, df_ultima)
            # Dados novos: invalida análises antigas desta empresa
            self.cache = {k: v for k, v in self.cache.items() if k[0] != empresa}
            self.root.after(0, self._pos_extracao)
        except Exception as exc:
            self.root.after(0, self._erro, str(exc))

    def _pos_extracao(self):
        self.progress.stop()
        self.btn_run.state(["!disabled"])
        self._carregar_resultado()
        self.notebook.select(0)

    def _resultado_atual(self):
        """Análise da empresa/lead time/agrupamento atuais, com memoização."""
        empresa = self.empresa.get()
        if empresa not in self.raw:
            return None
        agrupar = bool(self.agrupar.get() and self.mapa_grupos)
        chave = (empresa, self.lead_time.get(), agrupar)
        if chave not in self.cache:
            df_estoque, df_vendas, df_ultima = self.raw[empresa]
            if agrupar:
                df_estoque, df_vendas, df_ultima = aplicar_grupos(
                    df_estoque, df_vendas, self.mapa_grupos, df_ultima)
            self.cache[chave] = calcular_abc_xyz(
                df_vendas, df_estoque, self.lead_time.get(), df_ultima)
        return self.cache[chave]

    def _carregar_resultado(self):
        res = self._resultado_atual()
        if res is None:
            self.lbl_status.config(
                text=f"{self.empresa.get()} selecionada — "
                     "clique em Conectar e Analisar.",
                foreground=self.COR_SUBTEXTO)
            return
        self.df_resultado, self.meses_info = res

        # Monta a aba de média mensal com colunas dinâmicas (uma por mês)
        self._montar_tree_media(self.meses_info)

        # Popula o seletor de fornecedores (multi-seleção)
        forns = []
        if "Fornecedor" in self.df_resultado.columns:
            forns = sorted(self.df_resultado["Fornecedor"].dropna().unique().tolist())
        self._popular_fornecedores(forns)

        self._aplicar_filtro()

    def _trocar_empresa(self):
        """Troca de empresa: usa dados já extraídos ou pede nova conexão."""
        self.lbl_conexao.config(text=self._texto_conexao())
        self._carregar_resultado()

    def _trocar_lead_time(self):
        """Troca de lead time recalculada em memória (sem reconsultar o banco)."""
        if self.raw:
            self._carregar_resultado()

    # ── Filtro de fornecedor (multi-seleção) ────────────────────
    def _popular_fornecedores(self, forns: list):
        # Reabilita o botão SEMPRE que houver fornecedores — inclusive
        # quando a lista não mudou (ex.: reanálise da mesma empresa, que
        # desabilita o botão em _iniciar e antes não o reabilitava aqui)
        self.btn_fornecedor.state(["!disabled"] if forns else ["disabled"])
        if forns == self.fornecedores_todos:
            return  # mesma lista de fornecedores — mantém a seleção do usuário
        self.fornecedores_todos = forns
        self.fornecedores_selecionados &= set(forns)
        self._fechar_popup_fornecedor()
        self._atualizar_label_fornecedor()

    def _fechar_popup_fornecedor(self):
        if self.win_fornecedor is not None and self.win_fornecedor.winfo_exists():
            self.win_fornecedor.destroy()
        self.win_fornecedor = None
        self.lst_fornecedor = None

    def _atualizar_label_fornecedor(self):
        n = len(self.fornecedores_selecionados)
        if n == 0:
            texto = "Fornecedor: Todos"
        elif n == 1:
            nome = next(iter(self.fornecedores_selecionados))
            texto = f"Fornecedor: {nome}"
            if len(texto) > 26:
                texto = texto[:24] + "…"
        else:
            texto = f"Fornecedores: {n} selecionados"
        self.btn_fornecedor.config(text=f"{texto}  ▾")

    def _abrir_popup_fornecedor(self):
        if not self.fornecedores_todos:
            return
        if self.win_fornecedor is not None and self.win_fornecedor.winfo_exists():
            self.win_fornecedor.lift()
            return

        win = tk.Toplevel(self.root)
        win.title("Filtrar por fornecedor")
        win.configure(background=self.COR_SUPERFICIE)
        win.transient(self.root)
        win.resizable(False, True)
        x = self.btn_fornecedor.winfo_rootx()
        y = self.btn_fornecedor.winfo_rooty() + self.btn_fornecedor.winfo_height()
        win.geometry(f"+{x}+{y}")
        win.bind("<Escape>", lambda e: win.destroy())
        # Limpa as referências quando o popup for fechado por qualquer via
        win.bind("<Destroy>", self._on_popup_fornecedor_destruido)
        self.win_fornecedor = win

        ttk.Label(win,
                  text="Ctrl/Shift + clique para selecionar vários:",
                  font=(self.F_SANS, 9), foreground=self.COR_SUBTEXTO,
                  wraplength=260).pack(padx=10, pady=(10, 4), anchor="w")

        frm = ttk.Frame(win); frm.pack(padx=10, fill="both", expand=True)
        sb  = ttk.Scrollbar(frm, orient="vertical")
        lst = tk.Listbox(
            frm, selectmode="extended", exportselection=False,
            height=min(14, max(6, len(self.fornecedores_todos))),
            yscrollcommand=sb.set,
            bg=self.COR_SUPERFICIE, fg=self.COR_TEXTO,
            selectbackground=self.COR_ACENTO, selectforeground=self.COR_SUPERFICIE,
            font=(self.F_SANS, 9), relief="solid", borderwidth=1,
            highlightthickness=0)
        sb.config(command=lst.yview)
        sb.pack(side="right", fill="y")
        lst.pack(side="left", fill="both", expand=True)
        self.lst_fornecedor = lst

        for nome in self.fornecedores_todos:
            lst.insert("end", nome)
        for i, nome in enumerate(self.fornecedores_todos):
            if nome in self.fornecedores_selecionados:
                lst.selection_set(i)
        lst.bind("<<ListboxSelect>>", self._on_fornecedor_selecionado)

        botoes = ttk.Frame(win); botoes.pack(fill="x", padx=10, pady=8)
        ttk.Button(botoes, text="Todos os fornecedores",
                   command=self._filtro_todos_fornecedores).pack(side="left")
        ttk.Button(botoes, text="Marcar todos",
                   command=lambda: self._marcar_fornecedores(True)).pack(side="left", padx=6)
        ttk.Button(botoes, text="Fechar", command=win.destroy).pack(side="right")

    def _on_popup_fornecedor_destruido(self, event):
        if event.widget is self.win_fornecedor:
            self.win_fornecedor = None
            self.lst_fornecedor = None

    def _listbox_valida(self) -> bool:
        try:
            return (self.lst_fornecedor is not None
                    and self.lst_fornecedor.winfo_exists())
        except tk.TclError:
            return False

    def _filtro_todos_fornecedores(self):
        """Remove o filtro (equivale a 'todos') e fecha o popup."""
        self.fornecedores_selecionados = set()
        self._fechar_popup_fornecedor()
        self._atualizar_label_fornecedor()
        self._aplicar_filtro()

    def _on_fornecedor_selecionado(self, event=None):
        if not self._listbox_valida():
            return
        idx = self.lst_fornecedor.curselection()
        self.fornecedores_selecionados = {self.fornecedores_todos[i] for i in idx}
        self._atualizar_label_fornecedor()
        self._aplicar_filtro()

    def _marcar_fornecedores(self, marcar: bool):
        if not self._listbox_valida():
            return
        if marcar:
            self.lst_fornecedor.selection_set(0, "end")
        else:
            self.lst_fornecedor.selection_clear(0, "end")
        self._on_fornecedor_selecionado()

    # ── Filtro por coluna (ocultar zeros) ────────────────────────
    def _atualizar_label_filtro_colunas(self):
        n = len(self.ocultar_zeros)
        texto = "Filtrar colunas" if n == 0 else f"Filtrar colunas ({n})"
        self.btn_filtro_col.config(text=f"🚫0  {texto}  ▾")

    def _toggle_ocultar_zero(self, campo: str):
        if campo in self.ocultar_zeros:
            self.ocultar_zeros.discard(campo)
        else:
            self.ocultar_zeros.add(campo)
        self._atualizar_label_filtro_colunas()
        self._aplicar_filtro()

    def _limpar_ocultar_zeros(self):
        self.ocultar_zeros = set()
        for var in self.vars_ocultar_zero.values():
            var.set(False)
        self._atualizar_label_filtro_colunas()
        self._aplicar_filtro()

    def _abrir_popup_filtro_colunas(self):
        if self.win_filtro_col is not None and self.win_filtro_col.winfo_exists():
            self.win_filtro_col.lift()
            return

        win = tk.Toplevel(self.root)
        win.title("Filtrar colunas")
        win.configure(background=self.COR_SUPERFICIE)
        win.transient(self.root)
        win.resizable(False, False)
        x = self.btn_filtro_col.winfo_rootx()
        y = self.btn_filtro_col.winfo_rooty() + self.btn_filtro_col.winfo_height()
        win.geometry(f"+{x}+{y}")
        win.bind("<Escape>", lambda e: win.destroy())
        win.bind("<Destroy>", self._on_popup_filtro_col_destruido)
        self.win_filtro_col = win

        ttk.Label(win,
                  text="Oculta produtos com valor 0 (ou vazio) na coluna marcada:",
                  font=(self.F_SANS, 9), foreground=self.COR_SUBTEXTO,
                  wraplength=260).pack(padx=10, pady=(10, 6), anchor="w")

        frm = ttk.Frame(win); frm.pack(padx=10, fill="both", expand=True)
        self.vars_ocultar_zero = {}
        for i, (label, campo) in enumerate(self.FILTRO_ZERO_COLS.items()):
            var = tk.BooleanVar(value=campo in self.ocultar_zeros)
            self.vars_ocultar_zero[campo] = var
            ttk.Checkbutton(
                frm, text=label, variable=var,
                command=lambda c=campo: self._toggle_ocultar_zero(c)
            ).grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 16), pady=2)

        botoes = ttk.Frame(win); botoes.pack(fill="x", padx=10, pady=8)
        ttk.Button(botoes, text="Limpar tudo",
                   command=self._limpar_ocultar_zeros).pack(side="left")
        ttk.Button(botoes, text="Fechar", command=win.destroy).pack(side="right")

    def _on_popup_filtro_col_destruido(self, event):
        if event.widget is self.win_filtro_col:
            self.win_filtro_col = None

    def _aplicar_ocultar_zeros(self, df: pd.DataFrame) -> pd.DataFrame:
        for campo in self.ocultar_zeros:
            if campo not in df.columns:
                continue
            valores = pd.to_numeric(df[campo], errors="coerce")
            if campo in self.CAMPOS_INFINITO:
                # NaN aqui é "∞" (nunca vendeu / cobertura infinita) —
                # não é zero, então continua na tabela
                mask = valores.isna() | (valores != 0)
            else:
                mask = valores.fillna(0) != 0
            df = df[mask]
        return df

    # ── Grupos de produtos equivalentes ─────────────────────────
    GRUPOS_PADRAO = "grupos_produtos"

    def _toggle_agrupar(self):
        if self.agrupar.get() and not self.mapa_grupos:
            if not self._escolher_arquivo_grupos():
                self.agrupar.set(False)
                return
        self._carregar_resultado()

    def _carregar_grupos_padrao(self):
        """Carrega grupos_produtos.xlsx/.csv da pasta do script, se existir."""
        base = os.path.dirname(os.path.abspath(__file__))
        for ext in (".xlsx", ".csv"):
            p = os.path.join(base, self.GRUPOS_PADRAO + ext)
            if os.path.exists(p):
                try:
                    self._aplicar_arquivo_grupos(p)
                except Exception:
                    pass
                return

    def _escolher_arquivo_grupos(self) -> bool:
        p = filedialog.askopenfilename(
            title="Arquivo de grupos (colunas: Codigo, Grupo)",
            filetypes=[("Excel/CSV", "*.xlsx *.xls *.csv"), ("Todos", "*.*")])
        if not p:
            return False
        try:
            self._aplicar_arquivo_grupos(p)
            if self.agrupar.get():
                self._carregar_resultado()
            return True
        except Exception as exc:
            messagebox.showerror("Erro ao carregar grupos", str(exc))
            return False

    def _aplicar_arquivo_grupos(self, path):
        self.mapa_grupos = carregar_grupos(path)
        n_g = len(set(self.mapa_grupos.values()))
        self.lbl_grupos_txt.set(
            f"{os.path.basename(path)} — "
            f"{len(self.mapa_grupos)} códigos em {n_g} grupos")
        # O agrupamento muda a análise → descarta só as versões agrupadas
        self.cache = {k: v for k, v in self.cache.items() if not k[2]}

    def _df_filtrados(self):
        """Aplica os filtros de fornecedor(es), busca e zeros ocultos;
        retorna o df filtrado."""
        df = self.df_resultado
        if self.fornecedores_selecionados and "Fornecedor" in df.columns:
            df = df[df["Fornecedor"].isin(self.fornecedores_selecionados)]
        # Busca por múltiplos "contendo" com lógica E: cada termo digitado
        # (separado por espaço) precisa aparecer na descrição ou no código.
        termos = self.filtro_texto.get().strip().lower().split()
        if termos:
            alvo = (df["Descrição"].astype(str).str.lower() + " "
                    + df["Cod. do Produto"].astype(str).str.lower())
            mask = pd.Series(True, index=df.index)
            for t in termos:
                mask &= alvo.str.contains(t, regex=False)
            df = df[mask]
        df = self._aplicar_ocultar_zeros(df)
        return df

    def _aplicar_filtro(self, event=None):
        if self.df_resultado is None:
            return
        df = self._df_filtrados()

        n_total  = len(df)
        n_pedido = int((df["pedido"] > 0).sum()) if not df.empty else 0
        n_sg     = int(df["sem_giro"].sum()) if not df.empty else 0
        n_irreg  = int(df["intermitente"].sum()) if not df.empty else 0
        capital_parado_sg = (
            df.loc[df["sem_giro"], "capital_parado"].fillna(0).sum()
            if not df.empty else 0.0
        )

        filtros = []
        if self.fornecedores_selecionados:
            if len(self.fornecedores_selecionados) <= 2:
                filtros.append(" + ".join(sorted(self.fornecedores_selecionados)))
            else:
                filtros.append(f"{len(self.fornecedores_selecionados)} fornecedores")
        if self.filtro_texto.get().strip():
            filtros.append(f"busca “{self.filtro_texto.get().strip()}”")
        if self.ocultar_zeros:
            rotulos_zero = [l for l, c in self.FILTRO_ZERO_COLS.items()
                            if c in self.ocultar_zeros]
            filtros.append(f"sem zero em {', '.join(rotulos_zero)}")
        sufixo = f"  ·  filtro: {' + '.join(filtros)}" if filtros else ""
        self.lbl_status.config(
            text=f"✔  {n_total} produtos · {n_pedido} com pedido · "
                 f"{n_sg} sem giro · {n_irreg} irregulares{sufixo}",
            foreground=self.COR_OK)

        self._render_cards([("Produtos", str(n_total), self.COR_TEXTO),
                            ("Com Pedido", str(n_pedido), self.COR_PEDIDO),
                            ("Sem Giro", str(n_sg), self.COR_SUBTEXTO),
                            ("Irregulares", str(n_irreg), self.COR_AVISO),
                            ("Capital Parado", self._fmt_moeda(capital_parado_sg),
                             self.COR_ACENTO_ESC)])
        self._render_matriz(df)
        # Subtotal só aparece quando há um filtro ativo (fornecedor, busca
        # e/ou zeros ocultos) — sem filtro, "total geral" seria redundante
        # com os cards.
        filtro_ativo = bool(self.fornecedores_selecionados
                            or self.filtro_texto.get().strip()
                            or self.ocultar_zeros)
        self._preencher(self.tree_pedidos,
                        df[df["pedido"] > 0] if not df.empty else df, filtro_ativo)
        self._preencher(self.tree_curva_a,
                        df[df["curva_ABC"] == "A"] if not df.empty else df, filtro_ativo)
        self._preencher(self.tree_todos, df, filtro_ativo)
        self._preencher_media(df)

    def _linha_subtotal(self, df: pd.DataFrame, colunas) -> list:
        """Monta a linha de TOTAL somando as colunas numéricas que fazem
        sentido somar (valores, quantidades, R$); o resto fica em branco."""
        celulas = {c: "" for c in colunas}
        rotulo = f"TOTAL — {len(df)} produto{'s' if len(df) != 1 else ''} filtrado{'s' if len(df) != 1 else ''}"
        if "Descrição" in celulas:
            celulas["Descrição"] = rotulo
        elif "Código" in celulas:
            celulas["Código"] = rotulo
        for col, campo in self.SUBTOTAL_COLS.items():
            if col not in celulas or campo not in df.columns:
                continue
            total = df[campo].fillna(0).sum()
            if col in ("Valor/Mês", "Capital Parado", "Pedido R$"):
                celulas[col] = self._fmt_moeda(total)
            elif col == "Média/Mês":
                celulas[col] = f"{total:.1f}"
            elif col == "Est. Mínimo":
                celulas[col] = f"{total:.0f}"
            else:  # Est. Atual, Pedido — quantidades inteiras
                celulas[col] = int(total)
        return [celulas.get(c, "") for c in colunas]

    def _preencher(self, tree, df, mostrar_subtotal=False):
        tree.delete(*tree.get_children())
        if df is None or df.empty:
            return
        colunas = tree["columns"]
        tem_forn = "Fornecedor" in df.columns
        for _, row in df.iterrows():
            ped = int(row["pedido"])
            abc = str(row["classe"])[0] if str(row["classe"])[0] in "ABC" else "C"
            sem_giro = bool(row.get("sem_giro", False))
            novo = bool(row.get("produto_novo", False))
            if ped > 0:
                tag = "pedido"
            elif novo:          # entrou há pouco: ainda não deu tempo de girar
                tag = "novo"
            elif sem_giro:
                tag = "sem_giro"
            else:
                tag = abc
            try: cod = int(row["Cod. do Produto"])
            except (ValueError, TypeError): cod = row["Cod. do Produto"]
            cob = row["cobertura_meses"]
            msm = row.get("meses_sem_movimento", np.nan)
            valor = row.get("valor_mensal", 0)
            custo = row.get("Custo Liquido", np.nan)
            venda = row.get("Preco de Venda", np.nan)
            tend = row.get("tendencia_pct", np.nan)
            capital = row.get("capital_parado", np.nan)
            ped_valor = row.get("pedido_valor", np.nan)
            entrada = row.get("Ultima Entrada", pd.NaT)
            celulas = {
                "Código": cod,
                "Descrição": row["Descrição"],
                "Classe": row["classe"],
                "Fornecedor": row["Fornecedor"] if tem_forn else "",
                "Média/Mês": f"{row['media']:.1f}",
                "Valor/Mês": (f"{valor:,.0f}".replace(",", ".")
                              if pd.notna(valor) else "0"),
                "P. Custo": f"{custo:.2f}" if pd.notna(custo) else "—",
                "P. Venda": f"{venda:.2f}" if pd.notna(venda) else "—",
                "Tendência": self._fmt_tendencia(tend),
                "Cobertura": f"{cob:.1f}" if pd.notna(cob) else "∞",
                "Sem Giro (m)": f"{msm:.1f}" if pd.notna(msm) else "∞",
                "Últ. Entrada": self._fmt_data(entrada),
                "Capital Parado": (f"{capital:,.0f}".replace(",", ".")
                                   if pd.notna(capital) else "—"),
                "Est. Atual": int(row["estoque_atual"]),
                "Est. Mínimo": f"{row['estoq_minimo']:.0f}",
                "Pedido": ped if ped > 0 else "—",
                "Pedido R$": (f"{ped_valor:,.0f}".replace(",", ".")
                              if ped > 0 and pd.notna(ped_valor) else "—"),
                "Demanda": ("⚠ Irregular" if row["intermitente"]
                            else "✓ Regular"),
            }
            tree.insert("", "end", tags=(tag,),
                        values=[celulas.get(c, "") for c in colunas])
        if mostrar_subtotal:
            tree.insert("", "end", tags=("subtotal",),
                        values=self._linha_subtotal(df, colunas))

    def _erro(self, msg):
        self.progress.stop()
        self.btn_run.state(["!disabled"])
        # Reabilita o filtro se já havia dados carregados de antes
        self.btn_fornecedor.state(
            ["!disabled"] if self.fornecedores_todos else ["disabled"])
        self.lbl_status.config(text="Erro.", foreground=self.COR_PEDIDO)
        messagebox.showerror("Erro", f"Ocorreu um erro:\n\n{msg}")

    # ── Exportação ──────────────────────────────────────────────
    def _exportar(self, fmt):
        if self.df_resultado is None:
            messagebox.showinfo("Sem dados", "Execute a análise antes de exportar.")
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        emp = self.empresa.get().replace(" ", "_")
        # Exporta respeitando os filtros atuais (fornecedor + busca)
        df = self._df_filtrados()

        RENAME = {
            "media": "media_mensal_qtd", "valor_mensal": "valor_mensal_venda",
            "desvio_padrao": "desvio_padrão",
            "classe": "classe_ABC_XYZ", "z": "fator_Z",
            "intermitente": "demanda_irregular", "estoq_seguranca": "estoq_segurança",
            "estoq_minimo": "estoq_minimo", "qnt_em_falta": "qtd_em_falta",
            "meses_sem_movimento": "meses_sem_ultima_venda",
            "sem_giro": "sem_giro_periodo",
            "Custo Liquido": "custo_liquido_ult_nf",
            "Preco de Venda": "preco_venda",
            "pedido_valor": "pedido_valor_rs",
            "capital_parado": "capital_parado_rs",
            "media_recente": "media_3m_recente",
            "tendencia_pct": "tendencia_3m_vs_12m_pct",
            "Ultima Entrada": "data_ultima_entrada_nf",
            "meses_desde_entrada": "meses_desde_ultima_entrada",
            "produto_novo": "produto_novo_sem_giro",
        }
        df_exp = df.rename(columns={k: v for k, v in RENAME.items() if k in df.columns})

        if fmt == "csv":
            path = filedialog.asksaveasfilename(
                title="Salvar CSV", defaultextension=".csv",
                filetypes=[("CSV", "*.csv")],
                initialfile=f"Analise_Estoque_{emp}_{ts}.csv")
            if path:
                df_exp.to_csv(path, index=False, encoding="utf-8-sig")
                messagebox.showinfo("Exportado", f"Arquivo salvo:\n{path}")
        else:
            path = filedialog.asksaveasfilename(
                title="Salvar Excel", defaultextension=".xlsx",
                filetypes=[("Excel", "*.xlsx")],
                initialfile=f"Analise_Estoque_{emp}_{ts}.xlsx")
            if not path:
                return
            try:
                with pd.ExcelWriter(path, engine="openpyxl") as w:
                    df_exp.to_excel(w, sheet_name="Análise Completa", index=False)
                    df_exp[df_exp["pedido"] > 0].to_excel(
                        w, sheet_name="Pedidos de Compra", index=False)
                    df_exp[df_exp["classe_ABC_XYZ"].str.startswith("A", na=False)].to_excel(
                        w, sheet_name="Curva A", index=False)
                    df_exp[df_exp["demanda_irregular"] == True].to_excel(
                        w, sheet_name="Demanda Irregular", index=False)
                    sem_giro = df_exp[df_exp["sem_giro_periodo"] == True]
                    if len(sem_giro) > 0:
                        sem_giro.to_excel(w, sheet_name="Sem Giro", index=False)
                messagebox.showinfo("Exportado", f"Arquivo salvo:\n{path}")
            except Exception as exc:
                messagebox.showerror("Erro ao exportar", str(exc))


# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    root = tk.Tk()
    app = AppIntegrado(root)
    root.mainloop()
