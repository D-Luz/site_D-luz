#!/usr/bin/env python3
"""Sincroniza o CRM com o ERP (somente leitura no ERP).

Uso: CRM_DATA_DIR=<pasta> python3 scripts/sincronizar.py [ELE] [ILU]
Agende no Agendador de Tarefas (ou cron) a cada hora, em horário comercial.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db, sincronizar  # noqa: E402

if __name__ == "__main__":
    pasta = config.pasta_dados()
    conn = db.conectar(pasta / "crm.sqlite")
    db.criar_esquema(conn)
    empresas = [e for e in sys.argv[1:] if e in config.EMPRESAS] or None
    print(json.dumps(sincronizar.sincronizar(conn, empresas), ensure_ascii=False, indent=2))
