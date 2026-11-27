# Sistema de Aprovação de Pedidos — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir o sistema web interno de aprovação de pedidos (entrada, aprovação, envio, faturamento, recebimento, arquivamento) com login para todos, banco SQLite próprio e avisos.

**Architecture:** Aplicação Flask com páginas geradas no servidor. As regras de estado ficam em `workflow.py`, puro e sem Flask. Operações de negócio ficam em `servico.py`, que grava pedido, histórico e avisos na mesma transação. As rotas são finas e só chamam o serviço.

**Tech Stack:** Python 3, Flask, Flask-WTF (CSRF), Werkzeug (hash de senha), SQLite (`sqlite3`), Waitress, pytest, `tzdata`.

**Spec:** `docs/superpowers/specs/2026-10-23-aprovacao-pedidos-design.md`

## Global Constraints

- Nunca ler nem gravar no Firebird. O único banco é o SQLite de `data/`.
- Perfis: `solicitante`, `gestor`, `fiscal`, `admin`. Estados: `pendente`, `aprovado`, `reprovado`, `enviado`, `faturado`, `recebido`, `arquivado`.
- Título exibido: `Novo Pedido - {id}`, calculado a partir do `id` autoincrementado.
- Valor guardado em centavos inteiros. Horários guardados em UTC (ISO) e exibidos em `America/Sao_Paulo`; datas exibidas no formato `dd/mm/aaaa`.
- PDF: nome gravado `uuid4.pdf`; extensão `.pdf`; primeiros bytes `%PDF-`; limite padrão 20 MB (`MAX_PDF_MB`); pasta fora da área pública; acesso só por `/pedidos/<id>/pdf` com login e permissão.
- Senha mínimo 10 caracteres; bloqueio de 15 minutos após 5 falhas; mensagem de erro de login sempre genérica; sessão de 8 horas com cookie `HttpOnly`, `Secure`, `SameSite=Lax`; CSRF em todo POST.
- Chave secreta e credenciais de e-mail vêm de arquivo ou variável de ambiente, nunca do código.
- Autorização conferida no servidor em cada rota.
- Código sem nada específico de sistema operacional (`pathlib`); roda no Mac (desenvolvimento) e no Windows 11 (produção).
- Commits terminam com a linha `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Valor em formato brasileiro:** `"1.234,56"` vira 123456 centavos; `""`, `"0"`, `"-5"`, `"abc"` e `"12,345"` são recusados com mensagem clara. Teste na Task 7.
2. **Texto com HTML ou script** em cliente, descrição, comentário e nome do PDF aparece escapado em todas as telas e nunca é executado. Teste na Task 8.
3. **Usuário desativado com sessão aberta** perde o acesso na próxima requisição. Teste na Task 3.
4. **Datas inválidas ou no futuro** em data do pedido e data de chegada são recusadas. Teste na Task 7.
5. **PDF de 0 bytes, extensão `.PDF` maiúscula e envio sem arquivo** são tratados (vazio recusado, maiúscula aceita, ausência recusada com mensagem). Teste na Task 5.

---

### Task 1: Esqueleto, configuração e banco

**Files:**
- Create: `app/__init__.py`, `app/config.py`, `app/db.py`, `app/schema.sql`, `requirements.txt`, `.gitignore`, `tests/conftest.py`, `tests/test_base.py`

**Interfaces:**
- Produces: `create_app(overrides: dict | None = None) -> Flask`; `db.get_db() -> sqlite3.Connection` (`row_factory=sqlite3.Row`, `PRAGMA foreign_keys=ON`); `db.init_db() -> None`; `db.transacao()` (gerenciador de contexto que faz `commit` ou `rollback`); `db.agora_utc() -> str` (ISO 8601 UTC); fixtures `app`, `client`, `db` com banco temporário.

- [ ] **Step 1:** Em `Aprovacao de Pedidos/`, rodar `git init`, criar o venv, e criar `requirements.txt` com Flask, Flask-WTF, Waitress, pytest e tzdata (versões fixadas) e `.gitignore` com `data/`, `.venv/`, `__pycache__/`.
- [ ] **Step 2: Escrever os testes que falham** em `tests/test_base.py`: `test_schema_cria_as_quatro_tabelas` (usuarios, pedidos, historico, avisos), `test_foreign_keys_ativas`, `test_transacao_faz_rollback_em_erro`, `test_chave_secreta_gerada_e_reaproveitada` (duas fábricas no mesmo `DATA_DIR` têm a mesma chave), `test_cabecalhos_de_seguranca` (resposta de qualquer URL traz `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy` e `Content-Security-Policy`).
- [ ] **Step 3:** Rodar `python -m pytest tests/test_base.py -v`; esperado: FALHA (módulos não existem).
- [ ] **Step 4:** Implementar `config.py` (`DATA_DIR` por `PEDIDOS_DATA_DIR` ou `data/`, `MAX_PDF_MB=20`, `BASE_URL`, `EMAIL_MODE` em `desligado|smtp`, `SESSION_LIFETIME`=8 h, `LOGIN_MAX_FALHAS=5`, `BLOQUEIO_MIN=15`, `MIN_SENHA=10`), `schema.sql` exatamente com as colunas da seção 5 da spec, `db.py`, e `create_app` com `ProxyFix`, CSRF, cookies de sessão seguros e `after_request` dos cabeçalhos. A chave secreta vem de `PEDIDOS_SECRET_KEY` ou de `data/secret.key`, gerado na primeira execução.
- [ ] **Step 5:** Rodar os testes; esperado: PASSAM.
- [ ] **Step 6: Commit** — `feat: esqueleto da aplicação, configuração e banco`.

---

### Task 2: Regras de estado (`workflow.py`)

**Files:**
- Create: `app/workflow.py`, `tests/test_workflow.py`

**Interfaces:**
- Produces: constantes `STATUS`, `PERFIS`, `ACOES` (`aprovar`, `reprovar`, `confirmar_envio`, `informar_nf`, `marcar_chegada`, `arquivar`); exceções `TransicaoInvalida` e `NaoAutorizado`; `proximo_status(status: str, acao: str, perfil: str, eh_dono: bool = False) -> str`; `acoes_disponiveis(status: str, perfil: str, eh_dono: bool = False) -> list[str]`.

- [ ] **Step 1: Testes que falham**: tabela parametrizada com todas as linhas da seção 4 da spec (ex.: `("pendente","aprovar","gestor") -> "aprovado"`, `("aprovado","confirmar_envio","solicitante", dono) -> "enviado"`, `("recebido","arquivar","fiscal") -> "arquivado"`, `("reprovado","arquivar","admin") -> "arquivado"`); `test_estado_errado_levanta_transicao_invalida` (ex.: aprovar um pedido `enviado`); `test_perfil_errado_levanta_nao_autorizado` (solicitante tentando `aprovar`, gestor tentando `informar_nf`); `test_confirmar_envio_exige_dono` (solicitante que não é dono recebe `NaoAutorizado`); `test_admin_faz_o_que_o_fiscal_faz` (`informar_nf`, `marcar_chegada`, `arquivar`); `test_acoes_disponiveis` para cada status e perfil.
- [ ] **Step 2:** Rodar `python -m pytest tests/test_workflow.py -v`; esperado: FALHA.
- [ ] **Step 3:** Implementar a tabela de transições como um dicionário `{(status, acao): (novo_status, perfis_permitidos)}`; a regra de dono vale só para `confirmar_envio`. Sem importar Flask nem sqlite.
- [ ] **Step 4:** Rodar; esperado: PASSAM.
- [ ] **Step 5: Commit** — `feat: regras de estado do pedido`.

---

### Task 3: Autenticação e sessão

**Files:**
- Create: `app/auth.py`, `app/templates/base.html`, `app/templates/login.html`, `app/templates/trocar_senha.html`, `tests/test_auth.py`
- Modify: `app/__init__.py` (registrar o blueprint e o `before_request`)

**Interfaces:**
- Consumes: `db.get_db`, `db.transacao`, `db.agora_utc` (Task 1).
- Produces: `criar_usuario(nome: str, email: str, senha: str, perfil: str, trocar_senha: bool = True) -> int`; `tentar_login(email: str, senha: str, agora: datetime | None = None) -> sqlite3.Row | None`; `usuario_atual() -> sqlite3.Row | None`; decoradores `login_required` e `perfil_required(*perfis)`; rotas `/login`, `/sair`, `/trocar-senha`. Fixture de teste `entrar(client, email, senha)`.

- [ ] **Step 1: Testes que falham** em `tests/test_auth.py`: `test_senha_curta_e_recusada` (menos de 10 caracteres); `test_login_correto_abre_sessao`; `test_senha_errada_mostra_mensagem_generica` (mesma mensagem para e-mail inexistente e senha errada); `test_bloqueio_apos_cinco_falhas` (a 6ª tentativa com senha correta falha, e volta a funcionar após 15 minutos usando `agora`); `test_primeiro_acesso_obriga_trocar_senha` (qualquer rota redireciona para `/trocar-senha` enquanto `trocar_senha=1`); `test_rotas_exigem_login` (acesso anônimo à lista redireciona para `/login`); `test_usuario_desativado_perde_acesso_na_proxima_requisicao` (Review Focus 3); `test_post_sem_csrf_e_recusado`; `test_cookie_de_sessao_tem_flags` (`HttpOnly`, `Secure`, `SameSite=Lax`).
- [ ] **Step 2:** Rodar `python -m pytest tests/test_auth.py -v`; esperado: FALHA.
- [ ] **Step 3:** Implementar com `werkzeug.security`; contador `falhas_login` e `bloqueado_ate` zerados no sucesso; `before_request` que recarrega o usuário do banco a cada requisição (é o que derruba o desativado) e aplica a troca de senha obrigatória. `templates/base.html` traz o layout comum, ainda sem estilo final.
- [ ] **Step 4:** Rodar; esperado: PASSAM.
- [ ] **Step 5: Commit** — `feat: login, bloqueio por tentativas e controle de perfis`.

---

### Task 4: Administração de usuários e criação do administrador

**Files:**
- Create: `app/admin.py`, `app/templates/admin_usuarios.html`, `scripts/criar_admin.py`, `tests/test_admin.py`
- Modify: `app/__init__.py` (registrar o blueprint)

**Interfaces:**
- Consumes: `criar_usuario`, `perfil_required("admin")` (Task 3).
- Produces: rotas `GET /admin/usuarios`, `POST /admin/usuarios` (criar), `POST /admin/usuarios/<id>/ativo` (ativar ou desativar), `POST /admin/usuarios/<id>/senha` (redefinir, gera senha provisória e liga `trocar_senha`); script `python scripts/criar_admin.py` que pede nome, e-mail e senha no terminal.

- [ ] **Step 1: Testes que falham**: `test_so_admin_acessa_a_administracao` (os outros três perfis recebem 403); `test_admin_cria_usuario_com_troca_obrigatoria`; `test_email_duplicado_e_recusado`; `test_perfil_invalido_e_recusado`; `test_admin_nao_desativa_a_si_mesmo`; `test_redefinir_senha_liga_troca_obrigatoria`.
- [ ] **Step 2:** Rodar `python -m pytest tests/test_admin.py -v`; esperado: FALHA.
- [ ] **Step 3:** Implementar as rotas e o script (o script usa `criar_usuario(..., "admin", trocar_senha=False)`).
- [ ] **Step 4:** Rodar; esperado: PASSAM.
- [ ] **Step 5: Commit** — `feat: administração de usuários`.

---

### Task 5: Anexo em PDF

**Files:**
- Create: `app/arquivos.py`, `tests/test_arquivos.py`

**Interfaces:**
- Consumes: configuração `MAX_PDF_MB`, `DATA_DIR` (Task 1).
- Produces: exceção `PdfInvalido(mensagem: str)`; `salvar_pdf(arquivo: werkzeug.datastructures.FileStorage) -> tuple[str, str]` (retorna `(nome_gerado, nome_original)`); `caminho_pdf(nome_gerado: str) -> pathlib.Path`; `pdf_existe(nome_gerado: str) -> bool`.

- [ ] **Step 1: Testes que falham**: `test_aceita_pdf_valido_e_gera_nome_uuid` (nome termina em `.pdf`, difere do original); `test_extensao_maiuscula_e_aceita` (`NOTA.PDF`); `test_recusa_conteudo_que_nao_e_pdf` (arquivo `.pdf` cujos bytes não começam com `%PDF-`); `test_recusa_arquivo_vazio` (0 bytes); `test_recusa_acima_do_limite` (com `MAX_PDF_MB=1`); `test_recusa_sem_arquivo` (`None` ou `filename=""`); `test_nome_original_com_acentos_barras_e_300_caracteres` (grava sem erro, `nome_original` preservado e o arquivo no disco usa só o nome gerado); `test_caminho_nao_sai_da_pasta` (nome gerado com `../` não resolve para fora de `DATA_DIR/pdfs`); `test_pdf_existe_falso_quando_sumiu`.
- [ ] **Step 2:** Rodar `python -m pytest tests/test_arquivos.py -v`; esperado: FALHA.
- [ ] **Step 3:** Implementar. Ler os primeiros bytes e o tamanho antes de gravar; gravar em `DATA_DIR/pdfs/`; truncar `nome_original` para exibição (255 caracteres) sem alterar o nome em disco.
- [ ] **Step 4:** Rodar; esperado: PASSAM.
- [ ] **Step 5: Commit** — `feat: validação e armazenamento seguro de PDF`.

---

### Task 6: Avisos e envio de e-mail

**Files:**
- Create: `app/notificacoes.py`, `tests/test_notificacoes.py`

**Interfaces:**
- Consumes: `db.get_db`, `db.agora_utc` (Task 1).
- Produces: `destinatarios(db, evento: str, pedido: sqlite3.Row) -> list[int]`; `registrar_avisos(db, evento: str, pedido: sqlite3.Row) -> list[int]` (grava uma linha em `avisos` por destinatário, com `email_status` `pendente` ou `desligado` conforme `EMAIL_MODE`); `class Notificador(Protocol)` com `enviar(destino: str, assunto: str, corpo: str) -> None`; `NotificadorDesligado`, `NotificadorSmtp(host, porta, usuario, senha, remetente)`; `enviar_pendentes(db, notificador: Notificador) -> int` (devolve quantos foram enviados). `evento` é `criar` ou o nome de uma ação do workflow.

- [ ] **Step 1: Testes que falham**: `test_destinatarios_por_evento` com uma tabela cobrindo a seção 8 da spec: `criar` → solicitante e os gestores ativos; `aprovar` → solicitante e fiscais ativos; `reprovar` → solicitante; `confirmar_envio` → fiscais; `informar_nf` e `marcar_chegada` → solicitante; `test_usuario_inativo_nao_recebe`; `test_aviso_contem_titulo_e_link_do_pedido` (`BASE_URL/pedidos/<id>`); `test_falha_de_email_marca_erro_e_nao_levanta` (notificador que lança exceção); `test_reenvio_envia_os_com_erro`; `test_email_desligado_marca_desligado`.
- [ ] **Step 2:** Rodar `python -m pytest tests/test_notificacoes.py -v`; esperado: FALHA.
- [ ] **Step 3:** Implementar. O aviso de `reprovar` inclui o motivo (campo `comentario` do histórico mais recente do pedido). `NotificadorSmtp` usa `smtplib` com STARTTLS; credenciais vêm de variáveis de ambiente lidas pela fábrica, não pelo módulo.
- [ ] **Step 4:** Rodar; esperado: PASSAM.
- [ ] **Step 5: Commit** — `feat: avisos no sistema e envio de e-mail`.

---

### Task 7: Serviço de pedidos

**Files:**
- Create: `app/servico.py`, `tests/test_servico.py`

**Interfaces:**
- Consumes: `workflow.proximo_status` (Task 2), `arquivos.salvar_pdf` (Task 5), `notificacoes.registrar_avisos` (Task 6), `db.transacao` (Task 1).
- Produces: `parse_valor_brl(texto: str) -> int` (centavos; levanta `ValorInvalido`); `parse_data(texto: str, *, permitir_futuro: bool = False) -> date` (levanta `DataInvalida`); `criar_pedido(db, usuario, cliente: str, valor: str, descricao: str, arquivo, reenvio_de: int | None = None) -> int`; `aplicar_acao(db, pedido_id: int, acao: str, usuario, dados: dict) -> sqlite3.Row`; exceção `JaDecidido(decisor: str, quando: str)`.

- [ ] **Step 1: Testes que falham**: `test_parse_valor_brl` parametrizado (`"1.234,56"→123456`, `"10"→1000`, `"0,5"→50` aceitos; `""`, `"0"`, `"-5"`, `"abc"`, `"12,345"` recusados); `test_parse_data` (`"2026-10-23"` e `"23/10/2026"` aceitas; formato inválido e futuro recusados); `test_criar_pedido_grava_pendente_historico_e_avisos`; `test_titulo_usa_o_id` (`Novo Pedido - 1`, `Novo Pedido - 2`); `test_reenvio_de_aponta_para_pedido_reprovado_existente` (recusa apontar para pedido inexistente); `test_reprovar_exige_motivo`; `test_dois_gestores_so_o_primeiro_vale` (segunda chamada levanta `JaDecidido` com nome e hora do primeiro e não altera o histórico); `test_fluxo_completo_registra_historico_em_cada_passo` (pendente → aprovado → enviado → faturado → recebido → arquivado, cada linha com usuário, status anterior e novo); `test_informar_nf_exige_numero_ate_30_caracteres`; `test_marcar_chegada_usa_hoje_por_padrao`; `test_transicao_e_historico_na_mesma_transacao` (erro ao gravar aviso desfaz a mudança de status); `test_acao_recusada_nao_deixa_rastro`.
- [ ] **Step 2:** Rodar `python -m pytest tests/test_servico.py -v`; esperado: FALHA.
- [ ] **Step 3:** Implementar. A decisão do gestor usa `UPDATE pedidos SET status=? ... WHERE id=? AND status='pendente'` e confere `rowcount`; se for 0, consulta o histórico e levanta `JaDecidido`. `aplicar_acao` valida os dados da ação, pede o novo status ao workflow, atualiza o pedido, grava histórico e avisos dentro de uma única `transacao`.
- [ ] **Step 4:** Rodar; esperado: PASSAM.
- [ ] **Step 5: Commit** — `feat: serviço de pedidos com histórico e transições atômicas`.

---

### Task 8: Rotas e telas de pedidos

**Files:**
- Create: `app/pedidos.py`, `app/templates/lista.html`, `app/templates/novo.html`, `app/templates/detalhe.html`, `app/templates/avisos.html`, `tests/test_pedidos_rotas.py`
- Modify: `app/templates/base.html` (menu e contador de avisos), `app/__init__.py` (registrar o blueprint e o filtro de data/hora em `America/Sao_Paulo`)

**Interfaces:**
- Consumes: `servico.*` (Task 7), `workflow.acoes_disponiveis` (Task 2), `arquivos.caminho_pdf`, `arquivos.pdf_existe` (Task 5), `perfil_required` (Task 3).
- Produces: `GET /` (lista com filtro `?status=` e atalhos por perfil), `GET|POST /pedidos/novo`, `GET /pedidos/<id>`, `POST /pedidos/<id>/<acao>`, `GET /pedidos/<id>/pdf`, `GET /avisos`, `POST /avisos/<id>/lido`.

- [ ] **Step 1: Testes que falham**: `test_solicitante_ve_so_os_proprios_pedidos`; `test_solicitante_nao_abre_pedido_de_outro` (404, não 403, para não revelar existência); `test_solicitante_nao_baixa_pdf_de_outro`; `test_gestor_fiscal_e_admin_veem_todos`; `test_pdf_abre_com_content_type_pdf_e_requer_login`; `test_pdf_ausente_no_disco_mostra_aviso_e_nao_erro_500`; `test_botoes_da_tela_so_do_perfil_e_da_etapa` (gestor vê Aprovar/Reprovar só em pendente; solicitante vê Confirmar envio só em aprovado e só no dono); `test_acao_nao_permitida_pelo_post_retorna_403` (mesmo sem o botão); `test_fluxo_completo_pelo_navegador` (cria, aprova, envia, fatura, recebe e arquiva usando o cliente de teste, conferindo o histórico exibido); `test_texto_com_html_aparece_escapado` (cliente, descrição, comentário e nome do PDF com `<script>alert(1)</script>`; a resposta contém o texto escapado e nunca o `<script>` literal); `test_segundo_gestor_ve_mensagem_de_ja_decidido`; `test_avisos_marcar_como_lido`; `test_horario_do_historico_exibido_em_sao_paulo` (um registro gravado em `2026-10-23T15:00:00+00:00` aparece como `23/10/2026 12:00`); `test_atalhos_por_perfil` (gestor vê "A decidir" com só os pendentes; fiscal vê "Aguardando nota" com só os enviados e "Aguardando chegada" com só os faturados).
- [ ] **Step 2:** Rodar `python -m pytest tests/test_pedidos_rotas.py -v`; esperado: FALHA.
- [ ] **Step 3:** Implementar as rotas finas, que traduzem as exceções do serviço em mensagens (`flash`) e códigos HTTP. `/pedidos/<id>/pdf` usa `send_file` com `mimetype="application/pdf"` e `download_name` com o nome original já sanitizado. O contador de avisos não lidos entra no `base.html` por um `context_processor`.
- [ ] **Step 4:** Rodar; esperado: PASSAM.
- [ ] **Step 5: Commit** — `feat: telas e rotas de pedidos`.

---

### Task 9: Visual mobile e identidade do Grupo Dluz

**Files:**
- Create: `app/static/estilo.css`
- Modify: `app/templates/*.html` (classes e estrutura); `tests/test_pedidos_rotas.py` (um teste de marcação)

**Interfaces:**
- Consumes: as telas da Task 8.
- Produces: folha de estilo única; nenhuma mudança de rotas ou assinaturas.

- [ ] **Step 1:** Ler a memória `dluz-identidade-visual.md` e a paleta já usada em `dluz_assets.py` para extrair as cores exatas (creme, grafite, âmbar) e a fonte monoespaçada dos números; definir tudo como variáveis CSS.
- [ ] **Step 2: Teste que falha**: `test_paginas_tem_viewport_e_folha_de_estilo` (toda tela traz `<meta name="viewport" content="width=device-width, initial-scale=1">` e o link para `estilo.css`, e valores aparecem em elementos com a classe `num`).
- [ ] **Step 3:** Rodar o teste; esperado: FALHA. Implementar o CSS mobile-first (botões com pelo menos 44 px de altura, tabela da lista vira cartões em tela estreita, histórico em linha do tempo) e ajustar os templates.
- [ ] **Step 4:** Rodar o teste e a suíte completa (`python -m pytest -v`); esperado: PASSAM.
- [ ] **Step 5: Verificação visual:** iniciar o servidor local, abrir a lista, o detalhe e o formulário no navegador embutido em largura de celular (375 px) e conferir leitura, botões e ausência de rolagem horizontal. Anotar qualquer ajuste.
- [ ] **Step 6: Commit** — `feat: layout mobile e identidade visual do grupo`.

---

### Task 10: Backup

**Files:**
- Create: `scripts/backup.py`, `tests/test_backup.py`

**Interfaces:**
- Produces: `fazer_backup(data_dir: pathlib.Path, destino: pathlib.Path) -> pathlib.Path` (cria `destino/aprovacao_AAAAMMDD_HHMMSS/` com `pedidos.db` e a pasta `pdfs/`); linha de comando `python scripts/backup.py <destino> [--manter N]` que apaga os backups mais antigos além de N (padrão 30).

- [ ] **Step 1: Testes que falham**: `test_backup_copia_banco_consistente_e_pdfs` (abrir o `pedidos.db` copiado e ler os pedidos); `test_backup_funciona_com_banco_em_uso` (conexão aberta com escrita pendente); `test_manter_apaga_os_mais_antigos`; `test_destino_inexistente_e_criado`.
- [ ] **Step 2:** Rodar `python -m pytest tests/test_backup.py -v`; esperado: FALHA.
- [ ] **Step 3:** Implementar com `sqlite3.Connection.backup()` para o banco e `shutil.copytree` para os PDFs.
- [ ] **Step 4:** Rodar; esperado: PASSAM.
- [ ] **Step 5: Commit** — `feat: script de backup`.

---

### Task 11: Instalação no Windows 11 e publicação

**Files:**
- Create: `scripts/iniciar.py`, `scripts/instalar_windows.md`, `README.md`

**Interfaces:**
- Consumes: `create_app` (Task 1), `fazer_backup` (Task 10).
- Produces: `python scripts/iniciar.py` sobe o Waitress em `127.0.0.1:8000`; guia de instalação em português.

- [ ] **Step 1:** Escrever `scripts/iniciar.py` (`waitress.serve(create_app(), host="127.0.0.1", port=8000)`) e confirmar localmente que a página de login abre.
- [ ] **Step 2:** Escrever `scripts/instalar_windows.md` com os passos para o PC com Windows 11, cada um com o resultado esperado: instalar o Python e marcar "Add to PATH"; copiar a pasta; criar o venv e instalar `requirements.txt`; rodar `python scripts/criar_admin.py`; definir `PEDIDOS_BASE_URL` com o endereço do Funnel; criar a tarefa no Agendador de Tarefas ("ao iniciar o computador", sem exigir usuário logado, reiniciar em caso de falha) que roda `iniciar.py`; criar outra tarefa diária para `backup.py` apontando para um disco ou pasta diferente da do banco; habilitar o HTTPS e o Funnel no painel do Tailscale e executar `tailscale funnel 8000` (conferir a sintaxe vigente na documentação da Tailscale no momento); testar do celular com o Wi-Fi desligado.
- [ ] **Step 3:** Escrever o `README.md` curto: o que é, como rodar os testes, como iniciar e onde ficam os dados.
- [ ] **Step 4: Verificação:** rodar a suíte completa (`python -m pytest -v`); esperado: tudo passa. Iniciar `iniciar.py` e fazer um pedido de ponta a ponta com três usuários de teste (solicitante, gestor, fiscal), conferindo que o PDF abre em cada tela e que o histórico completo aparece.
- [ ] **Step 5: Commit** — `docs: instalação no Windows 11 e README`.

A instalação no PC da empresa (Step 2) é feita junto com o usuário, passo a passo, depois que todas as tasks anteriores estiverem aprovadas.
