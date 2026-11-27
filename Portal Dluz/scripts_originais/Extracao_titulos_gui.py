#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ATS — Títulos (Contas a Pagar / Receber)  ·  v1
================================================
Interface unificada para as extrações de Contas a Pagar e Contas a
Receber (substitui o uso manual de Extracao_cpagar.py e
Extracao_creceber.py, que continuam funcionando via terminal).

  • Visual Grupo Dluz: base creme, grafite profundo, acento âmbar;
    valores em fonte monoespaçada; saldo devedor em vermelho.
  • Alterna entre Pagar e Receber sem sair da tela.
  • Busca paralela nas três empresas, com tolerância a falha parcial.
  • Filtros pós-busca: empresa, situação e busca por nome.
  • Cards de resumo (Total Títulos, Total Pago, Saldo).
  • Ordenação por clique no cabeçalho · linhas zebradas.
  • Atalhos: Enter = buscar · Ctrl+E = exportar.

Dependências:
    pip install firebirdsql pandas openpyxl
"""

import threading
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import font as tkfont

import firebirdsql
import pandas as pd

warnings.filterwarnings('ignore', message='pandas only supports SQLAlchemy')

# ═══════════════════════════════════════════════════════════════
#  CONFIGURAÇÃO DAS 3 EMPRESAS
# ═══════════════════════════════════════════════════════════════
EMPRESAS = {
    "Dluz Elétrica":   {"HOST": "192.168.0.41",  "DATABASE": r"D:\DLUZR\BANCO\DLUZR25.FB"},
    "Cor e Cia":       {"HOST": "192.168.0.200", "DATABASE": r"D:\CorCiaR\BANCO\CORCIAR25.FB"},
    "Dluz Iluminação": {"HOST": "192.168.0.250", "DATABASE": r"D:\ILUMIR\BANCO\ILUMIR.FB"},
}

USER     = 'SYSDBA'
PASSWORD = 'masterkey'
PORT     = 3050
CHARSET  = 'WIN1252'

SQL_PAGAR = """
SELECT
    d.DT_VENCIMENTO                     AS "VENCIMENTO",
    d.DT_EMISSAO                        AS "EMISSÃO",
    d.DOCTOFORNEC                       AS "DOCUMENTO",
    d.CODFORNEC                         AS "CÓD.",
    f.NOME                              AS "NOME",
    COALESCE(d.VALORDOCTO, 0)           AS "VALOR TÍTULO",
    COALESCE(d.VALORPAGO,  0)           AS "VALOR PAGO",
    CASE
        WHEN d.SITUACAO = 1 THEN 'Aberto'
        WHEN d.SITUACAO = 2 THEN 'Pago'
        WHEN d.SITUACAO = 3 THEN 'Parcial'
        ELSE CAST(d.SITUACAO AS VARCHAR(10))
    END                                 AS "SITUAÇÃO"
FROM DOCUPAG d
LEFT JOIN FORNECE f ON d.CODFORNEC = f.CODFORNEC
WHERE d.DT_VENCIMENTO BETWEEN ? AND ?
ORDER BY d.DT_VENCIMENTO, f.NOME
"""

SQL_RECEBER = """
SELECT
    d.DT_VENCIMENTO                     AS "VENCIMENTO",
    d.DT_EMISSAO                        AS "EMISSÃO",
    d.CODDOCTO                          AS "DOCUMENTO",
    d.CODCLIENTE                        AS "CÓD.",
    c.NOME                              AS "NOME",
    COALESCE(d.VALORDOCTO, 0)           AS "VALOR TÍTULO",
    COALESCE(d.VALORPAGO,  0)           AS "VALOR PAGO",
    CASE
        WHEN d.SITUACAO = 1 THEN 'Aberto'
        WHEN d.SITUACAO = 2 THEN 'Pago'
        WHEN d.SITUACAO = 3 THEN 'Parcial'
        ELSE CAST(d.SITUACAO AS VARCHAR(10))
    END                                 AS "SITUAÇÃO"
