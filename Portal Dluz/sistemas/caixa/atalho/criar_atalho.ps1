# Chamado por criar_atalho.bat. So cria um atalho e copia um icone; nao instala nada.
param(
    [Parameter(Mandatory = $true)][string]$Servidor,
    [int]$Porta = 8200
)
$ErrorActionPreference = "Stop"
$Servidor = $Servidor.Trim()
$url = "http://${Servidor}:${Porta}"
# "Conferencia de Caixa" com acento, sem depender da codificacao deste arquivo.
$nome = "Confer" + [char]0x00EA + "ncia de Caixa"

Write-Host "Testando $url ..."
try {
    Invoke-WebRequest -Uri "$url/login" -UseBasicParsing -TimeoutSec 8 | Out-Null
} catch {
    Write-Host "ATENCAO: o servidor nao respondeu em $url." -ForegroundColor Yellow
    Write-Host "Confira o IP, se o PC servidor esta ligado e se a porta $Porta esta liberada no firewall dele."
    $resp = Read-Host "Criar o atalho mesmo assim? (s/n)"
    if ($resp -notmatch '^[sS]') { exit 1 }
}

# Navegador: Edge (vem com o Windows) ou, na falta dele, Chrome.
$candidatos = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$navegador = $candidatos | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $navegador) {
    Write-Host "ERRO: nao encontrei o Edge nem o Chrome neste PC." -ForegroundColor Red
    exit 1
}

# Icone guardado no proprio PC (o atalho continua com icone mesmo se a rede cair).
$pasta = Join-Path $env:LOCALAPPDATA "ConferenciaCaixa"
New-Item -ItemType Directory -Force -Path $pasta | Out-Null
$icone = Join-Path $pasta "caixa.ico"
try {
    Invoke-WebRequest -Uri "$url/static/caixa.ico" -UseBasicParsing -TimeoutSec 8 -OutFile $icone
} catch {
    $icone = $navegador  # sem o icone do sistema, usa o do navegador
}

$desktop = [Environment]::GetFolderPath("Desktop")
$caminho = Join-Path $desktop "$nome.lnk"
$shell = New-Object -ComObject WScript.Shell
$atalho = $shell.CreateShortcut($caminho)
$atalho.TargetPath = $navegador
$atalho.Arguments = "--app=$url"
$atalho.IconLocation = "$icone,0"
$atalho.Description = $nome
$atalho.WorkingDirectory = Split-Path $navegador
$atalho.Save()

Write-Host "Atalho criado: $caminho" -ForegroundColor Green
Write-Host "Ele abre $url numa janela propria."
