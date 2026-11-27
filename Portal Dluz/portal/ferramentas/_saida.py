"""Saídas comuns das ferramentas: Excel formatado e tabela HTML interativa.

As ferramentas que tinham janela com tabela, filtros e totais (tkinter) passam
a gerar uma página HTML autônoma com a mesma ideia: busca, filtro por coluna,
ordenação por clique e totais das linhas visíveis. A página funciona dentro do
portal e também se for baixada e aberta no computador.
"""

import html
import json
import math
from datetime import date, datetime

import pandas as pd

GRAFITE = "241F18"
AMBAR = "946517"
CREME = "F7F3EA"


def nome_arquivo(texto):
    proibidos = '<>:"/\\|?*'
    limpo = "".join("_" if c in proibidos or ord(c) < 32 else c for c in str(texto)).strip()
    return limpo.replace(" ", "_") or "arquivo"


def salvar_excel(caminho, abas, moeda=(), inteiros=()):
    """Grava {nome_aba: DataFrame} com cabeçalho grafite, larguras e formato de moeda."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    with pd.ExcelWriter(caminho, engine="openpyxl") as escritor:
        for aba, df in abas.items():
            df.to_excel(escritor, sheet_name=str(aba)[:31], index=False)
            ws = escritor.sheets[str(aba)[:31]]
            for celula in ws[1]:
                celula.font = Font(bold=True, color=CREME)
                celula.fill = PatternFill("solid", start_color=GRAFITE)
                celula.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws.freeze_panes = "A2"
            if len(df.columns):
                ws.auto_filter.ref = ws.dimensions
            for i, coluna in enumerate(df.columns, start=1):
                letra = get_column_letter(i)
                amostra = [len(str(coluna))] + [len(str(v)) for v in df[coluna].head(300).tolist()]
                ws.column_dimensions[letra].width = max(8, min(50, max(amostra) + 2))
                formato = None
                if coluna in moeda:
                    formato = '#,##0.00;[Red]-#,##0.00'
                elif coluna in inteiros:
                    formato = "#,##0"
                if formato:
                    for linha in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                        linha[0].number_format = formato
    return caminho


def _valor_json(v):
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, (pd.Timestamp, datetime, date)) and pd.isna(v):
        return None
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.strftime("%d/%m/%Y") if (v.hour, v.minute) == (0, 0) else v.strftime("%d/%m/%Y %H:%M")
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")
    if hasattr(v, "item"):
        try:
            return _valor_json(v.item())
        except (ValueError, AttributeError):
            pass
    if isinstance(v, (int, float, str, bool)):
        return v
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v)


def pagina_tabela(caminho, df, titulo, subtitulo="", moeda=(), somar=(), filtros=(), cartoes=None):
    """Grava uma página HTML com a tabela de `df`.

    moeda:   colunas mostradas como R$
    somar:   colunas com total no rodapé (das linhas visíveis)
    filtros: colunas com caixa de seleção para filtrar
    cartoes: [(rótulo, coluna)] — cartões de resumo com a soma da coluna
    """
    colunas = [str(c) for c in df.columns]
    linhas = [[_valor_json(v) for v in linha] for linha in df.itertuples(index=False, name=None)]
    dados = {
        "colunas": colunas,
        "linhas": linhas,
        "moeda": [c for c in colunas if c in set(map(str, moeda))],
        "somar": [c for c in colunas if c in set(map(str, somar))],
        "filtros": [c for c in colunas if c in set(map(str, filtros))],
        "cartoes": [[r, str(c)] for r, c in (cartoes or []) if str(c) in colunas],
    }
    conteudo = MODELO_TABELA.replace("__TITULO__", html.escape(titulo)).replace(
        "__SUBTITULO__", html.escape(subtitulo)
    ).replace("__DADOS__", json.dumps(dados, ensure_ascii=False).replace("</", "<\\/"))
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(conteudo)
    return caminho


MODELO_TABELA = r"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITULO__</title>
<style>
:root{--creme:#F7F3EA;--papel:#FFFDF8;--grafite:#241F18;--ambar:#946517;--borda:#DDD3BF;--suave:#6B6255;--verm:#B3261E}
*{box-sizing:border-box}
body{margin:0;background:var(--creme);color:var(--grafite);font:14px/1.45 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif}
header{padding:18px 20px 8px}
h1{font:600 22px/1.2 Georgia,"Newsreader",serif;margin:0}
.sub{color:var(--suave);margin-top:4px}
.barra{display:flex;flex-wrap:wrap;gap:8px;align-items:end;padding:8px 20px 12px}
.barra label{display:flex;flex-direction:column;font-size:12px;color:var(--suave);gap:3px}
input,select,button{font:inherit;padding:6px 8px;border:1px solid var(--borda);border-radius:6px;background:var(--papel);color:var(--grafite)}
button{cursor:pointer}
.cartoes{display:flex;flex-wrap:wrap;gap:10px;padding:0 20px 12px}
.cartao{background:var(--papel);border:1px solid var(--borda);border-radius:8px;padding:10px 14px;min-width:150px}
.cartao b{display:block;font:600 18px "IBM Plex Mono",ui-monospace,Consolas,monospace;font-variant-numeric:tabular-nums}
.cartao span{font-size:12px;color:var(--suave)}
.contagem{padding:0 20px 6px;color:var(--suave);font-size:12px}
.envolve{overflow:auto;margin:0 20px 20px;border:1px solid var(--borda);border-radius:8px;background:var(--papel);max-height:calc(100vh - 210px)}
table{border-collapse:collapse;width:100%;font-size:13px}
th{position:sticky;top:0;background:var(--grafite);color:var(--creme);text-align:left;padding:7px 8px;cursor:pointer;white-space:nowrap;user-select:none}
th.num,td.num{text-align:right}
td{padding:5px 8px;border-bottom:1px solid #EFE8DA;white-space:nowrap}
tr:nth-child(even) td{background:#FBF8F1}
td.num{font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace;font-variant-numeric:tabular-nums}
td.neg{color:var(--verm)}
tfoot td{position:sticky;bottom:0;background:#EDE3CE;font-weight:600}
@media (prefers-color-scheme:dark){:root{--creme:#1B1813;--papel:#24201A;--grafite:#EDE6D8;--borda:#3A342A;--suave:#A99F8E}
 th{background:#3A342A;color:#F7F3EA} tr:nth-child(even) td{background:#2A251E} td{border-bottom-color:#332D24} tfoot td{background:#3A3022}}
</style></head><body>
<header><h1>__TITULO__</h1><div class="sub">__SUBTITULO__</div></header>
<div class="cartoes" id="cartoes"></div>
<div class="barra" id="barra"><label>Buscar<input id="busca" type="search" placeholder="Digite para filtrar"></label></div>
<div class="contagem" id="contagem"></div>
<div class="envolve"><table><thead><tr id="cab"></tr></thead><tbody id="corpo"></tbody><tfoot><tr id="rod"></tr></tfoot></table></div>
<script>
const D=__DADOS__;
const fmtMoeda=new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'});
const fmtNum=new Intl.NumberFormat('pt-BR',{maximumFractionDigits:4});
const idx=Object.fromEntries(D.colunas.map((c,i)=>[c,i]));
const numerica=D.colunas.map((c,i)=>D.linhas.length>0&&D.linhas.every(l=>l[i]===null||typeof l[i]==='number'));
let ordem={col:-1,asc:true};const filtros={};
function fmt(v,i){if(v===null||v===undefined)return'';if(typeof v==='number'){return D.moeda.includes(D.colunas[i])?fmtMoeda.format(v):fmtNum.format(v)}return String(v)}
function el(t,txt,cls){const e=document.createElement(t);if(txt!==undefined)e.textContent=txt;if(cls)e.className=cls;return e}
const cab=document.getElementById('cab');
D.colunas.forEach((c,i)=>{const th=el('th',c,numerica[i]?'num':'');th.onclick=()=>{ordem={col:i,asc:ordem.col===i?!ordem.asc:true};desenhar()};cab.appendChild(th)});
const barra=document.getElementById('barra');
D.filtros.forEach(c=>{const i=idx[c];const vals=[...new Set(D.linhas.map(l=>l[i]).filter(v=>v!==null))].sort();
 const lab=el('label',c);const s=el('select');s.appendChild(new Option('Todos',''));vals.forEach(v=>s.appendChild(new Option(String(v),String(v))));
 s.onchange=()=>{filtros[c]=s.value;desenhar()};lab.appendChild(s);barra.appendChild(lab)});
const limpar=el('button','Limpar filtros');limpar.onclick=()=>{document.getElementById('busca').value='';barra.querySelectorAll('select').forEach(s=>s.value='');for(const k in filtros)filtros[k]='';desenhar()};barra.appendChild(limpar);
const csv=el('button','Baixar CSV');csv.onclick=()=>{const ls=visiveis();const esc=v=>{v=v===null?'':String(v);return /[";\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v};
 const t=[D.colunas.map(esc).join(';')].concat(ls.map(l=>l.map(v=>typeof v==='number'?String(v).replace('.',','):esc(v)).join(';'))).join('\n');
 const a=el('a');a.href=URL.createObjectURL(new Blob(['﻿'+t],{type:'text/csv'}));a.download=(document.title||'tabela')+'.csv';a.click()};barra.appendChild(csv);
document.getElementById('busca').oninput=desenhar;
function visiveis(){const q=document.getElementById('busca').value.trim().toLowerCase();
 let ls=D.linhas.filter(l=>Object.entries(filtros).every(([c,v])=>!v||String(l[idx[c]])===v)&&(!q||l.some(v=>v!==null&&String(v).toLowerCase().includes(q))));
 if(ordem.col>=0){const i=ordem.col,s=ordem.asc?1:-1;ls=ls.slice().sort((a,b)=>{const x=a[i],y=b[i];if(x===y)return 0;if(x===null)return 1;if(y===null)return-1;return(typeof x==='number'&&typeof y==='number'?x-y:String(x).localeCompare(String(y),'pt-BR',{numeric:true}))*s})}
 return ls}
function desenhar(){const ls=visiveis();const corpo=document.getElementById('corpo');corpo.replaceChildren();
 const lim=Math.min(ls.length,3000);
 for(let r=0;r<lim;r++){const tr=el('tr');ls[r].forEach((v,i)=>{const td=el('td',fmt(v,i),numerica[i]?'num':'');if(typeof v==='number'&&v<0)td.classList.add('neg');tr.appendChild(td)});corpo.appendChild(tr)}
 document.getElementById('contagem').textContent=ls.length.toLocaleString('pt-BR')+' linha(s)'+(ls.length>lim?' — mostrando as primeiras '+lim+' (use os filtros ou baixe o Excel/CSV)':'');
 const rod=document.getElementById('rod');rod.replaceChildren();
 if(D.somar.length){D.colunas.forEach((c,i)=>{let t='';if(i===0)t='TOTAL';if(D.somar.includes(c))t=fmt(ls.reduce((s,l)=>s+(typeof l[i]==='number'?l[i]:0),0),i);rod.appendChild(el('td',t,numerica[i]?'num':''))})}
 const cart=document.getElementById('cartoes');cart.replaceChildren();
 D.cartoes.forEach(([rot,c])=>{const i=idx[c];const tot=ls.reduce((s,l)=>s+(typeof l[i]==='number'?l[i]:0),0);const d=el('div','','cartao');d.appendChild(el('b',fmt(tot,i)));d.appendChild(el('span',rot));cart.appendChild(d)})}
desenhar();
</script></body></html>
"""
