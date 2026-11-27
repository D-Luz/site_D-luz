#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ATS — Consulta de Orçamento (saída no padrão Grupo Dluz)
========================================================
Pede o número do orçamento, busca no Firebird e gera um documento
HTML no padrão de design do Grupo Dluz, abrindo-o no navegador
(pronto para imprimir ou salvar em PDF).

Requer o arquivo 'dluz_assets.py' na mesma pasta (logo + fontes embutidas).

Dependências:
    pip install firebirdsql pandas
"""

import atexit
import os
import sys
import base64
import tempfile
import webbrowser
from datetime import datetime
from html import escape

import firebirdsql
import pandas as pd

from dluz_assets import LOGO_PNG_B64, FONTS

# ── Configuração das empresas ───────────────────────────────────
PORT    = 3050
CHARSET = "WIN1252"
USER    = "SYSDBA"
PASSWORD = "masterkey"

EMPRESAS = {
    "e": {
        "nome":     "D.LUZ Elétrica · Eraldo T. de Oliveira Ltda",
        "host":     "192.168.0.41",
        "database": "D:\\DLUZR\\BANCO\\DLUZR25.FB",
        "cnpj":     "04627585000125",
    },
    "i": {
        "nome":     "D.LUZ Iluminação",
        "host":     "192.168.0.250",
        "database": "D:\\ILUMIR\\BANCO\\ILUMIR.FB",
        "cnpj":     "30715699000137",
    },
}

# ── Query ───────────────────────────────────────────────────────
SQL = """
SELECT
    o.CODORC                                        AS "Nº Orçamento",
    o.CODEMPRESA                                    AS "Empresa",
    o.DATAORCAMENTO                                 AS "Data",
    o.VALIDADE                                      AS "Validade",
    TRIM(v.NOME)                                     AS "Vendedor",
    o.NOMECLIENTE                                   AS "Cliente",
    o.CONTATO                                       AS "Contato",
    o.CODCLIENTE                                    AS "Cód. Cliente",
    o.TOTALORCAMENTO                                AS "Total Orçamento",
    o.DESCONTOPERC                                  AS "Desconto %",
    o.DESCONTOVALOR                                 AS "Desconto R$",
    o.FRETE                                         AS "Frete",
    o.CANCELADO                                     AS "Cancelado",
    o.OBSERVACAO                                    AS "Observação",
    i.ORDEM                                         AS "Item",
    i.CODPROD                                       AS "Código",
    TRIM(p.DESCRICAO)                               AS "Descrição",
    i.QUANTIDADE                                    AS "Qtd",
    i.PRECOUNIT                                     AS "Val. Unit.",
    i.DESCONTOVLR                                   AS "Desconto Item",
    (i.QUANTIDADE * i.PRECOUNIT) - i.DESCONTOVLR    AS "Total Item"
FROM ORCCABE o
JOIN ORCITEM i  ON i.CODORC     = o.CODORC
               AND i.CODEMPRESA = o.CODEMPRESA
JOIN PRODUTO p  ON p.CODPROD    = i.CODPROD
LEFT JOIN VENDEND v ON v.CODVENDEDOR = o.CODVENDEDOR
WHERE o.CODORC = ?
ORDER BY i.ORDEM
"""

# A partir da atualização de set/2026, o ATS passou a gravar os
# orçamentos na tabela de pedidos (PEDIDOC/PEDIDOI, TIPOPEDIDO 55),
# não mais em ORCCABE/ORCITEM. Mesmas colunas de saída da query acima.
SQL_PEDIDO = """
SELECT
    o.CODPEDIDO                                     AS "Nº Orçamento",
    o.CODEMPRESA                                    AS "Empresa",
    o.DATAPEDIDO                                    AS "Data",
    o.DTVALIDADEDOCTO                                AS "Validade",
    TRIM(v.NOME)                                     AS "Vendedor",
    TRIM(c.NOME)                                     AS "Cliente",
    c.CONTATO                                        AS "Contato",
    o.CODCLIENTE                                    AS "Cód. Cliente",
    o.TOTALPEDIDO                                   AS "Total Orçamento",
    o.DESCONTOPERC                                  AS "Desconto %",
    o.DESCONTOVLR                                   AS "Desconto R$",
    o.FRETE                                         AS "Frete",
    CASE WHEN o.DOCTOEXPIRADOCANCELADO = 1 THEN 'S' ELSE 'N' END AS "Cancelado",
    o.OBSERVACAO                                    AS "Observação",
    i.SEQUENCIA                                     AS "Item",
    i.CODPROD                                       AS "Código",
    TRIM(p.DESCRICAO)                               AS "Descrição",
    i.QUANTIDADE                                    AS "Qtd",
    i.PRECOUNIT                                     AS "Val. Unit.",
    i.DESCONTOVLR                                   AS "Desconto Item",
    (i.QUANTIDADE * i.PRECOUNIT) - i.DESCONTOVLR    AS "Total Item"
