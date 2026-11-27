# Instalar o Portal Dluz no Windows

Este guia põe o portal para funcionar num PC da loja que fica ligado o dia todo. Os outros computadores, e quem tiver login, acessam pelo navegador. Siga os passos em ordem. Cada passo termina com **O que você deve ver**: se não aparecer isso, pare e resolva antes de seguir.

Tudo o que estiver em caixa de código vai no **PowerShell**: digite ou cole e aperte Enter. Os caminhos de exemplo são sempre estes:

| O que | Onde |
|---|---|
| O programa | `C:\PortalDluz` |
| Os dados (bancos, relatórios gerados, PDFs dos pedidos) | `C:\PortalDluzDados` |
| Os backups | `D:\BackupsPortal` (outro disco ou pasta de rede) |

> O PC precisa estar **na mesma rede dos servidores do ATS** (192.168.0.41, .200 e .250). Sem isso, os relatórios, o CRM e o Caixa não conseguem ler o ERP.

---

## 1. Instalar o Python

1. Baixe o **"Windows installer (64-bit)"** em <https://www.python.org/downloads/windows/>, versão **3.12 ou mais nova**. O portal foi testado no 3.14. Não use a versão da Microsoft Store.
2. Na **primeira tela** do instalador, marque **"Add python.exe to PATH"** e clique em **"Install Now"**.

Para conferir, abra o PowerShell (menu Iniciar > `PowerShell`) e rode:

```powershell
py --version
```

**O que você deve ver:** `Python 3.1x.x`, sem erro.

## 2. Copiar o programa

O jeito mais fácil é usar o arquivo **`Portal Dluz - pacote para a loja.zip`**, que já vem sem as pastas abaixo. No Windows, clique com o botão direito no `.zip` > "Extrair tudo" e copie o conteúdo da pasta `Portal Dluz` para `C:\PortalDluz`.

Se for copiar a pasta `Portal Dluz` direto, **não** leve junto:

- a pasta `.venv` (ela é criada no passo 3);
- a pasta `_demo_dados`, o arquivo `scripts\demo.py` e os `dados_demo.py` de `sistemas\pedidos\scripts`, `sistemas\crm\scripts` e `sistemas\caixa\scripts`. Eles só servem para demonstração, com senha pública.

Para gerar o pacote de novo num Mac, rode este comando dentro da pasta `Dluz`:

```bash
zip -r "Portal Dluz - pacote para a loja.zip" "Portal Dluz" -x "*/.venv/*" "*/_demo_dados/*" "*/__pycache__/*" "*/.pytest_cache/*" "*.DS_Store" "*/scripts/demo.py" "*/dados_demo.py" "*/.claude/*"
```

**O que você deve ver:** dentro de `C:\PortalDluz`, as pastas `portal`, `sistemas`, `scripts_originais`, `scripts` e `tests`, e o arquivo `requirements.txt`.

## 3. Criar o ambiente e instalar as bibliotecas

Abra o PowerShell **dentro** de `C:\PortalDluz`. No Explorador de Arquivos, abra a pasta, clique na barra de endereço, digite `powershell` e aperte Enter.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
```

**O que você deve ver:** o `pip` termina com `Successfully installed …`, e o `pytest` termina com `passed` e nenhum `failed`. Os testes usam dados de mentira e não acessam o ATS.

## 4. Pasta de dados e variáveis

Num PowerShell **como administrador** (botão direito no PowerShell > "Executar como administrador"), rode:

```powershell
mkdir C:\PortalDluzDados
setx DLUZ_DATA_DIR "C:\PortalDluzDados" /M
setx DLUZ_BACKUP_DESTINO "D:\BackupsPortal" /M
```

Variáveis opcionais, só se precisar:

| Variável | Para quê | Padrão |
|---|---|---|
| `DLUZ_FB_PASSWORD` | Senha do Firebird do ATS, se não for a padrão | `masterkey` |
| `DLUZ_FB_USER` | Usuário do Firebird | `SYSDBA` |
| `DLUZ_PORTA` | Porta do portal | `8000` |
| `DLUZ_HTTPS` | `1` só se o portal ficar atrás de HTTPS | (vazio) |
| `DLUZ_BASE_URL` | Endereço do portal, usado nos links dos e-mails do Pedidos | (vazio) |

Feche e abra o PowerShell de novo para as variáveis valerem.

### Já usa Pedidos, CRM ou Caixa? Leve os dados

O portal guarda os dados de cada sistema em `C:\PortalDluzDados\pedidos`, `\crm` e `\caixa`. Escolha um dos dois caminhos:

- **Copiar:** pare o sistema antigo e copie o conteúdo de `C:\PedidosDados` para `C:\PortalDluzDados\pedidos`. Faça o mesmo com a pasta do CRM (para `\crm`) e a do Caixa (para `\caixa`).
- **Apontar:** deixe as pastas onde estão e mantenha definidas as variáveis antigas `PEDIDOS_DATA_DIR`, `CRM_DATA_DIR` e `CAIXA_DATA_DIR`. O portal usa essas pastas.

Depois disso, **desative as tarefas antigas** do Agendador ("Pedidos - Servidor" e as do CRM e do Caixa). O portal já roda os três sistemas e faz a sincronização do CRM de hora em hora.

**Como o login antigo se liga ao novo:** no Pedidos, o usuário é achado pelo **e-mail**. No CRM e no Caixa, pelo **login**. Ao criar as pessoas no portal, use o mesmo e-mail e o mesmo login de antes, e cada uma continua com o perfil e o histórico que tinha.

## 5. Criar o primeiro administrador

```powershell
.\.venv\Scripts\python.exe scripts\criar_admin.py
```

**O que você deve ver:** a pasta de dados `C:\PortalDluzDados`, as perguntas (nome, login, e-mail e senha) e, no fim, `Administrador '…' criado.`

## 6. Rodar e testar

```powershell
.\.venv\Scripts\python.exe scripts\iniciar.py
```

**O que você deve ver:** `Portal Dluz no ar: http://192.168.0.X:8000`. Abra esse endereço no navegador do próprio PC e entre com o administrador. Depois, na tela do portal:

1. Vá em **Configurações** e clique em **Testar conexão**. As três empresas devem aparecer como "respondendo".
2. Abra **Imprimir orçamento** e gere um orçamento que exista, para confirmar a leitura do ATS.
3. Abra **Aprovação de Pedidos**, **CRM** e **Conferência de Caixa** no menu. Os três devem abrir sem pedir login de novo.

Para parar o portal, aperte `Ctrl+C`.

## 7. Liberar o acesso pelos outros computadores

No PowerShell como administrador:

```powershell
New-NetFirewallRule -DisplayName "Portal Dluz" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
```

**O que você deve ver:** de outro computador da loja, o endereço `http://192.168.0.X:8000` abre a tela de entrada do portal.

> Fixe o IP desse PC no roteador (reserva de DHCP), para o endereço não mudar.

## 8. Ligar sozinho com o Windows e fazer backup todo dia

Abra o **Agendador de Tarefas** e crie duas tarefas com **"Criar Tarefa…"** (não a "básica"):

| | Portal Dluz - Servidor | Portal Dluz - Backup |
|---|---|---|
| Geral | "Executar estando o usuário conectado ou não" e "Executar com privilégios mais altos" | igual |
| Disparador | "Na inicialização" | "Diariamente", às 22:00 |
| Ação: programa | `C:\PortalDluz\scripts\iniciar_portal.bat` | `C:\PortalDluz\scripts\backup_portal.bat` |
| Configurações | desmarque "Parar a tarefa se ela for executada por mais de…" | (padrão) |

**O que você deve ver:** depois de reiniciar o PC, o portal abre pelo navegador sem ninguém rodar nada. No dia seguinte aparece em `D:\BackupsPortal` uma pasta `portal_AAAAMMDD_HHMM`. Os backups com mais de 30 dias são apagados sozinhos.

## 9. Usuários

Em **Usuários** (menu de administração), crie cada pessoa. O portal mostra uma senha provisória, e a pessoa escolhe a dela no primeiro acesso. Na primeira vez que alguém abre um sistema, ele cria o cadastro dela lá:

| Quem | Pedidos | CRM | Caixa |
|---|---|---|---|
| Administrador do portal | admin | administrador | admin |
| Outros | solicitante | balcão | caixa |

Para mudar o perfil dentro de um sistema (gestor e fiscal no Pedidos, financeiro no Caixa, a empresa da caixa), use a tela de usuários do próprio sistema.

## 10. Acesso de fora da loja (opcional)

O portal foi feito para a rede da loja. Para abrir de fora, o caminho mais seguro é instalar o **Tailscale** nos aparelhos de quem precisa acessar. Assim o portal não fica público. Se mesmo assim decidir publicar na internet (por exemplo, pelo Tailscale Funnel, como o Pedidos fazia), defina `DLUZ_HTTPS=1` e use só senhas fortes. Lembre que aí os relatórios financeiros e de estoque também ficam acessíveis pela internet.

## O que não foi testado no Windows

O portal foi construído e testado num Mac, com um ATS de mentira. Confira junto, no primeiro dia:

- a leitura real do ATS em cada ferramenta (rode cada uma pelo menos uma vez);
- os passos 7 e 8 (firewall e Agendador);
- a **Proposta de orçamento** em PDF, que usa a fonte Arial do Windows quando as da pasta não servem.
