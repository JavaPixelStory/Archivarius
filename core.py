"""Ядро «Архивариуса»: сканирование, даты, шаблоны, планирование, исполнение, откат.

Модуль не импортирует tkinter и не зависит от интерфейса.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

# --- статусы -----------------------------------------------------------------

RENAME = "rename"
UNCHANGED = "unchanged"
BAD_DATE = "bad_date"
ERROR = "error"
SKIPPED = "skipped"

STATUS_LABELS = {
    RENAME: "к переименованию",
    UNCHANGED: "без изменений",
    BAD_DATE: "дата недостоверна",
    ERROR: "ошибки",
    SKIPPED: "пропущено",
}

# --- источники даты ----------------------------------------------------------

SRC_EXIF_ORIGINAL = "EXIF: снимок"
SRC_EXIF_DATETIME = "EXIF: изменение"
SRC_CTIME = "создание файла"
SRC_MTIME = "изменение файла"
SRC_FILENAME = "из имени файла"
SRC_META_CREATED = "метаданные: создан"
SRC_META_MODIFIED = "метаданные: изменён"
SRC_NONE = "—"

MIN_DATE = datetime(1980, 1, 1)
MAX_FUTURE = timedelta(days=1)

DATE_TOKENS = ("{YYYY}", "{YY}", "{MM}", "{DD}", "{HH}", "{mm}", "{ss}")
NAME_TOKENS = ("{NAME}", "{EXT}", "{N}")
ALL_TOKENS = DATE_TOKENS + NAME_TOKENS

FORBIDDEN_CHARS = set('<>:"/\\|?*')
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {
    f"LPT{i}" for i in range(1, 10)
}
SKIP_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}

EXIF_ORIGINAL = 36867
EXIF_DATETIME = 306
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".tif", ".tiff", ".png", ".webp", ".heic", ".heif"}
OOXML_SUFFIXES = {".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm", ".vsdx"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".m4a"}

MONTHS_RU = {
    1: "янв", 2: "фев", 3: "мар", 4: "апр", 5: "мая", 6: "июн",
    7: "июл", 8: "авг", 9: "сен", 10: "окт", 11: "ноя", 12: "дек",
}


# --- длинные пути ------------------------------------------------------------


def wp(path: os.PathLike | str) -> str:
    """Путь, пригодный для API Windows при длине > 260 символов."""
    text = str(path)
    if os.name != "nt" or len(text) < 240 or text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


# --- модели ------------------------------------------------------------------


@dataclass
class PlanItem:
    src: Path
    name: str
    ext: str
    dt: datetime | None = None
    source: str = SRC_NONE
    status: str = RENAME
    note: str = ""
    dst_name: str = ""
    dst_path: Path | None = None
    folder_label: str = ""

    @property
    def year_folder(self) -> str:
        return self.folder_label

    @property
    def display_dst(self) -> str:
        if not self.dst_path:
            return ""
        return f"{self.dst_path.parent.name}\\{self.dst_path.name}"


@dataclass
class PlanResult:
    items: list[PlanItem] = field(default_factory=list)
    folder_errors: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        result = {status: 0 for status in STATUS_LABELS}
        for item in self.items:
            result[item.status] = result.get(item.status, 0) + 1
        return result

    def actionable(self) -> list[PlanItem]:
        return [i for i in self.items if i.status == RENAME]


@dataclass
class RunStats:
    processed: int = 0
    renamed: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: int = 0
    elapsed: float = 0.0
    cancelled: bool = False
    journal_path: Path | None = None
    failed: list[PlanItem] = field(default_factory=list)


# --- шаблоны -----------------------------------------------------------------


class TemplateError(ValueError):
    pass


def _render(template: str, dt: datetime, name: str, ext: str, counter: str = "") -> str:
    return (
        template.replace("{YYYY}", f"{dt.year:04d}")
        .replace("{YY}", f"{dt.year % 100:02d}")
        .replace("{MM}", f"{dt.month:02d}")
        .replace("{DD}", f"{dt.day:02d}")
        .replace("{HH}", f"{dt.hour:02d}")
        .replace("{mm}", f"{dt.minute:02d}")
        .replace("{ss}", f"{dt.second:02d}")
        .replace("{MMM}", MONTHS_RU.get(dt.month, ""))
        .replace("{NAME}", name)
        .replace("{EXT}", ext)
        .replace("{N}", counter)
    )


def validate_template(template: str) -> str | None:
    """Возвращает текст ошибки или None, если шаблон корректен."""
    if not template or not template.strip():
        return "Шаблон пуст."
    bad = sorted({c for c in template if c in FORBIDDEN_CHARS or ord(c) < 32})
    if bad:
        shown = " ".join(repr(c) for c in bad)
        return f"Недопустимые символы: {shown}. Разрешены - _ . и пробел."
    if template.rstrip() != template or template.endswith("."):
        return "Имя не может заканчиваться пробелом или точкой."
    if not any(token in template for token in DATE_TOKENS):
        return "Шаблон должен содержать хотя бы один элемент даты, например {YYYY}."
    leftover = re.findall(r"\{[A-Za-z]+\}", template)
    unknown = [t for t in leftover if t not in ALL_TOKENS and t != "{MMM}"]
    if unknown:
        return f"Неизвестные элементы: {', '.join(unknown)}."
    probe = _render(template, datetime(2024, 5, 17, 14, 32, 5), "NAME", "ext", "01")
    if probe.split(".")[0].upper() in RESERVED_NAMES:
        return "Получившееся имя зарезервировано Windows."
    return None


def validate_year_folder(template: str) -> str | None:
    if not template or not template.strip():
        return "Шаблон папки года пуст."
    bad = sorted({c for c in template if (c in FORBIDDEN_CHARS and c != "/") or ord(c) < 32})
    if bad:
        return f"Недопустимые символы: {' '.join(repr(c) for c in bad)}."
    for part in template.split("/"):
        if not part.strip() or part != part.rstrip() or part.endswith("."):
            return "Части пути не могут быть пустыми и заканчиваться пробелом или точкой."
    if "{YYYY}" not in template and "{YY}" not in template:
        return "Шаблон папки года должен содержать {YYYY}."
    return None


def render_name(template: str, dt: datetime, name: str, ext: str, counter: str = "") -> str:
    base = _render(template, dt, name, ext, counter)
    base = base.replace("{MMM}", MONTHS_RU.get(dt.month, ""))
    result = base if ext == "" else f"{base}.{ext}"
    return result


# --- определение даты --------------------------------------------------------

_NAME_PATTERNS = (
    re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})[_\-]?(\d{2})(\d{2})(\d{2})(?!\d)"),
    re.compile(r"(?<!\d)(20\d{2})[-_.](\d{2})[-_.](\d{2})(?!\d)"),
    re.compile(r"(?<!\d)(\d{2})[-_.](\d{2})[-_.](20\d{2})(?!\d)"),
)


def date_from_name(name: str) -> datetime | None:
    for index, pattern in enumerate(_NAME_PATTERNS):
        match = pattern.search(name)
        if not match:
            continue
        groups = match.groups()
        try:
            if index == 0:
                return datetime(*(int(g) for g in groups))
            if index == 1:
                year, month, day = (int(g) for g in groups)
                return datetime(year, month, day)
            day, month, year = (int(g) for g in groups)
            return datetime(year, month, day)
        except ValueError:
            continue
    return None


def exif_date(path: Path) -> tuple[datetime | None, str]:
    """Дата из EXIF изображения: сначала момент съёмки, затем момент правки."""
    try:
        from PIL import Image
    except ImportError:
        return None, SRC_NONE
    try:
        with Image.open(wp(path)) as image:
            exif = image.getexif()
            if not exif:
                return None, SRC_NONE
            for tag, label in ((EXIF_ORIGINAL, SRC_EXIF_ORIGINAL), (EXIF_DATETIME, SRC_EXIF_DATETIME)):
                raw = exif.get(tag)
                if not raw:
                    continue
                parsed = _parse_exif(raw)
                if parsed:
                    return parsed, label
    except Exception:
        return None, SRC_NONE
    return None, SRC_NONE


# --- метаданные документов и видео ------------------------------------------
#
# Время создания живёт не только в EXIF фото. У офисных файлов и PDF это поля
# created/createdate, у видео — тег creation_time. Без них приложение опиралось бы
# на файловые даты, а они при копировании сбрасываются (см. _from_date_field).

PDF_CREATE_KEYS = (b"/CreationDate", b"/ModDate")
OOXML_CORES = (
    ("docProps/core.xml", "created"),
    ("docProps/core.xml", "modified"),
)


def _from_date_field(value) -> datetime | None:
    """Разбирает дату из PDF, OOXML или ISO-строки."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None

    # PDF: D:20190314092653+03'00'  или  D:20190314092653Z
    if text.startswith("D:"):
        body = text[2:]
        stamp = body[:14]
        for size, fmt in ((14, "%Y%m%d%H%M%S"), (8, "%Y%m%d")):
            chunk = stamp[:size]
            if len(chunk) == size and chunk.isdigit():
                try:
                    parsed = datetime.strptime(chunk, fmt)
                except ValueError:
                    continue
                offset = body[size:]
                if offset.startswith(("+", "-")) and len(offset) >= 5:
                    sign = 1 if offset[0] == "+" else -1
                    try:
                        zone = timezone(
                            sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[4:6]))
                        )
                        # Переводим в локальное время так же, как это делает ISO-ветка.
                        parsed = parsed.replace(tzinfo=zone).astimezone().replace(tzinfo=None)
                    except ValueError:
                        pass
                return parsed
        return None

    # ISO 8601 и близкие форматы: 2019-03-14T09:26:53Z, 2019-03-14 09:26:53.123
    cleaned = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed

    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text.split(".")[0], fmt)
        except ValueError:
            continue
    return None


