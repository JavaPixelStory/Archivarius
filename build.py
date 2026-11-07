"""Сборка Архивариуса для той ОС, на которой запущен скрипт.

Запуск:  python build.py

PyInstaller не умеет собирать под другую ОС: exe — только на Windows,
бинарник — только на Linux, .app — только на macOS. Поэтому скрипт
определяет систему сам и собирает ровно то, что для неё положено.
Чтобы получить все три версии сразу, используйте GitHub Actions
(см. .github/workflows/build.yml): там каждая сборка идёт на своей машине.

Результат:
    Windows  dist/Archivarius-<версия>-windows.zip   (Archivarius.exe + assets/)
    Linux    dist/Archivarius-<версия>-linux.tar.gz  (Archivarius + assets/)
    macOS    dist/Archivarius-<версия>-macos.zip     (Archivarius.app)
"""
from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import zipfile
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "Archivarius"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
PYI = [sys.executable, "-m", "PyInstaller"]

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"
LINUX = not WINDOWS and not MACOS


def say(text: str = "") -> None:
    print(text, flush=True)


def step(number: int, total: int, title: str) -> None:
    say()
    say("=" * 52)
    say(f"  Шаг {number} из {total}. {title}")
    say("=" * 52)


def run(cmd: list[str], what: str) -> None:
    say(f"  > {what}")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        say()
        say("-" * 52)
        say(f"  ОШИБКА на шаге: {what}")
        say(f"  Команда: {' '.join(cmd)}")
        say("-" * 52)
        sys.exit(1)


def read_version() -> str:
    """Версия из version.py — единый источник, без копий в скрипте."""
    namespace: dict = {}
    exec((ROOT / "version.py").read_text(encoding="utf-8"), namespace)
    return str(namespace["APP_VERSION"])


def platform_tag() -> str:
    if WINDOWS:
        return "windows"
    if MACOS:
        return "macos"
    return "linux"


def check_python() -> None:
    if sys.version_info < (3, 11):
        say(f"Нужен Python 3.11 или новее. Сейчас: {sys.version.split()[0]}")
        sys.exit(1)
    say(f"  Python {sys.version.split()[0]} на {platform_tag()}")
    say(f"  Папка проекта: {ROOT}")


def check_module(name: str) -> bool:
    return subprocess.run(
        [sys.executable, "-c", f"import {name}"], cwd=ROOT, capture_output=True
    ).returncode == 0


def clean() -> None:
    for target in ("__pycache__", "build", "dist"):
        path = ROOT / target
        if path.is_dir():
            say(f"  удаляю {target}/")
            shutil.rmtree(path, ignore_errors=True)
    for spec in ROOT.glob("*.spec"):
        # .spec генерирует PyInstaller сам; старый файл хранит пути чужой машины.
        say(f"  удаляю {spec.name}")
        spec.unlink()


def build_app() -> Path:
    """Собирает программу для текущей ОС. Возвращает путь к результату."""
    cmd = PYI + [
        "--onefile",
        "--windowed",
        "--noupx",
        "--clean",
        "--name", NAME,
    ]

    if WINDOWS:
        icon = ROOT / "assets" / "icon.ico"
    elif MACOS:
        icon = ROOT / "assets" / "icon.icns"
    else:
        icon = None  # на Linux иконка задаётся в .desktop-файле, не в бинарнике

    if icon is not None and icon.is_file():
        cmd += ["--icon", str(icon)]
        say(f"  иконка: {icon.relative_to(ROOT)}")
    elif icon is not None:
        say(f"  иконка {icon.name} не найдена — собираю без неё")

    if MACOS:
        cmd += ["--osx-bundle-identifier", "ru.javapixelstory.archivarius"]

    cmd.append("main.py")
    run(cmd, f"PyInstaller собирает {NAME} для {platform_tag()}")

    if MACOS:
        return DIST / f"{NAME}.app"
    if WINDOWS:
        return DIST / f"{NAME}.exe"
    return DIST / NAME


def copy_assets() -> None:
    """assets/ кладём рядом с результатом: программа ищет их там."""
    source = ROOT / "assets"
    target = DIST / "assets"
    if not source.is_dir():
        say("  папки assets нет — пропускаю")
        return
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target)
    say(f"  скопировано в {target.relative_to(ROOT)}/")


def package(artifact: Path, version: str) -> Path:
    """Упаковывает результат в архив с понятным именем."""
    tag = platform_tag()
    base = f"{NAME}-{version}-{tag}"

    if LINUX:
        # tar.gz сохраняет права на исполнение; zip на Linux их теряет.
        archive = DIST / f"{base}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(artifact, arcname=artifact.name)
            tar.add(DIST / "assets", arcname="assets")
        return archive

    archive = DIST / f"{base}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        if artifact.is_dir():
            # .app — это папка; кладём её целиком с сохранением путей.
            for path in artifact.rglob("*"):
                zf.write(path, path.relative_to(DIST))
        else:
            zf.write(artifact, artifact.name)
        if not MACOS:
            for path in (DIST / "assets").rglob("*"):
                zf.write(path, path.relative_to(DIST))
    return archive


def report(artifact: Path, archive: Path) -> None:
    say()
    say("=" * 52)
    if not artifact.exists():
        say("  СБОРКА НЕ УДАЛАСЬ: результат не появился")
        say("=" * 52)
        sys.exit(1)
    size_mb = archive.stat().st_size / (1024 * 1024)
    say("  ГОТОВО")
    say("=" * 52)
    say(f"  Архив:  {archive.relative_to(ROOT)}")
    say(f"  Размер: {size_mb:.1f} МБ")
    say()
    if MACOS:
        say("  Первый запуск на macOS: программа не подписана Apple,")
        say("  поэтому откройте её через правый клик → «Открыть»,")
        say("  или выполните в терминале:  xattr -cr Archivarius.app")


def main() -> int:
    version = read_version()
    say()
    say(f"Сборка Архивариус {version} для {platform_tag()}")

    step(1, 5, "Проверяю Python")
    check_python()

    step(2, 5, "Ставлю зависимости")
    run([sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
        "обновляю pip")
    run([sys.executable, "-m", "pip", "install", "--quiet", "-r", "requirements.txt"],
        "зависимости из requirements.txt")
    if not check_module("PyInstaller"):
        run([sys.executable, "-m", "pip", "install", "--quiet", "pyinstaller"],
            "ставлю PyInstaller")

    step(3, 5, "Чищу прошлые сборки")
    clean()

    step(4, 5, "Собираю программу (1–3 минуты)")
    artifact = build_app()

    step(5, 5, "Кладу assets и упаковываю")
    copy_assets()
    archive = package(artifact, version)

    report(artifact, archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
