#!/usr/bin/env python3
"""Consolida extratos bancários em PDF (Bradesco, Banco do Brasil, Caixa,
Inter e Stone) em um Excel único, com uma aba por empresa, transações em
sequência cronológica e saldo consolidado como se fosse uma conta só.

Uso:
    python3 consolidar_extratos.py arquivo1.pdf pasta/ ... [-o saida.xlsx]

A empresa é detectada pelo nome do arquivo (dluz / ilum / cor) e o banco
pelo conteúdo do PDF.
"""

import argparse
import glob
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime

import pdfplumber
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

# ---------------------------------------------------------------- helpers

MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}

MONEY_RE = re.compile(r"^-?[\d.]+,\d{2}$")


def br_float(s: str) -> float:
    return float(s.replace(".", "").replace(",", "."))


def br_date(s: str) -> date:
    d, m, y = s.split("/")
    y = int(y)
    if y < 100:
        y += 2000
    return date(y, int(m), int(d))


@dataclass
class Transacao:
    data: date
    banco: str
    descricao: str
    documento: str
    valor: float  # positivo = entrada, negativo = saída


@dataclass
class Extrato:
    banco: str
    conta: str
    saldo_anterior: float
    saldo_disponivel: float          # saldo informado pelo banco
    transacoes: list = field(default_factory=list)
    saldo_final_extrato: float = None  # último saldo impresso no extrato
    avisos: list = field(default_factory=list)  # inconsistências achadas ao ler

    def confere(self):
        calc = round(self.saldo_anterior + sum(t.valor for t in self.transacoes), 2)
        ok = self.saldo_final_extrato is None or abs(calc - self.saldo_final_extrato) < 0.005
        return ok and not self.avisos, calc


# ---------------------------------------------------------------- parsers

def extrair_texto(caminho):
    with pdfplumber.open(caminho) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def parse_bradesco(texto):
    conta = ""
    m = re.search(r"Ag: (\d+) \| CC: ([\d-]+)", texto)
    if m:
        conta = f"Ag {m.group(1)} CC {m.group(2)}"
    disp = 0.0
    m = re.search(r"\d{5} \| [\d-]+ ([\d.]+,\d{2}) [\d.]+,\d{2}", texto)
    if m:
        disp = br_float(m.group(1))

    ext = Extrato("Bradesco", conta, 0.0, disp)
    linhas = texto.splitlines()
    try:
        ini = next(i for i, l in enumerate(linhas)
                   if l.startswith("Data Lançamento"))
    except StopIteration:
        ext.saldo_anterior = disp  # sem lançamentos no período: saldo = disponível
        return ext

    data_atual = None
    pendente = ""          # descrição que antecede a linha de valores
    usou_pendente = False  # última transação veio de descrição pendente
    for linha in linhas[ini + 1:]:
        linha = linha.strip()
        if linha.startswith("Total ") or linha.startswith("Saldos Invest"):
            break
        m = re.match(r"(\d{2}/\d{2}/\d{4}) SALDO ANTERIOR ([\d.]+,\d{2})", linha)
        if m:
            ext.saldo_anterior = br_float(m.group(2))
            continue
        tokens = linha.split()
        if len(tokens) >= 2 and MONEY_RE.match(tokens[-1]) and MONEY_RE.match(tokens[-2]):
            saldo, valor = br_float(tokens.pop()), br_float(tokens.pop())
            data_txt = None
            if tokens and re.match(r"^\d{2}/\d{2}/\d{4}$", tokens[0]):
                data_txt = tokens.pop(0)
            doc = tokens.pop() if tokens and tokens[-1].isdigit() else ""
            desc = " ".join(tokens) or pendente
            usou_pendente = not " ".join(tokens)
            pendente = ""
            if data_txt:
                data_atual = br_date(data_txt)
            ext.transacoes.append(Transacao(data_atual, "Bradesco", desc, doc, valor))
            ext.saldo_final_extrato = saldo
        elif linha:
            if usou_pendente and ext.transacoes:
                ext.transacoes[-1].descricao += f" {linha}"
                usou_pendente = False
            else:
                pendente = linha
    return ext


