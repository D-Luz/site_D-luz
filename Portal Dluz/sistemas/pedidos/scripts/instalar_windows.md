# Instalação no Windows 11 e publicação

Este guia leva o sistema de Aprovação de Pedidos do zero até funcionar no PC da empresa, aberto na internet pelo Tailscale Funnel. Foi escrito para ser seguido passo a passo, junto com quem está ajudando na instalação. Cada passo termina com **O que você deve ver**: se não for isso, pare e resolva antes de continuar.

Como ler os blocos de comando: tudo que está em caixa de código é para ser digitado (ou colado) no **PowerShell** e confirmado com Enter. Os caminhos usados como exemplo são sempre os mesmos:

| O que | Onde |
|---|---|
| O programa (código) | `C:\PedidosAprovacao` |
| Os dados (banco, PDFs, registro) | `C:\PedidosDados` |
| Os backups | `D:\BackupsPedidos` (outro disco ou pasta de rede) |

> **Atenção, em duas linhas.** O sistema vai ficar público na internet. A única proteção dele é o login, por isso as senhas precisam ser fortes e o passo 15 (segurança) não é opcional. Os nomes de botões do Windows e do Tailscale mudam de tempos em tempos: se um nome não bater exatamente, procure o parecido.

Alguns passos usam comandos do Windows que **não puderam ser testados** durante o desenvolvimento (foi feito num Mac). Eles aparecem no passo 16, a lista do que precisa ser conferido junto, no PC.

---

## 1. O que você precisa

- Um PC com **Windows 11**, ligado o dia todo (vai ser o servidor), com internet estável. Se possível, ligado a um nobreak.
- O **Tailscale já instalado e com a conta conectada** nesse PC. Para conferir: o ícone do Tailscale aparece perto do relógio e o menu mostra "Connected" (conectado).
- Acesso de **administrador** no Windows e no painel do Tailscale (`login.tailscale.com`).
- O **Python** (programa que roda o sistema):
  - Versão: **3.12 ou mais nova**. O sistema foi desenvolvido e testado no Python 3.14. O mínimo absoluto é o 3.11 (o código usa recursos do 3.10 e a biblioteca WTForms 3.3 exige o 3.11), mas não vale a pena ficar no mínimo.
  - Baixe em <https://www.python.org/downloads/windows/> o **"Windows installer (64-bit)"**. Não use a versão da Microsoft Store.
  - Abra o instalador. Na **primeira tela**, antes de qualquer outro clique, **marque a caixa "Add python.exe to PATH"** (fica na parte de baixo). Depois clique em **"Install Now"** e aguarde terminar ("Setup was successful").

**Conferir o Python.** Clique no menu Iniciar, digite `PowerShell`, abra o "Windows PowerShell" e digite:

```powershell
py --version
```

**O que você deve ver:** `Python 3.14.x` (ou a versão que você instalou), sem mensagem de erro. Se aparecer "não é reconhecido", o Python não foi instalado ou a caixa do PATH ficou desmarcada: rode o instalador de novo, escolha "Modify" e marque a opção de PATH.

---

## 2. Copiar a pasta do projeto para `C:\PedidosAprovacao`

Quem está ajudando entrega a pasta do projeto (por pen drive, rede ou arquivo .zip). Ela deve ser copiada para `C:\PedidosAprovacao` **sem** estas pastas, que não devem ir junto:

- `.venv` (ambiente do Python do outro computador; o do PC novo é criado no passo 3);
- `data` (dados de teste de quem desenvolveu; o sistema novo começa vazio);
- `.git`, `.superpowers`, `__pycache__`, `.pytest_cache` e arquivos `.DS_Store` (lixo de desenvolvimento);
- o arquivo `scripts\dados_demo.py`: ele cria usuários de demonstração com uma senha **pública** e serve só para desenvolvimento. No PC do sistema, que fica na internet, ele seria uma porta aberta.

> Para quem prepara o pacote num Mac: dentro da pasta que contém "Aprovacao de Pedidos", o comando abaixo cria o `.zip` já sem essas pastas.
>
> ```bash
> zip -r pedidos.zip "Aprovacao de Pedidos" -x "*/.venv/*" "*/data/*" "*/.git/*" "*/.superpowers/*" "*/__pycache__/*" "*/.pytest_cache/*" "*.DS_Store" "*/scripts/dados_demo.py"
> ```
>
> No Windows, clique com o botão direito no `.zip` > "Extrair tudo" e copie o conteúdo da pasta extraída para `C:\PedidosAprovacao`. Se a pasta chegou de outro jeito (pen drive, rede), **apague `C:\PedidosAprovacao\scripts\dados_demo.py` depois de copiar**.

**O que você deve ver:** ao abrir `C:\PedidosAprovacao` no Explorador de Arquivos, as pastas `app`, `scripts` e `tests` e os arquivos `requirements.txt` e `README.md` — **diretamente** ali, e não dentro de uma pasta dentro da pasta. Confirme que existe o arquivo `C:\PedidosAprovacao\scripts\iniciar.py` e que **não** existe `C:\PedidosAprovacao\scripts\dados_demo.py`.

---

## 3. Criar o ambiente do Python e instalar as bibliotecas

Abra um PowerShell **dentro** da pasta do projeto: no Explorador de Arquivos, abra `C:\PedidosAprovacao`, clique na barra de endereço (onde aparece o caminho), apague o que está lá, digite `powershell` e aperte Enter. (Alternativa: botão direito num espaço vazio da pasta > "Abrir no Terminal".)

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

O primeiro comando demora alguns segundos e não escreve nada. O segundo baixa as bibliotecas da internet (1 a 2 minutos).

**O que você deve ver:** o segundo comando termina com uma linha começando por `Successfully installed` e listando Flask, Flask-WTF, waitress, pytest, tzdata e outras. Existe agora o arquivo `C:\PedidosAprovacao\.venv\Scripts\python.exe`.

Teste opcional (recomendado): `.\.venv\Scripts\python.exe -m pytest -q`. **Deve terminar com** `passed` e nenhum `failed`. Alguns testes que dependem de permissões de arquivo do Mac/Linux são pulados no Windows, e os de `scripts\dados_demo.py` (que não foi copiado, passo 2) também: eles aparecem como `skipped`, e isso é normal. Já um `failed` ou `error` não é: pare e resolva antes de continuar. Os testes usam pastas temporárias e não mexem nos dados reais.

---

## 4. Pasta de dados e variáveis de ambiente

### 4.1 Criar a pasta de dados e limitar quem pode mexer nas pastas

Os dados ficam **fora** da pasta do programa, para que atualizar o programa (passo 13) nunca apague o histórico.

```powershell
mkdir C:\PedidosDados
```

Use um disco local do próprio PC. **Não** use pasta de rede, nem pasta sincronizada pelo OneDrive (Documentos, Área de Trabalho): o banco de dados trava e pode corromper.

**O que você deve ver:** a pasta `C:\PedidosDados` existe, vazia.

**Agora, limite o acesso às pastas** do programa, dos dados e dos backups. Uma pasta criada na raiz de um disco herda, por padrão, a permissão "Usuários autenticados: Modificar": qualquer pessoa com login neste PC conseguiria ler a `secret.key` (a chave que assina os logins), abrir o banco (ou um backup dele) com todos os pedidos ou trocar o código do programa. Faça o seguinte **agora, antes do passo 5** (depois de `C:\PedidosAprovacao` e `C:\PedidosDados` existirem; a pasta dos backups é criada no item 5):

