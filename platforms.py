"""Всё, что различается между Windows, Linux и macOS.

Одна точка вместо проверок `os.name == "nt"` по всему коду. Если поведение
зависит от системы — оно живёт здесь и импортируется остальными модулями.

Правило: ни один другой файл проекта не должен знать, какая под ним ОС.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unicodedata
from pathlib import Path

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"
LINUX = not WINDOWS and not MACOS

# Человеческое имя системы — для сообщений и имён сборок.
LABEL = "Windows" if WINDOWS else ("macOS" if MACOS else "Linux")

# --- время создания файла ---------------------------------------------------

# st_ctime означает разное в разных системах, и это самая частая ошибка
# при переносе кода с Windows. На NTFS это время создания. На Linux и macOS —
# время последнего изменения inode (права, владелец, ссылки), к созданию
# отношения не имеющее. Настоящее создание на macOS — st_birthtime.
# На Linux его в stat нет вообще: файловая система не обязана его хранить.


def creation_time(stat_result: os.stat_result) -> float | None:
    """Время создания файла или None, если система его не хранит.

    None — честный ответ: лучше не показывать дату вовсе, чем показать
    время смены прав и разложить файл в чужой год.
    """
    birth = getattr(stat_result, "st_birthtime", None)
    if birth is not None:
        return birth
    if WINDOWS:
        # На NTFS st_ctime — именно creation time. В Python 3.12+ на Windows
        # появился st_birthtime, но на старых сборках его нет.
        return stat_result.st_ctime
    return None


# --- имена файлов -----------------------------------------------------------


def normalize_name(name: str) -> str:
    """Имя в форме NFC для сравнения.

    APFS (macOS) хранит имена в NFD: «й» = «и» + бревис, а не один символ.
    Без приведения к NFC «й.jpg» с флешки и «й.jpg», созданный в системе,
    выглядят разными строками — и программа попыталась бы перезаписать файл.
    """
    return unicodedata.normalize("NFC", name)


# --- открыть папку в проводнике --------------------------------------------


def open_in_file_manager(path: Path | str) -> None:
    """Открывает папку средствами системы. Бросает OSError, если не вышло."""
    target = str(path)
    if WINDOWS:
        os.startfile(target)  # type: ignore[attr-defined]
        return
    if MACOS:
        command = ["open", target]
    else:
        command = ["xdg-open", target]
    try:
        subprocess.run(command, check=False, capture_output=True, timeout=15)
    except FileNotFoundError:
        # xdg-open есть не во всех минимальных дистрибутивах.
        if MACOS:
            raise
        subprocess.run(["gio", "open", target], check=False, capture_output=True)


# --- уменьшение анимации ----------------------------------------------------

_REDUCE_CACHE: bool | None = None


def reduce_motion() -> bool:
    """Пользователь просил систему уменьшить анимацию.

    Windows — SystemParametersInfo(SPI_GETCLIENTAREAANIMATION).
    macOS — defaults читает настройку универсального доступа.
    Linux — gsettings, настройка GNOME «enable-animations».
    """
    global _REDUCE_CACHE
    if _REDUCE_CACHE is not None:
        return _REDUCE_CACHE

    _REDUCE_CACHE = _detect_reduce_motion()
    return _REDUCE_CACHE


def _detect_reduce_motion() -> bool:
    if WINDOWS:
        try:
            import ctypes

            enabled = ctypes.c_int(0)
            ok = ctypes.windll.user32.SystemParametersInfoW(
                0x1042, 0, ctypes.byref(enabled), 0
            )
            return bool(ok) and not enabled.value
        except Exception:  # noqa: BLE001
            return False

    if MACOS:
        # Ключ переехал между версиями macOS: пробуем оба.
        for key in ("reduceMotion", "reduceMotionEnabled"):
            try:
                result = subprocess.run(
                    ["defaults", "read", "com.apple.universalaccess", key],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except Exception:  # noqa: BLE001
                return False
            if result.returncode == 0:
                value = result.stdout.strip()
                if value in ("1", "true", "YES"):
                    return True
                if value in ("0", "false", "NO"):
                    return False
        return False

    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "enable-animations"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:  # noqa: BLE001
        return False
    return result.returncode == 0 and result.stdout.strip() == "false"


# --- системные каталоги -----------------------------------------------------

# Куда нельзя пускать раскладку: там файлы ОС и установленных программ.
# Внимание: /Volumes и /Network в список НЕ входят — на macOS это внешние диски
# и сетевые хранилища, то есть ровно те папки, которые пользователь и приходит
# раскладывать. Блокировать их — значит запретить программе основную работу.
_POSIX_SYSTEM_DIRS = (
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/boot",
    "/lib",
    "/lib32",
    "/lib64",
    "/var",
    "/opt",
    "/sys",
    "/proc",
    "/dev",
    "/run",
    "/snap",
    "/srv",
    "/root",
    "/System",
    "/Library",
    "/Applications",
    "/private",
    "/cores",
)

_WINDOWS_SYSTEM_DIRS = (
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "system32",
    "syswow64",
    "winsxs",
    "recovery",
    "perflogs",
    "$recycle.bin",
    "system volume information",
)


def posix_system_dirs() -> tuple[str, ...]:
    return _POSIX_SYSTEM_DIRS


def system_dir_names() -> set[str]:
    """Имена папок, запрещённых на системном диске Windows."""
    return set(_WINDOWS_SYSTEM_DIRS)


# --- исполняемые файлы ------------------------------------------------------

_WINDOWS_EXECUTABLE_SUFFIXES = {
    ".exe", ".dll", ".sys", ".msi", ".msix", ".msp", ".com", ".scr",
    ".cpl", ".ocx", ".drv", ".lnk", ".bat", ".cmd", ".ps1", ".vbs", ".jar",
    ".appx",
}

# На POSIX исполняемость задаётся битом, а не расширением, но скрипты
# и байткод тоже трогать не стоит — переименование ломает вызовы.
_POSIX_EXECUTABLE_SUFFIXES = {
    ".sh", ".bash", ".zsh", ".fish", ".py", ".pl", ".rb", ".command",
    ".app", ".service", ".desktop", ".so", ".dylib", ".dmg", ".pkg",
}


def executable_suffixes() -> set[str]:
    return (
        _WINDOWS_EXECUTABLE_SUFFIXES if WINDOWS else _POSIX_EXECUTABLE_SUFFIXES
    )


def has_execute_bit(path: Path, wp) -> bool:
    """Файл помечен как исполняемый битом прав. Только POSIX."""
    if WINDOWS:
        return False
    try:
        return bool(os.stat(wp(path)).st_mode & 0o111)
    except OSError:
        return False


# --- папки приложения -------------------------------------------------------

# Шрифты и иконки на POSIX принято держать в одном месте, на Windows — в своём.
def font_install_dir() -> Path:
    if WINDOWS:
        return Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts"
    if MACOS:
        return Path.home() / "Library" / "Fonts"
    return Path.home() / ".local" / "share" / "fonts"


def data_fallback_dir(app_name: str) -> Path:
    """Куда писать настройки, если папка программы только для чтения.

    Windows: %LOCALAPPDATA%. macOS: ~/Library/Application Support.
    Linux: $XDG_DATA_HOME или ~/.local/share.
    """
    if WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local"
        )
    elif MACOS:
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(
            Path.home() / ".local" / "share"
        )
    return Path(base) / app_name


# --- сборка -----------------------------------------------------------------

BUNDLE_ID = "com.javapixelstory.archivarius"


def artifact_name(app_name: str) -> str:
    """Как называется готовый файл после сборки."""
    if WINDOWS:
        return f"{app_name}.exe"
    if MACOS:
        return f"{app_name}.app"
    return app_name


def icon_for_build(assets: Path) -> Path | None:
    """Иконка в формате, который понимает PyInstaller на этой системе.

    Windows — .ico, macOS — .icns, Linux — .png.
    """
    if WINDOWS:
        candidates = ["icon.ico", "icon.png"]
    elif MACOS:
        candidates = ["icon.icns", "icon.ico", "icon.png"]
    else:
        candidates = ["icon.png", "icon.ico"]
    for name in candidates:
        path = assets / name
        if path.is_file():
            return path
    return None


def asset_dir_in_bundle(exe_dir: Path, app_name: str) -> Path:
    """Куда положить assets для готового файла.

    У macOS сборка — это .app-папка, и ресурсы должны лежать внутри неё,
    иначе при перетаскивании приложения в /Applications они потеряются.
    """
    if MACOS and exe_dir.name.endswith(".app"):
        return exe_dir / "Contents" / "MacOS" / "assets"
    return exe_dir / "assets"


def describe() -> str:
    """Строка для логов и экрана «о программе»."""
    return f"{LABEL} ({sys.platform}), Python {sys.version.split()[0]}"