FROM PEDIDOC o
JOIN PEDIDOI i  ON i.CODPEDIDO  = o.CODPEDIDO
               AND i.TIPOPEDIDO = o.TIPOPEDIDO
               AND i.CODEMPRESA = o.CODEMPRESA
JOIN PRODUTO p  ON p.CODPROD    = i.CODPROD
LEFT JOIN CLIENTE  c ON c.CODCLIENTE  = o.CODCLIENTE
LEFT JOIN VENDEND v ON v.CODVENDEDOR = o.CODVENDEDOR
WHERE o.CODPEDIDO = ? AND o.TIPOPEDIDO = '55'
  -- item retirado do orçamento não é apagado: fica com CANCELADO = 'S'
  AND COALESCE(i.CANCELADO, 'N') <> 'S'
ORDER BY i.SEQUENCIA
"""

# ── Limpeza de arquivos temporários ao encerrar ─────────────────
_temp_files: list[str] = []

@atexit.register
def _limpar_temporarios() -> None:
    for path in _temp_files:
        try:
            os.remove(path)
        except OSError:
            pass


# ── Helpers de formatação (padrão BR) ──────────────────────────
def brl(v, simbolo=True):
    try:
        s = f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "—"
    return f"R$ {s}" if simbolo else s


def num(v, casas=0):
    try:
        return f"{float(v):,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "—"


def dt(v):
    try:
        return pd.to_datetime(v).strftime("%d/%m/%Y")
    except Exception:
        return "—"


VALIDADE_DIAS = 7


def validade_dias(emissao) -> str:
    """Data de emissão + VALIDADE_DIAS, no formato 'dd/mm/aaaa (7 dias)'."""
    try:
        fim = pd.to_datetime(emissao) + pd.Timedelta(days=VALIDADE_DIAS)
        return f"{fim.strftime('%d/%m/%Y')} ({VALIDADE_DIAS} dias)"
    except Exception:
        return f"{VALIDADE_DIAS} dias"


def cnpj_fmt(v) -> str:
    d = "".join(c for c in str(v or "") if c.isdigit())
    if len(d) != 14:
        return ""
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def _safe_int(v) -> str:
    try:
        return str(int(float(v)))
    except (TypeError, ValueError):
        return "—"


# ── Geração do HTML (padrão Grupo Dluz) ────────────────────────
def _font_faces():
    blocos = []
    for fam, peso, b64 in FONTS:
        blocos.append(
            f"@font-face{{font-family:'{fam}';font-style:normal;font-weight:{peso};"
            f"font-display:swap;src:url(data:font/woff2;base64,{b64}) format('woff2');}}"
        )
    return "".join(blocos)


def _agrupar_itens(df: pd.DataFrame) -> pd.DataFrame:
    """Agrupa linhas do mesmo produto (Código+Descrição) somando qtd/valores."""
    agrupado = (
        df.groupby(["Código", "Descrição"], sort=False, as_index=False)
        .agg(
            Qtd=("Qtd", "sum"),
            **{"Desconto Item": ("Desconto Item", "sum")},
            **{"Total Item": ("Total Item", "sum")},
        )
    )
    agrupado["Val. Unit."] = (
        agrupado["Total Item"] + agrupado["Desconto Item"]
    ) / agrupado["Qtd"].replace(0, pd.NA)
    agrupado["Item"] = range(1, len(agrupado) + 1)
    return agrupado


def gerar_html(df: pd.DataFrame, empresa_nome: str, cnpj: str = "") -> str:
    cab = df.iloc[0]
    logo = f"data:image/png;base64,{LOGO_PNG_B64}"

    df_itens = _agrupar_itens(df)

    linhas = []
    for _, r in df_itens.iterrows():
        linhas.append(f"""
        <tr>
          <td class="c-item">{_safe_int(r['Item'])}</td>
          <td class="c-cod">{_safe_int(r['Código'])}</td>
          <td class="c-desc">{escape(str(r['Descrição']))}</td>
          <td class="c-num">{num(r['Qtd'], 0)}</td>
          <td class="c-money">{brl(r['Val. Unit.'], False)}</td>
          <td class="c-money">{brl(r['Desconto Item'], False)}</td>
          <td class="c-money c-total">{brl(r['Total Item'], False)}</td>
        </tr>""")
    corpo = "".join(linhas)

    n_itens    = len(df_itens)
    soma_itens = df_itens["Total Item"].sum()
    desc_total = float(cab["Desconto R$"]) if pd.notna(cab["Desconto R$"]) else 0.0
    desc_perc  = float(cab["Desconto %"]) if pd.notna(cab["Desconto %"]) else 0.0
    frete      = float(cab["Frete"]) if pd.notna(cab["Frete"]) else 0.0
    if pd.notna(cab["Total Orçamento"]):
        total_orc = float(cab["Total Orçamento"])
        # O ATS aplica o desconto % e guarda em DESCONTOVLR só o arredondamento;
        # o ajuste real é a diferença entre os itens e o total gravado.
        desc_total = round(soma_itens + frete - total_orc, 2)
    else:
        total_orc = soma_itens - desc_total + frete
    if desc_total < 0:
        ajuste_lbl, ajuste_vlr = "acréscimo", -desc_total
    else:
        ajuste_lbl = f"desconto ({num(desc_perc, 2).rstrip('0').rstrip(',')}%)" if desc_perc > 0 else "desconto"
        ajuste_vlr = desc_total
    cancelado   = str(cab.get("Cancelado", "")).upper() == "S"
    emitido     = datetime.now().strftime("%d/%m/%Y %H:%M")
    cliente     = escape(str(cab["Cliente"]))
    cnpj_txt    = cnpj_fmt(cnpj)
    rodape_emp  = f"{escape(empresa_nome)} · CNPJ {cnpj_txt}" if cnpj_txt else escape(empresa_nome)
    observacao  = escape(str(cab["Observação"])) if pd.notna(cab["Observação"]) else ""

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>Orçamento {_safe_int(cab['Nº Orçamento'])}</title>
<style>
{_font_faces()}
:root{{
  --surface-page:#f7f4f1; --surface-card:#fdfbf9; --cream-100:#fdf4ed;
  --graphite-900:#2b2926; --graphite-700:#4f4a45; --graphite-600:#6f665b;
  --graphite-500:#8e867a; --accent:#be8e2e;
  --money-default:#2b2926; --money-debit:#a52a1f; --money-credit:#3a6e47;
  --border-subtle:#e8e2da; --border-strong:#c4bdb4; --divider:#e8e2da;
  --font-serif:'Newsreader',Georgia,serif;
  --font-sans:'IBM Plex Sans',-apple-system,sans-serif;
  --font-mono:'IBM Plex Mono','Courier New',monospace;
}}
*{{box-sizing:border-box;}}
html{{-webkit-font-smoothing:antialiased;}}
body{{margin:0;background:var(--surface-page);font-family:var(--font-sans);
  color:var(--graphite-900);font-size:16px;line-height:1.5;}}
.page{{max-width:820px;margin:32px auto;background:var(--surface-card);
  border:1px solid var(--border-subtle);border-radius:12px;
  box-shadow:0 10px 24px rgba(43,41,38,.1),0 2px 6px rgba(43,41,38,.06);
  padding:48px 48px 40px;}}
.head{{display:flex;justify-content:space-between;align-items:flex-start;
  padding-bottom:24px;border-bottom:2px solid var(--graphite-900);}}
.head img{{height:46px;width:auto;display:block;}}
.head .sub{{margin-top:8px;font-size:12px;color:var(--graphite-500);letter-spacing:.04em;}}
.doc-meta{{text-align:right;}}
.doc-title{{font-family:var(--font-serif);font-weight:500;font-size:32px;
  line-height:1.1;margin:0;color:var(--graphite-900);}}
.doc-num{{font-family:var(--font-mono);font-size:18px;color:var(--accent);
  font-weight:600;margin-top:4px;}}
.badge-cancel{{display:inline-block;margin-top:6px;font-size:12px;
  background:var(--money-debit);color:#fff;padding:2px 10px;border-radius:9999px;}}
.info{{display:grid;grid-template-columns:1fr 1fr;gap:20px 40px;margin:28px 0 8px;}}
.info .label{{font-size:12px;color:var(--graphite-500);letter-spacing:.02em;margin-bottom:2px;}}
.info .value{{font-size:15px;color:var(--graphite-900);font-weight:500;}}
table{{width:100%;border-collapse:collapse;margin-top:20px;}}
thead th{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;
  color:var(--graphite-600);font-weight:600;text-align:left;
  padding:10px 8px;border-bottom:1px solid var(--border-strong);}}
thead th.r{{text-align:right;}}
tbody td{{padding:9px 8px;border-bottom:1px solid var(--divider);font-size:14px;vertical-align:top;}}
tbody tr:nth-child(even){{background:var(--cream-100);}}
.c-item{{color:var(--graphite-500);width:34px;}}
.c-cod{{font-family:var(--font-mono);color:var(--graphite-600);width:64px;}}
.c-desc{{color:var(--graphite-900);}}
.c-num{{font-family:var(--font-mono);text-align:right;width:62px;font-feature-settings:'tnum';}}
.c-money{{font-family:var(--font-mono);text-align:right;width:96px;
  color:var(--money-default);font-feature-settings:'tnum';}}
.c-total{{font-weight:600;}}
.totais{{display:flex;justify-content:flex-end;margin-top:24px;}}
.totais .box{{width:300px;}}
.totais .row{{display:flex;justify-content:space-between;padding:6px 0;
  font-size:14px;color:var(--graphite-700);}}
.totais .row .v{{font-family:var(--font-mono);font-feature-settings:'tnum';color:var(--money-default);}}
.totais .grand{{margin-top:8px;padding-top:12px;border-top:2px solid var(--graphite-900);
  display:flex;justify-content:space-between;align-items:baseline;}}
.totais .grand .k{{font-family:var(--font-serif);font-size:18px;font-weight:500;}}
.totais .grand .v{{font-family:var(--font-mono);font-size:24px;font-weight:600;
  color:var(--money-default);font-feature-settings:'tnum';}}
.obs{{margin-top:28px;padding:16px 20px;background:var(--cream-100);
  border:1px solid var(--border-subtle);border-radius:8px;}}
.obs-label{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;
  color:var(--graphite-500);font-weight:600;margin-bottom:6px;}}
.obs-body{{font-size:14px;color:var(--graphite-700);white-space:pre-wrap;line-height:1.6;}}
.foot{{margin-top:36px;padding-top:16px;border-top:1px solid var(--border-subtle);
  display:flex;justify-content:space-between;font-size:11px;
  color:var(--graphite-500);letter-spacing:.03em;}}
/* Impressão: página e margens fixas, para sair igual em qualquer PC/navegador */
@page{{size:A4 portrait;margin:12mm 12mm 14mm;}}
@media print{{
  html,body{{background:#fff;font-size:12px;}}
  .page{{background:#fff;box-shadow:none;border:none;border-radius:0;margin:0;max-width:none;width:100%;padding:0;}}
  .head img{{height:38px;}}
  .doc-title{{font-size:26px;}}
  .info{{margin:18px 0 4px;gap:12px 32px;}}
  .info .value{{font-size:13px;}}
  thead{{display:table-header-group;}}
  thead th{{font-size:9px;padding:6px 6px;}}
  tbody td{{font-size:11px;padding:5px 6px;}}
  tbody tr{{break-inside:avoid;}}
  tbody tr:nth-child(even){{background:var(--cream-100);-webkit-print-color-adjust:exact;print-color-adjust:exact;}}
  .totais,.obs,.foot{{break-inside:avoid;}}
  .totais .row{{font-size:12px;padding:4px 0;}}
  .totais .grand .v{{font-size:20px;}}
  .foot{{margin-top:24px;font-size:9px;}}
}}
</style>
</head>
<body>
  <div class="page">
    <div class="head">
      <div>
        <img src="{logo}" alt="D.LUZ">
        <div class="sub">{empresa_nome}</div>
      </div>
      <div class="doc-meta">
        <h1 class="doc-title">Orçamento</h1>
        <div class="doc-num">Nº {_safe_int(cab['Nº Orçamento'])}</div>
        {'<div class="badge-cancel">cancelado</div>' if cancelado else ''}
      </div>
    </div>
    <div class="info">
      <div class="field"><div class="label">cliente</div>
        <div class="value">{cliente}</div></div>
      <div class="field"><div class="label">contato</div>
        <div class="value">{escape(str(cab['Contato'])) if pd.notna(cab['Contato']) else '—'}</div></div>
      <div class="field"><div class="label">vendedor</div>
        <div class="value">{escape(str(cab['Vendedor'])) if pd.notna(cab['Vendedor']) else '—'}</div></div>
      <div class="field"><div class="label">data de emissão</div>
        <div class="value">{dt(cab['Data'])}</div></div>
      <div class="field"><div class="label">validade</div>
        <div class="value">{validade_dias(cab['Data'])}</div></div>
    </div>
    <table>
      <thead>
        <tr><th>#</th><th>cód.</th><th>descrição</th>
          <th class="r">qtd</th><th class="r">val. unit.</th>
          <th class="r">desconto</th><th class="r">total</th></tr>
      </thead>
      <tbody>{corpo}</tbody>
    </table>
    <div class="totais">
      <div class="box">
        <div class="row"><span>{n_itens} {'item' if n_itens == 1 else 'itens'} · subtotal</span><span class="v">{brl(soma_itens)}</span></div>
        <div class="row"><span>{ajuste_lbl}</span><span class="v">{brl(ajuste_vlr)}</span></div>
        <div class="row"><span>frete</span><span class="v">{brl(frete)}</span></div>
        <div class="grand"><span class="k">Total</span><span class="v">{brl(total_orc)}</span></div>
      </div>
    </div>
    {f'<div class="obs"><div class="obs-label">observações</div><div class="obs-body">{observacao}</div></div>' if observacao else ''}
    <div class="foot">
      <span>{rodape_emp}</span>
      <span>Documento gerado em {emitido} · Sistema ATS · Grupo Dluz</span>
    </div>
  </div>
</body>
</html>"""


