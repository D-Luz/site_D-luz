"""Carrega os scripts originais (pasta scripts_originais) como módulos.

Cada execução recebe uma cópia nova do módulo, então uma ferramenta pode trocar
as constantes do script (datas, empresas, senha) sem afetar outra execução que
esteja rodando ao mesmo tempo. Os scripts que tinham janela (tkinter ou
customtkinter) continuam importáveis num servidor sem essas bibliotecas: as
partes de janela não são usadas pelo portal.
"""

import importlib.util
import sys
import threading
import types
import uuid

from . import config, erp

_trava = threading.Lock()


class _Qualquer:
    """Objeto que aceita qualquer uso: serve de base de classe e de constante."""

    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return _Qualquer()

    def __getattr__(self, nome):
        return _Qualquer()


class _ModuloFalso(types.ModuleType):
    def __getattr__(self, nome):
        if nome.startswith("__"):
            raise AttributeError(nome)
        return type(nome, (_Qualquer,), {})


def _garantir_modulo(nome, submodulos=()):
    try:
        importlib.import_module(nome)
        for sub in submodulos:
            importlib.import_module(f"{nome}.{sub}")
    except Exception:
        pacote = _ModuloFalso(nome)
        pacote.__path__ = []
        sys.modules[nome] = pacote
        for sub in submodulos:
            mod = _ModuloFalso(f"{nome}.{sub}")
            sys.modules[f"{nome}.{sub}"] = mod
            setattr(pacote, sub, mod)


# Bibliotecas pesadas importadas uma vez, antes de qualquer tarefa: duas threads
# importando a mesma biblioteca ao mesmo tempo podem travar (deadlock de import).
PRE_IMPORTAR = (
    "pandas", "numpy", "openpyxl", "openpyxl.workbook", "openpyxl.chart", "openpyxl.styles",
    "openpyxl.formatting.rule", "openpyxl.utils", "firebirdsql", "pdfplumber", "fpdf", "fontTools.ttLib",
    "openpyxl.chart.shapes", "openpyxl.worksheet.properties", "reportlab.lib.pagesizes", "reportlab.platypus",
    "reportlab.lib.styles", "reportlab.lib.colors", "reportlab.lib.units", "PIL.Image", "requests",
    "xml.etree.ElementTree", "concurrent.futures",
)


def _aquecer_excel():
    """Grava e lê um Excel em memória: carrega as partes que o pandas importa sob demanda."""
    import io

    import pandas as pd

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame({"a": [1.5], "b": [pd.Timestamp("2026-01-01")]}).to_excel(w, index=False)
    buf.seek(0)
    pd.read_excel(buf)
_preparado = False


def _preparar():
    global _preparado
    if _preparado:
        return
    _garantir_modulo("tkinter", ("ttk", "filedialog", "messagebox", "font", "scrolledtext", "simpledialog"))
    _garantir_modulo("customtkinter")
    for nome in PRE_IMPORTAR:
        try:
            importlib.import_module(nome)
        except ImportError:
            pass
    _aquecer_excel()
    pasta = str(config.SCRIPTS)
    if pasta not in sys.path:
        sys.path.insert(0, pasta)
    _preparado = True


def preparar():
    """Chamado na subida do servidor."""
    with _trava:
        _preparar()


def carregar(arquivo, empresas=None):
    """Executa scripts_originais/<arquivo> como um módulo novo e devolve-o.

    Com `empresas` (de erp.empresas), troca hosts, bancos, usuário e senha do
    Firebird pelos valores configurados no portal.
    """
    caminho = config.SCRIPTS / arquivo
    nome = f"dluz_{caminho.stem.replace(' ', '_')}_{uuid.uuid4().hex[:8]}"
    with _trava:
        _preparar()
        spec = importlib.util.spec_from_file_location(nome, caminho)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[nome] = mod  # dataclasses e pickle procuram o módulo aqui
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(nome, None)
            raise
    if empresas is not None:
        erp.aplicar_no_script(mod, empresas)
    return mod
