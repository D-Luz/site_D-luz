#!/usr/bin/env python3
"""Servidor do CRM (Waitress). Uso: CRM_DATA_DIR=<pasta> python3 scripts/iniciar.py [porta]

Escuta só em 127.0.0.1 por padrão; para a rede da loja use CRM_HOST=0.0.0.0
(e, sem HTTPS, CRM_COOKIE_INSECURE=1).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from waitress import serve  # noqa: E402

from app import create_app  # noqa: E402

if __name__ == "__main__":
    porta = int(sys.argv[1]) if len(sys.argv) > 1 else 8100
    host = os.environ.get("CRM_HOST", "127.0.0.1")
    print(f"CRM Dluz em http://{host}:{porta}")
    serve(create_app(), host=host, port=porta, threads=4)
