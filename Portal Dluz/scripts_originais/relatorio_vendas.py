#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
import threading
import firebirdsql
import pandas as pd
import warnings

warnings.filterwarnings('ignore', message='pandas only supports SQLAlchemy')

# ═══════════════════════════════════════════════════════════════
#  CONFIGURAÇÃO MULTI-EMPRESAS
# ═══════════════════════════════════════════════════════════════
EMPRESAS = {
    "Dluz Elétrica (Eraldo T. de Oliveira Ltda)": {
        "HOST": "192.168.0.41",
        "DATABASE": r"D:\DLUZR\BANCO\DLUZR25.FB",
        "PREFIXO": "DluzEletrica"
    },
    "Cor e Cia (Tanan Mat. de Construção Ltda)": {
        "HOST": "192.168.0.200",
        "DATABASE": r"D:\CorCiaR\BANCO\CORCIAR25.FB",
        "PREFIXO": "CorECia"
    },
    "Dluz Iluminação (Dluz Iluminação Ltda)": {
        "HOST": "192.168.0.250",
        "DATABASE": r"D:\ILUMIR\BANCO\ILUMIR.FB",
        "PREFIXO": "DluzIluminacao"
    }
}

USER     = 'SYSDBA'
PASSWORD = 'masterkey'
PORT     = 3050
CHARSET  = 'WIN1252'

# ═══════════════════════════════════════════════════════════════
#  RELATÓRIO 1 — VENDAS (PDV), item a item
# ═══════════════════════════════════════════════════════════════
SQL_VENDAS = """
SELECT
    CAST(c.DATAPEDIDO AS DATE)                                AS "DATA",
    c.CODPEDIDO                                               AS "NO PEDIDO",
    i.CODPROD                                                 AS "CODIGO PRODUTO",
    TRIM(p.DESCRICAO)                                         AS "PRODUTO",
    i.QUANTIDADE                                              AS "QUANTIDADE",
    ROUND(i.QUANTIDADE * i.PRECOUNIT, 2)                      AS "VALOR BRUTO",
    ROUND(i.DESCONTOVLR, 2)                                   AS "DESCONTO ITEM",
    ROUND(c.DESCONTOVLR, 2)                                   AS "DESCONTO PEDIDO",
    COALESCE(CAST(c.CODVENDEDOR AS VARCHAR(10)), '????')
        || ' ' || COALESCE(c.USUARIO, 'NAO INFORMADO')        AS "VENDEDOR",
    COALESCE(TRIM(cli.NOME), 'CLIENTE NAO INFORMADO')          AS "CLIENTE",
    CASE
        WHEN COALESCE(e.VLRDINHEIRO, 0)    > 0 THEN 'Dinheiro'
        WHEN COALESCE(e.VLRCARTAO, 0)      > 0 THEN 'Cartão'
        WHEN COALESCE(e.VLRCHEQUE, 0)      > 0 THEN 'Cheque à Vista'
        WHEN COALESCE(e.VLRCHPREDATADO, 0) > 0 THEN 'Cheque Pré-datado'
        WHEN COALESCE(e.VLRCONVENIO, 0)    > 0 THEN 'Convênio'
        WHEN COALESCE(e.VLRRECEBER, 0)     > 0 THEN 'Contas a Receber'
        ELSE 'Outro'
    END                                                        AS "FORMA DE PAGAMENTO"
FROM PEDIDOI i
JOIN PEDIDOC c      ON c.CODEMPRESA  = i.CODEMPRESA
                   AND c.TIPOPEDIDO  = i.TIPOPEDIDO
                   AND c.CODPEDIDO   = i.CODPEDIDO
JOIN PRODUTO p      ON p.CODPROD     = i.CODPROD
LEFT JOIN CLIENTE cli ON cli.CODCLIENTE = c.CODCLIENTE
LEFT JOIN ENCEFAT e   ON e.CODPEDIDO   = c.CODPEDIDO
                     AND e.TIPOPEDIDO  = c.TIPOPEDIDO
WHERE c.TIPOPEDIDO   = 55
  AND i.CANCELADO    = 'N'
  AND c.DATAPEDIDO   BETWEEN ? AND ?
ORDER BY c.DATAPEDIDO, c.CODPEDIDO, i.CODPROD
"""