1. Abra o **Prompt de Comando como administrador**: menu Iniciar > digite `cmd` > botão direito em "Prompt de Comando" > **"Executar como administrador"** > Sim. (É o Prompt de Comando, não o PowerShell: os comandos abaixo funcionam assim, sem ajustes.)
2. Digite `echo %USERNAME%`. O nome que aparece **precisa ser a conta do Windows que vai rodar as tarefas do passo 9** (a mesma que instalou o Python). Se for outra (por exemplo, você abriu o prompt com as credenciais de outro administrador), troque `%USERNAME%` pelo nome da conta certa nos comandos abaixo, ou refaça-os depois com a conta certa.
3. Pasta de dados:

```
icacls C:\PedidosDados /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "%USERNAME%:(OI)(CI)M"
```

4. Pasta do programa:

```
icacls C:\PedidosAprovacao /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "%USERNAME%:(OI)(CI)RX"
```

5. Pasta dos backups (o backup tem todos os pedidos e PDFs: merece o mesmo cuidado). Crie-a e restrinja-a do mesmo jeito que a pasta de dados; se o seu disco de backup tiver outra letra, troque o `D:` nos dois comandos:

```
mkdir D:\BackupsPedidos
icacls D:\BackupsPedidos /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "%USERNAME%:(OI)(CI)M"
```

Em português claro: `/inheritance:r` apaga as permissões herdadas (as que davam acesso a todo mundo); `/grant:r` define quem pode entrar: o próprio Windows (`S-1-5-18`, "SYSTEM") e os Administradores (`S-1-5-32-544`) com **controle total** (`F`), e a conta da tarefa com **modificar** (`M`) na pasta de dados, pois o servidor grava ali o banco, os PDFs e o registro, e só **ler e executar** (`RX`) na pasta do programa, porque ele só precisa rodar o código. Na pasta dos backups a conta da tarefa também precisa de **modificar** (`M`): a tarefa de backup cria uma pasta nova por dia e apaga as mais antigas. `(OI)(CI)` faz valer para tudo que está dentro. Usamos os códigos `S-1-5-...` em vez de nomes porque os nomes mudam conforme o idioma do Windows. Se o backup for numa **pasta de rede** (`\\servidor\...`), não rode o `icacls` nela daqui: peça a quem cuida do servidor para dar acesso só à conta da tarefa e aos administradores.

**O que você deve ver:** cada comando `icacls` termina com `Successfully processed 1 files` (ou "1 arquivos processados com êxito"). Para conferir, digite `icacls C:\PedidosDados`: devem aparecer **só três** linhas (`NT AUTHORITY\SYSTEM`, `BUILTIN\Administrators` e a sua conta), e o mesmo para `icacls C:\PedidosAprovacao` e `icacls D:\BackupsPedidos`. Se aparecer `Users` ou `Authenticated Users`, o comando não valeu.

Consequência: a sua conta, num PowerShell comum, passa a só **ler** a pasta do programa. Para atualizar o sistema (passo 13) abra o PowerShell **como administrador**. Se o Python não puder gravar a pasta `__pycache__` dentro do programa, ele simplesmente roda sem guardar esse cache: é normal.

### 4.2 O que são variáveis de ambiente

São configurações com nome e valor que o Windows entrega aos programas. O sistema lê as suas (todas começam com `PEDIDOS_`). Usamos variáveis **do sistema** (e não do usuário) para que a tarefa automática do passo 9 as enxergue mesmo sem ninguém logado.

### 4.3 Como criar uma variável do sistema

1. Clique no menu Iniciar e digite `variáveis de ambiente`.
2. Abra **"Editar as variáveis de ambiente do sistema"** (o Windows pede permissão de administrador: aceite).
3. Na janela "Propriedades do Sistema", clique no botão **"Variáveis de Ambiente..."**.
4. Na parte de **baixo** ("Variáveis do sistema"), clique em **"Novo..."**.
5. Preencha "Nome da variável" e "Valor da variável" e clique **OK**. Repita para cada variável.
6. Ao terminar, clique **OK** nas duas janelas.

### 4.4 As variáveis

| Nome | Valor | Para que serve |
|---|---|---|
| `PEDIDOS_DATA_DIR` | `C:\PedidosDados` | **Obrigatória**: sem ela o servidor se recusa a subir, e `criar_admin.py` e `backup.py` se recusam a rodar. Onde ficam o banco (`pedidos.db`), os PDFs (`pdfs`), a chave interna (`secret.key`) e o registro (`logs`). |
| `PEDIDOS_BASE_URL` | `http://localhost:8000` (por enquanto) | Endereço público do sistema, usado nos links dos avisos. **Será trocado no passo 8** pelo endereço do Funnel. |
| `PEDIDOS_EMAIL_MODE` | `desligado` (ou `smtp`) | Envio de e-mail. `desligado` = os avisos aparecem só dentro do sistema (caixa "Avisos"). Para ligar o e-mail, veja 4.5 (Brevo). |
| `PEDIDOS_BACKUP_DESTINO` | `D:\BackupsPedidos` | Pasta dos backups (passos 9 e 12). **Sem barra no final.** |

Existe ainda `PEDIDOS_PORTA` (porta do servidor, padrão `8000`). **Não crie**: o Funnel do passo 8 aponta para a 8000.

**Não crie** `PEDIDOS_SECRET_KEY`: o sistema gera sozinho a chave em `C:\PedidosDados\secret.key` na primeira vez que roda. **Não crie** `PEDIDOS_COOKIE_INSECURE` como variável do sistema (veja o passo 6).

### 4.5 E-mail pela Brevo (recomendado)

A primeira versão funciona com `PEDIDOS_EMAIL_MODE=desligado`: os avisos aparecem **dentro do sistema** (item "Avisos" no menu), só não chegam por e-mail. Para ligar o e-mail, o caminho mais simples e que dura mais é um serviço de envio por SMTP, a **Brevo** (plano gratuito: 300 e-mails por dia). Não é o e-mail do Microsoft 365: a Microsoft vai desativar por padrão o login SMTP simples nas empresas no fim de dezembro de 2026 (o administrador ainda poderá reativar, e a data final será anunciada depois), então depender dele seria deixar uma data para o envio parar.

O e-mail sai de um **subdomínio só para isso** (`avisos.grupodluz.com`): os registros DNS ficam nesse subdomínio e o e-mail da empresa no Microsoft 365 **não é tocado**. Esta parte já foi feita na Brevo e na Hostinger. O que segue serve para refazer ou para outra empresa (os nomes das telas podem mudar com o tempo: confira no site da Brevo).

**A. Na Brevo** (conta gratuita; ela pode pedir uma validação antes de liberar o SMTP):

1. **Senders, domains, IPs > Domínios > Adicionar um novo domínio**: digite o **subdomínio** (`avisos.grupodluz.com`), não o domínio principal. Pule o "subdomínio de marca". Em "Método de configuração" escolha **Registros DNS individuais** e **Manual** (assim a Brevo não ganha acesso à sua conta da Hostinger).
2. A Brevo mostra 4 registros: um **TXT** (`avisos`, valor `brevo-code:...`), dois **CNAME** (`brevo1._domainkey.avisos` e `brevo2._domainkey.avisos`) e um **TXT** (`_dmarc.avisos`).

**B. Na Hostinger** (hPanel > Domínios > o domínio > **DNS / Nameservers** > Registros DNS):

3. Crie cada registro com o **mesmo tipo e valor** da Brevo. Em **Nome**, digite só o que vem **antes** do domínio (`brevo1._domainkey.avisos`): a Hostinger acrescenta `.grupodluz.com` sozinha. **Não altere nem apague nenhum registro existente** (MX, SPF, `autodiscover`, `MS=`): os da Brevo são novos, sob `avisos`.
4. Volte à Brevo, clique **Verificar registros** (pode levar de minutos a algumas horas) e depois **Autenticar domínio**. Na lista de domínios, `avisos.grupodluz.com` deve aparecer como **Autenticado**.