def parse_caixa(texto):
    m = re.search(r"Conta: ([\d| ]+[\d-]+)", texto)
    conta = m.group(1).replace(" | ", "/") if m else ""
    ext = Extrato("Caixa", conta, 0.0, 0.0)

    m = re.search(r"SALDO ANTERIOR [\d.,]+ ([\d.]+,\d{2}) (C|D)", texto)
    if m:
        ext.saldo_anterior = br_float(m.group(1)) * (1 if m.group(2) == "C" else -1)

    rx = re.compile(r"^(\d{2}/\d{2}/\d{4}) (\d{6}) (.+?) ([\d.]+,\d{2}) (C|D) ([\d.]+,\d{2}) (C|D)$")
    for linha in texto.splitlines():
        m = rx.match(linha.strip())
        if not m:
            continue
        if "SALDO DIA" in m.group(3):
            continue
        valor = br_float(m.group(4)) * (1 if m.group(5) == "C" else -1)
        saldo = br_float(m.group(6)) * (1 if m.group(7) == "C" else -1)
        ext.transacoes.append(Transacao(br_date(m.group(1)), "Caixa",
                                        m.group(3), m.group(2), valor))
        ext.saldo_final_extrato = saldo
    ext.saldo_disponivel = (ext.saldo_final_extrato
                            if ext.saldo_final_extrato is not None
                            else ext.saldo_anterior)
    return ext


def parse_bb(texto):
    m = re.search(r"Conta corrente ([\d-]+)", texto)
    conta = m.group(1) if m else ""
    ext = Extrato("Banco do Brasil", conta, 0.0, 0.0)

    m = re.search(r"Saldo Anterior ([\d.]+,\d{2}) (C|D)", texto)
    if m:
        ext.saldo_anterior = br_float(m.group(1)) * (1 if m.group(2) == "C" else -1)
    # linha "Saldo 1.510,11C" = conta + invest. resgate automático
    m = re.search(r"^Saldo ([\d.]+,\d{2})(C|D)$", texto, re.M)
    if m:
        ext.saldo_disponivel = br_float(m.group(1)) * (1 if m.group(2) == "C" else -1)

    rx = re.compile(
        r"^(\d{2}/\d{2}/\d{4}) (?:(\d{2}/\d{2}/\d{4}) )?(\d{4}) (\d{8}) (.+?)"
        r"(?: ([\d.]+))? ([\d.]+,\d{2}) (C|D)(?: ([\d.]+,\d{2}) (C|D))?$")
    em_lancamentos = False
    for linha in texto.splitlines():
        linha = linha.strip()
        if linha.startswith("Lançamentos"):
            em_lancamentos = True
            continue
        if not em_lancamentos:
            continue
        if linha.startswith(("Invest. Resgate", "LIMITE ESPECIAL", "Saldo de fundos")):
            break
        m = rx.match(linha)
        if m:
            hist = m.group(5).strip()
            if hist in ("Saldo Anterior", "S A L D O"):
                if hist == "S A L D O":
                    ext.saldo_final_extrato = (br_float(m.group(7))
                                               * (1 if m.group(8) == "C" else -1))
                continue
            data = br_date(m.group(2) or m.group(1))
            valor = br_float(m.group(7)) * (1 if m.group(8) == "C" else -1)
            ext.transacoes.append(Transacao(data, "Banco do Brasil", hist,
                                            m.group(6) or "", valor))
        elif linha and "Saldo Anterior" not in linha and ext.transacoes:
            ext.transacoes[-1].descricao += f" — {linha}"
    return ext


def parse_inter(texto):
    m = re.search(r"Conta: ([\d-]+)", texto)
    conta = m.group(1) if m else ""
    ext = Extrato("Inter", conta, 0.0, 0.0)

    m = re.search(r"Saldo total.*?\n(-?)R\$ ([\d.]+,\d{2})", texto)
    if m:
        ext.saldo_disponivel = br_float(m.group(2)) * (-1 if m.group(1) else 1)

    rx_dia = re.compile(r"^(\d{1,2}) de (\w+) de (\d{4}) Saldo do dia")
    rx_txn = re.compile(r"^(.+?) (-?)R\$ ([\d.]+,\d{2}) (-?)R\$ ([\d.]+,\d{2})$")
    data_atual = None
    buffer_desc = ""
    for linha in texto.splitlines():
        linha = linha.strip()
        m = rx_dia.match(linha)
        if m:
            data_atual = date(int(m.group(3)), MESES_PT[m.group(2).lower()], int(m.group(1)))
            buffer_desc = ""
            continue
        if data_atual is None:
            continue
        if linha.startswith(("Fale com a gente", "SAC:", "--- PAGE")):
            continue
        m = rx_txn.match(linha)
        if m and not m.group(1).startswith("R$"):
            valor = br_float(m.group(3)) * (-1 if m.group(2) else 1)
            saldo = br_float(m.group(5)) * (-1 if m.group(4) else 1)
            desc = f"{buffer_desc} {m.group(1)}".strip()
            buffer_desc = ""
            ext.transacoes.append(Transacao(data_atual, "Inter", desc, "", valor))
            ext.saldo_final_extrato = saldo
        elif linha:
            buffer_desc = f"{buffer_desc} {linha}".strip()
    if ext.transacoes:
        primeira = ext.transacoes[0]
        # o Inter não imprime saldo anterior: deriva do 1º "saldo por transação"
        primeiro_saldo = None
        for linha in texto.splitlines():
            m = rx_txn.match(linha.strip())
            if m and not m.group(1).startswith("R$"):
                primeiro_saldo = br_float(m.group(5)) * (-1 if m.group(4) else 1)
                break
        if primeiro_saldo is not None:
            ext.saldo_anterior = round(primeiro_saldo - primeira.valor, 2)
    else:
        ext.saldo_anterior = ext.saldo_disponivel
    return ext


