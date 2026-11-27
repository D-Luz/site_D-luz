@echo off
rem Inicia o servidor do sistema de pedidos (usado pela tarefa "Pedidos - Servidor").
rem Funciona de qualquer pasta: vai para a raiz do projeto, uma acima de scripts\.
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo ERRO: nao encontrei .venv\Scripts\python.exe em %CD%.
  echo Crie o ambiente virtual conforme scripts\instalar_windows.md.
  exit /b 1
)
".venv\Scripts\python.exe" scripts\iniciar.py
exit /b %ERRORLEVEL%
