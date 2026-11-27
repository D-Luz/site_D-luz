#!/usr/bin/env python3
"""Backup do Portal Dluz: bancos do portal, Pedidos, CRM e Caixa, e os PDFs dos pedidos.

Uso: DLUZ_DATA_DIR=<pasta> python scripts/backup.py <pasta_de_backup> [--manter 30]

Cria <pasta_de_backup>/portal_AAAAMMDD_HHMM/ com uma cópia consistente de cada
banco SQLite (pela API de backup do SQLite, segura com o sistema no ar) e dos
PDFs. Apaga os backups com mais de --manter dias (padrão 30).
"""

import argparse
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from portal import config  # noqa: E402

BANCOS = ["portal.sqlite", "pedidos/pedidos.db", "crm/crm.sqlite", "caixa/caixa.sqlite"]
PASTAS = ["pedidos/pdfs"]
CHAVES = ["segredo.key", "pedidos/secret.key", "crm/segredo.key", "caixa/segredo.key", "base_gestao_separada.xlsx"]


def copiar_banco(origem, destino):
    destino.parent.mkdir(parents=True, exist_ok=True)
    fonte = sqlite3.connect(f"file:{origem}?mode=ro", uri=True)
    alvo = sqlite3.connect(destino)
    try:
        fonte.backup(alvo)
    finally:
        alvo.close()
        fonte.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("destino")
    ap.add_argument("--manter", type=int, default=30)
    args = ap.parse_args()
    dados = config.pasta_dados()
    raiz = Path(args.destino)
    alvo = raiz / f"portal_{datetime.now():%Y%m%d_%H%M}"
    alvo.mkdir(parents=True, exist_ok=False)
    for nome in BANCOS:
        origem = _resolver(dados, nome)
        if origem.exists():
            copiar_banco(origem, alvo / nome)
            print(f"✔ {nome}")
    for nome in PASTAS + CHAVES:
        origem = _resolver(dados, nome)
        if origem.is_dir():
            shutil.copytree(origem, alvo / nome)
            print(f"✔ {nome}/")
        elif origem.is_file():
            (alvo / nome).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origem, alvo / nome)
    limite = time.time() - args.manter * 86400
    for antigo in raiz.glob("portal_*"):
        if antigo.is_dir() and antigo != alvo and antigo.stat().st_mtime < limite:
            shutil.rmtree(antigo, ignore_errors=True)
            print(f"apagado backup antigo: {antigo.name}")
    print(f"Backup em {alvo}")


def _resolver(dados, nome):
    """pedidos/..., crm/..., caixa/... seguem a pasta de cada sistema (pode ser uma instalação antiga)."""
    sistema, _, resto = nome.partition("/")
    if resto and sistema in ("pedidos", "crm", "caixa"):
        return config.pasta_do_sistema(dados, sistema) / resto
    return Path(dados) / nome


if __name__ == "__main__":
    main()
