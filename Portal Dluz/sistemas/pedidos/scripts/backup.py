"""Backup do sistema: python scripts/backup.py <destino> [--manter N]

Cria ``<destino>/aprovacao_AAAAMMDD_HHMMSS/`` (hora local) com ``pedidos.db``
e a pasta ``pdfs/``, e depois apaga os backups mais antigos, mantendo os N
mais novos (padrão 30). Use uma pasta em OUTRO disco ou na rede: um backup na
mesma máquina não protege contra a falha dela.

O banco é copiado pela API de backup do SQLite, então a cópia é consistente
mesmo com o sistema em uso. Depois da cópia o script confere a integridade dela
(PRAGMA integrity_check). O backup é montado numa pasta temporária (.tmp-...)
dentro do destino e só ganha o nome final se tudo deu certo; qualquer falha
apaga a pasta temporária e nada é apagado do histórico.

A ``secret.key`` NÃO entra no backup: ele pode ficar num disco menos protegido
e, se a chave se perder, o único efeito é encerrar as sessões abertas.

A pasta de dados é a mesma do sistema, a de PEDIDOS_DATA_DIR. Essa variável é
OBRIGATÓRIA: sem ela o script se recusa a rodar, em vez de copiar em silêncio
a pasta data/ do programa. Código de saída: 0 em caso de sucesso, 1 se houve
erro (mensagem no stderr).
"""

import argparse
import os
import re
import shutil
import sqlite3
import stat
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

# Permite rodar o script direto da raiz do projeto, sem instalar nada.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import MSG_SEM_DATA_DIR, carregar, data_dir_definido  # noqa: E402

MANTER_PADRAO = 30
TIMEOUT_PADRAO = 60.0  # segundos esperando o banco ficar livre

NOME_BACKUP = re.compile(r"aprovacao_[0-9]{8}_[0-9]{6}")
NOME_PDF = re.compile(r"[0-9a-f]{32}\.pdf")
PREFIXO_TEMPORARIO = ".tmp-"


def _dentro_de(pasta: Path, base: Path) -> bool:
    return pasta == base or base in pasta.parents


def _integridade(arquivo: Path) -> list[str]:
    """Resultado do PRAGMA integrity_check: ['ok'] se a cópia está íntegra."""
    conexao = None
    try:
        conexao = sqlite3.connect(arquivo)
        return [linha[0] for linha in conexao.execute("PRAGMA integrity_check")]
    except sqlite3.Error as erro:  # estrago tão grande que nem a conferência roda
        return [str(erro)]
    finally:
        if conexao is not None:
            conexao.close()


def _copiar_banco(origem: Path, copia: Path, timeout: float) -> None:
    prazo = time.monotonic() + timeout

    def conferir_prazo(*_args):
        # Chamado a cada passo da cópia; evita esperar para sempre um banco travado.
        if time.monotonic() > prazo:
            raise sqlite3.OperationalError("o banco continuou em uso até o fim da espera")

    # Conexão normal (não somente leitura): se o sistema caiu no meio de uma
    # escrita, o SQLite precisa desfazer o diário pendente antes de ler.
    leitura = destino = None
    try:
        leitura = sqlite3.connect(origem, timeout=timeout)
        destino = sqlite3.connect(copia)
        leitura.backup(destino, pages=-1, progress=conferir_prazo, sleep=0.05)
    except sqlite3.Error as erro:
        raise RuntimeError(f"Não foi possível copiar o banco de dados ({erro}).") from erro
    finally:
        if destino is not None:
            destino.close()
        if leitura is not None:
            leitura.close()


def _copiar_pdfs(origem: Path, destino: Path) -> None:
    destino.mkdir()
    if not origem.is_dir():
        return
    for arquivo in sorted(origem.iterdir()):
        if not (NOME_PDF.fullmatch(arquivo.name) and arquivo.is_file()):
            continue
        try:
            shutil.copy2(arquivo, destino / arquivo.name)
        except FileNotFoundError:
            continue  # apagado pelo sistema durante a cópia


def fazer_backup(
    data_dir: Path,
    destino: Path,
    agora: datetime | None = None,
    *,
    timeout: float = TIMEOUT_PADRAO,
) -> Path:
    """Copia banco e PDFs para ``destino/aprovacao_AAAAMMDD_HHMMSS/`` e devolve a pasta.

    Levanta FileNotFoundError (sem pedidos.db), ValueError (destino dentro da
    pasta de dados), FileExistsError (já há backup com esse nome; nunca
    sobrescreve) ou RuntimeError (banco ilegível, ocupado demais ou cópia
    corrompida). Em qualquer falha nada fica no destino.
    """
    data_dir = Path(data_dir)
    destino = Path(destino)
    banco = data_dir / "pedidos.db"

    if _dentro_de(destino.resolve(), data_dir.resolve()):
        raise ValueError("O destino do backup não pode ficar dentro da pasta de dados.")
    if not banco.is_file():
        raise FileNotFoundError(f"Não encontrei o banco de dados em {banco}.")

    agora = agora or datetime.now()
    final = destino / f"aprovacao_{agora:%Y%m%d_%H%M%S}"
    destino.mkdir(parents=True, exist_ok=True)
    if final.exists():
        raise FileExistsError(f"Já existe um backup com este nome: {final}")

    temporaria = destino / f"{PREFIXO_TEMPORARIO}{uuid.uuid4().hex}"
    temporaria.mkdir()
    try:
        copia = temporaria / "pedidos.db"
        _copiar_banco(banco, copia, timeout)
        problemas = _integridade(copia)
        if problemas != ["ok"]:
            raise RuntimeError(
                "A cópia do banco está corrompida (PRAGMA integrity_check: "
                f"{'; '.join(problemas[:3])}). O backup foi descartado."
            )
        _copiar_pdfs(data_dir / "pdfs", temporaria / "pdfs")
        if final.exists():
            raise FileExistsError(f"Já existe um backup com este nome: {final}")
        os.replace(temporaria, final)
    except BaseException:
        shutil.rmtree(temporaria, ignore_errors=True)
        raise
    return final


