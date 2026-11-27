# -*- coding: utf-8 -*-
"""
Painel de Orçamentos — Grupo Dluz · v4
=======================================
Preparado para compilação em .exe com PyInstaller (sys._MEIPASS).

Novidades da v4
---------------
* CORRIGIDO: abrir projeto restaurava só o cabeçalho (CTkCheckBox não tem
  .set(); agora as BooleanVars vinculadas é que são atualizadas).
* CORRIGIDO: validação de pagamento silenciosa — agora mostra o motivo
  quando o salvamento/geração é bloqueado.
* CORRIGIDO: PDF era salvo na pasta temporária do .exe (_MEIPASS), que é
  apagada ao fechar. Agora vai para a pasta "Orcamentos" ao lado do
  executável, com data/hora no nome.
* NOVO: na opção "À vista" é possível escolher entre informar o desconto
  (% ou R$) OU digitar diretamente o valor final à vista.
* NOVO: títulos pequenos (cabeçalhos) acima de todos os campos de
  preenchimento.
* NOVO: modo de edição real — editar não apaga mais o item da tabela;
  o botão vira "Salvar Alterações" e há "Cancelar Edição".
* Restauração completa do projeto (parcelas, modo à vista etc.).

Dependências:
  pip install customtkinter fpdf2 fonttools
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import customtkinter as ctk
from fpdf import FPDF
from tkinter import filedialog, messagebox, ttk

try:
    from fontTools.ttLib import TTFont
except Exception:
    TTFont = None


# =============================================================================
# CAMINHOS (compatível com PyInstaller)
# =============================================================================
def obter_caminho_recurso(caminho_relativo: str) -> Path:
    """Caminho absoluto de recursos embutidos (fonts, logo), compatível com .exe."""
    try:
        base_path = sys._MEIPASS  # executando como .exe (PyInstaller)
    except AttributeError:
        base_path = os.path.abspath(os.path.dirname(__file__))
    return Path(base_path) / caminho_relativo


def obter_pasta_saida() -> Path:
    """Pasta persistente para PDFs gerados (NUNCA usar _MEIPASS — é temporária)."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(os.path.abspath(os.path.dirname(__file__)))
    pasta = base / "Orcamentos"
    try:
        pasta.mkdir(exist_ok=True)
    except OSError:
        pasta = Path.home() / "Orcamentos Dluz"
        pasta.mkdir(exist_ok=True)
    return pasta


# =============================================================================
# CONFIGURAÇÃO GERAL
# =============================================================================
APP_TITLE = "Painel de Orçamentos - Grupo Dluz"
BASE_DIR = obter_caminho_recurso("")
FONT_DIR = obter_caminho_recurso("fonts")

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

CAPTION_FONT = ("Arial", 10)
CAPTION_COLOR = "#94a3b8"          # cinza-azulado discreto para os títulos pequenos


class DluzColors:
    SURFACE_PAGE = (247, 244, 241)
    SURFACE_CARD = (253, 251, 249)
    CREAM_100 = (253, 244, 237)
    CREAM_200 = (250, 232, 216)
    GRAPHITE_900 = (43, 41, 38)
    GRAPHITE_700 = (79, 74, 69)
    GRAPHITE_600 = (111, 102, 91)
    GRAPHITE_300 = (196, 189, 180)
    GRAPHITE_100 = (232, 226, 218)
    ACCENT = (190, 142, 46)
    ACCENT_DARK = (142, 106, 34)
    SUCCESS = (58, 110, 71)
    DANGER = (165, 42, 31)
    WHITE = (255, 255, 255)


# =============================================================================
# UTILITÁRIOS NUMÉRICOS / TEXTO
# =============================================================================
class NumeroInvalido(ValueError):
    pass


def parse_numero(valor_str: Any) -> float:
    valor_original = str(valor_str).strip()
    if not valor_original:
        return 0.0
    valor = valor_original.replace("R$", "").replace("r$", "").strip().replace(" ", "")
    if not re.fullmatch(r"-?[0-9.,]+", valor):
        raise NumeroInvalido(f"Valor numérico inválido: {valor_original}")
    if "," in valor and "." in valor:
        if valor.rfind(",") > valor.rfind("."):
            valor = valor.replace(".", "").replace(",", ".")
        else:
            valor = valor.replace(",", "")
    elif "," in valor:
        valor = valor.replace(",", ".")
    try:
        return float(valor)
    except ValueError as exc:
        raise NumeroInvalido(f"Valor numérico inválido: {valor_original}") from exc


def formatar_moeda(valor: float, com_simbolo: bool = False) -> str:
    texto = f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}" if com_simbolo else texto


def formatar_quantidade(valor: Any) -> str:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return ""
    if numero <= 0:
        return ""
    if numero.is_integer():
        return str(int(numero))
    return formatar_moeda(numero, com_simbolo=False).rstrip("0").rstrip(",")


def limpar_nome_arquivo(texto: str) -> str:
    texto = texto.strip() or "Cliente_Nao_Identificado"
    return re.sub(r"[^\w\-.À-ÿ]+", "_", texto.replace(" ", "_"), flags=re.UNICODE)[:80]


def abrir_arquivo(caminho: Path) -> None:
    if os.name == "nt":
        os.startfile(str(caminho))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(caminho)])
    else:
        subprocess.Popen(["xdg-open", str(caminho)])


def imprimir_arquivo(caminho: Path) -> None:
    if os.name == "nt":
        os.startfile(str(caminho), "print")
        return
    subprocess.run(["lp", str(caminho)], check=True)


# =============================================================================
# MODELO: ITEM DO ORÇAMENTO
# =============================================================================
@dataclass
class BudgetItem:
    id: str
    ambiente: str
    p_cod: str = ""; p_desc: str = ""; p_un: str = ""
    p_qtd: float = 0.0; p_vunit: float = 0.0; p_vtot: float = 0.0
    s_cod: str = ""; s_desc: str = ""; s_un: str = ""
    s_qtd: float = 0.0; s_vunit: float = 0.0; s_vtot: float = 0.0

    @classmethod
    def from_dict(cls, dados: Dict[str, Any]) -> "BudgetItem":
        pq = parse_numero(dados.get("p_qtd", 0)); pv = parse_numero(dados.get("p_vunit", 0))
        sq = parse_numero(dados.get("s_qtd", 0)); sv = parse_numero(dados.get("s_vunit", 0))
        return cls(
            id=str(dados.get("id") or uuid.uuid4()),
            ambiente=str(dados.get("ambiente", "")).strip().upper(),
            p_cod=str(dados.get("p_cod", "")).strip(),
            p_desc=str(dados.get("p_desc", "")).strip().upper(),
            p_un=str(dados.get("p_un", "")).strip().upper(),
            p_qtd=pq, p_vunit=pv,
            p_vtot=parse_numero(dados.get("p_vtot", pq * pv)) or (pq * pv),
            s_cod=str(dados.get("s_cod", "")).strip(),
            s_desc=str(dados.get("s_desc", "")).strip().upper(),
            s_un=str(dados.get("s_un", "")).strip().upper(),
            s_qtd=sq, s_vunit=sv,
            s_vtot=parse_numero(dados.get("s_vtot", sq * sv)) or (sq * sv),
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            "id": self.id, "ambiente": self.ambiente,
            "p_cod": self.p_cod, "p_desc": self.p_desc, "p_un": self.p_un,
            "p_qtd": self.p_qtd, "p_vunit": self.p_vunit, "p_vtot": self.p_vtot,
            "s_cod": self.s_cod, "s_desc": self.s_desc, "s_un": self.s_un,
            "s_qtd": self.s_qtd, "s_vunit": self.s_vunit, "s_vtot": self.s_vtot,
        }

    @property
    def total(self) -> float:
        return float(self.p_vtot or 0) + float(self.s_vtot or 0)

    def valores_tabela(self) -> Tuple[Any, ...]:
        return (
            self.ambiente, self.p_cod, self.p_desc, self.p_un, formatar_quantidade(self.p_qtd),
            formatar_moeda(self.p_vunit) if self.p_vunit > 0 else "",
            formatar_moeda(self.p_vtot) if self.p_vtot > 0 else "",
            self.s_cod, self.s_desc, self.s_un, formatar_quantidade(self.s_qtd),
            formatar_moeda(self.s_vunit) if self.s_vunit > 0 else "",
            formatar_moeda(self.s_vtot) if self.s_vtot > 0 else "",
        )


