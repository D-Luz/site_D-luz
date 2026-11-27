"""
Extrai produtos e cálculo de impostos de um XML de NF-e e gera uma planilha
Excel (.xlsx) com uma linha por item e uma linha de totais (via fórmulas).

Uso:
    - Rodando sem argumentos: abre uma janela para você selecionar o XML
      e outra para escolher onde salvar o .xlsx.
    - Rodando com argumentos (linha de comando / automação):
        python gerar_tabela_nf.py caminho/da/nota.xml caminho/de/saida.xlsx
"""

import os
import sys
import xml.etree.ElementTree as ET

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

NS = {"nfe": "http://www.portalfiscal.inf.br/nfe"}


def texto(elemento, tag, default="0"):
    """Busca uma subtag e devolve seu texto, ou um valor default se não existir."""
    if elemento is None:
        return default
    achado = elemento.find(f"nfe:{tag}", NS)
    return achado.text if achado is not None and achado.text is not None else default


def num(elemento, tag, default=0.0):
    return float(texto(elemento, tag, str(default)))


def extrair_itens(caminho_xml):
    root = ET.parse(caminho_xml).getroot()
    infNFe = root.find(".//nfe:infNFe", NS)

    ide = infNFe.find("nfe:ide", NS)
    emit = infNFe.find("nfe:emit", NS)
    dest = infNFe.find("nfe:dest", NS)
    icms_tot = infNFe.find(".//nfe:ICMSTot", NS)

    cabecalho = {
        "nNF": texto(ide, "nNF", ""),
        "dhEmi": texto(ide, "dhEmi", "")[:10],
        "emitente": texto(emit, "xNome", ""),
        "destinatario": texto(dest, "xNome", ""),
        "vNF": num(icms_tot, "vNF"),
        "vProd": num(icms_tot, "vProd"),
        "vICMS": num(icms_tot, "vICMS"),
        "vST": num(icms_tot, "vST"),
        "vIPI": num(icms_tot, "vIPI"),
        "vPIS": num(icms_tot, "vPIS"),
        "vCOFINS": num(icms_tot, "vCOFINS"),
    }

    itens = []
    for det in infNFe.findall("nfe:det", NS):
        prod = det.find("nfe:prod", NS)
        imposto = det.find("nfe:imposto", NS)

        # ICMS: a subtag varia (ICMS00, ICMS10, ICMS20, ICMS60...) -> pega a primeira filha
        icms_el = imposto.find("nfe:ICMS", NS)
        icms_sub = list(icms_el)[0] if icms_el is not None and len(icms_el) else None

        # IPI: pode vir como IPITrib (tributado) ou IPINT (não tributado)
        ipi_el = imposto.find("nfe:IPI", NS)
        ipi_sub = None
        if ipi_el is not None:
            ipi_sub = ipi_el.find("nfe:IPITrib", NS)
            if ipi_sub is None:
                ipi_sub = ipi_el.find("nfe:IPINT", NS)

        pis_sub = imposto.find("nfe:PIS/nfe:PISAliq", NS)
        cofins_sub = imposto.find("nfe:COFINS/nfe:COFINSAliq", NS)

        itens.append({
            "item": int(det.get("nItem")),
            "codigo": texto(prod, "cProd", ""),
            "descricao": texto(prod, "xProd", ""),
            "ncm": texto(prod, "NCM", ""),
            "cfop": texto(prod, "CFOP", ""),
            "cst_icms": texto(icms_sub, "CST", ""),
            "qtd": num(prod, "qCom"),
            "vl_unit": num(prod, "vUnCom"),
            "vl_produto": num(prod, "vProd"),
            "bc_icms": num(icms_sub, "vBC"),
            "p_icms": num(icms_sub, "pICMS"),
            "vl_icms": num(icms_sub, "vICMS"),
            "bc_icms_st": num(icms_sub, "vBCST"),
            "p_icms_st": num(icms_sub, "pICMSST"),
            "vl_icms_st": num(icms_sub, "vICMSST"),
            "bc_ipi": num(ipi_sub, "vBC"),
            "p_ipi": num(ipi_sub, "pIPI"),
            "vl_ipi": num(ipi_sub, "vIPI"),
            "bc_pis": num(pis_sub, "vBC"),
            "p_pis": num(pis_sub, "pPIS"),
            "vl_pis": num(pis_sub, "vPIS"),
            "bc_cofins": num(cofins_sub, "vBC"),
            "p_cofins": num(cofins_sub, "pCOFINS"),
            "vl_cofins": num(cofins_sub, "vCOFINS"),
            "vl_item": num(det, "vItem"),
            "vl_trib_ibpt": num(imposto, "vTotTrib"),
        })

    return cabecalho, itens


