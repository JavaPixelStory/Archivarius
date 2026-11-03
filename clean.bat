@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Удаляю кэш Python и старые настройки...
if exist __pycache__ rmdir /S /Q __pycache__
if exist build rmdir /S /Q build
if exist dist rmdir /S /Q dist
del /Q *.pyc 2>nul
echo Готово. Теперь: python main.py
pause