def pdf_date(path: Path) -> tuple[datetime | None, str]:
    """Дата создания из метаданных PDF (Info-словарь)."""
    try:
        size = path.stat().st_size
        with open(wp(path), "rb") as handle:
            # Info-словарь почти всегда лежит в хвосте файла вместе с xref.
            handle.seek(max(0, size - 65536))
            tail = handle.read()
            if b"/CreationDate" not in tail and b"/CreationDate" not in handle.read():
                handle.seek(0)
                head = handle.read(65536)
                tail = head + tail
    except OSError:
        return None, SRC_NONE

    for key, label in zip(PDF_CREATE_KEYS, (SRC_META_CREATED, SRC_META_MODIFIED)):
        pattern = re.compile(re.escape(key) + rb"\s*\(([^)]{8,40})\)")
        match = pattern.search(tail)
        if not match:
            continue
        parsed = _from_date_field(match.group(1).decode("latin-1", "replace"))
        if parsed:
            return parsed, label
    return None, SRC_NONE


def ooxml_date(path: Path) -> tuple[datetime | None, str]:
    """Дата создания из docProps/core.xml внутри docx/xlsx/pptx (это zip)."""
    try:
        with zipfile.ZipFile(wp(path)) as archive:
            names = set(archive.namelist())
            if "docProps/core.xml" not in names:
                return None, SRC_NONE
            with archive.open("docProps/core.xml") as handle:
                raw = handle.read().decode("utf-8", "replace")
    except (OSError, zipfile.BadZipFile, KeyError):
        return None, SRC_NONE

    for tag, label in (("created", SRC_META_CREATED), ("modified", SRC_META_MODIFIED)):
        match = re.search(rf"<dcterms:{tag}[^>]*>([^<]+)<", raw)
        if not match:
            continue
        parsed = _from_date_field(match.group(1))
        if parsed:
            return parsed, label
    return None, SRC_NONE


