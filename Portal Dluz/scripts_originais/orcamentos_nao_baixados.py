#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Orçamentos — baixados e não baixados (ATS ERP — Firebird)
===========================================================
Pergunta a empresa (Dluz Elétrica ou Dluz Iluminação) e um intervalo de datas,
e lista todos os orçamentos do período com uma coluna STATUS indicando se já
geraram pedido de venda (PEDIDOC.CODORC) ou não, incluindo o número do pedido
gerado quando houver.

Dependências:
    pip install firebirdsql pandas openpyxl
"""

import os
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import firebirdsql
import pandas as pd

# ── Configuração das empresas ───────────────────────────────────
PORT = 3050
CHARSET = "WIN1252"
USER = "SYSDBA"
PASSWORD = "masterkey"

EMPRESAS = {
    "1": {
        "nome": "Dluz Elétrica",
        "host": "192.168.0.41",
        "database": "D:\\DLUZR\\BANCO\\DLUZR25.FB",
    },
    "2": {
        "nome": "Dluz Iluminação",
        "host": "192.168.0.250",
        "database": "D:\\ILUMIR\\BANCO\\ILUMIR.FB",
    },
}

SQL = """
SELECT
    O.CODEMPRESA,
    O.CODORC,
    O.DATAORCAMENTO,
    O.NOMECLIENTE,
    O.CODCLIENTE,
    O.CODVENDEDOR,
    V.NOME AS VENDEDOR,
    O.TOTALORCAMENTO,
    O.VALIDADE,
    CASE WHEN P.CODPEDIDO IS NULL THEN 'Não baixado' ELSE 'Baixado' END AS STATUS,
    P.CODPEDIDO,
    P.DATAPEDIDO
FROM ORCCABE O
LEFT JOIN PEDIDOC P
    ON P.CODEMPRESAORC = O.CODEMPRESA
   AND P.CODORC = O.CODORC
LEFT JOIN VENDEND V
    ON V.CODVENDEDOR = O.CODVENDEDOR
WHERE O.DATAORCAMENTO >= ?
  AND O.DATAORCAMENTO <  ?
  AND (O.CANCELADO IS NULL OR O.CANCELADO <> 'S')
ORDER BY O.DATAORCAMENTO DESC
"""


def escolher_empresa() -> dict:
    print("Empresas disponíveis:")
    for chave, dados in EMPRESAS.items():
        print(f"  {chave} - {dados['nome']}")
    escolha = input("Escolha a empresa (número): ").strip()
    if escolha not in EMPRESAS:
        raise SystemExit("Empresa inválida.")
    return EMPRESAS[escolha]


def pedir_data(rotulo: str) -> date:
    while True:
        texto = input(f"Data {rotulo} (DD/MM/AAAA): ").strip()
        try:
            return datetime.strptime(texto, "%d/%m/%Y").date()
        except ValueError:
            print("Data inválida, use o formato DD/MM/AAAA (ex: 01/09/2026).")


def buscar_orcamentos(cfg: dict, dt_ini: date, dt_fim: date) -> pd.DataFrame:
    con = firebirdsql.connect(
        host=cfg["host"], database=cfg["database"], port=PORT,
        user=USER, password=PASSWORD, charset=CHARSET,
    )
    try:
        cur = con.cursor()
        # dt_fim é exclusivo, então soma 1 dia para incluir o dia final inteiro
        cur.execute(SQL, (dt_ini.isoformat(), (dt_fim + timedelta(days=1)).isoformat()))
        colunas = [d[0] for d in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=colunas)
    finally:
        con.close()
    return df


def main() -> None:
    empresa = escolher_empresa()
    dt_ini = pedir_data("inicial")
    dt_fim = pedir_data("final")

    print(f"\nConsultando {empresa['nome']} de {dt_ini.strftime('%d/%m/%Y')} "
          f"a {dt_fim.strftime('%d/%m/%Y')}...", flush=True)

    df = buscar_orcamentos(empresa, dt_ini, dt_fim)

    if df.empty:
        print("Nenhum orçamento encontrado para esse período.")
        return

    qtd_baixados = (df["STATUS"] == "Baixado").sum()
    qtd_nao_baixados = (df["STATUS"] == "Não baixado").sum()
    print(f"\n{len(df)} orçamento(s) no período — "
          f"{qtd_baixados} baixado(s), {qtd_nao_baixados} não baixado(s):\n")
    print(df.to_string(index=False))

    nome_arquivo = (f"orcamentos_{empresa['nome'].replace(' ', '_')}_"
                    f"{dt_ini.isoformat()}_a_{dt_fim.isoformat()}.xlsx")

    # Tenta salvar na pasta atual e, se der PermissionError (comum quando o
    # Acesso Controlado a Pastas do Windows Defender bloqueia o python.exe),
    # tenta outras pastas comuns como alternativa.
    candidatos = [
        Path.cwd() / nome_arquivo,
        Path.home() / "Downloads" / nome_arquivo,
        Path.home() / nome_arquivo,
    ]
    salvo = None
    ultimo_erro = None
    for caminho in candidatos:
        try:
            df.to_excel(caminho, index=False)
            salvo = caminho
            break
        except PermissionError as e:
            ultimo_erro = e
            continue

    if salvo is None:
        print(f"\nNão foi possível salvar o Excel em nenhuma pasta testada: {ultimo_erro}")
        print("Provável causa: Acesso Controlado a Pastas do Windows Defender bloqueando o python.exe.")
        print("Libere o python.exe em: Segurança do Windows > Proteção contra vírus e ameaças > "
              "Gerenciar proteção contra ransomware > Acesso controlado a pastas.")
        return

    print(f"\nArquivo gerado: {salvo}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("\nOcorreu um erro:\n")
        traceback.print_exc()
    finally:
        input("\nPressione Enter para sair...")