**C. Remetente e chave SMTP, na Brevo:**

5. **Remetentes > Adicionar remetente**: nome `Pedidos Grupo Dluz`, e-mail `pedidos@avisos.grupodluz.com`. Esse endereço **não precisa existir como caixa de e-mail** (o sistema só envia, não recebe respostas); ele deve aparecer como **Verificado**.
6. **SMTP & API > Gerar chave SMTP**: a Brevo manda um **código de 6 dígitos para o seu e-mail** (confirma que é você). Dê o nome `pedidos-aprovacao` e gere. **Copie a chave na hora** (ela só aparece uma vez) e guarde em local seguro; **nunca** a mande por e-mail ou chat. Na mesma página a Brevo mostra o **login SMTP** (parecido com `xxxxxxxx@smtp-brevo.com`: **não** é o seu e-mail) e o servidor.

**D. No PC**, crie estas variáveis do sistema (passo 4.3) e depois **reinicie o PC** (regra de ouro abaixo):

| Nome | Valor |
|---|---|
| `PEDIDOS_EMAIL_MODE` | `smtp` |
| `PEDIDOS_SMTP_HOST` | `smtp-relay.brevo.com` |
| `PEDIDOS_SMTP_PORTA` | `587` (não use a 465: o sistema só fala STARTTLS) |
| `PEDIDOS_SMTP_USUARIO` | o **login SMTP** que a Brevo mostra |
| `PEDIDOS_SMTP_SENHA` | a **chave SMTP** do passo 6 |
| `PEDIDOS_SMTP_REMETENTE` | `pedidos@avisos.grupodluz.com` |

Dois cuidados: (1) a chave fica numa variável do sistema, que **qualquer usuário deste PC consegue ler**: use só esta chave (de uso exclusivo do sistema); se vazar, apague-a na Brevo e gere outra; (2) **não ative** na Brevo o bloqueio de "endereços IP não autorizados" a menos que o PC tenha IP fixo: o IP de internet de um PC comum muda e o envio pararia.

**E. Testar, antes de usar o sistema.** Abra um **novo** PowerShell (para enxergar as variáveis) e rode, trocando o endereço pelo seu:

```powershell
cd C:\PedidosAprovacao
.\.venv\Scripts\python.exe scripts\testar_email.py voce@grupodluz.com
```

**O que você deve ver:** a configuração usada (servidor, porta, login, remetente e "Senha/chave: definida", **nunca o valor da chave**) e `OK: o e-mail de teste foi enviado`. Abra a caixa de entrada (e a pasta **Lixo/Spam**): o e-mail "Teste de envio - Aprovação de Pedidos" deve estar lá. Se der erro, o script **não mostra traceback**: diz em português o que conferir (login recusado, remetente não verificado, porta bloqueada, servidor não encontrado...). Quando chegar, o servidor do sistema já está pronto: faça o teste do passo 11 e veja se os e-mails dos avisos chegam aos gestores.

Se os primeiros e-mails caírem em **Lixo** no Microsoft 365, peça ao administrador do Microsoft 365 para liberar o remetente `pedidos@avisos.grupodluz.com`. O sistema tenta de novo um aviso que falhou até 5 vezes, com espera crescente; depois disso ele fica como erro, sem alerta: de vez em quando olhe o painel da Brevo (e o `pedidos.log`).

**O que você deve ver** (conferir as variáveis): abra um **novo** PowerShell (variáveis só chegam a janelas abertas depois da mudança) e digite `echo $env:PEDIDOS_DATA_DIR`. Deve aparecer `C:\PedidosDados`.

> **Regra de ouro:** depois de mudar qualquer variável do sistema, **reinicie o PC** antes de confiar na tarefa automática. O Agendador de Tarefas só passa a enxergar variáveis novas depois de reiniciar. (Programas abertos manualmente num PowerShell novo já enxergam na hora.)

---

## 5. Criar o primeiro administrador — e um segundo, reserva

O administrador cria os demais usuários e redefine senhas. Crie **dois** administradores, com e-mails diferentes: se um esquecer a senha, o outro resolve.

Num **novo** PowerShell, dentro da pasta do projeto (passo 3):

```powershell
.\.venv\Scripts\python.exe scripts\criar_admin.py
```

Antes de perguntar qualquer coisa, o programa mostra a pasta que vai usar: `Pasta de dados: C:\PedidosDados`. Se não for essa, feche esta janela do PowerShell sem responder e confira o passo 4. Depois ele pergunta, uma coisa por vez: **Nome**, **E-mail**, **Senha** e **Repita a senha**. Enquanto você digita a senha **nada aparece na tela** (nem asteriscos): é normal. A senha precisa ter **no mínimo 10 caracteres**; use uma frase longa.

**O que você deve ver:** `Administrador fulano@empresa.com criado. Já é possível entrar no sistema.` E a pasta `C:\PedidosDados` agora contém `pedidos.db`, `pdfs` e `secret.key`. Se, em vez disso, aparecer `Erro: a variável de ambiente PEDIDOS_DATA_DIR não está definida` e o programa terminar sem perguntar nada (código de saída 1), a variável não foi vista por este PowerShell: nada foi criado; confira o passo 4 e abra **outro** PowerShell (variáveis novas só chegam a janelas abertas depois delas).

Repita o comando para o **segundo administrador**, com **outro e-mail** (de preferência de outra pessoa de confiança). Guarde as duas senhas em local seguro.

### Se alguém esquecer a senha de administrador

Se o outro administrador ainda consegue entrar, ele abre a tela **Usuários** e clica em **"Redefinir senha"** ao lado do nome de quem esqueceu (o sistema mostra uma senha provisória, que a pessoa troca no primeiro acesso). Se **nenhum** administrador conseguir entrar, no PC:

```powershell
.\.venv\Scripts\python.exe scripts\criar_admin.py --redefinir
```

Pergunta o e-mail do administrador e a nova senha (duas vezes). Troca a senha, destrava a conta se ela estiver bloqueada por tentativas erradas e derruba as sessões abertas dele. Só funciona para administrador ativo que já existe; para os demais usuários use a tela Usuários.

Último recurso, **se tudo isso falhar**: criar um administrador novo com `criar_admin.py` (sem `--redefinir`), usando um e-mail que ainda não existe. Isso sempre funciona. **Não recomendamos mexer à mão no arquivo do banco de dados** (nem com programas de banco de dados): é fácil estragar o histórico, e não há comando pronto para isso, porque a senha precisa ser gravada de um jeito que só o sistema sabe fazer.

---

## 6. Primeira execução, na mão

Antes de automatizar, rode o servidor na mão para ver se tudo funciona. Use o PowerShell do passo 5 (ou abra outro, dentro de `C:\PedidosAprovacao`, para enxergar as variáveis do passo 4). O endereço local usa `http` (sem cadeado), e o cookie de login do sistema é marcado como "seguro" (só deve trafegar por `https`), o que alguns navegadores recusam em `http`. Para esse **teste local apenas**, ligue uma exceção **somente nesta janela** do PowerShell:

```powershell
$env:PEDIDOS_COOKIE_INSECURE = "1"
.\scripts\iniciar_pedidos.bat
```

Essa exceção vale só para essa janela do PowerShell: ao fechá-la, ela some. Nunca a coloque nas variáveis do sistema.

**O que você deve ver** no PowerShell, linhas como:

```
... INFO Aprovação de Pedidos iniciando
... INFO Python 3.14.x em Windows-11-...
... INFO Pasta de dados: C:\PedidosDados
... INFO EMAIL_MODE: desligado
... INFO BASE_URL: http://localhost:8000
... WARNING ATENÇÃO: PEDIDOS_COOKIE_INSECURE está ligado. ...
... WARNING ATENÇÃO: PEDIDOS_BASE_URL (http://localhost:8000) não começa com https:// ...
... INFO AVISO (informativo): o envio de e-mail está desligado; ...
... INFO Escutando em http://127.0.0.1:8000 (só este computador; ...)
... INFO Servidor pronto. Para encerrar, pressione Ctrl+C.
```

Os dois avisos `WARNING` são esperados agora (o `AVISO (informativo)` também: o e-mail fica desligado por enquanto, passo 4.5). A linha "Pasta de dados" **precisa** mostrar `C:\PedidosDados`. Se o Windows perguntar sobre o Firewall para o Python, clique em **Cancelar** (o sistema só escuta neste PC e não precisa de liberação).

Abra o navegador (neste mesmo PC) em <http://127.0.0.1:8000>.

**O que você deve ver:** a tela **"Entrar"** (e-mail e senha). Entre com o primeiro administrador: aparece a lista de pedidos (vazia) e, no menu, o item **Usuários**. Clique em **Sair**.

Para parar o servidor: volte ao PowerShell e aperte **Ctrl+C** (se o Windows perguntar "Deseja finalizar o arquivo em lotes (S/N)?", digite **S** e Enter); aparece `Servidor encerrado.` Feche a janela do PowerShell.

O registro de tudo que acontece fica em `C:\PedidosDados\logs\pedidos.log`.

---

## 7. Criar os usuários do sistema (uma vez)

Com o servidor rodando (na mão, como no passo 6 ou 8.3, ou já pela tarefa do passo 9), entre como administrador, abra **Usuários** e crie as pessoas: nome, e-mail e perfil (**solicitante**, **gestor**, **fiscal**, **admin**). O sistema mostra **uma vez** uma senha provisória: entregue-a à pessoa por um canal seguro; ela é obrigada a trocar no primeiro acesso. Depois disso, qualquer pessoa (administradores inclusive) troca a própria senha quando quiser pelo item **Trocar senha** do menu. O **administrador** também pode aprovar e reprovar pedidos (como o gestor) e fazer o trabalho do fiscal, mas não cria pedido nem confirma envio. Para o teste do passo 11 crie pelo menos um usuário de cada perfil: um solicitante, um gestor e um fiscal.

---

## 8. Publicar na internet com o Tailscale Funnel

O Funnel faz o endereço `https://...ts.net` chegar ao servidor do seu PC. O HTTPS (cadeado) é feito pelo próprio Tailscale; o sistema recebe os pedidos em `http://127.0.0.1:8000`.

> **Confirme na documentação atual.** Os nomes dos menus do painel do Tailscale e a sintaxe do comando `tailscale funnel` mudam entre versões. O que está abaixo é o que se sabe na data de escrita; **confira em <https://tailscale.com/kb/1223/funnel>** na hora da instalação e siga a documentação se houver diferença.

### 8.1 No painel do Tailscale (`login.tailscale.com/admin`, como administrador)

1. Aba **DNS**: confirme que o **MagicDNS** está ligado e clique em **"Enable HTTPS"** (habilitar certificados HTTPS) para o seu tailnet. Anote o nome do tailnet (algo como `tail1234.ts.net`).
2. Permissão do Funnel: na aba **Access controls** (ou no ajuste de Funnel que o painel oferecer), o Funnel precisa estar permitido para esta máquina (na documentação aparece como o atributo `funnel` do tailnet).
3. Aba **Machines**: encontre este PC, abra o menu `...` e escolha **"Disable key expiry"** (desativar expiração da chave). Sem isso, o PC é desconectado do Tailscale a cada ~180 dias e o sistema sai do ar sem aviso.
4. No ícone do Tailscale no PC (perto do relógio), confirme que a opção de rodar **sem usuário logado** ("Run unattended", em Preferences) está ligada. Sem isso, depois de um reinício o Tailscale só conecta quando alguém fizer login.

### 8.2 No PC, publicar a porta 8000

Num PowerShell (se reclamar de permissão, abra o PowerShell como administrador: botão direito > "Executar como administrador"):

```powershell
tailscale funnel --bg 8000
```

`--bg` deixa o Funnel configurado de forma permanente (continua depois de reiniciar). Para ver o estado: `tailscale funnel status`.

**O que você deve ver:** algo como

```
Available on the internet:

https://nome-do-pc.tail1234.ts.net/
|-- / proxy http://127.0.0.1:8000
```

O endereço `https://nome-do-pc.tail1234.ts.net` (o nome do PC, mais o nome do tailnet, mais `.ts.net`) é o **endereço público do sistema**. Anote-o.

### 8.3 Dizer ao sistema qual é o endereço

Edite a variável do sistema `PEDIDOS_BASE_URL` (passo 4.3, escolha a variável e clique em "Editar...") e coloque o endereço do Funnel, **sem barra no final**, por exemplo:

```
https://nome-do-pc.tail1234.ts.net
```

Abra um **novo** PowerShell (para enxergar a variável nova) e inicie o servidor na mão **sem** a exceção do passo 6:

```powershell
cd C:\PedidosAprovacao
.\scripts\iniciar_pedidos.bat
```

**O que você deve ver:** `BASE_URL: https://nome-do-pc.tail1234.ts.net` no registro e **nenhum** `WARNING`. Agora, no navegador, abra o **endereço do Funnel**: aparece a tela "Entrar" e o cadeado. Entre com o administrador. Se aparecer "Formulário inválido" ao entrar, veja o passo 14.

**Conferência importante (o login funcionar não basta):** sem o cabeçalho `X-Forwarded-Proto: https` o login também funciona, só que com as proteções de HTTPS reduzidas, e ninguém perceberia. Por isso, depois do primeiro login pelo endereço do Funnel, abra `C:\PedidosDados\logs\pedidos.log` no Bloco de Notas e confira:

- **deve existir** a linha `INFO Primeira requisição via proxy HTTPS recebida (X-Forwarded-Proto=https)`;
- **não deve existir** nenhuma linha `WARNING` dizendo que o pedido veio "sem X-Forwarded-Proto". Se existir, veja o passo 14.

Pare o servidor com **Ctrl+C** (se o Windows perguntar "Deseja finalizar o arquivo em lotes (S/N)?", digite **S** e Enter).

---

## 9. Início automático: Agendador de Tarefas

O servidor precisa subir sozinho quando o PC liga, sem ninguém fazer login, e voltar sozinho se cair. O Agendador de Tarefas **não é um vigia**: a opção "reiniciar se falhar" só vale para uma tarefa que nem conseguiu começar, não para um programa que já estava rodando e morreu. Por isso a tarefa do servidor terá **dois gatilhos**: um ao ligar o PC e outro que tenta iniciar a tarefa **a cada 5 minutos, para sempre**. Como a tarefa está configurada para **não iniciar uma nova instância** se já houver uma rodando, a tentativa é ignorada enquanto o servidor está vivo; se ele tiver morrido, volta em até 5 minutos. Duas tarefas:

- **"Pedidos - Servidor"**: roda `scripts\iniciar_pedidos.bat` na inicialização do PC.
- **"Pedidos - Backup"**: roda `scripts\backup_pedidos.bat` todo dia às 02:00.

**Antes de começar:** pare o servidor manual, se ainda estiver aberto (aperte **Ctrl+C**; se o Windows perguntar "Deseja finalizar o arquivo em lotes (S/N)?", digite **S** e Enter), e **reinicie o PC agora**, se você criou ou mudou variáveis do sistema (passos 4 e 8.3) e ainda não reiniciou. Sem isso, a tarefa pode não enxergar `PEDIDOS_DATA_DIR`; nesse caso o servidor **se recusa a subir** e mostra uma mensagem de erro (veja o passo 14), em vez de abrir um banco vazio.

