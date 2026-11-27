# Sistema de Aprovação de Pedidos — Especificação de Desenho

Data: 2026-10-23
Status: aguardando revisão do usuário

## 1. Objetivo

Um sistema web interno que cobre o ciclo de um pedido, do registro até a chegada da mercadoria: entrada, aprovação por gestor, confirmação de envio, faturamento (número da nota) e recebimento. Tudo fica rastreável em um só lugar, com histórico de quem fez o quê e quando.

**Restrição fixa:** o sistema nunca lê nem grava no banco Firebird do ATS ERP. Ele tem banco próprio.

**Critério de sucesso:** um pedido percorre o caminho completo (criado pelo solicitante, aprovado por um gestor pelo celular, enviado, faturado e recebido) com o PDF abrindo corretamente em todas as telas e o histórico registrado em cada passo.

## 2. Decisões já tomadas

| Tema | Decisão |
|---|---|
| Linguagem e framework | Python 3 com Flask, páginas geradas no servidor (Jinja2). Sem build de front-end. |
| Banco de dados | SQLite, arquivo único. |
| Servidor | Waitress, rodando em um PC da empresa com Windows 11, ligado o dia todo e com o Tailscale já instalado. |
| Acesso externo | Tailscale Funnel: endereço HTTPS fixo e gratuito, sem domínio, sem app no celular dos usuários. O Funnel é público, então o login do sistema é a única proteção. |
| Identificação | Login para todos. Não existe cadastro aberto; só o administrador cria usuários. |
| Avisos | Dentro do sistema (caixa de avisos) e por e-mail. Teams está fora do escopo. |
| Layout | Pensado primeiro para celular. Paleta do Grupo Dluz (creme, grafite, âmbar; números em fonte monoespaçada). |

## 3. Perfis

| Perfil | Quem | Vê | Pode fazer |
|---|---|---|---|
| **Solicitante** | Time de Pedidos | Só os próprios pedidos | Criar pedido, confirmar envio, reenviar pedido reprovado |
| **Gestor** | 2 pessoas | Todos os pedidos | Aprovar ou reprovar pedido pendente |
| **Fiscal** | Time Fiscal | Todos os pedidos | Informar nota fiscal, marcar chegada, arquivar |
| **Administrador** | Responsável pelo sistema | Todos os pedidos | Gerenciar usuários, aprovar ou reprovar pedido pendente (como o Gestor), arquivar, tudo que o Fiscal faz |

## 4. Fluxo e estados

```
pendente ──► reprovado ──► (solicitante cria pedido novo, ligado ao anterior)
   │
   ▼
aprovado ──► enviado ──► faturado ──► recebido ──► arquivado
```

| De | Para | Ação | Quem | Dados registrados |
|---|---|---|---|---|
| (novo) | pendente | Criar pedido | Solicitante | Cliente, valor, descrição, PDF |
| pendente | aprovado | Aprovar | Gestor ou administrador | Comentário (opcional) |
| pendente | reprovado | Reprovar | Gestor ou administrador | Motivo (**obrigatório**) |
| aprovado | enviado | Confirmar envio | Solicitante dono do pedido | Data do pedido |
| enviado | faturado | Informar nota | Fiscal ou administrador | Número da nota fiscal |
| faturado | recebido | Marcar chegada | Fiscal ou administrador | Data de chegada (padrão: hoje) |
| recebido ou reprovado | arquivado | Arquivar | Fiscal ou administrador | — |

Regras:

- **Primeiro que responder vale.** A decisão do gestor é uma atualização única condicionada a `status = 'pendente'`. Se duas pessoas que decidem (gestores ou administrador) clicarem juntas, a segunda vê "já decidido por [nome] em [data]" e nada é alterado.
- **Reprovado nunca volta atrás.** O solicitante cria um pedido novo, com um campo opcional que aponta o pedido anterior (`reenvio_de`).
- **Não existe edição** dos dados depois do envio. Corrigir um erro em pedido pendente exige reprovar e reenviar.
- **O sistema não confere** o número da nota nem a chegada contra o ERP. O Fiscal faz o registro no ERP por fora e só sinaliza aqui.
- Toda transição grava uma linha no histórico, na mesma transação da mudança de status.