# =============================================================================
# MODELO: CONDIÇÃO DE PAGAMENTO
# =============================================================================
MODO_AVISTA_DESCONTO = "desconto"
MODO_AVISTA_VALOR = "valor_final"

ROTULO_MODO = {
    MODO_AVISTA_DESCONTO: "Desconto (% ou R$)",
    MODO_AVISTA_VALOR: "Valor final (R$)",
}
MODO_POR_ROTULO = {v: k for k, v in ROTULO_MODO.items()}


@dataclass
class PaymentCondition:
    incluir_avista: bool = True
    incluir_desconto_parcelas: bool = False
    incluir_parcelas_sem_desconto: bool = False
    avista_modo: str = MODO_AVISTA_DESCONTO     # "desconto" | "valor_final"
    desconto_avista_texto: str = ""             # usado no modo desconto
    valor_avista_texto: str = ""                # usado no modo valor_final
    desconto_parcelado_texto: str = ""
    parcelas_desconto: int = 1
    parcelas_sem_desconto: int = 1

    # ── Serialização ─────────────────────────────────────────────────────
    @classmethod
    def from_dict(cls, dados: Optional[Dict[str, Any]]) -> "PaymentCondition":
        if not isinstance(dados, dict):
            return cls()
        # Formato legado ("tipo": ...)
        if "tipo" in dados:
            tipo = str(dados.get("tipo") or "avista_desconto")
            d_perc = parse_numero(dados.get("desconto_percentual", 0))
            d_val = parse_numero(dados.get("desconto_valor", 0))
            parc = cls._normalizar_parcelas(dados.get("parcelas", 1))
            if tipo == "desconto_parcelas":
                return cls(incluir_avista=False, incluir_desconto_parcelas=True,
                           desconto_parcelado_texto=formatar_moeda(d_val, True) if d_val > 0 else "",
                           parcelas_desconto=parc, parcelas_sem_desconto=parc)
            if tipo == "parcelas_sem_desconto":
                return cls(incluir_avista=False, incluir_parcelas_sem_desconto=True,
                           parcelas_desconto=parc, parcelas_sem_desconto=parc)
            return cls(incluir_avista=True,
                       desconto_avista_texto=f"{formatar_quantidade(d_perc)}%" if d_perc > 0 else "",
                       parcelas_desconto=parc, parcelas_sem_desconto=parc)
        # Formato atual
        modo = str(dados.get("avista_modo", MODO_AVISTA_DESCONTO))
        if modo not in (MODO_AVISTA_DESCONTO, MODO_AVISTA_VALOR):
            modo = MODO_AVISTA_DESCONTO
        return cls(
            incluir_avista=bool(dados.get("incluir_avista", True)),
            incluir_desconto_parcelas=bool(dados.get("incluir_desconto_parcelas", False)),
            incluir_parcelas_sem_desconto=bool(dados.get("incluir_parcelas_sem_desconto", False)),
            avista_modo=modo,
            desconto_avista_texto=str(dados.get("desconto_avista_texto", "")),
            valor_avista_texto=str(dados.get("valor_avista_texto", "")),
            desconto_parcelado_texto=str(dados.get("desconto_parcelado_texto", "")),
            parcelas_desconto=cls._normalizar_parcelas(dados.get("parcelas_desconto", dados.get("parcelas", 1))),
            parcelas_sem_desconto=cls._normalizar_parcelas(dados.get("parcelas_sem_desconto", dados.get("parcelas", 1))),
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            "incluir_avista": self.incluir_avista,
            "incluir_desconto_parcelas": self.incluir_desconto_parcelas,
            "incluir_parcelas_sem_desconto": self.incluir_parcelas_sem_desconto,
            "avista_modo": self.avista_modo,
            "desconto_avista_texto": self.desconto_avista_texto,
            "valor_avista_texto": self.valor_avista_texto,
            "desconto_parcelado_texto": self.desconto_parcelado_texto,
            "parcelas_desconto": self._normalizar_parcelas(self.parcelas_desconto),
            "parcelas_sem_desconto": self._normalizar_parcelas(self.parcelas_sem_desconto),
        }

    # ── Validação / cálculo ─────────────────────────────────────────────
    def validar(self) -> None:
        if not (self.incluir_avista or self.incluir_desconto_parcelas
                or self.incluir_parcelas_sem_desconto):
            raise NumeroInvalido("Selecione pelo menos uma condição de pagamento.")
        if self.incluir_avista:
            if self.avista_modo == MODO_AVISTA_VALOR:
                if parse_numero(self.valor_avista_texto) <= 0:
                    raise NumeroInvalido("Informe o valor final à vista (R$).")
            else:
                self._interpretar_desconto(self.desconto_avista_texto, 100.0, True)
        if self.incluir_desconto_parcelas:
            self._interpretar_desconto(self.desconto_parcelado_texto, 100.0, True)

    def calcular_opcoes(self, total_bruto: float) -> List[Dict[str, Any]]:
        total_bruto = max(0.0, float(total_bruto or 0))
        opcoes: List[Dict[str, Any]] = []

        if self.incluir_avista:
            if self.avista_modo == MODO_AVISTA_VALOR:
                vf = parse_numero(self.valor_avista_texto)
                desconto = total_bruto - vf
                if desconto > 0.005:
                    descricao = f"Valor fechado à vista (desconto de {formatar_moeda(desconto, True)})"
                else:
                    descricao = "Valor fechado à vista"
                opcoes.append({
                    "codigo": "1", "titulo": "Opção 1 — À vista (Dinheiro + Pix)",
                    "descricao": descricao, "desconto": max(desconto, 0.0),
                    "total_final": vf, "parcelas": 1, "valor_parcela": vf,
                })
            else:
                di = self._interpretar_desconto(self.desconto_avista_texto, total_bruto)
                opcoes.append({
                    "codigo": "1", "titulo": "Opção 1 — Desconto À vista (Dinheiro + Pix)",
                    "descricao": di["descricao"], "desconto": di["valor_desconto"],
                    "total_final": total_bruto - di["valor_desconto"],
                    "parcelas": 1, "valor_parcela": total_bruto - di["valor_desconto"],
                })

        if self.incluir_desconto_parcelas:
            parc = self._normalizar_parcelas(self.parcelas_desconto)
            di = self._interpretar_desconto(self.desconto_parcelado_texto, total_bruto)
            tf = total_bruto - di["valor_desconto"]
            opcoes.append({
                "codigo": "2", "titulo": "Opção 2 — Desconto + parcelas",
                "descricao": f'{di["descricao"]} | Parcelamento: {parc}x',
                "desconto": di["valor_desconto"], "total_final": tf,
                "parcelas": parc, "valor_parcela": tf / parc,
            })

        if self.incluir_parcelas_sem_desconto:
            parc = self._normalizar_parcelas(self.parcelas_sem_desconto)
            opcoes.append({
                "codigo": "3", "titulo": "Opção 3 — Parcelas sem desconto",
                "descricao": f"Sem desconto | Parcelamento: {parc}x",
                "desconto": 0.0, "total_final": total_bruto,
                "parcelas": parc, "valor_parcela": total_bruto / parc,
            })

        return opcoes or [{
            "codigo": "1", "titulo": "Opção 1 — À vista (Dinheiro + Pix)",
            "descricao": "Sem desconto", "desconto": 0.0,
            "total_final": total_bruto, "parcelas": 1, "valor_parcela": total_bruto,
        }]

    @classmethod
    def _interpretar_desconto(cls, texto: Any, total_bruto: float,
                              validar_apenas: bool = False) -> Dict[str, Any]:
        txt = str(texto or "").strip()
        if not txt:
            return {"tipo": "valor", "valor_desconto": 0.0, "descricao": "Sem desconto"}
        if "%" in txt:
            perc = parse_numero(txt.replace("%", ""))
            if perc < 0 or perc > 100:
                raise NumeroInvalido("O desconto deve estar entre 0% e 100%.")
            vd = total_bruto * (perc / 100.0)
            return {"tipo": "percentual", "valor_desconto": vd,
                    "descricao": f"Desconto: {formatar_quantidade(perc)}% ({formatar_moeda(vd, True)})"}
        val = parse_numero(txt)
        if val < 0:
            raise NumeroInvalido("O desconto não pode ser negativo.")
        vd = val if validar_apenas else min(val, total_bruto)
        return {"tipo": "valor", "valor_desconto": vd,
                "descricao": f"Desconto: {formatar_moeda(vd, True)}"}

    @staticmethod
    def _normalizar_parcelas(valor: Any) -> int:
        try:
            return max(1, min(12, int(float(str(valor or "1").replace("x", "").replace("X", "").strip()))))
        except (ValueError, TypeError):
            return 1


