"""Сборка Archivarius в один исполняемый файл.

Запуск:  build.bat   (или: python build.py)

Скрипт не использует батч-файл для логики: cmd.exe плохо переносит кириллицу
и многострочные команды, поэтому вся работа сделана на Python.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "Archivarius"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
EXE_NAME = f"{NAME}.exe" if sys.platform == "win32" else NAME
PYI = [sys.executable, "-m", "PyInstaller"]


def say(text: str = "") -> None:
    print(text, flush=True)


def step(number: int, total: int, title: str) -> None:
    say()
    say("=" * 52)
    say(f"  Шаг {number} из {total}. {title}")
    say("=" * 52)


def run(cmd: list[str], what: str) -> None:
    """Запускает команду и печатает её понятным текстом. При ошибке останавливает сборку."""
    say(f"  > {what}")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        say()
        say("-" * 52)
        say(f"  ОШИБКА на шаге: {what}")
        say(f"  Команда: {' '.join(cmd)}")
        say("-" * 52)
        sys.exit(1)


def check_python() -> None:
    if sys.version_info < (3, 11):
        say(f"Нужен Python 3.11 или новее. Сейчас: {sys.version.split()[0]}")
        say("Скачать: https://python.org")
        sys.exit(1)
    say(f"  Python {sys.version.split()[0]}")
    say(f"  Папка проекта: {ROOT}")


def check_module(name: str) -> bool:
    return subprocess.run(
        [sys.executable, "-c", f"import {name}"],
        cwd=ROOT, capture_output=True,
    ).returncode == 0


def clean() -> None:
    for target in ("__pycache__", "build", "dist"):
        path = ROOT / target
        if path.is_dir():
            say(f"  удаляю {target}/")
            shutil.rmtree(path, ignore_errors=True)
    spec = ROOT / f"{NAME}.spec"
    if spec.is_file():
        say(f"  удаляю {NAME}.spec")
        spec.unlink()
    for stale in ROOT.glob("*.pyc"):
        stale.unlink()


def build() -> None:
    cmd = PYI + [
        "--onefile",
        "--windowed",
        "--noupx",
        "--clean",
        "--name", NAME,
    ]
    icon = ROOT / "assets" / "icon.ico"
    if icon.is_file():
        cmd += ["--icon", str(icon)]
        say("  иконка: assets/icon.ico")
    else:
        say("  иконка не найдена — собираю без неё")
    cmd.append("main.py")
    run(cmd, "PyInstaller собирает exe")


def copy_assets() -> None:
    source = ROOT / "assets"
    target = DIST / "assets"
    if not source.is_dir():
        say("  папки assets нет — пропускаю")
        return
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target)
    say(f"  скопировано в {target.relative_to(ROOT)}/")


def report() -> None:
    exe = DIST / EXE_NAME
    say()
    say("=" * 52)
    if not exe.is_file():
        say("  СБОРКА НЕ УДАЛАСЬ: файл не появился")
        say("=" * 52)
        sys.exit(1)
    size_mb = exe.stat().st_size / (1024 * 1024)
    say("  ГОТОВО")
    say("=" * 52)
    say(f"  Файл:   {exe}")
    say(f"  Размер: {size_mb:.1f} МБ")
    say()
    say("  Запускается двойным кликом, установка не нужна.")
    say("  Рядом должна лежать папка assets/ — она уже скопирована.")


def main() -> int:
    say()
    say("Сборка Архивариус в один exe")

    step(1, 5, "Проверяю Python")
    check_python()

    step(2, 5, "Ставлю зависимости")
    run([sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", "pip"], "обновляю pip")
    run([sys.executable, "-m", "pip", "install", "--quiet", "-r", "requirements.txt"], "зависимости из requirements.txt")
    if not check_module("PyInstaller"):
        run([sys.executable, "-m", "pip", "install", "--quiet", "pyinstaller"], "ставлю PyInstaller")
    else:
        say("  PyInstaller уже установлен")

    step(3, 5, "Чищу прошлые сборки")
    clean()

    step(4, 5, "Собираю exe (1-3 минуты)")
    build()

    step(5, 5, "Кладу assets рядом с exe")
    copy_assets()

    report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