# ── Execução ────────────────────────────────────────────────────
def _selecionar_empresa() -> dict:
    while True:
        raw = input("Empresa — [E]létrica / [I]luminação (ou 'sair'): ").strip().lower()
        if raw in ("sair", "s", "q", "exit"):
            raise SystemExit
        if raw in EMPRESAS:
            emp = EMPRESAS[raw]
            print(f"  ✔ {emp['nome']}\n")
            return emp
        print("  ✗ Digite 'e' para Elétrica ou 'i' para Iluminação.\n")


def main():
    empresa = _selecionar_empresa()

    while True:
        raw = input("Número do orçamento (ou 'sair' / 'empresa'): ").strip()

        if raw.lower() in ("sair", "s", "q", "exit"):
            break

        if raw.lower() in ("empresa", "e", "i"):
            if raw.lower() == "empresa":
                empresa = _selecionar_empresa()
            elif raw.lower() in EMPRESAS:
                empresa = EMPRESAS[raw.lower()]
                print(f"  ✔ {empresa['nome']}\n")
            continue

        try:
            numero = int(raw)
        except ValueError:
            print("  ✗ Número inválido. Digite apenas dígitos.\n")
            continue

        try:
            con = firebirdsql.connect(
                host=empresa["host"], database=empresa["database"], port=PORT,
                user=USER, password=PASSWORD, charset=CHARSET,
            )
        except Exception as e:
            print(f"  ✗ Não foi possível conectar ao banco: {e}\n")
            continue

        try:
            # pd.read_sql não passa parâmetros corretamente com firebirdsql —
            # usar cursor diretamente garante compatibilidade
            numero_fmt = str(numero).zfill(8)
            cur = con.cursor()
            cur.execute(SQL, (numero_fmt,))
            colunas = [d[0] for d in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=colunas)

            if df.empty:
                # Desde a atualização do ATS (set/2026), orçamentos ficam em
                # PEDIDOC/PEDIDOI (TIPOPEDIDO 55) em vez de ORCCABE/ORCITEM.
                cur = con.cursor()
                cur.execute(SQL_PEDIDO, (numero_fmt,))
                colunas = [d[0] for d in cur.description]
                df = pd.DataFrame(cur.fetchall(), columns=colunas)
        except Exception as e:
            print(f"  ✗ Erro ao executar consulta: {e}\n")
            continue
        finally:
            con.close()

        if df.empty:
            print(f"  ✗ Orçamento {numero} não encontrado. Tente outro número.\n")
            continue

        html = gerar_html(df, empresa["nome"], empresa.get("cnpj", ""))
        saida = os.path.join(tempfile.gettempdir(), f"orcamento_{numero}.html")
        with open(saida, "w", encoding="utf-8") as f:
            f.write(html)
        _temp_files.append(saida)

        print(f"  ✔ Documento gerado: {saida}\n")
        webbrowser.open("file://" + saida)


if __name__ == "__main__":
    main()