# =============================================================================
# PERSISTÊNCIA DE PROJETOS (JSON)
# =============================================================================
class ProjectRepository:
    @staticmethod
    def salvar(cam: Path, cl: str, vd: str, ct: str, dt: str,
               it: Sequence[BudgetItem], pag: PaymentCondition) -> None:
        with cam.open("w", encoding="utf-8") as f:
            json.dump({
                "cliente": cl, "vendedor": vd, "contato": ct, "data": dt,
                "pagamento": pag.to_json(),
                "itens": [i.to_json() for i in it],
            }, f, ensure_ascii=False, indent=4)

    @staticmethod
    def abrir(cam: Path) -> Tuple[str, str, str, str, PaymentCondition, List[BudgetItem]]:
        with cam.open("r", encoding="utf-8") as f:
            d = json.load(f)
        itens = [BudgetItem.from_dict(i) for i in d.get("itens", [])]
        # O id vira o iid da linha na Treeview, que exige unicidade. Um arquivo
        # com ids repetidos (cópia manual, edição do JSON) derrubava a abertura
        # com TclError e deixava a lista dessincronizada da tabela.
        vistos: set = set()
        for item in itens:
            if item.id in vistos:
                item.id = str(uuid.uuid4())
            vistos.add(item.id)
        return (
            d.get("cliente", ""), d.get("vendedor", ""), d.get("contato", ""),
            d.get("data", ""), PaymentCondition.from_dict(d.get("pagamento")),
            itens,
        )


# =============================================================================
# FONTES DO PDF
# =============================================================================
class FontRegistry:
    def __init__(self, pdf: FPDF):
        self.pdf = pdf
        self._coverage = set()
        self.body = "DluzBody"; self.display = "DluzDisplay"; self.mono = "DluzMono"

    def register(self) -> None:
        br = self._first([FONT_DIR / "IBMPlexSans-Regular.ttf", FONT_DIR / "DejaVuSans.ttf",
                          Path("C:/Windows/Fonts/arial.ttf")])
        bb = self._first([FONT_DIR / "IBMPlexSans-Bold.ttf", FONT_DIR / "DejaVuSans-Bold.ttf",
                          Path("C:/Windows/Fonts/arialbd.ttf")]) or br
        mr = self._first([FONT_DIR / "IBMPlexMono-Regular.ttf", FONT_DIR / "DejaVuSansMono.ttf"]) or br
        mb = self._first([FONT_DIR / "IBMPlexMono-Bold.ttf", FONT_DIR / "DejaVuSansMono-Bold.ttf"]) or bb
        dr = self._first([FONT_DIR / "Newsreader-Regular.ttf", FONT_DIR / "DejaVuSerif.ttf"]) or br
        db = self._first([FONT_DIR / "Newsreader-SemiBold.ttf", FONT_DIR / "DejaVuSerif-Bold.ttf"]) or bb

        if not br:
            raise FileNotFoundError("Arial ou fontes na pasta 'fonts' não localizadas.")
        for fam, style, path in [(self.body, "", br), (self.body, "B", bb),
                                 (self.display, "", dr), (self.display, "B", db),
                                 (self.mono, "", mr), (self.mono, "B", mb)]:
            try:
                self.pdf.add_font(fam, style, str(path))
            except Exception:
                self.pdf.add_font(fam, style, str(path), uni=True)
            if TTFont:
                try:
                    self._coverage.update(TTFont(str(path), fontNumber=0).getBestCmap().keys())
                except Exception:
                    pass

    def safe_text(self, text: Any) -> str:
        t = str(text or "")
        if not self._coverage:
            return t
        return "".join(
            c if ord(c) in self._coverage or c in "\n\r\t" or ord(c) in {0x200D, 0xFE0F, 0xFE0E}
            else "□" for c in t
        )

    @staticmethod
    def _first(paths: List[Path]) -> Optional[Path]:
        for p in paths:
            if p.exists():
                return p
        return None


# =============================================================================
# GERADOR DE PDF
# =============================================================================
class BudgetPDF(FPDF):
    """FPDF com fundo e rodapé automáticos.

    Pintar o fundo em header() e o rodapé em footer() garante que QUALQUER
    quebra de página saia com a mesma aparência. Antes o rodapé era desenhado
    à mão antes de cada quebra manual, então uma quebra AUTOMÁTICA (disparada
    pelo fpdf ao estourar a margem) gerava uma página sem fundo e sem rodapé.
    """

    fontes: Optional["FontRegistry"] = None

    def header(self) -> None:
        self.set_fill_color(*DluzColors.SURFACE_PAGE)
        self.rect(0, 0, 297, 210, style="F")
        self.set_y(self.t_margin)

    def footer(self) -> None:
        if self.fontes is None:
            return
        self.set_y(195)
        self.set_draw_color(*DluzColors.GRAPHITE_100)
        self.line(10, 193, 286, 193)
        self.set_font(self.fontes.body, "B", 9)
        self.set_text_color(*DluzColors.GRAPHITE_600)
        self.cell(276, 5, self.fontes.safe_text("Validade da proposta: 7 dias"), align="C")


# Altura máxima utilizável de conteúdo: abaixo disso o rodapé começa.
LIMITE_CONTEUDO = 192