COLUNAS_VENDAS = (
    "DATA", "NO PEDIDO", "CODIGO PRODUTO", "PRODUTO", "QUANTIDADE", "VALOR BRUTO",
    "DESCONTO", "VALOR LIQUIDO", "VENDEDOR", "CLIENTE", "FORMA DE PAGAMENTO"
)
COLUNAS_SOMAR_VENDAS = ("QUANTIDADE", "VALOR BRUTO", "DESCONTO", "VALOR LIQUIDO")


def ratear_desconto_pedido(df):
    """
    O desconto 'à vista' é concedido no fechamento do pedido (PEDIDOC.DESCONTOVLR),
    não item a item — por isso PEDIDOI.DESCONTOVLR sozinho fica zerado nessas vendas.
    Rateia o desconto do cabeçalho entre os itens do pedido, proporcional ao valor
    bruto de cada um, e soma ao desconto de item (quando existir) para chegar no
    desconto total e no valor líquido de cada linha.
    """
    bruto_pedido = df.groupby("NO PEDIDO")["VALOR BRUTO"].transform("sum")
    desconto_pedido = df.groupby("NO PEDIDO")["DESCONTO PEDIDO"].transform("first")
    proporcao = df["VALOR BRUTO"] / bruto_pedido.where(bruto_pedido != 0, 1)
    desconto_rateado = (desconto_pedido * proporcao).round(2)

    df["DESCONTO"] = (df["DESCONTO ITEM"] + desconto_rateado).round(2)
    df["VALOR LIQUIDO"] = (df["VALOR BRUTO"] - df["DESCONTO"]).round(2)
    return df.drop(columns=["DESCONTO ITEM", "DESCONTO PEDIDO"])


def buscar_vendas(host, database, dt_ini, dt_fim):
    con = firebirdsql.connect(
        host=host, database=database, port=PORT,
        user=USER, password=PASSWORD, charset=CHARSET
    )
    try:
        cur = con.cursor()
        cur.execute(SQL_VENDAS, (dt_ini, dt_fim))
        colunas = [d[0] for d in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=colunas)
    finally:
        con.close()
    return ratear_desconto_pedido(df)


# ═══════════════════════════════════════════════════════════════
#  RELATÓRIO 2 — CONTAS A RECEBER (mesma query do gerar_dashboard_ats)
# ═══════════════════════════════════════════════════════════════
SQL_RECEBER = """
SELECT
    d.DT_VENCIMENTO AS "VENCIMENTO_RAW", d.DT_EMISSAO AS "EMISSÃO_RAW",
    d.CODDOCTO AS "DOCUMENTO", d.CODCLIENTE AS "CÓD. CLIENTE", c.NOME AS "CLIENTE",
    d.CODVENDEDOR AS "CÓD. VENDEDOR", v.NOME AS "VENDEDOR",
    COALESCE(d.VALORDOCTO, 0) AS "TÍTULO", COALESCE(d.VALORPAGO, 0) AS "PAGO",
    CASE WHEN d.SITUACAO = 1 THEN 'Aberto' WHEN d.SITUACAO = 3 THEN 'Parcial' ELSE CAST(d.SITUACAO AS VARCHAR(10)) END AS "SITUAÇÃO"
FROM DOCUREC d
LEFT JOIN CLIENTE c ON d.CODCLIENTE = c.CODCLIENTE
LEFT JOIN VENDEND v ON d.CODVENDEDOR = v.CODVENDEDOR
WHERE d.SITUACAO IN (1, 3)
ORDER BY c.NOME, d.DT_VENCIMENTO
"""

COLUNAS_RECEBER = (
    "CÓD. CLIENTE", "CLIENTE", "DOCUMENTO", "VENDEDOR", "EMISSÃO", "VENCIMENTO",
    "STATUS", "SITUAÇÃO", "TÍTULO", "PAGO", "SALDO"
)
COLUNAS_SOMAR_RECEBER = ("TÍTULO", "PAGO", "SALDO")


