# Instalação no PC da loja (Windows)

O sistema roda num PC ligado o dia todo e é aberto pelo navegador nos outros computadores da rede da loja (não fica na internet). Comandos em caixa de código vão no **PowerShell**. Caminhos usados:

| O que | Onde |
|---|---|
| O programa | `C:\ConferenciaCaixa` |
| Os dados (banco) | `C:\CaixaDados` |
| Os backups | `D:\BackupsCaixa` (outro disco ou pasta de rede) |

O PC precisa enxergar os servidores do ATS (`192.168.0.250` Iluminação e `192.168.0.41` Elétrica, porta 3050), como os outros sistemas do grupo.

## 1. Python

Instale o Python 3.12 ou mais novo de <https://www.python.org/downloads/windows/> ("Windows installer (64-bit)"), marcando **"Add python.exe to PATH"** na primeira tela. Confira com `py --version`. (Se o PC já roda o Aprovação de Pedidos ou o CRM, o Python já está lá.)

## 2. Copiar o programa

Copie a pasta do projeto para `C:\ConferenciaCaixa` **sem** `.venv`, `__pycache__`, `.pytest_cache` e **sem `scripts\dados_demo.py`** (cria usuários com senha pública). No Mac, de dentro de `Programas`:

```bash
zip -r caixa.zip "Conferencia de Caixa" -x "*/.venv/*" "*/__pycache__/*" "*/.pytest_cache/*" "*.DS_Store" "*/scripts/dados_demo.py"
```

**Deve existir** `C:\ConferenciaCaixa\scripts\iniciar.py` (direto ali, não numa pasta dentro da pasta).

## 3. Ambiente e bibliotecas

Na pasta `C:\ConferenciaCaixa` (barra de endereço do Explorador > `powershell` > Enter):

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
```

**Deve terminar com** `passed` e nenhum `failed`.

## 4. Testar a leitura do ATS

```powershell
.\.venv\Scripts\python.exe scripts\verificar_ats.py ILU 2026-10-23
.\.venv\Scripts\python.exe scripts\verificar_ats.py ELE 2026-10-23
```

**Deve mostrar** os totais de dinheiro, cartão, depósito/PIX e cheque, e `OK: todas as formas identificadas.`

## 5. Primeiro administrador

```powershell
$env:CAIXA_DATA_DIR = "C:\CaixaDados"
.\.venv\Scripts\python.exe scripts\criar_usuario.py --admin
```

Os demais usuários (caixas e financeiro) são criados depois, pela tela **Usuários**: o sistema mostra uma senha provisória uma única vez, e a pessoa escolhe a dela no primeiro acesso.

## 6. Iniciar com o Windows

Agendador de Tarefas > **Criar Tarefa**:

- Nome: `Caixa - Servidor`; marque **"Executar estando o usuário conectado ou não"**.
- Disparador: **Ao iniciar o sistema**.
- Ação: Iniciar um programa > `C:\ConferenciaCaixa\scripts\iniciar_caixa.bat`.
- Configurações: desmarque "Interromper a tarefa se ela for executada por mais de"; marque "Se a tarefa falhar, reiniciar a cada 1 minuto".

Clique com o botão direito na tarefa > **Executar**. O `.bat` já usa `C:\CaixaDados`, escuta na rede (`0.0.0.0`) e na porta **8200**.

## 7. Liberar a porta na rede da loja

PowerShell **como administrador**:

```powershell
New-NetFirewallRule -DisplayName "Conferencia de Caixa" -Direction Inbound -Protocol TCP -LocalPort 8200 -Action Allow -Profile Private
```

Descubra o IP do PC com `ipconfig` (ex.: `192.168.0.60`). Teste de outro computador abrindo `http://192.168.0.60:8200` no navegador: deve aparecer a tela de login. O atalho definitivo das estações é o passo 8.

## 8. Atalho nas máquinas do caixa e do financeiro

Nas estações não se instala nada: só um atalho que abre o sistema numa janela própria do Edge (sem barra de endereço), com o ícone do caixa. Igual ao atalho do ERP, o programa continua só no servidor.

1. Abra `C:\ConferenciaCaixa\atalho\criar_atalho.bat` no Bloco de Notas e preencha o IP do servidor na linha `set SERVIDOR=` (ex.: `set SERVIDOR=192.168.0.60`). Salve.
2. Copie a pasta `atalho` (os dois arquivos) para a pasta compartilhada da rede, onde as estações já acessam o ERP.
3. Em cada máquina, dois cliques em `criar_atalho.bat`. Ele testa se o servidor responde e cria **Conferência de Caixa** na área de trabalho.

**O que você deve ver:** o atalho com o ícone do caixa; ao abrir, a tela de login numa janela sem barra de endereço. Se o IP do servidor mudar, rode o `.bat` de novo (ele substitui o atalho). Por isso vale fixar o IP do servidor no roteador.

## 9. Backup diário

Agendador de Tarefas > Criar Tarefa `Caixa - Backup`, diário às 20:00, ação `C:\ConferenciaCaixa\scripts\backup_caixa.bat`. Ele grava em `D:\BackupsCaixa` (troque com a variável `CAIXA_BACKUP_DESTINO`) e guarda os últimos 90.

Para restaurar: pare a tarefa `Caixa - Servidor`, copie o backup escolhido para `C:\CaixaDados\caixa.sqlite` e inicie a tarefa de novo.

## A conferir no PC (não testado no Windows)

O sistema foi desenvolvido e testado num Mac contra os servidores reais do ATS. Os `.bat`, o script do atalho, a tarefa agendada e a regra de firewall foram escritos para o Windows mas não puderam ser executados lá: confira os passos 6 a 9 junto com quem instalar.
