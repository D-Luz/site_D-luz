#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ATS — Somatório do PDV  ·  v10
==============================
Novidades em relação à v9:
  • Pix (carteira digital "Pix Offline") incluído em CART. DIG. e em
    Vendas a vista. O valor não vem da ENCEFAT: fica em
    PAGAMENTODIGITAL.VALOR (STATUS = 2, pago). Só existe nas bases já
    atualizadas (Elétrica e Iluminação); na Cor e Cia a tabela existe, mas
    vazia, e nada muda.
  • Campos de data com máscara: as barras são inseridas automaticamente
    enquanto se digita (ex.: 01062026 -> 01/06/2026).

Da v9:
  • Identidade visual Grupo Dluz: base creme, grafite profundo e acento
    dourado/âmbar; números em fonte monoespaçada; totais em destaque.
  • Busca paralela: as três empresas são consultadas ao mesmo tempo.
  • Tolerância a falha parcial: se uma empresa estiver fora do ar, as
    demais ainda retornam (com aviso de quais falharam).
  • Ordenação por clique no cabeçalho da coluna.
  • Linhas zebradas para leitura confortável.
  • Atalhos: Enter = buscar · Ctrl+E = exportar.
  • Exportação Excel com larguras de coluna ajustadas e formato numérico BR.
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
#  CONFIGURAÇÃO MULTI-EMPRESAS
# ═══════════════════════════════════════════════════════════════
EMPRESAS = {
    "Dluz Elétrica":   {"HOST": "192.168.0.41",  "DATABASE": r"D:\DLUZR\BANCO\DLUZR25.FB",    "PREFIXO": "DluzEletrica"},
    "Cor e Cia":       {"HOST": "192.168.0.200", "DATABASE": r"D:\CorCiaR\BANCO\CORCIAR25.FB", "PREFIXO": "CorECia"},
    "Dluz Iluminação": {"HOST": "192.168.0.250", "DATABASE": r"D:\ILUMIR\BANCO\ILUMIR.FB",     "PREFIXO": "DluzIluminacao"},
}

USER     = 'SYSDBA'
PASSWORD = 'masterkey'
PORT     = 3050
CHARSET  = 'WIN1252'
TODAS    = "Todas as empresas"

SQL_PDV = """
SELECT
    c.CODPEDIDO                                              AS "NO PDV",
    c.DATAPEDIDO                                             AS "DATA",
    cli.NOME                                                 AS "CLIENTE",
    c.TOTALPEDIDO                                            AS "TOTAL PDV",
    COALESCE(e.VLRCHEQUE,      0)                            AS "CH VISTA",
    COALESCE(e.VLRCHPREDATADO, 0)                            AS "CH PRAZO",
    COALESCE(e.VLRCONVENIO,    0)                            AS "CONVENIOS",
    COALESCE(e.VLRDINHEIRO,    0)                            AS "DINHEIRO",
    (
        COALESCE(e.VLRDINHEIRO,    0) +
        COALESCE(e.VLRCHPREDATADO, 0) +
        COALESCE(e.VLRCHEQUE,      0) +
        COALESCE(e.VLRCONVENIO,    0) +
        COALESCE(pd.VLRPIX,        0)
    )                                                        AS "Vendas a vista",
    COALESCE(e.VLRRECEBER,     0)                            AS "CONTAS REC",
    (
        COALESCE(e.VLRCARTAO,      0) +
        COALESCE(pd.VLRPIX,        0)
    )                                                        AS "CART. DIG.",
    COALESCE(e.VLRTROCO,       0)                            AS "TROCO",
    COALESCE(e.VLRACRESCIMO,   0)                            AS "ACRESC.",
    COALESCE(e.TROCA,          0)                            AS "TROCA",
    COALESCE(CAST(c.CODVENDEDOR AS VARCHAR(10)), '') ||
        ' ' || COALESCE(TRIM(v.NOME), '')                    AS "VENDEDOR",
    c.CODORC                                                 AS "ORC."
FROM PEDIDOC c
LEFT JOIN CLIENTE cli ON c.CODCLIENTE  = cli.CODCLIENTE
LEFT JOIN ENCEFAT e   ON c.CODPEDIDO   = e.CODPEDIDO
                     AND c.TIPOPEDIDO  = e.TIPOPEDIDO
LEFT JOIN VENDEND v   ON c.CODVENDEDOR = v.CODVENDEDOR
LEFT JOIN (
    SELECT TIPOPEDIDO, CODPEDIDO, SUM(VALOR) AS VLRPIX
    FROM PAGAMENTODIGITAL
    WHERE STATUS = 2
    GROUP BY TIPOPEDIDO, CODPEDIDO
) pd                  ON c.CODPEDIDO   = pd.CODPEDIDO
                     AND c.TIPOPEDIDO  = pd.TIPOPEDIDO
WHERE c.TIPOPEDIDO = 55
  AND c.DATAPEDIDO BETWEEN ? AND ?
ORDER BY c.DATAPEDIDO, c.CODPEDIDO
"""