class ErroRetencao(Exception):
    """Alguns backups antigos não puderam ser apagados.

    ``apagados`` são os que saíram; ``falhas`` é uma lista de (pasta, erro).
    """

    def __init__(self, apagados, falhas):
        self.apagados = apagados
        self.falhas = falhas
        detalhe = "; ".join(f"{pasta} ({erro})" for pasta, erro in falhas)
        super().__init__(f"Não consegui remover: {detalhe}")


def _ao_falhar(funcao, caminho, erro):
    """Tratador do rmtree: tira o atributo somente leitura e tenta de novo uma vez."""
    excecao = erro[1] if isinstance(erro, tuple) else erro  # onerror x onexc
    if os.path.islink(caminho):
        raise excecao  # nunca mexe no destino de um link
    os.chmod(caminho, stat.S_IRWXU)
    funcao(caminho)


def _apagar_pasta(pasta: Path) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(pasta, onexc=_ao_falhar)
    else:
        shutil.rmtree(pasta, onerror=_ao_falhar)


def aplicar_retencao(destino: Path, manter: int, *, preservar: Path | None = None) -> list[Path]:
    """Apaga os backups mais antigos além dos ``manter`` mais novos; devolve os apagados.

    Só mexe em pastas chamadas aprovacao_AAAAMMDD_HHMMSS; qualquer outra coisa
    em ``destino`` (arquivos, pastas, .tmp-...) é ignorada.

    ``preservar`` (o backup recém-criado) nunca é apagado, mesmo que o relógio
    do PC o faça parecer o mais antigo; ele conta entre os ``manter``, e as
    vagas restantes ficam com os mais novos dos demais.

    Se alguma pasta não puder ser apagada, as outras continuam sendo tentadas e
    no fim levanta ErroRetencao com o que saiu e o que falhou.
    """
    if manter < 1:
        raise ValueError("O número de backups a manter precisa ser pelo menos 1.")
    destino = Path(destino)
    if not destino.is_dir():
        return []
    backups = sorted(
        (
            p
            for p in destino.iterdir()
            if NOME_BACKUP.fullmatch(p.name) and p.is_dir() and not p.is_symlink()
        ),
        key=lambda p: p.name,
    )
    preservar = Path(preservar) if preservar is not None else None
    vagas = manter
    if preservar in backups:
        backups.remove(preservar)
        vagas -= 1
    apagar = backups[: max(0, len(backups) - vagas)]

    apagados, falhas = [], []
    for pasta in apagar:
        try:
            _apagar_pasta(pasta)
        except OSError as erro:
            falhas.append((pasta, erro))
        else:
            apagados.append(pasta)
    if falhas:
        raise ErroRetencao(apagados, falhas)
    return apagados


def _tamanho_mb(pasta: Path) -> float:
    return sum(p.stat().st_size for p in pasta.rglob("*") if p.is_file()) / (1024 * 1024)


def _argumentos(argv):
    analisador = argparse.ArgumentParser(
        description="Copia o banco e os PDFs do sistema para uma pasta de backup."
    )
    analisador.add_argument("destino", type=Path, help="pasta onde os backups ficam guardados")
    analisador.add_argument(
        "--manter",
        type=int,
        default=MANTER_PADRAO,
        help=f"quantos backups manter (padrão {MANTER_PADRAO}); os mais antigos são apagados",
    )
    return analisador.parse_args(argv)


def main(argv=None) -> int:
    argumentos = _argumentos(sys.argv[1:] if argv is None else argv)
    if argumentos.manter < 1:
        print("Erro: --manter precisa ser pelo menos 1.", file=sys.stderr)
        return 1
    if not data_dir_definido():
        print(MSG_SEM_DATA_DIR, file=sys.stderr)
        return 1
    try:
        data_dir = carregar()["DATA_DIR"]
        pasta = fazer_backup(data_dir, argumentos.destino)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 1
    tamanho = f"{_tamanho_mb(pasta):.1f}".replace(".", ",")
    print(f"Backup criado: {pasta} ({tamanho} MB)")
    try:
        for antigo in aplicar_retencao(argumentos.destino, argumentos.manter, preservar=pasta):
            print(f"Backup antigo removido: {antigo}")
    except ErroRetencao as erro:
        for antigo in erro.apagados:
            print(f"Backup antigo removido: {antigo}")
        print(
            f"Erro: o backup FOI criado ({pasta}), mas não consegui remover backups antigos. "
            f"{erro}",
            file=sys.stderr,
        )
        return 1
    except OSError as erro:
        print(
            f"Erro: o backup FOI criado ({pasta}), mas a limpeza de backups antigos falhou: {erro}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
