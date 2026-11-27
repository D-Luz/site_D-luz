@echo off
rem Inicia o Portal Dluz (usado pela tarefa "Portal Dluz - Servidor").
rem Funciona de qualquer pasta: vai para a raiz do projeto, uma acima de scripts\.
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo ERRO: nao encontrei .venv\Scripts\python.exe em %CD%.
  echo Crie o ambiente virtual conforme INSTALAR_WINDOWS.md.
  exit /b 1
)
if "%DLUZ_DATA_DIR%"=="" (
  echo ERRO: a variavel de ambiente DLUZ_DATA_DIR nao esta definida.
  echo Defina a pasta de dados conforme INSTALAR_WINDOWS.md.
  exit /b 1
)
".venv\Scripts\python.exe" scripts\iniciar.py
exit /b %ERRORLEVEL%