_MP4_CONTAINERS = {b"moov", b"trak", b"mdia"}
_MP4_TIME_BOXES = {b"mvhd", b"mdhd"}
_MP4_MAX_MOOV = 64 * 1024 * 1024


def _mp4_times(data: bytes, start: int, end: int, out: list[int]) -> None:
    pos = start
    while pos + 8 <= end:
        size = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = int.from_bytes(data[pos + 8:pos + 16], "big")
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        body = pos + header
        if kind in _MP4_CONTAINERS:
            _mp4_times(data, body, pos + size, out)
        elif kind in _MP4_TIME_BOXES and pos + size - body >= 12:
            width = 8 if data[body] == 1 else 4
            if pos + size - body >= 4 + width:
                out.append(int.from_bytes(data[body + 4:body + 4 + width], "big"))
        pos += size


def video_date(path: Path) -> tuple[datetime | None, str]:
    """Время создания из MP4/MOV: атомы mvhd и mdhd внутри moov, которого может не быть в первых байтах."""
    try:
        size_total = os.path.getsize(wp(path))
        with open(wp(path), "rb") as handle:
            if b"ftyp" not in handle.read(64)[:64]:
                return None, SRC_NONE
            moov = None
            pos = 0
            while pos + 8 <= size_total:
                handle.seek(pos)
                head = handle.read(16)
                if len(head) < 8:
                    break
                size = int.from_bytes(head[:4], "big")
                kind = head[4:8]
                if size == 1 and len(head) >= 16:
                    size = int.from_bytes(head[8:16], "big")
                elif size == 0:
                    size = size_total - pos
                if size < 8 or pos + size > size_total:
                    break
                if kind == b"moov":
                    if size <= _MP4_MAX_MOOV:
                        handle.seek(pos)
                        moov = handle.read(size)
                    break
                pos += size
    except OSError:
        return None, SRC_NONE

    if not moov:
        return None, SRC_NONE
    seconds: list[int] = []
    _mp4_times(moov, 0, len(moov), seconds)
    epoch = datetime(1904, 1, 1)
    dates = []
    for value in seconds:
        try:
            parsed = epoch + timedelta(seconds=value)
        except OverflowError:
            continue
        if parsed.year >= 1970:
            dates.append(parsed)
    if not dates:
        return None, SRC_NONE
    return min(dates), SRC_META_CREATED


