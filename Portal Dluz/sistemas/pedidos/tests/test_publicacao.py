"""Arquivos da instalação no Windows: .bat, guia e README batem com o código."""

import re
from pathlib import Path

import pytest

from app.config import PADROES, PREFIXO_ENV

RAIZ = Path(__file__).resolve().parent.parent
SCRIPTS = RAIZ / "scripts"
BATS = ("iniciar_pedidos.bat", "backup_pedidos.bat")
VARIAVEL = re.compile(r"PEDIDOS_[A-Z_]*[A-Z](?![A-Z_*])")
# O guia manda NÃO copiar o script de demonstração para o PC: lá ele não existe,
# e a documentação continua citando-o (para dizer justamente isso).
SO_NO_DESENVOLVIMENTO = {nome for nome in ("dados_demo.py",) if not (SCRIPTS / nome).exists()}


def _ler(caminho):
    return caminho.read_text(encoding="utf-8")


def _variaveis(texto):
    return set(VARIAVEL.findall(texto))


def _variaveis_que_existem():
    """Todo PEDIDOS_* que o código de fato lê: config, e-mail, porta e backup."""
    existentes = {PREFIXO_ENV + chave for chave in PADROES}
    for fonte in (
        RAIZ / "app" / "notificacoes.py",
        SCRIPTS / "iniciar.py",
        SCRIPTS / "backup_pedidos.bat",
    ):
        existentes |= _variaveis(_ler(fonte))
    return existentes


@pytest.mark.parametrize("nome", BATS)
def test_bat_tem_quebra_de_linha_do_windows_e_so_ascii(nome):
    bruto = (SCRIPTS / nome).read_bytes()
    bruto.decode("ascii")  # o cmd usa outra página de código: sem acentos
    assert b"\r\n" in bruto
    assert re.search(rb"(?<!\r)\n", bruto) is None, "há quebra de linha sem \\r"


@pytest.mark.parametrize("nome", BATS)
def test_bat_nao_tem_caminho_fixo_de_usuario(nome):
    texto = _ler(SCRIPTS / nome)
    assert "cd /d \"%~dp0..\"" in texto
    sem_exemplo = texto.replace("D:\\BackupsPedidos", "")  # exemplo numa mensagem de erro
    assert not re.search(r"\b[A-Za-z]:\\", sem_exemplo), "caminho de disco fixo"
    assert "Users" not in texto


def test_iniciar_bat_roda_o_servidor_com_o_python_do_venv():
    texto = _ler(SCRIPTS / "iniciar_pedidos.bat")
    assert r'".venv\Scripts\python.exe" scripts\iniciar.py' in texto
    assert (SCRIPTS / "iniciar.py").is_file()


def test_backup_bat_exige_a_variavel_e_chama_o_backup():
    texto = _ler(SCRIPTS / "backup_pedidos.bat")
    assert 'if "%PEDIDOS_BACKUP_DESTINO%"==""' in texto
    assert r'scripts\backup.py "%PEDIDOS_BACKUP_DESTINO%"' in texto
    assert (SCRIPTS / "backup.py").is_file()


def test_gitattributes_forca_crlf_nos_bat():
    assert "*.bat text eol=crlf" in _ler(RAIZ / ".gitattributes").splitlines()


@pytest.mark.parametrize("arquivo", [SCRIPTS / "instalar_windows.md", RAIZ / "README.md"])
def test_documentacao_so_cita_variaveis_que_existem(arquivo):
    citadas = _variaveis(_ler(arquivo))
    assert citadas, "o documento deveria citar variáveis PEDIDOS_*"
    inexistentes = citadas - _variaveis_que_existem()
    assert not inexistentes, f"{arquivo.name} cita variáveis que o código não lê: {sorted(inexistentes)}"


def test_guia_explica_todas_as_variaveis_de_instalacao():
    guia = _variaveis(_ler(SCRIPTS / "instalar_windows.md"))
    esperadas = {
        "PEDIDOS_DATA_DIR",
        "PEDIDOS_BASE_URL",
        "PEDIDOS_EMAIL_MODE",
        "PEDIDOS_BACKUP_DESTINO",
        "PEDIDOS_PORTA",
        "PEDIDOS_COOKIE_INSECURE",
        "PEDIDOS_SMTP_HOST",
        "PEDIDOS_SMTP_PORTA",
        "PEDIDOS_SMTP_USUARIO",
        "PEDIDOS_SMTP_SENHA",
        "PEDIDOS_SMTP_REMETENTE",
    }
    assert esperadas <= guia


def test_guia_cita_so_scripts_que_existem():
    texto = _ler(SCRIPTS / "instalar_windows.md")
    citados = set(re.findall(r"scripts\\([A-Za-z_]+\.(?:py|bat|md))", texto))
    assert {"iniciar_pedidos.bat", "backup_pedidos.bat", "criar_admin.py", "iniciar.py"} <= citados
    for nome in citados - SO_NO_DESENVOLVIMENTO:
        assert (SCRIPTS / nome).is_file(), f"o guia cita scripts\\{nome}, que não existe"