def formatar_codigo(valor):
    """Normaliza o código do cliente para texto, removendo o '.0' que o
    pandas adiciona quando a coluna é lida como float (por causa de nulos)."""
    if pd.isna(valor):
        return ''
    texto = str(valor).strip()
    if texto.endswith('.0'):
        texto = texto[:-2]
    return texto


def formatar_vendedor(codigo, nome):
    nome = '' if pd.isna(nome) else str(nome).strip()
    if nome:
        return nome
    codigo = formatar_codigo(codigo)
    return f"Cód. {codigo}" if codigo else "-"


def classificar_data(venc, hoje):
    """Mesma classificação usada no gerar_dashboard_ats (coluna STATUS)."""
    if pd.isna(venc):
        return 'Sem Vencimento'
    diff_dias = (hoje - venc).days
    if diff_dias < 0:
        return 'A vencer'
    elif diff_dias <= (2 * 365.25):
        return 'Em dia'
    elif diff_dias <= (5 * 365.25):
        return 'Antigo'
    else:
        return 'Prescrito'


def buscar_receber(host, database):
    con = firebirdsql.connect(
        host=host, database=database, port=PORT,
        user=USER, password=PASSWORD, charset=CHARSET
    )
    try:
        df = pd.read_sql(SQL_RECEBER, con)
    finally:
        con.close()

    if df.empty:
        return df

    df["VENCIMENTO"] = pd.to_datetime(df["VENCIMENTO_RAW"])
    df["EMISSÃO"] = pd.to_datetime(df["EMISSÃO_RAW"])
    df["SALDO"] = (df["TÍTULO"] - df["PAGO"]).round(2)
    hoje = pd.Timestamp(datetime.now().date())
    df["STATUS"] = df["VENCIMENTO"].apply(lambda v: classificar_data(v, hoje))
    df["CÓD. CLIENTE"] = df["CÓD. CLIENTE"].apply(formatar_codigo)
    df["VENDEDOR"] = df.apply(lambda r: formatar_vendedor(r["CÓD. VENDEDOR"], r["VENDEDOR"]), axis=1)
    df["EMISSÃO"] = df["EMISSÃO"].dt.strftime("%d/%m/%Y")
    df["VENCIMENTO"] = df["VENCIMENTO"].dt.strftime("%d/%m/%Y")
    return df.drop(columns=["VENCIMENTO_RAW", "EMISSÃO_RAW", "CÓD. VENDEDOR"])


# ═══════════════════════════════════════════════════════════════
#  METADADOS DE CADA MODO — o que muda na interface entre os dois relatórios
# ═══════════════════════════════════════════════════════════════
MODOS = {
    "Vendas (PDV)": {
        "titulo": "📊 Relatório de Vendas — Item a Item (PDV)",
        "usa_data": True,
        "colunas": COLUNAS_VENDAS,
        "colunas_somar": COLUNAS_SOMAR_VENDAS,
        "larguras": {
            "DATA": 85, "NO PEDIDO": 90, "CODIGO PRODUTO": 90, "PRODUTO": 260,
            "QUANTIDADE": 85, "VALOR BRUTO": 90, "DESCONTO": 80, "VALOR LIQUIDO": 95,
            "VENDEDOR": 120, "CLIENTE": 200, "FORMA DE PAGAMENTO": 130
        },
        "alinhar_esquerda": ("PRODUTO", "VENDEDOR", "CLIENTE"),
        "filtros": [
            ("Vendedor", "VENDEDOR"),
            ("Forma Pagto:", "FORMA DE PAGAMENTO"),
        ],
        "totais_col": "PRODUTO",
        "totais_label": "TOTAIS --->",
        "aba_excel": "Vendas",
        "prefixo_arquivo": "RelatorioVendas",
    },
    "Contas a Receber": {
        "titulo": "💰 Contas a Receber — Títulos em Aberto/Parcial",
        "usa_data": False,
        "colunas": COLUNAS_RECEBER,
        "colunas_somar": COLUNAS_SOMAR_RECEBER,
        "larguras": {
            "CÓD. CLIENTE": 90, "CLIENTE": 200, "DOCUMENTO": 100, "VENDEDOR": 130,
            "EMISSÃO": 85, "VENCIMENTO": 85, "STATUS": 95, "SITUAÇÃO": 80,
            "TÍTULO": 95, "PAGO": 95, "SALDO": 95
        },
        "alinhar_esquerda": ("VENDEDOR", "CLIENTE"),
        "filtros": [
            ("Vendedor", "VENDEDOR"),
            ("Status:", "STATUS"),
        ],
        "totais_col": "DOCUMENTO",
        "totais_label": "TOTAIS --->",
        "aba_excel": "Contas a Receber",
        "prefixo_arquivo": "ContasReceber",
    },
}


