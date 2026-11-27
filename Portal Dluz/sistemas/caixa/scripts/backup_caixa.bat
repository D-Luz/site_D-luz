@echo off
rem Copia o banco da Conferencia de Caixa (usado pela tarefa "Caixa - Backup").
cd /d "%~dp0.."
if not defined CAIXA_DATA_DIR set CAIXA_DATA_DIR=C:\CaixaDados
if not defined CAIXA_BACKUP_DESTINO set CAIXA_BACKUP_DESTINO=D:\BackupsCaixa
".venv\Scripts\python.exe" scripts\backup.py "%CAIXA_BACKUP_DESTINO%"
exit /b %ERRORLEVEL%