Use uma conta do Windows **com senha** (conta sem senha não consegue rodar tarefa "sem estar conectado") e a **mesma conta que instalou o Python** (o ambiente `.venv` depende da pasta do Python dessa conta).

### 9.1 Tarefa "Pedidos - Servidor" (pelo mouse)

1. Menu Iniciar > digite **Agendador de Tarefas** > abra.
2. Menu da direita: **"Criar Tarefa..."** (**não** "Criar Tarefa Básica", que não tem as opções abaixo).
3. Aba **Geral**:
   - Nome: `Pedidos - Servidor`
   - Marque **"Executar estando o usuário conectado ou não"**.
   - Deixe **desmarcado** "Executar com privilégios mais altos" (não é necessário).
   - Em "Configurar para", pode deixar "Windows 10" (vale para o 11).
4. Aba **Gatilhos**, **dois** gatilhos:
   - **Novo...** > em "Iniciar a tarefa", escolha **"Na inicialização"** > OK.
   - **Novo...** > em "Iniciar a tarefa", escolha **"Em um agendamento"**, marque **"Diariamente"** (a cada 1 dia), início hoje às 00:00:00 > em "Configurações avançadas" marque **"Repetir a tarefa a cada: 5 minutos"**, "por uma duração de: **Indefinidamente**" > confira que **"Habilitado"** está marcado > OK.
5. Aba **Ações** > **Novo...**:
   - Ação: "Iniciar um programa"
   - Programa/script: `C:\PedidosAprovacao\scripts\iniciar_pedidos.bat`
   - Iniciar em (opcional): `C:\PedidosAprovacao`
   - OK.
6. Aba **Condições**: **desmarque** "Iniciar a tarefa somente se o computador estiver ligado à energia CA" (e "Interromper se o computador passar a funcionar com bateria").
7. Aba **Configurações**:
   - Marque "Permitir que a tarefa seja executada sob demanda".
   - Em "Se a tarefa já estiver em execução, aplicar a seguinte regra": **"Não iniciar uma nova instância"** (**essencial**: é o que impede o gatilho de 5 em 5 minutos de abrir um segundo servidor).
   - Marque também, como reforço, **"Se a tarefa falhar, reiniciar a cada: 1 minuto"** e **"Tentar reiniciar até: 3 vezes"**. Isso é só um extra: quem de fato traz o servidor de volta é o gatilho de 5 minutos.
   - **Desmarque** "Interromper a tarefa se ela for executada por mais de: 3 dias". (Sem tempo limite!)
8. Clique **OK**. O Windows pede a **senha da conta**: é a senha do Windows (ou da conta Microsoft, se você entra no PC com um e-mail da Microsoft), **não** o PIN nem a digital.

**O que você deve ver:** a tarefa aparece na lista "Biblioteca do Agendador de Tarefas". Clique com o botão direito nela > **Executar**. Depois de alguns segundos, atualize (F5): a coluna Status mostra **"Em execução"** (e, na aba Gatilhos da tarefa, aparecem os dois gatilhos). No navegador, o endereço do Funnel mostra a tela "Entrar", e em `C:\PedidosDados\logs\pedidos.log` há as linhas de início.

### 9.2 Tarefa "Pedidos - Backup" (pelo mouse)

Mesmos passos, com estas diferenças:

- Nome: `Pedidos - Backup`; também "Executar estando o usuário conectado ou não".
- Gatilho: **"Diariamente"**, início às **02:00:00**, repetir a cada 1 dia.
- Ação: Programa/script `C:\PedidosAprovacao\scripts\backup_pedidos.bat`; Iniciar em `C:\PedidosAprovacao`. O campo "Adicione argumentos" fica vazio (só se quiser mudar o `--manter`, passo 12).
- Condições: desmarque a exigência de energia CA.
- Configurações: marque **"Executar a tarefa assim que possível após uma inicialização agendada ter sido perdida"** (se o PC estiver desligado às 02:00, ela roda quando ligar). Pode deixar o limite de tempo em 1 hora.

**O que você deve ver:** clique com o botão direito > **Executar**. Em poucos segundos aparece uma pasta nova em `D:\BackupsPedidos`, com nome `aprovacao_AAAAMMDD_HHMMSS`, contendo `pedidos.db` e a pasta `pdfs`. Na tarefa, "Último resultado da execução" mostra `0x0` (sucesso). Se o resultado for outro, rode `scripts\backup_pedidos.bat` na mão num PowerShell para ver a mensagem de erro.

### 9.3 Alternativa pela linha de comando (`schtasks`) — NÃO TESTADA

Só use se preferir comandos ao mouse. Atenção: o comando do servidor abaixo cria **só o gatilho que repete a cada 5 minutos** (o segundo da lista do passo 9.1); o gatilho "Na inicialização" e a regra "não iniciar uma nova instância" só dá para configurar pelo mouse: abra a tarefa depois e faça-os (passo 9.1). **A sintaxe não foi testada** (será conferida junto, no passo 16). Troque `NOME_DO_USUARIO` pela conta do Windows. Rode no PowerShell como administrador; o `/RP *` faz o Windows pedir a senha.

```powershell
schtasks /Create /TN "Pedidos - Servidor" /TR "C:\PedidosAprovacao\scripts\iniciar_pedidos.bat" /SC MINUTE /MO 5 /RU "NOME_DO_USUARIO" /RP * /F

schtasks /Create /TN "Pedidos - Backup" /TR "C:\PedidosAprovacao\scripts\backup_pedidos.bat" /SC DAILY /ST 02:00 /RU "NOME_DO_USUARIO" /RP * /F
```

**Cuidado:** tarefas criadas assim vêm com **limite de 72 horas** de execução e **sem** "reiniciar em caso de falha". Depois de criar a tarefa do servidor, abra-a no Agendador de Tarefas e faça o que falta do passo 9.1: o gatilho "Na inicialização", na aba **Configurações** a regra "Não iniciar uma nova instância" e a desmarcação do limite de tempo (e, de reforço, reiniciar a cada 1 minuto até 3 vezes). O comando `schtasks` não tem opção para isso.

### 9.4 Reiniciar o PC para testar

Pare o servidor manual, se ainda estiver aberto (aperte **Ctrl+C**; se o Windows perguntar "Deseja finalizar o arquivo em lotes (S/N)?", digite **S** e Enter). Reinicie o PC (**Iniciar > Reiniciar**) e **não faça login**: espere 2 minutos e, de outro aparelho (o celular com 4G), abra o endereço do Funnel.

**O que você deve ver:** a tela "Entrar", sem ninguém ter feito login no PC. Depois, ao entrar no PC e abrir `C:\PedidosDados\logs\pedidos.log`, as linhas de início têm a hora da reinicialização e mostram `Pasta de dados: C:\PedidosDados`.

**Teste de queda (o servidor volta sozinho?).** Com o sistema funcionando, abra o Gerenciador de Tarefas (Ctrl+Shift+Esc) > aba **Detalhes**: aparecem **dois** `python.exe` (o ambiente `.venv` do Windows abre um processo "lançador" que chama o Python de verdade). Selecione qualquer um dos dois > **Finalizar tarefa**; o outro cai junto (se sobrar um, finalize-o também). A tela do Funnel deixa de abrir. **Espere até 5 minutos**: o gatilho repetido inicia a tarefa de novo, a tela "Entrar" volta e `pedidos.log` ganha uma nova linha `Aprovação de Pedidos iniciando`. Se não voltar em 5 minutos, o gatilho repetido está errado (passo 9.1, item 4).

---