## 5. Modelo de dados (SQLite)

**usuarios**: `id`, `nome`, `email` (único), `senha_hash`, `perfil` (solicitante, gestor, fiscal, admin), `ativo`, `trocar_senha` (obrigar troca no primeiro acesso), `falhas_login`, `bloqueado_ate`, `criado_em`.

**pedidos**: `id` (autonumeração), `cliente`, `valor_centavos` (inteiro), `descricao`, `pdf_arquivo` (nome gerado), `pdf_nome_original`, `status`, `solicitante_id`, `reenvio_de` (id de pedido, opcional), `data_pedido`, `numero_nf`, `data_chegada`, `criado_em`, `atualizado_em`.

O título exibido é calculado como `Novo Pedido - {id}`. Assim o número é sequencial, único e nunca precisa de uma segunda gravação.

**historico**: `id`, `pedido_id`, `usuario_id`, `acao`, `status_anterior`, `status_novo`, `comentario`, `criado_em`. Só recebe inserções, nunca alterações.

**avisos**: `id`, `usuario_id` (destinatário), `pedido_id`, `assunto`, `corpo`, `lido`, `email_status` (pendente, enviado, erro, desligado), `criado_em`.

O valor é guardado em centavos inteiros para evitar erro de arredondamento. Datas são guardadas em ISO e exibidas no formato brasileiro.

## 6. O anexo em PDF

Esta é a parte que falhou no Power Automate, então tem regras próprias:

1. O arquivo é gravado com **nome gerado** (`uuid4.pdf`). O nome original só é guardado como texto para exibição, sempre escapado. Isso elimina nome inválido, acentos, barras e nomes longos.
2. Validação no envio: extensão `.pdf`, primeiros bytes `%PDF-` e tamanho máximo configurável (padrão 20 MB). Arquivo recusado mostra mensagem clara.
3. Os arquivos ficam em pasta **fora** da área pública. O acesso é só pela rota `/pedidos/<id>/pdf`, que exige login e confere o perfil (solicitante só o próprio pedido).
4. O link do PDF é sempre o do próprio sistema, curto e sem limite de 255 caracteres. Se o arquivo sumir do disco, a tela mostra um aviso em vez de erro.

## 7. Autenticação e segurança

