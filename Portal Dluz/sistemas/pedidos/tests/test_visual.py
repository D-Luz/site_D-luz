"""Contrato do estilo.css: responsivo, acessibilidade e que toda classe usada existe."""

import ast
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
CSS = (RAIZ / "app" / "static" / "estilo.css").read_text(encoding="utf-8")
TEMPLATES = sorted((RAIZ / "app" / "templates").glob("*.html"))
CODIGO = sorted((RAIZ / "app").glob("*.py"))


def sem_comentarios(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def blocos(css, abertura):
    """Corpo de cada bloco cuja linha de abertura contém `abertura` (chaves balanceadas)."""
    achados = []
    css = sem_comentarios(css)
    for achado in re.finditer(re.escape(abertura) + r"[^{]*\{", css):
        nivel, i = 1, achado.end()
        while nivel:
            nivel += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        achados.append(css[achado.end():i - 1])
    return achados


def regra(css, seletor):
    """Corpo da primeira regra (fora de @media) cujo seletor contém `seletor`."""
    fora = css
    for bloco in blocos(css, "@media"):
        fora = fora.replace(bloco, "")
    for achado in re.finditer(r"([^{}]+)\{([^{}]*)\}", sem_comentarios(fora)):
        if seletor in achado.group(1):
            return achado.group(2)
    raise AssertionError(f"regra {seletor!r} não encontrada")


# --- responsivo ---------------------------------------------------------------


def test_lista_vira_tabela_a_partir_de_45em_e_usuarios_so_a_partir_de_64em():
    (largo,) = blocos(CSS, "@media (min-width: 45em)")
    (mais_largo,) = blocos(CSS, "@media (min-width: 64em)")

    assert ".tabela-pedidos { display: table" in largo
    assert ".tabela-usuarios" not in largo  # seis colunas não cabem em 720 px
    assert ".tabela-usuarios { display: table" in mais_largo
    assert ".tabela-pedidos { display: table" not in mais_largo  # já é tabela desde 45em


def test_modo_cartao_usa_rotulos_dos_atributos_data_label():
    assert re.search(r"content:\s*attr\(data-label\)", CSS)
    base = sem_comentarios(CSS).split("@media")[0]
    assert re.search(r"\.tabela td\s*\{[^}]*display:\s*flex", base)  # cartão no celular, sem media


def test_nenhuma_tabela_larga_pode_estourar_a_tela():
    # Em tela larga as células da tabela podem quebrar texto (e-mails, nomes, clientes longos).
    (largo,) = blocos(CSS, "@media (min-width: 45em)")
    (mais_largo,) = blocos(CSS, "@media (min-width: 64em)")
    assert re.search(r"\.tabela-pedidos td\.cliente\s*\{[^}]*overflow-wrap:\s*anywhere", largo)
    for celula in ("principal", "email"):  # nome e e-mail são as células de texto longo e sem espaços
        assert re.search(rf"\.tabela-usuarios td\.{celula}[,\s{{][^}}]*overflow-wrap:\s*anywhere", mais_largo)
    # Entre 45em e 64em nada fica em uma linha só (exceto .num, o valor em reais): a tabela
    # da lista precisa caber em 720 px mesmo com cliente e valor enormes.
    assert "white-space: nowrap" not in largo
    # Só com folga (64em) a tabela da lista fixa pedido e data em uma linha; as células de
    # usuários nunca ficam em uma linha só (só os botões de ação).
    assert not re.search(r"\.tabela-usuarios td(\.[\w-]+)?\s*\{[^}]*white-space:\s*nowrap", mais_largo)


# --- toque e legibilidade -----------------------------------------------------------


def test_area_de_toque_de_44px_nos_controles():
    assert re.search(r"--altura-toque:\s*44px", CSS)
    for seletor in ("button,", 'input[type="text"]', "::file-selector-button"):
        assert "min-height: var(--altura-toque)" in regra(CSS, seletor), seletor
    assert "min-height: var(--altura-toque)" in regra(CSS, ".menu a")


def test_campos_de_formulario_tem_16px_contra_o_zoom_do_ios():
    corpo = regra(CSS, 'input[type="text"]')
    assert re.search(r"font-size:\s*16px", corpo)
    for tipo in ("email", "password", "date", "file"):
        assert f'input[type="{tipo}"]' in CSS
    assert "select," in CSS and "textarea" in CSS


def _token(nome):
    return re.search(rf"--{nome}:\s*(#[0-9a-fA-F]{{6}})", CSS).group(1)


def _contraste(cor_a, cor_b):
    """Razão de contraste WCAG 2 entre duas cores #rrggbb."""
    def luminancia(cor):
        canais = [int(cor[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canais]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    clara, escura = sorted((luminancia(cor_a), luminancia(cor_b)), reverse=True)
    return (clara + 0.05) / (escura + 0.05)


def test_borda_dos_campos_tem_contraste_de_3_para_1():
    # WCAG 1.4.11: o contorno de um controle precisa de 3:1 contra o que está em volta.
    borda = _token("linha-controle")
    for fundo in ("creme", "superficie"):
        assert _contraste(borda, _token(fundo)) >= 3, fundo
    assert "border: 1px solid var(--linha-controle)" in regra(CSS, 'input[type="text"]')
    assert _contraste(_token("linha-forte"), _token("creme")) < 3  # por isso o token separado


def test_numeros_em_monoespacada_com_algarismos_tabulares():
    corpo = regra(CSS, ".num")
    assert "var(--fonte-mono)" in corpo and "tabular-nums" in corpo
    assert re.search(r"--fonte-mono:[^;]*monospace", CSS)


@pytest.mark.parametrize("trecho", [
    ".perigo", ".secundario", ".linha-do-tempo", ".card", ".botao",
    ":focus-visible", "@media (prefers-reduced-motion: reduce)",
])
def test_estilo_define(trecho):
    assert trecho in CSS


def test_foco_visivel_com_contorno():
    assert re.search(r":focus-visible\s*\{[^}]*outline:\s*2px solid", CSS)


# --- toda classe usada existe ----------------------------------------------------------

# Classes de template sem regra própria no CSS. Hoje nenhuma: se um dia houver uma
# classe só de marcação (gancho de teste ou de script), liste aqui e explique por quê.
CLASSES_SEM_ESTILO = {}


def classes_dos_templates():
    usadas = {}
    for caminho in TEMPLATES:
        for valor in re.findall(r'\bclass="([^"]*)"', caminho.read_text(encoding="utf-8")):
            valor = re.sub(r"\{%.*?%\}|\{\{.*?\}\}", " ", valor)  # só a parte estática
            for classe in valor.split():
                if classe.endswith("-"):  # prefixo de classe montada (status-..., aviso-...)
                    continue
                usadas.setdefault(classe, caminho.name)
    return usadas


def test_toda_classe_dos_templates_tem_regra_no_css():
    css = sem_comentarios(CSS)
    usadas = classes_dos_templates()
    assert len(usadas) > 30  # o leitor de classes está mesmo lendo os templates
    sem_regra = {
        classe: arquivo for classe, arquivo in usadas.items()
        if classe not in CLASSES_SEM_ESTILO and not re.search(rf"\.{re.escape(classe)}(?![\w-])", css)
    }
    assert not sem_regra, f"classes sem regra em estilo.css: {sem_regra}"


def test_lista_de_excecoes_so_tem_classes_que_ainda_existem():
    usadas = classes_dos_templates()
    for classe in CLASSES_SEM_ESTILO:
        assert classe in usadas, f"{classe} saiu dos templates: tire-a da lista"


def categorias_de_flash():
    categorias = set()
    for caminho in CODIGO:
        for no in ast.walk(ast.parse(caminho.read_text(encoding="utf-8"))):
            if isinstance(no, ast.Call) and getattr(no.func, "id", None) == "flash":
                valor = no.args[1] if len(no.args) > 1 else next(
                    (k.value for k in no.keywords if k.arg == "category"), None)
                assert isinstance(valor, ast.Constant), f"{caminho.name}:{no.lineno}: flash sem categoria literal"
                categorias.add(valor.value)
    return categorias


def test_toda_categoria_de_flash_tem_regra_no_css():
    categorias = categorias_de_flash()
    assert {"sucesso", "erro"} <= categorias
    for categoria in categorias:
        assert f".aviso-{categoria}" in CSS, f"flash '{categoria}' sem regra .aviso-{categoria}"