def metadata_date(path: Path) -> tuple[datetime | None, str]:
    """Единая точка входа: метаданные файла согласно его типу."""
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        found, label = exif_date(path)
        if found:
            return found, label
    if suffix == ".pdf":
        found, label = pdf_date(path)
        if found:
            return found, label
    if suffix in OOXML_SUFFIXES:
        found, label = ooxml_date(path)
        if found:
            return found, label
    if suffix in VIDEO_SUFFIXES:
        found, label = video_date(path)
        if found:
            return found, label
    return None, SRC_NONE


def _parse_exif(raw) -> datetime | None:
    text = str(raw).strip().split(".")[0]
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


SRC_TEMPLATE_NAME = "из имени по шаблону"

_TOKEN_PATTERN = {
    "{YYYY}": r"(?P<Y>\d{4})",
    "{YY}": r"(?P<y>\d{2})",
    "{MM}": r"(?P<m>\d{2})",
    "{DD}": r"(?P<d>\d{2})",
    "{HH}": r"(?P<H>\d{2})",
    "{mm}": r"(?P<M>\d{2})",
    "{ss}": r"(?P<S>\d{2})",
    "{N}": r"\d+",
    "{NAME}": r".+?",
    "{EXT}": r"[^.]*",
}


def template_date(name: str, template: str) -> datetime | None:
    """Дата из имени файла, если имя уже собрано по шаблону.

    Нужно для идемпотентности: повторный запуск должен узнавать разложенные файлы,
    даже если файловая система не хранит время создания (ctime меняется при переименовании).
    """
    if "{MMM}" in template:
        return None
    body = ""
    position = 0
    for token, regex in _TOKEN_PATTERN.items():
        token_pos = template.find(token)
        while token_pos != -1:
            body += re.escape(template[:token_pos]) + regex
            template = template[token_pos + len(token):]
            token_pos = template.find(token)
    body += re.escape(template)

    try:
        match = re.fullmatch(r"(?s)" + body + r"(?:_\d+)?", name)
    except re.error:
        return None
    if not match:
        return None

    parts = match.groupdict()
    try:
        year = int(parts["Y"]) if parts.get("Y") else 2000 + int(parts["y"])
        return datetime(
            year,
            int(parts.get("m") or 1),
            int(parts.get("d") or 1),
            int(parts.get("H") or 0),
            int(parts.get("M") or 0),
            int(parts.get("S") or 0),
        )
    except (ValueError, TypeError):
        return None


def collect_dates(path: Path, stat_result: os.stat_result, cfg: dict) -> list[tuple[datetime, str]]:
    """Все правдоподобные даты файла: метаданные, имя, mtime, ctime.

    Даты вне диапазона (раньше 1980 или позже завтрашнего дня) отбрасываются сразу,
    чтобы не выигрывать отбор «самая ранняя» из-за битого тега.
    """
    now = datetime.now()
    found: list[tuple[datetime, str]] = []

    def add(value: datetime | None, label: str) -> None:
        if value is not None and _sane(value, now):
            found.append((value, label))

    try:
        meta_dt, meta_label = metadata_date(path)
    except Exception:  # noqa: BLE001 - битые метаданные не должны ломать скан
        meta_dt, meta_label = None, SRC_NONE
    add(meta_dt, meta_label)

    template = cfg.get("template") or ""
    if template:
        add(template_date(path.stem, template), SRC_TEMPLATE_NAME)

    if cfg.get("filename_date_fallback"):
        add(date_from_name(path.stem), SRC_FILENAME)

    add(_from_timestamp(stat_result.st_mtime), SRC_MTIME)
    add(_from_timestamp(stat_result.st_ctime), SRC_CTIME)
    return found


def detect_date(path: Path, stat_result: os.stat_result, cfg: dict) -> tuple[datetime | None, str]:
    """Самая ранняя правдоподобная дата из всех найденных.

    Файлы копируются, пересохраняются и переносятся. Каждое такое действие может
    только добавить более позднюю дату (новое время создания, новое изменение), но
    не может сделать дату раньше реальной. Поэтому побеждает самая старая дата,
    которая не выходит за допустимый диапазон.
    """
    candidates = collect_dates(path, stat_result, cfg)
    if not candidates:
        return None, SRC_NONE
    # min() — ключевая строка: побеждает самая ранняя дата из всех источников.
    return min(candidates, key=lambda item: item[0])


def _from_timestamp(value: float) -> datetime | None:
    try:
        return datetime.fromtimestamp(value)
    except (OSError, OverflowError, ValueError):
        return None


def _sane(dt: datetime | None, now: datetime) -> bool:
    if dt is None:
        return False
    return MIN_DATE <= dt <= now + MAX_FUTURE


# --- сбор файлов -------------------------------------------------------------


def _is_hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith("."):
        return True
    if os.name != "nt":
        return False
    try:
        attrs = entry.stat(follow_symlinks=False).st_file_attributes  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        return False
    return bool(attrs & 0x2) or bool(attrs & 0x4)


