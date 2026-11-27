#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║      EXTRATOR DE TRANSAÇÕES PIX — EXTRATO BANCO INTER        ║
║                        Versão 3.1                            ║
╚══════════════════════════════════════════════════════════════╝

Dependências:
    pip install pdfplumber pandas openpyxl

Novidades da v3.0:
  • Correção do "Pix devolvido": agora "Pix recebido devolvido" e
    "Pix enviado devolvido" são reconhecidos com o tipo e o sinal certos
  • Parser baseado no formato real da linha do Inter:
        Tipo: "descrição" [-]R$ valor [-]R$ saldo
    (muito mais confiável do que procurar valor solto no texto)
  • Nova coluna "Saldo" (saldo por transação, direto do extrato)
  • Seleção de VÁRIOS PDFs de uma vez; coluna "Empresa" lida do
    cabeçalho de cada extrato
  • Aba "Resumo" no Excel com totais de recebidos/enviados/devoluções
  • Titular limpo de códigos ("Cp :00360305-", "00019 215647050",
    "null" → Não identificado)
  • Verificação de consistência: alerta se o sinal do valor não bater
    com o tipo da transação
  • Uso por linha de comando:  python extrator_pix_inter.py a.pdf b.pdf
    (sem janelas; salva o Excel na pasta do primeiro PDF)

Novidades da v3.1:
  • Nova coluna "Código" com o identificador do pagador/recebedor
    (ex.: "Cp :00360305-") separado do nome, em vez de ser descartado
