"""Testa o envio de e-mail do sistema, sem abrir pedido nenhum.

    python scripts/testar_email.py alguem@suaempresa.com.br

Manda UM e-mail de teste para o endereço dado, usando as mesmas variáveis de
ambiente (PEDIDOS_SMTP_HOST, _PORTA, _USUARIO, _SENHA e _REMETENTE) e o mesmo
código de envio que o sistema usa de verdade. Se falhar, explica em português
o que conferir. Não precisa de PEDIDOS_DATA_DIR e não abre o banco: só fala com
o servidor de e-mail.

O teste funciona mesmo com PEDIDOS_EMAIL_MODE=desligado (o servidor do sistema
só envia com `smtp`; o script avisa quando for esse o caso). A senha SMTP nunca
é escrita na tela: nem na configuração mostrada, nem nas mensagens de erro.
"""

import argparse
import os
import re
import smtplib
import socket
import ssl
import sys
from datetime import datetime
from pathlib import Path

# Permite rodar o script direto da raiz do projeto, sem instalar nada.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.notificacoes import notificador_da_config  # noqa: E402

ASSUNTO = "Teste de envio - Aprovação de Pedidos"
ENDERECO = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _sem_segredo(texto, senha):
    """Troca a senha por *** em qualquer texto que vá para a tela."""
    texto = str(texto)
    return texto.replace(senha, "***") if senha else texto


def _corpo(notificador):
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    return (
        "Este é um e-mail de TESTE do sistema de Aprovação de Pedidos.\n"
        "Se você o recebeu, o envio de e-mails está funcionando.\n\n"
        f"Enviado em: {agora}\n"
        f"Servidor SMTP: {notificador.host}:{notificador.porta}\n"
        f"Remetente: {notificador.remetente}\n\n"
        "Pode apagar esta mensagem.\n"
    )


def _diagnostico(erro, notificador, destino):
    """Linhas que explicam o erro e dizem o que conferir. Sem a senha."""
    host, porta = notificador.host, notificador.porta
    if isinstance(erro, smtplib.SMTPAuthenticationError):
        return [
            "O servidor recusou o login.",
            "Confira PEDIDOS_SMTP_USUARIO (o login que o provedor mostra, que costuma NÃO ser o seu e-mail)",
            "e PEDIDOS_SMTP_SENHA (a chave SMTP, não a senha da sua conta). Se a chave foi apagada ou",
            "digitada errada, gere uma nova no provedor.",
        ]
    if isinstance(erro, smtplib.SMTPSenderRefused):
        return [
            f"O servidor não aceitou o remetente {notificador.remetente}.",
            "Confira PEDIDOS_SMTP_REMETENTE: o endereço precisa estar cadastrado e verificado no provedor,",
            "e o domínio dele precisa estar autenticado (registros DNS).",
        ]
    if isinstance(erro, smtplib.SMTPRecipientsRefused):
        return [
            f"O servidor não aceitou o destinatário {destino}.",
            "Confira se o endereço está certo e existe.",
        ]
    if isinstance(erro, smtplib.SMTPNotSupportedError):
        return [
            f"O servidor {host}:{porta} não oferece STARTTLS (conexão segura).",
            "Confira PEDIDOS_SMTP_PORTA: o sistema precisa da porta 587 (STARTTLS), não da 465.",
        ]
    if isinstance(erro, smtplib.SMTPServerDisconnected):
        return [
            "A conexão com o servidor foi encerrada no meio do envio.",
            "Tente de novo. Se repetir, confira PEDIDOS_SMTP_HOST e PEDIDOS_SMTP_PORTA.",
        ]
    if isinstance(erro, smtplib.SMTPResponseException):
        return [
            f"O servidor recusou a mensagem (código {erro.smtp_code}).",
            "O texto do servidor vem abaixo; ele costuma dizer o motivo.",
        ]
    if isinstance(erro, smtplib.SMTPException):
        return ["O servidor de e-mail devolveu um erro. O texto dele vem abaixo."]
    if isinstance(erro, ssl.SSLError):
        return [
            "Falhou a conexão segura (TLS) com o servidor.",
            "Confira se o relógio e a data do Windows estão certos e se um antivírus ou proxy não está",
            "interceptando a conexão.",
        ]
    if isinstance(erro, socket.gaierror):
        return [
            f"Não encontrei o servidor {host}.",
            "Confira PEDIDOS_SMTP_HOST (sem http://, só o nome) e a conexão deste PC com a internet.",
        ]
    if isinstance(erro, TimeoutError):
        return [
            f"O servidor {host} não respondeu a tempo na porta {porta}.",
            "Algo no caminho pode estar bloqueando a porta 587 (firewall do PC, do roteador ou do provedor",
            "de internet). Teste em outra rede para confirmar.",
        ]
    if isinstance(erro, ConnectionRefusedError):
        return [
            f"O servidor {host} recusou a conexão na porta {porta}.",
            "Confira PEDIDOS_SMTP_HOST e PEDIDOS_SMTP_PORTA (587).",
        ]
    if isinstance(erro, OSError):
        return [
            f"Erro de rede ao falar com {host}:{porta}.",
            "Confira a conexão deste PC com a internet, PEDIDOS_SMTP_HOST e PEDIDOS_SMTP_PORTA.",
        ]
    return ["Erro inesperado. O texto dele vem abaixo."]


