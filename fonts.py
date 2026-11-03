"""Подключение шрифта Roboto.

Шрифт лежит в assets/fonts и регистрируется в системе при старте, поэтому
пользователю не нужно устанавливать его вручную. Если файлов нет или
зарегистрировать не удалось, приложение откатывается на системный шрифт —
вёрстка остаётся рабочей, меняется только начертание.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

LOG = logging.getLogger("archivarius.fonts")

FAMILY = "Roboto"
FALLBACK = "TkDefaultFont"

FILES = {
    "normal": "Roboto-Regular.ttf",
    "medium": "Roboto-Medium.ttf",
    "bold": "Roboto-SemiBold.ttf",
    "black": "Roboto-Bold.ttf",
}

_loaded = False
_available = False


def _fonts_dir() -> Path:
    try:
        import settings as cfgmod

        return cfgmod.assets_dir() / "fonts"
    except Exception:  # noqa: BLE001
        return Path(__file__).resolve().parent / "assets" / "fonts"


def _register_windows(path: Path) -> bool:
    """AddFontResourceExW с флагом FR_PRIVATE: шрифт доступен только процессу."""
    try:
        import ctypes

        FR_PRIVATE = 0x10
        result = ctypes.windll.gdi32.AddFontResourceExW(str(path), FR_PRIVATE, 0)
        return bool(result)
    except Exception:  # noqa: BLE001
        return False


def _register_linux(path: Path) -> bool:
    """Копируем в пользовательский каталог шрифтов и обновляем кэш."""
    import shutil
    import subprocess

    try:
        target_dir = Path.home() / ".local" / "share" / "fonts"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        if not target.is_file() or target.stat().st_size != path.stat().st_size:
            shutil.copy2(path, target)
            subprocess.run(["fc-cache", "-f", str(target_dir)],
                           check=False, capture_output=True, timeout=20)
        return True
    except Exception:  # noqa: BLE001
        return False


def _register_macos(path: Path) -> bool:
    try:
        target_dir = Path.home() / "Library" / "Fonts"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        if not target.is_file():
            import shutil

            shutil.copy2(path, target)
        return True
    except Exception:  # noqa: BLE001
        return False


def load() -> bool:
    """Регистрирует шрифты один раз. Возвращает True, если Roboto доступен."""
    global _loaded, _available
    if _loaded:
        return _available
    _loaded = True

    folder = _fonts_dir()
    if not folder.is_dir():
        LOG.warning("Каталог шрифтов не найден: %s", folder)
        return False

    missing = [name for name in FILES.values() if not (folder / name).is_file()]
    if missing:
        LOG.warning("Нет файлов шрифта: %s", ", ".join(missing))
        return False

    register = {"win32": _register_windows, "darwin": _register_macos}.get(
        sys.platform, _register_linux
    )
    for name in FILES.values():
        if not register(folder / name):
            LOG.warning("Не удалось зарегистрировать %s", name)
            return False

    _available = True
    return True


def family() -> str:
    """Имя семейства для Tk. Roboto, если загрузился, иначе системный."""
    return FAMILY if _available else FALLBACK


def weight(name: str) -> str:
    """Tk понимает normal/bold; medium и black эмулируем через bold."""
    return {"bold": "bold", "black": "bold", "medium": "bold"}.get(name, "normal")
