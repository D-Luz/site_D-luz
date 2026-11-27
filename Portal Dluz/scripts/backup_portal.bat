@echo off
rem Backup diario do Portal Dluz (usado pela tarefa "Portal Dluz - Backup").
rem A pasta de destino vem da variavel de ambiente DLUZ_BACKUP_DESTINO.
cd /d "%~dp0.."
if "%DLUZ_BACKUP_DESTINO%"=="" (
  echo ERRO: a variavel de ambiente DLUZ_BACKUP_DESTINO nao esta definida.
  exit /b 1
)
".venv\Scripts\python.exe" scripts\backup.py "%DLUZ_BACKUP_DESTINO%" %*
exit /b %ERRORLEVEL%
