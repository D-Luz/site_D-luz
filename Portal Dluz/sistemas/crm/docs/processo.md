# Processo de CRM — Grupo Dluz

Documento de operação. O sistema (pasta `CRM Dluz`) é a ferramenta que sustenta este processo; o processo vale mesmo antes de o sistema estar no ar.

## O que os dados do ERP mostraram (Dluz Elétrica, últimos 12 meses)

| Fato | Número | O que significa |
|---|---|---|
| Vendas para «consumidor» (cliente 00000000) | 10.115 de 15.149 (67% das vendas) | Dois terços das vendas não têm ninguém para contatar. É o maior gargalo. |
| Valor das vendas com cliente identificado (30 dias) | ~69% do R$ | Pelo valor a situação é melhor: as compras grandes costumam ter cadastro. |
| Orçamentos abertos com cliente contatável | ~17% do valor (Elétrica + Iluminação) | Os orçamentos grandes são justamente os anônimos. Sem cliente, não há retorno. |
| Eletricistas no segmento do ERP | ~18 com compra no ano | Compram só ~R$ 28 mil no próprio nome: o volume deles passa por vendas no nome do cliente ou como «consumidor». |
| Recurso nativo de profissional do ATS | `PROFISSIONAL` (14 cadastros), `PARAMDAV.USAPROFISSIONAL = 0`, `COMPPEDIDOC.CODPROF` vazio | O ERP já sabe vincular eletricista à venda, mas está desligado. |

Conclusão: **o CRM só funciona se a loja passar a saber quem comprou e quem indicou.** Por isso o primeiro passo do processo não é mensagem nem campanha: é identificação no balcão.

## Os 5 passos

### 1. Identificar (a base de tudo)

No balcão, em toda venda:
- **Perguntar o WhatsApp** do cliente e cadastrar. Frase sugerida: «Posso te avisar pelo WhatsApp quando seu pedido/orçamento estiver pronto?».
- **Perguntar «é para qual eletricista?»** e anotar. No fim do dia, alguém lança na tela **Vendas** do CRM (leva minutos).
- **Orçamento sempre com cliente e telefone.** Orçamento no «consumidor» não pode ser cobrado depois.

Metas do painel: contatável ≥ 40% do valor nas primeiras 4 semanas, depois 60%. Mostrar o placar por vendedor semanalmente.

Em paralelo, **pedir ao suporte da ATS para ligar o recurso nativo de profissional**, nas três empresas (hoje desligado em todas). Os parâmetros que encontrei: `PARAPDV.MOSTRAPROFISSIONAL` (campo no PDV), `PARAMDAV.USAPROFISSIONAL` e `PARAMDAVVERTICAL.USAPROFISSIONAL` (campo nos orçamentos/DAVs) e, opcionalmente, `PARAMDAV.OBRIGARPROFISSIONAL` (obriga a escolher). O vendedor escolhe o eletricista na própria venda e o ATS grava em `COMPPEDIDOC.CODPROF`. O CRM já lê esse campo a cada sincronização e atribui a venda sozinho (origem «indicado no ERP»), sem digitação dupla; a tela **Vendas** vira só correção. Para isso, os eletricistas precisam estar no cadastro `PROFISSIONAL` do ATS (hoje: 14 na Elétrica, nenhum na Iluminação) e importados no CRM. Não altere esses parâmetros sem a ATS.

O ATS também tem campos de comissão e bônus por venda (`COMISSAOPROF`, `PONTOBONUS`, `VALORBONUS`) e comissão por produto (`PRODUTODETALHE.COMISSAOPROFISSIONAL`, hoje tudo zerado). Não sei como o ATS calcula ou paga isso; vale perguntar à ATS se o programa de pontos pode ser feito nele em vez de no CRM.

### 2. Segmentar

- **Eletricista:** nível A / B / C pelas compras em 12 meses atribuídas a ele (limites em Configurações; hoje A ≥ R$ 8.000 e B ≥ R$ 2.500; subir quando as atribuições crescerem).
- **Cliente final:** novo, recorrente ou parado (60 / 120 / 180 dias).
- **Orçamento:** aberto, provavelmente baixado (mesmo vendedor e valor de uma venda posterior) ou cópia.

### 3. Contatar (a fila «Hoje»)

