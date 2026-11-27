#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ATS — Posição de Estoque
=========================
Seleção única de empresa · Data atual ou histórico estimado (PDV).

Banco Real/Fiscal → estoque e custo via COMPPROD
                    (SUM(ESTOQUE) e MAX(PRECOCUSTO) por CODPROD)

Histórico estimado: parte do estoque atual e soma as saídas PDV
(TIPOPEDIDO=55) ocorridas APÓS a data-alvo.

Visual: Grupo Dluz Design System — base creme quente, grafite profundo,
acento único dourado/âmbar; valores monetários em mono tabular;
saldo devedor sempre em vermelho.

Dependências:
    pip install firebirdsql pandas openpyxl
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.font as tkfont
from datetime import date, datetime
from typing import Optional
import threading

import firebirdsql
import pandas as pd


# ═══════════════════════════════════════════════════════════════
#  CONFIGURAÇÃO DAS EMPRESAS
# ═══════════════════════════════════════════════════════════════
EMPRESAS = {
    "Dluz Elétrica": {
        "host":   "192.168.0.41",
        "real":   r"D:\DLUZR\BANCO\DLUZR25.FB",
        "fiscal": r"D:\DLUZF\BANCO\DLUZF25.FB",
    },
    "Cor e Cia": {
        "host":   "192.168.0.200",
        "real":   r"D:\CorCiaR\BANCO\CORCIAR25.FB",
        "fiscal": r"D:\CorCiaF\BANCO\CORCIAF25.FB",
    },
    "Dluz Iluminação": {
        "host":   "192.168.0.250",
        "real":   r"D:\ILUMIR\BANCO\ILUMIR.FB",
        "fiscal": r"D:\ILUMIF\BANCO\ILUMIF.FB",
    },
}

PORT     = 3050
USER     = "SYSDBA"
PASSWORD = "masterkey"
CHARSET  = "WIN1252"


# ═══════════════════════════════════════════════════════════════
#  SQL — BANCO REAL  (estoque e custo via COMPPROD)
# ═══════════════════════════════════════════════════════════════
SQL_REAL_ATUAL = """
SELECT
    p.CODPROD                                                     AS "Cód. Produto",
    TRIM(p.DESCRICAO)                                             AS "Descrição",
    COALESCE(cp.ESTOQUE, 0)                                       AS "Estoque",
    COALESCE(cp.PRECOCUSTO, 0)                                    AS "Preço de Custo",
    COALESCE(p.PRECO, 0)                                          AS "Preço de Venda",
    COALESCE(cp.ESTOQUE, 0) * COALESCE(cp.PRECOCUSTO, 0)         AS "Total Item",
    TRIM(f.NOME)                                                  AS "Fornecedor"
FROM PRODUTO p
LEFT JOIN (
    SELECT
        CODPROD,
        SUM(ESTOQUE)    AS ESTOQUE,
        MAX(PRECOCUSTO) AS PRECOCUSTO
    FROM COMPPROD
    GROUP BY CODPROD
) cp ON cp.CODPROD = p.CODPROD
LEFT JOIN FORNECE f ON f.CODFORNEC = p.PRINCIPALFORNEC
WHERE p.ATIVO = 'S'
ORDER BY p.DESCRICAO
"""

SQL_REAL_HISTORICO = """
SELECT
    p.CODPROD                                                           AS "Cód. Produto",
    TRIM(p.DESCRICAO)                                                   AS "Descrição",
    COALESCE(cp.ESTOQUE, 0) + COALESCE(saidas.QTD, 0)                  AS "Estoque",
    COALESCE(cp.PRECOCUSTO, 0)                                         AS "Preço de Custo",
    COALESCE(p.PRECO, 0)                                                AS "Preço de Venda",
    (COALESCE(cp.ESTOQUE, 0) + COALESCE(saidas.QTD, 0))
        * COALESCE(cp.PRECOCUSTO, 0)                                   AS "Total Item",
    TRIM(f.NOME)                                                        AS "Fornecedor"
FROM PRODUTO p
LEFT JOIN (
    SELECT
        CODPROD,
        SUM(ESTOQUE)    AS ESTOQUE,
        MAX(PRECOCUSTO) AS PRECOCUSTO
    FROM COMPPROD
    GROUP BY CODPROD
) cp ON cp.CODPROD = p.CODPROD
LEFT JOIN (
    SELECT i.CODPROD, SUM(i.QUANTIDADE) AS QTD
    FROM PEDIDOI i
    JOIN PEDIDOC c ON c.CODEMPRESA = i.CODEMPRESA
                  AND c.TIPOPEDIDO = i.TIPOPEDIDO
                  AND c.CODPEDIDO  = i.CODPEDIDO
    WHERE c.TIPOPEDIDO = 55
      AND i.CANCELADO  = 'N'
      AND CAST(c.DATAPEDIDO AS DATE) > ?
    GROUP BY i.CODPROD
) saidas ON saidas.CODPROD = p.CODPROD
LEFT JOIN FORNECE f ON f.CODFORNEC = p.PRINCIPALFORNEC
WHERE p.ATIVO = 'S'
ORDER BY p.DESCRICAO
"""


