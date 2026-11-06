"""Интерфейс «Архивариуса». Вся логика работы с файлами — в core.py.

Вёрстка повторяет присланные макеты: белый фон без карточек и шапки,
скруглённые элементы, маскот на главном экране, в настройках и на результате.
Шрифт — Poppins, радиусы: 16 у больших кнопок, 8 у маленьких.
Окно фиксированного размера 900×620.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import core
import fonts
import settings as cfgmod
import version
import widgets as ui_kit

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DND_AVAILABLE = True
except Exception:  # noqa: BLE001 - приложение обязано работать и без библиотеки
    DND_FILES = None
    TkinterDnD = None
    DND_AVAILABLE = False

APP_TITLE = "Архивариус"
LOG = logging.getLogger(cfgmod.APP_NAME)

# --- палитра из макета -------------------------------------------------------
GREEN = "#2F7D32"
GREEN_DARK = "#26632A"
GREEN_SOFT = "#EBF2EB"
GOLD = "#FFDAAC"
INK = "#1F2A20"
MUTED = "#6B7B6E"
DANGER = "#B3261E"
BG = "#FFFFFF"
CARD = "#FFFFFF"
LINE = "#D7E4D7"

# Совместимость со старыми именами.
ACCENT = GREEN
ACCENT_DARK = GREEN_DARK
BLUE = GREEN
GOLD_SOFT = GOLD

STRINGS = {
    "tagline": "Раскладывает файлы по годам",
    "drop": "Перетащите сюда папку",
    "or": "или",
    "choose": "Выберите папку",
    "recent": "Недавние папки",
    "recent_empty": "Пока пусто — выберите папку",
    "template": "Шаблон названия файла",
    "year_folder": "Шаблон названия папки",
    "mode": "Режим работы",
    "mode_inplace": "Переименовать и разложить",
    "mode_dry": "Только отчёт, ничего не менять",
    "recursive": "Включая вложенные папки",
    "filename_date": "Брать дату из имени файла, если других нет",
    "skip_system": "Пропускать системные и скрытые файлы",
    "keep_names": "Раскладывать без переименования",
    "preview_title": "Просмотр",
    "col_old": "Имя сейчас",
    "col_new": "Имя после",
    "col_folder": "Папка",
    "col_source": "Источник даты",
    "col_status": "Статус",
    "btn_go": "Навести порядок!",
    "btn_back": "Назад",
    "btn_cancel": "Отмена",
    "btn_save": "Сохранить",
    "btn_reset": "Сбросить настройки",
    "working": "Работаю…",
    "thanks": "Вернуться к работе",
    "open_folder": "Открыть папку",
    "undo": "Отменить изменения",
    "retry": "Повторить пропущенные",
    "nothing": "Здесь пока нечего раскладывать",
    "help_title": "Как это работает",
}

MODES = [("inplace", STRINGS["mode_inplace"]), ("dry", STRINGS["mode_dry"])]

# Колонки таблицы просмотра. Сумма 838 = ширина рабочей области.
COLUMNS = (
    ("old", 210),
    ("new", 220),
    ("folder", 88),
    ("source", 175),
    ("status", 145),
)

EXPLANATIONS = {
    core.SRC_EXIF_ORIGINAL: "дата съёмки из EXIF внутри файла. Её ставит камера или телефон, "
    "она переживает копирование и не зависит от свойств Windows.",
    core.SRC_EXIF_DATETIME: "дата изменения из EXIF внутри файла. Съёмки в тегах нет, но есть "
    "время правки снимка.",
    core.SRC_META_CREATED: "дата создания из метаданных документа. Хранится внутри файла, "
    "копирование её не меняет.",
    core.SRC_META_MODIFIED: "дата изменения из метаданных документа. Более поздней даты "
    "в файле нет.",
    core.SRC_TEMPLATE_NAME: "дата уже есть в имени файла — оно собрано по шаблону.",
    core.SRC_FILENAME: "дата распознана в исходном имени файла.",
    core.SRC_MTIME: "времени создания содержимого в файле нет, поэтому взято время изменения "
    "файла. Оно сохраняется при копировании.",
    core.SRC_CTIME: "никаких дат внутри файла нет, взято время создания файла в Windows. "
    "У скачанных и скопированных файлов это дата скачивания, а не съёмки.",
    core.SRC_NONE: "дат не найдено, файл оставлен без изменений.",
}

# --- справка -------------------------------------------------------------
# Содержимое окна «i». Список пар: заголовок раздела → текст.

HELP_SECTIONS: list[tuple[str, str, str]] = [
    (
        "icon_wand",
        "Что делает программа",
        "Берёт папку, где свалены файлы, и раскладывает их по годам и месяцам. Каждому файлу достаётся понятное имя — по дате создания.\nНичего не удаляется и не перезаписывается: файлы только **переезжают**.",
    ),
    (
        "icon_clock",
        "Откуда берётся дата",
        "Сначала программа смотрит внутрь файла: у фотографий это EXIF, у PDF, документов и видео — свойства самого файла.\nЕсли внутри даты нет — читает имя файла, потом время изменения.\nИз всех найденных побеждает **самая ранняя** — она ближе всего к реальной.",
    ),
    (
        "icon_folder",
        "Как будут выглядеть папки",
        "**Файл «IMG_4823.jpg»**, снятый 16 ноября 2012 года, станет «2012/11/2012-11-16_16-09-52.jpg».\nШаблон меняется в настройках: «{YYYY}» — только годы, «{YYYY}/{MM}» — годы и месяцы.",
    ),
    (
        "icon_undo",
        "Если что-то пошло не так",
        "Кнопка «Отменить изменения» вернёт все файлы на прежние места — по журналу операций. Журнал хранится **отдельно** и не удаляется сам.\nВыключить компьютер посреди работы тоже не страшно: каждый перенос записывается сразу.",
    ),
    (
        "icon_check",
        "Чего программа не тронет",
        "Системные папки — Windows, Program Files, ProgramData, корень диска — она просто не откроет.\nИсполняемые файлы (.exe, .dll, .sys, .lnk, .bat) пропускаются и остаются на месте.",
    ),
    (
        "icon_check",
        "Безопасно и без интернета",
        "Программа не подключается к сети и ничего никуда не отправляет. Права администратора не нужны.\nСовет: перед первым запуском включите режим **«Только отчёт»** — он покажет план и ничего не изменит.",
    ),
]

HELP_W, HELP_H = 660, 640
HELP_PAD = 28  # поля окна справки слева и справа
HELP_SCROLLBAR = 18  # ширина полосы прокрутки
HELP_ICON_SIZE = 22  # сторона зелёного значка в заголовке раздела

WINDOW_W, WINDOW_H = 900, 620
MARGIN = 24  # поля по краям экрана
HOME_GAP = 22  # одинаковый промежуток между блоками главного экрана
HELP_BUTTON = 40  # сторона квадратной кнопки справки
RADIUS_HELP = 20  # скругление кнопки справки: четверть стороны — остаётся квадрат
HOME_TEXT_SIZE = 10  # кегль пояснения и подписи «Недавние папки»
SETTINGS_TOP = 8  # отступ заголовка «Настройки» от верхнего края окна
SETTINGS_TITLE_OFFSET = 38  # от заголовка до первого поля;
SETTINGS_TITLE_GAP = 24  # расстояние от заголовка до поля ввода в настройках
SELECT_RADIUS = 16  # радиус скругления селекта «Недавние папки»
CONTENT_X = 31
CONTENT_W = 838  # рабочая ширина


def reduce_motion() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        enabled = ctypes.c_int(0)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            0x1042, 0, ctypes.byref(enabled), 0
        )
        return bool(ok) and not enabled.value
    except Exception:  # noqa: BLE001
        return False


def plural(number: int, one: str, few: str, many: str) -> str:
    if number % 10 == 1 and number % 100 != 11:
        return one
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return few
    return many


def fmt_duration(seconds: float) -> str:
    if seconds < 1:
        return "меньше секунды"
    if seconds < 60:
        return f"{seconds:.0f} {plural(int(seconds), 'секунда', 'секунды', 'секунд')}"
    minutes = int(seconds // 60)
    return f"{minutes} {plural(minutes, 'минута', 'минуты', 'минут')}"


def load_photo(path: Path, box: tuple[int, int]) -> tk.PhotoImage | None:
    if not path.is_file():
        return None
    try:
        from PIL import Image, ImageTk

        with Image.open(path) as image:
            image = image.convert("RGBA")
            image.thumbnail(box, Image.LANCZOS)
            return ImageTk.PhotoImage(image)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Не удалось загрузить %s: %s", path, exc)
        return None


def load_banner(path: Path, width: int) -> tk.PhotoImage | None:
    """Картинка точно заданной ширины, высота — по её пропорции.

    load_photo() вызывает thumbnail(), а он умеет только уменьшать: картинка
    мельче нужного размера остаётся как есть и не доходит до краёв. Здесь
    resize() — масштаб работает в обе стороны.
    """
    if not path.is_file() or width <= 0:
        return None
    try:
        from PIL import Image, ImageTk

        with Image.open(path) as image:
            image = image.convert("RGBA")
            ratio = image.height / image.width if image.width else 0.5625
            height = max(1, round(width * ratio))
            image = image.resize((width, height), Image.LANCZOS)
            return ImageTk.PhotoImage(image)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Не удалось загрузить %s: %s", path, exc)
        return None


MARKUP_RE = re.compile(r"\*\*(.+?)\*\*")


def help_entry(entry):
    """Раздел справки в любом виде.

    Ожидается (иконка, заголовок, текст). Записи старого формата
    (заголовок, текст) и случайно обёрнутые лишний раз — тоже принимаются:
    иначе одна опечатка в HELP_SECTIONS роняет всё окно справки.
    """
    if isinstance(entry, (tuple, list)):
        if len(entry) == 3:
            return entry
        if len(entry) == 2:
            return None, entry[0], entry[1]
        if len(entry) == 1:
            return help_entry(entry[0])
    raise ValueError(f"Непонятный раздел справки: {entry!r}")


def _insert_markup(text: tk.Text, chunk: str, base_tag: str) -> None:
    """Вставляет текст, превращая **вот такое** в зелёный текст без смены начертания.

    Обычный insert() не умеет красить часть строки, поэтому строка режется
    на куски: до выделения, само выделение, после. Звёздочки не попадают
    в текст — они только разметка в HELP_SECTIONS.
    """
    position = 0
    for match in MARKUP_RE.finditer(chunk):
        text.insert("end", chunk[position : match.start()], base_tag)
        text.insert("end", match.group(1), (base_tag, "hl"))
        position = match.end()
    text.insert("end", chunk[position:], base_tag)


BaseClass = TkinterDnD.Tk if DND_AVAILABLE else tk.Tk


class App(BaseClass):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = cfgmod.load_settings()
        self.title(f"{APP_TITLE} {version.APP_VERSION}")
        self.geometry(f"{WINDOW_W}x{WINDOW_H}")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._set_window_icon()

        self.folder: Path | None = None
        self.plan_result: core.PlanResult | None = None
        self.run_stats: core.RunStats | None = None
        self.last_failed: list[core.PlanItem] = []
        self._cancel = threading.Event()
        self._queue: queue.Queue = queue.Queue()
        self._progress = {"done": 0, "total": 0}
        self._anim_frames: list[tk.PhotoImage] = []
        self._anim_after: str | None = None
        self._thanks_after: str | None = None
        # Откуда открыли настройки: чтобы вернуться туда же, а не на главный.
        self._settings_origin = "home"

        self._build_styles()

        self.screens: dict[str, tk.Frame] = {}
        self._build_home()
        self._build_preview()
        self._build_progress()
        self._build_result()
        self._build_journal()
        self._build_settings()

        self.current = "home"
        self.show("home")
        self.bind("<Escape>", self._on_escape)
        self.bind("<Return>", self._on_enter)
        self._poll()

        if DND_AVAILABLE:
            try:
                self.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
                self.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Drag and drop недоступен: %s", exc)

        initial = sys.argv[1] if len(sys.argv) > 1 else ""
        if initial:
            self.after(120, lambda: self._start_scan(Path(initial).expanduser()))

    # --- окно и иконка -------------------------------------------------------

    def _set_window_icon(self) -> None:
        icon = cfgmod.assets_dir() / "icon.ico"
        if not icon.is_file():
            return
        for setter in (
            lambda: self.iconbitmap(default=str(icon)),
            lambda: self.iconbitmap(str(icon)),
        ):
            try:
                setter()
                return
            except tk.TclError:
                continue
        try:
            from PIL import Image, ImageTk

            image = Image.open(icon)
            sizes = sorted(image.ico.sizes()) if hasattr(image, "ico") else [(64, 64)]
            photos = []
            for size in sizes:
                image.size = size
                photos.append(ImageTk.PhotoImage(image.convert("RGBA")))
            if photos:
                self._icon_photos = photos
                self.iconphoto(True, *photos)
        except Exception:  # noqa: BLE001
            pass

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        row_font = ui_kit.font(10)
        style.configure(
            "Treeview",
            font=row_font,
            rowheight=32,
            background=BG,
            fieldbackground=BG,
            borderwidth=0,
            relief="flat",
        )
        style.map(
            "Treeview",
            background=[("selected", GREEN_SOFT)],
            foreground=[("selected", GREEN)],
        )
        # Заголовки рисуем на Canvas, поэтому штатные отключаем.
        style.configure("Treeview.Heading", font=ui_kit.font(10), borderwidth=0)

        style.configure(
            "TCombobox",
            padding=(12, 8),
            fieldbackground=GREEN_SOFT,
            background=GREEN_SOFT,
            foreground=GREEN,
            bordercolor=LINE,
            arrowcolor=GREEN,
            arrowsize=16,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", GREEN_SOFT)],
            foreground=[("readonly", GREEN)],
            bordercolor=[("focus", GREEN)],
        )
        self.option_add("*TCombobox*Listbox.font", row_font)

        style.configure(
            "Green.Horizontal.TProgressbar",
            background=GREEN,
            troughcolor=GREEN_SOFT,
            borderwidth=0,
            thickness=10,
        )

    def _screen(self, name: str) -> tk.Frame:
        frame = tk.Frame(self, bg=BG, width=WINDOW_W, height=WINDOW_H)
        self.screens[name] = frame
        return frame

    def show(self, name: str) -> None:
        self._stop_animation()
        for frame in self.screens.values():
            frame.place_forget()
        self.screens[name].place(x=0, y=0, width=WINDOW_W, height=WINDOW_H)
        self.current = name

    def _label(
        self,
        master,
        text: str,
        size: int = 11,
        color: str = INK,
        weight: str = "normal",
        **kwargs,
    ) -> tk.Label:
        return tk.Label(
            master, text=text, bg=BG, fg=color, font=ui_kit.font(size, weight), **kwargs
        )

    def _open_help(self) -> None:
        """Окно справки. Одно на всё приложение: повторное нажатие поднимает его."""
        existing = getattr(self, "_help_window", None)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            return

        window = tk.Toplevel(self)
        self._help_window = window
        window.title(APP_TITLE)
        window.configure(bg=BG)
        window.resizable(False, False)
        window.transient(self)

        # Окно по центру главного.
        self.update_idletasks()
        x = self.winfo_rootx() + (WINDOW_W - HELP_W) // 2
        y = self.winfo_rooty() + max(20, (WINDOW_H - HELP_H) // 2)
        window.geometry(f"{HELP_W}x{HELP_H}+{x}+{y}")

        header = tk.Frame(window, bg=BG)
        header.pack(fill="x", padx=28, pady=(24, 0))

        tk.Label(
            header,
            text=STRINGS["help_title"],
            bg=BG,
            fg=GREEN,
            font=ui_kit.font(18, "bold"),
            anchor="w",
        ).pack(side="left")

        close_btn = ui_kit.RoundButton(
            header,
            "Закрыть",
            command=window.destroy,
            kind="primary",
            width=104,
            height=34,
            font_size=10,
            bg=BG,
        )
        close_btn.pack(side="right")

        body = tk.Frame(window, bg=BG)
        body.pack(fill="both", expand=True, padx=28, pady=(16, 8))

        text = tk.Text(
            body,
            bg=BG,
            fg=INK,
            bd=0,
            highlightthickness=0,
            wrap="word",
            cursor="arrow",
            font=ui_kit.font(11),
            spacing1=2,
            spacing3=6,
            padx=0,
            pady=0,
        )
        scroll = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)

        text.tag_configure(
            "head_icon",
            spacing1=14,
        )
        text.tag_configure(
            "head",
            font=ui_kit.font(12, "bold"),
            foreground=GREEN,
            spacing1=10,
            spacing3=4,
        )
        text.tag_configure(
            "body",
            font=ui_kit.font(11),
            foreground=INK,
            lmargin1=0,
            lmargin2=0,
            spacing3=10,
        )
        # Выделение цветом внутри абзаца: **текст** в HELP_SECTIONS.
        # Цвет — основной зелёный GREEN, как у заголовков и кнопок.
        # Только цвет: начертание остаётся обычным, как у всего абзаца.
        text.tag_configure("hl", foreground=GREEN)
        text.tag_configure("banner_line", justify="center", spacing1=0, spacing3=0)

        # Картинка 16:9 в начале. Нет файла — справка работает без неё.
        # Ширину берём у самого виджета, а не константой: тогда картинка
        # упирается в те же края, что и текст, при любом размере окна.
        text.update_idletasks()
        banner_w = text.winfo_width()
        if banner_w <= 1:  # окно ещё не разложено — считаем от размера окна
            banner_w = HELP_W - HELP_PAD * 2 - HELP_SCROLLBAR
        banner_w -= 2  # запас, чтобы картинка не задевала полосу прокрутки

        banner = load_banner(cfgmod.assets_dir() / "Archivarius.jpg", banner_w)
        if banner is not None:
            window._banner = (
                banner  # ссылка: иначе Tk соберёт картинку сборщиком мусора
            )
            text.insert("end", "\n", "banner_line")
            text.image_create("end", image=banner)
            text.insert("end", "\n\n", "banner_line")

        # Ссылки на картинки: без них Tk соберёт их сборщиком мусора.
        window._help_icons = []
        for entry in HELP_SECTIONS:
            icon, title, paragraph = help_entry(entry)
            photo = ui_kit.load_badge(icon, HELP_ICON_SIZE) if icon else None
            if photo is not None:
                window._help_icons.append(photo)
                text.insert("end", "\n", "head_icon")
                text.image_create("end", image=photo, align="center")
                text.insert("end", "  " + title + "\n", "head")
            else:
                # Нет файла иконки — заголовок без неё, справка работает.
                text.insert("end", title + "\n", "head")
            _insert_markup(text, paragraph + "\n", "body")
        text.configure(state="disabled")

        window.bind("<Escape>", lambda _e: window.destroy())
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        window.focus_force()

    def _open_settings(self) -> None:
        """Запоминает текущий экран, чтобы «Назад» вернул именно туда."""
        self._settings_origin = self.current if self.current != "settings" else "home"
        self.show("settings")

    def _settings_back(self) -> None:
        target = self._settings_origin
        if target == "preview" and self.plan_result is not None:
            self.show("preview")
        else:
            self.show("home")

    # --- главный экран -------------------------------------------------------

    def _build_home(self) -> None:
        """Маскот слева, справа одна колонка с равным вертикальным ритмом.

        Все блоки правой колонки разделены одинаковым промежутком HOME_GAP:
        кнопка настроек → пояснение → зона перетаскивания → недавние папки →
        главная кнопка.
        """
        frame = self._screen("home")

        # Квадратная кнопка справки в левом верхнем углу. Маскот начинается
        # ниже, поэтому кнопка его не перекрывает.
        self._help_button = ui_kit.RoundButton(
            frame,
            "",
            command=self._open_help,
            kind="primary",
            width=HELP_BUTTON,
            height=HELP_BUTTON,
            icon="icon_info",
            radius=RADIUS_HELP,
            bg=BG,
            icon_color="white",
        )
        self._help_button.place(x=MARGIN, y=MARGIN)

        photo = load_photo(cfgmod.assets_dir() / "mascot_idle.png", (390, 560))
        if photo is not None:
            holder = tk.Label(frame, image=photo, bg=BG, bd=0)
            holder.image = photo
            holder.place(x=MARGIN, y=40, width=390, height=560)

        # Кнопка справки должна быть поверх маскота: поднимаем её в стек.
        # Canvas переопределяет tkraise (поднимает элементы внутри холста),
        # поэтому вызываем версию из базового класса: поднимаем сам виджет.
        tk.Misc.tkraise(self._help_button)

        # В Tkinter нет z-index: порядок наложения задаётся порядком создания, и
        # маскот создан после кнопки, поэтому перекрывает её нижнюю половину.
        # Поднимаем кнопку наверх среди соседей по frame.
        # Важно: у Canvas метод lift() переопределён как tag_raise для фигур
        # внутри холста, поэтому вызываем подъём виджета явно через tk.Misc.
        tk.Misc.lift(self._help_button)

        # Одна ширина для всех элементов правой колонки, выровнены по центру.
        col_w = 440
        col_x = 425
        gap = HOME_GAP  # одинаковый промежуток между всеми блоками

        # --- 1. Кнопка настроек ---
        settings_h = 34
        y = MARGIN
        self._settings_button = ui_kit.RoundButton(
            frame,
            "Настройки",
            command=self._open_settings,
            kind="primary",
            width=125,
            height=settings_h,
            icon="icon_settings",
            font_size=10,
            bg=BG,
        )
        self._settings_button.place(x=744, y=y)
        y += settings_h + gap

        # --- 2. Пояснение (кегль как у «или» в зоне) ---
        hint = tk.Label(
            frame,
            text="Наведу порядок в указанной папке: распределю все файлы\n"
            "по годам и дам им правильные имена на основе даты создания.",
            bg=BG,
            fg=MUTED,
            font=ui_kit.font(HOME_TEXT_SIZE),
            justify="left",
            anchor="nw",
        )
        hint.place(x=col_x, y=y, width=col_w)
        hint.update_idletasks()
        y += hint.winfo_reqheight() + gap

        # --- 3. Зона перетаскивания ---
        zone_h = 250
        self.drop_zone = ui_kit.DropZone(
            frame, col_w, zone_h, bg=BG, onclick=self._choose_folder
        )
        self.drop_zone.place(x=col_x, y=y)

        # Три элемента внутри зоны с равными промежутками, группа по центру.
        hint_font = ui_kit.font(15)
        or_font = ui_kit.font(11)
        btn_h = 54
        inner_gap = 20
        hint_h, or_h = 22, 18
        group_h = hint_h + or_h + btn_h + inner_gap * 2
        top = (zone_h - group_h) / 2

        self.drop_zone.create_text(
            col_w / 2,
            top + hint_h / 2,
            text=STRINGS["drop"],
            fill=GREEN,
            font=hint_font,
        )
        self.drop_zone.create_text(
            col_w / 2,
            top + hint_h + inner_gap + or_h / 2,
            text=STRINGS["or"],
            fill=MUTED,
            font=or_font,
        )

        self.choose_button = ui_kit.RoundButton(
            frame,
            STRINGS["choose"],
            command=self._choose_folder,
            kind="primary",
            width=col_w - 80,
            height=btn_h,
            icon="icon_folder_big",
            font_size=13,
            bg=GREEN_SOFT,
        )
        self.choose_button.place(
            x=col_x + 40, y=y + top + hint_h + or_h + inner_gap * 2
        )
        y += zone_h + gap

        # --- 4. Недавние папки: подпись и селект как один блок ---
        recent_label = self._label(
            frame, STRINGS["recent"], HOME_TEXT_SIZE, MUTED, "normal", anchor="w"
        )
        recent_label.place(x=col_x, y=y, width=col_w)
        recent_label.update_idletasks()
        y += recent_label.winfo_reqheight() + 6

        self.recent_var = tk.StringVar()
        self.recent_box = ui_kit.RoundedSelect(
            frame,
            self.recent_var,
            width=col_w,
            height=40,
            font_size=11,
            radius=SELECT_RADIUS,
            bg=BG,
            placeholder=STRINGS["recent_empty"],
            command=self._on_recent,
        )
        self.recent_box.place(x=col_x, y=y)
        self._refresh_recent()
        y += 40 + gap

        # --- 5. Главная кнопка ---
        self.go_button = ui_kit.RoundButton(
            frame,
            STRINGS["btn_go"],
            command=self._scan_from_home,
            kind="primary",
            width=col_w,
            height=72,
            font_size=16,
            bg=BG,
        )
        self.go_button.place(x=col_x, y=y)

    def _refresh_recent(self) -> None:
        folders = [p for p in self.cfg.get("last_folders", []) if os.path.isdir(p)]
        self.recent_box.set_values(folders, select_first=True)

    def _on_recent(self, _value: str | None = None) -> None:
        value = _value or self.recent_var.get()
        if value:
            self._start_scan(Path(value))

    def _choose_folder(self) -> None:
        chosen = filedialog.askdirectory(title="Выберите папку с файлами")
        if chosen:
            self._start_scan(Path(chosen))

    def _on_drop(self, event) -> None:  # type: ignore[no-untyped-def]
        try:
            raw = self.tk.splitlist(event.data)
        except tk.TclError:
            raw = [event.data]
        for candidate in raw:
            path = Path(str(candidate).strip("{}"))
            if path.is_dir():
                self._start_scan(path)
                return
        messagebox.showinfo(
            APP_TITLE, "Нужна папка, а не файл. " "Перетащите папку целиком."
        )

    def _remember(self, folder: Path) -> None:
        folders = [str(folder)] + [
            p for p in self.cfg.get("last_folders", []) if str(p) != str(folder)
        ]
        self.cfg["last_folders"] = folders[:5]
        cfgmod.save_settings(self.cfg)
        self._refresh_recent()

    # --- сканирование --------------------------------------------------------

    def _scan_from_home(self) -> None:
        error = core.validate_template(
            self.cfg.get("template", "")
        ) or core.validate_year_folder(self.cfg.get("year_folder", ""))
        if error:
            messagebox.showwarning(
                APP_TITLE,
                "В настройках некорректный шаблон:\n"
                f"{error}\n\nОткройте настройки и исправьте.",
            )
            self._open_settings()
            return
        if self.folder is None:
            self._choose_folder()
            return
        self._start_scan(self.folder)

    def _start_scan(self, folder: Path) -> None:
        folder = folder.expanduser()
        problem = core.check_folder(folder)
        if problem:
            messagebox.showwarning(APP_TITLE, problem)
            return
        if core.is_inside_year_folder(folder, self.cfg):
            if not messagebox.askyesno(
                APP_TITLE,
                f"Похоже, «{folder.name}» — это уже папка одного года.\n"
                "Разложить файлы внутри неё по годам?",
            ):
                return
        self.folder = folder
        self._remember(folder)

        self._cancel = threading.Event()
        self._progress = {"done": 0, "total": 0}
        self.progress_label.config(text="Читаю папку…")
        self.progress_bar.config(mode="indeterminate")
        self.progress_bar.start(12)
        self.show("progress")
        threading.Thread(
            target=self._scan_worker, args=(folder, dict(self.cfg)), daemon=True
        ).start()

    def _scan_worker(self, folder: Path, cfg: dict) -> None:
        try:
            result = core.plan(folder, cfg, self._cancel, self._on_progress)
            self._queue.put(("plan", result))
        except Exception as exc:  # noqa: BLE001
            self._queue.put(("fatal", core.human_error(exc)))

    def _on_progress(self, done: int, total: int) -> None:
        self._progress = {"done": done, "total": total}

    # --- просмотр ------------------------------------------------------------

    def _build_preview(self) -> None:
        """Экран просмотра.

        Вертикального скролла нет: колонки подогнаны под ширину окна, длинные
        имена обрезаются. Заголовки рисуются на Canvas — так видно разделение
        столбцов зелёными линиями, чего штатный Treeview не умеет.
        """
        frame = self._screen("preview")

        self._label(
            frame, STRINGS["preview_title"], 18, GREEN, "bold", anchor="w"
        ).place(x=CONTENT_X, y=MARGIN)

        self._settings_button_p = ui_kit.RoundButton(
            frame,
            "Настройки",
            command=self._open_settings,
            kind="primary",
            width=125,
            height=34,
            icon="icon_settings",
            font_size=10,
            bg=BG,
        )
        self._settings_button_p.place(x=744, y=MARGIN)

        chips = tk.Frame(frame, bg=BG)
        chips.place(x=CONTENT_X, y=62, width=CONTENT_W, height=32)
        self.chip_var = tk.StringVar(value="all")
        self.chips: dict[str, ui_kit.RadioDot] = {}
        x = 0
        for key, label in (
            ("all", "все"),
            (core.RENAME, "к переименованию"),
            (core.UNCHANGED, "без изменений"),
            (core.BAD_DATE, "дата недостоверна"),
            (core.ERROR, "ошибки"),
        ):
            width = {
                "all": 92,
                core.RENAME: 200,
                core.UNCHANGED: 196,
                core.BAD_DATE: 198,
                core.ERROR: 112,
            }[key]
            dot = ui_kit.RadioDot(
                chips,
                f"{label} · 0",
                self.chip_var,
                key,
                bg=BG,
                font_size=10,
                command=self._fill_table,
            )
            dot.configure(width=width, height=26)
            dot.place(x=x, y=0)
            self.chips[key] = dot
            x += width

        table = tk.Frame(frame, bg=BG, highlightbackground=LINE, highlightthickness=1)
        table.place(x=CONTENT_X, y=100, width=CONTENT_W, height=370)

        names = tuple(name for name, _ in COLUMNS)
        titles = {
            "old": STRINGS["col_old"],
            "new": STRINGS["col_new"],
            "folder": STRINGS["col_folder"],
            "source": STRINGS["col_source"],
            "status": STRINGS["col_status"],
        }
        self.tree = ttk.Treeview(
            table, columns=names, show="headings", selectmode="browse", height=10
        )
        for name, width in COLUMNS:
            self.tree.heading(name, text=titles[name], anchor="w")
            self.tree.column(name, width=width, minwidth=60, anchor="w", stretch=True)

        hscroll = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(xscrollcommand=hscroll.set)
        self.tree.pack(fill="both", expand=True, padx=1, pady=(1, 0))
        hscroll.pack(fill="x", pady=(0, 1))

        self.tree.tag_configure(core.ERROR, foreground=DANGER)
        self.tree.tag_configure(core.BAD_DATE, foreground="#8A6D3B")
        self.tree.tag_configure(core.UNCHANGED, foreground=MUTED)
        self.tree.bind("<<TreeviewSelect>>", self._update_source_hint)

        # Слева — сколько файлов к переименованию, справа под ним — описание даты.
        self.summary_label = self._label(frame, "", 11, INK, "normal", anchor="w")
        self.summary_label.place(x=CONTENT_X, y=484, width=380)

        self.source_hint = self._label(
            frame,
            "",
            9,
            MUTED,
            "normal",
            anchor="e",
            justify="right",
            wraplength=420,
        )
        self.source_hint.place(x=440, y=486, width=430, height=44)

        self.source_hint.config(text="Выберите строку, чтобы увидеть источник даты.")

        self._back_button = ui_kit.RoundButton(
            frame,
            STRINGS["btn_back"],
            command=self._back_home,
            kind="secondary",
            width=207,
            height=52,
            bg=BG,
        )
        self._back_button.place(x=390, y=544)

        self.run_button = ui_kit.RoundButton(
            frame,
            STRINGS["btn_go"],
            command=self._run_operation,
            kind="primary",
            width=255,
            height=52,
            font_size=13,
            bg=BG,
        )
        self.run_button.place(x=621, y=544)

    def _back_home(self) -> None:
        self.show("home")

    def _current_items(self) -> list[core.PlanItem]:
        if not self.plan_result:
            return []
        key = self.chip_var.get()
        items = self.plan_result.items
        if key == "all":
            return items
        return [i for i in items if i.status == key]

    def _fill_table(self, reset_scroll: bool = True) -> None:
        if reset_scroll:
            self.tree.delete(*self.tree.get_children())
        items = self._current_items()

        for item in items:
            self.tree.insert(
                "",
                "end",
                values=(
                    item.name,
                    item.dst_name or "—",
                    item.year_folder or "—",
                    item.source,
                    core.STATUS_LABELS.get(item.status, item.status)
                    + (f" · {item.note}" if item.note else ""),
                ),
                tags=(item.status,),
            )
        self._update_source_hint()

    def _update_source_hint(self, _event=None) -> None:  # type: ignore[no-untyped-def]
        if not hasattr(self, "source_hint"):
            return
        selection = self.tree.selection()
        if not selection:
            self.source_hint.config(
                text="Выберите строку, чтобы увидеть источник даты."
            )
            return
        index = self.tree.index(selection[0])
        items = self._current_items()
        if index >= len(items):
            return
        item = items[index]
        explanation = EXPLANATIONS.get(item.source, "Источник даты не указан.")
        self.source_hint.config(text=explanation)

    def _show_plan(self, result: core.PlanResult) -> None:
        self.plan_result = result
        counts = result.counts()
        total = len(result.items)

        for key, dot in self.chips.items():
            label = {
                "all": f"все · {total}",
                core.RENAME: f"к переименованию · {counts.get(core.RENAME, 0)}",
                core.UNCHANGED: f"без изменений · {counts.get(core.UNCHANGED, 0)}",
                core.BAD_DATE: f"дата недостоверна · {counts.get(core.BAD_DATE, 0)}",
                core.ERROR: f"ошибки · {counts.get(core.ERROR, 0)}",
            }[key]
            dot._label = label
            dot._draw()

        rename_count = counts.get(core.RENAME, 0)
        parts = [
            f"{rename_count} {plural(rename_count, 'файл', 'файла', 'файлов')} "
            f"к переименованию"
        ]
        self.summary_label.config(
            text=" · ".join(parts) if total else STRINGS["nothing"]
        )

        self.chip_var.set("all")
        self.show("preview")
        self._fill_table()
        self.run_button.set_enabled(bool(rename_count))

    # --- прогресс ------------------------------------------------------------

    def _build_progress(self) -> None:
        frame = self._screen("progress")

        self.progress_label = self._label(
            frame, STRINGS["working"], 16, GREEN, "bold", anchor="w"
        )
        self.progress_label.place(x=160, y=250, width=580)

        self.progress_bar = ttk.Progressbar(
            frame,
            mode="determinate",
            maximum=100,
            style="Green.Horizontal.TProgressbar",
        )
        self.progress_bar.place(x=160, y=292, width=580, height=10)

        self.progress_detail = self._label(frame, "", 10, MUTED, "normal", anchor="w")
        self.progress_detail.place(x=160, y=312, width=580)

        self._cancel_button = ui_kit.RoundButton(
            frame,
            STRINGS["btn_cancel"],
            command=self._cancel_current,
            kind="secondary",
            width=180,
            height=46,
            bg=BG,
        )
        self._cancel_button.place(x=560, y=356)

    def _cancel_current(self) -> None:
        self._cancel.set()
        self.progress_label.config(text="Останавливаюсь после текущего файла…")

    def _run_operation(self) -> None:
        if not self.plan_result or not self.folder:
            return
        problem = core.check_folder(self.folder)
        if problem:
            messagebox.showwarning(APP_TITLE, problem)
            return
        self._cancel = threading.Event()
        self._progress = {"done": 0, "total": len(self.plan_result.actionable())}
        self.progress_label.config(text="Раскладываю файлы…")
        self.progress_bar.config(mode="determinate", maximum=100, value=0)
        self.progress_detail.config(text="")
        self.show("progress")
        threading.Thread(
            target=self._run_worker,
            args=(self.folder, self.plan_result, dict(self.cfg)),
            daemon=True,
        ).start()

    def _run_worker(self, folder: Path, result: core.PlanResult, cfg: dict) -> None:
        try:
            stats = core.execute(
                folder,
                result,
                cfg,
                cfgmod.journal_dir(),
                progress=lambda done, total, _item: self._on_progress(done, total),
                cancel=self._cancel,
            )
            self._queue.put(("done", stats))
        except Exception as exc:  # noqa: BLE001
            self._queue.put(("fatal", core.human_error(exc)))

    # --- результат -----------------------------------------------------------

    def _build_result(self) -> None:
        frame = self._screen("result")

        photo = load_photo(cfgmod.assets_dir() / "mascot_work.png", (420, 580))
        if photo is not None:
            holder = tk.Label(frame, image=photo, bg=BG, bd=0)
            holder.image = photo
            holder.place(x=0, y=40, width=420, height=580)

        self.result_title = self._label(
            frame, "Магия сработала!", 26, GREEN, "bold", anchor="center"
        )
        self.result_title.place(x=450, y=150, width=420)

        self.result_summary = self._label(
            frame,
            "",
            11,
            INK,
            "normal",
            anchor="center",
            justify="center",
            wraplength=420,
        )
        self.result_summary.place(x=450, y=205, width=420)

        # Обе кнопки одной ширины.
        btn_w = 200
        self.thanks_button = ui_kit.RoundButton(
            frame,
            STRINGS["thanks"],
            command=self._on_thanks,
            kind="secondary",
            width=btn_w,
            height=53,
            font_size=11,
            bg=BG,
        )
        self.thanks_button.place(x=450, y=400)

        self.journal_button = ui_kit.RoundButton(
            frame,
            STRINGS["open_folder"],
            command=self._open_result_folder,
            kind="primary",
            width=btn_w,
            height=53,
            icon="icon_folder",
            font_size=11,
            bg=BG,
        )
        self.journal_button.place(x=670, y=400)

        # «Отменить изменения» — призрачная кнопка с подсветкой, выровнена
        # по правому краю с «Открыть папку».
        self.undo_button = ui_kit.RoundButton(
            frame,
            STRINGS["undo"],
            command=self._on_undo,
            kind="ghost",
            width=btn_w,
            height=36,
            font_size=10,
            bg=BG,
        )
        self.retry_button = ui_kit.RoundButton(
            frame,
            STRINGS["retry"],
            command=self._on_retry,
            kind="ghost",
            width=btn_w,
            height=36,
            font_size=10,
            bg=BG,
        )

    def _open_result_folder(self) -> None:
        if not self.folder:
            return
        try:
            if os.name == "nt":
                os.startfile(str(self.folder))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self.folder)])
            else:
                subprocess.Popen(["xdg-open", str(self.folder)])
        except Exception as exc:  # noqa: BLE001
            messagebox.showinfo(APP_TITLE, core.human_error(exc))

    def _show_result(self, stats: core.RunStats) -> None:
        self.run_stats = stats
        self.last_failed = list(stats.failed)
        counts = self.plan_result.counts() if self.plan_result else {}
        improved = stats.renamed

        if stats.cancelled:
            self.result_title.config(text="Остановлено")
            lead = "Вы остановили работу.\nСделанное можно отменить."
        elif improved:
            self.result_title.config(text="Магия сработала!")
            lead = (
                "Хаос побеждён! Мудрый Архивариус разложил всё\n"
                "по своим временным полкам, и ни один файл\n"
                "не потерялся в измерениях."
            )
        else:
            self.result_title.config(text="Менять нечего")
            lead = "Все файлы уже лежат на своих местах."

        files_word = plural(improved, "файл", "файла", "файлов")
        numbers = (
            f"Разложено: {improved} {files_word} · "
            f"без изменений: {counts.get(core.UNCHANGED, 0)} · "
            f"ошибок: {stats.errors}\n"
            f"Заняло: {fmt_duration(stats.elapsed)}"
        )
        self.result_summary.config(text=f"{lead}\n\n{numbers}")

        self.retry_button.place_forget()
        self.undo_button.place_forget()
        if self.last_failed:
            self.retry_button.place(x=670, y=500)
        if improved and stats.journal_path:
            self.undo_button.place(x=670, y=466)

        self.show("result")
        self._play_animation()

    def _play_animation(self) -> None:
        self._stop_animation()
        folder = cfgmod.assets_dir() / "anim_done"
        if reduce_motion() or not folder.is_dir():
            return
        files = sorted(folder.glob("*.png"))[:120]
        if not files:
            return
        frames = [load_photo(path, (420, 580)) for path in files]
        frames = [f for f in frames if f is not None]
        if not frames:
            return
        self._anim_frames = frames
        delay = max(int(1000 / 24), 20)
        state = {"index": 0}

        def step() -> None:
            if self.current != "result" or not self._anim_frames:
                return
            state["index"] = (state["index"] + 1) % len(self._anim_frames)
            if state["index"] == 0:
                self._anim_after = None
                return
            self._anim_after = self.after(delay, step)

        self._anim_after = self.after(delay, step)

    def _stop_animation(self) -> None:
        for attr in ("_anim_after", "_thanks_after"):
            value = getattr(self, attr, None)
            if value is not None:
                try:
                    self.after_cancel(value)
                except tk.TclError:
                    pass
                setattr(self, attr, None)

    def _on_thanks(self) -> None:
        self._back_home()

    def _on_retry(self) -> None:
        if not self.last_failed:
            return
        stats = core.retry_failed(self.last_failed, self.cfg, cfgmod.journal_dir())
        if stats.errors == 0:
            messagebox.showinfo(APP_TITLE, f"Получилось: {stats.renamed}.")
        else:
            messagebox.showwarning(
                APP_TITLE, f"Всё ещё занято или недоступно: {stats.errors}."
            )
        if self.run_stats is not None:
            self.run_stats.renamed += stats.renamed
            self.run_stats.errors = stats.errors
        self.last_failed = list(stats.failed)
        self.retry_button.place_forget()
        if self.last_failed:
            self.retry_button.place(x=670, y=500)
        if self.run_stats is not None:
            good = self.run_stats.renamed
            self.result_summary.config(
                text=f"Разложено: {good} {plural(good, 'файл', 'файла', 'файлов')} · "
                f"ошибок: {self.run_stats.errors}"
            )

    def _on_undo(self) -> None:
        if not self.run_stats or not self.run_stats.journal_path:
            return
        if not messagebox.askyesno(APP_TITLE, "Вернуть файлы на прежние места?"):
            return
        try:
            stats = core.rollback(self.run_stats.journal_path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                APP_TITLE,
                f"Не удалось отменить изменения: {core.human_error(exc)}",
            )
            return
        text = f"Возвращено файлов: {stats.restored}"
        if stats.skipped:
            text += f"\nПропущено: {stats.skipped}"
        if stats.errors:
            text += f"\nОшибок: {stats.errors}"
        messagebox.showinfo(APP_TITLE, text)
        self._back_home()

    # --- журнал --------------------------------------------------------------

    def _build_journal(self) -> None:
        frame = self._screen("journal")

        self._label(frame, "Журнал операций", 18, GREEN, "bold", anchor="w").place(
            x=CONTENT_X, y=MARGIN
        )

        self._back_button_j = ui_kit.RoundButton(
            frame,
            "На главную",
            command=self._back_home,
            kind="secondary",
            width=160,
            height=38,
            icon="icon_back",
            font_size=10,
            bg=BG,
        )
        self._back_button_j.place(x=709, y=MARGIN)

        table = tk.Frame(frame, bg=BG, highlightbackground=LINE, highlightthickness=1)
        table.place(x=CONTENT_X, y=72, width=CONTENT_W, height=420)

        self.journal_tree = ttk.Treeview(
            table, columns=("file", "moved", "errors"), show="headings", height=12
        )
        for column, title, width in (
            ("file", "Сессия", 420),
            ("moved", "Операций", 150),
            ("errors", "Ошибок", 150),
        ):
            self.journal_tree.heading(column, text=title, anchor="w")
            self.journal_tree.column(column, width=width, anchor="w", stretch=True)
        style = ttk.Style(self)
        style.configure(
            "Treeview.Heading",
            font=ui_kit.font(10, "medium"),
            background=GREEN_SOFT,
            foreground=GREEN,
            relief="flat",
            padding=(8, 8),
            borderwidth=0,
        )
        self.journal_tree.pack(fill="both", expand=True, padx=1, pady=1)

        self._undo_button_j = ui_kit.RoundButton(
            frame,
            "Отменить выбранную сессию",
            command=self._undo_selected,
            kind="secondary",
            width=290,
            height=48,
            icon="icon_undo",
            font_size=11,
            bg=BG,
        )
        self._undo_button_j.place(x=CONTENT_X, y=528)

        self._open_journal_btn = ui_kit.RoundButton(
            frame,
            "Папка журнала",
            command=self._open_journal_folder,
            kind="ghost",
            width=170,
            height=48,
            icon="icon_journal",
            font_size=10,
            bg=BG,
        )
        self._open_journal_btn.place(x=699, y=528)

        self._refresh_journals()

    def _refresh_journals(self) -> None:
        self.journal_tree.delete(*self.journal_tree.get_children())
        for path in core.list_journals(cfgmod.journal_dir()):
            summary = core.journal_summary(path)
            self.journal_tree.insert(
                "",
                "end",
                iid=str(path),
                values=(
                    path.stem.replace("_", " "),
                    summary["moved"],
                    summary["errors"],
                ),
            )

    def _undo_selected(self) -> None:
        selection = self.journal_tree.selection()
        if not selection:
            messagebox.showinfo(APP_TITLE, "Выберите сессию в списке.")
            return
        path = Path(selection[0])
        if not messagebox.askyesno(
            APP_TITLE,
            f"Вернуть файлы сессии «{path.stem}» на прежние места?",
        ):
            return
        stats = core.rollback(path)
        messagebox.showinfo(
            APP_TITLE,
            f"Возвращено: {stats.restored}\nПропущено: {stats.skipped}\n"
            f"Ошибок: {stats.errors}",
        )
        self._refresh_journals()

    def _open_journal_folder(self) -> None:
        folder = cfgmod.journal_dir()
        try:
            if os.name == "nt":
                os.startfile(folder)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", str(folder)], check=False)
            else:
                subprocess.run(["xdg-open", str(folder)], check=False)
        except Exception:  # noqa: BLE001
            messagebox.showinfo(APP_TITLE, f"Папка журнала: {folder}")

    # --- настройки -----------------------------------------------------------

    def _build_settings(self) -> None:
        """Настройки. Расстояние «заголовок → поле» везде одинаковое."""
        frame = self._screen("settings")

        # Заголовок поднят к краю: освободившееся место нужно четвёртому чекбоксу.
        self._label(frame, "Настройки", 18, GREEN, "bold", anchor="w").place(
            x=CONTENT_X, y=SETTINGS_TOP
        )

        # Маскот уменьшен и поднят, чтобы кнопки не обрезали картинку.
        photo = load_photo(cfgmod.assets_dir() / "mascot_settings.png", (300, 420))
        if photo is not None:
            holder = tk.Label(frame, image=photo, bg=BG, bd=0)
            holder.image = photo
            holder.place(x=560, y=70, width=300, height=420)

        hint_font = ui_kit.font(8)
        field_w = 403
        title_h = 20
        field_h = 40
        y = SETTINGS_TOP + SETTINGS_TITLE_OFFSET

        # --- шаблон названия файла ---
        self._label(frame, STRINGS["template"], 11, GREEN, "normal", anchor="w").place(
            x=CONTENT_X, y=y, height=title_h
        )
        y += SETTINGS_TITLE_GAP
        self.s_template = tk.StringVar(value=self.cfg.get("template", ""))
        self.template_field = ui_kit.Field(
            frame, self.s_template, width=field_w, height=field_h, font_size=11, bg=BG
        )
        self.template_field.place(x=30, y=y)
        self.s_template.trace_add("write", lambda *_: self._validate_settings())
        y += field_h + 8

        tk.Label(
            frame,
            text="{YYYY} — год, {YY} — две цифры года, {MM} — месяц, {DD} — день,\n"
            "{HH} — часы, {mm} — минуты, {ss} — секунды.\n"
            "Пример: {YYYY}.{MM}.{DD}_{HH}-{mm}-{ss} → 2012.11.16_16-09-52",
            bg=BG,
            fg=MUTED,
            font=hint_font,
            justify="left",
            anchor="nw",
        ).place(x=CONTENT_X, y=y, width=500, height=46)
        y += 46 + 14

        # --- шаблон названия папки ---
        self._label(
            frame, STRINGS["year_folder"], 11, GREEN, "normal", anchor="w"
        ).place(x=CONTENT_X, y=y, height=title_h)
        y += SETTINGS_TITLE_GAP
        self.s_year = tk.StringVar(value=self.cfg.get("year_folder", "{YYYY}"))
        self.year_field = ui_kit.Field(
            frame, self.s_year, width=field_w, height=field_h, font_size=11, bg=BG
        )
        self.year_field.place(x=30, y=y)
        self.s_year.trace_add("write", lambda *_: self._validate_settings())
        y += field_h + 8

        tk.Label(
            frame,
            text="Те же переменные, что и у файла.\n"
            "«Архив {YYYY}» → папка «Архив 2012».",
            bg=BG,
            fg=MUTED,
            font=hint_font,
            justify="left",
            anchor="nw",
        ).place(x=CONTENT_X, y=y, width=500, height=32)
        y += 32 + 14

        # --- режим работы ---
        self._label(frame, STRINGS["mode"], 11, GREEN, "normal", anchor="w").place(
            x=CONTENT_X, y=y, height=title_h
        )
        y += title_h + 8
        self.s_mode = tk.StringVar(value=self.cfg.get("mode", "inplace"))
        for index, (value, label) in enumerate(MODES):
            dot = ui_kit.RadioDot(frame, label, self.s_mode, value, bg=BG, font_size=11)
            dot.place(x=CONTENT_X, y=y + index * 28)
        y += len(MODES) * 28 + 10

        # --- дополнительно ---
        self._label(frame, "Дополнительно", 11, GREEN, "normal", anchor="w").place(
            x=CONTENT_X, y=y, height=title_h
        )
        y += title_h + 8

        self.s_recursive = tk.BooleanVar(value=bool(self.cfg.get("recursive")))
        ui_kit.CheckSquare(
            frame, STRINGS["recursive"], self.s_recursive, bg=BG, font_size=11
        ).place(x=CONTENT_X, y=y)
        y += 26

        self.s_fname = tk.BooleanVar(value=bool(self.cfg.get("filename_date_fallback")))
        ui_kit.CheckSquare(
            frame, STRINGS["filename_date"], self.s_fname, bg=BG, font_size=11
        ).place(x=CONTENT_X, y=y)
        y += 26

        self.s_skip = tk.BooleanVar(value=bool(self.cfg.get("skip_system_files")))
        ui_kit.CheckSquare(
            frame, STRINGS["skip_system"], self.s_skip, bg=BG, font_size=11
        ).place(x=CONTENT_X, y=y)
        y += 26

        self.s_keep_names = tk.BooleanVar(value=bool(self.cfg.get("keep_names")))
        ui_kit.CheckSquare(
            frame, STRINGS["keep_names"], self.s_keep_names, bg=BG, font_size=11
        ).place(x=CONTENT_X, y=y)

        # --- кнопки у нижнего края ---
        btn_y = 544
        self._reset_btn = ui_kit.RoundButton(
            frame,
            STRINGS["btn_reset"],
            command=self._reset_settings,
            kind="secondary",
            width=235,
            height=52,
            font_size=12,
            bg=BG,
        )
        self._reset_btn.place(x=CONTENT_X, y=btn_y)

        self._settings_back_btn = ui_kit.RoundButton(
            frame,
            STRINGS["btn_back"],
            command=self._settings_back,
            kind="secondary",
            width=207,
            height=52,
            font_size=12,
            bg=BG,
        )
        self._settings_back_btn.place(x=390, y=btn_y)

        self.s_save = ui_kit.RoundButton(
            frame,
            STRINGS["btn_save"],
            command=self._save_settings,
            kind="primary",
            width=255,
            height=52,
            icon="icon_check",
            font_size=12,
            bg=BG,
        )
        self.s_save.place(x=621, y=btn_y)

        self._validate_settings()

    def _validate_settings(self) -> None:
        # Без переименования шаблон имени не используется — не требуем его.
        keep = bool(self.s_keep_names.get()) if hasattr(self, "s_keep_names") else False
        template_error = None if keep else core.validate_template(self.s_template.get())
        year_error = core.validate_year_folder(self.s_year.get())

        # Ошибка — красная рамка, иначе зелёная при фокусе.
        self.template_field.set_focus_ring(
            bool(template_error), DANGER if template_error else GREEN
        )
        self.year_field.set_focus_ring(
            bool(year_error), DANGER if year_error else GREEN
        )
        self.s_save.set_enabled(not (template_error or year_error))

    def _save_settings(self) -> None:
        self.cfg.update(
            template=self.s_template.get(),
            year_folder=self.s_year.get(),
            mode=self.s_mode.get(),
            recursive=bool(self.s_recursive.get()),
            filename_date_fallback=bool(self.s_fname.get()),
            skip_system_files=bool(self.s_skip.get()),
            keep_names=bool(self.s_keep_names.get()),
        )
        cfgmod.save_settings(self.cfg)
        self.template_field.canvas.delete("ring")
        self.year_field.canvas.delete("ring")
        self._play_saved_feedback()

    def _play_saved_feedback(self) -> None:
        button = self.s_save
        button.set_enabled(False)
        button.set_text("Сохранено")
        self.after(700, lambda: button.set_text(STRINGS["btn_save"]))
        self.after(900, self._finish_saved_feedback)

    def _finish_saved_feedback(self) -> None:
        self.s_save.set_text(STRINGS["btn_save"])
        self.s_save.set_enabled(True)
        self._settings_back()

    def _reset_settings(self) -> None:
        defaults = cfgmod.DEFAULTS
        self.s_template.set(defaults["template"])
        self.s_year.set(defaults["year_folder"])
        self.s_mode.set(defaults["mode"])
        self.s_recursive.set(bool(defaults["recursive"]))
        self.s_fname.set(bool(defaults["filename_date_fallback"]))
        self.s_skip.set(bool(defaults["skip_system_files"]))
        self._validate_settings()

    # --- очередь событий -----------------------------------------------------

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "plan":
                    self._show_plan(payload)
                elif kind == "done":
                    self._show_result(payload)
                elif kind == "fatal":
                    self.progress_label.config(text="Что-то пошло не так")
                    messagebox.showerror(APP_TITLE, f"Не получилось: {payload}")
                    self._back_home()
        except queue.Empty:
            pass

        if self.current == "progress":
            done = self._progress.get("done", 0)
            total = self._progress.get("total", 0)
            if total > 0:
                self.progress_bar.stop()
                self.progress_bar.config(mode="determinate", maximum=total, value=done)
                self.progress_label.config(text=f"Файл {done} из {total}")
                percent = int(done * 100 / total)
                self.progress_detail.config(text=f"{percent}%")
            else:
                self.progress_detail.config(text="")

        self.after(80, self._poll)

    def _on_escape(self) -> None:
        if self.current == "progress":
            self._cancel_current()
        elif self.current == "settings":
            self._settings_back()
        elif self.current in ("preview", "journal", "result"):
            self._back_home()

    def _on_enter(self) -> None:
        if self.current == "preview":
            if self.run_button.is_enabled():
                self._run_operation()
        elif self.current == "result":
            self._on_thanks()

    def _on_close(self) -> None:
        try:
            cfgmod.save_settings(self.cfg)
        except Exception:  # noqa: BLE001
            pass
        self.destroy()


def run() -> None:
    cfgmod.setup_logging()
    fonts.load()
    app = App()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        app.destroy()
