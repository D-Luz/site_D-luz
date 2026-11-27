#!/usr/bin/env python3
"""Servidor da Conferência de Caixa (Waitress).

Uso: CAIXA_DATA_DIR=<pasta> python scripts/iniciar.py [porta]

Escuta só em 127.0.0.1 por padrão; para a rede da loja use CAIXA_HOST=0.0.0.0
(e, sem HTTPS, CAIXA_COOKIE_INSECURE=1).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from waitress import serve  # noqa: E402

from app import create_app  # noqa: E402

if __name__ == "__main__":
    porta = int(sys.argv[1]) if len(sys.argv) > 1 else 8200
    host = os.environ.get("CAIXA_HOST", "127.0.0.1")
    print(f"Conferência de Caixa em http://{host}:{porta}")
    serve(create_app(), host=host, port=porta, threads=6)
