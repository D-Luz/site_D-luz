# CRM Dluz

Painel web interno do Grupo Dluz (Elétrica e Iluminação) para relacionamento com eletricistas e clientes. Python (Flask) com banco SQLite próprio. **Só lê o ERP ATS (Firebird); nunca grava nele.** O processo que o sistema sustenta está em [`docs/processo.md`](docs/processo.md).

## O que faz

- **Hoje:** fila diária de contatos (retorno de orçamento, eletricista em risco, acompanhamento, extrato de pontos, clientes parados, pós-venda, aniversários), com botão que abre o WhatsApp com a mensagem pronta e registro do resultado.
- **Vendas:** ligar cada venda do dia ao eletricista que a trouxe (a venda fica no ERP; o vínculo fica no CRM).
- **Eletricistas:** cadastro, nível A/B/C, vínculo com os cadastros dele nas duas lojas, pontos (padrão 1% da compra), extrato e resgate.
- **Orçamentos:** DAVs em aberto, sem baixados nem cópias, e quais ainda não têm cliente contatável.
- **Painel:** % da venda com cliente contatável, % ligada a eletricista, eletricistas ativos, orçamentos e retorno dos contatos.
- **Configurações** (administrador): percentual de pontos, níveis, limites da fila e textos das mensagens.

## Rodar

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export CRM_DATA_DIR=~/crm-dados                       # pasta do banco (obrigatória)
export CRM_COOKIE_INSECURE=1                          # só se não houver HTTPS (rede local)
.venv/bin/python scripts/criar_usuario.py --admin     # primeiro administrador
.venv/bin/python scripts/sincronizar.py               # lê o ERP (ELE e ILU); agende de hora em hora
.venv/bin/python scripts/iniciar.py 8100              # http://127.0.0.1:8100
```

Para acesso pela rede da loja: `CRM_HOST=0.0.0.0`. Usuários do balcão são criados com `criar_usuario.py` (sem `--admin`); só administradores veem Configurações, sincronizam e lançam ajustes de pontos.

Para ver o sistema com dados de mentira (sem tocar no ERP): `CRM_DATA_DIR=/tmp/crm-demo .venv/bin/python scripts/dados_demo.py` (usuários `admin` e `balcao`, senha `demo12345`; só para desenvolvimento).

## Testes

```bash
.venv/bin/python -m pytest
```

## Estrutura

```
app/erp.py           leitura do ERP (só SELECT)
app/sincronizar.py   copia ERP → SQLite; classifica orçamentos (baixado/cópia)
app/regras.py        fila do dia, níveis, métricas do painel
app/pontos.py        atribuição de vendas e livro de pontos
app/rotas.py         telas;  app/auth.py  login, CSRF
scripts/             iniciar, sincronizar, criar_usuario, dados_demo
```

## Limitações conhecidas

- Orçamento «baixado» é uma aproximação (mesmo vendedor e valor de uma venda posterior), porque o ERP não liga o DAV ao pedido.
- O CRM não envia mensagens sozinho: abre o WhatsApp e a pessoa envia.
- O resgate de pontos é um registro; o desconto/crédito é dado no ERP à mão.
- Devolução parcial não é descontada dos pontos automaticamente (ajuste manual).
- As credenciais do Firebird seguem o padrão dos outros scripts do grupo (variáveis `CRM_FB_USER` / `CRM_FB_PASSWORD` sobrescrevem).
