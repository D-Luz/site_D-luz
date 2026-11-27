@echo off
rem Cria na area de trabalho o atalho "Conferencia de Caixa", que abre o sistema
rem do servidor numa janela propria do Edge (ou do Chrome). Nada e instalado.
rem
rem Quem instala: escreva abaixo o IP ou o nome do PC servidor, uma vez, e deixe
rem esta pasta (criar_atalho.bat + criar_atalho.ps1) na pasta compartilhada.
rem Em cada maquina: dois cliques em criar_atalho.bat.
set SERVIDOR=
set PORTA=8200

if "%SERVIDOR%"=="" set /p SERVIDOR=IP ou nome do servidor do caixa (ex.: 192.168.0.60): 
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0criar_atalho.ps1" -Servidor "%SERVIDOR%" -Porta %PORTA%
echo.
pause
