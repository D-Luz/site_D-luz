"""scripts/backup.py: cópia consistente do banco e dos PDFs, e retenção."""

import importlib.util
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from app import db as db_module

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backup.py"
AGORA = datetime(2026, 10, 23, 14, 30, 5)
PDF_A = "a" * 32 + ".pdf"
PDF_B = "0123456789abcdef" * 2 + ".pdf"


@pytest.fixture(scope="module")
def backup():
    """O módulo do script (ele não faz parte de um pacote)."""
    spec = importlib.util.spec_from_file_location("backup_script", SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def data_dir(app):
    """Pasta de dados real (banco com esquema, secret.key) com 2 pedidos e 2 PDFs."""
    with app.app_context():
        conexao = db_module.get_db()
        conexao.execute(
            "INSERT INTO usuarios (nome, email, senha_hash, perfil, criado_em) "
            "VALUES ('Ana', 'ana@exemplo.com', 'x', 'solicitante', '2026-10-23T10:00:00+00:00')"
        )
        for numero, pdf in ((1, PDF_A), (2, PDF_B)):
            conexao.execute(
                "INSERT INTO pedidos (cliente, valor_centavos, descricao, pdf_arquivo, "
                "pdf_nome_original, solicitante_id, criado_em, atualizado_em) "
                "VALUES (?, ?, 'desc', ?, 'original.pdf', 1, "
                "'2026-10-23T10:00:00+00:00', '2026-10-23T10:00:00+00:00')",
                (f"Cliente {numero}", numero * 100, pdf),
            )
    pasta = Path(app.config["DATA_DIR"])
    (pasta / "pdfs" / PDF_A).write_bytes(b"%PDF-1.4 conteudo A\n" * 50)
    (pasta / "pdfs" / PDF_B).write_bytes(bytes(range(256)) * 20)
    return pasta


def listar_pedidos(arquivo_db):
    conexao = sqlite3.connect(arquivo_db)
    try:
        return conexao.execute(
            "SELECT id, cliente, valor_centavos, pdf_arquivo FROM pedidos ORDER BY id"
        ).fetchall()
    finally:
        conexao.close()


def sobras(destino):
    return sorted(p.name for p in destino.iterdir())


def test_backup_copia_banco_consistente_e_pdfs(backup, data_dir, tmp_path):
    destino = tmp_path / "backups"
    pasta = backup.fazer_backup(data_dir, destino, AGORA)

    assert pasta == destino / "aprovacao_20261023_143005"
    assert listar_pedidos(pasta / "pedidos.db") == listar_pedidos(data_dir / "pedidos.db")
    assert len(listar_pedidos(pasta / "pedidos.db")) == 2
    conexao = sqlite3.connect(pasta / "pedidos.db")
    try:
        assert conexao.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        conexao.close()
    for nome in (PDF_A, PDF_B):
        assert (pasta / "pdfs" / nome).read_bytes() == (data_dir / "pdfs" / nome).read_bytes()
    assert sobras(destino) == ["aprovacao_20261023_143005"]


def test_backup_nao_copia_a_chave_secreta_nem_arquivos_estranhos(backup, data_dir, tmp_path):
    assert (data_dir / "secret.key").is_file()
    pdfs = data_dir / "pdfs"
    (pdfs / ".tmp-abc123").write_bytes(b"sobra de upload")
    (pdfs / "notas.txt").write_text("nada")
    (pdfs / ("A" * 32 + ".pdf")).write_bytes(b"maiusculas")
    (pdfs / ("a" * 31 + ".pdf")).write_bytes(b"curto")
    (pdfs / ("b" * 32 + ".pdf.bak")).write_bytes(b"outra extensao")
    (pdfs / ("c" * 32 + ".pdf")).mkdir()  # pasta com nome de PDF

    pasta = backup.fazer_backup(data_dir, tmp_path / "bk", AGORA)

    assert sorted(p.name for p in pasta.iterdir()) == ["pdfs", "pedidos.db"]
    assert sorted(p.name for p in (pasta / "pdfs").iterdir()) == sorted([PDF_A, PDF_B])
    assert not list(pasta.rglob("secret.key"))


def test_backup_sem_pasta_de_pdfs_cria_pasta_vazia(backup, data_dir, tmp_path):
    (data_dir / "pdfs" / PDF_A).unlink()
    (data_dir / "pdfs" / PDF_B).unlink()
    (data_dir / "pdfs").rmdir()

    pasta = backup.fazer_backup(data_dir, tmp_path / "bk", AGORA)

    assert (pasta / "pdfs").is_dir()
    assert list((pasta / "pdfs").iterdir()) == []


def test_pdf_que_some_durante_a_copia_e_ignorado(backup, data_dir, tmp_path, monkeypatch):
    copiar_original = backup.shutil.copy2

    def copiar(origem, destino_arquivo, *args, **kwargs):
        if Path(origem).name == PDF_A:
            raise FileNotFoundError(origem)
        return copiar_original(origem, destino_arquivo, *args, **kwargs)

    monkeypatch.setattr(backup.shutil, "copy2", copiar)

    pasta = backup.fazer_backup(data_dir, tmp_path / "bk", AGORA)

    assert [p.name for p in (pasta / "pdfs").iterdir()] == [PDF_B]


def test_destino_inexistente_e_criado(backup, data_dir, tmp_path):
    destino = tmp_path / "um" / "dois" / "backups com espaço e açaí"
    assert not destino.exists()

    pasta = backup.fazer_backup(data_dir, destino, AGORA)

    assert pasta.parent == destino
    assert (pasta / "pedidos.db").is_file()


def test_backup_funciona_com_banco_em_uso(backup, data_dir, tmp_path):
    """Escrita pendente (BEGIN IMMEDIATE): o backup lê o último estado confirmado."""
    escritor = sqlite3.connect(data_dir / "pedidos.db", isolation_level=None)
    try:
        escritor.execute("BEGIN IMMEDIATE")
        escritor.execute(
            "INSERT INTO pedidos (cliente, valor_centavos, descricao, pdf_arquivo, "
            "pdf_nome_original, solicitante_id, criado_em, atualizado_em) "
            "VALUES ('Pendente', 1, 'd', 'x.pdf', 'x.pdf', 1, 'agora', 'agora')"
        )

        pasta = backup.fazer_backup(data_dir, tmp_path / "bk", AGORA)

        clientes = [linha[1] for linha in listar_pedidos(pasta / "pedidos.db")]
        assert clientes == ["Cliente 1", "Cliente 2"]  # a linha não confirmada não entra
    finally:
        escritor.execute("ROLLBACK")
        escritor.close()


def test_backup_espera_o_banco_ser_liberado(backup, data_dir, tmp_path):
    """Com o banco travado para leitura (EXCLUSIVE), o backup espera o commit."""
    escritor = sqlite3.connect(data_dir / "pedidos.db", isolation_level=None, check_same_thread=False)
    escritor.execute("BEGIN EXCLUSIVE")
    escritor.execute(
        "INSERT INTO pedidos (cliente, valor_centavos, descricao, pdf_arquivo, "
        "pdf_nome_original, solicitante_id, criado_em, atualizado_em) "
        "VALUES ('Tardio', 3, 'd', 'x.pdf', 'x.pdf', 1, 'agora', 'agora')"
    )

    def liberar():
        time.sleep(0.4)
        escritor.execute("COMMIT")

    fio = threading.Thread(target=liberar)
    fio.start()
    try:
        pasta = backup.fazer_backup(data_dir, tmp_path / "bk", AGORA, timeout=10)
    finally:
        fio.join()
        escritor.close()

    clientes = [linha[1] for linha in listar_pedidos(pasta / "pedidos.db")]
    assert clientes == ["Cliente 1", "Cliente 2", "Tardio"]


def test_banco_que_nao_libera_falha_limpo_e_sem_pasta(backup, data_dir, tmp_path):
    destino = tmp_path / "bk"
    escritor = sqlite3.connect(data_dir / "pedidos.db", isolation_level=None)
    escritor.execute("BEGIN EXCLUSIVE")
    try:
        inicio = time.monotonic()
        with pytest.raises(RuntimeError, match="banco"):
            backup.fazer_backup(data_dir, destino, AGORA, timeout=0.3)
        assert time.monotonic() - inicio < 5
    finally:
        escritor.execute("ROLLBACK")
        escritor.close()

    assert sobras(destino) == []


def test_banco_de_origem_corrompido_falha_sem_deixar_pasta(backup, data_dir, tmp_path):
    destino = tmp_path / "bk"
    (data_dir / "pedidos.db").write_bytes(b"isto nao e um banco sqlite" * 400)

    with pytest.raises(RuntimeError, match="banco"):
        backup.fazer_backup(data_dir, destino, AGORA)

    assert sobras(destino) == []


def test_copia_corrompida_e_apagada(backup, data_dir, tmp_path, monkeypatch):
    """O integrity_check da cópia reprova: nada fica no destino."""
    destino = tmp_path / "bk"

    def reprovar(arquivo):
        return ["*** in database main ***", "Page 3: btree page corrupta"]

    monkeypatch.setattr(backup, "_integridade", reprovar)

    with pytest.raises(RuntimeError, match="corrompida"):
        backup.fazer_backup(data_dir, destino, AGORA)

    assert sobras(destino) == []


def test_pagina_corrompida_no_meio_do_banco_nao_vira_backup(backup, data_dir, tmp_path):
    destino = tmp_path / "bk"
    caminho = data_dir / "pedidos.db"
    tamanho = caminho.stat().st_size
    assert tamanho >= 4096 * 3
    with open(caminho, "r+b") as arquivo:
        arquivo.seek(4096 * 2)
        arquivo.write(b"\xff" * 4096)

    with pytest.raises(RuntimeError):
        backup.fazer_backup(data_dir, destino, AGORA)

    assert sobras(destino) == []


def test_mesmo_segundo_recusa_e_preserva_o_primeiro(backup, data_dir, tmp_path):
    destino = tmp_path / "bk"
    primeiro = backup.fazer_backup(data_dir, destino, AGORA)
    marca = primeiro / "pdfs" / PDF_A
    conteudo = marca.read_bytes()

    with pytest.raises(FileExistsError):
        backup.fazer_backup(data_dir, destino, AGORA)

    assert sobras(destino) == ["aprovacao_20261023_143005"]
    assert marca.read_bytes() == conteudo
    assert len(listar_pedidos(primeiro / "pedidos.db")) == 2


def test_destino_dentro_da_pasta_de_dados_e_recusado(backup, data_dir):
    for destino in (data_dir, data_dir / "backups", data_dir / "pdfs", data_dir / "a" / ".." / "b"):
        with pytest.raises(ValueError, match="dentro da pasta de dados"):
            backup.fazer_backup(data_dir, destino, AGORA)
    assert sorted(p.name for p in data_dir.iterdir()) == ["pdfs", "pedidos.db", "secret.key"]


def test_destino_irmao_com_nome_parecido_e_aceito(backup, data_dir, tmp_path):
    destino = data_dir.parent / (data_dir.name + "-backups")
    pasta = backup.fazer_backup(data_dir, destino, AGORA)
    assert (pasta / "pedidos.db").is_file()


def test_sem_banco_falha_com_mensagem_e_nao_cria_destino(backup, tmp_path):
    vazio = tmp_path / "vazio"
    vazio.mkdir()
    destino = tmp_path / "bk"

    with pytest.raises(FileNotFoundError, match="pedidos.db"):
        backup.fazer_backup(vazio, destino, AGORA)

    assert not destino.exists()


def test_dois_backups_ficam_em_pastas_ordenadas(backup, data_dir, tmp_path):
    destino = tmp_path / "bk"
    segundo = backup.fazer_backup(data_dir, destino, datetime(2026, 10, 24, 8, 0, 0))
    primeiro = backup.fazer_backup(data_dir, destino, datetime(2026, 10, 23, 23, 59, 59))

    assert segundo.name == "aprovacao_20261024_080000"
    assert primeiro.name == "aprovacao_20261023_235959"
    assert sobras(destino) == [primeiro.name, segundo.name]


def criar_backups_falsos(destino, nomes):
    for nome in nomes:
        pasta = destino / nome
        pasta.mkdir(parents=True)
        (pasta / "pedidos.db").write_bytes(b"x")


def test_manter_apaga_os_mais_antigos(backup, tmp_path):
    destino = tmp_path / "bk"
    nomes = [f"aprovacao_202610{dia:02d}_020000" for dia in (3, 1, 5, 2, 4)]
    criar_backups_falsos(destino, nomes)

    apagados = backup.aplicar_retencao(destino, 2)

    assert sorted(p.name for p in apagados) == [
        "aprovacao_20261001_020000",
        "aprovacao_20261002_020000",
        "aprovacao_20261003_020000",
    ]
    assert sobras(destino) == ["aprovacao_20261004_020000", "aprovacao_20261005_020000"]


def test_retencao_nao_toca_em_nada_que_nao_seja_backup(backup, tmp_path):
    destino = tmp_path / "bk"
    criar_backups_falsos(destino, ["aprovacao_20250101_000000", "aprovacao_20260101_000000"])
    estranhos_pastas = [
        "aprovacao_x",
        "aprovacao_20260101_000000.bak",
        "aprovacao_2026010_000000",
        "aprovacao_20260101_0000000",
        "aprovacao_20260101_000000 ",
        "outros",
        ".tmp-abc",
        "aprovacao_٢٠٢٦٠١٠١_٠٠٠٠٠٠",
    ]
    for nome in estranhos_pastas:
        (destino / nome).mkdir()
        (destino / nome / "dentro.txt").write_text("fica")
    (destino / "notes.txt").write_text("fica")
    (destino / "aprovacao_20240101_000000").write_text("arquivo com nome de backup")

    apagados = backup.aplicar_retencao(destino, 1)

    assert [p.name for p in apagados] == ["aprovacao_20250101_000000"]
    restante = set(sobras(destino))
    assert restante == {
        "aprovacao_20260101_000000",
        "notes.txt",
        "aprovacao_20240101_000000",
        *estranhos_pastas,
    }
    for nome in estranhos_pastas:
        assert (destino / nome / "dentro.txt").read_text() == "fica"


def test_retencao_recusa_manter_menor_que_um(backup, tmp_path):
    destino = tmp_path / "bk"
    criar_backups_falsos(destino, ["aprovacao_20260101_000000"])
    for manter in (0, -1):
        with pytest.raises(ValueError):
            backup.aplicar_retencao(destino, manter)
    assert sobras(destino) == ["aprovacao_20260101_000000"]


def test_retencao_com_menos_backups_que_o_limite_nao_apaga(backup, tmp_path):
    destino = tmp_path / "bk"
    criar_backups_falsos(destino, ["aprovacao_20260101_000000"])
    assert backup.aplicar_retencao(destino, 30) == []
    assert backup.aplicar_retencao(tmp_path / "nao-existe", 30) == []


def test_main_faz_backup_imprime_caminho_e_aplica_retencao(
    backup, data_dir, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("PEDIDOS_DATA_DIR", str(data_dir))
    destino = tmp_path / "bk"
    criar_backups_falsos(destino, ["aprovacao_20200101_000000", "aprovacao_20200102_000000"])

    codigo = backup.main([str(destino), "--manter", "2"])

    saida = capsys.readouterr()
    assert codigo == 0
    assert saida.err == ""
    novos = [p for p in destino.iterdir() if not p.name.startswith("aprovacao_2020")]
    assert len(novos) == 1
    assert str(novos[0]) in saida.out
    assert "MB" in saida.out
    assert "aprovacao_20200101_000000" in saida.out  # linha do backup removido
    assert sobras(destino) == ["aprovacao_20200102_000000", novos[0].name]


def test_main_manter_padrao_e_30(backup, data_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("PEDIDOS_DATA_DIR", str(data_dir))
    destino = tmp_path / "bk"
    criar_backups_falsos(destino, [f"aprovacao_2020{m:02d}01_000000" for m in range(1, 13)])
    assert backup.main([str(destino)]) == 0
    assert len(sobras(destino)) == 13


def test_main_sem_banco_devolve_1_com_mensagem_no_stderr(
    backup, tmp_path, monkeypatch, capsys
):
    vazio = tmp_path / "vazio"
    vazio.mkdir()
    monkeypatch.setenv("PEDIDOS_DATA_DIR", str(vazio))

    codigo = backup.main([str(tmp_path / "bk")])

    saida = capsys.readouterr()
    assert codigo == 1
    assert saida.out == ""
    assert "pedidos.db" in saida.err
    assert "Traceback" not in saida.err


@pytest.mark.parametrize("valor", [None, "", "   "])
def test_main_recusa_sem_pedidos_data_dir(backup, tmp_path, monkeypatch, capsys, valor):
    # Sem a variável o backup copiaria em silêncio a pasta data/ do programa.
    from app import config as config_mod

    monkeypatch.delenv("PEDIDOS_DATA_DIR", raising=False)
    if valor is not None:
        monkeypatch.setenv("PEDIDOS_DATA_DIR", valor)
    projeto = tmp_path / "projeto"
    (projeto / "data").mkdir(parents=True)
    sqlite3.connect(projeto / "data" / "pedidos.db").close()
    monkeypatch.setitem(config_mod.PADROES, "DATA_DIR", projeto / "data")
    destino = tmp_path / "bk"

    assert backup.main([str(destino)]) == 1

    saida = capsys.readouterr()
    assert "PEDIDOS_DATA_DIR" in saida.err and "Traceback" not in saida.err
    assert saida.out == ""
    assert not destino.exists()


def test_main_destino_dentro_dos_dados_devolve_1(backup, data_dir, monkeypatch, capsys):
    monkeypatch.setenv("PEDIDOS_DATA_DIR", str(data_dir))
    codigo = backup.main([str(data_dir / "bk")])
    assert codigo == 1
    assert "dentro da pasta de dados" in capsys.readouterr().err


def test_main_manter_invalido_devolve_1_sem_fazer_backup(
    backup, data_dir, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("PEDIDOS_DATA_DIR", str(data_dir))
    destino = tmp_path / "bk"
    assert backup.main([str(destino), "--manter", "0"]) == 1
    assert "--manter" in capsys.readouterr().err
    assert not destino.exists()


def test_main_com_backup_falho_nao_aplica_retencao(
    backup, data_dir, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("PEDIDOS_DATA_DIR", str(data_dir))
    (data_dir / "pedidos.db").write_bytes(b"lixo" * 5000)
    destino = tmp_path / "bk"
    antigos = ["aprovacao_20200101_000000", "aprovacao_20200102_000000"]
    criar_backups_falsos(destino, antigos)

    codigo = backup.main([str(destino), "--manter", "1"])

    saida = capsys.readouterr()
    assert codigo == 1
    assert saida.err.strip() != ""
    assert "Traceback" not in saida.err
    assert sobras(destino) == antigos


# --- Correções da revisão ---------------------------------------------------


def test_backup_com_relogio_atrasado_sobrevive_a_retencao(
    backup, data_dir, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("PEDIDOS_DATA_DIR", str(data_dir))
    destino = tmp_path / "bk"
    criar_backups_falsos(destino, [f"aprovacao_2099010{d}_000000" for d in range(1, 6)])
    monkeypatch.setattr(backup, "datetime", type("D", (), {"now": staticmethod(lambda: AGORA)}))

    assert backup.main([str(destino), "--manter", "3"]) == 0

    assert sobras(destino) == [
        "aprovacao_20261023_143005",  # o novo, mais "antigo" que todos
        "aprovacao_20990104_000000",
        "aprovacao_20990105_000000",
    ]
    assert "aprovacao_20990101_000000" in capsys.readouterr().out


def test_retencao_preservar_conta_entre_os_mantidos(backup, tmp_path):
    destino = tmp_path / "bk"
    nomes = [f"aprovacao_2026010{d}_000000" for d in range(1, 7)]
    criar_backups_falsos(destino, nomes)

    apagados = backup.aplicar_retencao(destino, 3, preservar=destino / nomes[1])

    assert sorted(p.name for p in apagados) == [nomes[0], nomes[2], nomes[3]]
    assert sobras(destino) == [nomes[1], nomes[4], nomes[5]]


def test_retencao_preservar_com_manter_um_deixa_so_o_preservado(backup, tmp_path):
    destino = tmp_path / "bk"
    nomes = [f"aprovacao_2026010{d}_000000" for d in range(1, 4)]
    criar_backups_falsos(destino, nomes)

    backup.aplicar_retencao(destino, 1, preservar=destino / nomes[0])

    assert sobras(destino) == [nomes[0]]


def test_retencao_preservar_entre_os_mais_novos_se_comporta_como_antes(backup, tmp_path):
    destino = tmp_path / "bk"
    nomes = [f"aprovacao_2026010{d}_000000" for d in range(1, 5)]
    criar_backups_falsos(destino, nomes)

    backup.aplicar_retencao(destino, 2, preservar=destino / nomes[3])

    assert sobras(destino) == nomes[2:]


def test_retencao_preservar_inexistente_e_ignorado(backup, tmp_path):
    destino = tmp_path / "bk"
    nomes = [f"aprovacao_2026010{d}_000000" for d in range(1, 4)]
    criar_backups_falsos(destino, nomes)

    backup.aplicar_retencao(destino, 2, preservar=destino / "aprovacao_20300101_000000")

    assert sobras(destino) == nomes[1:]


def test_retencao_nao_apaga_mais_do_que_o_excedente(backup, tmp_path):
    destino = tmp_path / "bk"
    nomes = [f"aprovacao_2026010{d}_000000" for d in range(1, 6)]
    criar_backups_falsos(destino, nomes)
    assert backup.aplicar_retencao(destino, 7, preservar=destino / nomes[0]) == []
    assert sobras(destino) == nomes


def estado_durante(monkeypatch, backup, nome, destino, registro):
    """Troca a função `nome` do módulo por uma que anota o conteúdo do destino."""
    original = getattr(backup, nome)

    def espiada(*args, **kwargs):
        registro.append(sobras(destino))
        return original(*args, **kwargs)

    monkeypatch.setattr(backup, nome, espiada)


def test_backup_so_ganha_o_nome_final_no_fim(backup, data_dir, tmp_path, monkeypatch):
    destino = tmp_path / "bk"
    durante_pdfs, durante_integridade = [], []
    estado_durante(monkeypatch, backup, "_copiar_pdfs", destino, durante_pdfs)
    estado_durante(monkeypatch, backup, "_integridade", destino, durante_integridade)

    pasta = backup.fazer_backup(data_dir, destino, AGORA)

    for registro in (durante_pdfs, durante_integridade):
        assert len(registro) == 1
        assert len(registro[0]) == 1 and registro[0][0].startswith(".tmp-")
    assert sobras(destino) == [pasta.name]


def test_falha_na_copia_dos_pdfs_nao_deixa_nada(backup, data_dir, tmp_path, monkeypatch):
    destino = tmp_path / "bk"

    def negar(*args, **kwargs):
        raise PermissionError("sem acesso ao pdf")

    monkeypatch.setattr(backup.shutil, "copy2", negar)

    with pytest.raises(PermissionError, match="sem acesso"):
        backup.fazer_backup(data_dir, destino, AGORA)

    assert sobras(destino) == []


def test_falha_logo_antes_de_renomear_nao_deixa_nada(backup, data_dir, tmp_path, monkeypatch):
    destino = tmp_path / "bk"

    def falhar(*args, **kwargs):
        raise OSError("disco cheio")

    monkeypatch.setattr(backup.os, "replace", falhar)

    with pytest.raises(OSError, match="disco cheio"):
        backup.fazer_backup(data_dir, destino, AGORA)

    assert sobras(destino) == []


def test_falha_na_conferencia_de_integridade_nao_deixa_nada(
    backup, data_dir, tmp_path, monkeypatch
):
    destino = tmp_path / "bk"

    def falhar(arquivo):
        raise OSError("disco sumiu")

    monkeypatch.setattr(backup, "_integridade", falhar)

    with pytest.raises(OSError, match="disco sumiu"):
        backup.fazer_backup(data_dir, destino, AGORA)

    assert sobras(destino) == []


def test_falha_na_retencao_mantem_o_backup_continua_e_devolve_1(
    backup, data_dir, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("PEDIDOS_DATA_DIR", str(data_dir))
    destino = tmp_path / "bk"
    nomes = [f"aprovacao_2020010{d}_000000" for d in range(1, 5)]
    criar_backups_falsos(destino, nomes)
    original = backup.shutil.rmtree

    def rmtree_com_falha(pasta, *args, **kwargs):
        if Path(pasta).name == nomes[1]:
            raise PermissionError("em uso")
        return original(pasta, *args, **kwargs)

    monkeypatch.setattr(backup.shutil, "rmtree", rmtree_com_falha)

    codigo = backup.main([str(destino), "--manter", "1"])

    saida = capsys.readouterr()
    novo = next(p for p in destino.iterdir() if not p.name.startswith("aprovacao_2020"))
    assert codigo == 1
    assert "FOI criado" in saida.err and str(novo) in saida.err
    assert nomes[1] in saida.err and "em uso" in saida.err
    assert "Traceback" not in saida.err
    # as outras continuaram sendo apagadas, mesmo depois da falha
    assert sobras(destino) == sorted([nomes[1], novo.name])
    assert nomes[0] in saida.out and nomes[2] in saida.out and nomes[3] in saida.out


def test_retencao_levanta_erro_com_apagados_e_falhas(backup, tmp_path, monkeypatch):
    destino = tmp_path / "bk"
    nomes = [f"aprovacao_2020010{d}_000000" for d in range(1, 4)]
    criar_backups_falsos(destino, nomes)
    original = backup.shutil.rmtree

    def rmtree_com_falha(pasta, *args, **kwargs):
        if Path(pasta).name == nomes[0]:
            raise PermissionError("em uso")
        return original(pasta, *args, **kwargs)

    monkeypatch.setattr(backup.shutil, "rmtree", rmtree_com_falha)

    with pytest.raises(backup.ErroRetencao) as info:
        backup.aplicar_retencao(destino, 1)

    assert [p.name for p in info.value.apagados] == [nomes[1]]
    assert [p.name for p, _ in info.value.falhas] == [nomes[0]]


def test_apagar_pasta_remove_arquivo_somente_leitura(backup, tmp_path):
    import os
    import stat

    pasta = tmp_path / "aprovacao_20200101_000000"
    pasta.mkdir()
    arquivo = pasta / "pedidos.db"
    arquivo.write_bytes(b"x")
    os.chmod(arquivo, stat.S_IREAD)

    backup._apagar_pasta(pasta)

    assert not pasta.exists()


def test_tratador_do_rmtree_destrava_e_tenta_de_novo(backup, tmp_path):
    import os
    import stat

    arquivo = tmp_path / "ro.bin"
    arquivo.write_bytes(b"x")
    os.chmod(arquivo, stat.S_IREAD)
    tentativas = []

    def apagar(caminho):
        tentativas.append(os.access(caminho, os.W_OK))
        os.unlink(caminho)

    backup._ao_falhar(apagar, str(arquivo), PermissionError("ro"))

    assert tentativas == [True]
    assert not arquivo.exists()


# No Windows o modo é emulado (somente leitura vira 0o444, nunca S_IREAD = 0o400).
@pytest.mark.skipif(sys.platform == "win32", reason="permissões POSIX")
def test_tratador_do_rmtree_nao_segue_links(backup, tmp_path):
    import os
    import stat

    alvo = tmp_path / "alvo.bin"
    alvo.write_bytes(b"x")
    os.chmod(alvo, stat.S_IREAD)
    link = tmp_path / "link"
    try:
        link.symlink_to(alvo)
    except (OSError, NotImplementedError):
        pytest.skip("sem suporte a links simbólicos")

    with pytest.raises(PermissionError):
        backup._ao_falhar(os.unlink, str(link), PermissionError("ro"))

    assert stat.S_IMODE(alvo.stat().st_mode) == stat.S_IREAD


# No Windows chmod(0) só liga o atributo somente leitura: o banco continua legível.
@pytest.mark.skipif(sys.platform == "win32", reason="permissões POSIX")
def test_banco_ilegivel_falha_limpo(backup, data_dir, tmp_path, capsys, monkeypatch):
    import os

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root lê qualquer arquivo")
    monkeypatch.setenv("PEDIDOS_DATA_DIR", str(data_dir))
    destino = tmp_path / "bk"
    banco = data_dir / "pedidos.db"
    banco.chmod(0)
    try:
        codigo = backup.main([str(destino)])
    finally:
        banco.chmod(0o600)

    saida = capsys.readouterr()
    assert codigo == 1
    assert "banco" in saida.err and "Traceback" not in saida.err
    assert sobras(destino) == []


def test_sqlite3_error_na_abertura_vira_runtimeerror(backup, data_dir, tmp_path, monkeypatch):
    destino = tmp_path / "bk"

    def nao_abre(*args, **kwargs):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(backup.sqlite3, "connect", nao_abre)

    with pytest.raises(RuntimeError, match="banco"):
        backup.fazer_backup(data_dir, destino, AGORA)

    assert sobras(destino) == []


def test_main_com_pedidos_db_que_e_pasta_devolve_1(backup, tmp_path, monkeypatch, capsys):
    dados = tmp_path / "dados"
    (dados / "pedidos.db").mkdir(parents=True)
    monkeypatch.setenv("PEDIDOS_DATA_DIR", str(dados))
    destino = tmp_path / "bk"

    assert backup.main([str(destino)]) == 1

    err = capsys.readouterr().err
    assert "pedidos.db" in err and "Traceback" not in err
    assert not destino.exists()
