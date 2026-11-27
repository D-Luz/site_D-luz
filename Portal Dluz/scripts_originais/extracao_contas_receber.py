import firebirdsql
import pandas as pd
import warnings
from datetime import datetime

# Silencia aviso sobre conexões nativas do pandas
warnings.filterwarnings('ignore', message='pandas only supports SQLAlchemy')

# ═══════════════════════════════════════════════════════════════
# CONFIGURAÇÃO DAS 3 EMPRESAS
# ═══════════════════════════════════════════════════════════════
EMPRESAS = {
    "Dluz Elétrica": {"HOST": "192.168.0.41", "DATABASE": r"D:\DLUZR\BANCO\DLUZR25.FB"},
    "Cor e Cia": {"HOST": "192.168.0.200", "DATABASE": r"D:\CorCiaR\BANCO\CORCIAR25.FB"},
    "Dluz Iluminação": {"HOST": "192.168.0.250", "DATABASE": r"D:\ILUMIR\BANCO\ILUMIR.FB"},
}

USER     = 'SYSDBA'
PASSWORD = 'masterkey'
PORT     = 3050
CHARSET  = 'WIN1252'

# Mesma query do gerar_dashboard_ats-11.py (SQL_RECEBER), com o join de VENDEND
# acrescentado para trazer o nome do vendedor.
SQL_RECEBER = """
SELECT
    d.DT_VENCIMENTO AS "VENCIMENTO_RAW", d.DT_EMISSAO AS "EMISSÃO_RAW",
    d.CODDOCTO AS "DOCUMENTO", d.CODCLIENTE AS "CÓD. CLIENTE", c.NOME AS "CLIENTE",
    d.CODVENDEDOR AS "CÓD. VENDEDOR", v.NOME AS "VENDEDOR",
    COALESCE(d.VALORDOCTO, 0) AS "VALOR TÍTULO", COALESCE(d.VALORPAGO, 0) AS "VALOR PAGO",
    CASE WHEN d.SITUACAO = 1 THEN 'Aberto' WHEN d.SITUACAO = 3 THEN 'Parcial' ELSE CAST(d.SITUACAO AS VARCHAR(10)) END AS "SITUAÇÃO"
FROM DOCUREC d
LEFT JOIN CLIENTE c ON d.CODCLIENTE = c.CODCLIENTE
LEFT JOIN VENDEND v ON d.CODVENDEDOR = v.CODVENDEDOR
WHERE d.SITUACAO IN (1, 3)
ORDER BY c.NOME, d.DT_VENCIMENTO
"""


def formatar_codigo(valor):
    """Normaliza o código do cliente para texto, removendo o '.0' que o
    pandas adiciona quando a coluna é lida como float (por causa de nulos)."""
    if pd.isna(valor):
        return ''
    texto = str(valor).strip()
    if texto.endswith('.0'):
        texto = texto[:-2]
    return texto


def formatar_vendedor(codigo, nome):
    nome = '' if pd.isna(nome) else str(nome).strip()
    if nome:
        return nome
    codigo = formatar_codigo(codigo)
    return f"Cód. {codigo}" if codigo else "-"


def classificar_data(venc, hoje):
    """Mesma classificação do dashboard (coluna STATUS)."""
    if pd.isna(venc):
        return 'Sem Vencimento'
    diff_dias = (hoje - venc).days
    if diff_dias < 0:
        return 'A vencer'
    elif diff_dias <= (2 * 365.25):
        return 'Em dia'
    elif diff_dias <= (5 * 365.25):
        return 'Antigo'
    else:
        return 'Prescrito'


def extrair_contas_receber():
    print("🚀 Iniciando extração de Contas a Receber\n" + "-" * 60)

    lista_tabelas = []
    for nome_empresa, config in EMPRESAS.items():
        print(f"🔌 Conectando: {nome_empresa} ({config['HOST']})...")
        try:
            con = firebirdsql.connect(
                host=config["HOST"], database=config["DATABASE"],
                port=PORT, user=USER, password=PASSWORD, charset=CHARSET
            )
            df = pd.read_sql(SQL_RECEBER, con)
            con.close()

            if not df.empty:
                df.insert(0, "EMPRESA", nome_empresa)
                lista_tabelas.append(df)
                print(f"  ✔ Sucesso: {len(df)} títulos extraídos.")
            else:
                print("  ⚠️ Nenhum título em aberto/parcial nesta empresa.")
        except Exception as e:
            print(f"  ❌ Falha de conexão com a {nome_empresa}:\n    {e}")

    if not lista_tabelas:
        print("\n❌ Extração abortada. Nenhum dado retornado.")
        return

    df_final = pd.concat(lista_tabelas, ignore_index=True)

    # Colunas calculadas — mesma lógica do dashboard
    df_final['VENCIMENTO'] = pd.to_datetime(df_final['VENCIMENTO_RAW'])
    df_final['EMISSÃO'] = pd.to_datetime(df_final['EMISSÃO_RAW'])
    df_final['SALDO'] = df_final['VALOR TÍTULO'] - df_final['VALOR PAGO']
    hoje = pd.Timestamp(datetime.now().date())
    df_final['STATUS'] = df_final['VENCIMENTO'].apply(lambda v: classificar_data(v, hoje))
    df_final['CÓD. CLIENTE'] = df_final['CÓD. CLIENTE'].apply(formatar_codigo)
    df_final['VENDEDOR'] = df_final.apply(lambda r: formatar_vendedor(r['CÓD. VENDEDOR'], r['VENDEDOR']), axis=1)

    # Layout final pedido: EMPRESA, CÓD. CLIENTE, DOCUMENTO, VENDEDOR, EMISSÃO,
    # VENCIMENTO, STATUS, SITUAÇÃO, TÍTULO, PAGO, SALDO
    df_final['EMISSÃO'] = df_final['EMISSÃO'].dt.strftime('%d/%m/%Y')
    df_final['VENCIMENTO'] = df_final['VENCIMENTO'].dt.strftime('%d/%m/%Y')
    df_final = df_final.rename(columns={'VALOR TÍTULO': 'TÍTULO', 'VALOR PAGO': 'PAGO'})
    df_final = df_final[[
        'EMPRESA', 'CÓD. CLIENTE', 'DOCUMENTO', 'VENDEDOR', 'EMISSÃO',
        'VENCIMENTO', 'STATUS', 'SITUAÇÃO', 'TÍTULO', 'PAGO', 'SALDO'
    ]]
    df_final = df_final.sort_values(['EMPRESA', 'VENCIMENTO'])

    linha_totais = {col: "" for col in df_final.columns}
    linha_totais["EMPRESA"] = "TOTAIS GERAIS --->"
    linha_totais["TÍTULO"] = df_final["TÍTULO"].sum()
    linha_totais["PAGO"] = df_final["PAGO"].sum()
    linha_totais["SALDO"] = df_final["SALDO"].sum()
    df_final = pd.concat([df_final, pd.DataFrame([linha_totais])], ignore_index=True)

    nome_arquivo = f"Contas_Receber_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    df_final.to_excel(nome_arquivo, index=False, engine='openpyxl')

    print("-" * 60)
    print("✅ Processo finalizado com sucesso!")
    print(f"💾 Arquivo gerado: {nome_arquivo}")


if __name__ == "__main__":
    extrair_contas_receber()