## 10. Configurações de energia e do Windows Update

O servidor só funciona enquanto o PC está ligado e acordado.

1. **Nunca suspender.** Configurações > Sistema > Energia e bateria > Tela e suspensão: em "Quando conectado, colocar o dispositivo em suspensão após", escolha **Nunca**. (O desligamento da tela pode continuar.)
2. **Desativar a inicialização rápida.** Painel de Controle > Opções de Energia > "Escolher a função dos botões de energia" > "Alterar configurações não disponíveis no momento" > **desmarque "Ativar inicialização rápida"**. Por quê: com ela, "Desligar" não desliga de verdade (guarda o estado do sistema), o que pode atrapalhar tarefas "na inicialização" e variáveis novas. Escolha conservadora: sem ela, ligar o PC é sempre uma partida completa e previsível.
3. **Windows Update.** Configurações > Windows Update > Opções avançadas: defina o **Horário de atividade** cobrindo o expediente (por exemplo 06:00 às 22:00) para o PC não reiniciar durante o uso. O Windows reinicia sozinho fora desse horário depois de atualizações. Isso é aceitável **porque** a tarefa do passo 9 sobe o servidor na inicialização, sem login, e o Tailscale também (item "sem usuário logado", passo 8.1).
4. **Voltar sozinho depois de uma queda de energia.** Na configuração da BIOS/UEFI do PC (tecla F2, Del ou similar ao ligar), procure "Restore on AC Power Loss" / "After Power Failure" e escolha **"Power On"**. Se não achar, deixe para conferir depois.

**O que você deve ver:** PC sem entrar em suspensão em horas de ociosidade; depois de qualquer reinício, o sistema volta sozinho em poucos minutos.

---

## 11. Teste de ponta a ponta pelo celular (4G, Wi-Fi desligado)

Este teste confirma o que importa: funciona de fora da empresa, em celulares reais.

1. No celular, **desligue o Wi-Fi** (use os dados móveis, 4G/5G).
2. **Solicitante**: abra o endereço do Funnel e entre (troque a senha provisória no primeiro acesso). Em **Novo pedido** preencha Cliente, Valor, Descrição e escolha um **PDF** (o seletor do celular oferece arquivos/Drive). **Enviar para aprovação**. Abra o pedido e toque em **Abrir PDF**: o PDF deve abrir.
3. **Gestor**: entre (outro aparelho, ou **Sair** e entre de novo). Abra o pedido, **Abrir PDF** (deve abrir) e toque em **Aprovar**.
4. **Solicitante**: o pedido aparece aprovado. **Abrir PDF**; em "Data do pedido" deixe a data de hoje e toque em **Confirmar envio**.
5. **Fiscal**: abra o pedido (enviado), **Abrir PDF**, escreva o "Número da nota fiscal" (qualquer número de teste) e toque em **Informar nota fiscal**.
6. **Fiscal**: no mesmo pedido, **Abrir PDF** e toque em **Marcar chegada** (data de hoje). O pedido fica "Recebido".
7. **Fiscal**: toque em **Arquivar**.
8. Em **cada** passo acima, role até **Histórico** e confira: o passo que você acabou de fazer aparece, com o **nome do usuário certo** e a **hora de São Paulo** (compare com o relógio do celular), na ordem. No fim o histórico mostra: criar, aprovar, confirmar envio, informar nota fiscal, marcar chegada e arquivar.

Faça o fluxo completo (itens 2 a 8) com um dos celulares. Além disso, nos **dois** celulares, um **iPhone (Safari)** e um **Android (Chrome)**, abra o PDF pelo menos nos itens 2 e 3.

**O que você deve ver:** o PDF abre no iPhone (o Safari mostra na tela) e no Android. No Android, o Chrome pode baixar o PDF e abrir com o aplicativo de PDF do aparelho (Drive, Arquivos...): isso é aceitável **se o arquivo abrir e mostrar o conteúdo**.

> A visualização do PDF só foi verificada num navegador de computador (Chromium), com a política de segurança do sistema ligada. **Se o PDF não abrir no iPhone ou no Android** (tela em branco, erro, nada acontece), anote o aparelho, o navegador e o que apareceu e avise quem desenvolveu o sistema: é um defeito a corrigir, não um erro seu.

---

## 12. Backups

O que existe: a tarefa "Pedidos - Backup" (passo 9.2) cria, todo dia às 02:00, uma pasta `D:\BackupsPedidos\aprovacao_AAAAMMDD_HHMMSS` com:

- `pedidos.db`: cópia consistente do banco, conferida depois de copiada;
- `pdfs\`: todos os PDFs.

Regras:

- **A pasta de backup deve ficar em outro disco ou em outra máquina** (disco externo, pasta de rede), com o acesso restrito pelo `icacls` do passo 4.1, item 5. Backup no mesmo disco não protege contra falha do disco. Se o destino for uma pasta de rede, o `PEDIDOS_BACKUP_DESTINO` pode ser algo como `\\servidor\backups\pedidos`, mas a conta da tarefa precisa ter acesso a ela (confira no passo 16).
- Por padrão são guardados os **30 backups mais novos**; os mais antigos são apagados automaticamente. Para guardar outra quantidade (por exemplo 60), edite a ação da tarefa "Pedidos - Backup" e, no campo **"Adicione argumentos (opcional)"**, escreva `--manter 60`. Pela linha de comando o equivalente é `.\.venv\Scripts\python.exe scripts\backup.py D:\BackupsPedidos --manter 60`.
- A `secret.key` **não entra no backup**. Se ela se perder, o único efeito é que todo mundo precisa fazer login de novo.

### Como restaurar (computador novo ou dados estragados)

1. No Agendador de Tarefas, clique com o botão direito em **"Pedidos - Servidor"** > **Desabilitar**, e só depois, de novo com o botão direito > **Finalizar**. A ordem importa: o gatilho de 5 em 5 minutos (passo 9.1) religaria o servidor no meio da restauração, recriando `C:\PedidosDados` com as permissões abertas, um banco vazio e uma chave nova. **Confirme que nenhum `python.exe` sobrou**: no Gerenciador de Tarefas (Ctrl+Shift+Esc, aba Detalhes) ou, num PowerShell, `tasklist | findstr python` (não deve listar nada; se listar, finalize o processo). Um processo antigo continuaria dono da porta 8000 e do banco, e o novo não conseguiria subir.

   **O que você deve ver:** a coluna Status da tarefa mostra **"Desabilitado"** e `tasklist | findstr python` não mostra nada.
2. Renomeie `C:\PedidosDados` para `C:\PedidosDados_antes_da_restauracao` (guarda o estado estragado, caso seja útil).
3. Crie uma pasta nova `C:\PedidosDados` e **reaplique nela o comando `icacls` da pasta de dados** (passo 4.1, item 3, no Prompt de Comando como administrador): a pasta nova não herda as permissões da antiga.
4. Da pasta do backup escolhido (por exemplo `D:\BackupsPedidos\aprovacao_20261023_020001`), copie para `C:\PedidosDados`: o arquivo **`pedidos.db`** e a pasta **`pdfs`**.
5. (Opcional) Copie também `secret.key` da pasta renomeada: assim as pessoas continuam logadas. Se não copiar, o sistema cria uma chave nova e todos entram de novo.
6. **Por último**, clique com o botão direito em **"Pedidos - Servidor"** > **Habilitar** e, em seguida, > **Executar** (para não esperar o gatilho de 5 minutos).

**O que você deve ver:** a tarefa volta a "Em execução", o sistema abre, o login funciona e os pedidos do backup aparecem, com os PDFs abrindo.

### Lembrete: teste a restauração a cada 3 meses

Um backup que nunca foi restaurado pode estar inútil sem ninguém saber. Todo trimestre, sem parar o sistema real, num PowerShell novo:

```powershell
mkdir C:\TesteRestauracao
copy D:\BackupsPedidos\aprovacao_XXXXXXXX_XXXXXX\pedidos.db C:\TesteRestauracao\
xcopy D:\BackupsPedidos\aprovacao_XXXXXXXX_XXXXXX\pdfs C:\TesteRestauracao\pdfs\ /E /I
$env:PEDIDOS_DATA_DIR = "C:\TesteRestauracao"
$env:PEDIDOS_PORTA = "8001"
$env:PEDIDOS_COOKIE_INSECURE = "1"
cd C:\PedidosAprovacao
.\scripts\iniciar_pedidos.bat
```

(troque `aprovacao_XXXXXXXX_XXXXXX` pelo nome da pasta de backup mais recente). Abra <http://127.0.0.1:8001>, entre, abra um pedido e o PDF dele. Para parar, **Ctrl+C** (se o Windows perguntar "Deseja finalizar o arquivo em lotes (S/N)?", digite **S** e Enter), feche o PowerShell e apague `C:\TesteRestauracao`. Aproveite para conferir se há uma pasta de backup nova a cada dia.

---

## 13. Atualizar o sistema mais tarde

1. Faça um backup: Agendador de Tarefas > "Pedidos - Backup" > **Executar**.
2. "Pedidos - Servidor" > **Desabilitar**, e só depois > **Finalizar** (nessa ordem: senão o gatilho de 5 em 5 minutos religa o servidor com o código copiado pela metade). **Confirme que nenhum `python.exe` sobrou** (Gerenciador de Tarefas, aba Detalhes, ou `tasklist | findstr python` num PowerShell: não deve listar nada); um processo antigo continuaria dono da porta 8000 e do banco. **O que você deve ver:** Status "Desabilitado" e nenhum `python.exe`.
3. Copie os arquivos novos por cima de `C:\PedidosAprovacao` (como administrador), **sem** apagar a pasta `.venv` e sem trazer `data`, `.git` nem `scripts\dados_demo.py` (como no passo 2; se ele vier junto, apague-o). Os dados estão em `C:\PedidosDados`, fora do programa, e não são afetados.
4. Num PowerShell **aberto como administrador** (a sua conta só lê a pasta do programa, passo 4.1), dentro de `C:\PedidosAprovacao`: `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`. (Opcional: `.\.venv\Scripts\python.exe -m pytest -q` deve terminar sem `failed`; `skipped` é normal, passo 3.)
5. **Por último**: "Pedidos - Servidor" > **Habilitar** e, em seguida, > **Executar**.

**O que você deve ver:** a tarefa "Em execução", `Servidor pronto` no final de `C:\PedidosDados\logs\pedidos.log`, e o sistema abre pelo endereço do Funnel com os pedidos de antes.

---

## 14. Problemas comuns

O registro fica em `C:\PedidosDados\logs\pedidos.log` (e os antigos em `pedidos.log.1` a `.5`). Abra com o Bloco de Notas: é o primeiro lugar a olhar.

| Sintoma | O que verificar |
|---|---|
| A página não abre de fora (celular, 4G) | 1) O PC está ligado e com internet? 2) Tarefa "Pedidos - Servidor" em execução? 3) `tailscale status` mostra o PC conectado? 4) `tailscale funnel status` mostra o endereço apontando para `http://127.0.0.1:8000`? 5) A chave do PC no Tailscale expirou (painel > Machines)? 6) Abre pelo próprio PC em `http://127.0.0.1:8000`? Se sim, o problema é o Funnel/Tailscale; se não, é o servidor. |
| Ao entrar aparece "Formulário inválido" ou "Sua sessão expirou" | Ao abrir pelo endereço do Funnel: o Funnel não está repassando o HTTPS como esperado, ou o endereço digitado não é o do Funnel (por exemplo, o do IP). Abra pelo endereço `https://...ts.net`, limpe os dados do site e tente de novo. Se persistir, avise o suporte: precisa conferir os cabeçalhos do Funnel (passo 16). |
| Entra, mas na mesma hora volta para "Entrar" | O cookie não está sendo aceito: confira se o endereço é `https://`. Se estiver testando por `http://127.0.0.1:8000`, falta a exceção do passo 6. |
| "database is locked" no registro / página de erro | Outro programa está com o banco aberto (um visualizador de SQLite, o antivírus, uma sincronização de nuvem). Feche os outros programas que abriram `pedidos.db`, confirme que `C:\PedidosDados` não está em pasta de rede/OneDrive e, se persistir, peça para excluir `C:\PedidosDados` da verificação em tempo real do antivírus. Quase sempre passa sozinho em segundos. |
| O PDF não abre | Abra o pedido: se aparecer "Arquivo PDF não encontrado", o arquivo sumiu de `C:\PedidosDados\pdfs` (restaure do backup). Se abre no computador e não no celular, anote aparelho/navegador e avise o suporte (passo 11). |
| Os e-mails não saem | Com `PEDIDOS_EMAIL_MODE=desligado` é o esperado: veja os avisos no item "Avisos". Com `smtp`: rode `scripts\testar_email.py` (passo 4.5, item E), que diz o que conferir; e procure por "Falha ao enviar" em `pedidos.log` (o motivo aparece lá, sem senhas). |
| Quero trocar a minha senha | Qualquer usuário logado: item **Trocar senha** do menu (pede a senha atual e a nova duas vezes). |
| Esqueci a senha | Qualquer administrador: tela **Usuários > Redefinir senha**. Se foi a de um administrador e nenhum entra: `.\.venv\Scripts\python.exe scripts\criar_admin.py --redefinir` (passo 5). Conta bloqueada por tentativas erradas: aguarde 15 minutos ou use a redefinição. |
| O PC reiniciou e ninguém consegue entrar | 1) Espere uns 5 minutos (o gatilho de 5 em 5 minutos pode ser quem sobe o servidor). 2) Veja a tarefa "Pedidos - Servidor": Status e "Último resultado da execução". 3) Veja o fim de `pedidos.log`. 4) **Se a tarefa não enxergou `PEDIDOS_DATA_DIR`**, o servidor se recusa a subir (de propósito, para não abrir um banco vazio no lugar errado): a tarefa fica com "Último resultado" diferente de `0x0` e não grava nada, nem registro, então o `pedidos.log` não explica. Para ver a mensagem, rode `.\scripts\iniciar_pedidos.bat` num PowerShell: aparece "a variável de ambiente PEDIDOS_DATA_DIR não está definida". Confira a variável (passos 4.3 e 4.4) e **reinicie o PC** (as variáveis do sistema só chegam à tarefa depois de reiniciar). 5) Se o `pedidos.log` tiver o aviso "a pasta de dados ... está dentro da pasta do programa", a variável foi definida com o caminho errado: corrija-a e reinicie; o banco real continua em `C:\PedidosDados`. 6) Veja se o Tailscale conectou sozinho (ícone; "Run unattended"). |
| A tarefa parou depois de eu trocar a senha do Windows | A tarefa guarda a senha da conta de quando foi criada. Se a senha da conta mudou, ela deixa de rodar até você a digitar de novo: Agendador de Tarefas > clique duas vezes na tarefa > aba Geral > **OK** (o Windows pede a senha nova; vale para as duas tarefas, servidor e backup). |
| O `pedidos.log` tem um `WARNING` dizendo que o pedido veio "sem X-Forwarded-Proto" | O Funnel não está avisando que a conexão é HTTPS. O login ainda funciona, mas algumas proteções ficam reduzidas. Confira `tailscale funnel status` (deve mostrar o `https://...` e `proxy http://127.0.0.1:8000`), atualize o Tailscale, refaça `tailscale funnel --bg 8000` e avise o suporte. |
| A porta 8000 está ocupada | O registro mostra "Não consegui abrir a porta 8000". O servidor já está rodando (talvez a tarefa e um PowerShell manual ao mesmo tempo) ou sobrou um `python.exe` antigo. Num PowerShell, `tasklist | findstr python` mostra quem é; finalize o que não deve estar lá. |
| O PowerShell diz que `.venv\Scripts\python.exe` não existe | O passo 3 não foi feito nesta pasta, ou o PowerShell não está dentro de `C:\PedidosAprovacao` (`cd C:\PedidosAprovacao`). |