def _stone_blocos(caminho):
    """Lê o extrato da Stone por coordenadas. Cada transação é um bloco de 1 a 5
    linhas coladas (~6pt) em torno da linha da data; blocos distintos ficam a
    ~25pt um do outro. As colunas vêm das posições do cabeçalho de cada página,
    o que separa a contraparte (que pode ficar na mesma linha do valor) da
    descrição, sem depender da ordem do texto."""
    blocos = []
    with pdfplumber.open(caminho) as pdf:
        for pag in pdf.pages:
            palavras = pag.extract_words()
            cab = next((w for w in palavras
                        if w["text"] == "DATA" and w["x0"] < 60), None)
            if cab is None:
                continue
            x = {w["text"]: w["x0"] for w in palavras
                 if abs(w["top"] - cab["top"]) < 3
                 and w["text"] in ("TIPO", "DESCRIÇÃO", "VALOR", "SALDO", "CONTRAPARTE")}
            if len(x) < 5:
                continue
            fim = next((w["top"] for w in palavras
                        if w["text"] == "Informações" and w["top"] > cab["top"]),
                       pag.height)
            col = lambda w: ("tipo" if w["x0"] < x["DESCRIÇÃO"] - 4 and w["x0"] >= x["TIPO"] - 3
                             else "data" if w["x0"] < x["TIPO"] - 3
                             else "desc" if w["x0"] < x["VALOR"] - 3
                             else "valor" if w["x0"] < x["SALDO"] - 8
                             else "saldo" if w["x0"] < x["CONTRAPARTE"] - 8
                             else "contra")
            corpo = sorted((w for w in palavras if cab["top"] + 3 < w["top"] < fim),
                           key=lambda w: (w["top"], w["x0"]))
            linhas = []
            for w in corpo:
                if linhas and abs(w["top"] - linhas[-1][0]) <= 2:
                    linhas[-1][1].append(w)
                else:
                    linhas.append((w["top"], [w]))
            grupo, anterior = [], None
            for top, ws in linhas:
                if anterior is not None and top - anterior > 15:
                    blocos.append((grupo, col))
                    grupo = []
                grupo.append(ws)
                anterior = top
            if grupo:
                blocos.append((grupo, col))
    return blocos


def parse_stone(texto, caminho):
    m = re.search(r"Stone Instituição de Pagamento S\.A\.\s*\n?0001 (\S+)", texto)
    conta = re.sub(r"[^\d]", "", m.group(1)) if m else ""
    if len(conta) > 1:  # o PDF troca o hífen por um glifo especial
        conta = f"{conta[:-1]}-{conta[-1]}"
    ext = Extrato("Stone", conta, 0.0, 0.0)

    txns = []  # (data, descricao, valor, saldo), da mais recente para a mais antiga
    for grupo, col in _stone_blocos(caminho):
        linha_data = next((ws for ws in grupo
                           if re.fullmatch(r"\d{2}/\d{2}/\d{2}", ws[0]["text"])), None)
        if linha_data is None:
            continue
        por_col = lambda nome: [[w["text"] for w in ws if col(w) == nome] for ws in grupo]
        # valor e saldo podem cair 1-2pt abaixo da linha da data: lê do bloco todo
        palavras = [w for ws in grupo for w in ws]
        tipo = next(w["text"] for w in palavras if col(w) == "tipo")
        nums = {}
        for nome in ("valor", "saldo"):
            partes = " ".join(w["text"] for w in palavras if col(w) == nome)
            n = re.search(r"\d[\d.]*,\d{2}", partes)
            nums[nome] = br_float(n.group()) * (-1 if "-" in partes else 1)
        valor = abs(nums["valor"]) * (1 if tipo == "Entrada" else -1)

        desc = [" ".join(p) for p in por_col("desc") if p]
        contra = " ".join(" ".join(p) for p in por_col("contra") if p)
        metodo = desc.pop() if desc and "|" in desc[-1] else ""
        texto_desc = " ".join(desc)
        if contra:
            texto_desc += f" — {contra}"
        if metodo:
            texto_desc += f" ({metodo})"
        txns.append((br_date(linha_data[0]["text"]), texto_desc.strip(),
                     valor, nums["saldo"]))

    # cada saldo = saldo da transação mais antiga seguinte + o valor da própria;
    # se quebrar, alguma transação deixou de ser lida
    for (d, desc, valor, saldo), (_, _, _, saldo_ant) in zip(txns, txns[1:]):
        if abs(saldo_ant + valor - saldo) > 0.005:
            ext.avisos.append(f"saldo não fecha em {d:%d/%m/%Y} ({desc[:40]})")

    txns.reverse()  # Stone lista da mais recente para a mais antiga
    for d, desc, valor, saldo in txns:
        ext.transacoes.append(Transacao(d, "Stone", desc, "", valor))
    if txns:
        ext.saldo_anterior = round(txns[0][3] - txns[0][2], 2)
        ext.saldo_final_extrato = txns[-1][3]
        ext.saldo_disponivel = txns[-1][3]
    return ext


