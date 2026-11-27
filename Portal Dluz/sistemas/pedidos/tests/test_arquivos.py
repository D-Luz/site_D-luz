import io
import os
import re

import pytest
from werkzeug.datastructures import FileStorage

from app import arquivos
from app.arquivos import PdfInvalido, caminho_pdf, pdf_existe, salvar_pdf

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
NOME_GERADO = re.compile(r"^[0-9a-f]{32}\.pdf$")


def arquivo(conteudo=PDF, nome="nota.pdf"):
    return FileStorage(stream=io.BytesIO(conteudo), filename=nome)


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield app


def pasta_pdfs(app):
    return app.config["DATA_DIR"] / "pdfs"


def test_aceita_pdf_valido_e_gera_nome_uuid(ctx):
    nome_gerado, nome_original = salvar_pdf(arquivo(nome="nota.pdf"))

    assert NOME_GERADO.match(nome_gerado)
    assert nome_gerado != nome_original
    assert nome_original == "nota.pdf"
    assert pdf_existe(nome_gerado)


def test_grava_exatamente_os_bytes_enviados(ctx):
    conteudo = PDF + bytes(range(256)) * 10
    nome_gerado, _ = salvar_pdf(arquivo(conteudo))

    assert caminho_pdf(nome_gerado).read_bytes() == conteudo
    assert os.listdir(pasta_pdfs(ctx)) == [nome_gerado]


def test_dois_envios_do_mesmo_nome_geram_nomes_diferentes(ctx):
    primeiro, _ = salvar_pdf(arquivo(nome="nota.pdf"))
    segundo, _ = salvar_pdf(arquivo(nome="nota.pdf"))

    assert primeiro != segundo
    assert pdf_existe(primeiro) and pdf_existe(segundo)


def test_cria_a_pasta_de_pdfs_se_faltar(ctx):
    pasta_pdfs(ctx).rmdir()  # create_app já cria; aqui simula a pasta apagada
    assert not pasta_pdfs(ctx).exists()
    salvar_pdf(arquivo())
    assert pasta_pdfs(ctx).is_dir()


def test_extensao_maiuscula_e_aceita(ctx):
    nome_gerado, nome_original = salvar_pdf(arquivo(nome="NOTA.PDF"))

    assert nome_original == "NOTA.PDF"
    assert nome_gerado.endswith(".pdf")  # o nome em disco é sempre minúsculo


def test_recusa_extensao_que_nao_e_pdf(ctx):
    with pytest.raises(PdfInvalido, match=r"extensão \.pdf"):
        salvar_pdf(arquivo(nome="nota.txt"))
    with pytest.raises(PdfInvalido):
        salvar_pdf(arquivo(nome="nota"))
    with pytest.raises(PdfInvalido):
        salvar_pdf(arquivo(nome="nota.pdf.exe"))


def test_recusa_conteudo_que_nao_e_pdf(ctx):
    with pytest.raises(PdfInvalido, match="não é um PDF válido"):
        salvar_pdf(arquivo(b"MZ\x90\x00 isto e um executavel", "nota.pdf"))
    assert not list(pasta_pdfs(ctx).glob("*"))


def test_recusa_arquivo_vazio(ctx):
    with pytest.raises(PdfInvalido, match="vazio"):
        salvar_pdf(arquivo(b"", "nota.pdf"))


@pytest.mark.parametrize("enviado", [None, FileStorage(stream=io.BytesIO(b""), filename="")])
def test_recusa_sem_arquivo(ctx, enviado):
    with pytest.raises(PdfInvalido, match="Selecione um arquivo PDF"):
        salvar_pdf(enviado)


def test_recusa_acima_do_limite(app):
    app.config["MAX_PDF_MB"] = 1
    with app.app_context():
        grande = PDF + b"0" * (1024 * 1024)
        with pytest.raises(PdfInvalido, match="1 MB"):
            salvar_pdf(arquivo(grande))
        assert not list(pasta_pdfs(app).glob("*"))


def test_limite_exato_e_aceito_e_um_byte_a_mais_e_recusado(app):
    app.config["MAX_PDF_MB"] = 1
    limite = 1024 * 1024
    with app.app_context():
        no_limite = PDF + b"0" * (limite - len(PDF))
        assert len(no_limite) == limite
        nome_gerado, _ = salvar_pdf(arquivo(no_limite))
        assert caminho_pdf(nome_gerado).stat().st_size == limite

        with pytest.raises(PdfInvalido, match="1 MB"):
            salvar_pdf(arquivo(no_limite + b"0"))


def test_nao_le_o_arquivo_inteiro_quando_acima_do_limite(app):
    app.config["MAX_PDF_MB"] = 1

    class Fluxo(io.BytesIO):
        lido = 0

        def read(self, tamanho=-1):
            dados = super().read(tamanho)
            Fluxo.lido += len(dados)
            return dados

    enorme = Fluxo(PDF + b"0" * (10 * 1024 * 1024))
    with app.app_context(), pytest.raises(PdfInvalido):
        salvar_pdf(FileStorage(stream=enorme, filename="nota.pdf"))
    assert Fluxo.lido <= 1024 * 1024 + 1