def _mostrar_configuracao(notificador, ambiente):
    senha = "definida" if notificador.senha else "NÃO definida"
    print("Configuração usada:")
    print(f"  Servidor:   {notificador.host}:{notificador.porta}")
    print(f"  Login:      {notificador.usuario or '(nenhum)'}")
    print(f"  Senha/chave: {senha}")
    print(f"  Remetente:  {notificador.remetente}")
    if not notificador.usuario or not notificador.senha:
        print(
            "AVISO: PEDIDOS_SMTP_USUARIO e/ou PEDIDOS_SMTP_SENHA estão vazias. "
            "Servidores da internet exigem login; o envio sem login só funciona em servidores internos."
        )
    modo = (ambiente.get("PEDIDOS_EMAIL_MODE") or "desligado").strip()
    if modo != "smtp":
        print(
            f"AVISO: PEDIDOS_EMAIL_MODE está '{modo}'. Este teste ignora isso, mas o servidor do "
            "sistema só envia e-mails com PEDIDOS_EMAIL_MODE=smtp (e precisa ser reiniciado)."
        )
    print()


def main(argv=None, environ=None):
    analisador = argparse.ArgumentParser(
        description="Envia um e-mail de teste com as variáveis PEDIDOS_SMTP_* do sistema."
    )
    analisador.add_argument("destino", help="endereço que vai receber o e-mail de teste")
    argumentos = analisador.parse_args(argv)
    environ = os.environ if environ is None else environ

    destino = argumentos.destino
    if not ENDERECO.fullmatch(destino):
        print(f"Erro: '{destino.strip()}' não parece um e-mail válido (use nome@dominio.com).", file=sys.stderr)
        return 1

    try:
        notificador = notificador_da_config({"EMAIL_MODE": "smtp"}, environ)
    except RuntimeError as erro:
        print(f"Falta configurar o envio: {erro}", file=sys.stderr)
        print("Veja o passo 4.5 do guia (scripts/instalar_windows.md).", file=sys.stderr)
        return 1

    _mostrar_configuracao(notificador, environ)
    print(f"Enviando um e-mail de teste para {destino} ...")
    try:
        notificador.enviar(destino, ASSUNTO, _corpo(notificador))
    except Exception as erro:  # o objetivo do script é explicar qualquer falha, sem traceback
        print(f"\nO e-mail de teste NÃO foi enviado para {destino}.", file=sys.stderr)
        for linha in _diagnostico(erro, notificador, destino):
            print(f"  {linha}", file=sys.stderr)
        detalhe = _sem_segredo(erro, notificador.senha)
        print(f"\nResposta técnica: {type(erro).__name__}: {detalhe}", file=sys.stderr)
        return 1

    print(f"\nOK: o e-mail de teste foi enviado para {destino}.")
    print("Abra a caixa de entrada (e também a pasta Lixo/Spam) e confirme que a mensagem chegou.")
    print("O servidor aceitar o envio não garante a entrega: só a chegada na caixa confirma.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
