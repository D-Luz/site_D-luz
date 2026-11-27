#!/usr/bin/env python3
"""Confere a leitura do ATS de um dia, sem tocar no banco do caixa.

Uso: python scripts/verificar_ats.py ILU 2026-10-23

Mostra os totais por forma e confirma que as formas somam o total das vendas
(se não somarem, aparece "outra forma no ATS" e é preciso investigar).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import conferencia, config, erp  # noqa: E402
from app.formatos import brl  # noqa: E402


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in config.EMPRESAS:
        sys.exit(f"Uso: python scripts/verificar_ats.py [{'|'.join(config.EMPRESAS)}] AAAA-MM-DD")
    emp, dia = sys.argv[1], sys.argv[2]
    leitura = erp.ler_dia(emp, dia, forcar=True)
    print(f"{config.EMPRESAS[emp]['nome']} {dia}: {leitura['pdvs']} PDVs no caixa; a prazo {brl(leitura['a_prazo'])}")
    for t in conferencia.totais_por_forma([], leitura["partes"]):
        print(f"  {t['rotulo']:<22} {brl(t['ats']):>14}")
    outros = [p for p in leitura["partes"] if p["forma"] == "outros"]
    for p in outros:
        print(f"  ! PDV {p['pdv']} tem {brl(p['valor'])} sem forma identificada")
    print("OK: todas as formas identificadas." if not outros else "ATENÇÃO: há valores sem forma identificada.")


if __name__ == "__main__":
    main()
