# Как собрать Архивариус

Сборка теперь кроссплатформенная: одна команда собирает под вашу ОС.

## Локальная сборка (Windows / Linux / macOS)

Одна команда вместо `build.bat`:

```bash
python build.py
```

Что она делает:

1. Проверяет Python (нужен 3.11+)
2. Ставит зависимости из `requirements.txt` + PyInstaller
3. Чистит старые сборки (`build/`, `dist/`, `*.spec`)
4. Собирает программу: `--onefile --windowed`
   - Windows → `Archivarius.exe`
   - Linux → `Archivarius` (бинарник с правами 755)
   - macOS → `Archivarius.app`
5. Копирует `assets/` рядом с результатом
6. Упаковывает в архив:
   - Windows → `Archivarius-<версия>-windows.zip`
   - Linux → `Archivarius-<версия>-linux.tar.gz`
   - macOS → `Archivarius-<версия>-macos.zip`

Результат: папка `dist/` с архивом и распакованными файлами.

### Первый запуск

```bash
# Создайте виртуальное окружение (один раз)
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS

# Соберите
python build.py
```

### Следующие разы

```bash
.venv\Scripts\activate
python build.py
```

### Зачем `build.bat` больше не нужен

`build.py` — это тот же `build.bat`, только на Python: работает на всех ОС,
не зависит от кодировки cmd.exe, показывает прогресс с кириллицей.

`build.bat` можно оставить как ярлык:

```bat
@echo off
python build.py
pause
```

---

## GitHub Actions — три сборки одним запуском

`.github/workflows/build.yml` собирает для Windows, Linux и macOS сразу —
на трёх машинах параллельно.

### Вручную

1. Зайдите в **Actions** → **Сборка** → **Run workflow**
2. Подождите ~5 минут
3. Скачайте три архива из **Artifacts**

### Автоматом при релизе

1. Создайте тег:
   ```bash
   git tag v2.0
   git push origin v2.0
   ```
2. GitHub Actions соберёт три версии и опубликует релиз с тремя архивами

---

## Что изменилось

| Было | Стало |
| --- | --- |
| `build.bat` только под Windows | `build.py` под все три ОС |
| Иконка `.ico` (Windows) | `.ico` + `.icns` (macOS) |
| Версия в двух местах | Одна — `version.py` |
| Сборки руками | CI собирает все три |

---

## Если что-то не так

**"PyInstaller не найден"** — `pip install pyinstaller`

**"Python 3.11 или новее"** — обновите Python

**Windows: иконка не встроилась** — проверьте `assets/icon.ico`

**macOS: "повреждённое приложение"** — нет подписи Apple; обход:
```bash
xattr -cr dist/Archivarius.app
```
или правый клик → «Открыть».

**Linux: не запускается** — нет Tk:
```bash
sudo apt install python3-tk
```

---

## Быстрая шпаргалка

```bash
# Локально
python build.py

# Очистить перед сборкой
clean.bat          # Windows
rm -rf build dist *.spec   # Linux / macOS

# CI (вручную)
Actions → Сборка → Run workflow

# CI (автоматом)
git tag v2.0 && git push origin v2.0
```
