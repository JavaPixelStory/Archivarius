@echo off
REM ---------------------------------------------------------------
REM  Archivarius build launcher.
REM  This file is pure ASCII on purpose: cmd.exe mangles UTF-8
REM  Russian text and multibyte line continuations.
REM  All real logic (and all Russian messages) lives in build.py.
REM ---------------------------------------------------------------
cd /d "%~dp0"

set PY=
where python >nul 2>&1 && set PY=python
if not defined PY where py >nul 2>&1 && set PY=py -3
if not defined PY goto :nopython

%PY% build.py
if errorlevel 1 goto :failed

pause
exit /b 0

:nopython
echo.
echo ============================================================
echo   Python not found
echo ============================================================
echo.
echo   Install Python 3.11 or newer from https://python.org
echo   During setup, tick "Add Python to PATH".
echo.
pause
exit /b 1

:failed
echo.
echo ============================================================
echo   BUILD FAILED
echo ============================================================
echo.
echo   Read the messages above - they name the failing step.
echo.
pause
exit /b 1