# ═══════════════════════════════════════════════════════════════
#  SQL — BANCO FISCAL  (estoque e custo via COMPPROD)
# ═══════════════════════════════════════════════════════════════
SQL_FISCAL_ATUAL = """
SELECT
    p.CODPROD                                                     AS "Cód. Produto",
    TRIM(p.DESCRICAO)                                             AS "Descrição",
    COALESCE(cp.ESTOQUE, 0)                                       AS "Estoque",
    COALESCE(cp.PRECOCUSTO, 0)                                    AS "Preço de Custo",
    COALESCE(p.PRECO, 0)                                          AS "Preço de Venda",
    COALESCE(cp.ESTOQUE, 0) * COALESCE(cp.PRECOCUSTO, 0)         AS "Total Item",
    TRIM(f.NOME)                                                  AS "Fornecedor"
FROM PRODUTO p
LEFT JOIN (
    SELECT
        CODPROD,
        SUM(ESTOQUE)    AS ESTOQUE,
        MAX(PRECOCUSTO) AS PRECOCUSTO
    FROM COMPPROD
    GROUP BY CODPROD
) cp ON cp.CODPROD = p.CODPROD
LEFT JOIN FORNECE f ON f.CODFORNEC = p.PRINCIPALFORNEC
WHERE p.ATIVO = 'S'
ORDER BY p.DESCRICAO
"""

SQL_FISCAL_HISTORICO = """
SELECT
    p.CODPROD                                                           AS "Cód. Produto",
    TRIM(p.DESCRICAO)                                                   AS "Descrição",
    COALESCE(cp.ESTOQUE, 0) + COALESCE(saidas.QTD, 0)                  AS "Estoque",
    COALESCE(cp.PRECOCUSTO, 0)                                         AS "Preço de Custo",
    COALESCE(p.PRECO, 0)                                                AS "Preço de Venda",
    (COALESCE(cp.ESTOQUE, 0) + COALESCE(saidas.QTD, 0))
        * COALESCE(cp.PRECOCUSTO, 0)                                   AS "Total Item",
    TRIM(f.NOME)                                                        AS "Fornecedor"
FROM PRODUTO p
LEFT JOIN (
    SELECT
        CODPROD,
        SUM(ESTOQUE)    AS ESTOQUE,
        MAX(PRECOCUSTO) AS PRECOCUSTO
    FROM COMPPROD
    GROUP BY CODPROD
) cp ON cp.CODPROD = p.CODPROD
LEFT JOIN (
    SELECT i.CODPROD, SUM(i.QUANTIDADE) AS QTD
    FROM PEDIDOI i
    JOIN PEDIDOC c ON c.CODEMPRESA = i.CODEMPRESA
                  AND c.TIPOPEDIDO = i.TIPOPEDIDO
                  AND c.CODPEDIDO  = i.CODPEDIDO
    WHERE c.TIPOPEDIDO = 55
      AND i.CANCELADO  = 'N'
      AND CAST(c.DATAPEDIDO AS DATE) > ?
    GROUP BY i.CODPROD
) saidas ON saidas.CODPROD = p.CODPROD
LEFT JOIN FORNECE f ON f.CODFORNEC = p.PRINCIPALFORNEC
WHERE p.ATIVO = 'S'
ORDER BY p.DESCRICAO
"""

# Mapa rápido: (tipo_banco, historico) → SQL
SQL_MAP = {
    ("real",   False): SQL_REAL_ATUAL,
    ("real",   True):  SQL_REAL_HISTORICO,
    ("fiscal", False): SQL_FISCAL_ATUAL,
    ("fiscal", True):  SQL_FISCAL_HISTORICO,
}


