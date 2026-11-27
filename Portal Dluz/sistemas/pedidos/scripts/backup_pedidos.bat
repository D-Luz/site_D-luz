@echo off
rem Faz o backup diario (usado pela tarefa "Pedidos - Backup").
rem A pasta de destino vem da variavel de ambiente PEDIDOS_BACKUP_DESTINO.
rem Argumentos extras (por exemplo --manter 60) sao repassados ao backup.py.
cd /d "%~dp0.."
if "%PEDIDOS_BACKUP_DESTINO%"=="" (
  echo ERRO: a variavel de ambiente PEDIDOS_BACKUP_DESTINO nao esta definida.
  echo Defina a pasta de backup conforme scripts\instalar_windows.md.
  exit /b 1
)
rem Uma barra no fim da pasta quebraria o argumento entre aspas.
if "%PEDIDOS_BACKUP_DESTINO:~-1%"=="\" (
  echo ERRO: PEDIDOS_BACKUP_DESTINO nao pode terminar com barra invertida.
  echo Exemplo certo: D:\BackupsPedidos
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo ERRO: nao encontrei .venv\Scripts\python.exe em %CD%.
  exit /b 1
)
".venv\Scripts\python.exe" scripts\backup.py "%PEDIDOS_BACKUP_DESTINO%" %*
exit /b %ERRORLEVEL%
