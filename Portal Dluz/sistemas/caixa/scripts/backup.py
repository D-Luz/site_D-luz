#!/usr/bin/env python3
"""Backup do banco (cópia consistente mesmo com o sistema rodando).

Uso: CAIXA_DATA_DIR=<pasta> python scripts/backup.py <pasta de destino> [quantos guardar]

Grava caixa-AAAA-MM-DD_HHMM.sqlite no destino e apaga os mais antigos além dos
últimos N (padrão 90).
"""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402


def main():
    if len(sys.argv) < 2:
        sys.exit("Uso: python scripts/backup.py <pasta de destino> [quantos guardar]")
    origem = config.pasta_dados() / "caixa.sqlite"
    if not origem.exists():
        sys.exit(f"Banco não encontrado: {origem}")
    destino = Path(sys.argv[1])
    destino.mkdir(parents=True, exist_ok=True)
    manter = int(sys.argv[2]) if len(sys.argv) > 2 else 90
    arquivo = destino / f"caixa-{datetime.now():%Y-%m-%d_%H%M}.sqlite"
    with sqlite3.connect(str(origem)) as fonte, sqlite3.connect(str(arquivo)) as copia:
        fonte.backup(copia)
    antigos = sorted(destino.glob("caixa-*.sqlite"))[:-manter]
    for velho in antigos:
        velho.unlink()
    print(f"Backup gravado em {arquivo} ({len(antigos)} antigo(s) apagado(s)).")


if __name__ == "__main__":
    main()
