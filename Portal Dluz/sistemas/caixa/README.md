# Conferência de Caixa

Sistema web interno do Grupo Dluz (Iluminação e Elétrica) que substitui a planilha diária de caixa. A caixa lança os recebimentos e as saídas como no Excel; o sistema cruza com o ATS na hora, aponta o que não bate, guarda quem lançou o quê e quando, e mantém o histórico de todos os dias. Python (Flask) com banco SQLite próprio. **Só lê o ATS (Firebird); nunca grava nele.**

## Como funciona

1. **Abrir o caixa** do dia, com o saldo inicial da gaveta (sugerido: o contado no último fechamento).
2. **Lançar** cada recebimento e cada saída: forma + valor (+ observação). Só teclado: valor e `Enter`; `Shift+Tab` e o número da forma para trocar (1 dinheiro, 2 cartão, 3 depósito/PIX, 4 cheque, 5 dev. crédito, 6 troca de pontos, 7 retirada p/ cofre, 8 pagamento, 9 outra saída). Cada linha grava data, hora e usuário; editar e excluir ficam no registro de alterações, que não pode ser apagado.
3. **Conferência ao vivo:** para cada forma, lançado × ATS × diferença, e ao lado de cada valor o PDV que tem esse valor (só sugestão).
4. **Encerrar o dia:** o ATS é lido de novo e cada lançamento é cruzado PDV a PDV. Aparecem os lançamentos sem PDV, os PDVs sem lançamento e os valores diferentes. Dá para ligar um lançamento a um PDV à mão (ex.: PDV que teve o valor alterado depois). A caixa informa o dinheiro contado; com divergência, a justificativa é obrigatória. O dia trava e a folha sai para impressão.
5. **Financeiro:** aprova (com observação) os lançamentos de dev. crédito e troca de pontos, marca o dia como conferido (observação obrigatória se houve divergência) ou reabre para correção.
6. **Histórico:** todos os dias por empresa e mês, com situação, diferença da gaveta, divergências, quem fechou e quem conferiu.

### Onde o ATS guarda cada forma (conferido em out/2026)

| Forma | ATS |
|---|---|
| Dinheiro | `ENCEFAT.VLRDINHEIRO` |
| Cartão | `ENCEFAT.VLRCONVENIO` (o ATS registra cartão como convênio) |
| Depósito/PIX | `PAGAMENTODIGITAL` (carteira digital, `STATUS = 2`), em uso desde ~21/09/2026 |
| Cheque | `ENCEFAT.VLRCHEQUE + VLRCHPREDATADO` |
| A prazo | `ENCEFAT.VLRRECEBER`: fora do caixa, só informado |

Vendas = `PEDIDOC` tipo 55, `FATURADO = 'S'`, pela `DATAPEDIDO`. Se as formas de um PDV não somam o total da venda, a sobra aparece como "Outra forma no ATS", para ninguém deixar passar. `python scripts/verificar_ats.py ILU 2026-10-23` mostra os totais de um dia e confere isso.

## Papéis

| Papel | Pode |
|---|---|
| Caixa | abrir, lançar, editar, encerrar o dia |
| Financeiro | o mesmo, e aprovar dev. crédito/pontos, conferir e reabrir |
| Administrador | tudo, e cadastrar usuários |

Um usuário pode ficar preso a uma empresa (a caixa da Iluminação só vê a Iluminação).

## Instalar no PC da loja (Windows)

Passo a passo: [`scripts/instalar_windows.md`](scripts/instalar_windows.md). O sistema roda só no servidor; nas outras máquinas vai apenas um atalho, criado por [`atalho/criar_atalho.bat`](atalho/criar_atalho.bat), que abre o sistema numa janela própria do Edge.

## Rodar para desenvolver

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export CAIXA_DATA_DIR=/tmp/caixa-demo CAIXA_COOKIE_INSECURE=1
.venv/bin/python scripts/dados_demo.py        # usuários admin, ana, financeiro (senha demo12345)
.venv/bin/python scripts/iniciar.py 8200      # http://127.0.0.1:8200
```

Sem acesso ao ATS, `CAIXA_ATS_DEMO=1` usa vendas inventadas. `dados_demo.py` cria senhas públicas: **não copie para o PC da loja**.

## Testes

```bash
.venv/bin/python -m pytest
```

## Estrutura

```
app/erp.py           leitura do ATS (só SELECT), cache de 60 s
app/conferencia.py   regras: cruzamento PDV a PDV, totais por forma, gaveta (funções puras)
app/caixa.py         operações no banco + registro de alterações
app/rotas.py         telas;  app/auth.py  login, CSRF, papéis, usuários
app/schema.sql       banco (valores em centavos; histórico protegido contra alteração)
scripts/             iniciar, criar_usuario, backup, verificar_ats, dados_demo, .bat do Windows
```

## Limitações conhecidas

- O dia de um PDV é a `DATAPEDIDO` (igual à data do movimento no `ENCEFAT`). Em out/2026 houve 9 pedidos por empresa com `DATAFATURA` posterior; eles contam no dia do pedido.
- Pagamento misto (ex.: parte dinheiro, parte cartão) vira uma linha por forma no ATS; a caixa lança cada parte separada.
- Dev. crédito e troca de pontos não são conferidos contra o ATS: dependem da aprovação do financeiro.
- O fechamento exige o ATS respondendo; a tela de lançamentos funciona mesmo com o ATS fora do ar.
