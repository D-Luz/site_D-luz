@echo off
rem Inicia a Conferencia de Caixa na rede da loja (usado pela tarefa "Caixa - Servidor").
rem Funciona de qualquer pasta: vai para a raiz do projeto, uma acima de scripts\.
cd /d "%~dp0.."
if not defined CAIXA_DATA_DIR set CAIXA_DATA_DIR=C:\CaixaDados
if not defined CAIXA_HOST set CAIXA_HOST=0.0.0.0
rem Rede local sem HTTPS: o cookie de sessao precisa funcionar por http.
if not defined CAIXA_COOKIE_INSECURE set CAIXA_COOKIE_INSECURE=1
if not exist ".venv\Scripts\python.exe" (
  echo ERRO: nao encontrei .venv\Scripts\python.exe em %CD%.
  echo Crie o ambiente virtual conforme scripts\instalar_windows.md.
  exit /b 1
)
".venv\Scripts\python.exe" scripts\iniciar.py 8200
exit /b %ERRORLEVEL%
