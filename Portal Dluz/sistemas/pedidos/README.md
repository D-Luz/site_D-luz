# Aprovação de Pedidos

Sistema web interno do Grupo Dluz para acompanhar um pedido do registro até a chegada da mercadoria, com histórico de quem fez o quê e quando. Feito em Python (Flask), com banco SQLite próprio: **nunca lê nem grava no banco Firebird do ATS ERP**.

## O fluxo, em um parágrafo

O **solicitante** registra o pedido (cliente, valor, descrição e o PDF). O **gestor** (ou o **administrador**) o aprova ou reprova (a reprovação exige um motivo, e o solicitante pode reenviar). Aprovado, o solicitante **confirma o envio**; o **fiscal** informa a **nota fiscal** e, quando a mercadoria chega, registra a **chegada**, e o pedido fica concluído (depois pode ser arquivado). Cada passo gera avisos dentro do sistema (e, se ligado, por e-mail) e fica registrado no histórico. Só o **administrador** cria usuários. O sistema roda em um PC com Windows 11 e é publicado na internet pelo Tailscale Funnel; o login é a única proteção.

## Instalar no Windows 11

Passo a passo completo, em português: [`scripts/instalar_windows.md`](scripts/instalar_windows.md).

## Testes

```bash
python -m pytest
```

(Num ambiente virtual com `pip install -r requirements.txt`, que fixa a versão exata de todos os pacotes.) Os testes usam pastas temporárias e não tocam nos dados reais. No Windows, os poucos testes que dependem de permissões POSIX aparecem como `skipped`.

## Rodar localmente, para desenvolver

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt            # no Windows: .venv\Scripts\pip
export PEDIDOS_DATA_DIR=/tmp/pedidos-demo            # pasta descartável
.venv/bin/python scripts/dados_demo.py               # usuários e pedidos de mentira
PEDIDOS_COOKIE_INSECURE=1 .venv/bin/python scripts/iniciar.py
```

`scripts/iniciar.py`, `scripts/criar_admin.py` e `scripts/backup.py` exigem `PEDIDOS_DATA_DIR` (por isso o `export` acima): sem ela, recusam-se a rodar. Abra <http://127.0.0.1:8000>. Os usuários de demonstração (`admin@demo.local`, `pedidos@demo.local`, `gestor1@demo.local`, `fiscal@demo.local`...) e a senha estão em `scripts/dados_demo.py`.

> **Só para desenvolvimento.** `scripts/dados_demo.py` cria usuários com senha pública, e `PEDIDOS_COOKIE_INSECURE=1` permite login sem HTTPS. Nunca use nenhum dos dois em produção. `dados_demo.py` se recusa a rodar quando `PEDIDOS_BASE_URL` começa com `https://` ou `PEDIDOS_EMAIL_MODE` é `smtp` (salvo com `--aceito-risco-de-producao`) e **não deve ser copiado para o PC do sistema**. Em produção, o primeiro administrador é criado com `scripts/criar_admin.py`, que mostra a pasta de dados antes de perguntar qualquer coisa.

Senhas: o administrador cria cada usuário com uma senha provisória, trocada no primeiro acesso; depois, cada um troca a própria senha pelo item **Trocar senha** do menu.

## Estrutura

```
app/                   o sistema (Flask)
  __init__.py          fábrica da aplicação, cookies, CSRF, cabeçalhos de segurança
  config.py            configuração e variáveis de ambiente PEDIDOS_*
  db.py, schema.sql    banco SQLite e transações
  auth.py              login, sessão, perfis
  workflow.py          estados e transições (sem Flask)
  servico.py           regras dos pedidos
  pedidos.py           rotas de pedidos e avisos
  admin.py             rotas de usuários
  arquivos.py          validação e gravação do PDF
  notificacoes.py      avisos e envio de e-mail
  templates/, static/  telas e estilo
scripts/
  iniciar.py           servidor de produção (Waitress, só em 127.0.0.1)
  criar_admin.py       cria administrador (--redefinir: nova senha)
  backup.py            backup do banco e dos PDFs
  testar_email.py      manda um e-mail de teste com as variáveis PEDIDOS_SMTP_*
  dados_demo.py        dados de demonstração (só para desenvolvimento)
  iniciar_pedidos.bat, backup_pedidos.bat   usados pelo Agendador de Tarefas
  instalar_windows.md  guia de instalação
tests/                 testes (pytest)
docs/                  especificação e plano
```

## Onde ficam os dados

Tudo fica na pasta de dados, **fora** do código: `PEDIDOS_DATA_DIR` (no Windows: `C:\PedidosDados`). Os scripts de operação exigem a variável; só a aplicação criada direto pelo código, como nos testes, cai na pasta `data/` do projeto se ela não existir:

- `pedidos.db`: o banco;
- `pdfs/`: os PDFs, com nome gerado;
- `secret.key`: chave da sessão, criada na primeira execução (não entra no backup);
- `logs/pedidos.log`: o registro do servidor.

Os backups (`scripts/backup.py`) vão para outra pasta ou disco.

## Configuração

Variáveis de ambiente `PEDIDOS_*`: as de [`app/config.py`](app/config.py) (`DATA_DIR`, `BASE_URL`, `EMAIL_MODE`, `MAX_PDF_MB`, `MIN_SENHA`...), as de e-mail `PEDIDOS_SMTP_*`, `PEDIDOS_PORTA` (porta do servidor, padrão 8000) e `PEDIDOS_BACKUP_DESTINO` (usada por `backup_pedidos.bat`).