def test_nao_confia_no_content_type_do_cliente(ctx):
    falso = FileStorage(
        stream=io.BytesIO(b"<html>nao sou pdf</html>"),
        filename="nota.pdf",
        content_type="application/pdf",
    )
    with pytest.raises(PdfInvalido, match="não é um PDF válido"):
        salvar_pdf(falso)


def test_nome_original_com_acentos_barras_e_300_caracteres(ctx):
    nome = "pasta/sub\\relatório açaí é ção " + "ã" * 300 + ".pdf"
    nome_gerado, nome_original = salvar_pdf(arquivo(nome=nome))

    assert nome_original.startswith("relatório açaí é ção ã")
    assert nome_original.endswith(".pdf")
    assert len(nome_original) <= 255
    assert "/" not in nome_original and "\\" not in nome_original
    # No disco só existe o nome gerado.
    assert os.listdir(pasta_pdfs(ctx)) == [nome_gerado]


def test_nome_original_curto_com_acentos_fica_intacto(ctx):
    _, nome_original = salvar_pdf(arquivo(nome="Pedido João & Cia (nº 3).pdf"))
    assert nome_original == "Pedido João & Cia (nº 3).pdf"


def test_nome_original_de_caminho_windows_vira_so_o_nome(ctx):
    _, nome_original = salvar_pdf(arquivo(nome="C:\\Users\\x\\nota.pdf"))
    assert nome_original == "nota.pdf"


def test_nome_original_sem_caracteres_de_controle(ctx):
    _, nome_original = salvar_pdf(arquivo(nome="no\x00ta\r\n\tfinal.pdf"))
    assert nome_original == "notafinal.pdf"


def test_nome_original_sem_marcas_bidirecionais(ctx):
    _, nome_original = salvar_pdf(arquivo(nome="nota\u202efdp.exe.pdf"))
    assert nome_original == "notafdp.exe.pdf"


def test_nome_original_que_sobra_so_com_extensao_vira_arquivo_pdf(ctx):
    nome_gerado, nome_original = salvar_pdf(arquivo(nome="pasta/.pdf"))
    assert nome_original == "arquivo.pdf"
    assert pdf_existe(nome_gerado)


def test_recusa_nome_que_termina_em_barra_ou_so_espacos(ctx):
    for nome in ["pasta/", "pasta\\", "   ", "\x00\r\n"]:
        with pytest.raises(PdfInvalido):
            salvar_pdf(arquivo(nome=nome))


def test_nome_original_so_com_extensao_nao_fica_vazio(ctx):
    _, nome_original = salvar_pdf(arquivo(nome=" \x00.pdf"))
    assert nome_original == "arquivo.pdf"


def test_falha_na_gravacao_nao_deixa_arquivo_nem_temporario(ctx, monkeypatch):
    def falha(origem, destino):
        raise OSError("disco cheio")

    monkeypatch.setattr(arquivos.os, "replace", falha)

    with pytest.raises(OSError, match="disco cheio"):
        salvar_pdf(arquivo())
    assert list(pasta_pdfs(ctx).iterdir()) == []


def test_caminho_nao_sai_da_pasta(ctx):
    pasta = pasta_pdfs(ctx).resolve()
    caminho = caminho_pdf("0123456789abcdef0123456789abcdef.pdf")
    assert caminho.parent.resolve() == pasta

    for malicioso in [
        "../secret.pdf",
        "../../etc/passwd",
        "/etc/passwd",
        "C:\\Windows\\win.pdf",
        "a\\b.pdf",
        "..\\x.pdf",
        "0123456789abcdef0123456789abcdef.pdf/../x.pdf",
        "../0123456789abcdef0123456789abcdef.pdf",
        "0123456789ABCDEF0123456789ABCDEF.pdf",  # hex maiúsculo
        "0123456789abcdef0123456789abcde.pdf",  # 31 caracteres
        "0123456789abcdef0123456789abcdef0.pdf",  # 33 caracteres
        "0123456789abcdef0123456789abcdef.PDF",
        "0123456789abcdef0123456789abcdef.txt",
        "0123456789abcdef0123456789abcdef.pdf\n",  # $ do regex aceitaria o \n
        "0123456789abcdef0123456789abcdef.pdf\x00",
        "",
    ]:
        with pytest.raises(PdfInvalido):
            caminho_pdf(malicioso)


@pytest.mark.parametrize("invalido", [None, 123, b"0123456789abcdef0123456789abcdef.pdf"])
def test_caminho_recusa_tipos_inesperados(ctx, invalido):
    with pytest.raises(PdfInvalido):
        caminho_pdf(invalido)
    assert pdf_existe(invalido) is False


def test_pdf_existe_falso_quando_sumiu(ctx):
    nome_gerado, _ = salvar_pdf(arquivo())
    assert pdf_existe(nome_gerado) is True

    caminho_pdf(nome_gerado).unlink()
    assert pdf_existe(nome_gerado) is False


def test_pdf_existe_falso_para_nome_invalido_sem_levantar_erro(ctx):
    assert pdf_existe("../secret.pdf") is False
    assert pdf_existe("") is False
    assert pdf_existe("/etc/passwd") is False


def test_pdf_existe_falso_para_pasta_com_nome_valido(ctx):
    (pasta_pdfs(ctx) / ("a" * 32 + ".pdf")).mkdir()

    assert pdf_existe("a" * 32 + ".pdf") is False