def detectar_banco(texto):
    if "gerenciador.caixa" in texto or "SAC CAIXA" in texto:
        return parse_caixa
    # Stone antes do Inter: contrapartes como "Banco Inter S.A." aparecem no
    # corpo do extrato da Stone, então o Inter exige o cabeçalho da própria conta
    if "Stone Instituição de Pagamento S.A." in texto and "DATA TIPO" in texto:
        return parse_stone
    if "Instituição: Banco Inter" in texto:
        return parse_inter
    if "Dt. balancete" in texto:
        return parse_bb
    if "Invest Fácil" in texto or "Últimos Lançamentos" in texto:
        return parse_bradesco
    return None


EMPRESAS = {
    "dluz": "DLUZ",
    "ilum": "ILUM",
    "cor": "COR",
}
NOMES_EMPRESAS = {
    "DLUZ": "Elétrica D Luz — Eraldo T de Oliveira Ltda",
    "ILUM": "D'Luz Iluminação Ltda",
    "COR": "Cor & Cia — Tanan Materiais de Construção Ltda",
}


def detectar_empresa(nome_arquivo):
    nome = os.path.basename(nome_arquivo).lower()
    for chave, sigla in EMPRESAS.items():
        if chave in nome:
            return sigla
    return None


# ---------------------------------------------------------------- excel

GRAFITE, CREME, SUPERFICIE = "241F18", "F7F3EA", "FFFDF8"
AMBAR, VERMELHO, TEXTO = "946517", "B3261E", "2C2620"
FMT_MOEDA = "#,##0.00;[Red]-#,##0.00"

F_TITULO = Font(name="Georgia", size=14, bold=True, color=GRAFITE)
F_SUB = Font(name="Arial", size=9, color=AMBAR)
F_SECAO = Font(name="Georgia", size=11, bold=True, color=AMBAR)
F_HEADER = Font(name="Arial", size=9, bold=True, color=CREME)
F_TEXTO = Font(name="Arial", size=9, color=TEXTO)
F_NUM = Font(name="Consolas", size=9, color=TEXTO)
F_NUM_NEG = Font(name="Consolas", size=9, color=VERMELHO)
F_TOTAL = Font(name="Consolas", size=9, bold=True, color=GRAFITE)
FILL_HEADER = PatternFill("solid", start_color=GRAFITE)
FILL_ZEBRA = PatternFill("solid", start_color=CREME)
FILL_TOTAL = PatternFill("solid", start_color="EDE3CE")
BORDA_FINA = Border(bottom=Side(style="hair", color="D8CDB8"))