class BudgetPDFGenerator:
    def __init__(self):
        self.pdf = BudgetPDF(orientation="L", unit="mm", format="A4")
        # margem 18 => quebra automática em y > 192, igual às checagens manuais
        self.pdf.set_auto_page_break(auto=True, margin=18)
        self.fonts = FontRegistry(self.pdf)
        self.fonts.register()
        self.pdf.fontes = self.fonts
        self.logo_path = self._encontrar_logo()

    def _encontrar_logo(self) -> Optional[Path]:
        for nome in ["logo.png", "01 (1).png", "01 (1)_2.png", "image_b1ee11.png"]:
            cam = obter_caminho_recurso(nome)
            if cam.exists():
                return cam
        return None

    def _nova_pagina(self) -> None:
        self.pdf.add_page()  # header() pinta o fundo, footer() fecha a anterior

    def _ajustar(self, texto: Any, largura: float, fonte: str,
                 tamanho: float, estilo: str = "") -> str:
        """Corta o texto para caber em `largura` mm, com reticências.

        Cortar por número de caracteres (o antigo [:40]) não funciona: 40
        caracteres largos medem 73mm numa coluna de 60mm e invadiam a coluna
        seguinte. Aqui a medida é a largura real renderizada.
        """
        t = self._txt(texto)
        self.pdf.set_font(fonte, estilo, tamanho)
        if self.pdf.get_string_width(t) <= largura:
            return t
        retic = self._txt("…")
        if "□" in retic:
            retic = "..."
        limite = largura - self.pdf.get_string_width(retic)
        if limite <= 0:
            return ""
        while t and self.pdf.get_string_width(t) > limite:
            t = t[:-1]
        return t.rstrip() + retic

    def _titulo_ambiente(self, amb: str, continuacao: bool = False) -> None:
        rotulo = f"AMBIENTE: {amb}" + (" (continuação)" if continuacao else "")
        self.pdf.set_font(self.fonts.body, "B", 12)
        self.pdf.set_text_color(*DluzColors.GRAPHITE_900)
        self.pdf.set_draw_color(*DluzColors.GRAPHITE_300)
        self.pdf.cell(276, 8, self._txt(rotulo), ln=1, border="B")
        self.pdf.ln(2)

    def _cabecalho_tabela(self) -> None:
        self.pdf.set_font(self.fonts.body, "B", 8.5)
        self.pdf.set_fill_color(*DluzColors.GRAPHITE_900)
        self.pdf.set_text_color(*DluzColors.WHITE)
        self.pdf.cell(18, 7, self._txt(" Código"), border=0, fill=True)
        self.pdf.cell(60, 7, self._txt(" Produto"), border=0, fill=True)
        self.pdf.cell(15, 7, self._txt("UN"), border=0, fill=True, align="C")
        self.pdf.cell(15, 7, self._txt("QTD"), border=0, fill=True, align="C")
        self.pdf.cell(15, 7, self._txt("V.UNIT"), border=0, fill=True, align="R")
        self.pdf.cell(15, 7, self._txt("V.TOTAL"), border=0, fill=True, align="R")
        self.pdf.set_fill_color(*DluzColors.ACCENT)
        self.pdf.cell(18, 7, self._txt(" Código"), border=0, fill=True)
        self.pdf.cell(60, 7, self._txt(" Complemento"), border=0, fill=True)
        self.pdf.cell(15, 7, self._txt("UN"), border=0, fill=True, align="C")
        self.pdf.cell(15, 7, self._txt("QTD"), border=0, fill=True, align="C")
        self.pdf.cell(15, 7, self._txt("V.UNIT"), border=0, fill=True, align="R")
        self.pdf.cell(15, 7, self._txt("V.TOTAL "), border=0, fill=True, align="R", ln=1)

    def gerar(self, cl: str, vd: str, ct: str, dt: str, it: Sequence[BudgetItem],
              pag: PaymentCondition, saida: Path) -> Path:
        self._nova_pagina()

        # ── Logo e título ────────────────────────────────────────────
        ix = 10
        if self.logo_path:
            try:
                self.pdf.image(str(self.logo_path), x=10, y=10, w=45)
                ix = 60
            except Exception:
                pass

        self.pdf.set_xy(ix, 12)
        self.pdf.set_font(self.fonts.display, "B", 22)
        self.pdf.set_text_color(*DluzColors.GRAPHITE_900)
        self.pdf.cell(0, 10, self._txt("PROPOSTA COMERCIAL"), ln=1)
        self.pdf.set_x(ix)
        self.pdf.set_font(self.fonts.body, "", 10)
        self.pdf.set_text_color(*DluzColors.GRAPHITE_600)
        self.pdf.cell(0, 5, self._txt("Grupo Dluz — Painel de Orçamentos"), ln=1)

        # ── Cartão de dados do cliente ───────────────────────────────
        if self.pdf.get_y() < 35:
            self.pdf.set_y(40)
        self.pdf.set_fill_color(*DluzColors.SURFACE_CARD)
        self.pdf.set_draw_color(*DluzColors.GRAPHITE_100)
        self.pdf.rect(10, self.pdf.get_y() - 1, 276, 18, style="DF")

        y_i = self.pdf.get_y()
        # Cada valor é cortado pela largura REAL da célula (menos um respiro),
        # senão o nome do cliente invadia o rótulo "Vendedor:".
        txt_cl = self._ajustar(cl, 94, self.fonts.body, 10)
        txt_vd = self._ajustar(vd, 65, self.fonts.body, 10)
        txt_dt = self._ajustar(dt, 40, self.fonts.body, 10)
        txt_ct = self._ajustar(ct, 240, self.fonts.body, 10)
        self.pdf.set_font(self.fonts.body, "B", 10); self.pdf.set_text_color(*DluzColors.GRAPHITE_900)
        self.pdf.cell(17, 7, self._txt("Cliente:"), 0, 0)
        self.pdf.set_font(self.fonts.body, "", 10); self.pdf.cell(96, 7, txt_cl, 0, 0)
        self.pdf.set_font(self.fonts.body, "B", 10); self.pdf.cell(22, 7, self._txt("Vendedor:"), 0, 0)
        self.pdf.set_font(self.fonts.body, "", 10); self.pdf.cell(67, 7, txt_vd, 0, 0)
        self.pdf.set_font(self.fonts.body, "B", 10); self.pdf.cell(12, 7, self._txt("Data:"), 0, 0)
        self.pdf.set_font(self.fonts.body, "", 10); self.pdf.cell(0, 7, txt_dt, 0, 1)
        self.pdf.set_x(10)
        self.pdf.set_font(self.fonts.body, "B", 10); self.pdf.cell(17, 7, self._txt("Contato:"), 0, 0)
        self.pdf.set_font(self.fonts.body, "", 10); self.pdf.cell(0, 7, txt_ct, 0, 1)
        self.pdf.set_y(y_i + 22)

        # ── Itens por ambiente ───────────────────────────────────────
        tg = 0.0
        ag: "OrderedDict[str, List[BudgetItem]]" = OrderedDict()
        for i in it:
            ag.setdefault(i.ambiente, []).append(i)

        for amb, ita in ag.items():
            if self.pdf.get_y() + 24 > LIMITE_CONTEUDO:
                self._nova_pagina()
            self._titulo_ambiente(amb)
            self._cabecalho_tabela()

            for idx_i, item in enumerate(ita):
                if self.pdf.get_y() + 12 > LIMITE_CONTEUDO:
                    # Ao virar a página o cabeçalho precisa voltar, senão as
                    # linhas seguintes ficam órfãs (sem indicar V.UNIT/V.TOTAL).
                    self._nova_pagina()
                    self._titulo_ambiente(amb, continuacao=True)
                    self._cabecalho_tabela()
                self.pdf.set_fill_color(*(DluzColors.CREAM_100 if idx_i % 2 != 0 else DluzColors.SURFACE_CARD))
                self.pdf.set_text_color(*DluzColors.GRAPHITE_900)
                v = [
                    (18, self._ajustar(f" {item.p_cod}", 17, self.fonts.body, 7.8), "L", self.fonts.body),
                    (60, self._ajustar(f" {item.p_desc}", 59, self.fonts.body, 7.8), "L", self.fonts.body),
                    (15, self._ajustar(item.p_un, 14, self.fonts.body, 7.8), "C", self.fonts.body),
                    (15, formatar_quantidade(item.p_qtd), "C", self.fonts.body),
                    (15, formatar_moeda(item.p_vunit) if item.p_vunit > 0 else "", "R", self.fonts.mono),
                    (15, formatar_moeda(item.p_vtot) if item.p_vtot > 0 else "", "R", self.fonts.mono),
                    (18, self._ajustar(f" {item.s_cod}", 17, self.fonts.body, 7.8), "L", self.fonts.body),
                    (60, self._ajustar(f" {item.s_desc}", 59, self.fonts.body, 7.8), "L", self.fonts.body),
                    (15, self._ajustar(item.s_un, 14, self.fonts.body, 7.8), "C", self.fonts.body),
                    (15, formatar_quantidade(item.s_qtd), "C", self.fonts.body),
                    (15, formatar_moeda(item.s_vunit) if item.s_vunit > 0 else "", "R", self.fonts.mono),
                    (15, f"{formatar_moeda(item.s_vtot) if item.s_vtot > 0 else ''} ", "R", self.fonts.mono),
                ]
                for ix2, (lw, tx, al, fo) in enumerate(v):
                    self.pdf.set_font(fo, "", 7.8)
                    self.pdf.cell(lw, 6, self._txt(tx), border=0, align=al, fill=True,
                                  ln=1 if ix2 == 11 else 0)
                tg += item.total
            self.pdf.ln(6)

        # ── Total geral ──────────────────────────────────────────────
        if self.pdf.get_y() + 18 > LIMITE_CONTEUDO:
            self._nova_pagina()
        self.pdf.ln(5)
        self.pdf.set_font(self.fonts.body, "B", 14); self.pdf.set_text_color(*DluzColors.GRAPHITE_900)
        self.pdf.cell(236, 10, self._txt("TOTAL DA PROPOSTA:"), align="R")
        self.pdf.set_font(self.fonts.mono, "B", 16); self.pdf.set_text_color(*DluzColors.ACCENT_DARK)
        self.pdf.cell(40, 10, self._txt(formatar_moeda(tg, True)), align="R", ln=1)

        # ── Condições de pagamento ───────────────────────────────────
        ops = pag.calcular_opcoes(tg)
        alt = 16 + (len(ops) * 12)
        if self.pdf.get_y() + alt + 6 > LIMITE_CONTEUDO:
            self._nova_pagina()
        self.pdf.ln(2)
        y = self.pdf.get_y()
        self.pdf.set_fill_color(*DluzColors.SURFACE_CARD)
        self.pdf.set_draw_color(*DluzColors.GRAPHITE_100)
        self.pdf.rect(10, y, 276, alt, style="DF")
        self.pdf.set_xy(14, y + 3)
        self.pdf.set_font(self.fonts.body, "B", 10); self.pdf.set_text_color(*DluzColors.GRAPHITE_900)
        self.pdf.cell(0, 5, self._txt("CONDIÇÕES DE PAGAMENTO"), ln=1)
        self.pdf.set_x(14)
        self.pdf.set_font(self.fonts.body, "", 8); self.pdf.set_text_color(*DluzColors.GRAPHITE_600)
        self.pdf.cell(84, 5, self._txt("Opção"), 0, 0)
        self.pdf.cell(62, 5, self._txt("Desconto"), 0, 0)
        self.pdf.cell(48, 5, self._txt("Total final"), 0, 0, align="R")
        self.pdf.cell(70, 5, self._txt("Parcelamento"), 0, 1, align="R")
        self.pdf.set_draw_color(*DluzColors.GRAPHITE_100)
        self.pdf.line(14, self.pdf.get_y(), 282, self.pdf.get_y()); self.pdf.ln(1)
        for o in ops:
            self.pdf.set_x(14)
            self.pdf.set_font(self.fonts.body, "B", 8.6); self.pdf.set_text_color(*DluzColors.GRAPHITE_900)
            self.pdf.cell(84, 6, self._txt(o["titulo"]), 0, 0)
            self.pdf.set_font(self.fonts.body, "", 8.2); self.pdf.set_text_color(*DluzColors.GRAPHITE_600)
            self.pdf.cell(62, 6, self._txt(formatar_moeda(o["desconto"], True)), 0, 0)
            self.pdf.set_font(self.fonts.mono, "B", 8.8); self.pdf.set_text_color(*DluzColors.ACCENT_DARK)
            self.pdf.cell(48, 6, self._txt(formatar_moeda(o["total_final"], True)), 0, 0, align="R")
            self.pdf.cell(70, 6, self._txt(f"{int(o['parcelas'])}x de {formatar_moeda(o['valor_parcela'], True)}"), 0, 1, align="R")
            self.pdf.set_x(14)
            self.pdf.set_font(self.fonts.body, "", 7.7); self.pdf.set_text_color(*DluzColors.GRAPHITE_600)
            self.pdf.cell(0, 4, self._txt(o["descricao"]), 0, 1)
        self.pdf.set_y(y + alt + 4)

        self.pdf.output(str(saida))
        return saida

    def _txt(self, t: Any) -> str:
        return self.fonts.safe_text(t)