def test_guia_usa_a_porta_e_as_opcoes_que_o_codigo_tem():
    guia = _ler(SCRIPTS / "instalar_windows.md")
    assert "http://127.0.0.1:8000" in guia
    assert "tailscale funnel --bg 8000" in guia
    assert "--redefinir" in _ler(SCRIPTS / "criar_admin.py")
    assert "--manter" in _ler(SCRIPTS / "backup.py")
    for opcao in ("--redefinir", "--manter"):
        assert opcao in guia
    assert "PORTA_PADRAO = 8000" in _ler(SCRIPTS / "iniciar.py")


def test_guia_cita_as_mensagens_que_o_codigo_realmente_escreve():
    guia = _ler(SCRIPTS / "instalar_windows.md")
    # A mensagem da variável que falta mora em app/config.py (iniciar, criar_admin e backup a usam).
    codigo = _ler(SCRIPTS / "iniciar.py") + _ler(RAIZ / "app" / "config.py")
    for trecho in (
        "Primeira requisição via proxy HTTPS recebida (X-Forwarded-Proto=https)",
        "a variável de ambiente PEDIDOS_DATA_DIR não está definida",
        "dentro da pasta do programa",
        "Servidor encerrado.",
        "Aprovação de Pedidos iniciando",
    ):
        assert trecho in codigo, f"iniciar.py não escreve mais: {trecho}"
    assert "Primeira requisição via proxy HTTPS recebida (X-Forwarded-Proto=https)" in guia
    assert "PEDIDOS_DATA_DIR não está definida" in guia
    assert "dentro da pasta do programa" in guia
    assert "sem X-Forwarded-Proto" in guia


def test_guia_restringe_as_pastas_com_icacls_por_sid():
    guia = _ler(SCRIPTS / "instalar_windows.md")
    assert 'icacls C:\\PedidosDados /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "%USERNAME%:(OI)(CI)M"' in guia
    assert 'icacls C:\\PedidosAprovacao /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "%USERNAME%:(OI)(CI)RX"' in guia


def test_guia_restringe_tambem_a_pasta_de_backup():
    guia = _ler(SCRIPTS / "instalar_windows.md")
    assert 'icacls D:\\BackupsPedidos /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "%USERNAME%:(OI)(CI)M"' in guia
    assert "icacls D:\\BackupsPedidos`" in guia  # e diz como conferir


def _secao(texto, titulo):
    inicio = texto.index(titulo)
    fim = texto.find("\n## ", inicio + 1)
    return texto[inicio:fim if fim != -1 else None]


@pytest.mark.parametrize("titulo", ["### Como restaurar", "## 13. Atualizar o sistema"])
def test_restaurar_e_atualizar_desabilitam_a_tarefa_antes_e_habilitam_por_ultimo(titulo):
    # O gatilho de 5 em 5 minutos religaria o servidor no meio do trabalho.
    secao = _secao(_ler(SCRIPTS / "instalar_windows.md"), titulo)
    assert secao.index("**Desabilitar**") < secao.index("**Finalizar**")
    assert "tasklist | findstr python" in secao
    passos = re.findall(r"^\d+\. .*$", secao, re.MULTILINE)
    assert "**Habilitar**" in passos[-1]
    assert all("**Habilitar**" not in passo for passo in passos[:-1])


def test_guia_deixa_o_dados_demo_fora_do_pc():
    guia = _ler(SCRIPTS / "instalar_windows.md")
    assert '"*/scripts/dados_demo.py"' in _secao(guia, "## 2. Copiar")
    assert "apague `C:\\PedidosAprovacao\\scripts\\dados_demo.py`" in guia


def test_documentacao_so_cita_opcoes_que_os_scripts_tem():
    externas = {"--bg", "--version"}  # tailscale funnel --bg, py --version
    if not (SCRIPTS / "dados_demo.py").exists():
        # No PC o script de demonstração não é copiado; as opções dele só são citadas no README.
        externas |= {"--forcar", "--aceito-risco-de-producao"}
    codigo = "".join(_ler(caminho) for caminho in SCRIPTS.glob("*.py"))
    for arquivo in (SCRIPTS / "instalar_windows.md", RAIZ / "README.md"):
        for opcao in set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", _ler(arquivo))) - externas:
            assert f'"{opcao}"' in codigo, f"{arquivo.name} cita {opcao}, que nenhum script aceita"


def test_readme_cita_so_scripts_que_existem():
    citados = set(re.findall(r"scripts/([A-Za-z_]+\.(?:py|bat|md))", _ler(RAIZ / "README.md")))
    assert {"iniciar.py", "criar_admin.py", "backup.py", "dados_demo.py"} <= citados
    for nome in citados - SO_NO_DESENVOLVIMENTO:
        assert (SCRIPTS / nome).is_file(), f"o README cita scripts/{nome}, que não existe"


def test_guia_e_readme_citam_a_tela_trocar_senha_que_existe():
    assert "/trocar-senha" in _ler(RAIZ / "app" / "auth.py")
    assert "url_for('auth.trocar_senha')" in _ler(RAIZ / "app" / "templates" / "base.html")
    for arquivo in (SCRIPTS / "instalar_windows.md", RAIZ / "README.md"):
        assert "**Trocar senha**" in _ler(arquivo), arquivo.name