def escrever_aba(wb, sigla, extratos):
    ws = wb.create_sheet(sigla)
    ws.sheet_view.showGridLines = False

    ws["B1"] = f"{sigla} · Extrato Consolidado"
    ws["B1"].font = F_TITULO
    ws["B2"] = (f"{NOMES_EMPRESAS.get(sigla, sigla)} · contas somadas como conta única · "
                f"gerado em {datetime.now():%d/%m/%Y %H:%M}")
    ws["B2"].font = F_SUB

    # ---- resumo por conta (colunas C..I; "Banco" em C, alinhado com as transações)
    ws["B4"] = "Resumo por conta"
    ws["B4"].font = F_SECAO
    cab = ["Banco", "Conta", "Saldo Anterior", "Entradas (+)", "Saídas (−)",
           "Saldo Final", "Saldo Disponível Informado"]
    for j, titulo in enumerate(cab, start=3):
        c = ws.cell(row=5, column=j, value=titulo)
        c.font, c.fill = F_HEADER, FILL_HEADER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    n_txn = sum(len(e.transacoes) for e in extratos)
    linha_txn_ini = 5 + len(extratos) + 5   # 1ª linha de transações
    linha_txn_fim = linha_txn_ini + max(n_txn, 1) - 1
    col_banco, col_valor = "C", "F"         # colunas na tabela de transações

    r = 6
    for e in extratos:
        ws.cell(row=r, column=3, value=e.banco).font = F_TEXTO
        ws.cell(row=r, column=4, value=e.conta).font = F_TEXTO
        ws.cell(row=r, column=5, value=round(e.saldo_anterior, 2))
        ws.cell(row=r, column=6,
                value=f'=SUMIFS({col_valor}{linha_txn_ini}:{col_valor}{linha_txn_fim},'
                      f'{col_banco}{linha_txn_ini}:{col_banco}{linha_txn_fim},C{r},'
                      f'{col_valor}{linha_txn_ini}:{col_valor}{linha_txn_fim},">0")')
        ws.cell(row=r, column=7,
                value=f'=SUMIFS({col_valor}{linha_txn_ini}:{col_valor}{linha_txn_fim},'
                      f'{col_banco}{linha_txn_ini}:{col_banco}{linha_txn_fim},C{r},'
                      f'{col_valor}{linha_txn_ini}:{col_valor}{linha_txn_fim},"<0")')
        ws.cell(row=r, column=8, value=f"=E{r}+F{r}+G{r}")
        ws.cell(row=r, column=9, value=round(e.saldo_disponivel, 2))
        for j in range(5, 10):
            c = ws.cell(row=r, column=j)
            c.number_format, c.font = FMT_MOEDA, F_NUM
            c.border = BORDA_FINA
        for j in (3, 4):
            ws.cell(row=r, column=j).border = BORDA_FINA
        r += 1

    ws.cell(row=r, column=3, value="TOTAL").font = F_TOTAL
    for j, col in ((5, "E"), (6, "F"), (7, "G"), (8, "H"), (9, "I")):
        c = ws.cell(row=r, column=j, value=f"=SUM({col}6:{col}{r-1})")
        c.number_format, c.font, c.fill = FMT_MOEDA, F_TOTAL, FILL_TOTAL
    ws.cell(row=r, column=3).fill = FILL_TOTAL
    ws.cell(row=r, column=4).fill = FILL_TOTAL
    linha_total = r

    # ---- transações em sequência (colunas B..G)
    r += 2
    ws.cell(row=r, column=2, value="Transações em sequência (todas as contas)").font = F_SECAO
    r += 1
    cab2 = ["Data", "Banco", "Descrição", "Documento", "Valor", "Saldo Consolidado"]
    for j, titulo in enumerate(cab2, start=2):
        c = ws.cell(row=r, column=j, value=titulo)
        c.font, c.fill = F_HEADER, FILL_HEADER
        c.alignment = Alignment(horizontal="center", vertical="center")
    assert r + 1 == linha_txn_ini, "layout desalinhado com as fórmulas SUMIFS"

    txns = [t for e in extratos for t in e.transacoes]
    txns.sort(key=lambda t: t.data)
    r += 1
    for i, t in enumerate(txns, start=1):
        c = ws.cell(row=r, column=2, value=t.data)
        c.number_format, c.font = "DD/MM/YYYY", F_NUM
        ws.cell(row=r, column=3, value=t.banco).font = F_TEXTO
        ws.cell(row=r, column=4, value=t.descricao).font = F_TEXTO
        ws.cell(row=r, column=5, value=t.documento).font = F_NUM
        c = ws.cell(row=r, column=6, value=round(t.valor, 2))
        c.number_format = FMT_MOEDA
        c.font = F_NUM_NEG if t.valor < 0 else F_NUM
        saldo_prev = f"G{r-1}" if i > 1 else f"E{linha_total}"
        c = ws.cell(row=r, column=7, value=f"={saldo_prev}+F{r}")
        c.number_format, c.font = FMT_MOEDA, F_NUM
        if i % 2 == 0:
            for j in range(2, 8):
                ws.cell(row=r, column=j).fill = FILL_ZEBRA
        r += 1

    larguras = {"A": 2, "B": 12, "C": 16, "D": 62, "E": 24,
                "F": 15, "G": 18, "H": 16, "I": 24}
    for col, w in larguras.items():
        ws.column_dimensions[col].width = w
    return txns