# =============================================================================
# APLICATIVO (UI)
# =============================================================================
class AppOrcamento(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x900")
        # Em notebooks com tela menor que a janela, o conteúdo passava da borda
        # inferior e os botões do rodapé ficavam inacessíveis (sem rolagem).
        self.minsize(1000, 600)
        self.lista_itens: List[BudgetItem] = []
        self.item_em_edicao: Optional[str] = None  # id do item em edição (modo edição)

        # Variáveis de pagamento
        self.total_var = ctk.StringVar(value="Total Atual: R$ 0,00")
        self.pagamento_avista_var = ctk.BooleanVar(value=True)
        self.pagamento_desconto_parcelas_var = ctk.BooleanVar(value=False)
        self.pagamento_parcelas_sem_desconto_var = ctk.BooleanVar(value=False)
        self.modo_avista_var = ctk.StringVar(value=ROTULO_MODO[MODO_AVISTA_DESCONTO])
        self.parcelas_desconto_var = ctk.StringVar(value="1")
        self.parcelas_sem_desconto_var = ctk.StringVar(value="1")

        self._montar_ui()

    # ── Helpers de UI ────────────────────────────────────────────────────
    def _campo(self, parent, titulo: str, width: int, placeholder: str = "",
               lado: str = "left", padx: int = 5) -> ctk.CTkEntry:
        """Entry com título pequeno em cima (cabeçalho do campo)."""
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.pack(side=lado, padx=padx)
        ctk.CTkLabel(box, text=titulo, font=CAPTION_FONT, text_color=CAPTION_COLOR,
                     anchor="w", height=14).pack(fill="x", padx=2)
        entry = ctk.CTkEntry(box, width=width, placeholder_text=placeholder)
        entry.pack()
        return entry

    def _campo_grid(self, parent, titulo: str, width: int, placeholder: str,
                    row: int, col: int) -> ctk.CTkEntry:
        """Entry com título pequeno em cima, posicionado via grid."""
        ctk.CTkLabel(parent, text=titulo, font=CAPTION_FONT, text_color=CAPTION_COLOR,
                     anchor="w", height=14).grid(row=row, column=col, padx=5, sticky="w")
        entry = ctk.CTkEntry(parent, width=width, placeholder_text=placeholder)
        entry.grid(row=row + 1, column=col, padx=5, pady=(0, 10))
        return entry

    # ── Montagem da interface ────────────────────────────────────────────
    def _montar_ui(self):
        # ── Rodapé FIXO (sempre visível, fora da área rolável) ───────
        # Total + GERAR PDF + IMPRIMIR ficam ancorados na base da janela,
        # então nunca somem mesmo em telas pequenas.
        rodape = ctk.CTkFrame(self, fg_color="#0b1220")
        rodape.pack(side="bottom", fill="x")
        rodape_in = ctk.CTkFrame(rodape, fg_color="transparent")
        rodape_in.pack(fill="x", padx=20, pady=12)
        ctk.CTkLabel(rodape_in, textvariable=self.total_var, font=("Arial", 18, "bold"),
                     text_color="#facc15").pack(side="right", padx=10)
        ctk.CTkButton(rodape_in, text="GERAR PDF", fg_color="#0f172a", height=40,
                      command=lambda: self.pdf("salvar")).pack(side="left", padx=10)
        ctk.CTkButton(rodape_in, text="IMPRIMIR", fg_color="#64748b", height=40,
                      command=lambda: self.pdf("imprimir")).pack(side="left", padx=10)

        # ── Área rolável que contém todo o restante do formulário ────
        cont = ctk.CTkScrollableFrame(self, fg_color="transparent")
        cont.pack(side="top", fill="both", expand=True)

        # Barra superior
        m = ctk.CTkFrame(cont, fg_color="transparent")
        m.pack(fill="x", padx=20, pady=10)
        ctk.CTkButton(m, text="Abrir Projeto", fg_color="#334155",
                      command=self.abrir_projeto).pack(side="left", padx=5)
        ctk.CTkButton(m, text="Salvar Projeto", fg_color="#2563eb",
                      command=self.salvar_projeto).pack(side="left", padx=5)
        ctk.CTkButton(m, text="Limpar Tudo", fg_color="#7f1d1d",
                      command=self.limpar_tudo).pack(side="right", padx=5)

        # ── Dados do cliente ─────────────────────────────────────────
        t = ctk.CTkFrame(cont)
        t.pack(pady=10, padx=20, fill="x")
        ctk.CTkLabel(t, text="DADOS DO CLIENTE", font=("Arial", 16, "bold")).pack(pady=(8, 2))
        dc = ctk.CTkFrame(t, fg_color="transparent")
        dc.pack(fill="x", padx=10, pady=(0, 8))
        self.entry_cliente = self._campo(dc, "Cliente", 340, "Nome do cliente")
        self.entry_vendedor = self._campo(dc, "Vendedor", 220, "Nome do vendedor")
        self.entry_contato = self._campo(dc, "Contato", 220, "Telefone / e-mail")
        self.entry_data = self._campo(dc, "Data", 130, "dd/mm/aaaa", lado="right")
        self.entry_data.insert(0, _dt.datetime.now().strftime("%d/%m/%Y"))

        # ── Inclusão de itens ────────────────────────────────────────
        mi = ctk.CTkFrame(cont)
        mi.pack(pady=5, padx=20, fill="x")
        amb_box = ctk.CTkFrame(mi, fg_color="transparent")
        amb_box.pack(pady=(10, 0))
        ctk.CTkLabel(amb_box, text="Ambiente", font=CAPTION_FONT,
                     text_color=CAPTION_COLOR, anchor="w", height=14).pack(fill="x", padx=2)
        self.entry_ambiente = ctk.CTkEntry(amb_box, placeholder_text="Ex: Garagem, Sala, Cozinha…", width=300)
        self.entry_ambiente.pack()

        fd = ctk.CTkFrame(mi, fg_color="transparent")
        fd.pack(fill="x", padx=10, pady=5)

        # PRODUTO (com títulos pequenos em cada campo)
        fp = ctk.CTkFrame(fd, border_width=2, border_color="#0f172a")
        fp.pack(side="left", fill="x", expand=True, padx=5)
        ctk.CTkLabel(fp, text="PRODUTO", text_color="#94a3b8",
                     font=("Arial", 14, "bold")).grid(row=0, column=0, columnspan=5, pady=(6, 4))
        self.p_cod = self._campo_grid(fp, "Cód.", 70, "Cód", 1, 0)
        self.p_desc = self._campo_grid(fp, "Descrição", 180, "Descrição", 1, 1)
        self.p_un = self._campo_grid(fp, "UN", 50, "UN", 1, 2)
        self.p_qtd = self._campo_grid(fp, "QTD", 50, "QTD", 1, 3)
        self.p_vunit = self._campo_grid(fp, "V. Unit (R$)", 80, "V.Unit", 1, 4)

        # COMPLEMENTO
        fs = ctk.CTkFrame(fd, border_width=2, border_color="#2563eb")
        fs.pack(side="right", fill="x", expand=True, padx=5)
        ctk.CTkLabel(fs, text="COMPLEMENTO", text_color="#60a5fa",
                     font=("Arial", 14, "bold")).grid(row=0, column=0, columnspan=5, pady=(6, 4))
        self.s_cod = self._campo_grid(fs, "Cód.", 70, "Cód", 1, 0)
        self.s_desc = self._campo_grid(fs, "Descrição", 180, "Descrição", 1, 1)
        self.s_un = self._campo_grid(fs, "UN", 50, "UN", 1, 2)
        self.s_qtd = self._campo_grid(fs, "QTD", 50, "QTD", 1, 3)
        self.s_vunit = self._campo_grid(fs, "V. Unit (R$)", 80, "V.Unit", 1, 4)

        # Atalho: Enter no V.Unit adiciona a linha
        self.p_vunit.bind("<Return>", lambda _e: self.add_item())
        self.s_vunit.bind("<Return>", lambda _e: self.add_item())

        botoes_item = ctk.CTkFrame(mi, fg_color="transparent")
        botoes_item.pack(pady=(5, 12))
        self.btn_add = ctk.CTkButton(botoes_item, text="Adicionar Linha",
                                     fg_color="#16a34a", command=self.add_item)
        self.btn_add.pack(side="left", padx=5)
        self.btn_cancelar_edicao = ctk.CTkButton(botoes_item, text="Cancelar Edição",
                                                 fg_color="#64748b",
                                                 command=self.cancelar_edicao)
        # (o botão de cancelar só aparece quando em modo de edição)

        # ── Tabela de itens ──────────────────────────────────────────
        fb = ctk.CTkFrame(cont)
        fb.pack(pady=5, padx=20, fill="both", expand=True)
        sx = ttk.Scrollbar(fb, orient="horizontal")
        sx.pack(side="bottom", fill="x")
        cols = ("Ambiente", "Cód", "Produto", "UN", "QTD", "V.UN", "V.TOT",
                "Cód_S", "Complemento", "UN_S", "QTD_S", "V.UN_S", "V.TOT_S")
        wids = [100, 50, 150, 40, 40, 80, 80, 50, 150, 40, 40, 80, 80]
        self.tb = ttk.Treeview(fb, columns=cols, show="headings", height=8,
                               xscrollcommand=sx.set)
        sx.config(command=self.tb.xview)
        for c, w in zip(cols, wids):
            self.tb.heading(c, text=c)
            self.tb.column(c, width=w, anchor="center")
        self.tb.pack(fill="both", expand=True, padx=5, pady=5)
        self.tb.bind("<Double-1>", lambda _e: self.edit_item())

        fc = ctk.CTkFrame(cont, fg_color="transparent")
        fc.pack(fill="x", padx=20)
        ctk.CTkButton(fc, text="Editar", fg_color="#d97706", command=self.edit_item).pack(side="left", padx=5)
        ctk.CTkButton(fc, text="Excluir", fg_color="#dc2626", command=self.del_item).pack(side="left", padx=5)

        # ── Pagamento ────────────────────────────────────────────────
        pg = ctk.CTkFrame(cont)
        pg.pack(fill="x", padx=20, pady=8)
        ctk.CTkLabel(pg, text="CONDIÇÕES DE PAGAMENTO", font=("Arial", 13, "bold")).pack(pady=(8, 0))
        pl = ctk.CTkFrame(pg, fg_color="transparent")
        pl.pack(fill="x", padx=10, pady=8)

        # Linha 0 — À vista (desconto OU valor final direto)
        self.ca = ctk.CTkCheckBox(pl, text="À vista (Dinheiro + Pix)",
                                  variable=self.pagamento_avista_var, command=self.upd_pag)
        self.ca.grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.menu_modo_avista = ctk.CTkOptionMenu(
            pl, values=list(ROTULO_MODO.values()), variable=self.modo_avista_var,
            width=160, command=lambda _: self.upd_pag())
        self.menu_modo_avista.grid(row=0, column=1, sticky="w", padx=6)
        self.ea = ctk.CTkEntry(pl, width=155, placeholder_text="Desc (% ou R$)")
        self.ea.grid(row=0, column=2, sticky="w", padx=6)
        self.lbl_dica_avista = ctk.CTkLabel(pl, text="", font=CAPTION_FONT,
                                            text_color=CAPTION_COLOR, anchor="w")
        self.lbl_dica_avista.grid(row=0, column=3, sticky="w", padx=6)

        # Linha 1 — Desconto + parcelas
        self.cp = ctk.CTkCheckBox(pl, text="Desconto + parcelas",
                                  variable=self.pagamento_desconto_parcelas_var,
                                  command=self.upd_pag)
        self.cp.grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.ep = ctk.CTkEntry(pl, width=155, placeholder_text="Desc (% ou R$)")
        self.ep.grid(row=1, column=1, sticky="w", padx=6, pady=4)
        po = [str(i) for i in range(1, 13)]
        self.mp = ctk.CTkOptionMenu(pl, values=po, variable=self.parcelas_desconto_var,
                                    width=80, command=lambda _: self.upd_pag())
        self.mp.grid(row=1, column=2, padx=6)

        # Linha 2 — Parcelas sem desconto
        self.cs = ctk.CTkCheckBox(pl, text="Parcelas sem desconto",
                                  variable=self.pagamento_parcelas_sem_desconto_var,
                                  command=self.upd_pag)
        self.cs.grid(row=2, column=0, sticky="w", padx=6, pady=4)
        self.ms = ctk.CTkOptionMenu(pl, values=po, variable=self.parcelas_sem_desconto_var,
                                    width=80, command=lambda _: self.upd_pag())
        self.ms.grid(row=2, column=1, padx=6)

        # Obs.: NÃO ligar <KeyRelease> a upd_pag aqui. upd_pag reconfigura
        # placeholder_text/state do CTkEntry, e fazer isso a cada tecla
        # reativa a lógica de placeholder do customtkinter, apagando o que
        # está sendo digitado. upd_pag só precisa rodar quando o modo/checkbox
        # muda (já tratado pelos comandos do OptionMenu e dos CheckBox).

        # (Total + GERAR PDF + IMPRIMIR agora ficam no rodapé fixo,
        #  criado no início de _montar_ui — sempre visível.)
        self.upd_pag()

    # ── Lógica dos itens ─────────────────────────────────────────────────
    def get_num(self, c: ctk.CTkEntry, n: str) -> Optional[float]:
        try:
            return parse_numero(c.get())
        except NumeroInvalido:
            messagebox.showwarning("Erro", f"Campo {n} inválido.")
            c.focus()
            return None

    def _ler_item_dos_campos(self, item_id: str) -> Optional[BudgetItem]:
        amb = self.entry_ambiente.get().strip().upper()
        if not amb:
            messagebox.showwarning("Aviso", "Ambiente obrigatório")
            return None
        pq = self.get_num(self.p_qtd, "QTD Produto"); pv = self.get_num(self.p_vunit, "V.Unit Produto")
        sq = self.get_num(self.s_qtd, "QTD Complemento"); sv = self.get_num(self.s_vunit, "V.Unit Complemento")
        if pq is None or pv is None or sq is None or sv is None:
            return None
        return BudgetItem(
            item_id, amb,
            self.p_cod.get().strip(), self.p_desc.get().strip().upper(),
            self.p_un.get().strip().upper(), pq, pv, pq * pv,
            self.s_cod.get().strip(), self.s_desc.get().strip().upper(),
            self.s_un.get().strip().upper(), sq, sv, sq * sv,
        )

    def _limpar_campos_item(self):
        for c in [self.p_cod, self.p_desc, self.p_un, self.p_qtd, self.p_vunit,
                  self.s_cod, self.s_desc, self.s_un, self.s_qtd, self.s_vunit]:
            c.delete(0, "end")

    def add_item(self):
        editando = self.item_em_edicao is not None
        item_id = self.item_em_edicao or str(uuid.uuid4())
        item = self._ler_item_dos_campos(item_id)
        if item is None:
            return

        if editando:
            # Substitui o item existente mantendo a posição na tabela
            for idx, antigo in enumerate(self.lista_itens):
                if antigo.id == item_id:
                    self.lista_itens[idx] = item
                    break
            self.tb.item(item_id, values=item.valores_tabela())
            self._sair_modo_edicao()
        else:
            self.lista_itens.append(item)
            self.tb.insert("", "end", iid=item.id, values=item.valores_tabela())

        self._limpar_campos_item()
        self.p_cod.focus()
        self.upd_tot()

    def edit_item(self):
        s = self.tb.selection()
        if not s:
            return messagebox.showinfo("Editar", "Selecione um item na tabela.")
        item = next((x for x in self.lista_itens if x.id == s[0]), None)
        if not item:
            return
        self.entry_ambiente.delete(0, "end"); self.entry_ambiente.insert(0, item.ambiente)
        pares = [
            (self.p_cod, item.p_cod), (self.p_desc, item.p_desc), (self.p_un, item.p_un),
            (self.p_qtd, formatar_quantidade(item.p_qtd)), (self.p_vunit, formatar_moeda(item.p_vunit) if item.p_vunit > 0 else ""),
            (self.s_cod, item.s_cod), (self.s_desc, item.s_desc), (self.s_un, item.s_un),
            (self.s_qtd, formatar_quantidade(item.s_qtd)), (self.s_vunit, formatar_moeda(item.s_vunit) if item.s_vunit > 0 else ""),
        ]
        for campo, valor in pares:
            campo.delete(0, "end")
            campo.insert(0, str(valor) if valor else "")
        # Entra em modo de edição (o item PERMANECE na tabela)
        self.item_em_edicao = item.id
        self.btn_add.configure(text="Salvar Alterações", fg_color="#d97706")
        self.btn_cancelar_edicao.pack(side="left", padx=5)

    def cancelar_edicao(self):
        self._sair_modo_edicao()
        self._limpar_campos_item()

    def _sair_modo_edicao(self):
        self.item_em_edicao = None
        self.btn_add.configure(text="Adicionar Linha", fg_color="#16a34a")
        self.btn_cancelar_edicao.pack_forget()

    def del_item(self):
        # A tabela está em selectmode="extended": o usuário pode marcar várias
        # linhas, então precisamos apagar TODAS as selecionadas, não só a 1ª.
        selecionados = self.tb.selection()
        if not selecionados:
            return
        if self.item_em_edicao in selecionados:
            self._sair_modo_edicao()
            self._limpar_campos_item()
        self.tb.delete(*selecionados)
        alvo = set(selecionados)
        self.lista_itens = [i for i in self.lista_itens if i.id not in alvo]
        self.upd_tot()

    def limpar_tudo(self):
        if self.lista_itens and not messagebox.askyesno(
                "Limpar tudo", "Apagar todos os itens e dados do orçamento atual?"):
            return
        self._sair_modo_edicao()
        self.lista_itens.clear()
        self.tb.delete(*self.tb.get_children())
        self.upd_tot()
        for c in [self.entry_cliente, self.entry_vendedor, self.entry_contato,
                  self.entry_ambiente]:
            c.delete(0, "end")
        self._limpar_campos_item()
        # A data e as condições de pagamento também fazem parte do orçamento:
        # deixá-las para trás fazia o próximo orçamento nascer com dados do anterior.
        self.entry_data.delete(0, "end")
        self.entry_data.insert(0, _dt.datetime.now().strftime("%d/%m/%Y"))
        self._resetar_pagamento()

    def _resetar_pagamento(self):
        self.pagamento_avista_var.set(True)
        self.pagamento_desconto_parcelas_var.set(False)
        self.pagamento_parcelas_sem_desconto_var.set(False)
        self.modo_avista_var.set(ROTULO_MODO[MODO_AVISTA_DESCONTO])
        self._modo_avista_atual = MODO_AVISTA_DESCONTO
        self.parcelas_desconto_var.set("1")
        self.parcelas_sem_desconto_var.set("1")
        for campo in (self.ea, self.ep):
            campo.configure(state="normal")
            campo.delete(0, "end")
        self.upd_pag()

    # ── Pagamento ────────────────────────────────────────────────────────
    def get_pag(self, silencioso: bool = False) -> Optional[PaymentCondition]:
        p = PaymentCondition(
            incluir_avista=self.pagamento_avista_var.get(),
            incluir_desconto_parcelas=self.pagamento_desconto_parcelas_var.get(),
            incluir_parcelas_sem_desconto=self.pagamento_parcelas_sem_desconto_var.get(),
            avista_modo=MODO_POR_ROTULO.get(self.modo_avista_var.get(), MODO_AVISTA_DESCONTO),
            desconto_avista_texto=self.ea.get() if self._modo_avista() == MODO_AVISTA_DESCONTO else "",
            valor_avista_texto=self.ea.get() if self._modo_avista() == MODO_AVISTA_VALOR else "",
            desconto_parcelado_texto=self.ep.get(),
            parcelas_desconto=PaymentCondition._normalizar_parcelas(self.parcelas_desconto_var.get()),
            parcelas_sem_desconto=PaymentCondition._normalizar_parcelas(self.parcelas_sem_desconto_var.get()),
        )
        try:
            p.validar()
            return p
        except Exception as exc:
            if not silencioso:
                messagebox.showwarning("Condição de pagamento", str(exc))
            return None

    def _modo_avista(self) -> str:
        return MODO_POR_ROTULO.get(self.modo_avista_var.get(), MODO_AVISTA_DESCONTO)

    def upd_pag(self):
        avista = self.pagamento_avista_var.get()
        # Trocar de modo muda o SIGNIFICADO do campo: um "5%" digitado como
        # desconto passaria a ser lido como valor final fechado. Limpar evita
        # que o orçamento saia errado (ou trave num aviso sem explicação).
        modo = self._modo_avista()
        if getattr(self, "_modo_avista_atual", None) not in (None, modo):
            self.ea.configure(state="normal")
            self.ea.delete(0, "end")
        self._modo_avista_atual = modo
        self.menu_modo_avista.configure(state="normal" if avista else "disabled")
        self.ea.configure(state="normal" if avista else "disabled")
        if modo == MODO_AVISTA_VALOR:
            novo_placeholder = "Valor à vista (R$)"
            self.lbl_dica_avista.configure(text="Digite o valor final fechado à vista.")
        else:
            novo_placeholder = "Desc (% ou R$)"
            self.lbl_dica_avista.configure(text="Ex.: 5%  ou  150,00")
        # Só reconfigura o placeholder quando ele realmente muda — reconfigurar
        # à toa reativa a lógica de placeholder do CTkEntry e apaga o digitado.
        if getattr(self, "_ea_placeholder_atual", None) != novo_placeholder:
            self._ea_placeholder_atual = novo_placeholder
            self.ea.configure(placeholder_text=novo_placeholder)
        self.ep.configure(state="normal" if self.pagamento_desconto_parcelas_var.get() else "disabled")
        self.mp.configure(state="normal" if self.pagamento_desconto_parcelas_var.get() else "disabled")
        self.ms.configure(state="normal" if self.pagamento_parcelas_sem_desconto_var.get() else "disabled")

    def upd_tot(self):
        self.total_var.set(f"Total: {formatar_moeda(sum(i.total for i in self.lista_itens), True)}")

    # ── Projeto (salvar / abrir) ─────────────────────────────────────────
    def salvar_projeto(self):
        pag = self.get_pag()
        if pag is None:
            return
        cliente = self.entry_cliente.get().strip()
        sugestao = f"Projeto_{limpar_nome_arquivo(cliente or 'Orcamento')}.json"
        cam = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON", "*.json")],
            initialfile=sugestao, title="Salvar Projeto")
        if not cam:
            return
        try:
            ProjectRepository.salvar(
                Path(cam), cliente, self.entry_vendedor.get(),
                self.entry_contato.get(), self.entry_data.get(),
                self.lista_itens, pag)
            messagebox.showinfo("Projeto salvo",
                                f"Projeto salvo com {len(self.lista_itens)} item(ns):\n{cam}")
        except Exception as exc:
            messagebox.showerror("Erro ao salvar", str(exc))

    def abrir_projeto(self):
        # Abrir substitui tudo que está na tela. Sem confirmação, um clique
        # errado descartava um orçamento inteiro já digitado.
        if self.lista_itens and not messagebox.askyesno(
                "Abrir projeto",
                f"O orçamento atual tem {len(self.lista_itens)} item(ns) que serão "
                "substituídos.\n\nDeseja continuar?"):
            return
        cam = filedialog.askopenfilename(filetypes=[("JSON", "*.json")],
                                         title="Abrir Projeto")
        if not cam:
            return
        try:
            cl, vd, ct, dt, p, itens = ProjectRepository.abrir(Path(cam))
        except Exception as exc:
            return messagebox.showerror("Erro ao abrir", f"Arquivo inválido:\n{exc}")

        self._sair_modo_edicao()

        # Cabeçalho
        for campo, valor in [(self.entry_cliente, cl), (self.entry_vendedor, vd),
                             (self.entry_contato, ct), (self.entry_data, dt)]:
            campo.delete(0, "end")
            campo.insert(0, valor)

        # Pagamento — CORRIGIDO: setar as BooleanVars, não os checkboxes
        self.pagamento_avista_var.set(p.incluir_avista)
        self.pagamento_desconto_parcelas_var.set(p.incluir_desconto_parcelas)
        self.pagamento_parcelas_sem_desconto_var.set(p.incluir_parcelas_sem_desconto)
        self.modo_avista_var.set(ROTULO_MODO.get(p.avista_modo, ROTULO_MODO[MODO_AVISTA_DESCONTO]))
        # Marca o modo como "já aplicado" para que o upd_pag() abaixo não
        # interprete a restauração como troca manual e limpe o valor salvo.
        self._modo_avista_atual = p.avista_modo
        self.parcelas_desconto_var.set(str(p.parcelas_desconto))
        self.parcelas_sem_desconto_var.set(str(p.parcelas_sem_desconto))
        # IMPORTANTE: escrever nos campos SEMPRE com state="normal". Se o
        # CTkEntry estiver desabilitado ao inserir um valor vazio, o
        # CustomTkinter grava o próprio texto do placeholder como se fosse
        # o valor real do campo (bug do widget) — corrompendo o projeto
        # restaurado. Só aplicamos o estado (habilitado/desabilitado)
        # correto depois, com o upd_pag() final.
        self.ea.configure(state="normal")
        self.ea.delete(0, "end")
        self.ea.insert(0, p.valor_avista_texto if p.avista_modo == MODO_AVISTA_VALOR
                       else p.desconto_avista_texto)
        self.ep.configure(state="normal")
        self.ep.delete(0, "end")
        self.ep.insert(0, p.desconto_parcelado_texto)
        self.upd_pag()  # reaplica estados (entries desabilitados etc.)

        # Itens
        self.lista_itens = itens
        self.tb.delete(*self.tb.get_children())
        for i in itens:
            self.tb.insert("", "end", iid=i.id, values=i.valores_tabela())
        self.upd_tot()
        aviso = ""
        if len(self.tb.get_children()) != len(itens):
            aviso = "\n\nAtenção: a tabela ficou dessincronizada. Confira os itens."
        messagebox.showinfo("Projeto aberto",
                            f"Projeto carregado com {len(itens)} item(ns).{aviso}")

    # ── PDF ──────────────────────────────────────────────────────────────
    def pdf(self, acao: str):
        if not self.lista_itens:
            return messagebox.showwarning("Sem itens", "Adicione pelo menos um item ao orçamento.")
        pag = self.get_pag()
        if pag is None:
            return
        cliente = self.entry_cliente.get().strip() or "Nao_Identificado"
        carimbo = _dt.datetime.now().strftime("%d-%m-%Y_%H%M")
        cam = obter_pasta_saida() / f"Orcamento_{limpar_nome_arquivo(cliente)}_{carimbo}.pdf"
        try:
            BudgetPDFGenerator().gerar(
                cliente, self.entry_vendedor.get(), self.entry_contato.get(),
                self.entry_data.get(), self.lista_itens, pag, cam)
        except PermissionError:
            return messagebox.showerror(
                "Erro PDF", "Não foi possível gravar o arquivo.\n"
                            "Feche o PDF se ele estiver aberto e tente novamente.")
        except Exception as exc:
            return messagebox.showerror("Erro PDF", str(exc))

        # O PDF já está gravado em disco. Falhar ao abrir/imprimir (visualizador
        # ausente, impressora offline) não pode derrubar o app nem esconder do
        # usuário onde o arquivo ficou.
        try:
            if acao == "salvar":
                abrir_arquivo(cam)
            else:
                imprimir_arquivo(cam)
        except Exception as exc:
            acao_txt = "abrir" if acao == "salvar" else "imprimir"
            messagebox.showwarning(
                "PDF gerado",
                f"O PDF foi gerado com sucesso, mas não foi possível {acao_txt}:\n"
                f"{exc}\n\nArquivo salvo em:\n{cam}")


if __name__ == "__main__":
    app = AppOrcamento()
    app.mainloop()
