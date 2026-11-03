@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [0/3] Чищу кэш Python...
if exist __pycache__ rmdir /S /Q __pycache__
del /Q *.pyc 2>nul

echo [1/3] Проверяю зависимости...
python -m pip install -q -r requirements.txt || goto :fail

echo [2/3] Собираю exe...
if exist assets\icon.ico (
    python -m PyInstaller --onefile --windowed --noupx --name Archivarius --clean --icon assets\icon.ico main.py || goto :fail
) else (
    echo     assets\icon.ico не найден — собираю без иконки.
    python -m PyInstaller --onefile --windowed --noupx --name Archivarius --clean main.py || goto :fail
)

echo [3/3] Копирую assets рядом с exe...
if exist assets xcopy /E /I /Y assets dist\assets >nul

echo.
echo Готово: dist\Archivarius.exe
pause
exit /b 0

:fail
echo.
echo Сборка не удалась. Проверьте, что установлен Python 3.11+ и PyInstaller.
pause
exit /b 1