def escrever_resumo(wb, dados):
    ws = wb.create_sheet("RESUMO", 0)
    ws.sheet_view.showGridLines = False
    ws["B1"] = "Grupo Dluz · Saldos Consolidados"
    ws["B1"].font = F_TITULO
    ws["B2"] = f"gerado em {datetime.now():%d/%m/%Y %H:%M}"
    ws["B2"].font = F_SUB
    cab = ["Empresa", "Contas", "Transações", "Saldo Final Consolidado",
           "Saldo Disponível Informado"]
    for j, titulo in enumerate(cab, start=2):
        c = ws.cell(row=4, column=j, value=titulo)
        c.font, c.fill = F_HEADER, FILL_HEADER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    r = 5
    for sigla, extratos in dados.items():
        linha_total = 6 + len(extratos)
        ws.cell(row=r, column=2, value=sigla).font = F_TEXTO
        ws.cell(row=r, column=3, value=len(extratos)).font = F_NUM
        ws.cell(row=r, column=4,
                value=sum(len(e.transacoes) for e in extratos)).font = F_NUM
        c = ws.cell(row=r, column=5, value=f"={sigla}!H{linha_total}")
        c.number_format, c.font = FMT_MOEDA, F_NUM
        c = ws.cell(row=r, column=6, value=f"={sigla}!I{linha_total}")
        c.number_format, c.font = FMT_MOEDA, F_NUM
        r += 1
    ws.cell(row=r, column=2, value="TOTAL GERAL").font = F_TOTAL
    for j, col in ((3, "C"), (4, "D"), (5, "E"), (6, "F")):
        c = ws.cell(row=r, column=j, value=f"=SUM({col}5:{col}{r-1})")
        c.number_format = FMT_MOEDA if j >= 5 else "0"
        c.font, c.fill = F_TOTAL, FILL_TOTAL
    ws.cell(row=r, column=2).fill = FILL_TOTAL
    for col, w in {"A": 2, "B": 14, "C": 10, "D": 12, "E": 24, "F": 26}.items():
        ws.column_dimensions[col].width = w


# ---------------------------------------------------------------- pdf

