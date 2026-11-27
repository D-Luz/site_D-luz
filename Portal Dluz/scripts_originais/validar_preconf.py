#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Validação: NFENTRI.PRECONF é preço UNITÁRIO ou TOTAL da linha?
===============================================================
A análise de estoque (ats analise estoque_V5.py) calcula o custo da
última NF assumindo que PRECONF é o preço de UMA unidade:

    total_do_item = PRECONF × QUANTIDADE
    custo_unit    = (total_do_item + IPI + ICMS-ST + frete) / QUANTIDADE

Se PRECONF já for o total da linha, o custo unitário sai inflado
EXATAMENTE pelo fator da quantidade da nota (uma caixa de 100 unidades
mostraria custo 100× maior). Este script resolve a dúvida sem que você
precise conferir nota por nota.

Como decide — três provas independentes:

  A) TOTAL DA NF (definitiva, quando o banco tem a coluna de total):
     para cada nota, compara SOMA(PRECONF × QTD) e SOMA(PRECONF) com o
     valor total da própria NF. Só uma das duas bate.

  B) CUSTO DO ERP (sempre disponível): compara os dois cenários com o
     PRECOCUSTO que o ATS mantém em COMPPROD. Usa a MEDIANA das razões
     sobre muitos produtos, então preço desatualizado de um item não
     distorce o veredito.

  C) COERÊNCIA COM O PREÇO DE VENDA: conta quantos produtos ficariam com
     custo ACIMA do preço de venda em cada cenário. Um cenário que põe
     metade da base vendendo no prejuízo está errado.

IMPORTANTE: só linhas com QUANTIDADE > 1 entram nas provas — quando a
quantidade é 1 os dois cenários dão o mesmo número e não informam nada.

O script é SOMENTE LEITURA (apenas SELECT). Não altera o banco.

Uso:
    python validar_preconf.py

Gera também um Excel com a amostra, para conferência visual.

Dependências:
    pip install firebirdsql pandas openpyxl
"""

from datetime import datetime, date, timedelta

import firebirdsql
import pandas as pd
import numpy as np
import warnings

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

# ═══════════════════════════════════════════════════════════════
#  CONEXÕES — as mesmas do ats analise estoque_V5.py
# ═══════════════════════════════════════════════════════════════
EMPRESAS = {
    "Dluz Elétrica": {
        "host": "192.168.0.41",
        "database": r"D:\DLUZR\BANCO\DLUZR25.FB",
    },
    "Dluz Iluminação": {
        "host": "192.168.0.250",
        "database": r"D:\ILUMIR\BANCO\ILUMIR.FB",
    },
    "Cor e Cia": {
        "host": "192.168.0.200",
        "database": r"D:\CorCiaR\BANCO\CORCIAR25.FB",
    },
}
USER, PASSWORD, PORT, CHARSET = "SYSDBA", "masterkey", 3050, "WIN1252"

# Quantas linhas de NF analisar por empresa e quão para trás olhar
LIMITE_LINHAS = 4000
MESES_HISTORICO = 24

# Nomes prováveis da coluna de total da nota em NFENTRC, em ordem de
# preferência. O script confirma qual existe antes de usar.
CANDIDATOS_TOTAL_NF = [
    "VALORTOTALNF", "VLRTOTALNF", "TOTALNF", "VALORNF", "VLRNF",
    "VALORTOTAL", "VLRTOTAL", "TOTALNOTA", "VALORNOTA", "VLRLIQUIDO",
    "TOTALPRODUTOS", "VALORPRODUTOS", "VLRPRODUTOS",
]


# ───────────────────────────────────────────────────────────────
#  Infra
# ───────────────────────────────────────────────────────────────
def conectar(cfg):
    return firebirdsql.connect(
        host=cfg["host"], database=cfg["database"], port=PORT,
        user=USER, password=PASSWORD, charset=CHARSET,
    )


def ler(con, sql, params=None):
    cur = con.cursor()
    try:
        cur.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)
    finally:
        cur.close()


def colunas_da_tabela(con, tabela: str) -> list:
    """Lista os campos de uma tabela (introspecção do próprio Firebird)."""
    sql = """
        SELECT TRIM(rf.RDB$FIELD_NAME) AS CAMPO
        FROM RDB$RELATION_FIELDS rf
        WHERE rf.RDB$RELATION_NAME = ?
        ORDER BY rf.RDB$FIELD_POSITION
    """
    try:
        return ler(con, sql, [tabela])["CAMPO"].astype(str).str.strip().tolist()
    except Exception:
        return []


# ───────────────────────────────────────────────────────────────
#  Provas
# ───────────────────────────────────────────────────────────────
SQL_AMOSTRA = """
SELECT FIRST {limite}
    I.CODPROD                    AS "cod",
    TRIM(P.DESCRICAO)            AS "descricao",
    I.NUMERONF                   AS "nf",
    I.CODFORNEC                  AS "codfornec",
    I.CODEMPRESA                 AS "codempresa",
    C.DT_ENTRADA                 AS "data",
    I.QUANTIDADE                 AS "qtd",
    I.PRECONF                    AS "preconf",
    COALESCE(I.FRETE, 0)         AS "frete",
    COALESCE(I.VLRICMS, 0)       AS "icms",
    COALESCE(I.VALORSUBSTRIB, 0) AS "icms_st",
    COALESCE(I.VLRIPI, 0)        AS "ipi",
    COALESCE(EA.CUSTO_ERP, 0)    AS "custo_erp",
    COALESCE(P.PRECO, 0)         AS "preco_venda"