---

## 15. Segurança, em linguagem simples

- **O sistema está na internet.** Qualquer pessoa no mundo que descobrir o endereço vê a tela de login. Por isso o login é a única barreira.
- **Senhas fortes**: no mínimo 10 caracteres (o sistema exige), de preferência uma frase longa. Nada de datas de aniversário ou o nome da empresa. Cada pessoa tem a sua; ninguém compartilha senha. Quem desconfiar que alguém viu a sua senha troca na hora, pelo item **Trocar senha** do menu.
- O sistema bloqueia a conta por 15 minutos depois de 5 senhas erradas e mostra sempre a mesma mensagem de erro, mas isso não substitui uma senha boa.
- **Quando alguém sair da empresa**, desative-o na tela **Usuários > Desativar** no mesmo dia. A pessoa perde o acesso na hora.
- **Não espalhe o endereço do Funnel**: só para quem usa. Não coloque em site, redes sociais ou grupos grandes.
- **Não abra portas no roteador** (port forwarding). O Funnel não precisa disso, e abrir uma porta seria um segundo caminho sem a proteção do Tailscale.
- Mantenha o **Windows** (Windows Update) e o **Tailscale** atualizados.
- Use o PC do servidor só para isso, se possível, e com conta do Windows protegida por senha.
- O backup contém os dados dos pedidos: guarde-o em local com acesso restrito.
- **Nunca rode `scripts\dados_demo.py` neste PC** (ele nem deve estar aqui, passo 2): ele cria um administrador `admin@demo.local` com senha pública. Se ele aparecer, apague-o.
- O servidor escuta somente em `127.0.0.1` (só o próprio PC); quem fala com o mundo é o Tailscale. Não existe opção para mudar isso, de propósito.

---

## 16. Checklist final: o que só pode ser conferido junto, no PC

O sistema foi desenvolvido e testado num Mac. Os comandos e as telas do Windows e do Tailscale deste guia **não foram executados** por quem escreveu. Confira estes itens, marcando um a um, na primeira instalação:

- [ ] Versão do Python instalada (3.12 ou mais nova) e `py -m venv .venv` + `pip install -r requirements.txt` funcionam (passos 1 e 3).
- [ ] O PowerShell aceita `.\.venv\Scripts\python.exe` e `.\scripts\iniciar_pedidos.bat` como escritos, e **Ctrl+C** encerra o servidor com a mensagem `Servidor encerrado.` (e como fica a pergunta "Deseja finalizar o arquivo em lotes (S/N)?" do `.bat`).
- [ ] O sistema só foi testado no Python 3.14 (num Mac): rodar `.\.venv\Scripts\python.exe -m pytest -q` no PC (PowerShell como administrador, por causa do passo 4.1) e ver tudo passar (nenhum `failed`; os `skipped` são os testes só de Mac/Linux e os de `dados_demo.py`).
- [ ] O arquivo `pedidos.log` abre com acentos corretos no Bloco de Notas (ele é gravado em UTF-8; o console do PowerShell pode mostrar `?` no lugar de algum caractere, sem problema).
- [ ] `iniciar_pedidos.bat` e `backup_pedidos.bat` rodam (os arquivos têm quebra de linha do Windows; as mensagens saem sem caracteres estranhos).
- [ ] **Agendador de Tarefas**: todas as opções do passo 9.1 existem com esses nomes; a tarefa sobe depois de **reiniciar sem ninguém fazer login**; os **dois gatilhos** do servidor (na inicialização e repetição de 5 em 5 minutos) funcionam com "não iniciar uma nova instância" (não surge um segundo servidor); **teste de queda**: mate um dos dois `python.exe` pelo Gerenciador de Tarefas, confira que o outro cai junto, e veja o servidor voltar em até 5 minutos; não para sozinha depois de 3 dias.
- [ ] **Desabilitar antes de Finalizar** (restauração e atualização, passos 12 e 13): com a tarefa "Pedidos - Servidor" **Desabilitada** e finalizada, espere 6 minutos e confira que o servidor **não** voltou (`tasklist | findstr python` vazio, Status "Desabilitado"); depois **Habilitar** + **Executar** o traz de volta.
- [ ] Sintaxe do `schtasks` do passo 9.3 (se for usado) e a conta da tarefa tem o direito de "Fazer logon como um trabalho em lotes".
- [ ] **Variáveis do SISTEMA** visíveis para a tarefa: o registro mostra `Pasta de dados: C:\PedidosDados` depois de reiniciar o PC.
- [ ] **Permissões (`icacls`, passo 4.1)**: os comandos funcionam como escritos (SIDs, aspas e parênteses no Prompt de Comando); `icacls C:\PedidosDados` e `icacls C:\PedidosAprovacao` listam só SYSTEM, Administradores e a conta da tarefa; a conta da tarefa consegue criar o banco, ler e gravar PDFs e gravar em `logs`, e rodar o programa só com leitura/execução; um usuário comum do PC **não** consegue abrir `C:\PedidosDados`; `icacls D:\BackupsPedidos` (passo 4.1, item 5) também lista só os três, a tarefa de backup consegue criar e apagar pastas lá e um usuário comum não consegue abri-la.
- [ ] A conta da tarefa enxerga a pasta do Python (o `.venv` depende dela) e a pasta de backup (se for de rede, com credenciais).
- [ ] **Sintaxe do Funnel** e passos do painel (passo 8) conforme a documentação do Tailscale da época: `tailscale funnel --bg 8000`; HTTPS habilitado; Funnel permitido; expiração de chave desativada; Tailscale conectando sem usuário logado.
- [ ] **Cabeçalhos do Funnel**: depois do primeiro login pelo endereço `https://...ts.net`, o `pedidos.log` tem a linha `Primeira requisição via proxy HTTPS recebida (X-Forwarded-Proto=https)` e **não** tem nenhum `WARNING` de "sem X-Forwarded-Proto". (O login funcionar sozinho não prova nada.) Se aparecer "Formulário inválido" ao entrar, também é isto.
- [ ] **PDF no iPhone (Safari) e no Android (Chrome)** abre (passo 11), com a política de segurança do sistema.
- [ ] Energia: nunca suspender; inicialização rápida desligada; horário de atividade do Windows Update; "Power On" depois de queda de energia (BIOS).
- [ ] A senha pedida ao criar as tarefas é a do Windows/conta Microsoft (não o PIN) e a tarefa roda com o PC sem login.
- [ ] O backup agendado roda às 02:00 (ou ao ligar o PC, se estava desligado) e deixa a pasta nova em `D:\BackupsPedidos`, com "Último resultado" `0x0`.
- [ ] A restauração de teste do passo 12 funciona.
- [ ] **E-mail (passo 4.5)**: `scripts\testar_email.py` mostra `OK` e o e-mail de teste chega à caixa de entrada (não ao Lixo); a porta 587 de saída não é bloqueada pela rede do PC; depois do teste do passo 11, os avisos chegam por e-mail aos gestores e ao fiscal.
- [ ] Antivírus/Defender não bloqueia nem trava `pedidos.db`.