FROM DOCUREC d
LEFT JOIN CLIENTE c ON d.CODCLIENTE = c.CODCLIENTE
WHERE d.DT_VENCIMENTO BETWEEN ? AND ?
ORDER BY d.DT_VENCIMENTO, c.NOME
"""

MODOS = {
    "pagar":   {"sql": SQL_PAGAR,   "titulo": "Contas a Pagar",   "pessoa": "Fornecedor"},
    "receber": {"sql": SQL_RECEBER, "titulo": "Contas a Receber", "pessoa": "Cliente"},
}

COLS_NUMERICAS = ["VALOR TÍTULO", "VALOR PAGO", "SALDO"]


def consultar_empresa(nome, cfg, sql, dt_ini, dt_fim):
    """Consulta uma empresa e devolve o DataFrame com coluna EMPRESA e SALDO."""
    con = firebirdsql.connect(
        host=cfg["HOST"], database=cfg["DATABASE"],
        port=PORT, user=USER, password=PASSWORD, charset=CHARSET,
    )
    try:
        df = pd.read_sql(sql, con, params=(dt_ini, dt_fim))
    finally:
        con.close()
    if not df.empty:
        df.insert(0, "EMPRESA", nome)
        df["SALDO"] = df["VALOR TÍTULO"] - df["VALOR PAGO"]
        for c in ("VENCIMENTO", "EMISSÃO"):
            df[c] = pd.to_datetime(df[c]).dt.strftime("%d/%m/%Y")
    return df


# ═══════════════════════════════════════════════════════════════
#  DESIGN — identidade Grupo Dluz
# ═══════════════════════════════════════════════════════════════
class Tema:
    BG          = "#f7f3ea"
    SURFACE     = "#fffdf8"
    BORDA       = "#e9e0d0"
    GRAFITE     = "#241f18"
    TEXTO       = "#2c2620"
    TEXTO_SUAVE = "#7c7468"
    AMBAR       = "#946517"
    AMBAR_FORTE = "#74500f"
    AMBAR_SOFT  = "#f0e5cd"
    VERMELHO    = "#b3261e"
    VERDE       = "#3d6a3d"
    ZEBRA       = "#faf6ec"
    TOTAL_BG    = "#efe6d2"
    SELECAO     = "#e6dabf"

    @staticmethod
    def fontes(root):
        disponiveis = set(tkfont.families(root))

        def pick(*opcoes, padrao="TkDefaultFont"):
            for f in opcoes:
                if f in disponiveis:
                    return f
            return padrao

        sans  = pick("Segoe UI", "Helvetica Neue", "Helvetica", "Arial")
        serif = pick("Georgia", "Times New Roman", "Times")
        mono  = pick("Consolas", "Menlo", "Courier New", "Courier")
        return sans, serif, mono


# ═══════════════════════════════════════════════════════════════
#  APLICAÇÃO
# ═══════════════════════════════════════════════════════════════
class AppTitulos:

    COLUNAS = ("EMPRESA", "VENCIMENTO", "EMISSÃO", "DOCUMENTO", "CÓD.",
               "NOME", "VALOR TÍTULO", "VALOR PAGO", "SALDO", "SITUAÇÃO")
    LARGURAS = {
        "EMPRESA": 120, "VENCIMENTO": 90, "EMISSÃO": 90, "DOCUMENTO": 100,
        "CÓD.": 60, "NOME": 240, "VALOR TÍTULO": 105, "VALOR PAGO": 105,
        "SALDO": 105, "SITUAÇÃO": 80,
    }

    def __init__(self, root):
        self.root = root
        self.root.title("ATS — Títulos · Pagar / Receber")
        self.root.geometry("1240x740")
        self.root.minsize(980, 560)

        self.df_resultado = None
        self.ordenacao = {}
        self.v_modo            = tk.StringVar(value="pagar")
        self.v_filtro_empresa  = tk.StringVar(value="Todas")
        self.v_filtro_situacao = tk.StringVar(value="Todas")
        self.v_busca_nome      = tk.StringVar(value="")

        self.f_sans, self.f_serif, self.f_mono = Tema.fontes(root)

        self._aplicar_estilo()
        self._construir_interface()
        self._preencher_datas_padrao()
        self._atalhos()
        self._centralizar()

    # ── Estilo ──────────────────────────────────────────────────
    def _aplicar_estilo(self):
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass

        T = Tema
        self.root.configure(background=T.BG)

        st.configure(".",           background=T.BG, foreground=T.TEXTO,
                     font=(self.f_sans, 10))
        st.configure("TFrame",      background=T.BG)
        st.configure("TLabel",      background=T.BG, foreground=T.TEXTO)
        st.configure("Sub.TLabel",  foreground=T.TEXTO_SUAVE)
        st.configure("Titulo.TLabel",
                     font=(self.f_serif, 17, "bold"), foreground=T.GRAFITE)
        st.configure("TSeparator",  background=T.BORDA)

        st.configure("TLabelframe", background=T.BG,
                     bordercolor=T.BORDA, relief="solid", borderwidth=1)
        st.configure("TLabelframe.Label",
                     background=T.BG, foreground=T.AMBAR,
                     font=(self.f_sans, 9, "bold"))

        st.configure("TRadiobutton", background=T.BG, foreground=T.TEXTO,
                     indicatorcolor=T.SURFACE, font=(self.f_sans, 10))
        st.map("TRadiobutton",
               background=[("active", T.BG)],
               indicatorcolor=[("selected", T.AMBAR)])

        st.configure("TEntry",
                     fieldbackground=T.SURFACE, bordercolor=T.BORDA,
                     lightcolor=T.BORDA, darkcolor=T.BORDA,
                     foreground=T.TEXTO, insertcolor=T.TEXTO)
        st.map("TEntry", bordercolor=[("focus", T.AMBAR)])

        st.configure("TCombobox",
                     fieldbackground=T.SURFACE, background=T.SURFACE,
                     bordercolor=T.BORDA, arrowcolor=T.AMBAR,
                     foreground=T.TEXTO)
        st.map("TCombobox",
               bordercolor=[("focus", T.AMBAR)],
               fieldbackground=[("readonly", T.SURFACE)])

        st.configure("Primario.TButton",
                     background=T.AMBAR, foreground=T.SURFACE,
                     bordercolor=T.AMBAR, focusthickness=0,
                     font=(self.f_sans, 10, "bold"), padding=(14, 6))
        st.map("Primario.TButton",
               background=[("active", T.AMBAR_FORTE), ("disabled", T.BORDA)],
               foreground=[("disabled", T.TEXTO_SUAVE)])

        st.configure("Secundario.TButton",
                     background=T.BG, foreground=T.TEXTO,
                     bordercolor=T.BORDA, focusthickness=0,
                     font=(self.f_sans, 10), padding=(12, 6))
        st.map("Secundario.TButton",
               background=[("active", T.AMBAR_SOFT), ("disabled", T.BG)],
               foreground=[("disabled", T.TEXTO_SUAVE)])

        st.configure("TProgressbar",
                     background=T.AMBAR, troughcolor=T.AMBAR_SOFT,
                     bordercolor=T.BORDA)

        st.configure("Treeview",
                     background=T.SURFACE, fieldbackground=T.SURFACE,
                     foreground=T.TEXTO, rowheight=26,
                     font=(self.f_sans, 9), bordercolor=T.BORDA)
        st.configure("Treeview.Heading",
                     font=(self.f_sans, 9, "bold"),
                     background=T.GRAFITE, foreground="#fbf7f0",
                     relief="flat", padding=(4, 6))
        st.map("Treeview.Heading", background=[("active", "#3a332a")])
        st.map("Treeview",
               background=[("selected", T.SELECAO)],
               foreground=[("selected", T.GRAFITE)])

        st.configure("Vertical.TScrollbar",
                     background=T.BORDA, troughcolor=T.BG, arrowcolor=T.AMBAR)
        st.configure("Horizontal.TScrollbar",
                     background=T.BORDA, troughcolor=T.BG, arrowcolor=T.AMBAR)

    # ── Interface ────────────────────────────────────────────────
    def _construir_interface(self):
        main = ttk.Frame(self.root, padding=16)
        main.pack(fill=tk.BOTH, expand=True)

        cab = ttk.Frame(main)
        cab.pack(fill="x")
        ttk.Label(cab, text="GRUPO DLUZ · ATS",
                  font=(self.f_sans, 8, "bold"),
                  foreground=Tema.TEXTO_SUAVE).pack(anchor="w")
        self.lbl_titulo = ttk.Label(cab, text="Títulos — Contas a Pagar",
                                    style="Titulo.TLabel")
        self.lbl_titulo.pack(anchor="w", pady=(2, 0))
        ttk.Separator(main, orient="horizontal").pack(fill="x", pady=(8, 12))

        # ── Linha 1: Modo + Datas + Ações ────────────────────────
        frm_busca = ttk.Frame(main)
        frm_busca.pack(fill="x", pady=(0, 8))

        grp_modo = ttk.LabelFrame(frm_busca, text=" TIPO ", padding=8)
        grp_modo.pack(side="left", fill="y", padx=(0, 10))
        ttk.Radiobutton(grp_modo, text="Contas a Pagar",
                        variable=self.v_modo, value="pagar",
                        command=self._on_modo).pack(anchor="w", pady=1)
        ttk.Radiobutton(grp_modo, text="Contas a Receber",
                        variable=self.v_modo, value="receber",
                        command=self._on_modo).pack(anchor="w", pady=1)

        grp_datas = ttk.LabelFrame(frm_busca, text=" VENCIMENTO ENTRE ", padding=8)
        grp_datas.pack(side="left", fill="y", padx=(0, 10))

        ttk.Label(grp_datas, text="Inicial:").grid(row=0, column=0, sticky="w")
        self.ent_data_ini = ttk.Entry(grp_datas, width=12, font=(self.f_mono, 10))
        self.ent_data_ini.grid(row=0, column=1, padx=(4, 14))
        ttk.Label(grp_datas, text="Final:").grid(row=0, column=2, sticky="w")
        self.ent_data_fim = ttk.Entry(grp_datas, width=12, font=(self.f_mono, 10))
        self.ent_data_fim.grid(row=0, column=3, padx=(4, 0))
        ttk.Label(grp_datas, text="(DD/MM/AAAA)", style="Sub.TLabel",
                  font=(self.f_sans, 8)).grid(row=1, column=1, columnspan=3,
                                              sticky="w", pady=(4, 0))

        grp_acao = ttk.LabelFrame(frm_busca, text=" AÇÕES ", padding=8)
        grp_acao.pack(side="left", fill="y")

        self.btn_buscar = ttk.Button(
            grp_acao, text="Buscar no Banco", style="Primario.TButton",
            command=self._iniciar_busca)
        self.btn_buscar.pack(side="left", padx=(0, 8))

        self.btn_exportar = ttk.Button(
            grp_acao, text="Exportar Excel", style="Secundario.TButton",
            command=self._exportar_excel, state="disabled")
        self.btn_exportar.pack(side="left")

        # ── Linha 2: Progress + Status ───────────────────────────
        frm_status = ttk.Frame(main)
        frm_status.pack(fill="x", pady=(0, 8))
        self.progress   = ttk.Progressbar(frm_status, mode="indeterminate", length=140)
        self.lbl_status = ttk.Label(
            frm_status,
            text="Escolha o tipo e o período de vencimento, depois clique em Buscar  (Enter).",
            style="Sub.TLabel")
        self.lbl_status.pack(side="left")

        # ── Linha 3: Cards de resumo ─────────────────────────────
        self.frm_cards = ttk.Frame(main)
        self.frm_cards.pack(fill="x", pady=(0, 8))
        self._render_cards([("Títulos", "—", Tema.TEXTO),
                            ("Valor Títulos", "—", Tema.TEXTO),
                            ("Valor Pago", "—", Tema.VERDE),
                            ("Saldo", "—", Tema.VERMELHO)])

        # ── Linha 4: Filtros de visualização ─────────────────────
        grp_filtros = ttk.LabelFrame(main, text=" FILTROS DE VISUALIZAÇÃO ", padding=8)
        grp_filtros.pack(fill="x", pady=(0, 10))

        ttk.Label(grp_filtros, text="Empresa:",
                  font=(self.f_sans, 9, "bold")).pack(side="left", padx=(0, 4))
        self.cbo_filtro_empresa = ttk.Combobox(
            grp_filtros, textvariable=self.v_filtro_empresa,
            state="disabled", width=18, font=(self.f_sans, 9))
        self.cbo_filtro_empresa.pack(side="left", padx=(0, 16))
        self.cbo_filtro_empresa.bind("<<ComboboxSelected>>",
                                     lambda e: self._aplicar_filtros())

        ttk.Label(grp_filtros, text="Situação:",
                  font=(self.f_sans, 9, "bold")).pack(side="left", padx=(0, 4))
        self.cbo_filtro_situacao = ttk.Combobox(
            grp_filtros, textvariable=self.v_filtro_situacao,
            state="disabled", width=12, font=(self.f_sans, 9))
        self.cbo_filtro_situacao.pack(side="left", padx=(0, 16))
        self.cbo_filtro_situacao.bind("<<ComboboxSelected>>",
                                      lambda e: self._aplicar_filtros())

        self.lbl_pessoa = ttk.Label(grp_filtros, text="Fornecedor:",
                                    font=(self.f_sans, 9, "bold"))
        self.lbl_pessoa.pack(side="left", padx=(0, 4))
        self.ent_busca = ttk.Entry(grp_filtros, textvariable=self.v_busca_nome,
                                   width=24)
        self.ent_busca.pack(side="left", padx=(0, 14))
        self.v_busca_nome.trace_add(
            "write", lambda *_: self.root.after(300, self._aplicar_filtros))

        ttk.Button(grp_filtros, text="Limpar filtros", style="Secundario.TButton",
                   command=self._limpar_filtros).pack(side="left")

        # ── Tabela ───────────────────────────────────────────────
        frm_tree = ttk.Frame(main)
        frm_tree.pack(fill=tk.BOTH, expand=True)

        self.tree = ttk.Treeview(frm_tree, columns=self.COLUNAS, show="headings")
        self.tree.tag_configure("zebra", background=Tema.ZEBRA)
        self.tree.tag_configure("numero", font=(self.f_mono, 9))
        self.tree.tag_configure(
            "total", font=(self.f_mono, 9, "bold"),
            background=Tema.TOTAL_BG, foreground=Tema.GRAFITE)

        vsb = ttk.Scrollbar(frm_tree, orient="vertical",   command=self.tree.yview)
        hsb = ttk.Scrollbar(frm_tree, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        for col in self.COLUNAS:
            anc = ("w" if col in ("NOME", "EMPRESA", "DOCUMENTO")
                   else "e" if col in COLS_NUMERICAS else "center")
            self.tree.heading(col, text=col,
                              command=lambda c=col: self._ordenar_por(c))
            self.tree.column(col, width=self.LARGURAS.get(col, 80),
                             anchor=anc, minwidth=45)

        vsb.pack(side="right",  fill="y")
        hsb.pack(side="bottom", fill="x")
        self.tree.pack(fill=tk.BOTH, expand=True)

    # ── Helpers ──────────────────────────────────────────────────
    def _atalhos(self):
        self.root.bind("<Return>",    lambda e: self._iniciar_busca())
        self.root.bind("<Control-e>", lambda e: self._exportar_excel())

    def _preencher_datas_padrao(self):
        hoje = datetime.now().strftime("%d/%m/%Y")
        self.ent_data_ini.insert(0, hoje)
        self.ent_data_fim.insert(0, hoje)

    def _centralizar(self):
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        ww, wh = self.root.winfo_width(),       self.root.winfo_height()
        self.root.geometry(f"{ww}x{wh}+{(sw - ww) // 2}+{(sh - wh) // 2}")

    def _set_status(self, msg, cor):
        self.lbl_status.config(text=msg, foreground=cor)

    @staticmethod
    def _fmt_br(val):
        try:
            return (f"{float(val):,.2f}"
                    .replace(",", "X")
                    .replace(".", ",")
                    .replace("X", "."))
        except (ValueError, TypeError):
            return "0,00"

    def _render_cards(self, dados):
        for w in self.frm_cards.winfo_children():
            w.destroy()
        for titulo, valor, cor in dados:
            card = tk.Frame(self.frm_cards, bg=Tema.SURFACE, bd=0,
                            highlightbackground=Tema.BORDA,
                            highlightthickness=1, padx=16, pady=8)
            card.pack(side="left", padx=(0, 8))
            tk.Label(card, text=valor, font=(self.f_mono, 15, "bold"),
                     fg=cor, bg=Tema.SURFACE).pack()
            tk.Label(card, text=titulo.upper(), font=(self.f_sans, 8),
                     fg=Tema.TEXTO_SUAVE, bg=Tema.SURFACE).pack()

    def _on_modo(self):
        modo = MODOS[self.v_modo.get()]
        self.lbl_titulo.config(text=f"Títulos — {modo['titulo']}")
        self.lbl_pessoa.config(text=f"{modo['pessoa']}:")

    def _df_filtrado(self):
        df = self.df_resultado
        if df is None:
            return None
        emp = self.v_filtro_empresa.get()
        sit = self.v_filtro_situacao.get()
        nom = self.v_busca_nome.get().strip().lower()
        if emp and emp != "Todas":
            df = df[df["EMPRESA"] == emp]
        if sit and sit != "Todas":
            df = df[df["SITUAÇÃO"] == sit]
        if nom:
            df = df[df["NOME"].astype(str).str.lower()
                    .str.contains(nom, na=False)]
        return df

    # ── Busca ────────────────────────────────────────────────────
    def _iniciar_busca(self):
        if "disabled" in self.btn_buscar.state():
            return
        try:
            dt_ini = datetime.strptime(
                self.ent_data_ini.get().strip(), "%d/%m/%Y")
            dt_fim = datetime.strptime(
                self.ent_data_fim.get().strip(), "%d/%m/%Y")
        except ValueError:
            messagebox.showerror("Formato Incorreto",
                                 "Use o formato DD/MM/AAAA nas datas.\n"
                                 "Exemplo: 01/06/2026")
            return
        if dt_ini > dt_fim:
            messagebox.showerror("Período Inválido",
                                 "A data inicial é posterior à data final.")
            return

        self.btn_buscar.state(["disabled"])
        self.btn_exportar.state(["disabled"])
        self.cbo_filtro_empresa.config(state="disabled")
        self.cbo_filtro_situacao.config(state="disabled")
        self.progress.pack(side="left", padx=(8, 0), before=self.lbl_status)
        self.progress.start(10)
        self._set_status("Consultando as três empresas…", Tema.AMBAR)

        threading.Thread(
            target=self._thread_busca,
            args=(dt_ini.strftime("%Y-%m-%d"), dt_fim.strftime("%Y-%m-%d"),
                  MODOS[self.v_modo.get()]["sql"]),
            daemon=True,
        ).start()

    def _thread_busca(self, dt_ini, dt_fim, sql):
        frames, falhas = [], []
        with ThreadPoolExecutor(max_workers=len(EMPRESAS)) as pool:
            futuros = {
                pool.submit(consultar_empresa, nome, cfg, sql, dt_ini, dt_fim): nome
                for nome, cfg in EMPRESAS.items()
            }
            for fut in as_completed(futuros):
                nome = futuros[fut]
                try:
                    df = fut.result()
                    if not df.empty:
                        frames.append(df)
                except Exception as exc:
                    falhas.append((nome, str(exc)))

        if falhas and not frames:
            detalhe = "\n".join(f"• {n}: {e}" for n, e in falhas)
            self.root.after(0, self._pos_busca_erro, detalhe)
            return

        if frames:
            df_final = pd.concat(frames, ignore_index=True)
            chave = pd.to_datetime(df_final["VENCIMENTO"],
                                   format="%d/%m/%Y", errors="coerce")
            df_final = (df_final.assign(_ord=chave)
                        .sort_values(["_ord", "EMPRESA", "NOME"])
                        .drop(columns="_ord")
                        .reset_index(drop=True))
        else:
            df_final = pd.DataFrame(columns=list(self.COLUNAS))

        self.df_resultado = df_final
        self.root.after(0, self._pos_busca_ok, falhas)

    def _destravar_controles(self):
        self.progress.stop()
        self.progress.pack_forget()
        self.btn_buscar.state(["!disabled"])

    def _pos_busca_ok(self, falhas=()):
        self._destravar_controles()

        df = self.df_resultado
        if df is None or df.empty:
            self.btn_exportar.state(["disabled"])
            self._set_status(
                "Nenhum título encontrado para este período.", Tema.AMBAR_FORTE)
            self._render_cards([("Títulos", "0", Tema.TEXTO),
                                ("Valor Títulos", "0,00", Tema.TEXTO),
                                ("Valor Pago", "0,00", Tema.VERDE),
                                ("Saldo", "0,00", Tema.VERMELHO)])
            self._preencher_tabela(df)
            return

        empresas = ["Todas"] + sorted(df["EMPRESA"].unique().tolist())
        self.cbo_filtro_empresa.config(values=empresas, state="readonly")
        self.v_filtro_empresa.set("Todas")

        situacoes = ["Todas"] + sorted(df["SITUAÇÃO"].astype(str).unique().tolist())
        self.cbo_filtro_situacao.config(values=situacoes, state="readonly")
        self.v_filtro_situacao.set("Todas")
        self.v_busca_nome.set("")

        self.btn_exportar.state(["!disabled"])
        self.ordenacao.clear()
        self._aplicar_filtros()

        if falhas:
            nomes = ", ".join(n for n, _ in falhas)
            messagebox.showwarning(
                "Busca Parcial",
                f"Não foi possível consultar: {nomes}.\n"
                "Os resultados exibidos incluem apenas as empresas acessíveis.")

    def _pos_busca_erro(self, msg):
        self._destravar_controles()
        self._set_status("Erro na consulta.", Tema.VERMELHO)
        messagebox.showerror(
            "Erro de Banco de Dados",
            f"Falha ao conectar ou executar no Firebird:\n\n{msg}")

    # ── Filtros ──────────────────────────────────────────────────
    def _limpar_filtros(self):
        if self.df_resultado is None:
            return
        self.v_filtro_empresa.set("Todas")
        self.v_filtro_situacao.set("Todas")
        self.v_busca_nome.set("")
        self._aplicar_filtros()

    def _aplicar_filtros(self, event=None):
        df = self._df_filtrado()
        if df is None:
            return

        self._preencher_tabela(df)

        tot_tit  = df["VALOR TÍTULO"].sum() if not df.empty else 0
        tot_pago = df["VALOR PAGO"].sum()   if not df.empty else 0
        saldo    = df["SALDO"].sum()        if not df.empty else 0
        self._render_cards([
            ("Títulos",       str(len(df)),          Tema.TEXTO),
            ("Valor Títulos", self._fmt_br(tot_tit), Tema.TEXTO),
            ("Valor Pago",    self._fmt_br(tot_pago), Tema.VERDE),
            ("Saldo",         self._fmt_br(saldo),
             Tema.VERMELHO if saldo > 0 else Tema.VERDE),
        ])

        emp, sit = self.v_filtro_empresa.get(), self.v_filtro_situacao.get()
        sufixo = ""
        if emp != "Todas": sufixo += f"  ·  {emp}"
        if sit != "Todas": sufixo += f"  ·  {sit}"
        self._set_status(
            f"{len(df)} título(s) exibido(s){sufixo}", Tema.VERDE)

    # ── Ordenação ────────────────────────────────────────────────
    def _ordenar_por(self, col):
        df = self._df_filtrado()
        if df is None or df.empty or col not in df.columns:
            return
        asc = self.ordenacao.get(col, False)
        self.ordenacao = {col: not asc}

        if col in ("VENCIMENTO", "EMISSÃO"):
            chave = pd.to_datetime(df[col], format="%d/%m/%Y", errors="coerce")
        elif col in COLS_NUMERICAS:
            chave = pd.to_numeric(df[col], errors="coerce")
        else:
            chave = df[col].astype(str).str.lower()

        ordem = chave.sort_values(ascending=not asc).index
        self._preencher_tabela(df.loc[ordem])

    # ── Tabela ───────────────────────────────────────────────────
    def _preencher_tabela(self, df):
        self.tree.delete(*self.tree.get_children())
        if df is None or df.empty:
            return

        for i, (_, row) in enumerate(df.iterrows()):
            vals = []
            for col in self.COLUNAS:
                v = row.get(col, "")
                if col in COLS_NUMERICAS:
                    vals.append(self._fmt_br(v))
                else:
                    vals.append("" if pd.isna(v) else str(v))
            tags = ("numero", "zebra") if i % 2 else ("numero",)
            self.tree.insert("", "end", values=vals, tags=tags)

        totais = {c: "" for c in self.COLUNAS}
        totais["NOME"] = "TOTAIS"
        for col in COLS_NUMERICAS:
            if col in df.columns:
                totais[col] = df[col].sum()

        vals_t = [
            self._fmt_br(totais[c]) if c in COLS_NUMERICAS
            else str(totais[c])
            for c in self.COLUNAS
        ]
        self.tree.insert("", "end", values=vals_t, tags=("total",))
        self.tree.yview_moveto(1.0)

    # ── Exportação ───────────────────────────────────────────────
    def _exportar_excel(self):
        if "disabled" in self.btn_exportar.state():
            return
        df = self._df_filtrado()
        if df is None or df.empty:
            return

        modo = MODOS[self.v_modo.get()]
        ini = self.ent_data_ini.get().replace("/", "")
        fim = self.ent_data_fim.get().replace("/", "")
        prefixo = ("Contas_Pagar" if self.v_modo.get() == "pagar"
                   else "Contas_Receber")
        path = filedialog.asksaveasfilename(
            title="Salvar como Planilha Excel",
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")],
            initialfile=f"{prefixo}_Unificado_{ini}_a_{fim}.xlsx",
        )
        if not path:
            return

        try:
            totais = {c: "" for c in df.columns}
            totais["NOME"] = "TOTAIS"
            for col in COLS_NUMERICAS:
                if col in df.columns:
                    totais[col] = df[col].sum()

            df_exp = pd.concat([df, pd.DataFrame([totais])], ignore_index=True)

            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                df_exp.to_excel(writer, sheet_name=modo["titulo"], index=False)
                ws = writer.sheets[modo["titulo"]]
                for idx, col in enumerate(df_exp.columns, start=1):
                    letra = ws.cell(row=1, column=idx).column_letter
                    ws.column_dimensions[letra].width = max(
                        len(str(col)) + 2,
                        self.LARGURAS.get(col, 80) // 7)
                    if col in COLS_NUMERICAS:
                        for cel in ws[letra][1:]:
                            cel.number_format = '#,##0.00'

            messagebox.showinfo("Exportação Concluída",
                                f"Arquivo salvo com sucesso em:\n\n{path}")
        except Exception as exc:
            messagebox.showerror("Erro ao Salvar",
                                 f"Não foi possível gravar o arquivo:\n{exc}")


# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    root = tk.Tk()
    app = AppTitulos(root)
    root.mainloop()