FROM NFENTRI I
JOIN NFENTRC C
    ON C.CODEMPRESA = I.CODEMPRESA
   AND C.NUMERONF   = I.NUMERONF
   AND C.CODFORNEC  = I.CODFORNEC
JOIN PRODUTO P ON P.CODPROD = I.CODPROD
LEFT JOIN (
    SELECT CODPROD, MAX(PRECOCUSTO) AS CUSTO_ERP
    FROM COMPPROD GROUP BY CODPROD
) EA ON EA.CODPROD = I.CODPROD
WHERE I.QUANTIDADE > 1
  AND I.PRECONF > 0
  AND C.DT_ENTRADA >= ?
ORDER BY C.DT_ENTRADA DESC
"""


def calcular_cenarios(df: pd.DataFrame) -> pd.DataFrame:
    """Custo unitário sob as duas hipóteses, linha a linha."""
    d = df.copy()
    for c in ("qtd", "preconf", "frete", "icms", "icms_st", "ipi",
              "custo_erp", "preco_venda"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0)
    acrescimos = d["ipi"] + d["icms_st"] + d["frete"]

    # Hipótese A (a que o programa usa hoje): PRECONF é unitário
    d["custo_unit_A"] = ((d["preconf"] * d["qtd"] + acrescimos) / d["qtd"]).round(4)
    # Hipótese B: PRECONF já é o total da linha
    d["custo_unit_B"] = ((d["preconf"] + acrescimos) / d["qtd"]).round(4)

    com_ref = d["custo_erp"] > 0
    d["razao_A"] = np.where(com_ref, d["custo_unit_A"] / d["custo_erp"], np.nan)
    d["razao_B"] = np.where(com_ref, d["custo_unit_B"] / d["custo_erp"], np.nan)
    return d


def prova_total_nf(con, df: pd.DataFrame, col_total: str) -> dict:
    """Prova definitiva: qual soma bate com o total declarado na NF."""
    if not col_total or df.empty:
        return {}
    # Pega até 40 notas da amostra e recalcula somando TODOS os itens
    chaves = (df[["codempresa", "nf", "codfornec"]]
              .drop_duplicates().head(40))
    linhas = []
    for _, k in chaves.iterrows():
        # col_total vem da lista fixa CANDIDATOS_TOTAL_NF e foi conferida
        # contra o catálogo do banco — seguro interpolar
        sql = f"""
            SELECT
                SUM(I.PRECONF * I.QUANTIDADE) AS "soma_unit",
                SUM(I.PRECONF)                AS "soma_total",
                MAX(C.{col_total})            AS "total_nf"
            FROM NFENTRI I
            JOIN NFENTRC C
                ON C.CODEMPRESA = I.CODEMPRESA
               AND C.NUMERONF   = I.NUMERONF
               AND C.CODFORNEC  = I.CODFORNEC
            WHERE I.CODEMPRESA = ? AND I.NUMERONF = ? AND I.CODFORNEC = ?
        """
        try:
            r = ler(con, sql, [k["codempresa"], k["nf"], k["codfornec"]])
        except Exception:
            continue
        if r.empty:
            continue
        linhas.append(r.iloc[0])
    if not linhas:
        return {}

    t = pd.DataFrame(linhas)
    for c in ("soma_unit", "soma_total", "total_nf"):
        t[c] = pd.to_numeric(t[c], errors="coerce")
    t = t[t["total_nf"] > 0].dropna()
    if t.empty:
        return {}

    # Erro relativo de cada hipótese contra o total declarado
    err_A = ((t["soma_unit"] - t["total_nf"]).abs() / t["total_nf"])
    err_B = ((t["soma_total"] - t["total_nf"]).abs() / t["total_nf"])
    return {
        "notas": len(t),
        "erro_medio_A": float(err_A.median()),
        "erro_medio_B": float(err_B.median()),
        "acertos_A": int((err_A < 0.02).sum()),   # dentro de 2%
        "acertos_B": int((err_B < 0.02).sum()),
    }


def analisar_empresa(nome: str, cfg: dict):
    print(f"\n{'='*66}\n{nome}  ({cfg['host']})\n{'='*66}")
    try:
        con = conectar(cfg)
    except Exception as ex:
        print(f"  ✗ Não conectou: {ex}")
        return None
    try:
        # ── Introspecção: o que existe em NFENTRC/NFENTRI ──
        cols_c = colunas_da_tabela(con, "NFENTRC")
        col_total = next((c for c in CANDIDATOS_TOTAL_NF if c in cols_c), None)
        if col_total:
            print(f"  Coluna de total da NF encontrada: NFENTRC.{col_total}")
        else:
            candidatas = [c for c in cols_c
                          if any(p in c for p in ("TOTAL", "VALOR", "VLR", "LIQ"))]
            print("  Nenhuma coluna de total conhecida em NFENTRC.")
            if candidatas:
                print("  Candidatas encontradas (avalie e some em "
                      f"CANDIDATOS_TOTAL_NF): {', '.join(candidatas[:15])}")

        # ── Amostra de itens de NF ──
        desde = (date.today() - timedelta(days=30 * MESES_HISTORICO)).strftime("%Y-%m-%d")
        df = ler(con, SQL_AMOSTRA.format(limite=LIMITE_LINHAS), [desde])
        if df.empty:
            print("  ✗ Nenhum item de NF com quantidade > 1 no período.")
            return None
        d = calcular_cenarios(df)
        print(f"  Amostra: {len(d)} itens de NF (quantidade > 1), "
              f"últimos {MESES_HISTORICO} meses")
        print(f"  Quantidade mediana por linha: {d['qtd'].median():.0f}")

        # ── PROVA A: total da NF ──
        pa = prova_total_nf(con, df, col_total) if col_total else {}
        if pa:
            print(f"\n  [A] Total da NF — {pa['notas']} notas conferidas")
            print(f"      Hipótese A (unitário): erro mediano "
                  f"{pa['erro_medio_A']*100:6.1f}%  ·  {pa['acertos_A']}/{pa['notas']} batem")
            print(f"      Hipótese B (total)   : erro mediano "
                  f"{pa['erro_medio_B']*100:6.1f}%  ·  {pa['acertos_B']}/{pa['notas']} batem")
        else:
            print("\n  [A] Total da NF — não foi possível (sem coluna de total)")

        # ── PROVA B: custo do ERP ──
        com_ref = d["razao_A"].notna()
        pb = {}
        if com_ref.sum() >= 10:
            mA, mB = d.loc[com_ref, "razao_A"].median(), d.loc[com_ref, "razao_B"].median()
            perto_A = int((abs(d.loc[com_ref, "razao_A"] - 1) <
                           abs(d.loc[com_ref, "razao_B"] - 1)).sum())
            pb = {"n": int(com_ref.sum()), "mediana_A": float(mA),
                  "mediana_B": float(mB), "perto_A": perto_A}
            print(f"\n  [B] Custo do ERP (PRECOCUSTO) — {pb['n']} itens com referência")
            print(f"      Hipótese A (unitário): custo calculado / custo ERP = {mA:8.2f}×")
            print(f"      Hipótese B (total)   : custo calculado / custo ERP = {mB:8.2f}×")
            print(f"      Itens onde A fica mais perto do ERP: "
                  f"{perto_A}/{pb['n']} ({perto_A/pb['n']*100:.0f}%)")
        else:
            print("\n  [B] Custo do ERP — poucos itens com PRECOCUSTO preenchido")

        # ── PROVA C: custo acima do preço de venda ──
        com_venda = d["preco_venda"] > 0
        pc = {}
        if com_venda.sum() >= 10:
            acima_A = int((d.loc[com_venda, "custo_unit_A"] >
                           d.loc[com_venda, "preco_venda"]).sum())
            acima_B = int((d.loc[com_venda, "custo_unit_B"] >
                           d.loc[com_venda, "preco_venda"]).sum())
            n = int(com_venda.sum())
            pc = {"n": n, "acima_A": acima_A, "acima_B": acima_B}
            print(f"\n  [C] Custo acima do preço de venda — {n} itens com preço")
            print(f"      Hipótese A (unitário): {acima_A:5d} itens "
                  f"({acima_A/n*100:5.1f}%) venderiam no prejuízo")
            print(f"      Hipótese B (total)   : {acima_B:5d} itens "
                  f"({acima_B/n*100:5.1f}%) venderiam no prejuízo")

        # ── PROVA D: margem implícita ──
        # Sob a hipótese certa, preço de venda ÷ custo cai na faixa de um
        # distribuidor (~1,3× a 3×). A hipótese errada estoura a margem.
        pd_ = {}
        if com_venda.sum() >= 10:
            g = d.loc[com_venda]
            margem_A = float((g["preco_venda"] /
                              g["custo_unit_A"].replace(0, np.nan)).median())
            margem_B = float((g["preco_venda"] /
                              g["custo_unit_B"].replace(0, np.nan)).median())
            pd_ = {"margem_A": margem_A, "margem_B": margem_B}
            print(f"\n  [D] Margem implícita (preço de venda ÷ custo)")
            print(f"      Hipótese A (unitário): {margem_A:6.2f}×")
            print(f"      Hipótese B (total)   : {margem_B:6.2f}×")
            print("      (esperado para distribuidor: entre 1,3× e 3×)")

        # ── VEREDITO ──
        # A prova [A] é ESTRUTURAL: compara com o total declarado na
        # própria nota. Quando ela é categórica, decide sozinha — as
        # outras são heurísticas e dependem de o ERP estar bem preenchido.
        decisiva = bool(pa) and abs(pa["erro_medio_A"] - pa["erro_medio_B"]) > 0.50
        votos_A = votos_B = 0
        if pa:
            if pa["erro_medio_A"] < pa["erro_medio_B"]: votos_A += 2
            else: votos_B += 2
        if pb:
            if abs(pb["mediana_A"] - 1) < abs(pb["mediana_B"] - 1): votos_A += 1
            else: votos_B += 1
        if pd_:
            plaus = lambda m: 1.15 <= m <= 3.5
            if plaus(pd_["margem_A"]) and not plaus(pd_["margem_B"]): votos_A += 1
            elif plaus(pd_["margem_B"]) and not plaus(pd_["margem_A"]): votos_B += 1
        if pc:
            # só vota se a diferença for material — 1% contra 1,4% é ruído
            n = pc["n"]
            dif = abs(pc["acima_A"] - pc["acima_B"]) / n
            if dif >= 0.05:
                if pc["acima_A"] < pc["acima_B"]: votos_A += 1
                else: votos_B += 1

        print(f"\n  {'─'*62}")
        if decisiva or votos_A != votos_B:
            unitario = (pa["erro_medio_A"] < pa["erro_medio_B"]) if decisiva \
                       else (votos_A > votos_B)
            if unitario:
                print("  ✔ VEREDITO: PRECONF é PREÇO UNITÁRIO — a premissa do "
                      "programa está CORRETA.")
                print("    Nada a mudar em ats analise estoque_V5.py.")
            else:
                print("  ✗ VEREDITO: PRECONF é o TOTAL DA LINHA — a premissa "
                      "do programa está ERRADA.")
                print("    Correção: em calcular_custo_liquido(), trocar")
                print('      df["_total_item"] = df["Preco_Entrada"] * df["Quantidade"]')
                print("    por")
                print('      df["_total_item"] = df["Preco_Entrada"]')
            if decisiva:
                print("    (decidido pela prova [A], estrutural: só uma das "
                      "hipóteses fecha com o total da nota)")
        else:
            print("  ? INCONCLUSIVO — as provas não convergiram.")
            print("    Confira manualmente uma NF na planilha gerada.")
        print(f"  (votos: unitário {votos_A} × {votos_B} total)")

        # ── Alerta separado: qualidade do PRECOCUSTO do ERP ──
        # Divergência aqui NÃO é problema do PRECONF — é o campo de custo
        # do ERP que está defasado ou em outra base. Importa porque o
        # analisador usa PRECOCUSTO como fallback de quem não tem NF.
        if pb and (pb["mediana_A"] > 1.4 or pb["mediana_A"] < 0.6):
            print(f"\n  ⚠ ATENÇÃO — PRECOCUSTO (COMPPROD) desta empresa diverge "
                  f"{pb['mediana_A']:.2f}× do custo real da NF.")
            cobertura = float((d["custo_erp"] > 0).mean())
            print(f"    Preenchido em apenas {cobertura*100:.0f}% dos itens "
                  "da amostra.")
            print("    Isso afeta o analisador: produtos SEM NF de compra caem "
                  "nesse campo\n    como fallback de custo — e herdam a distorção.")

        d["empresa"] = nome
        return d
    finally:
        con.close()


def main():
    print("Validação de NFENTRI.PRECONF — unitário ou total da linha?")
    print("(somente leitura: o script não altera nada no banco)")

    amostras, falhas = [], []
    for nome, cfg in EMPRESAS.items():
        try:
            d = analisar_empresa(nome, cfg)
            if d is not None:
                amostras.append(d)
        except Exception as ex:
            falhas.append(f"{nome}: {ex}")
            print(f"  ✗ Erro: {ex}")

    if not amostras:
        print("\nNenhuma empresa retornou dados. Verifique a rede/conexões.")
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    saida = f"Validacao_PRECONF_{ts}.xlsx"
    cols = ["empresa", "cod", "descricao", "nf", "data", "qtd", "preconf",
            "frete", "ipi", "icms_st", "custo_unit_A", "custo_unit_B",
            "custo_erp", "razao_A", "razao_B", "preco_venda"]
    todas = pd.concat(amostras, ignore_index=True)
    with pd.ExcelWriter(saida, engine="openpyxl") as w:
        todas[[c for c in cols if c in todas.columns]].to_excel(
            w, sheet_name="Amostra", index=False)
    print(f"\nPlanilha para conferência visual: {saida}")
    print("  → custo_unit_A = hipótese atual (PRECONF unitário)")
    print("  → custo_unit_B = hipótese alternativa (PRECONF total da linha)")
    print("  Escolha uma linha, abra a NF no ATS e veja qual coluna bate.")

    if falhas:
        print("\nFalhas:")
        for f in falhas:
            print(f"  - {f}")


if __name__ == "__main__":
    main()