class AppRelatorioVendas:
    COR_TEXTO = "#2c3e50"
    COR_SUBTEXTO = "#888888"
    COR_HEADER = "#dde3ea"
    COR_OK = "#27ae60"
    COR_AVISO = "#e67e22"
    COR_ERRO = "#c0392b"

    def __init__(self, root):
        self.root = root
        self.root.title("ATS — Relatórios Multi-Empresas")
        self.root.geometry("1300x680")
        self.root.minsize(1050, 550)

        self.modo = "Vendas (PDV)"
        self.df_bruto = None       # dados originais, sem filtro
        self.df_resultado = None   # dados exibidos (após filtros)

        self._aplicar_estilo()
        self._construir_interface()
        self._preencher_datas_padrao()
        self._centralizar()

    def _aplicar_estilo(self):
        st = ttk.Style(self.root)
        try: st.theme_use("clam")
        except Exception: pass
        st.configure("TFrame", background="#f5f5f5")
        st.configure("TLabel", background="#f5f5f5", foreground=self.COR_TEXTO)
        st.configure("TLabelframe", background="#f5f5f5")
        st.configure("TLabelframe.Label", background="#f5f5f5", font=("Helvetica", 9, "bold"), foreground=self.COR_TEXTO)
        st.configure("Treeview", rowheight=24, font=("Helvetica", 9))
        st.configure("Treeview.Heading", font=("Helvetica", 9, "bold"), background=self.COR_HEADER, foreground=self.COR_TEXTO)
        st.map("Treeview", background=[("selected", "#3498db")])
        self.root.configure(background="#f5f5f5")

    def _construir_interface(self):
        main = ttk.Frame(self.root, padding=16)
        main.pack(fill=tk.BOTH, expand=True)

        self.lbl_titulo = ttk.Label(main, text=MODOS[self.modo]["titulo"], font=("Helvetica", 14, "bold"))
        self.lbl_titulo.pack(anchor="w")
        ttk.Separator(main, orient="horizontal").pack(fill="x", pady=(8, 12))

        # Tipo de relatório + Empresa (busca inicial)
        frm_empresa = ttk.Frame(main)
        frm_empresa.pack(fill="x", pady=(0, 10))

        ttk.Label(frm_empresa, text="Relatório:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 10))
        self.cbo_modo = ttk.Combobox(
            frm_empresa, values=list(MODOS.keys()), state="readonly", width=20, font=("Helvetica", 10)
        )
        self.cbo_modo.set(self.modo)
        self.cbo_modo.pack(side="left", padx=(0, 25))
        self.cbo_modo.bind("<<ComboboxSelected>>", self._ao_trocar_modo)

        ttk.Label(frm_empresa, text="Selecione a Empresa:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 10))
        self.cbo_empresa = ttk.Combobox(
            frm_empresa,
            values=["Todas as empresas"] + list(EMPRESAS.keys()),
            state="readonly", width=42, font=("Helvetica", 10)
        )
        self.cbo_empresa.current(0)
        self.cbo_empresa.pack(side="left")

        # Datas (só se aplica ao modo Vendas)
        self.frm_filtros_data = ttk.LabelFrame(main, text="  Intervalo de Datas do Pedido  ", padding=12)
        self.frm_filtros_data.pack(fill="x", pady=(0, 10))

        ttk.Label(self.frm_filtros_data, text="Data Inicial (DD/MM/AAAA):", font=("Helvetica", 9, "bold")).grid(row=0, column=0, padx=(5, 5), sticky="w")
        self.ent_data_ini = ttk.Entry(self.frm_filtros_data, width=15, font=("Helvetica", 10))
        self.ent_data_ini.grid(row=0, column=1, padx=(0, 20), sticky="w")

        ttk.Label(self.frm_filtros_data, text="Data Final (DD/MM/AAAA):", font=("Helvetica", 9, "bold")).grid(row=0, column=2, padx=(5, 5), sticky="w")
        self.ent_data_fim = ttk.Entry(self.frm_filtros_data, width=15, font=("Helvetica", 10))
        self.ent_data_fim.grid(row=0, column=3, padx=(0, 20), sticky="w")

        # Filtros pós-busca (rótulos/colunas mudam conforme o modo)
        self.frm_pos = ttk.LabelFrame(main, text="  Filtros (aplicados sobre os dados já carregados)  ", padding=12)
        frm_pos = self.frm_pos
        frm_pos.pack(fill="x", pady=(0, 10))

        ttk.Label(frm_pos, text="Empresa:", font=("Helvetica", 9, "bold")).grid(row=0, column=0, padx=(5, 5), sticky="w")
        self.cbo_filtro_empresa = ttk.Combobox(frm_pos, state="disabled", width=30, font=("Helvetica", 9))
        self.cbo_filtro_empresa.grid(row=0, column=1, padx=(0, 20), sticky="w")
        self.cbo_filtro_empresa.bind("<<ComboboxSelected>>", lambda e: self._aplicar_filtros())

        self.lbl_filtro2 = ttk.Label(frm_pos, text="Vendedor:", font=("Helvetica", 9, "bold"))
        self.lbl_filtro2.grid(row=0, column=2, padx=(5, 5), sticky="w")
        self.cbo_filtro2 = ttk.Combobox(frm_pos, state="disabled", width=20, font=("Helvetica", 9))
        self.cbo_filtro2.grid(row=0, column=3, padx=(0, 20), sticky="w")
        self.cbo_filtro2.bind("<<ComboboxSelected>>", lambda e: self._aplicar_filtros())

        self.lbl_filtro3 = ttk.Label(frm_pos, text="Forma Pagto:", font=("Helvetica", 9, "bold"))
        self.lbl_filtro3.grid(row=0, column=4, padx=(5, 5), sticky="w")
        self.cbo_filtro3 = ttk.Combobox(frm_pos, state="disabled", width=18, font=("Helvetica", 9))
        self.cbo_filtro3.grid(row=0, column=5, padx=(0, 20), sticky="w")
        self.cbo_filtro3.bind("<<ComboboxSelected>>", lambda e: self._aplicar_filtros())

        self.btn_limpar_filtros = ttk.Button(frm_pos, text="Limpar filtros", command=self._limpar_filtros, state="disabled")
        self.btn_limpar_filtros.grid(row=0, column=6, padx=(0, 5))

        # Ações
        frm_acoes = ttk.Frame(main)
        frm_acoes.pack(fill="x", pady=(0, 10))

        self.btn_buscar = ttk.Button(frm_acoes, text="▶  Buscar no Banco", command=self._iniciar_busca, width=20)
        self.btn_buscar.pack(side="left", padx=(0, 10))

        self.btn_exportar = ttk.Button(frm_acoes, text="📊  Exportar para Excel", command=self._exportar_excel, state="disabled", width=22)
        self.btn_exportar.pack(side="left", padx=(0, 10))

        self.progress = ttk.Progressbar(frm_acoes, mode="indeterminate", length=150)
        self.lbl_status = ttk.Label(frm_acoes, text="Selecione a empresa, insira as datas e clique em Buscar.", foreground=self.COR_SUBTEXTO)
        self.lbl_status.pack(side="left", padx=(5, 0))

        # Tabela
        frm_tree = ttk.Frame(main)
        frm_tree.pack(fill=tk.BOTH, expand=True)

        self.tree = ttk.Treeview(frm_tree, show="headings")
        self.tree.tag_configure("total", font=("Helvetica", 9, "bold"), background="#e8f4f8")

        vsb = ttk.Scrollbar(frm_tree, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(frm_tree, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self.tree.pack(fill=tk.BOTH, expand=True)

        self._configurar_colunas_tabela()

    def _configurar_colunas_tabela(self):
        """(Re)monta as colunas da Treeview conforme o modo atual."""
        cfg = MODOS[self.modo]
        self.tree["columns"] = cfg["colunas"]
        for col in cfg["colunas"]:
            anc = "w" if col in cfg["alinhar_esquerda"] else "center"
            self.tree.heading(col, text=col)
            self.tree.column(col, width=cfg["larguras"].get(col, 90), anchor=anc, minwidth=60)

    def _ao_trocar_modo(self, event=None):
        novo_modo = self.cbo_modo.get()
        if novo_modo == self.modo:
            return
        self.modo = novo_modo
        cfg = MODOS[self.modo]

        self.lbl_titulo.config(text=cfg["titulo"])
        rotulo2, _ = cfg["filtros"][0]
        rotulo3, _ = cfg["filtros"][1]
        self.lbl_filtro2.config(text=f"{rotulo2}:" if not rotulo2.endswith(":") else rotulo2)
        self.lbl_filtro3.config(text=rotulo3 if rotulo3.endswith(":") else f"{rotulo3}:")

        if cfg["usa_data"]:
            self.frm_filtros_data.pack(fill="x", pady=(0, 10), before=self.frm_pos)
        else:
            self.frm_filtros_data.pack_forget()

        # Limpa dados e tabela ao trocar de relatório — evita misturar colunas
        self.df_bruto = None
        self.df_resultado = None
        self.tree.delete(*self.tree.get_children())
        self._configurar_colunas_tabela()
        self.cbo_filtro_empresa.configure(values=[], state="disabled")
        self.cbo_filtro2.configure(values=[], state="disabled")
        self.cbo_filtro3.configure(values=[], state="disabled")
        self.btn_limpar_filtros.state(["disabled"])
        self.btn_exportar.state(["disabled"])
        self.lbl_status.config(text="Selecione a empresa e clique em Buscar.", foreground=self.COR_SUBTEXTO)

    def _preencher_datas_padrao(self):
        hoje_pt = datetime.now().strftime("%d/%m/%Y")
        self.ent_data_ini.insert(0, hoje_pt)
        self.ent_data_fim.insert(0, hoje_pt)

    # ─────────────────────────────────────────────────────────
    #  BUSCA NO BANCO
    # ─────────────────────────────────────────────────────────
    def _iniciar_busca(self):
        cfg = MODOS[self.modo]
        empresa_selecionada = self.cbo_empresa.get()

        dt_ini = dt_fim = None
        if cfg["usa_data"]:
            data_ini_raw = self.ent_data_ini.get().strip()
            data_fim_raw = self.ent_data_fim.get().strip()
            try:
                dt_ini = datetime.strptime(data_ini_raw, "%d/%m/%Y").strftime("%Y-%m-%d")
                dt_fim = datetime.strptime(data_fim_raw, "%d/%m/%Y").strftime("%Y-%m-%d")
            except ValueError:
                messagebox.showerror("Formato Incorreto", "Digite as datas no formato DD/MM/AAAA.")
                return

        self.btn_buscar.state(["disabled"])
        self.btn_exportar.state(["disabled"])
        self.cbo_empresa.state(["disabled"])
        self.cbo_modo.state(["disabled"])
        self.progress.pack(side="left", padx=10, before=self.lbl_status)
        self.progress.start(10)
        self.lbl_status.config(text="Conectando...", foreground=self.COR_AVISO)

        threading.Thread(
            target=self._executar_busca_thread,
            args=(dt_ini, dt_fim, empresa_selecionada),
            daemon=True
        ).start()

    def _executar_busca_thread(self, dt_ini, dt_fim, empresa_selecionada):
        try:
            buscar_fn = buscar_vendas if self.modo == "Vendas (PDV)" else buscar_receber
            empresas_alvo = EMPRESAS.items() if empresa_selecionada == "Todas as empresas" else [(empresa_selecionada, EMPRESAS[empresa_selecionada])]

            partes = []
            for nome, dados in empresas_alvo:
                if dt_ini is not None:
                    df_emp = buscar_fn(dados["HOST"], dados["DATABASE"], dt_ini, dt_fim)
                else:
                    df_emp = buscar_fn(dados["HOST"], dados["DATABASE"])
                if not df_emp.empty:
                    df_emp.insert(0, "EMPRESA", nome)
                    partes.append(df_emp)
            df = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()

            self.df_bruto = df
            self.root.after(0, self._pos_busca_sucesso)
        except Exception as exc:
            self.root.after(0, self._pos_busca_erro, str(exc))

    def _opcoes_filtro(self, coluna):
        """Lista ordenada e sem nulos/mistura de tipos para popular um combobox de filtro."""
        valores = self.df_bruto[coluna].dropna().astype(str).unique().tolist()
        return sorted(valores)

    def _pos_busca_sucesso(self):
        self.progress.stop()
        self.progress.pack_forget()
        self.btn_buscar.state(["!disabled"])
        self.cbo_empresa.state(["!disabled"])
        self.cbo_modo.state(["!disabled"])

        if self.df_bruto is None or self.df_bruto.empty:
            self.lbl_status.config(text="⚠️ Nenhum registro encontrado.", foreground=self.COR_AVISO)
            self.tree.delete(*self.tree.get_children())
            self.btn_exportar.state(["disabled"])
            return

        cfg = MODOS[self.modo]
        _, col2 = cfg["filtros"][0]
        _, col3 = cfg["filtros"][1]

        empresas_disp = ["Todas"] + self._opcoes_filtro("EMPRESA")
        self.cbo_filtro_empresa.configure(values=empresas_disp, state="readonly")
        self.cbo_filtro_empresa.set("Todas")

        opcoes2 = ["Todos"] + self._opcoes_filtro(col2)
        self.cbo_filtro2.configure(values=opcoes2, state="readonly")
        self.cbo_filtro2.set("Todos")

        opcoes3 = ["Todos"] + self._opcoes_filtro(col3)
        self.cbo_filtro3.configure(values=opcoes3, state="readonly")
        self.cbo_filtro3.set("Todos")

        self.btn_limpar_filtros.state(["!disabled"])

        self._aplicar_filtros()

    def _pos_busca_erro(self, msg_erro):
        self.progress.stop()
        self.progress.pack_forget()
        self.btn_buscar.state(["!disabled"])
        self.cbo_empresa.state(["!disabled"])
        self.cbo_modo.state(["!disabled"])
        self.lbl_status.config(text="❌ Erro na consulta.", foreground=self.COR_ERRO)
        messagebox.showerror("Erro de Banco de Dados", f"Falha ao conectar ou executar no Firebird:\n\n{msg_erro}")

    # ─────────────────────────────────────────────────────────
    #  FILTROS PÓS-BUSCA (sem nova consulta ao banco)
    # ─────────────────────────────────────────────────────────
    def _aplicar_filtros(self):
        if self.df_bruto is None or self.df_bruto.empty:
            return

        cfg = MODOS[self.modo]
        _, col2 = cfg["filtros"][0]
        _, col3 = cfg["filtros"][1]

        df = self.df_bruto.copy()

        empresa_f = self.cbo_filtro_empresa.get()
        if empresa_f and empresa_f != "Todas":
            df = df[df["EMPRESA"] == empresa_f]

        filtro2_v = self.cbo_filtro2.get()
        if filtro2_v and filtro2_v != "Todos":
            df = df[df[col2] == filtro2_v]

        filtro3_v = self.cbo_filtro3.get()
        if filtro3_v and filtro3_v != "Todos":
            df = df[df[col3] == filtro3_v]

        df = df.reset_index(drop=True)
        if self.modo == "Vendas (PDV)":
            df["DATA"] = pd.to_datetime(df["DATA"]).dt.strftime("%d/%m/%Y")

        # linha de totais
        totais_col = cfg["totais_col"]
        linha_totais = {col: "" for col in df.columns}
        linha_totais[totais_col] = cfg["totais_label"]
        for col in cfg["colunas_somar"]:
            linha_totais[col] = df[col].sum()
        df_com_totais = pd.concat([df, pd.DataFrame([linha_totais])], ignore_index=True)

        self.df_resultado = df_com_totais
        self._atualizar_tabela(df.shape[0])

    def _atualizar_tabela(self, qtd_registros):
        self.tree.delete(*self.tree.get_children())

        if self.df_resultado is None or self.df_resultado.empty:
            self.btn_exportar.state(["disabled"])
            self.lbl_status.config(text="⚠️ Nenhum registro para os filtros selecionados.", foreground=self.COR_AVISO)
            return

        cfg = MODOS[self.modo]
        colunas = cfg["colunas"]
        colunas_somar = cfg["colunas_somar"]
        totais_col = cfg["totais_col"]
        totais_label = cfg["totais_label"]

        for _, row in self.df_resultado.iterrows():
            valores = []
            for col in colunas:
                val = row[col]
                if col in colunas_somar:
                    valores.append(f"{float(val):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
                else:
                    valores.append("" if pd.isna(val) else str(val))
            tag = ("total",) if row[totais_col] == totais_label else ()
            self.tree.insert("", "end", values=valores, tags=tag)

        self.btn_exportar.state(["!disabled"])
        self.lbl_status.config(text=f"✔ {qtd_registros} itens exibidos.", foreground=self.COR_OK)

    def _limpar_filtros(self):
        self.cbo_filtro_empresa.set("Todas")
        self.cbo_filtro2.set("Todos")
        self.cbo_filtro3.set("Todos")
        self._aplicar_filtros()

    # ─────────────────────────────────────────────────────────
    #  EXPORTAÇÃO
    # ─────────────────────────────────────────────────────────
    def _exportar_excel(self):
        if self.df_resultado is None or self.df_resultado.empty:
            return

        cfg = MODOS[self.modo]
        empresa_f = self.cbo_filtro_empresa.get() or "Todas"

        if cfg["usa_data"]:
            data_ini_raw = self.ent_data_ini.get().replace("/", "")
            data_fim_raw = self.ent_data_fim.get().replace("/", "")
            nome_sugerido = f"{cfg['prefixo_arquivo']}_{empresa_f.replace(' ', '')}_{data_ini_raw}_a_{data_fim_raw}.xlsx"
        else:
            agora = datetime.now().strftime("%Y%m%d_%H%M")
            nome_sugerido = f"{cfg['prefixo_arquivo']}_{empresa_f.replace(' ', '')}_{agora}.xlsx"

        path = filedialog.asksaveasfilename(
            title="Salvar como Planilha Excel",
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")],
            initialfile=nome_sugerido
        )
        if not path:
            return

        try:
            self.df_resultado.to_excel(path, sheet_name=cfg["aba_excel"], index=False, engine='openpyxl')
            messagebox.showinfo("Exportação Concluída", f"O relatório foi salvo em:\n\n{path}")
        except Exception as exc:
            messagebox.showerror("Erro ao Salvar", f"Não foi possível gravar o arquivo:\n{exc}")

    def _centralizar(self):
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        ww, wh = self.root.winfo_width(), self.root.winfo_height()
        self.root.geometry(f"{ww}x{wh}+{(sw-ww)//2}+{(sh-wh)//2}")


if __name__ == "__main__":
    root = tk.Tk()
    app = AppRelatorioVendas(root)
    root.mainloop()
