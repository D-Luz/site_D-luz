#!/usr/bin/env python3
"""Servidor do Portal Dluz (Waitress).

Uso: DLUZ_DATA_DIR=<pasta> python scripts/iniciar.py [porta]

Escuta em toda a rede da loja (0.0.0.0) na porta 8000 por padrão. Para mudar:
DLUZ_HOST e DLUZ_PORTA (ou a porta como argumento).
"""

import os
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from waitress import serve  # noqa: E402

from portal import carregador, create_app, integracao, tarefas  # noqa: E402


def _limpeza_diaria(app):
    while True:
        try:
            tarefas.limpar_antigas(app, dias=30)
        except Exception as exc:
            print(f"[portal] limpeza de arquivos antigos falhou: {exc}", file=sys.stderr)
        time.sleep(24 * 3600)


def _ip_da_rede():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.0.1", 80))
            return s.getsockname()[0]
    except OSError:
        return "este-computador"


if __name__ == "__main__":
    porta = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("DLUZ_PORTA", "8000"))
    host = os.environ.get("DLUZ_HOST", "0.0.0.0")
    app = create_app()
    carregador.preparar()
    for nome, erro in app.config["SISTEMAS_ERROS"].items():
        print(f"ATENÇÃO: {nome} não carregou: {erro}")
    integracao.iniciar_sincronizacao_crm(app)
    threading.Thread(target=_limpeza_diaria, args=(app,), daemon=True).start()
    print(f"Portal Dluz no ar: http://{_ip_da_rede() if host == '0.0.0.0' else host}:{porta}")
    serve(app, host=host, port=porta, threads=12)
