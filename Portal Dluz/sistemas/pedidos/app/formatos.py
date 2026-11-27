"""Formatação de valores para exibição (reais e datas no padrão brasileiro)."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")


def formatar_brl(centavos):
    """123456 -> 'R$ 1.234,56'. Valores negativos levam o sinal antes do R$."""
    sinal = "-" if centavos < 0 else ""
    reais, resto = divmod(abs(int(centavos)), 100)
    return f"{sinal}R$ {reais:,}".replace(",", ".") + f",{resto:02d}"


def formatar_data(valor):
    """Data pura '2026-10-23' -> '23/10/2026'.

    Vazio vira 'não informada'; qualquer outro texto (inclusive um instante
    ISO, que deve passar por ``formatar_instante``) volta como está.
    """
    if not valor:
        return "não informada"
    try:
        return date.fromisoformat(str(valor)).strftime("%d/%m/%Y")
    except ValueError:
        return str(valor)


def formatar_instante(valor):
    """Instante ISO 8601 -> 'dd/mm/aaaa HH:MM' no horário de São Paulo.

    Valor sem fuso é tratado como UTC. Texto que não é um instante volta como
    está; nunca levanta.
    """
    try:
        instante = datetime.fromisoformat(str(valor))
        if instante.tzinfo is None:
            instante = instante.replace(tzinfo=timezone.utc)
        return instante.astimezone(FUSO).strftime("%d/%m/%Y %H:%M")
    except (ValueError, OverflowError):
        return str(valor)


# Rótulos em português de cada status e de cada ação do histórico (um só lugar).
STATUS_LABEL = {
    "pendente": "Pendente",
    "aprovado": "Aprovado",
    "reprovado": "Reprovado",
    "enviado": "Enviado",
    "faturado": "Faturado",
    "recebido": "Recebido",
    "arquivado": "Arquivado",
}

ACAO_LABEL = {
    "criar": "Pedido criado",
    "aprovar": "Aprovado",
    "reprovar": "Reprovado",
    "confirmar_envio": "Envio confirmado",
    "informar_nf": "Nota fiscal informada",
    "marcar_chegada": "Chegada registrada",
    "arquivar": "Arquivado",
}