def collect_files(
    root: Path,
    recursive: bool,
    skip_system: bool,
    cancel: threading.Event | None = None,
    skipped_exec: list[Path] | None = None,
) -> list[Path]:
    found: list[Path] = []
    if not recursive:
        with os.scandir(wp(root)) as entries:
            for entry in entries:
                if cancel is not None and cancel.is_set():
                    break
                try:
                    if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                if skip_system and (entry.name.lower() in SKIP_NAMES or _is_hidden(entry)):
                    continue
                if is_executable(Path(entry.path)):
                    if skipped_exec is not None:
                        skipped_exec.append(Path(entry.path))
                    continue
                found.append(Path(entry.path))
        return found

    for dirpath, dirnames, filenames in os.walk(wp(root), followlinks=False):
        if cancel is not None and cancel.is_set():
            break
        dirnames[:] = [
            d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))
            and not (skip_system and d.startswith("."))
        ]
        for name in filenames:
            if cancel is not None and cancel.is_set():
                break
            if skip_system and name.lower() in SKIP_NAMES:
                continue
            if is_executable(Path(name)):
                if skipped_exec is not None:
                    skipped_exec.append(Path(os.path.join(dirpath, name)))
                continue
            full = Path(os.path.join(dirpath, name))
            if full.is_symlink():
                continue
            if skip_system and os.name == "nt":
                try:
                    attrs = os.stat(wp(full), follow_symlinks=False).st_file_attributes  # type: ignore[attr-defined]
                    if attrs & 0x2 or attrs & 0x4:
                        continue
                except OSError:
                    continue
            found.append(full)
    return found


# --- планирование ------------------------------------------------------------


def is_already_placed(path: Path, dt: datetime, cfg: dict) -> bool:
    """Файл уже назван по шаблону и лежит в своей вложенной папке (год/месяц)."""
    folder_template = cfg.get("year_folder", "{YYYY}")
    parts = tuple(_render(p, dt, "", "", "") for p in folder_template.split("/"))
    if path.parent.parts[-len(parts):] != parts:
        return False
    # Без переименования имя не трогаем: достаточно, что папка уже верная.
    if cfg.get("keep_names"):
        return True
    template = cfg.get("template", "")
    expected_stem = _render(template, dt, path.stem, path.suffix.lstrip("."), "")
    expected_stem = expected_stem.replace("{MMM}", MONTHS_RU.get(dt.month, ""))
    if path.stem == expected_stem:
        return True
    return bool(re.fullmatch(re.escape(expected_stem) + r"_\d+", path.stem))


def _blocking_part(root: Path, folder: Path) -> Path | None:
    current = root
    for part in folder.relative_to(root).parts:
        current = current / part
        if current.exists() and not current.is_dir():
            return current
    return None


