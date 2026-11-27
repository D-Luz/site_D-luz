# Portal Dluz

Uma interface web única para todos os programas do Grupo Dluz: um login, um endereço, um menu.

- **Sistemas:** Aprovação de Pedidos (`/pedidos`), CRM Dluz (`/crm`) e Conferência de Caixa (`/caixa`). Cada um continua sendo o sistema original, com o próprio banco e os próprios testes, só que montado dentro do portal e com o login dele.
- **Ferramentas:** os scripts soltos (extrações, relatórios, análises, extratos, NF-e, orçamentos) viraram formulários. Cada execução roda em segundo plano e gera os arquivos (Excel, PDF, HTML) e uma tabela interativa com filtros e totais, que substitui as janelas antigas. Tudo fica em "Meus relatórios" por 30 dias.
- **Painel:** o Painel ATS fica guardado no portal para todos. Antes ele era publicado no Netlify.

Instalação no Windows: [`INSTALAR_WINDOWS.md`](INSTALAR_WINDOWS.md).

## As ferramentas e de onde vieram

| Grupo | Ferramenta | Programa original |
|---|---|---|
| Vendas | Orçamentos baixados e não baixados | `orcamentos_nao_baixados.py` |
| Vendas | Orçamentos por vendedor | `relatorio_orcamentos_vendedor.py` |
| Vendas | Imprimir orçamento | `Orcamento_imp NOVO.py` (+ `dluz_assets.py`) |
| Vendas | Venda mensal por empresa | `venda_mensal_por_empresa.py` |
| Vendas | Vendas item a item (PDV) | `Extracoes/relatorio_vendas.py` |
| Vendas | Somatório do PDV | `Extracoes/Extracao somatorio/Extracao_somatorio_v10.py` |
| Vendas | Planilha-modelo de orçamento | `Orcamentos/Planilha_orcamento.py` |
| Financeiro | Contas a pagar / a receber | `Extracoes/Extracao_titulos_gui.py` (substitui `Extracao_cpagar.py` e `Extracao_creceber.py`) |
| Financeiro | Contas a receber em aberto | `Extracoes/extracao_contas_receber.py` |
| Financeiro | Acompanhamento semanal | `Extracoes/acompanhamento_semanal_ats.py` |
| Financeiro | Receita mensal comparativa | `Relatorio via site/relatorio_mensal_comparativo.py` |
| Estoque | Análise de estoque ABC × XYZ | `Analise Estoque - compras/ats analise estoque_V5.py` |
| Estoque | Posição de estoque | `Extracoes/Extracao_posicaoestoque.py` |
| Estoque | Última NF de compra (Real / Fiscal) | `Extracoes/Extracao UEPS/…_v4.py`, `…_fiscal.py` |
| Estoque | Validar custo da NF (PRECONF) | `Analise Estoque - compras/validar_preconf.py` |
| Bancos e fiscal | Extrator de Pix — Inter | `extrator_pix_inter.py` |
| Bancos e fiscal | Consolidar extratos | `Consolidar extratos/consolidar_extratos.py` |
| Bancos e fiscal | NF-e (XML) para Excel | `Gerar excel nf/gerar_tabela_nf.py` |
| Painéis | Painel ATS | `Relatorio via site/gerar_dashboard_ats-11.py` |
| Vendas | Proposta de orçamento (PDF) | `Orcamentos/Orcamento_Final_v7.py` (Painel de Orçamentos) |

Quando havia várias versões de um script, entrou só a mais nova: v10 do somatório, V5 da análise de estoque, v4 da última NF, -11 do painel e v7 do orçamento. As versões antigas e o `Analise_receber_online*` ficaram de fora porque a versão nova faz a mesma coisa.

## Como funciona por dentro

```
portal/                 o portal (Flask)
  auth.py               login único, sessão, CSRF, usuários
  integracao.py         monta Pedidos, CRM e Caixa em /pedidos, /crm, /caixa e passa o usuário logado
  tarefas.py            fila das ferramentas (threads), registro ao vivo e arquivos gerados
  carregador.py         carrega os scripts originais como módulos, uma cópia por execução
  erp.py                conexões do ATS num lugar só (tela Configurações)
  ferramentas/          um arquivo por grupo, e cada ferramenta chama o script original
scripts_originais/      cópia dos scripts (o portal chama as funções deles)
sistemas/               cópia de Pedidos, CRM e Caixa, com app/portal.py (login pelo portal)
scripts/                iniciar, criar_admin, backup, .bat do Windows e demo
tests/                  testes do portal e um "Firebird de mentira" para rodar sem o ATS
```

- **Login único:** o portal confere a sessão e coloca o usuário numa chave interna da requisição (`environ["dluz.portal.usuario"]`), que só o próprio servidor consegue gravar. Cada sistema lê essa chave em `app/portal.py`. Fora do portal, os sistemas continuam com o login próprio de antes.
- **Bancos do ATS:** host, banco Real e banco Fiscal de cada empresa são editados em Configurações. O portal troca nos scripts os valores que estavam escritos no código. A senha do Firebird vem de `DLUZ_FB_PASSWORD`.
- **Páginas geradas:** painéis e tabelas abrem num quadro isolado (CSP `sandbox`), sem acesso à sessão do portal.

## Desenvolver

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest                # portal (47 testes)
(cd sistemas/pedidos && ../../.venv/bin/python -m pytest -q)   # 1244 testes
(cd sistemas/crm && ../../.venv/bin/python -m pytest -q)       # 29
(cd sistemas/caixa && ../../.venv/bin/python -m pytest -q)     # 37
.venv/bin/python scripts/demo.py          # http://127.0.0.1:8765, usuário demo / demo12345, dados de mentira
```

Para criar uma ferramenta nova, escreva uma função com `@ferramenta(...)` num arquivo de `portal/ferramentas/`, descrevendo os campos do formulário. Ela recebe `ctx` (parâmetros, pasta de saída, `ctx.script("arquivo.py")` para carregar um script original já com os bancos configurados). Depois inclua um caso em `tests/test_ferramentas.py`.
