"""Загрузка и сохранение настроек, пути приложения, логирование."""
from __future__ import annotations

import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "Archivarius"

DEFAULTS: dict = {
    "template": "{YYYY}-{MM}-{DD}_{HH}-{mm}-{ss}",
    "year_folder": "{YYYY}/{MM}",
    "mode": "inplace",
    "recursive": False,
    "date_sources": ["meta", "mtime", "ctime"],
    "filename_date_fallback": False,
    "skip_system_files": True,
    "keep_names": False,
    "show_preview": True,
    "journal_retention": 20,
    "last_folders": [],
}


def exe_dir() -> Path:
    """Папка рядом с exe (или с исходниками при запуске из Python)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    """Папка для settings.json / журналов / логов. Портативно, с откатом в LOCALAPPDATA."""
    base = exe_dir()
    probe = base / ".write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return base
    except OSError:
        fallback = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def assets_dir() -> Path:
    """assets рядом с exe; при onefile-сборке дополнительно смотрим во временную распаковку."""
    candidates = [exe_dir() / "assets"]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "assets")
    for path in candidates:
        if path.is_dir():
            return path
    return candidates[0]


def journal_dir() -> Path:
    path = data_dir() / "journal"
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> dict:
    data = dict(DEFAULTS)
    path = settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return data
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in DEFAULTS:
                data[key] = value
    if not isinstance(data.get("last_folders"), list):
        data["last_folders"] = []
    # Копирование убрано: файлы только перемещаются. Старый settings.json с mode="copy"
    # приводим к перемещению, чтобы не осталось нерабочего значения.
    if data.get("mode") not in ("inplace", "dry"):
        data["mode"] = "inplace"
    # Порядок источников даты изменён: файловые даты при копировании сбрасываются,
    # поэтому сначала метаданные, затем mtime (он сохраняется при копировании),
    # и только потом ctime. Старый список без "meta" приводим к новому.
    sources = data.get("date_sources")
    if not isinstance(sources, list) or "meta" not in sources:
        data["date_sources"] = list(DEFAULTS["date_sources"])
    return data


def save_settings(data: dict) -> None:
    clean = {key: data.get(key, DEFAULTS[key]) for key in DEFAULTS}
    clean["last_folders"] = [str(p) for p in clean.get("last_folders", [])][:5]
    path = settings_path()
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        logging.getLogger(APP_NAME).warning("Не удалось сохранить настройки в %s", path)


def setup_logging() -> None:
    logger = logging.getLogger(APP_NAME)
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    try:
        handler = RotatingFileHandler(
            data_dir() / "app.log", maxBytes=1_000_000, backupCount=1, encoding="utf-8"
        )
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
