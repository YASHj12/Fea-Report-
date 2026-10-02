@echo off
setlocal
cd /d "%~dp0"
title FEA Report Maker
echo ==============================================================
echo   FEA Report Maker
echo ==============================================================
echo.

rem ---- 0. the ZIP file must be extracted first --------------------------------
if not exist "app\server.py" goto :notextracted

rem ---- 1. find Python (the Microsoft Store shortcut does not count) -------------
set "PY="
py -3 --version >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto :havepython
python --version >nul 2>nul
if not errorlevel 1 set "PY=python"
if not defined PY goto :nopython
:havepython

rem ---- 2. first start of this version: private environment + packages ----------
if exist ".venv\fea_ready_22" goto :run
echo   First start of this version: installing the needed Python packages.
echo   This needs internet and takes about 1 minute. It is done only once.
echo.
if exist ".venv" rmdir /s /q ".venv"
%PY% -m venv ".venv"
if errorlevel 1 goto :novenv
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto :nopip
echo ready> ".venv\fea_ready_22"
echo   Packages installed.
echo.

rem ---- 3. run -----------------------------------------------------------------
:run
".venv\Scripts\python.exe" app\server.py %*
echo.
echo   The program has stopped.
pause
exit /b 0

:notextracted
echo   The program files were not found next to this file.
echo.
echo   You probably started it from INSIDE the ZIP file.
echo   1. Close this window.
echo   2. Right-click the ZIP file, choose "Extract All..." and click Extract.
echo   3. Open the new folder and double-click  start_windows.bat  there.
echo.
pause
exit /b 1

:nopython
echo   Python was not found on this computer.
echo.
echo   1. Go to  https://www.python.org/downloads/
echo   2. Download and run the installer.
echo   3. IMPORTANT: tick  "Add python.exe to PATH"  on the first screen.
echo   4. Then double-click this file again.
echo.
pause
exit /b 1

:novenv
echo.
echo   Could not set up the Python environment.
echo   - Python not installed yet (or only the Microsoft Store shortcut exists)?
echo     Install it from https://www.python.org/downloads/ , tick "Add python.exe to PATH",
echo     then double-click this file again.
echo   - Python was just installed? Close this window and double-click this file again.
echo.
if exist ".venv" rmdir /s /q ".venv"
pause
exit /b 1

:nopip
echo.
echo   The packages could not be installed. Please check the internet connection
echo   and double-click this file again.
echo.
if exist ".venv" rmdir /s /q ".venv"
pause
exit /b 1