# ═══════════════════════════════════════════════════════════════
#  EXTRAÇÃO
# ═══════════════════════════════════════════════════════════════
def _read_firebird(con, sql: str, params=None) -> pd.DataFrame:
    cur = con.cursor()
    try:
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        colunas = [desc[0] for desc in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=colunas)
    finally:
        cur.close()


def extrair_estoque(empresa: str, tipo_banco: str,
                    data_ref: Optional[date]) -> pd.DataFrame:
    cfg      = EMPRESAS[empresa]
    historico = data_ref is not None
    sql       = SQL_MAP[(tipo_banco, historico)]
    con = firebirdsql.connect(
        host=cfg["host"], database=cfg[tipo_banco],
        port=PORT, user=USER, password=PASSWORD, charset=CHARSET,
    )
    try:
        params = (data_ref.strftime("%Y-%m-%d"),) if historico else None
        df     = _read_firebird(con, sql, params=params)
    finally:
        con.close()
    df.insert(0, "Empresa", empresa)
    df.insert(1, "Banco",   "Real" if tipo_banco == "real" else "Fiscal")
    return df


# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════
def _brl(v) -> str:
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"

def _float_safe(v) -> float:
    try:    return float(v)
    except: return 0.0

def _sort_key(v):
    s = str(v).replace("R$", "").replace(".", "").replace(",", ".").strip()
    try:    return (0, float(s))
    except: return (1, str(v).lower())


def _fam(candidatas, padrao) -> str:
    """Primeira família de fonte instalada dentre as candidatas."""
    disponiveis = set(tkfont.families())
    for nome in candidatas:
        if nome in disponiveis:
            return nome
    return padrao