COLS_NUMERICAS = [
    "TOTAL PDV", "CH VISTA", "CH PRAZO", "CONVENIOS",
    "DINHEIRO", "Vendas a vista", "CONTAS REC", "CART. DIG.",
    "TROCO", "ACRESC.", "TROCA",
]


def consultar_empresa(nome, cfg, dt_ini, dt_fim):
    """Executa a consulta em uma empresa e devolve o DataFrame com coluna EMPRESA."""
    con = firebirdsql.connect(
        host=cfg["HOST"], database=cfg["DATABASE"],
        port=PORT, user=USER, password=PASSWORD, charset=CHARSET,
    )
    try:
        df = pd.read_sql(SQL_PDV, con, params=(dt_ini, dt_fim))
    finally:
        con.close()
    if not df.empty:
        df.insert(0, "EMPRESA", nome)
    return df


# ═══════════════════════════════════════════════════════════════
#  DESIGN — identidade Grupo Dluz
#  Base creme quente · grafite profundo · acento dourado/âmbar
#  Números em mono · saldos/totais em destaque · menos cor, mais hierarquia
# ═══════════════════════════════════════════════════════════════
class Tema:
    BG          = "#f7f3ea"   # creme quente (fundo geral)
    SURFACE     = "#fffdf8"   # quase-branco quente (cartões / tabela)
    BORDA       = "#e9e0d0"   # borda suave
    GRAFITE     = "#241f18"   # grafite profundo (cabeçalhos)
    TEXTO       = "#2c2620"   # texto principal
    TEXTO_SUAVE = "#7c7468"   # texto secundário
    AMBAR       = "#946517"   # acento dourado/âmbar
    AMBAR_FORTE = "#74500f"   # acento em hover/press
    AMBAR_SOFT  = "#f0e5cd"   # fundo de destaque suave
    VERMELHO    = "#b3261e"   # erros / saldos devedores
    VERDE       = "#3d6a3d"   # confirmações
    ZEBRA       = "#faf6ec"   # linha alternada
    TOTAL_BG    = "#efe6d2"   # linha de totais (âmbar bem suave)
    SELECAO     = "#e6dabf"   # linha selecionada

    @staticmethod
    def fontes(root):
        """Escolhe famílias disponíveis no sistema, com fallbacks."""
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
class AppRelatorioPDV:

    COLUNAS = (
        "EMPRESA", "NO PDV", "DATA", "CLIENTE",
        "TOTAL PDV", "CH VISTA", "CH PRAZO", "CONVENIOS",
        "DINHEIRO", "Vendas a vista", "CONTAS REC", "CART. DIG.", "TROCO",
        "ACRESC.", "TROCA", "VENDEDOR", "ORC.",
    )
    LARGURAS = {
        "EMPRESA": 125, "NO PDV": 65, "DATA": 82, "CLIENTE": 160,
        "TOTAL PDV": 88, "CH VISTA": 75, "CH PRAZO": 75,
        "CONVENIOS": 80, "DINHEIRO": 80, "Vendas a vista": 100, "CONTAS REC": 85,
        "CART. DIG.": 82, "TROCO": 68, "ACRESC.": 68, "TROCA": 65,
        "VENDEDOR": 130, "ORC.": 58,
    }

    def __init__(self, root):
        self.root = root
        self.root.title("ATS — Somatório do PDV · v10")
        self.root.geometry("1320x740")
        self.root.minsize(980, 560)

        self.df_resultado = None            # DataFrame bruto (sem linha de totais)
        self.ordenacao = {}                 # coluna -> bool (ascendente)
        self.v_filtro_empresa  = tk.StringVar(value="Todas")
        self.v_filtro_vendedor = tk.StringVar(value="Todos")

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

        # Botão primário (âmbar) e secundário (contorno)
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
        st.map("Treeview.Heading",
               background=[("active", "#3a332a")])
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

        # Cabeçalho
        cab = ttk.Frame(main)
        cab.pack(fill="x")
        ttk.Label(cab, text="Somatório do Ponto de Venda",
                  style="Titulo.TLabel").pack(side="left")
        ttk.Label(cab, text="ATS · Grupo Dluz", style="Sub.TLabel",
                  font=(self.f_sans, 9)).pack(side="right", anchor="s", pady=(0, 2))
        ttk.Separator(main, orient="horizontal").pack(fill="x", pady=(8, 12))

        # ── Linha 1: Busca ───────────────────────────────────────
        frm_busca = ttk.Frame(main)
        frm_busca.pack(fill="x", pady=(0, 8))

        grp_emp = ttk.LabelFrame(frm_busca, text=" EMPRESA PARA BUSCA ", padding=8)
        grp_emp.pack(side="left", fill="y", padx=(0, 10))

        self.cbo_empresa_busca = ttk.Combobox(
            grp_emp, values=[TODAS] + list(EMPRESAS.keys()),
            state="readonly", width=24, font=(self.f_sans, 10))
        self.cbo_empresa_busca.current(0)
        self.cbo_empresa_busca.pack()

        grp_datas = ttk.LabelFrame(frm_busca, text=" INTERVALO DE DATAS ", padding=8)
        grp_datas.pack(side="left", fill="y", padx=(0, 10))

        ttk.Label(grp_datas, text="Inicial:").grid(row=0, column=0, sticky="w")
        self.v_data_ini = tk.StringVar()
        self.v_data_fim = tk.StringVar()
        self.ent_data_ini = ttk.Entry(grp_datas, width=12, font=(self.f_mono, 10),
                                      textvariable=self.v_data_ini)
        self.ent_data_ini.grid(row=0, column=1, padx=(4, 14))
        ttk.Label(grp_datas, text="Final:").grid(row=0, column=2, sticky="w")
        self.ent_data_fim = ttk.Entry(grp_datas, width=12, font=(self.f_mono, 10),
                                      textvariable=self.v_data_fim)
        self.ent_data_fim.grid(row=0, column=3, padx=(4, 0))
        self._mascara_data(self.ent_data_ini, self.v_data_ini)
        self._mascara_data(self.ent_data_fim, self.v_data_fim)

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
            text="Selecione a empresa e o período, depois clique em Buscar  (Enter).",
            style="Sub.TLabel")
        self.lbl_status.pack(side="left")

        # ── Linha 3: Filtros de visualização (pós-busca) ─────────
        grp_filtros = ttk.LabelFrame(main, text=" FILTROS DE VISUALIZAÇÃO ", padding=8)
        grp_filtros.pack(fill="x", pady=(0, 10))

        ttk.Label(grp_filtros, text="Empresa:",
                  font=(self.f_sans, 9, "bold")).pack(side="left", padx=(0, 4))
        self.cbo_filtro_empresa = ttk.Combobox(
            grp_filtros, textvariable=self.v_filtro_empresa,
            state="disabled", width=20, font=(self.f_sans, 9))
        self.cbo_filtro_empresa.pack(side="left", padx=(0, 18))
        self.cbo_filtro_empresa.bind("<<ComboboxSelected>>", self._on_filtro_empresa)

        ttk.Label(grp_filtros, text="Vendedor:",
                  font=(self.f_sans, 9, "bold")).pack(side="left", padx=(0, 4))
        self.cbo_filtro_vendedor = ttk.Combobox(
            grp_filtros, textvariable=self.v_filtro_vendedor,
            state="disabled", width=30, font=(self.f_sans, 9))
        self.cbo_filtro_vendedor.pack(side="left", padx=(0, 14))
        self.cbo_filtro_vendedor.bind("<<ComboboxSelected>>",
                                      lambda e: self._aplicar_filtros())

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
            anc = "w" if col in ("CLIENTE", "VENDEDOR", "EMPRESA") else "e" \
                  if col in COLS_NUMERICAS else "center"
            self.tree.heading(col, text=col,
                              command=lambda c=col: self._ordenar_por(c))
            self.tree.column(col,
                             width=self.LARGURAS.get(col, 80),
                             anchor=anc, minwidth=45)

        vsb.pack(side="right",  fill="y")
        hsb.pack(side="bottom", fill="x")
        self.tree.pack(fill=tk.BOTH, expand=True)

    # ── Helpers ──────────────────────────────────────────────────
    def _atalhos(self):
        self.root.bind("<Return>",    lambda e: self._iniciar_busca())
        self.root.bind("<Control-e>", lambda e: self._exportar_excel())

    @staticmethod
    def _mascara_data(entry, var):
        """Formata o campo como DD/MM/AAAA enquanto o usuário digita.

        Aceita só dígitos (descarta o resto), insere as barras sozinho, mantém
        o cursor no lugar e, ao apagar, deixa a barra sumir em vez de recolocá-la.
        """
        estado = {"prev": "", "busy": False}

        def formatar(digitos, apagando):
            out = digitos[:2]
            if len(digitos) > 2:
                out += "/" + digitos[2:4]
            if len(digitos) > 4:
                out += "/" + digitos[4:8]
            if not apagando and len(digitos) in (2, 4):
                out += "/"
            return out

        def ao_escrever(*_):
            if estado["busy"]:
                return
            atual = var.get()
            apagando = len(atual) < len(estado["prev"])
            try:
                cursor = entry.index("insert")
            except tk.TclError:
                cursor = len(atual)

            digitos_antes = sum(c.isdigit() for c in atual[:cursor])
            digitos = "".join(c for c in atual if c.isdigit())[:8]
            novo = formatar(digitos, apagando)

            if novo != atual:
                estado["busy"] = True
                try:
                    var.set(novo)
                    # reposiciona o cursor depois do mesmo nº de dígitos
                    pos, vistos = 0, 0
                    while pos < len(novo) and vistos < min(digitos_antes, len(digitos)):
                        vistos += novo[pos].isdigit()
                        pos += 1
                    if (not apagando and pos < len(novo) and novo[pos] == "/"
                            and pos == len(novo) - 1):
                        pos += 1            # logo após a barra automática
                    entry.icursor(pos)
                finally:
                    estado["busy"] = False
            estado["prev"] = novo

        var.trace_add("write", ao_escrever)

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
        """Formata número no padrão brasileiro (1.234,56)."""
        try:
            return (f"{float(val):,.2f}"
                    .replace(",", "X")
                    .replace(".", ",")
                    .replace("X", "."))
        except (ValueError, TypeError):
            return "0,00"

    def _df_filtrado(self):
        """Aplica os filtros de visualização ativos sobre o resultado bruto."""
        df = self.df_resultado
        if df is None:
            return None
        emp  = self.v_filtro_empresa.get()
        vend = self.v_filtro_vendedor.get()
        if emp and emp != "Todas":
            df = df[df["EMPRESA"] == emp]
        if vend and vend != "Todos":
            df = df[df["VENDEDOR"] == vend]
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

        empresa_sel = self.cbo_empresa_busca.get()

        self.btn_buscar.state(["disabled"])
        self.btn_exportar.state(["disabled"])
        self.cbo_empresa_busca.state(["disabled"])
        self.cbo_filtro_empresa.config(state="disabled")
        self.cbo_filtro_vendedor.config(state="disabled")
        self.progress.pack(side="left", padx=(8, 0), before=self.lbl_status)
        self.progress.start(10)
        self._set_status("Consultando bancos de dados…", Tema.AMBAR)

        threading.Thread(
            target=self._thread_busca,
            args=(dt_ini.strftime("%Y-%m-%d"), dt_fim.strftime("%Y-%m-%d"),
                  empresa_sel),
            daemon=True,
        ).start()

    def _thread_busca(self, dt_ini, dt_fim, empresa_sel):
        alvos = (list(EMPRESAS.items()) if empresa_sel == TODAS
                 else [(empresa_sel, EMPRESAS[empresa_sel])])

        frames, falhas = [], []
        with ThreadPoolExecutor(max_workers=len(alvos)) as pool:
            futuros = {
                pool.submit(consultar_empresa, nome, cfg, dt_ini, dt_fim): nome
                for nome, cfg in alvos
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
            df_final = df_final.sort_values(
                ["DATA", "EMPRESA", "NO PDV"], ignore_index=True)
            df_final["DATA"] = (pd.to_datetime(df_final["DATA"])
                                .dt.strftime("%d/%m/%Y"))
        else:
            df_final = pd.DataFrame(columns=list(self.COLUNAS))

        self.df_resultado = df_final
        self.root.after(0, self._pos_busca_ok, falhas)

    def _destravar_controles(self):
        self.progress.stop()
        self.progress.pack_forget()
        self.btn_buscar.state(["!disabled"])
        self.cbo_empresa_busca.state(["!disabled"])

    def _pos_busca_ok(self, falhas=()):
        self._destravar_controles()

        df = self.df_resultado
        if df is None or df.empty:
            self.btn_exportar.state(["disabled"])
            self._set_status(
                "Nenhum registro encontrado para este período.", Tema.AMBAR_FORTE)
            return

        empresas = ["Todas"] + sorted(df["EMPRESA"].unique().tolist())
        self.cbo_filtro_empresa.config(values=empresas, state="readonly")
        self.v_filtro_empresa.set("Todas")

        self._atualizar_combo_vendedor(df)
        self.cbo_filtro_vendedor.config(state="readonly")

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
    def _atualizar_combo_vendedor(self, df_base):
        """Repopula o combo de vendedor e reseta a seleção para 'Todos'."""
        vendedores = ["Todos"] + sorted(
            str(v) for v in df_base["VENDEDOR"].dropna().unique()
            if str(v).strip()
        )
        self.cbo_filtro_vendedor.config(values=vendedores)
        self.v_filtro_vendedor.set("Todos")

    def _on_filtro_empresa(self, event=None):
        if self.df_resultado is None:
            return
        emp = self.v_filtro_empresa.get()
        df_base = (self.df_resultado
                   if emp == "Todas"
                   else self.df_resultado[self.df_resultado["EMPRESA"] == emp])
        self._atualizar_combo_vendedor(df_base)
        self._aplicar_filtros()

    def _limpar_filtros(self):
        if self.df_resultado is None:
            return
        self.v_filtro_empresa.set("Todas")
        self._atualizar_combo_vendedor(self.df_resultado)
        self._aplicar_filtros()

    def _aplicar_filtros(self, event=None):
        df = self._df_filtrado()
        if df is None:
            return

        self._preencher_tabela(df)

        emp, vend = self.v_filtro_empresa.get(), self.v_filtro_vendedor.get()
        sufixo = ""
        if emp  != "Todas": sufixo += f"  ·  {emp}"
        if vend != "Todos": sufixo += f"  ·  {vend}"
        self._set_status(
            f"{len(df)} registro(s) exibido(s){sufixo}", Tema.VERDE)

    # ── Ordenação ────────────────────────────────────────────────
    def _ordenar_por(self, col):
        df = self._df_filtrado()
        if df is None or df.empty or col not in df.columns:
            return
        asc = self.ordenacao.get(col, False)
        self.ordenacao = {col: not asc}

        if col == "DATA":
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

        # Linha de totais — recalculada a cada filtragem
        totais = {c: "" for c in self.COLUNAS}
        totais["CLIENTE"] = "TOTAIS"
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

        ini = self.ent_data_ini.get().replace("/", "")
        fim = self.ent_data_fim.get().replace("/", "")
        path = filedialog.asksaveasfilename(
            title="Salvar como Planilha Excel",
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")],
            initialfile=f"SomatorioPDV_{ini}_a_{fim}.xlsx",
        )
        if not path:
            return

        try:
            totais = {c: "" for c in df.columns}
            totais["CLIENTE"] = "TOTAIS"
            for col in COLS_NUMERICAS:
                if col in df.columns:
                    totais[col] = df[col].sum()

            df_exp = pd.concat([df, pd.DataFrame([totais])], ignore_index=True)

            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                df_exp.to_excel(writer, sheet_name="Somatório PDV", index=False)
                ws = writer.sheets["Somatório PDV"]

                # Larguras de coluna + formato numérico BR
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
    app = AppRelatorioPDV(root)
    root.mainloop()