"""

import re
import sys
import tkinter as tk
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tkinter import filedialog
from typing import Any, Callable

import pandas as pd
import pdfplumber

# ─────────────────────────────────────────────────────────────────────────────
# Constantes e expressões regulares
# ─────────────────────────────────────────────────────────────────────────────

MESES_PT: dict[str, str] = {
    "janeiro": "01",   "jan": "01",
    "fevereiro": "02", "fev": "02",
    "marco": "03",     "mar": "03",
    "abril": "04",     "abr": "04",
    "maio": "05",      "mai": "05",
    "junho": "06",     "jun": "06",
    "julho": "07",     "jul": "07",
    "agosto": "08",    "ago": "08",
    "setembro": "09",  "set": "09",
    "outubro": "10",   "out": "10",
    "novembro": "11",  "nov": "11",
    "dezembro": "12",  "dez": "12",
}

# Data por extenso: "16 de Junho de 2026", "16 Junho 2026" ou "16 Jun. 2026"
RE_DATA = re.compile(
    r"\b(\d{1,2})\s+(?:de\s+)?([A-Za-zÀ-ÿçÇ]{3,})\.?\s+(?:de\s+)?(\d{4})\b",
    re.IGNORECASE,
)

# Data numérica: "16/06/2026" ou "16-06-26"
RE_DATA_NUMERICA = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")

VALOR = r"\d{1,3}(?:\.\d{3})*,\d{2}"

# Linha completa de transação Pix do extrato do Inter:
#   Pix recebido: "Cp :00360305-FULANO" R$ 1.848,00 R$ 16.087,45
#   Pix enviado: "Cp :00360305-FULANO" -R$ 6.000,00 R$ 7.745,29
#   Pix recebido devolvido: "Cp :90400888-null" -R$ 7,00 R$ 3.105,36
RE_TRANSACAO_PIX = re.compile(
    r"^\s*(?P<tipo>Pix\s+(?:recebido|enviado)(?:\s+devolvido)?)\s*:\s*"
    r'"(?P<descricao>.*?)"\s*'
    rf"(?P<sinal>-?)\s*R\$\s*(?P<valor>{VALOR})"
    rf"(?:\s+(?P<sinal_saldo>-?)\s*R\$\s*(?P<saldo>{VALOR}))?\s*$",
    re.IGNORECASE,
)

# Início de linha Pix (para juntar linhas quando a transação vier quebrada)
RE_INICIO_PIX = re.compile(
    r"^\s*Pix\s+(?:recebido|enviado)(?:\s+devolvido)?\s*:", re.IGNORECASE
)

# Código do pagador/recebedor que antecede o nome: "Cp :00360305-" / "Cv:123-"
RE_CODIGO_CP = re.compile(r"^\s*[A-Za-z]{2}\s*:\s*(\d+)\s*-\s*", re.IGNORECASE)

# Token puramente numérico (com pontos), usado para separar códigos extras
# que aparecem antes do nome: "00019 215647050 FULANO", "20.006.098 FULANO"
RE_TOKEN_NUMERICO = re.compile(r"^[\d.]+$")

# Empresa e período no cabeçalho do extrato
RE_CNPJ_LINHA = re.compile(r"^CPF/CNPJ:", re.IGNORECASE)
RE_PERIODO = re.compile(r"Período:\s*(\S+)\s*a\s*(\S+)", re.IGNORECASE)

MAX_LINHAS_BLOCO_PIX = 4

# Sinal esperado por tipo (para o alerta de consistência):
#   recebido → entra dinheiro (+)      enviado → sai dinheiro (−)
#   recebido devolvido → sai (−)       enviado devolvido → volta (+)
SINAL_ESPERADO = {
    "Recebido": 1,
    "Enviado": -1,
    "Recebido devolvido": -1,
    "Enviado devolvido": 1,
}


# ─────────────────────────────────────────────────────────────────────────────
# Funções auxiliares
# ─────────────────────────────────────────────────────────────────────────────

def remover_acentos(texto: str) -> str:
    normalizado = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in normalizado if unicodedata.category(c) != "Mn")


def limpar_espacos(texto: str) -> str:
    return re.sub(r"\s+", " ", texto).strip()


def normalizar_data(texto: str) -> str | None:
    """Extrai e normaliza datas para dd/mm/aaaa."""
    m = RE_DATA.search(texto)
    if m:
        dia, mes_txt, ano = m.groups()
        mes_num = MESES_PT.get(remover_acentos(mes_txt))
        if mes_num:
            return f"{int(dia):02d}/{mes_num}/{ano}"

    m = RE_DATA_NUMERICA.search(texto)
    if m:
        dia, mes, ano = m.groups()
        ano = f"20{ano}" if len(ano) == 2 else ano
        return f"{int(dia):02d}/{int(mes):02d}/{ano}"

    return None


def valor_para_decimal(valor_txt: str) -> Decimal | None:
    """Converte '1.500,00' (formato brasileiro) para Decimal."""
    try:
        return Decimal(valor_txt.replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None


def formatar_moeda(valor: Decimal | float | None) -> str:
    if valor is None or pd.isna(valor):
        return "N/D"
    valor_decimal = Decimal(str(valor))
    sinal = "-" if valor_decimal < 0 else ""
    texto = f"{abs(valor_decimal):,.2f}"
    texto = texto.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{sinal}R$ {texto}"


def normalizar_tipo(tipo_bruto: str) -> str:
    """'Pix recebido devolvido' → 'Recebido devolvido'."""
    tipo = limpar_espacos(remover_acentos(tipo_bruto))
    tipo = tipo.removeprefix("pix").strip()
    return tipo.capitalize()  # 'Recebido', 'Enviado', 'Recebido devolvido'…


def extrair_codigo_e_titular(descricao: str) -> tuple[str, str]:
    """
    Separa o código do pagador/recebedor (prefixo "Cp") do nome do titular.

        'Cp :00360305-BASTOS CONSTRUTORA LTDA'
            → codigo='00360305'          titular='BASTOS CONSTRUTORA LTDA'
        '00019 215647050 JEFFERSON LOPES'
            → codigo='00019 215647050'    titular='JEFFERSON LOPES'
        'Cp :10573521-20.006.098 ELISANDRA FRANCA DA COSTA'
            → codigo='10573521 20.006.098'  titular='ELISANDRA FRANCA DA COSTA'
        'Cp :90400888-null'
            → codigo='90400888'          titular='Não identificado'
    """
    texto = limpar_espacos(descricao)
    partes_codigo: list[str] = []

    m = RE_CODIGO_CP.match(texto)
    if m:
        partes_codigo.append(m.group(1))
        texto = texto[m.end():]

    # Consome tokens numéricos adicionais que precedem o nome (códigos de
    # banco/agência, fragmentos de CPF/CNPJ etc.), sem esvaziar o nome.
    tokens = texto.split()
    while len(tokens) > 1 and RE_TOKEN_NUMERICO.match(tokens[0]):
        partes_codigo.append(tokens.pop(0))
    texto = " ".join(tokens)

    codigo = " ".join(partes_codigo)
    titular = texto.strip(" \"'“”‘’ ,;:.-")
    if not titular or titular.lower() == "null":
        titular = "Não identificado"
    return codigo, titular


def _dialogo_topmost(func: Callable[..., Any], **kwargs: Any) -> Any:
    """Cria um root Tk invisível e garante que o diálogo apareça no topo."""
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    try:
        return func(parent=root, **kwargs)
    finally:
        root.destroy()


def selecionar_arquivos() -> list[str]:
    caminhos = _dialogo_topmost(
        filedialog.askopenfilenames,
        title="Selecione um ou mais extratos em PDF",
        filetypes=[("Arquivos PDF", "*.pdf"), ("Todos os arquivos", "*.*")],
    )
    return list(caminhos or [])


def escolher_destino_excel(nome_sugerido: str) -> str:
    return _dialogo_topmost(
        filedialog.asksaveasfilename,
        title="Salvar planilha Excel como",
        defaultextension=".xlsx",
        filetypes=[("Planilha Excel", "*.xlsx")],
        initialfile=nome_sugerido,
    ) or ""


# ─────────────────────────────────────────────────────────────────────────────
# Leitura do PDF
# ─────────────────────────────────────────────────────────────────────────────

def extrair_linhas_pdf(caminho: str | Path) -> list[str]:
    """Lê todas as páginas do PDF e retorna as linhas de texto não vazias."""
    caminho = Path(caminho)
    linhas: list[str] = []

    try:
        with pdfplumber.open(caminho) as pdf:
            for num, pagina in enumerate(pdf.pages, start=1):
                texto = pagina.extract_text(x_tolerance=3, y_tolerance=3)
                if texto:
                    linhas.extend(l for l in texto.splitlines() if l.strip())
                else:
                    print(f"    Página {num}: *** SEM TEXTO *** "
                          "(PDF pode ser imagem/scan — OCR necessário)")
    except Exception as exc:
        raise RuntimeError(
            f"Não foi possível ler '{caminho.name}'. Verifique se o arquivo "
            "não está protegido por senha, corrompido ou aberto em outro programa."
        ) from exc

    return linhas


def salvar_dump_diagnostico(caminho_pdf: Path, linhas: list[str]) -> Path:
    """Salva o texto bruto em .txt ao lado do PDF, para diagnóstico."""
    txt_path = caminho_pdf.with_name(f"{caminho_pdf.stem}_raw_text.txt")
    txt_path.write_text("\n".join(linhas), encoding="utf-8")
    return txt_path


def extrair_cabecalho(linhas: list[str]) -> tuple[str, str]:
    """Lê empresa e período do topo do extrato."""
    empresa, periodo = "", ""
    for idx, linha in enumerate(linhas[:10]):
        if RE_CNPJ_LINHA.match(linha.strip()) and idx > 0:
            empresa = limpar_espacos(linhas[idx - 1])
        m = RE_PERIODO.search(linha)
        if m:
            periodo = f"{m.group(1)} a {m.group(2)}"
    return empresa, periodo


# ─────────────────────────────────────────────────────────────────────────────
# Parser das transações Pix
# ─────────────────────────────────────────────────────────────────────────────

def montar_bloco_pix(linhas: list[str], inicio: int) -> tuple[str, int]:
    """
    Junta a linha Pix com as seguintes quando a transação veio quebrada
    (nome longo empurrando o valor para a linha de baixo).
    Retorna o texto combinado e o último índice consumido.
    """
    partes = [limpar_espacos(linhas[inicio])]
    fim = inicio

    limite = min(len(linhas), inicio + MAX_LINHAS_BLOCO_PIX + 1)
    for j in range(inicio + 1, limite):
        if RE_TRANSACAO_PIX.match(" ".join(partes)):
            break
        proxima = linhas[j].strip()
        if not proxima:
            continue
        # Nova transação ou novo dia encerram o bloco
        if RE_INICIO_PIX.match(proxima) or normalizar_data(proxima):
            break
        partes.append(limpar_espacos(proxima))
        fim = j

    return limpar_espacos(" ".join(partes)), fim


def processar_linhas(linhas: list[str], empresa: str = "") -> pd.DataFrame:
    """
    Varre as linhas do extrato e monta um DataFrame com:
        Data | Tipo de Pix | Código | Titular | Valor | Saldo [| Empresa]
    """
    dados: list[dict[str, object]] = []
    avisos: list[str] = []
    data_atual: str | None = None

    i = 0
    while i < len(linhas):
        linha = linhas[i].strip()

        # 1. Cabeçalho de dia: atualiza a data corrente
        #    ("1 de Julho de 2026 Saldo do dia: …")
        if "saldo do dia" in linha.lower():
            data_detectada = normalizar_data(linha)
            if data_detectada:
                data_atual = data_detectada

        # 2. Linha de transação Pix
        if RE_INICIO_PIX.match(linha):
            bloco, ultimo_indice = montar_bloco_pix(linhas, i)
            m = RE_TRANSACAO_PIX.match(bloco)

            if not m:
                avisos.append(f"Linha Pix não interpretada: {bloco[:90]}")
                i = ultimo_indice + 1
                continue

            tipo = normalizar_tipo(m.group("tipo"))
            codigo, titular = extrair_codigo_e_titular(m.group("descricao"))

            valor = valor_para_decimal(m.group("valor"))
            if valor is not None and m.group("sinal") == "-":
                valor = -valor

            saldo = valor_para_decimal(m.group("saldo") or "") if m.group("saldo") else None
            if saldo is not None and m.group("sinal_saldo") == "-":
                saldo = -saldo

            # 3. Consistência: sinal do valor deve combinar com o tipo
            esperado = SINAL_ESPERADO.get(tipo)
            if valor is not None and esperado and valor != 0:
                if (valor > 0) != (esperado > 0):
                    avisos.append(
                        f"Sinal inesperado em {data_atual or 'sem data'}: "
                        f"{tipo} | {titular} | {formatar_moeda(valor)}"
                    )

            dados.append({
                "Data":        data_atual or "N/D",
                "Tipo de Pix": tipo,
                "Código":      codigo,
                "Titular":     titular,
                "Valor":       float(valor) if valor is not None else None,
                "Saldo":       float(saldo) if saldo is not None else None,
                "Empresa":     empresa,
            })

            seta = "↓" if (valor or 0) >= 0 else "↑"
            print(f"  {seta} {data_atual or 'sem data'} | {tipo:<19} | "
                  f"{codigo:<12} | {titular[:42]:<42} | {formatar_moeda(valor):>15}")

            i = ultimo_indice

        i += 1

    if avisos:
        print("\n  ⚠ Avisos:")
        for aviso in avisos:
            print(f"    • {aviso}")

    return pd.DataFrame(
        dados,
        columns=["Data", "Tipo de Pix", "Código", "Titular", "Valor", "Saldo", "Empresa"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Exportação
# ─────────────────────────────────────────────────────────────────────────────

def montar_resumo(tabela: pd.DataFrame) -> pd.DataFrame:
    """Totais por empresa e tipo, com contagem de transações."""
    grupo = ["Empresa", "Tipo de Pix"] if tabela["Empresa"].nunique() > 1 else ["Tipo de Pix"]
    resumo = (
        tabela.groupby(grupo, dropna=False)
        .agg(Transações=("Valor", "size"), Total=("Valor", "sum"))
        .reset_index()
    )
    return resumo


def tabela_para_console(tabela: pd.DataFrame) -> str:
    visualizacao = tabela.copy()
    for coluna in ("Valor", "Saldo"):
        visualizacao[coluna] = visualizacao[coluna].apply(formatar_moeda)
    if visualizacao["Empresa"].nunique() <= 1:
        visualizacao = visualizacao.drop(columns=["Empresa"])
    return visualizacao.to_string(index=False)


FORMATO_MOEDA_EXCEL = '"R$" #,##0.00;[Red]-"R$" #,##0.00'


def salvar_excel(tabela: pd.DataFrame, caminho: str | Path) -> None:
    caminho = Path(caminho)
    varias_empresas = tabela["Empresa"].nunique() > 1
    exportar = tabela if varias_empresas else tabela.drop(columns=["Empresa"])
    resumo = montar_resumo(tabela)

    with pd.ExcelWriter(caminho, engine="openpyxl") as writer:
        exportar.to_excel(writer, index=False, sheet_name="Pix")
        resumo.to_excel(writer, index=False, sheet_name="Resumo")

        planilha = writer.sheets["Pix"]
        planilha.freeze_panes = "A2"
        planilha.auto_filter.ref = planilha.dimensions

        colunas = list(exportar.columns)
        larguras = {"Data": 12, "Tipo de Pix": 20, "Código": 16, "Titular": 46,
                    "Valor": 16, "Saldo": 16, "Empresa": 34}
        for idx, nome in enumerate(colunas, start=1):
            letra = planilha.cell(row=1, column=idx).column_letter
            planilha.column_dimensions[letra].width = larguras.get(nome, 14)
            if nome in ("Valor", "Saldo"):
                for celula in planilha[letra][1:]:
                    celula.number_format = FORMATO_MOEDA_EXCEL

        aba_resumo = writer.sheets["Resumo"]
        for idx, nome in enumerate(resumo.columns, start=1):
            letra = aba_resumo.cell(row=1, column=idx).column_letter
            aba_resumo.column_dimensions[letra].width = 34 if nome == "Empresa" else 20
            if nome == "Total":
                for celula in aba_resumo[letra][1:]:
                    celula.number_format = FORMATO_MOEDA_EXCEL


# ─────────────────────────────────────────────────────────────────────────────
# Fluxo principal
# ─────────────────────────────────────────────────────────────────────────────

def processar_pdf(caminho_pdf: str | Path) -> pd.DataFrame:
    """Extrai as transações Pix de um único PDF."""
    caminho_pdf = Path(caminho_pdf)
    print(f"\n▶ {caminho_pdf.name}")

    linhas = extrair_linhas_pdf(caminho_pdf)
    if not linhas:
        print("  ⚠ Nenhum texto extraído (scan/imagem? arquivo corrompido?).")
        return pd.DataFrame()

    empresa, periodo = extrair_cabecalho(linhas)
    if empresa:
        print(f"  Empresa: {empresa}" + (f"  |  Período: {periodo}" if periodo else ""))

    tabela = processar_linhas(linhas, empresa=empresa or caminho_pdf.stem)

    if tabela.empty:
        txt = salvar_dump_diagnostico(caminho_pdf, linhas)
        print("  ⚠ Nenhuma transação Pix encontrada.")
        print(f"    Texto bruto salvo para diagnóstico em: {txt}")
    else:
        print(f"  ✔ {len(tabela)} transação(ões) Pix.")

    return tabela


def main() -> None:
    SEP = "═" * 62
    print(SEP)
    print("  EXTRATOR DE PIX — EXTRATO BANCO INTER  │  v3.1")
    print(SEP)

    # ── Passo 1: PDFs por argumento ou por janela de seleção ─────────────
    modo_cli = len(sys.argv) > 1
    if modo_cli:
        caminhos_pdf = sys.argv[1:]
    else:
        print("\n[1/3] Selecione o(s) arquivo(s) PDF do extrato…")
        caminhos_pdf = selecionar_arquivos()

    if not caminhos_pdf:
        print("  Nenhum arquivo selecionado. Encerrando.")
        input("\nPressione ENTER para fechar…")
        return

    # ── Passo 2: processar cada PDF ───────────────────────────────────────
    print(f"\n[2/3] Processando {len(caminhos_pdf)} arquivo(s)…")
    tabelas: list[pd.DataFrame] = []
    for caminho in caminhos_pdf:
        try:
            tabela = processar_pdf(caminho)
        except RuntimeError as exc:
            print(f"  ERRO: {exc}")
            continue
        if not tabela.empty:
            tabelas.append(tabela)

    if not tabelas:
        print("\n  ⚠ Nenhuma transação Pix encontrada em nenhum arquivo.")
        if not modo_cli:
            input("\nPressione ENTER para fechar…")
        return

    consolidado = pd.concat(tabelas, ignore_index=True)

    print(f"\n  Total geral: {len(consolidado)} transação(ões).")
    print("\n" + tabela_para_console(consolidado))
    print("\n  ── Resumo ──")
    resumo = montar_resumo(consolidado).copy()
    resumo["Total"] = resumo["Total"].apply(formatar_moeda)
    print(resumo.to_string(index=False))

    # ── Passo 3: salvar Excel ─────────────────────────────────────────────
    nome_sugerido = f"Extrato_Pix_{datetime.now():%Y-%m-%d}.xlsx"
    if modo_cli:
        caminho_excel = str(Path(caminhos_pdf[0]).parent / nome_sugerido)
    else:
        print("\n[3/3] Escolha onde salvar a planilha Excel…")
        caminho_excel = escolher_destino_excel(nome_sugerido)

    if caminho_excel:
        try:
            salvar_excel(consolidado, caminho_excel)
            print(f"\n  ✔ Planilha salva em:\n  {caminho_excel}")
        except Exception as exc:
            print(f"\n  ERRO ao salvar a planilha: {exc}")
    else:
        print("\n  Salvamento cancelado pelo usuário.")

    print("\n" + SEP)
    if not modo_cli:
        input("\nPressione ENTER para fechar…")


if __name__ == "__main__":
    main()
