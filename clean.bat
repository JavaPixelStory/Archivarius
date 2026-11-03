@echo off
REM Pure ASCII on purpose - see build.bat for the reason.
cd /d "%~dp0"
echo Removing Python cache and previous builds...
if exist __pycache__ rmdir /S /Q __pycache__
if exist build rmdir /S /Q build
if exist dist rmdir /S /Q dist
del /Q *.pyc 2>nul
del /Q Archivarius.spec 2>nul
echo Done. Now run: build.bat
pause