COLUNAS = [
    ("item", "Item", 6, "0"),
    ("codigo", "Código", 10, None),
    ("descricao", "Descrição", 38, None),
    ("ncm", "NCM", 10, None),
    ("cfop", "CFOP", 8, None),
    ("cst_icms", "CST ICMS", 9, None),
    ("qtd", "Qtd", 9, "#,##0.00"),
    ("vl_unit", "Vl. Unit.", 12, "R$ #,##0.00"),
    ("vl_produto", "Vl. Produto", 13, "R$ #,##0.00"),
    ("bc_icms", "BC ICMS", 12, "R$ #,##0.00"),
    ("p_icms", "% ICMS", 9, "0.00%"),
    ("vl_icms", "Vl. ICMS", 12, "R$ #,##0.00"),
    ("bc_icms_st", "BC ICMS-ST", 12, "R$ #,##0.00"),
    ("p_icms_st", "% ICMS-ST", 10, "0.00%"),
    ("vl_icms_st", "Vl. ICMS-ST", 12, "R$ #,##0.00"),
    ("bc_ipi", "BC IPI", 12, "R$ #,##0.00"),
    ("p_ipi", "% IPI", 9, "0.00%"),
    ("vl_ipi", "Vl. IPI", 12, "R$ #,##0.00"),
    ("bc_pis", "BC PIS", 12, "R$ #,##0.00"),
    ("p_pis", "% PIS", 9, "0.00%"),
    ("vl_pis", "Vl. PIS", 12, "R$ #,##0.00"),
    ("bc_cofins", "BC COFINS", 13, "R$ #,##0.00"),
    ("p_cofins", "% COFINS", 10, "0.00%"),
    ("vl_cofins", "Vl. COFINS", 13, "R$ #,##0.00"),
    ("vl_item", "Vl. Total Item", 14, "R$ #,##0.00"),
    ("vl_trib_ibpt", "Vl. Trib. (IBPT)", 15, "R$ #,##0.00"),
]

# colunas monetárias que entram na linha de totais (SUM) e na conferência com o XML
COLS_TOTAL = {
    "vl_produto": "vProd", "vl_icms": "vICMS", "vl_icms_st": "vST",
    "vl_ipi": "vIPI", "vl_pis": "vPIS", "vl_cofins": "vCOFINS", "vl_item": None,
}

AZUL_ESCURO = "1F3864"
CINZA_CLARO = "F2F2F2"
DOURADO = "BF8F00"
FONTE = "Arial"