def fmt_brl(v):
    return f"{v:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def gerar_pdf(dados, caminho):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (BaseDocTemplate, Frame, PageBreak,
                                    PageTemplate, Paragraph, Spacer, Table,
                                    TableStyle)

    c_grafite = colors.HexColor("#241f18")
    c_creme = colors.HexColor("#f7f3ea")
    c_superficie = colors.HexColor("#fffdf8")
    c_ambar = colors.HexColor("#946517")
    c_vermelho = colors.HexColor("#b3261e")
    c_texto = colors.HexColor("#2c2620")
    c_total = colors.HexColor("#ede3ce")
    c_borda = colors.HexColor("#d8cdb8")

    st_titulo = ParagraphStyle("titulo", fontName="Times-Bold", fontSize=16,
                               leading=20, textColor=c_grafite, spaceAfter=3)
    st_sub = ParagraphStyle("sub", fontName="Helvetica", fontSize=8,
                            textColor=c_ambar, spaceAfter=10)
    st_secao = ParagraphStyle("secao", fontName="Times-Bold", fontSize=11,
                              textColor=c_ambar, spaceBefore=8, spaceAfter=4)
    st_desc = ParagraphStyle("desc", fontName="Helvetica", fontSize=6.8,
                             textColor=c_texto, leading=8)
    st_doc = ParagraphStyle("doc", fontName="Courier", fontSize=6,
                            textColor=c_texto, leading=7)

    def fundo(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(c_creme)
        canvas.rect(0, 0, A4[0], A4[1], stroke=0, fill=1)
        canvas.setFont("Courier", 7)
        canvas.setFillColor(c_ambar)
        canvas.drawRightString(A4[0] - 15 * mm, 8 * mm, f"pág. {canvas.getPageNumber()}")
        canvas.drawString(15 * mm, 8 * mm,
                          f"Grupo Dluz · extratos consolidados · {datetime.now():%d/%m/%Y}")
        canvas.restoreState()

    doc = BaseDocTemplate(caminho, pagesize=A4,
                          leftMargin=15 * mm, rightMargin=15 * mm,
                          topMargin=14 * mm, bottomMargin=14 * mm)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
    doc.addPageTemplates([PageTemplate(id="pagina", frames=[frame], onPage=fundo)])

    base_estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), c_grafite),
        ("TEXTCOLOR", (0, 0), (-1, 0), c_creme),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7),
        ("FONTSIZE", (0, 1), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 1), (-1, -2), 0.25, c_borda),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]

    historia = []

    # ---- página de resumo geral
    historia.append(Paragraph("Grupo Dluz · Saldos Consolidados", st_titulo))
    historia.append(Paragraph(f"gerado em {datetime.now():%d/%m/%Y %H:%M}", st_sub))
    linhas = [["Empresa", "Contas", "Transações", "Saldo Final Consolidado",
               "Saldo Disponível Informado"]]
    tg_final = tg_disp = 0.0
    for sigla, extratos in dados.items():
        final = sum(e.saldo_anterior + sum(t.valor for t in e.transacoes)
                    for e in extratos)
        disp = sum(e.saldo_disponivel for e in extratos)
        tg_final += final
        tg_disp += disp
        linhas.append([sigla, str(len(extratos)),
                       str(sum(len(e.transacoes) for e in extratos)),
                       fmt_brl(final), fmt_brl(disp)])
    linhas.append(["TOTAL GERAL", "", "", fmt_brl(tg_final), fmt_brl(tg_disp)])
    t = Table(linhas, colWidths=[35 * mm, 20 * mm, 25 * mm, 50 * mm, 50 * mm])
    t.setStyle(TableStyle(base_estilo + [
        ("BACKGROUND", (0, 1), (-1, -2), c_superficie),
        ("BACKGROUND", (0, -1), (-1, -1), c_total),
        ("FONTNAME", (0, -1), (-1, -1), "Courier-Bold"),
        ("FONTNAME", (1, 1), (-1, -1), "Courier"),
        ("FONTNAME", (0, 1), (0, -2), "Helvetica"),
        ("TEXTCOLOR", (0, 1), (-1, -1), c_texto),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
    ]))
    historia.append(t)

    # ---- uma seção por empresa
    for sigla, extratos in dados.items():
        historia.append(PageBreak())
        historia.append(Paragraph(f"{sigla} · Extrato Consolidado", st_titulo))
        historia.append(Paragraph(
            f"{NOMES_EMPRESAS.get(sigla, sigla)} · contas somadas como conta única",
            st_sub))

        # resumo por conta
        historia.append(Paragraph("Resumo por conta", st_secao))
        linhas = [["Banco", "Conta", "Saldo\nAnterior", "Entradas\n(+)",
                   "Saídas\n(−)", "Saldo\nFinal", "Disponível\nInformado"]]
        tot = [0.0] * 5
        for e in extratos:
            ent = sum(t.valor for t in e.transacoes if t.valor > 0)
            sai = sum(t.valor for t in e.transacoes if t.valor < 0)
            fin = e.saldo_anterior + ent + sai
            for i, v in enumerate((e.saldo_anterior, ent, sai, fin, e.saldo_disponivel)):
                tot[i] += v
            linhas.append([e.banco, e.conta, fmt_brl(e.saldo_anterior),
                           fmt_brl(ent), fmt_brl(sai), fmt_brl(fin),
                           fmt_brl(e.saldo_disponivel)])
        linhas.append(["TOTAL", ""] + [fmt_brl(v) for v in tot])
        t = Table(linhas, colWidths=[26 * mm, 38 * mm, 23 * mm, 23 * mm,
                                     23 * mm, 23 * mm, 24 * mm])
        t.setStyle(TableStyle(base_estilo + [
            ("BACKGROUND", (0, 1), (-1, -2), c_superficie),
            ("BACKGROUND", (0, -1), (-1, -1), c_total),
            ("FONTNAME", (2, 1), (-1, -1), "Courier"),
            ("FONTNAME", (0, -1), (-1, -1), "Courier-Bold"),
            ("TEXTCOLOR", (0, 1), (-1, -1), c_texto),
            ("TEXTCOLOR", (4, 1), (4, -1), c_vermelho),
            ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
            ("FONTSIZE", (0, 1), (1, -1), 6.5),
        ]))
        historia.append(t)

        # transações em sequência
        historia.append(Paragraph("Transações em sequência (todas as contas)", st_secao))
        txns = sorted((t for e in extratos for t in e.transacoes),
                      key=lambda t: t.data)
        linhas = [["Data", "Banco", "Descrição", "Documento", "Valor",
                   "Saldo Consolidado"]]
        estilo = base_estilo + [
            ("FONTNAME", (0, 1), (0, -1), "Courier"),
            ("FONTNAME", (4, 1), (-1, -1), "Courier"),
            ("TEXTCOLOR", (0, 1), (-1, -1), c_texto),
            ("ALIGN", (4, 0), (-1, -1), "RIGHT"),
            ("FONTSIZE", (1, 1), (1, -1), 6.5),
        ]
        saldo = tot[0]
        for i, txn in enumerate(txns, start=1):
            saldo += txn.valor
            linhas.append([f"{txn.data:%d/%m/%Y}", txn.banco,
                           Paragraph(txn.descricao, st_desc),
                           Paragraph(txn.documento, st_doc),
                           fmt_brl(txn.valor), fmt_brl(saldo)])
            if txn.valor < 0:
                estilo.append(("TEXTCOLOR", (4, i), (4, i), c_vermelho))
            if saldo < 0:
                estilo.append(("TEXTCOLOR", (5, i), (5, i), c_vermelho))
            if i % 2 == 0:
                estilo.append(("BACKGROUND", (0, i), (-1, i), c_superficie))
        t = Table(linhas, colWidths=[17 * mm, 21 * mm, 72 * mm, 22 * mm,
                                     22 * mm, 26 * mm], repeatRows=1)
        t.setStyle(TableStyle(estilo))
        historia.append(t)

    doc.build(historia)