Uma pessoa abre o CRM de manhã e trabalha a lista, em ordem de prioridade:

| Tarefa | Quando aparece | Ação |
|---|---|---|
| Eletricista em risco | Passou de 2× o intervalo habitual entre compras (mínimo 21 dias) | Ligar/WhatsApp: entender o motivo, sem pressionar. |
| Orçamento | Dias 1, 3 e 7 depois da data | Perguntar se conseguiu avaliar; oferecer ajuste. |
| Acompanhamento A | Eletricista A sem contato há 14 dias | Perguntar das obras; oferecer separar lista. |
| Extrato de pontos | Primeira semana do mês, saldo ≥ R$ 20 | Informar o saldo e lembrar de usar. |
| Cliente parado | 60 a 365 dias sem comprar, com histórico relevante | Reativar; limitado a 15 por dia. |
| Pós-venda | 7 a 14 dias após compra de R$ 100+ | Perguntar se deu tudo certo. |
| Aniversário | No dia | Parabéns. |

As mensagens são modelos editáveis; o botão abre o WhatsApp com o texto pronto e **quem envia é a pessoa**. Depois, registrar o resultado (conversou, sem resposta, sem interesse, número errado): a tarefa sai da lista e a cadência continua sozinha.

### 4. Reter o eletricista (contra a comissão do concorrente)

Não competir em comissão escondida. Ganhar em quatro frentes:

1. **Programa de pontos simples e transparente** — 1% da compra (teto definido por você) em pontos que valem R$ 1, extrato mensal, resgate registrado no balcão. É um programa pequeno de propósito: ele não paga o que o concorrente paga, então precisa vir acompanhado das frentes abaixo.
2. **O que dinheiro não compra no concorrente:** material separado para a obra antes de ele chegar, estoque garantido (o motivo pelo qual os clientes vêm à Dluz), atendimento prioritário, entrega, suporte técnico, treinamentos/lançamentos.
3. **Relacionamento direto com o cliente final:** cadastrar o consumidor na primeira compra (passo 1) para que o vínculo não dependa só do eletricista. O eletricista continua sendo premiado pela indicação; a loja passa a conhecer quem compra.
4. **Transparência para o consumidor final**, nas redes sociais e no balcão: orientar a pedir o orçamento e a nota fiscal em seu nome e conferir o preço direto na loja. Quem repassa comissão escondida perde força quando o cliente passa a comparar. Tom: informativo, sem acusar ninguém.

### 5. Medir (aba «Painel»)

Semanalmente, na mesma reunião:
1. % do valor vendido com cliente contatável (passo 1).
2. % do valor ligado a um eletricista.
3. Eletricistas que compraram nos últimos 90 dias / total.
4. Orçamentos em aberto e % contatável.
5. Contatos feitos na semana e % das conversas seguidas de compra em até 14 dias. Esse é o número que diz se o esforço vale a pena.

## Rotina

| Quando | Quem | O quê |
|---|---|---|
| Todo atendimento | Balcão | Pedir WhatsApp; perguntar o eletricista; orçamento com cliente. |
| Fim do dia (10 min) | Balcão/gestor | Tela **Vendas**: ligar as vendas do dia aos eletricistas. |
| Manhã (30–60 min) | Responsável pelo CRM | Trabalhar a fila **Hoje** e registrar resultados. |
| Início do mês | Gestor | Revisar níveis e resgates; extrato de pontos sai na fila. |
| Semanal | Gestor | Ler o **Painel** e cobrar o passo 1. |

## Decisões ainda em aberto

- **Quem é o responsável pela fila diária?** (não definido)
- **Teto da recompensa:** usado 1% (sua resposta). Revisar depois de 2–3 meses com a margem real por categoria.
- **Devoluções:** o CRM remove pontos de vendas que somem do ERP (canceladas); devolução parcial deve ser lançada como ajuste manual por um administrador.
- **Orçamentos baixados:** desde 15/09/2026 o ERP não liga o orçamento (DAV) ao pedido que o baixou. O CRM usa «mesmo vendedor + mesmo valor» como aproximação; um orçamento baixado com valor alterado ainda aparece como aberto.
- **LGPD:** o contato é feito por WhatsApp a partir de cadastros de clientes que já compram na loja; para campanhas em massa, colher consentimento no cadastro.
