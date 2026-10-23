@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 goto erro_pasta

rem O ambiente e os arquivos de construcao ficam fora da entrega.
set "PASTA_BUILD=%LOCALAPPDATA%\Simulador-RM-EDF\compilacao"
set "PYTHON_BUILD=%PASTA_BUILD%\venv\Scripts\python.exe"
set "PYINSTALLER_CONFIG_DIR=%PASTA_BUILD%\cache"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
set "PYTHONHOME="
set "PYTHONPATH="

if not exist "main.py" (
    echo ERRO: mantenha este .bat junto de main.py e dos outros arquivos Python.
    goto falha
)

echo [1/3] Procurando instalacao do Python compativel...
if exist "%PYTHON_BUILD%" goto ferramentas

rem 1. Tenta inicializador py com Python 3 (padrao do instalador python.org no Windows)
py -3 -c "import sys, tkinter; assert sys.version_info >= (3, 10)" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py"
    set "PYTHON_ARGS=-3"
    goto criar_ambiente
)

rem 2. Tenta inicializador py sem argumentos
py -c "import sys, tkinter; assert sys.version_info >= (3, 10)" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py"
    set "PYTHON_ARGS="
    goto criar_ambiente
)

rem 3. Tenta comando python no PATH
python -c "import sys, tkinter; assert sys.version_info >= (3, 10)" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    set "PYTHON_ARGS="
    goto criar_ambiente
)

rem 4. Tenta comando python3 no PATH
python3 -c "import sys, tkinter; assert sys.version_info >= (3, 10)" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python3"
    set "PYTHON_ARGS="
    goto criar_ambiente
)

rem 5. Procura nas pastas de instalacao comuns do Windows (caso nao esteja no PATH)
for %%D in (
    "%LOCALAPPDATA%\Programs\Python\Python3*"
    "%ProgramFiles%\Python3*"
    "%ProgramFiles(x86)%\Python3*"
    "%LOCALAPPDATA%\Python\pythoncore-3*"
) do (
    if exist "%%~D\python.exe" (
        "%%~D\python.exe" -c "import sys, tkinter; assert sys.version_info >= (3, 10)" >nul 2>&1
        if not errorlevel 1 (
            set "PYTHON_CMD=%%~D\python.exe"
            set "PYTHON_ARGS="
            goto criar_ambiente
        )
    )
)

echo.
echo ERRO: Nao foi encontrada uma instalacao do Python 3.10 ou superior com Tkinter.
echo.
echo Para resolver:
echo   1. Baixe o Python no site oficial: https://www.python.org/downloads/
echo   2. Durante a instalacao, marque a opcao "Add python.exe to PATH".
echo   3. Certifique-se de que a opcao de interface grafica (tcl/tk) esta marcada.
goto falha

:criar_ambiente
echo [1/3] Criando ambiente virtual isolado em:
echo "%PASTA_BUILD%\venv"
"%PYTHON_CMD%" %PYTHON_ARGS% -m venv "%PASTA_BUILD%\venv"
if errorlevel 1 goto falha

:ferramentas
"%PYTHON_BUILD%" -c "import sys, tkinter; assert sys.version_info >= (3, 10), 'Use Python 3.10 ou superior com Tkinter.'"
if errorlevel 1 goto falha
echo [2/3] Instalando/atualizando o PyInstaller no ambiente isolado...
"%PYTHON_BUILD%" -m pip --disable-pip-version-check install "PyInstaller>=6.0" "pyinstaller-hooks-contrib"
if errorlevel 1 goto falha

echo [3/3] Gerando o executavel diretamente nesta pasta...
"%PYTHON_BUILD%" -m PyInstaller --noconfirm --onefile --windowed --noupx --hidden-import tkinter --name Simulador-RM-EDF --distpath "%CD%" --workpath "%PASTA_BUILD%\work" --specpath "%PASTA_BUILD%" main.py
if errorlevel 1 goto falha
if not exist "Simulador-RM-EDF.exe" goto falha

echo.
echo ========================================================
echo CONCLUIDO. Executavel gerado com sucesso nesta pasta:
echo "%CD%\Simulador-RM-EDF.exe"
echo ========================================================
pause
popd
exit /b 0

:falha
echo.
echo Nao foi possivel gerar o novo executavel. Confira a mensagem acima.
echo O executavel antigo da entrega nao foi alterado.
pause
popd
exit /b 1

:erro_pasta
echo ERRO: nao foi possivel abrir a pasta do projeto.
pause
exit /b 1