# ---------------------------------------------------------------- main

def selecionar_arquivos_dialogo():
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    arquivos = filedialog.askopenfilenames(
        title="Selecione os extratos em PDF",
        filetypes=[("PDF", "*.pdf *.PDF"), ("Todos os arquivos", "*.*")],
    )
    if not arquivos:
        root.destroy()
        return None, None

    saida = filedialog.asksaveasfilename(
        title="Salvar relatório consolidado como",
        defaultextension=".pdf",
        initialfile="extratos_consolidados.pdf",
        initialdir=os.path.expanduser("~/Downloads"),
        filetypes=[("PDF", "*.pdf"), ("Excel", "*.xlsx")],
    )
    root.destroy()
    if not saida:
        return None, None
    return list(arquivos), saida


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("entradas", nargs="*", help="PDFs ou pastas com PDFs "
                    "(se omitido, abre caixa de seleção)")
    ap.add_argument("-o", "--saida", default=None,
        help="arquivo de saída (.pdf padrão; use .xlsx para Excel)")
    args = ap.parse_args()

    if args.entradas:
        entradas, saida = args.entradas, args.saida or os.path.expanduser(
            "~/Downloads/extratos_consolidados.pdf")
    else:
        entradas, saida = selecionar_arquivos_dialogo()
        if entradas is None:
            print("Nenhum arquivo selecionado. Encerrando.")
            sys.exit(0)
    args.saida = saida

    arquivos = []
    for e in entradas:
        if os.path.isdir(e):
            arquivos += sorted(glob.glob(os.path.join(e, "*.pdf")))
            arquivos += sorted(glob.glob(os.path.join(e, "*.PDF")))
        else:
            arquivos.append(e)

    dados = {}  # sigla -> [Extrato]
    for arq in arquivos:
        sigla = detectar_empresa(arq)
        if not sigla:
            print(f"[AVISO] empresa não identificada no nome, ignorando: {arq}")
            continue
        texto = extrair_texto(arq)
        parser = detectar_banco(texto)
        if not parser:
            print(f"[AVISO] banco não identificado, ignorando: {arq}")
            continue
        ext = parser(texto, arq) if parser is parse_stone else parser(texto)
        dados.setdefault(sigla, []).append(ext)
        ok, calc = ext.confere()
        if ext.avisos:
            status = "DIVERGENTE: " + "; ".join(ext.avisos)
        else:
            status = "OK" if ok else (f"DIVERGENTE (calculado {calc:.2f} ≠ "
                                      f"extrato {ext.saldo_final_extrato})")
        print(f"{sigla:5} {ext.banco:16} {len(ext.transacoes):3} transações  "
              f"anterior {ext.saldo_anterior:>12,.2f}  "
              f"disponível {ext.saldo_disponivel:>12,.2f}  [{status}]")

    ordem_banco = ["Bradesco", "Banco do Brasil", "Caixa", "Inter", "Stone"]
    dados = {s: sorted(exts, key=lambda e: ordem_banco.index(e.banco))
             for s, exts in sorted(dados.items())}

    if args.saida.lower().endswith(".xlsx"):
        wb = Workbook()
        wb.remove(wb.active)
        for sigla, extratos in dados.items():
            escrever_aba(wb, sigla, extratos)
        escrever_resumo(wb, dados)
        wb.save(args.saida)
    else:
        gerar_pdf(dados, args.saida)
    print(f"\nGerado: {args.saida}")


if __name__ == "__main__":
    main()
