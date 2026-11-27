"""Leitura do ERP ATS (Firebird). SOMENTE consultas SELECT: este módulo nunca grava no ERP.

Cada empresa tem seu banco; os códigos (cliente, vendedor, pedido) se repetem entre
elas, por isso o CRM sempre guarda a chave da empresa junto.
"""

import firebirdsql

from . import config
from .formatos import normalizar_fone, para_data


def _conectar(emp):
    dados = config.EMPRESAS[emp]
    return firebirdsql.connect(
        host=dados["host"],
        port=config.FB_PORTA,
        database=dados["banco"],
        user=config.FB_USUARIO,
        password=config.FB_SENHA,
        charset=config.FB_CHARSET,
    )


def _select(cur, sql, params=()):
    if not sql.lstrip().upper().startswith("SELECT"):
        raise RuntimeError("O CRM só pode fazer consultas SELECT no ERP.")
    cur.execute(sql, params)
    return cur.fetchall()


def _s(v):
    return (v or "").strip() if isinstance(v, str) or v is None else str(v).strip()


def ler(emp, desde):
    """Tudo o que o CRM precisa de uma empresa. `desde` é um date (início do histórico)."""
    conn = _conectar(emp)
    try:
        cur = conn.cursor()
        vendedores = {_s(c): _s(n) for c, n in _select(cur, "SELECT CODVENDEDOR, NOME FROM VENDEND")}

        clientes = []
        for cod, nome, fone, cel, pref, seg, ult, nasc, ativo in _select(
            cur,
            "SELECT CODCLIENTE, NOME, FONE, CELULAR, PREFIXCELULAR, CODSEGMENTO, "
            "DT_ULTVENDA, DT_NASCIMENTO, ATIVO FROM CLIENTE",
        ):
            celular = _s(cel)
            if pref and celular and len(celular) in (8, 9):
                celular = _s(pref) + celular
            clientes.append(
                {
                    "codigo": _s(cod),
                    "nome": _s(nome),
                    "telefone": normalizar_fone(celular, _s(fone)),
                    "segmento": _s(seg),
                    "ult_venda": para_data(ult),
                    "nascimento": para_data(nasc),
                    "ativo": 0 if _s(ativo) == "N" else 1,
                }
            )

        # Eletricista indicado na própria venda (recurso nativo do ATS, hoje desligado:
        # PARAPDV.MOSTRAPROFISSIONAL / PARAMDAV.USAPROFISSIONAL). Uma consulta só, sem
        # JOIN com PEDIDOC: junções nessa tabela são lentas no servidor de produção.
        prof_por_pedido = {
            _s(cod): _s(prof)
            for cod, prof in _select(
                cur,
                "SELECT CODPEDIDO, CODPROF FROM COMPPEDIDOC "
                "WHERE TIPOPEDIDO = 55 AND CODPROF IS NOT NULL AND CODPROF <> ''",
            )
        }

        # Vendas efetivas: FATURADO='S'. O tipo 55 também guarda DAVs que nunca
        # moveram estoque (FATURADO='N'), por isso o filtro é obrigatório.
        vendas = [
            {
                "codigo": _s(cod),
                "data": para_data(data),
                "cliente": _s(cli),
                "vendedor": _s(vend),
                "vendedor_nome": vendedores.get(_s(vend), _s(vend)),
                "total": float(total or 0),
                "codprof": prof_por_pedido.get(_s(cod), ""),
            }
            for cod, data, cli, vend, total in _select(
                cur,
                "SELECT CODPEDIDO, DATAPEDIDO, CODCLIENTE, CODVENDEDOR, TOTALPEDIDO FROM PEDIDOC "
                "WHERE TIPOPEDIDO = 55 AND FATURADO = 'S' AND DATAPEDIDO >= ?",
                (desde,),
            )
        ]

        # Desde 15/09/2026 o orçamento é um DAV (tipo 'O') em PEDIDOC.
        orcamentos = [
            {
                "codigo": _s(cod),
                "data": para_data(data),
                "cliente": _s(cli),
                "vendedor": _s(vend),
                "vendedor_nome": vendedores.get(_s(vend), _s(vend)),
                "total": float(total or 0),
            }
            for cod, data, cli, vend, total in _select(
                cur,
                "SELECT CODPEDIDO, DATAPEDIDO, CODCLIENTE, CODVENDEDOR, TOTALPEDIDO FROM PEDIDOC "
                "WHERE TIPOPEDIDO = 55 AND TIPODAV = 'O' AND FATURADO = 'N' AND DATAPEDIDO >= ?",
                (desde,),
            )
        ]

        profissionais = [
            {
                "codigo": _s(cod),
                "nome": _s(nome),
                "telefone": normalizar_fone(_s(cel), _s(tel)),
                "nascimento": para_data(nasc),
                "cpf": _s(cpf),
                "ativo": 0 if _s(ativo) == "N" else 1,
            }
            for cod, nome, tel, cel, nasc, cpf, ativo in _select(
                cur,
                "SELECT CODPROF, NOME, TELEFONE, CELULAR, DT_NASCIMENTO, CGCCPF, ATIVO FROM PROFISSIONAL",
            )
        ]

        return {
            "clientes": clientes,
            "vendas": vendas,
            "orcamentos": orcamentos,
            "profissionais": profissionais,
        }
    finally:
        conn.close()