def plan(
    root: Path,
    cfg: dict,
    cancel: threading.Event | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> PlanResult:
    result = PlanResult()
    now = datetime.now()
    skipped_exec: list[Path] = []
    paths = collect_files(
        root,
        bool(cfg.get("recursive")),
        bool(cfg.get("skip_system_files")),
        cancel,
        skipped_exec,
    )
    paths.sort(key=lambda p: (str(p.parent).lower(), p.name.lower()))

    template = cfg.get("template") or "{YYYY}-{MM}-{DD}_{HH}-{mm}-{ss}"
    folder_template = cfg.get("year_folder") or "{YYYY}"
    total = len(paths)


    taken: set[str] = set()
    existing: dict[str, set[str]] = {}
    next_attempt: dict[str, int] = {}
    year_blocked: dict[str, str] = {}

    # Исполняемые файлы не раскладываются: переименование ломает программы.
    for blocked in skipped_exec:
        result.items.append(
            PlanItem(
                src=blocked,
                name=blocked.name,
                ext=blocked.suffix.lstrip("."),
                status=SKIPPED,
                note="исполняемый файл — пропущен",
            )
        )

    for index, path in enumerate(paths, start=1):
        if cancel is not None and cancel.is_set():
            break
        item = PlanItem(src=path, name=path.name, ext=path.suffix.lstrip("."))
        try:
            st = os.stat(wp(path), follow_symlinks=False)
        except OSError as exc:
            item.status = ERROR
            item.note = human_error(exc)
            result.items.append(item)
            if progress:
                progress(index, total)
            continue

        dt, source = detect_date(path, st, cfg)
        item.dt, item.source = dt, source

        if not _sane(dt, now):
            item.status = BAD_DATE
            item.note = "дата вне допустимого диапазона"
            result.items.append(item)
            if progress:
                progress(index, total)
            continue

        assert dt is not None
        if is_already_placed(path, dt, cfg):
            item.status = UNCHANGED
            item.folder_label = "/".join(path.parent.parts[-len(folder_template.split("/")):])
            item.dst_path = path
            result.items.append(item)
            if progress:
                progress(index, total)
            continue

        folder_name = _render(folder_template, dt, "", "", "")
        if folder_name in year_blocked:
            item.status = ERROR
            item.note = year_blocked[folder_name]
            result.items.append(item)
            if progress:
                progress(index, total)
            continue

        folder_path = root.joinpath(*folder_name.split("/"))
        blocker = _blocking_part(root, folder_path)
        if blocker is not None:
            note = f"В корне есть файл «{blocker.name}», нужна папка — год пропущен."
            year_blocked[folder_name] = note
            result.folder_errors.append(note)
            item.status = ERROR
            item.note = note
            result.items.append(item)
            if progress:
                progress(index, total)
            continue

        if cfg.get("keep_names"):
            # Раскладка без переименования: имя остаётся прежним.
            dst_name = path.name
        else:
            counter = f"{index:03d}" if "{N}" in template else ""
            dst_name = render_name(
                template, dt, path.stem, path.suffix.lstrip("."), counter
            )
        dst_name = _dedupe(folder_path, dst_name, taken, existing, next_attempt)
        item.dst_name = dst_name
        item.folder_label = folder_name
        item.dst_path = folder_path / dst_name
        result.items.append(item)
        if progress:
            progress(index, total)

    return result


def _folder_listing(folder: Path, existing: dict[str, set[str]]) -> set[str]:
    """Содержимое папки читается с диска один раз, а не на каждый конфликт."""
    key = os.path.normcase(str(folder))
    cached = existing.get(key)
    if cached is None:
        try:
            cached = {name.lower() for name in os.listdir(wp(folder))}
        except OSError:
            cached = set()
        existing[key] = cached
    return cached


def _dedupe(
    folder: Path,
    name: str,
    taken: set[str],
    existing: dict[str, set[str]],
    next_attempt: dict[str, int],
) -> str:
    """Подбирает свободное имя: имя, имя_2, имя_3, … Никогда не перезаписывает цель."""
    listing = _folder_listing(folder, existing)
    prefix = os.path.normcase(str(folder)) + "|"

    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""

    candidate = name
    attempt = next_attempt.get(prefix + name, 2)
    while True:
        low = candidate.lower()
        if low not in listing and (prefix + low) not in taken:
            taken.add(prefix + low)
            listing.add(low)
            next_attempt[prefix + name] = attempt
            return candidate
        candidate = f"{stem}_{attempt}{dot}{ext}"
        attempt += 1


# --- исполнение --------------------------------------------------------------


def human_error(exc: BaseException) -> str:
    name = type(exc).__name__
    winerror = getattr(exc, "winerror", None)
    if isinstance(exc, PermissionError) or winerror == 5:
        return "нет прав доступа"
    if isinstance(exc, FileNotFoundError):
        return "файл не найден"
    if winerror in (32, 33):
        return "файл занят другой программой"
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        return "на диске нет места"
    return f"ошибка: {name}"


def open_journal(settings_dir: Path) -> tuple[Path, Any]:
    settings_dir.mkdir(parents=True, exist_ok=True)
    path = settings_dir / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}.jsonl"
    suffix = 1
    while path.exists():
        path = settings_dir / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_{suffix}.jsonl"
        suffix += 1
    handle = open(path, "w", encoding="utf-8", buffering=1)
    return path, handle


def cleanup_journals(folder: Path, retention: int) -> None:
    try:
        files = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for stale in files[max(retention, 1):]:
        try:
            stale.unlink()
        except OSError:
            pass


