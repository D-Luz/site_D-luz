#!/usr/bin/env python3
"""Confere (só leitura) se o recurso de eletricista por venda do ATS está ligado e em uso.

Uso: .venv/bin/python scripts/verificar_erp_profissional.py
Rode antes e depois do ajuste na ATS. Depois de ligado, faça uma venda de teste com
um eletricista e rode de novo: «vendas com eletricista» deve subir.
"""

import sys
from pathlib import Path

import firebirdsql

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402

BASES = {
    "Dluz Elétrica": config.EMPRESAS["ELE"],
    "Dluz Iluminação": config.EMPRESAS["ILU"],
    "Cor e Cia": {"host": "192.168.0.200", "banco": "D:\\CorCiaR\\BANCO\\CORCIAR25.FB"},
}


def um(cur, sql):
    try:
        cur.execute(sql)
        return cur.fetchone()
    except Exception as e:  # noqa: BLE001
        return f"erro: {str(e).splitlines()[-1][:60]}"


for nome, b in BASES.items():
    print(f"\n== {nome}")
    try:
        con = firebirdsql.connect(host=b["host"], port=config.FB_PORTA, database=b["banco"],
                                  user=config.FB_USUARIO, password=config.FB_SENHA, charset=config.FB_CHARSET)
    except Exception as e:  # noqa: BLE001
        print("  sem conexão:", str(e)[:80])
        continue
    cur = con.cursor()
    print("  PDV mostra o campo (PARAPDV.MOSTRAPROFISSIONAL):", um(cur, "SELECT FIRST 1 MOSTRAPROFISSIONAL FROM PARAPDV"))
    print("  Orçamento usa (PARAMDAV.USAPROFISSIONAL, OBRIGAR):", um(cur, "SELECT FIRST 1 USAPROFISSIONAL, OBRIGARPROFISSIONAL FROM PARAMDAV"))
    print("  Orçamento vertical (PARAMDAVVERTICAL.USAPROFISSIONAL):", um(cur, "SELECT FIRST 1 USAPROFISSIONAL FROM PARAMDAVVERTICAL"))
    print("  Eletricistas ativos no cadastro PROFISSIONAL:", um(cur, "SELECT COUNT(*) FROM PROFISSIONAL WHERE ATIVO = 'S'"))
    print("  Vendas com eletricista / total (COMPPEDIDOC):", um(
        cur, "SELECT SUM(CASE WHEN CODPROF IS NOT NULL AND CODPROF <> '' THEN 1 ELSE 0 END), COUNT(*) FROM COMPPEDIDOC"))
    con.close()