def gerar_planilha(cabecalho, itens, caminho_saida):
    wb = Workbook()
    ws = wb.active
    ws.title = f"NF {cabecalho['nNF']}"

    borda_fina = Border(*(Side(style="thin", color="D9D9D9"),) * 4)

    # --- Bloco de identificação da nota ---
    ws["A1"] = f"NF-e nº {cabecalho['nNF']}  —  {cabecalho['dhEmi']}"
    ws["A1"].font = Font(name=FONTE, bold=True, size=13, color=AZUL_ESCURO)
    ws["A2"] = f"Emitente: {cabecalho['emitente']}"
    ws["A3"] = f"Destinatário: {cabecalho['destinatario']}"
    for r in (2, 3):
        ws[f"A{r}"].font = Font(name=FONTE, size=10, italic=True)

    linha_header = 5
    linha_dados_ini = linha_header + 1
    linha_dados_fim = linha_dados_ini + len(itens) - 1
    linha_totais = linha_dados_fim + 1

    # --- Cabeçalho da tabela ---
    for c, (chave, titulo, largura, _fmt) in enumerate(COLUNAS, start=1):
        cel = ws.cell(row=linha_header, column=c, value=titulo)
        cel.font = Font(name=FONTE, bold=True, color="FFFFFF", size=10)
        cel.fill = PatternFill("solid", fgColor=AZUL_ESCURO)
        cel.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cel.border = borda_fina
        ws.column_dimensions[get_column_letter(c)].width = largura

    # --- Linhas de dados ---
    for i, item in enumerate(itens):
        linha = linha_dados_ini + i
        for c, (chave, _titulo, _largura, fmt) in enumerate(COLUNAS, start=1):
            cel = ws.cell(row=linha, column=c, value=item[chave])
            cel.font = Font(name=FONTE, size=10)
            cel.border = borda_fina
            if fmt:
                cel.number_format = fmt
            if chave == "descricao":
                cel.alignment = Alignment(horizontal="left")
            else:
                cel.alignment = Alignment(horizontal="center")
        if i % 2 == 1:
            for c in range(1, len(COLUNAS) + 1):
                ws.cell(row=linha, column=c).fill = PatternFill("solid", fgColor=CINZA_CLARO)

    # --- Linha de totais (fórmulas, não valores fixos) ---
    ws.cell(row=linha_totais, column=1, value="TOTAL").font = Font(name=FONTE, bold=True, size=10)
    ws.merge_cells(start_row=linha_totais, start_column=1, end_row=linha_totais, end_column=6)
    ws.cell(row=linha_totais, column=1).alignment = Alignment(horizontal="center")

    for c, (chave, _titulo, _largura, fmt) in enumerate(COLUNAS, start=1):
        if chave in COLS_TOTAL:
            col_letra = get_column_letter(c)
            cel = ws.cell(
                row=linha_totais, column=c,
                value=f"=SUM({col_letra}{linha_dados_ini}:{col_letra}{linha_dados_fim})",
            )
            cel.number_format = fmt
            cel.font = Font(name=FONTE, bold=True, size=10)
        else:
            cel = ws.cell(row=linha_totais, column=c)
        cel.fill = PatternFill("solid", fgColor="D9E2F3")
        cel.border = borda_fina

    for c in range(1, len(COLUNAS) + 1):
        ws.cell(row=linha_totais, column=c).border = Border(
            top=Side(style="double", color=AZUL_ESCURO), bottom=Side(style="thin")
        )

    ws.freeze_panes = f"A{linha_dados_ini}"

    # --- Bloco de conferência com os totais oficiais da NF (ICMSTot) ---
    linha_conf = linha_totais + 2
    ws.cell(row=linha_conf, column=1, value="Conferência com os totais da NF-e (ICMSTot)").font = \
        Font(name=FONTE, bold=True, size=10, color=DOURADO)

    linhas_conf = [
        ("Vl. Produtos", "vl_produto", cabecalho["vProd"]),
        ("Vl. ICMS", "vl_icms", cabecalho["vICMS"]),
        ("Vl. ICMS-ST", "vl_icms_st", cabecalho["vST"]),
        ("Vl. IPI", "vl_ipi", cabecalho["vIPI"]),
        ("Vl. PIS", "vl_pis", cabecalho["vPIS"]),
        ("Vl. COFINS", "vl_cofins", cabecalho["vCOFINS"]),
    ]
    coluna_idx = {chave: c for c, (chave, *_r) in enumerate(COLUNAS, start=1)}

    linha_atual = linha_conf + 1
    headers_conf = ["Imposto", "Soma da tabela", "Total oficial (XML)", "Diferença"]
    for c, h in enumerate(headers_conf, start=1):
        cel = ws.cell(row=linha_atual, column=c, value=h)
        cel.font = Font(name=FONTE, bold=True, size=9)
        cel.fill = PatternFill("solid", fgColor=CINZA_CLARO)

    for nome, chave, valor_oficial in linhas_conf:
        linha_atual += 1
        col_letra = get_column_letter(coluna_idx[chave])
        ws.cell(row=linha_atual, column=1, value=nome).font = Font(name=FONTE, size=9)
        cel_soma = ws.cell(row=linha_atual, column=2, value=f"={col_letra}{linha_totais}")
        cel_soma.number_format = "R$ #,##0.00"
        cel_oficial = ws.cell(row=linha_atual, column=3, value=valor_oficial)
        cel_oficial.number_format = "R$ #,##0.00"
        cel_dif = ws.cell(row=linha_atual, column=4, value=f"=B{linha_atual}-C{linha_atual}")
        cel_dif.number_format = "R$ #,##0.00"

    wb.save(caminho_saida)
    return linha_dados_ini, linha_dados_fim, linha_totais


def selecionar_xml_entrada():
    from tkinter import Tk, filedialog
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    caminho = filedialog.askopenfilename(
        title="Selecione o XML da NF-e",
        filetypes=[("Arquivos XML", "*.xml"), ("Todos os arquivos", "*.*")],
    )
    root.destroy()
    return caminho


def selecionar_xlsx_saida(nome_sugerido, pasta_inicial):
    from tkinter import Tk, filedialog
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    caminho = filedialog.asksaveasfilename(
        title="Salvar planilha como",
        initialdir=pasta_inicial,
        initialfile=nome_sugerido,
        defaultextension=".xlsx",
        filetypes=[("Planilha Excel", "*.xlsx")],
    )
    root.destroy()
    return caminho


if __name__ == "__main__":
    if len(sys.argv) > 1:
        entrada = sys.argv[1]
        saida = sys.argv[2] if len(sys.argv) > 2 else None
    else:
        entrada = selecionar_xml_entrada()
        saida = None

    if not entrada:
        print("Nenhum XML selecionado. Encerrando.")
        sys.exit(1)

    if not saida:
        cab_preview = ET.parse(entrada).getroot()
        nNF = cab_preview.find(".//nfe:ide/nfe:nNF", NS)
        numero = nNF.text if nNF is not None else "nota"
        nome_sugerido = f"NF_{numero}_itens_impostos.xlsx"
        pasta_inicial = os.path.dirname(entrada)
        if len(sys.argv) > 1:
            saida = os.path.join(pasta_inicial, nome_sugerido)
        else:
            saida = selecionar_xlsx_saida(nome_sugerido, pasta_inicial)
            if not saida:
                print("Nenhum local de salvamento escolhido. Encerrando.")
                sys.exit(1)

    cab, itens = extrair_itens(entrada)
    gerar_planilha(cab, itens, saida)
    print(f"OK: {len(itens)} itens exportados para {saida}")