def execute(
    root: Path,
    plan_result: PlanResult,
    cfg: dict,
    journal_folder: Path,
    progress: Callable[[int, int, PlanItem], None] | None = None,
    cancel: threading.Event | None = None,
) -> RunStats:
    started = time.perf_counter()
    stats = RunStats()
    mode = cfg.get("mode", "inplace")
    todo = plan_result.actionable()
    total = len(todo)

    if mode == "dry":
        stats.processed = len(plan_result.items)
        stats.renamed = total
        stats.elapsed = time.perf_counter() - started
        return stats

    cleanup_journals(journal_folder, int(cfg.get("journal_retention", 20)))
    journal_path, handle = open_journal(journal_folder)
    stats.journal_path = journal_path

    try:
        for index, item in enumerate(todo, start=1):
            if cancel is not None and cancel.is_set():
                stats.cancelled = True
                break
            assert item.dst_path is not None
            try:
                item.dst_path.parent.mkdir(parents=True, exist_ok=True)
                if os.path.exists(wp(item.dst_path)):
                    raise FileExistsError(str(item.dst_path))
                # Только перемещение в пределах тома: место на диске не удваивается.
                # os.rename не трогает ни содержимое, ни время изменения файла.
                # os.utime здесь НЕ вызывается: приложение не имеет права переписывать
                # реальные даты файла — ни «изменён», ни «создан».
                os.rename(wp(item.src), wp(item.dst_path))
                action = "moved"
                handle.write(
                    json.dumps(
                        {
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "src": str(item.src),
                            "dst": str(item.dst_path),
                            "status": action,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                handle.flush()
                stats.renamed += 1
            except Exception as exc:  # noqa: BLE001 - ошибка одного файла не должна ломать пакет
                item.status = ERROR
                item.note = human_error(exc)
                stats.failed.append(item)
                stats.errors += 1
                handle.write(
                    json.dumps(
                        {
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "src": str(item.src),
                            "dst": str(item.dst_path),
                            "status": "error",
                            "error": item.note,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                handle.flush()
            if progress:
                progress(index, total, item)
        stats.unchanged = sum(1 for i in plan_result.items if i.status == UNCHANGED)
        stats.skipped = sum(1 for i in plan_result.items if i.status == BAD_DATE)
        stats.processed = len(plan_result.items)
    finally:
        handle.close()

    stats.elapsed = time.perf_counter() - started
    return stats


def retry_failed(items: Sequence[PlanItem], cfg: dict, journal_folder: Path) -> RunStats:
    """Повторная попытка для файлов, которые были заняты."""
    started = time.perf_counter()
    stats = RunStats()
    todo = [i for i in items if i.status == ERROR and i.dst_path is not None]
    if not todo:
        stats.elapsed = time.perf_counter() - started
        return stats

    journal_path, handle = open_journal(journal_folder)
    stats.journal_path = journal_path
    try:
        for item in todo:
            assert item.dst_path is not None
            try:
                item.dst_path.parent.mkdir(parents=True, exist_ok=True)
                if os.path.exists(wp(item.dst_path)):
                    raise FileExistsError(str(item.dst_path))
                os.rename(wp(item.src), wp(item.dst_path))
                item.status = RENAME
                item.note = ""
                stats.renamed += 1
                handle.write(
                    json.dumps(
                        {
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "src": str(item.src),
                            "dst": str(item.dst_path),
                            "status": "moved",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                handle.flush()
            except Exception as exc:  # noqa: BLE001
                item.note = human_error(exc)
                stats.failed.append(item)
                stats.errors += 1
    finally:
        handle.close()
    stats.elapsed = time.perf_counter() - started
    return stats


# --- откат -------------------------------------------------------------------


@dataclass
class RollbackStats:
    restored: int = 0
    skipped: int = 0
    errors: int = 0
    details: list[str] = field(default_factory=list)


def rollback(journal_path: Path) -> RollbackStats:
    stats = RollbackStats()
    records: list[dict] = []
    try:
        with open(journal_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
    except OSError as exc:
        stats.errors += 1
        stats.details.append(human_error(exc))
        return stats

    for record in reversed(records):
        if record.get("status") == "error":
            continue
        src = Path(record.get("src", ""))
        dst = Path(record.get("dst", ""))
        if not record.get("src") or not record.get("dst"):
            continue
        try:
            if not os.path.exists(wp(dst)):
                stats.skipped += 1
                stats.details.append(f"нет файла: {dst.name}")
                continue
            if os.path.exists(wp(src)):
                stats.skipped += 1
                stats.details.append(f"имя занято: {src.name}")
                continue
            src.parent.mkdir(parents=True, exist_ok=True)
            if src.parent.exists() and not os.listdir(wp(src.parent)):
                pass
            shutil.move(wp(dst), wp(src))
            stats.restored += 1
        except Exception as exc:  # noqa: BLE001
            stats.errors += 1
            stats.details.append(human_error(exc))
    _prune_empty(records)
    return stats


def _prune_empty(records: Iterable[dict]) -> None:
    """Удаляет пустые папки, созданные раскладкой. Папки исходников и их родители не трогает."""
    records = list(records)
    protected: set[Path] = set()
    for r in records:
        if r.get("src"):
            protected.update(Path(r["src"]).parents)
    for r in records:
        if not r.get("dst"):
            continue
        folder = Path(r["dst"]).parent
        while folder not in protected and folder.is_dir():
            try:
                if os.listdir(wp(folder)):
                    break
                folder.rmdir()
            except OSError:
                break
            folder = folder.parent


def list_journals(folder: Path) -> list[Path]:
    try:
        return sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []


def journal_summary(path: Path) -> dict:
    total = moved = errors = 0
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                total += 1
                if record.get("status") == "error":
                    errors += 1
                else:
                    moved += 1
    except OSError:
        pass
    return {"total": total, "moved": moved, "errors": errors}


# --- защита от системных папок ----------------------------------------------
#
# Программа переименовывает всё, что найдёт в выбранной папке. Если выбрать
# Windows или Program Files, разложатся по датам файлы программ, и приложения
# перестанут запускаться. Права администратора от такой ошибки не спасают —
# при их наличии запись пройдёт. Поэтому такие пути запрещены явно.

# Переменные окружения Windows, содержимое которых трогать нельзя.
PROTECTED_ENV_VARS = (
    "SystemRoot",
    "windir",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "ProgramFilesArm",
    "ProgramW6432",
    "ProgramData",
    "CommonProgramFiles",
    "CommonProgramFiles(x86)",
)

# Имена папок, которые запрещены на системном диске.
PROTECTED_DIR_NAMES = {
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
}

# Каталоги POSIX: тот же смысл для Linux и macOS.
PROTECTED_POSIX_DIRS = (
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

# Исполняемые файлы: переименование ломает программы и ярлыки.
EXECUTABLE_SUFFIXES = {
    ".exe",
    ".dll",
    ".sys",
    ".msi",
    ".msix",
    ".msp",
    ".com",
    ".scr",
    ".cpl",
    ".ocx",
    ".drv",
    ".lnk",
    ".bat",
    ".cmd",
    ".ps1",
    ".vbs",
    ".jar",
    ".appx",
}


def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, ValueError):
        return path


def _drive_of(path: Path) -> str:
    """Диск или корень, к которому относится путь. Для сравнения путей между собой."""
    text = os.path.normcase(str(path))
    return os.path.splitdrive(text)[0] or "/"


def _under(target: str, base: str) -> bool:
    """target лежит внутри base или совпадает с ним. Разделители приводятся к «/».

    Сравнивать через os.sep нельзя: на Linux он «/», и Windows-путь
    «C:\\Windows\\System32» не распознавался бы как вложенный в «C:\\Windows».
    """
    target = target.replace("\\", "/").rstrip("/")
    base = base.replace("\\", "/").rstrip("/")
    if not base:
        return False
    return target == base or target.startswith(base + "/")


def protected_reason(path: Path) -> str | None:
    """Почему в эту папку нельзя раскладывать файлы. None — можно."""
    resolved = _resolve(path)

    if resolved.parent == resolved:
        return (
            "Это корень диска. Выберите папку внутри него — "
            "раскладывать файлы в корне нельзя."
        )

    target = os.path.normcase(str(resolved))
    system_drive = _drive_of(Path(os.environ.get("SystemRoot", "C:\\")))

    for var in PROTECTED_ENV_VARS:
        raw = os.environ.get(var)
        if not raw:
            continue
        try:
            candidate = os.path.normcase(str(_resolve(Path(raw))))
        except (OSError, ValueError):
            continue
        if candidate and _under(target, candidate):
            return (
                f"Это системная папка «{Path(raw).name}» — в ней лежат файлы "
                "Windows и установленных программ. Выберите свою папку с документами "
                "или фотографиями."
            )

    # Имена вроде «Windows» или «Program Files» запрещены только на системном
    # диске Windows: на Linux папка пользователя с таким именем безобидна.
    if (
        os.name == "nt"
        and resolved.name.lower() in PROTECTED_DIR_NAMES
        and _drive_of(resolved) == system_drive
    ):
        return (
            f"«{resolved.name}» — системная папка Windows. "
            "Переименование файлов внутри неё нарушит работу системы."
        )

    for base in PROTECTED_POSIX_DIRS:
        if _under(target, base):
            return (
                f"Это системный каталог «{base}» — в нём файлы операционной системы "
                "и установленных программ. Выберите свою папку с документами "
                "или фотографиями."
            )

    return None


def is_executable(path: Path) -> bool:
    """Исполняемый файл, который нельзя переименовывать."""
    return path.suffix.lower() in EXECUTABLE_SUFFIXES


def check_folder(path: Path) -> str | None:
    """Валидация выбранной папки. Возвращает текст ошибки или None."""
    if not path.exists():
        return "Такой папки нет."
    if not path.is_dir():
        return "Это файл, а не папка."
    reason = protected_reason(path)
    if reason:
        return reason
    if not os.access(wp(path), os.R_OK):
        return "Нет прав на чтение этой папки."
    if not os.access(wp(path), os.W_OK):
        return "Нет прав на запись в эту папку — переименовать не получится."
    return None


def is_inside_year_folder(path: Path, cfg: dict) -> bool:
    """Грубая защита: папка уже выглядит как папка года."""
    pattern = cfg.get("year_folder", "{YYYY}").split("/")[0]
    if "{YYYY}" in pattern:
        return bool(re.fullmatch(pattern.replace("{YYYY}", r"\d{4}").replace("{YY}", r"\d{2}"), path.name))
    return False
