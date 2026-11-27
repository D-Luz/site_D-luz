"""Bancos e fiscal: ferramentas que trabalham sobre arquivos enviados (PDF de extrato, XML de NF-e)."""

from datetime import datetime

import pandas as pd

from ..tarefas import ErroAmigavel
from . import Campo, ferramenta
from ._saida import pagina_tabela


@ferramenta(
    "pix-inter",
    "Extrator de Pix — Banco Inter",
    "bancos",
    "Lê um ou mais extratos do Banco Inter em PDF e separa todas as transações Pix (recebidas, enviadas e "
    "devolvidas) com data, tipo, titular, código e valor. Gera Excel com resumo.",
    [Campo("pdfs", "Extratos do Inter (PDF)", "arquivos", aceita=".pdf")],
    origem="extrator_pix_inter.py",
)
def pix_inter(ctx):
    mod = ctx.script("extrator_pix_inter.py")
    tabelas = []
    for caminho in ctx.entradas("pdfs"):
        try:
            t = mod.processar_pdf(caminho)
        except RuntimeError as exc:
            print(f"  ERRO: {exc}")
            continue
        if not t.empty:
            tabelas.append(t)
    if not tabelas:
        raise ErroAmigavel("Nenhuma transação Pix encontrada nos arquivos enviados.")
    consolidado = pd.concat(tabelas, ignore_index=True)
    print("\n── Resumo ──")
    resumo = mod.montar_resumo(consolidado).copy()
    print(resumo.assign(Total=resumo["Total"].apply(mod.formatar_moeda)).to_string(index=False))
    nome = f"Extrato_Pix_{datetime.now():%Y-%m-%d_%H%M}"
    mod.salvar_excel(consolidado, ctx.arquivo(nome + ".xlsx"))
    colunas_valor = [c for c in consolidado.columns if "valor" in str(c).lower()]
    pagina_tabela(ctx.arquivo(nome + ".html"), consolidado, "Transações Pix — Banco Inter",
                  f"{len(ctx.entradas('pdfs'))} extrato(s)", moeda=colunas_valor, somar=colunas_valor,
                  filtros=[c for c in consolidado.columns if str(c).lower() in ("tipo", "empresa")])
    ctx.pagina = nome + ".html"
    ctx.resumo = f"{len(consolidado)} transação(ões) Pix."


@ferramenta(
    "consolidar-extratos",
    "Consolidar extratos bancários",
    "bancos",
    "Junta extratos em PDF do Bradesco, Banco do Brasil, Caixa, Inter e Stone numa aba por empresa, em ordem "
    "cronológica e com saldo consolidado, e confere cada extrato com o saldo do banco.",
    [Campo("pdfs", "Extratos (PDF)", "arquivos", aceita=".pdf"),
     Campo("empresa", "Empresa dos arquivos", "escolha",
           opcoes=[("", "Descobrir pelo nome do arquivo"), ("dluz", "Dluz Elétrica"), ("ilum", "Dluz Iluminação"),
                   ("cor", "Cor e Cia")], obrigatorio=False, padrao="",
           ajuda="No modo automático o nome do arquivo precisa conter dluz, ilum ou cor."),
     Campo("formato", "Formato", "escolha", opcoes=[("pdf", "PDF"), ("xlsx", "Excel"), ("ambos", "PDF e Excel")],
           padrao="ambos")],
    origem="Consolidar extratos/consolidar_extratos.py",
)
def consolidar_extratos(ctx):
    mod = ctx.script("consolidar_extratos.py")
    forcada = ctx.params.get("empresa") or ""
    dados = {}
    for arq in ctx.entradas("pdfs"):
        sigla = mod.EMPRESAS.get(forcada) if forcada else mod.detectar_empresa(str(arq))
        if not sigla:
            print(f"[AVISO] empresa não identificada no nome, ignorando: {arq.name}")
            continue
        texto = mod.extrair_texto(str(arq))
        parser = mod.detectar_banco(texto)
        if not parser:
            print(f"[AVISO] banco não identificado, ignorando: {arq.name}")
            continue
        ext = parser(texto, str(arq)) if parser is mod.parse_stone else parser(texto)
        dados.setdefault(sigla, []).append(ext)
        ok, calc = ext.confere()
        if ext.avisos:
            status = "DIVERGENTE: " + "; ".join(ext.avisos)
        else:
            status = "OK" if ok else f"DIVERGENTE (calculado {calc:.2f} ≠ extrato {ext.saldo_final_extrato})"
        print(f"{sigla:5} {ext.banco:16} {len(ext.transacoes):3} transações  anterior {ext.saldo_anterior:>12,.2f}  "
              f"disponível {ext.saldo_disponivel:>12,.2f}  [{status}]")
    if not dados:
        raise ErroAmigavel("Nenhum extrato reconhecido. Veja os avisos no registro abaixo.")
    ordem = ["Bradesco", "Banco do Brasil", "Caixa", "Inter", "Stone"]
    dados = {s: sorted(e, key=lambda x: ordem.index(x.banco)) for s, e in sorted(dados.items())}
    base = f"extratos_consolidados_{datetime.now():%Y%m%d_%H%M}"
    formato = ctx.params.get("formato") or "ambos"
    if formato in ("xlsx", "ambos"):
        wb = mod.Workbook()
        wb.remove(wb.active)
        for sigla, extratos in dados.items():
            mod.escrever_aba(wb, sigla, extratos)
        mod.escrever_resumo(wb, dados)
        wb.save(ctx.arquivo(base + ".xlsx"))
    if formato in ("pdf", "ambos"):
        mod.gerar_pdf(dados, str(ctx.arquivo(base + ".pdf")))
        ctx.pagina = base + ".pdf"
    n = sum(len(e) for e in dados.values())
    ctx.resumo = f"{n} extrato(s) de {len(dados)} empresa(s) consolidados."


@ferramenta(
    "nfe-excel",
    "NF-e (XML) para Excel",
    "bancos",
    "Lê o XML de uma ou mais notas fiscais eletrônicas e gera uma planilha por nota, com uma linha por item, "
    "todos os impostos e a linha de totais.",
    [Campo("xmls", "XML da NF-e", "arquivos", aceita=".xml")],
    origem="Gerar excel nf/gerar_tabela_nf.py",
)
def nfe_excel(ctx):
    mod = ctx.script("gerar_tabela_nf.py")
    feitos = 0
    for xml in ctx.entradas("xmls"):
        try:
            cab, itens = mod.extrair_itens(str(xml))
        except Exception as exc:
            print(f"✗ {xml.name}: não é um XML de NF-e válido ({exc})")
            continue
        numero = (cab.get("nNF") if isinstance(cab, dict) else None) or xml.stem
        saida = ctx.arquivo(f"NF_{numero}_itens_impostos.xlsx")
        mod.gerar_planilha(cab, itens, str(saida))
        print(f"✔ {xml.name}: {len(itens)} item(ns) → {saida.name}")
        feitos += 1
    if not feitos:
        raise ErroAmigavel("Nenhum XML de NF-e pôde ser lido.")
    ctx.resumo = f"{feitos} nota(s) convertida(s)."