# ═══════════════════════════════════════════════════════════════
#  INTERFACE — Grupo Dluz Design System
# ═══════════════════════════════════════════════════════════════
class AppEstoque:

    # ── Tokens de cor (Grupo Dluz) ─────────────────────────────
    COR_BG      = "#F6F0E6"   # surface-page — creme quente
    COR_CARD    = "#FFFDF8"   # surface-card — off-white quente
    COR_SUNKEN  = "#EFE7D8"   # surface-sunken — linhas zebradas
    COR_BORDA   = "#E6DBC8"   # border-subtle — filete arenito
    COR_TEXTO   = "#2B2926"   # text-primary — grafite profundo
    COR_TITULO  = "#565049"   # graphite-600 — cabeçalhos sobre creme
    COR_SUB     = "#6F665B"   # text-secondary
    COR_MUTED   = "#8E867A"   # text-muted
    COR_ACCENT  = "#BE8E2E"   # amber-500 — o único acento
    COR_ACC_HOV = "#A2761F"   # amber-600 — hover
    COR_ACC_PRS = "#876214"   # amber-700 — pressed / texto acento
    COR_SEL     = "#D3A954"   # amber-400 — seleção / foco
    COR_OK      = "#3A6E47"   # credit — verde sóbrio
    COR_ERRO    = "#A52A1F"   # debit — devedor sempre vermelho
    COR_AVISO   = "#A2761F"   # warning — âmbar
    COR_COM     = "#FFFDF8"   # linha com estoque — papel neutro
    COR_SEM     = "#F4DBD5"   # linha sem estoque — tinta debit

    COLUNAS = ("Empresa", "Banco", "Cód. Produto", "Descrição", "Estoque",
               "Preço de Custo", "Preço de Venda", "Total Item", "Fornecedor")
    COL_W   = {
        "Empresa": 115, "Banco": 58, "Cód. Produto": 72, "Descrição": 225,
        "Estoque": 78, "Preço de Custo": 105, "Preço de Venda": 105,
        "Total Item": 120, "Fornecedor": 185,
    }

    def __init__(self, root: tk.Tk):
        self.root        = root
        self.root.title("ATS — Posição de Estoque")
        self.root.geometry("1240x760")
        self.root.minsize(1000, 580)
        self.root.configure(bg=self.COR_BG)

        # ── Famílias tipográficas (serif editorial · sans neutra · mono tabular)
        self.F_SERIF = _fam(["Newsreader", "Georgia", "Times New Roman"], "Georgia")
        self.F_SANS  = _fam(["IBM Plex Sans", "Segoe UI", "Helvetica Neue"], "Helvetica")
        self.F_MONO  = _fam(["IBM Plex Mono", "Consolas", "Menlo"], "Courier")

        # ── Variáveis de controle
        self.empresa_sel = tk.StringVar(value=list(EMPRESAS.keys())[0])
        self.tipo_banco  = tk.StringVar(value="real")
        self.modo_data   = tk.StringVar(value="hoje")
        self.data_str    = tk.StringVar(value=date.today().strftime("%d/%m/%Y"))
        self.filtro_forn = tk.StringVar(value="Todos")
        self.busca_desc  = tk.StringVar(value="")
        self.filtro_est  = tk.StringVar(value="Todos")

        self.df_full:   Optional[pd.DataFrame] = None
        self._hist      = False
        self._sort_dir: dict = {}

        self._aplicar_estilo()
        self._construir_ui()
        self._centralizar()

    # ───────────────────────────────────────────────────────────
    def _aplicar_estilo(self):
        st = ttk.Style(self.root)
        try: st.theme_use("clam")
        except Exception: pass

        sans = self.F_SANS

        for cls in ("TFrame", "TLabel", "TLabelframe",
                    "TCheckbutton", "TRadiobutton", "TNotebook"):
            st.configure(cls, background=self.COR_BG)

        st.configure("TLabel", foreground=self.COR_TEXTO, font=(sans, 10))
        st.configure("TLabelframe", bordercolor=self.COR_BORDA,
                     lightcolor=self.COR_BORDA, darkcolor=self.COR_BORDA)
        st.configure("TLabelframe.Label", background=self.COR_BG,
                     font=(sans, 9, "bold"), foreground=self.COR_TITULO)
        st.configure("TSeparator", background=self.COR_BORDA)
        st.configure("TNotebook.Tab", padding=[10, 4])

        # Radio / check — indicador no acento âmbar
        st.configure("TRadiobutton", foreground=self.COR_TEXTO, font=(sans, 10),
                     indicatorcolor=self.COR_CARD)
        st.map("TRadiobutton",
               background=[("active", self.COR_BG)],
               indicatorcolor=[("selected", self.COR_ACCENT)])
        st.map("TCheckbutton",
               background=[("active", self.COR_BG)],
               indicatorcolor=[("selected", self.COR_ACCENT)])

        # Botões — secundário: papel com filete; primário: âmbar
        st.configure("TButton", font=(sans, 10),
                     background=self.COR_CARD, foreground=self.COR_TEXTO,
                     bordercolor=self.COR_BORDA, lightcolor=self.COR_CARD,
                     darkcolor=self.COR_CARD, focuscolor=self.COR_SEL,
                     padding=[12, 6])
        st.map("TButton",
               background=[("pressed", self.COR_SUNKEN), ("active", "#FBF7F0")],
               bordercolor=[("active", "#CDBCA1")])

        st.configure("Accent.TButton", font=(sans, 10, "bold"),
                     background=self.COR_ACCENT, foreground="#FFFDF8",
                     bordercolor=self.COR_ACC_HOV, lightcolor=self.COR_ACCENT,
                     darkcolor=self.COR_ACCENT, focuscolor=self.COR_SEL,
                     padding=[14, 6])
        st.map("Accent.TButton",
               background=[("disabled", "#CDBCA1"),
                           ("pressed", self.COR_ACC_PRS),
                           ("active", self.COR_ACC_HOV)],
               foreground=[("disabled", "#FFFDF8")])

        # Campos de entrada — papel, filete sutil, foco âmbar
        st.configure("TEntry", fieldbackground=self.COR_CARD,
                     foreground=self.COR_TEXTO, bordercolor=self.COR_BORDA,
                     lightcolor=self.COR_CARD, darkcolor=self.COR_CARD,
                     insertcolor=self.COR_TEXTO)
        st.map("TEntry",
               bordercolor=[("focus", self.COR_SEL)],
               fieldbackground=[("disabled", self.COR_SUNKEN)])
        st.configure("TCombobox", fieldbackground=self.COR_CARD,
                     background=self.COR_CARD, foreground=self.COR_TEXTO,
                     bordercolor=self.COR_BORDA, arrowcolor=self.COR_SUB,
                     lightcolor=self.COR_CARD, darkcolor=self.COR_CARD)
        st.map("TCombobox",
               bordercolor=[("focus", self.COR_SEL)],
               fieldbackground=[("disabled", self.COR_SUNKEN),
                                ("readonly", self.COR_CARD)])

        # Barra de progresso — âmbar sobre creme rebaixado
        st.configure("TProgressbar", background=self.COR_ACCENT,
                     troughcolor=self.COR_SUNKEN, bordercolor=self.COR_BORDA,
                     lightcolor=self.COR_ACCENT, darkcolor=self.COR_ACCENT)

        # Tabela — papel, cabeçalho rebaixado, seleção âmbar
        st.configure("Treeview", rowheight=26, font=(sans, 9),
                     background=self.COR_CARD, fieldbackground=self.COR_CARD,
                     foreground=self.COR_TEXTO, bordercolor=self.COR_BORDA,
                     lightcolor=self.COR_CARD, darkcolor=self.COR_CARD)
        st.configure("Treeview.Heading", font=(sans, 9, "bold"),
                     background=self.COR_SUNKEN, foreground=self.COR_TITULO,
                     bordercolor=self.COR_BORDA, relief="flat", padding=[4, 5])
        st.map("Treeview.Heading",
               background=[("active", "#E6DBC8")])
        st.map("Treeview",
               background=[("selected", self.COR_SEL)],
               foreground=[("selected", "#1E1C19")])

        st.configure("Vertical.TScrollbar", background=self.COR_SUNKEN,
                     troughcolor=self.COR_BG, bordercolor=self.COR_BORDA,
                     arrowcolor=self.COR_SUB)
        st.configure("Horizontal.TScrollbar", background=self.COR_SUNKEN,
                     troughcolor=self.COR_BG, bordercolor=self.COR_BORDA,
                     arrowcolor=self.COR_SUB)

    # ───────────────────────────────────────────────────────────
    def _construir_ui(self):
        main = ttk.Frame(self.root, padding=16)
        main.pack(fill=tk.BOTH, expand=True)

        # Título editorial em serifa; eyebrow em caixa alta acima
        ttk.Label(main, text="GRUPO DLUZ · ATS",
                  font=(self.F_SANS, 8, "bold"),
                  foreground=self.COR_MUTED).pack(anchor="w")
        ttk.Label(main, text="Posição de Estoque",
                  font=(self.F_SERIF, 20, "bold"),
                  foreground=self.COR_TEXTO).pack(anchor="w", pady=(2, 0))
        ttk.Label(main,
                  text="Seleção única de empresa · Data atual ou histórico estimado (PDV)",
                  foreground=self.COR_SUB).pack(anchor="w", pady=(2, 8))
        ttk.Separator(main).pack(fill="x", pady=(0, 10))

        # ── Painel de configurações
        cfg_frame = ttk.Frame(main)
        cfg_frame.pack(fill="x", pady=(0, 8))
        cfg_frame.columnconfigure(1, weight=1)

        # Col 0 — Empresa (radio buttons — seleção única)
        grp_emp = ttk.LabelFrame(cfg_frame, text="  Empresa  ", padding=(12, 8))
        grp_emp.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        for nome in EMPRESAS:
            ttk.Radiobutton(
                grp_emp, text=nome,
                variable=self.empresa_sel, value=nome,
            ).pack(anchor="w", pady=3)

        # Col 1 — Banco + Data
        grp_op = ttk.LabelFrame(cfg_frame, text="  Opções  ", padding=(12, 8))
        grp_op.grid(row=0, column=1, sticky="nsew")
        grp_op.columnconfigure(4, weight=1)

        # Banco
        ttk.Label(grp_op, text="Banco:",
                  font=(self.F_SANS, 9, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Radiobutton(grp_op, text="Real",
                        variable=self.tipo_banco, value="real").grid(
                            row=0, column=1, padx=(10, 6), sticky="w")
        ttk.Radiobutton(grp_op, text="Fiscal",
                        variable=self.tipo_banco, value="fiscal").grid(
                            row=0, column=2, sticky="w")
        ttk.Label(grp_op,
                  text="  Real/Fiscal → COMPPROD (estoque + custo)",
                  foreground=self.COR_MUTED,
                  font=(self.F_SANS, 8)).grid(row=0, column=3, columnspan=2,
                                              sticky="w", padx=(18, 0))

        ttk.Separator(grp_op, orient="horizontal").grid(
            row=1, column=0, columnspan=5, sticky="ew", pady=8)

        # Data
        ttk.Label(grp_op, text="Data:",
                  font=(self.F_SANS, 9, "bold")).grid(row=2, column=0, sticky="w")
        ttk.Radiobutton(grp_op, text="Hoje",
                        variable=self.modo_data, value="hoje",
                        command=self._toggle_data).grid(
                            row=2, column=1, padx=(10, 6), sticky="w")
        ttk.Radiobutton(grp_op, text="Data específica",
                        variable=self.modo_data, value="especifica",
                        command=self._toggle_data).grid(
                            row=2, column=2, sticky="w")
        self.ent_data = ttk.Entry(grp_op, textvariable=self.data_str,
                                  width=12, font=(self.F_MONO, 10),
                                  state="disabled")
        self.ent_data.grid(row=2, column=3, padx=(12, 4))
        ttk.Label(grp_op, text="(DD/MM/AAAA)",
                  foreground=self.COR_MUTED,
                  font=(self.F_SANS, 8)).grid(row=2, column=4, sticky="w")

        # Aviso histórico (oculto inicialmente)
        self.lbl_aviso = ttk.Label(
            grp_op,
            text="Atenção — histórico estimado: soma as saídas PDV (TIPOPEDIDO=55) "
                 "ocorridas APÓS a data-alvo ao estoque atual. "
                 "Entradas/ajustes pós-data NÃO são descontados.",
            foreground=self.COR_AVISO, font=(self.F_SANS, 8))
        self.lbl_aviso.grid(row=3, column=0, columnspan=5, sticky="w", pady=(6, 0))
        self.lbl_aviso.grid_remove()

        # ── Ação
        action = ttk.Frame(main)
        action.pack(fill="x", pady=(0, 8))
        self.btn_gerar = ttk.Button(action, text="Gerar relatório",
                                    style="Accent.TButton",
                                    command=self._iniciar, width=20)
        self.btn_gerar.pack(side="left")
        self.progress = ttk.Progressbar(action, mode="indeterminate", length=180)
        self.progress.pack(side="left", padx=10)
        self.lbl_status = ttk.Label(action,
                                    text="Selecione a empresa e clique em Gerar.",
                                    foreground=self.COR_SUB)
        self.lbl_status.pack(side="left")

        ttk.Separator(main).pack(fill="x", pady=(0, 8))

        # ── Cards de resumo
        self.frm_cards = ttk.Frame(main)
        self.frm_cards.pack(fill="x", pady=(0, 8))
        self._render_cards([
            ("Produtos",    "—", self.COR_TEXTO),
            ("Com estoque", "—", self.COR_OK),
            ("Sem estoque", "—", self.COR_ERRO),
            ("Total (R$)",  "—", self.COR_TEXTO),
        ])

        # ── Barra de filtros
        bar = ttk.Frame(main)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Fornecedor:").pack(side="left")
        self.cbo_forn = ttk.Combobox(bar, textvariable=self.filtro_forn,
                                     state="disabled", width=36,
                                     font=(self.F_SANS, 9))
        self.cbo_forn.pack(side="left", padx=(6, 16))
        self.cbo_forn.bind("<<ComboboxSelected>>", lambda _: self._aplicar_filtro())

        ttk.Label(bar, text="Descrição:").pack(side="left")
        ttk.Entry(bar, textvariable=self.busca_desc, width=22).pack(
            side="left", padx=(6, 16))
        self.busca_desc.trace_add(
            "write", lambda *_: self.root.after(300, self._aplicar_filtro))

        ttk.Label(bar, text="Estoque:").pack(side="left")
        ttk.Combobox(bar, textvariable=self.filtro_est, state="readonly",
                     values=["Todos", "Com estoque", "Sem estoque"],
                     width=14).pack(side="left", padx=6)
        self.filtro_est.trace_add("write", lambda *_: self._aplicar_filtro())

        # ── Rodapé (empacotado antes da tabela e fixo no fundo, para
        # permanecer visível mesmo com a janela no tamanho mínimo)
        rod = ttk.Frame(main)
        rod.pack(side="bottom", fill="x", pady=(6, 0))
        self.lbl_total = ttk.Label(rod, text="",
                                   font=(self.F_MONO, 10, "bold"))
        self.lbl_total.pack(side="left")
        ttk.Button(rod, text="Exportar Excel",
                   command=lambda: self._exportar("xlsx")).pack(
                       side="right", padx=(4, 0))
        ttk.Button(rod, text="Exportar CSV",
                   command=lambda: self._exportar("csv")).pack(
                       side="right", padx=(0, 4))

        # ── Treeview
        frm_t = ttk.Frame(main)
        frm_t.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frm_t, columns=self.COLUNAS,
                                 show="headings", height=14)
        vsb = ttk.Scrollbar(frm_t, orient="vertical",   command=self.tree.yview)
        hsb = ttk.Scrollbar(frm_t, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        for col in self.COLUNAS:
            anc = "w" if col in ("Descrição", "Fornecedor") else "center"
            self.tree.heading(col, text=col,
                              command=lambda c=col: self._ordenar(c))
            self.tree.column(col, width=self.COL_W.get(col, 90),
                             anchor=anc, minwidth=40)
        # Sem estoque = estado devedor → tinta vermelha sutil; zebra em creme
        self.tree.tag_configure("com",  background=self.COR_COM)
        self.tree.tag_configure("com2", background="#FBF7F0")
        self.tree.tag_configure("sem",  background=self.COR_SEM,
                                foreground=self.COR_ERRO)
        vsb.pack(side="right",  fill="y")
        hsb.pack(side="bottom", fill="x")
        self.tree.pack(fill="both", expand=True)

    # ───────────────────────────────────────────────────────────
    def _toggle_data(self):
        if self.modo_data.get() == "especifica":
            self.ent_data.config(state="normal")
            self.lbl_aviso.grid()
        else:
            self.ent_data.config(state="disabled")
            self.lbl_aviso.grid_remove()

    def _render_cards(self, dados):
        for w in self.frm_cards.winfo_children():
            w.destroy()
        for titulo, valor, cor in dados:
            # Cartão: papel quase-branco, filete arenito, sem relevo plástico
            card = tk.Frame(self.frm_cards, bg=self.COR_CARD, bd=0,
                            highlightbackground=self.COR_BORDA,
                            highlightthickness=1, padx=16, pady=8)
            card.pack(side="left", padx=4)
            # Cifras e contagens em mono tabular
            tk.Label(card, text=valor, font=(self.F_MONO, 16, "bold"),
                     fg=cor, bg=self.COR_CARD).pack()
            tk.Label(card, text=titulo.upper(), font=(self.F_SANS, 8),
                     fg=self.COR_MUTED, bg=self.COR_CARD).pack()

    def _ordenar(self, col):
        itens = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]
        asc   = not self._sort_dir.get(col, False)
        self._sort_dir[col] = asc
        itens.sort(key=lambda t: _sort_key(t[0]), reverse=asc)
        for i, (_, k) in enumerate(itens):
            self.tree.move(k, "", i)

    def _status(self, msg: str, cor: str = None):
        self.lbl_status.config(text=msg, foreground=cor or self.COR_SUB)

    def _centralizar(self):
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        ww, wh = self.root.winfo_width(),       self.root.winfo_height()
        self.root.geometry(f"{ww}x{wh}+{(sw-ww)//2}+{(sh-wh)//2}")

    # ───────────────────────────────────────────────────────────
    def _iniciar(self):
        emp = self.empresa_sel.get()
        data_ref = None
        if self.modo_data.get() == "especifica":
            try:
                data_ref = datetime.strptime(
                    self.data_str.get().strip(), "%d/%m/%Y").date()
            except ValueError:
                messagebox.showerror("Data inválida",
                                     "Use o formato DD/MM/AAAA.\nEx.: 31/03/2025")
                return

        self._hist = data_ref is not None
        self.btn_gerar.state(["disabled"])
        self.cbo_forn.config(state="disabled")
        self.progress.start(10)
        self._status(f"Conectando em {emp}…", self.COR_AVISO)

        threading.Thread(
            target=self._executar,
            args=(emp, self.tipo_banco.get(), data_ref),
            daemon=True,
        ).start()

    def _executar(self, empresa, tipo_banco, data_ref):
        try:
            df = extrair_estoque(empresa, tipo_banco, data_ref)
            self.root.after(0, self._pos_execucao, df, [])
        except Exception as ex:
            self.root.after(0, self._pos_execucao,
                            pd.DataFrame(), [f"{empresa}: {ex}"])

    def _pos_execucao(self, df: pd.DataFrame, erros: list):
        self.progress.stop()
        self.btn_gerar.state(["!disabled"])
        if erros and df.empty:
            self._status("Falha na conexão.", self.COR_ERRO)
            messagebox.showerror("Erro de conexão", "\n".join(erros))
            return
        self.df_full = df
        forns = ["Todos"] + sorted(
            df["Fornecedor"].fillna("(sem fornecedor)").unique().tolist())
        self.cbo_forn.config(values=forns, state="readonly")
        self.filtro_forn.set("Todos")
        self._aplicar_filtro()

    # ───────────────────────────────────────────────────────────
    def _aplicar_filtro(self):
        if self.df_full is None or self.df_full.empty:
            return
        df = self.df_full.copy()

        forn = self.filtro_forn.get()
        if forn and forn != "Todos":
            df = df[df["Fornecedor"].fillna("(sem fornecedor)") == forn]

        busca = self.busca_desc.get().strip().lower()
        if busca:
            df = df[df["Descrição"].str.lower().str.contains(busca, na=False)]

        est_n = pd.to_numeric(df["Estoque"], errors="coerce").fillna(0)
        if self.filtro_est.get() == "Com estoque":
            df = df[est_n > 0]
        elif self.filtro_est.get() == "Sem estoque":
            df = df[est_n <= 0]

        n_total = len(df)
        est_num = pd.to_numeric(df["Estoque"], errors="coerce").fillna(0)
        n_com   = int((est_num > 0).sum())
        n_sem   = n_total - n_com
        total_v = pd.to_numeric(df["Total Item"], errors="coerce").fillna(0).sum()

        self._render_cards([
            ("Produtos",    str(n_total), self.COR_TEXTO),
            ("Com estoque", str(n_com),   self.COR_OK),
            ("Sem estoque", str(n_sem),   self.COR_ERRO),
            ("Total (R$)",  _brl(total_v).replace("R$ ", ""), self.COR_TEXTO),
        ])
        self._preencher(df)
        suf = " · histórico estimado" if self._hist else ""
        self._status(f"{n_total} produtos exibidos{suf}", self.COR_OK)
        self.lbl_total.config(text=f"Total filtrado: {_brl(total_v)}")

    def _preencher(self, df: pd.DataFrame):
        self.tree.delete(*self.tree.get_children())
        vis = 0
        for _, row in df.iterrows():
            est = _float_safe(row.get("Estoque", 0))
            if est > 0:
                tag = "com" if vis % 2 == 0 else "com2"
                vis += 1
            else:
                tag = "sem"
            try:    cod = int(row["Cód. Produto"])
            except: cod = row.get("Cód. Produto", "")
            # Formata estoque: sem decimais se inteiro, 3 casas se fracionado
            if est == int(est):
                est_str = str(int(est))
            else:
                est_str = f"{est:,.3f}".replace(",", "X").replace(".", ",").replace("X", ".")
            self.tree.insert("", "end", tags=(tag,), values=(
                row.get("Empresa",        ""),
                row.get("Banco",          ""),
                cod,
                row.get("Descrição",      ""),
                est_str,
                _brl(row.get("Preço de Custo", 0)),
                _brl(row.get("Preço de Venda", 0)),
                _brl(row.get("Total Item",     0)),
                row.get("Fornecedor",     ""),
            ))

    # ───────────────────────────────────────────────────────────
    def _exportar(self, fmt: str):
        if self.df_full is None or self.df_full.empty:
            messagebox.showinfo("Sem dados", "Gere o relatório antes de exportar.")
            return
        ts   = datetime.now().strftime("%Y%m%d_%H%M")
        nome = f"Estoque_{ts}"
        if fmt == "csv":
            path = filedialog.asksaveasfilename(
                defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                initialfile=f"{nome}.csv", title="Salvar CSV")
            if path:
                self.df_full.to_csv(path, index=False, encoding="utf-8-sig")
                messagebox.showinfo("Exportado", f"Salvo:\n{path}")
        else:
            path = filedialog.asksaveasfilename(
                defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")],
                initialfile=f"{nome}.xlsx", title="Salvar Excel")
            if not path:
                return
            try:
                with pd.ExcelWriter(path, engine="openpyxl") as w:
                    self.df_full.to_excel(w, sheet_name="Estoque", index=False)
                messagebox.showinfo("Exportado", f"Salvo:\n{path}")
            except Exception as ex:
                messagebox.showerror("Erro ao exportar", str(ex))


# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    root = tk.Tk()
    AppEstoque(root)
    root.mainloop()