- Senhas guardadas com hash forte (`werkzeug.security`), mínimo de 10 caracteres.
- O administrador cria o usuário com senha provisória; no primeiro acesso a troca é obrigatória.
- Bloqueio de 15 minutos após 5 tentativas erradas; mensagem de erro sempre genérica.
- Sessão por cookie `HttpOnly`, `Secure` e `SameSite=Lax`, expirando em 8 horas.
- Proteção CSRF em todos os formulários (Flask-WTF).
- Cabeçalhos de segurança básicos (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy`).
- A chave secreta da aplicação vem de arquivo ou variável de ambiente, nunca do código. Credenciais de e-mail seguem a mesma regra.
- O Flask fica atrás do Funnel, então usa `ProxyFix` para reconhecer HTTPS e o IP real.
- Autorização é conferida **no servidor** em cada rota. Esconder botões não conta como proteção.

## 8. Avisos

Cada transição gera avisos para os destinatários certos:

| Evento | Destinatários |
|---|---|
| Pedido criado | Solicitante (confirmação); os 2 gestores (para decidir) |
| Aprovado | Solicitante (confirmar envio); Fiscal (dados e PDF) |
| Reprovado | Solicitante (com o motivo) |
| Enviado | Fiscal (aguardar faturamento) |
| Faturado | Solicitante |
| Recebido | Solicitante |

- O aviso é sempre gravado na tabela `avisos` e aparece na caixa de avisos do sistema.
- O e-mail passa por uma interface de envio (`Notificador`) com duas implementações: uma que apenas registra (primeira versão) e uma SMTP, ligada por configuração. Falha no e-mail **nunca** bloqueia uma transição; fica marcada como `erro` e pode ser reenviada.
- O e-mail traz um botão "Abrir pedido" com o endereço público configurado em `BASE_URL`.
- O envio pelo Microsoft 365 depende da situação do tenant (o envio SMTP básico vem sendo restringido) e será resolvido na fase de e-mail.

## 9. Telas

1. **Login** e **troca de senha**.
2. **Lista de pedidos**, com filtros por status e atalhos por perfil: "Meus pedidos", "A decidir" (gestor), "Aguardando nota" e "Aguardando chegada" (fiscal). O número de avisos não lidos aparece no topo.
3. **Novo pedido**: cliente, valor, descrição e PDF; opção de indicar o pedido anterior em caso de reenvio.
4. **Detalhe do pedido**: dados, botão para abrir o PDF, histórico completo e os botões que o perfil atual pode usar naquela etapa.
5. **Avisos**.
6. **Administração de usuários** (somente administrador): criar, ativar e desativar, redefinir senha.

## 10. Estrutura do projeto

```
Aprovacao de Pedidos/
  app/
    __init__.py        # fábrica da aplicação e configuração
    config.py
    db.py              # conexão e transações
    schema.sql
    auth.py            # login, sessão, decoradores de perfil
    workflow.py        # estados, transições, permissões (sem Flask)
    pedidos.py         # rotas de pedidos
    arquivos.py        # validação e gravação de PDF
    notificacoes.py    # avisos e Notificador
    admin.py           # rotas de usuários
    templates/
    static/
  scripts/
    criar_admin.py
    backup.py
  tests/
  data/                # banco e PDFs (fora do controle de versão)
  requirements.txt
  docs/
```

`workflow.py` não depende do Flask: recebe estado atual, perfil e ação e devolve o novo estado ou um erro. Isso permite testar todas as regras de transição isoladamente.

## 11. Operação

- Instalação: `pip install -r requirements.txt`, `scripts/criar_admin.py`, depois iniciar com Waitress.
- Publicação: `tailscale funnel` apontando para a porta do programa (a sintaxe exata será conferida na hora da instalação).
- Início automático: tarefa do Agendador de Tarefas do Windows 11, disparada na inicialização do PC, que roda o Waitress. O desenvolvimento acontece num Mac, então o código usa `pathlib` e nada específico de um sistema operacional. O `requirements.txt` inclui `tzdata`, porque o Windows não traz o banco de fusos horários que o Python usa. Horários são guardados em UTC e exibidos em `America/Sao_Paulo`.
- **Backup diário** pelo `scripts/backup.py`: cópia consistente do banco (API de backup do SQLite) e da pasta de PDFs para outra pasta ou disco. Sem backup, uma falha do PC perde o histórico.

## 12. Testes

Com `pytest`, o desenvolvimento é guiado por testes:

- **Unidade (`workflow.py`):** todas as transições válidas; transições inválidas e perfis não autorizados são recusados.
- **Integração (cliente de teste do Flask):** o fluxo completo de ponta a ponta; reprovação com motivo obrigatório; reenvio ligado ao pedido anterior.
- **Concorrência:** dois gestores decidindo o mesmo pedido, com só o primeiro valendo.
- **PDF:** arquivo que não é PDF, acima do limite, nome com acentos, barras e mais de 255 caracteres; arquivo ausente no disco.
- **Segurança:** bloqueio após erros de senha, rotas protegidas sem login, solicitante tentando abrir pedido ou PDF de outro, CSRF ausente.

## 13. Fora do escopo desta versão

Integração com o Firebird; Teams; edição de pedido depois de enviado; relatórios e exportação; lembretes automáticos de pedido parado; mais de uma empresa; recuperação de senha por e-mail (o administrador redefine).

## 14. Pendências que não bloqueiam a construção

- Servidor e conta de e-mail para os avisos.
- Nome do endereço do Funnel, definido no momento da instalação.
