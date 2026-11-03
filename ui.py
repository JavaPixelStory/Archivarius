"""Интерфейс «Архивариуса» на tkinter/ttk. Вся логика — в core.py."""
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
import settings as cfgmod
import version

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DND_AVAILABLE = True
except Exception:  # noqa: BLE001 - приложение обязано работать и без библиотеки
    DND_FILES = None
    TkinterDnD = None
    DND_AVAILABLE = False

APP_TITLE = "Архивариус"
LOG = logging.getLogger(cfgmod.APP_NAME)

BG = "#FAF7F2"
CARD = "#FFFFFF"
INK = "#2B2B2B"
MUTED = "#7A7266"
ACCENT = "#C9A227"
ACCENT_DARK = "#A8861A"
BLUE = "#1F3A5F"
GOLD_SOFT = "#F3E9C8"

STRINGS = {
    "tagline": "Раскладывает файлы по годам",
    "drop": "Перетащите сюда папку",
    "or": "или",
    "choose": "Выбрать папку",
    "recent": "Недавние папки",
    "advanced": "Дополнительно",
    "template": "Шаблон имени",
    "year_folder": "Папка года",
    "mode": "Режим",
    "mode_inplace": "Переименовать и разложить",
    "mode_dry": "Только отчёт, ничего не менять",
    "recursive": "Включая вложенные папки",
    "filename_date": "Брать дату из имени файла, если других нет",
    "preview_title": "Предпросмотр",
    "col_old": "Имя сейчас",
    "col_new": "Имя после",
    "col_folder": "Папка года",
    "col_source": "Источник даты",
    "col_status": "Статус",
    "btn_go": "Разложить",
    "btn_back": "Назад",
    "btn_cancel": "Отмена",
    "working": "Работаю…",
    "result_title": "Готово",
    "thanks": "Спасибо",
    "undo": "Отменить изменения",
    "open_journal": "Открыть журнал",
    "again": "Разложить другую папку",
    "retry": "Повторить пропущенные",
    "nothing": "Здесь пока нечего раскладывать",
    "nothing_hint": "В выбранной папке нет файлов, которые нужно переименовать.",
}

MODES = [("inplace", STRINGS["mode_inplace"]), ("dry", STRINGS["mode_dry"])]


def reduce_motion() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        enabled = ctypes.c_int(0)
        ok = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
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
    if seconds < 60:
        return f"{seconds:.1f} с"
    minutes = int(seconds // 60)
    return f"{minutes} мин {int(seconds % 60)} с"


# --- маскот ------------------------------------------------------------------


class Mascot(tk.Frame):
    """Картинка маскота или аккуратный векторный плейсхолдер, если ассетов нет."""

    def __init__(self, master, width: int = 260, height: int = 260, **kwargs):
        super().__init__(master, bg=kwargs.pop("bg", BG), width=width, height=height)
        self.pack_propagate(False)
        self.width = width
        self.height = height
        self._image: tk.PhotoImage | None = None
        self.canvas = tk.Canvas(self, width=width, height=height, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self._frames_anchor: tk.Label | None = None
        self._draw_placeholder()

    def _draw_placeholder(self) -> None:
        c = self.canvas
        c.delete("all")
        w, h = self.width, self.height
        cx, cy = w // 2, h // 2
        r = int(min(w, h) * 0.30)

        c.create_oval(cx - r - 26, cy + r - 8, cx + r + 26, cy + r + 74, fill=BLUE, outline="")
        c.create_polygon(
            cx - r + 4, cy + r - 4, cx + r - 4, cy + r - 4, cx + r + 30, cy + r + 78,
            cx - r - 30, cy + r + 78, fill=BLUE, outline="", smooth=True,
        )
        c.create_line(cx, cy + r + 10, cx, cy + r + 66, fill=GOLD_SOFT, width=3)
        c.create_oval(cx - 20, cy + r + 4, cx + 20, cy + r + 34, fill=GOLD_SOFT, outline="")

        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#F2D6C0", outline="#D9B79C", width=2)
        c.create_arc(
            cx - r, cy - r - 6, cx + r, cy + int(r * 0.35),
            start=0, extent=180, fill="#EDEDED", outline="#D6D6D6", width=2,
        )
        c.create_oval(cx - r + 12, cy - r + 6, cx - r + 40, cy - r + 34, fill="#EDEDED", outline="")
        c.create_oval(cx + r - 40, cy - r + 6, cx + r - 12, cy - r + 34, fill="#EDEDED", outline="")

        gr = int(r * 0.40)
        gy = cy - int(r * 0.12)
        for dx in (-int(r * 0.46), int(r * 0.46)):
            c.create_oval(cx + dx - gr, gy - gr, cx + dx + gr, gy + gr, outline=INK, width=3, fill="#FFFFFF")
            c.create_oval(cx + dx - 4, gy - 4, cx + dx + 4, gy + 4, fill=INK, outline="")
        c.create_line(cx - 8, gy, cx + 8, gy, fill=INK, width=3)

        c.create_polygon(
            cx - int(r * 0.7), cy + int(r * 0.30), cx + int(r * 0.7), cy + int(r * 0.30),
            cx, cy + int(r * 1.05), fill="#F7F7F7", outline="#DCDCDC", smooth=True,
        )
        c.create_arc(
            cx - int(r * 0.32), cy + int(r * 0.18), cx + int(r * 0.32), cy + int(r * 0.56),
            start=200, extent=140, style="arc", outline="#B08A6E", width=3,
        )

        for index, offset in enumerate((0, 14, 28)):
            c.create_rectangle(
                cx - 96 + offset, cy + int(r * 1.0) + offset,
                cx - 20 + offset, cy + int(r * 1.0) + 22 + offset,
                fill="#E8D4A8" if index % 2 else "#DCC48E", outline="#B99C63",
            )

    def show_image(self, photo: tk.PhotoImage | None) -> None:
        if photo is None:
            self._draw_placeholder()
            return
        self.canvas.delete("all")
        self._image = photo
        self.canvas.create_image(self.width // 2, self.height // 2, image=photo)


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


# --- приложение --------------------------------------------------------------

BaseClass = TkinterDnD.Tk if DND_AVAILABLE else tk.Tk


STRINGS_SAVE_LABEL = "Сохранить"


class App(BaseClass):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = cfgmod.load_settings()
        self.title(f"{APP_TITLE} {version.APP_VERSION}")
        self.geometry(self.cfg.get("geometry") or "900x620")
        self.minsize(720, 520)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

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

        self._build_styles()
        self._build_header()
        self.container = tk.Frame(self, bg=BG)
        self.container.pack(fill="both", expand=True, padx=28, pady=(0, 22))

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

    # --- общее ---------------------------------------------------------------

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        try:
            import sv_ttk

            sv_ttk.set_theme("light")
            style = ttk.Style(self)
        except Exception:  # noqa: BLE001 - без sv-ttk приложение работает на clam
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD, relief="flat")
        style.configure("TLabel", background=BG, foreground=INK, font=("Segoe UI", 10))
        style.configure("Card.TLabel", background=CARD, foreground=INK, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Title.TLabel", background=BG, foreground=BLUE, font=("Segoe UI Semibold", 20))
        style.configure("Head.TLabel", background=BG, foreground=BLUE, font=("Segoe UI Semibold", 14))
        style.configure("Drop.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 12))
        style.configure("Big.TButton", font=("Segoe UI Semibold", 12), padding=(22, 12))
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 13), padding=(34, 14))
        style.configure("Link.TButton", font=("Segoe UI", 10), padding=(6, 4))
        style.configure("Treeview", font=("Segoe UI", 10), rowheight=26, background=CARD, fieldbackground=CARD)
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 9))
        style.configure("TCheckbutton", background=CARD, foreground=INK, font=("Segoe UI", 10))
        style.configure("Card.TRadiobutton", background=CARD, foreground=INK, font=("Segoe UI", 10))
        style.configure("TEntry", padding=4)
        style.map("Accent.TButton", background=[("active", ACCENT_DARK), ("!disabled", ACCENT)])

    def _build_header(self) -> None:
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=28, pady=(18, 10))
        tk.Label(header, text=APP_TITLE, bg=BG, fg=BLUE, font=("Segoe UI Semibold", 22)).pack(side="left")
        tk.Label(
            header, text=STRINGS["tagline"], bg=BG, fg=MUTED, font=("Segoe UI", 10)
        ).pack(side="left", padx=(12, 0), pady=(8, 0))
        ttk.Button(header, text="Настройки", style="Link.TButton", command=lambda: self.show("settings")).pack(side="right")

    def show(self, name: str) -> None:
        self._stop_animation()
        for frame in self.screens.values():
            frame.pack_forget()
        self.screens[name].pack(fill="both", expand=True)
        self.current = name

    def _card(self, master) -> tk.Frame:
        return tk.Frame(master, bg=CARD, highlightbackground="#E6DED2", highlightthickness=1)

    # --- главный экран -------------------------------------------------------

    def _build_home(self) -> None:
        frame = tk.Frame(self.container, bg=BG)
        self.screens["home"] = frame

        card = self._card(frame)
        card.pack(fill="x", pady=(0, 14))

        self.drop_zone = tk.Frame(card, bg=CARD, height=130)
        self.drop_zone.pack(fill="x", padx=18, pady=(14, 12))
        self.drop_zone.pack_propagate(False)
        self.drop_hint = tk.Label(
            self.drop_zone, text=STRINGS["drop"], bg=CARD, fg=MUTED, font=("Segoe UI", 14)
        )
        self.drop_hint.pack(expand=True)
        tk.Label(
            self.drop_zone, text=STRINGS["or"], bg=CARD, fg="#B9B0A2", font=("Segoe UI", 9)
        ).pack()
        ttk.Button(self.drop_zone, text=STRINGS["choose"], style="Big.TButton", command=self._choose_folder).pack(
            pady=(8, 0)
        )
        self.drop_zone.bind("<Button-1>", lambda _e: self._choose_folder())

        self.folder_label = tk.Label(
            card, text="", bg=CARD, fg=BLUE, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=780
        )
        self.folder_label.pack(fill="x", padx=20, pady=(0, 14))

        recent_card = self._card(frame)
        recent_card.pack(fill="x", pady=(0, 14))
        row = tk.Frame(recent_card, bg=CARD)
        row.pack(fill="x", padx=18, pady=14)
        tk.Label(row, text=STRINGS["recent"], bg=CARD, fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
        self.recent_var = tk.StringVar()
        self.recent_box = ttk.Combobox(row, textvariable=self.recent_var, state="readonly", width=62)
        self.recent_box.pack(side="left", padx=12)
        self.recent_box.bind("<<ComboboxSelected>>", self._on_recent)
        self._refresh_recent()

        adv_card = self._card(frame)
        adv_card.pack(fill="x")
        self.adv_open = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            adv_card, text=STRINGS["advanced"], variable=self.adv_open,
            command=self._toggle_advanced, style="TCheckbutton",
        ).pack(anchor="w", padx=18, pady=(12, 0))
        self.adv_body = tk.Frame(adv_card, bg=CARD)
        self._build_advanced(self.adv_body)

        self.go_button = ttk.Button(frame, text=STRINGS["btn_go"], style="Accent.TButton", command=self._scan_from_home)
        self.go_button.pack(anchor="e", pady=(12, 0))

    def _build_advanced(self, body: tk.Frame) -> None:
        grid = body
        tk.Label(grid, text=STRINGS["template"], bg=CARD, fg=MUTED, font=("Segoe UI", 9)).grid(
            row=0, column=0, sticky="w", padx=18, pady=(10, 2)
        )
        self.template_var = tk.StringVar(value=self.cfg.get("template", ""))
        ttk.Entry(grid, textvariable=self.template_var, width=44).grid(row=1, column=0, sticky="w", padx=18)
        self.template_error = tk.Label(grid, text="", bg=CARD, fg="#B3261E", font=("Segoe UI", 8))
        self.template_error.grid(row=2, column=0, sticky="w", padx=18)
        self.template_var.trace_add("write", lambda *_: self._validate_templates())

        tk.Label(grid, text=STRINGS["year_folder"], bg=CARD, fg=MUTED, font=("Segoe UI", 9)).grid(
            row=0, column=1, sticky="w", padx=12, pady=(10, 2)
        )
        self.year_var = tk.StringVar(value=self.cfg.get("year_folder", "{YYYY}"))
        ttk.Entry(grid, textvariable=self.year_var, width=18).grid(row=1, column=1, sticky="w", padx=12)
        self.year_error = tk.Label(grid, text="", bg=CARD, fg="#B3261E", font=("Segoe UI", 8))
        self.year_error.grid(row=2, column=1, sticky="w", padx=12)
        self.year_var.trace_add("write", lambda *_: self._validate_templates())

        tk.Label(grid, text=STRINGS["mode"], bg=CARD, fg=MUTED, font=("Segoe UI", 9)).grid(
            row=3, column=0, sticky="w", padx=18, pady=(12, 2)
        )
        self.mode_var = tk.StringVar(value=self.cfg.get("mode", "inplace"))
        mode_row = tk.Frame(grid, bg=CARD)
        mode_row.grid(row=4, column=0, columnspan=2, sticky="w", padx=18)
        for value, label in MODES:
            ttk.Radiobutton(
                mode_row, text=label, value=value, variable=self.mode_var, style="Card.TRadiobutton"
            ).pack(side="left", padx=(0, 16))

        self.recursive_var = tk.BooleanVar(value=bool(self.cfg.get("recursive")))
        ttk.Checkbutton(
            grid, text=STRINGS["recursive"], variable=self.recursive_var, style="TCheckbutton"
        ).grid(row=5, column=0, sticky="w", padx=18, pady=(10, 0))

        self.fname_date_var = tk.BooleanVar(value=bool(self.cfg.get("filename_date_fallback")))
        ttk.Checkbutton(
            grid, text=STRINGS["filename_date"], variable=self.fname_date_var, style="TCheckbutton"
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=18, pady=(4, 14))

    def _toggle_advanced(self) -> None:
        if self.adv_open.get():
            self.adv_body.pack(fill="x")
        else:
            self.adv_body.pack_forget()

    def _validate_templates(self) -> None:
        error = core.validate_template(self.template_var.get())
        self.template_error.config(text=error or "")
        year_error = core.validate_year_folder(self.year_var.get())
        self.year_error.config(text=year_error or "")

    def _refresh_recent(self) -> None:
        folders = [p for p in self.cfg.get("last_folders", []) if os.path.isdir(p)]
        self.recent_box["values"] = folders
        if folders:
            self.recent_box.current(0)

    def _on_recent(self, _event=None) -> None:
        value = self.recent_var.get()
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
        messagebox.showinfo(APP_TITLE, "Нужна папка, а не файл. Перетащите папку целиком.")

    def _remember(self, folder: Path) -> None:
        folders = [str(folder)] + [p for p in self.cfg.get("last_folders", []) if str(p) != str(folder)]
        self.cfg["last_folders"] = folders[:5]
        cfgmod.save_settings(self.cfg)
        self._refresh_recent()

    # --- сканирование --------------------------------------------------------

    def _scan_from_home(self) -> None:
        self.cfg["template"] = self.template_var.get()
        self.cfg["year_folder"] = self.year_var.get()
        self.cfg["mode"] = self.mode_var.get()
        self.cfg["recursive"] = bool(self.recursive_var.get())
        self.cfg["filename_date_fallback"] = bool(self.fname_date_var.get())
        error = core.validate_template(self.cfg["template"]) or core.validate_year_folder(self.cfg["year_folder"])
        if error:
            messagebox.showwarning(APP_TITLE, error)
            return
        cfgmod.save_settings(self.cfg)
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
        self.folder_label.config(text=str(folder))
        self.cfg["template"] = self.template_var.get()
        self.cfg["year_folder"] = self.year_var.get()
        self.cfg["mode"] = self.mode_var.get()
        self.cfg["recursive"] = bool(self.recursive_var.get())
        self.cfg["filename_date_fallback"] = bool(self.fname_date_var.get())

        self._cancel = threading.Event()
        self._progress = {"done": 0, "total": 0}
        self.progress_label.config(text="Читаю папку…")
        self.progress_bar.config(mode="indeterminate")
        self.progress_bar.start(12)
        self.show("progress")
        threading.Thread(target=self._scan_worker, args=(folder, dict(self.cfg)), daemon=True).start()

    def _scan_worker(self, folder: Path, cfg: dict) -> None:
        try:
            result = core.plan(folder, cfg, self._cancel, self._on_progress)
            self._queue.put(("plan", result))
        except Exception as exc:  # noqa: BLE001
            self._queue.put(("fatal", core.human_error(exc)))

    def _on_progress(self, done: int, total: int) -> None:
        self._progress = {"done": done, "total": total}

    # --- предпросмотр --------------------------------------------------------

    def _build_preview(self) -> None:
        frame = tk.Frame(self.container, bg=BG)
        self.screens["preview"] = frame

        top = tk.Frame(frame, bg=BG)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text=STRINGS["preview_title"], style="Head.TLabel").pack(side="left")
        self.preview_path = tk.Label(top, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9))
        self.preview_path.pack(side="left", padx=12)

        chips = tk.Frame(frame, bg=BG)
        chips.pack(fill="x", pady=(0, 8))
        self.chip_var = tk.StringVar(value="all")
        self.chips: dict[str, ttk.Radiobutton] = {}
        for key, label in (
            ("all", "все"),
            (core.RENAME, STRINGS["col_new"]),
            (core.UNCHANGED, "без изменений"),
            (core.BAD_DATE, "дата недостоверна"),
            (core.ERROR, "ошибки"),
        ):
            button = ttk.Radiobutton(
                chips, text=label, value=key, variable=self.chip_var, style="Card.TRadiobutton",
                command=self._fill_table,
            )
            button.pack(side="left", padx=(0, 14))
            self.chips[key] = button

        table_card = self._card(frame)
        table_card.pack(fill="both", expand=True)
        columns = ("old", "new", "folder", "source", "status")
        self.tree = ttk.Treeview(table_card, columns=columns, show="headings", selectmode="browse")
        widths = {"old": 250, "new": 260, "folder": 100, "source": 130, "status": 130}
        titles = {
            "old": STRINGS["col_old"], "new": STRINGS["col_new"], "folder": STRINGS["col_folder"],
            "source": STRINGS["col_source"], "status": STRINGS["col_status"],
        }
        for column in columns:
            self.tree.heading(column, text=titles[column])
            self.tree.column(column, width=widths[column], anchor="w", stretch=column in ("old", "new"))
        scroll = ttk.Scrollbar(table_card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)
        scroll.pack(side="right", fill="y", pady=1, padx=(0, 1))
        self.tree.tag_configure(core.ERROR, foreground="#B3261E")
        self.tree.tag_configure(core.BAD_DATE, foreground="#8A6D3B")
        self.tree.tag_configure(core.UNCHANGED, foreground=MUTED)

        bottom = tk.Frame(frame, bg=BG)
        bottom.pack(fill="x", pady=(10, 0))
        self.summary_label = tk.Label(bottom, text="", bg=BG, fg=INK, font=("Segoe UI", 10))
        self.summary_label.pack(side="left")
        ttk.Button(bottom, text=STRINGS["btn_back"], command=self._back_home).pack(side="right")
        self.run_button = ttk.Button(
            bottom, text=STRINGS["btn_go"], style="Accent.TButton", command=self._run_operation
        )
        self.run_button.pack(side="right", padx=10)

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
        batch = 300

        def feed(start: int) -> None:
            if self.current != "preview":
                return
            for index in range(start, min(start + batch, len(items))):
                item = items[index]
                self.tree.insert(
                    "", "end",
                    values=(
                        item.name,
                        item.dst_name or "—",
                        item.year_folder or "—",
                        item.source,
                        core.STATUS_LABELS.get(item.status, item.status) + (f" · {item.note}" if item.note else ""),
                    ),
                    tags=(item.status,),
                )
            if start + batch < len(items):
                self.after(10, lambda: feed(start + batch))

        feed(0)

    def _show_plan(self, result: core.PlanResult) -> None:
        self.plan_result = result
        counts = result.counts()
        total = len(result.items)
        self.preview_path.config(text=str(self.folder or ""))
        for key, button in self.chips.items():
            label = {
                "all": f"все · {total}",
                core.RENAME: f"к переименованию · {counts.get(core.RENAME, 0)}",
                core.UNCHANGED: f"без изменений · {counts.get(core.UNCHANGED, 0)}",
                core.BAD_DATE: f"дата недостоверна · {counts.get(core.BAD_DATE, 0)}",
                core.ERROR: f"ошибки · {counts.get(core.ERROR, 0)}",
            }[key]
            button.config(text=label)

        parts = [f"{counts.get(core.RENAME, 0)} {plural(counts.get(core.RENAME, 0), 'файл', 'файла', 'файлов')} к переименованию"]
        if counts.get(core.UNCHANGED):
            parts.append(f"{counts[core.UNCHANGED]} уже на месте")
        if counts.get(core.BAD_DATE):
            parts.append(f"{counts[core.BAD_DATE]} с недостоверной датой")
        if counts.get(core.ERROR):
            parts.append(f"{counts[core.ERROR]} с ошибками")
        self.summary_label.config(text=" · ".join(parts) if total else STRINGS["nothing"])

        self.chip_var.set("all")
        self.show("preview")
        self._fill_table()
        self.run_button.state(["!disabled"] if counts.get(core.RENAME) else ["disabled"])

    # --- выполнение ----------------------------------------------------------

    def _build_progress(self) -> None:
        frame = tk.Frame(self.container, bg=BG)
        self.screens["progress"] = frame
        card = self._card(frame)
        card.pack(fill="x", pady=(40, 0))
        self.progress_label = tk.Label(card, text=STRINGS["working"], bg=CARD, fg=BLUE, font=("Segoe UI", 12))
        self.progress_label.pack(anchor="w", padx=24, pady=(24, 10))
        self.progress_bar = ttk.Progressbar(card, mode="determinate", maximum=100)
        self.progress_bar.pack(fill="x", padx=24)
        self.progress_detail = tk.Label(card, text="", bg=CARD, fg=MUTED, font=("Segoe UI", 9))
        self.progress_detail.pack(anchor="w", padx=24, pady=(8, 6))
        ttk.Button(card, text=STRINGS["btn_cancel"], command=self._cancel_current).pack(
            side="right", padx=24, pady=(0, 22)
        )

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
            target=self._run_worker, args=(self.folder, self.plan_result, dict(self.cfg)), daemon=True
        ).start()

    def _run_worker(self, folder: Path, result: core.PlanResult, cfg: dict) -> None:
        try:
            stats = core.execute(
                folder, result, cfg, cfgmod.journal_dir(),
                progress=lambda done, total, _item: self._on_progress(done, total),
                cancel=self._cancel,
            )
            self._queue.put(("done", stats))
        except Exception as exc:  # noqa: BLE001
            self._queue.put(("fatal", core.human_error(exc)))

    # --- результат -----------------------------------------------------------

    def _build_result(self) -> None:
        frame = tk.Frame(self.container, bg=BG)
        self.screens["result"] = frame

        card = self._card(frame)
        card.pack(fill="both", expand=True)
        self.mascot = Mascot(card, width=280, height=250, bg=CARD)
        self.mascot.pack(pady=(22, 6))

        self.result_title = tk.Label(card, text=STRINGS["result_title"], bg=CARD, fg=BLUE, font=("Segoe UI Semibold", 17))
        self.result_title.pack()
        self.result_summary = tk.Label(card, text="", bg=CARD, fg=INK, font=("Segoe UI", 10), justify="center")
        self.result_summary.pack(pady=(6, 16))

        buttons = tk.Frame(card, bg=CARD)
        buttons.pack(pady=(0, 8))
        self.thanks_button = ttk.Button(buttons, text=STRINGS["thanks"], style="Accent.TButton", command=self._on_thanks)
        self.thanks_button.pack(side="left", padx=6)
        self.retry_button = ttk.Button(buttons, text=STRINGS["retry"], command=self._on_retry)
        self.undo_button = ttk.Button(buttons, text=STRINGS["undo"], command=self._on_undo)
        self.journal_button = ttk.Button(buttons, text=STRINGS["open_journal"], command=lambda: self.show("journal"))

        links = tk.Frame(card, bg=CARD)
        links.pack(pady=(0, 18))
        ttk.Button(links, text=STRINGS["again"], style="Link.TButton", command=self._back_home).pack(side="left", padx=8)

    def _show_result(self, stats: core.RunStats) -> None:
        self.run_stats = stats
        self.last_failed = list(stats.failed)
        counts = self.plan_result.counts() if self.plan_result else {}
        improved = stats.renamed
        self.result_title.config(text="Готово" if improved else "Нечего было менять")

        lines = [
            f"Обработано {stats.processed} {plural(stats.processed, 'файл', 'файла', 'файлов')}",
            f"разложено: {improved} · без изменений: {counts.get(core.UNCHANGED, 0)} "
            f"· пропущено: {counts.get(core.BAD_DATE, 0)} · ошибок: {stats.errors}",
            f"Время: {fmt_duration(stats.elapsed)}",
        ]
        if stats.cancelled:
            lines.append("Остановлено по вашей команде — сделанное можно отменить.")
        self.result_summary.config(text="\n".join(lines))

        for button in (self.retry_button, self.undo_button, self.journal_button):
            button.pack_forget()
        if self.last_failed:
            self.retry_button.pack(side="left", padx=6)
        if improved and stats.journal_path:
            self.undo_button.pack(side="left", padx=6)
        self.journal_button.pack(side="left", padx=6)

        self.show("result")
        self._play_animation()

    def _play_animation(self) -> None:
        self._stop_animation()
        assets = cfgmod.assets_dir()
        if reduce_motion():
            self.mascot.show_image(load_photo(assets / "mascot_work.png", (280, 250)))
            return

        if not self._anim_frames:
            folder = assets / "anim_done"
            files = sorted(folder.glob("*.png"))[:120] if folder.is_dir() else []
            for path in files:
                photo = load_photo(path, (280, 250))
                if photo is not None:
                    self._anim_frames.append(photo)

        if not self._anim_frames:
            self.mascot.show_image(load_photo(assets / "mascot_idle.png", (280, 250)))
            return

        delay = max(int(1000 / 24), 20)
        state = {"index": 0}

        def step() -> None:
            if self.current != "result":
                return
            self.mascot.show_image(self._anim_frames[state["index"] % len(self._anim_frames)])
            state["index"] += 1
            if state["index"] < len(self._anim_frames):
                self._anim_after = self.after(delay, step)
            else:
                self._anim_after = None

        step()

    def _stop_animation(self) -> None:
        if self._anim_after is not None:
            try:
                self.after_cancel(self._anim_after)
            except tk.TclError:
                pass
            self._anim_after = None
        if self._thanks_after is not None:
            try:
                self.after_cancel(self._thanks_after)
            except tk.TclError:
                pass
            self._thanks_after = None

    def _on_thanks(self) -> None:
        assets = cfgmod.assets_dir()
        idle = load_photo(assets / "mascot_work.png", (280, 250)) or load_photo(
            assets / "mascot_idle.png", (280, 250)
        )
        if idle is not None and not reduce_motion():
            self.mascot.show_image(idle)
            self._thanks_after = self.after(1000, self._back_home)
        else:
            self._back_home()

    def _on_retry(self) -> None:
        if not self.last_failed:
            return
        stats = core.retry_failed(self.last_failed, self.cfg, cfgmod.journal_dir())
        if stats.errors == 0:
            messagebox.showinfo(APP_TITLE, f"Получилось: {stats.renamed}.")
        else:
            messagebox.showwarning(APP_TITLE, f"Всё ещё занято или недоступно: {stats.errors}.")
        if self.run_stats is not None:
            self.run_stats.renamed += stats.renamed
            self.run_stats.errors = stats.errors
        self.last_failed = list(stats.failed)
        if self.last_failed:
            self.retry_button.pack(side="left", padx=6)
        else:
            self.retry_button.pack_forget()
        if self.run_stats is not None:
            self.result_summary.config(
                text=f"Разложено: {self.run_stats.renamed} · ошибок: {self.run_stats.errors}"
            )

    def _on_undo(self) -> None:
        if not self.run_stats or not self.run_stats.journal_path:
            return
        if not messagebox.askyesno(APP_TITLE, "Вернуть файлы на прежние места?"):
            return
        try:
            stats = core.rollback(self.run_stats.journal_path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"Не удалось отменить изменения: {core.human_error(exc)}")
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
        frame = tk.Frame(self.container, bg=BG)
        self.screens["journal"] = frame
        top = tk.Frame(frame, bg=BG)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Журнал операций", style="Head.TLabel").pack(side="left")
        ttk.Button(top, text="Открыть папку журнала", command=self._open_journal_folder).pack(side="right")
        ttk.Button(top, text=STRINGS["btn_back"], command=self._back_home).pack(side="right", padx=8)

        card = self._card(frame)
        card.pack(fill="both", expand=True)
        self.journal_tree = ttk.Treeview(card, columns=("file", "moved", "errors"), show="headings")
        for column, title, width in (
            ("file", "Сессия", 320), ("moved", "Операций", 110), ("errors", "Ошибок", 110)
        ):
            self.journal_tree.heading(column, text=title)
            self.journal_tree.column(column, width=width, anchor="w")
        self.journal_tree.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        scroll = ttk.Scrollbar(card, orient="vertical", command=self.journal_tree.yview)
        self.journal_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y", pady=1)

        bottom = tk.Frame(frame, bg=BG)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text="Отменить выбранную сессию", command=self._undo_selected).pack(side="left")
        self._refresh_journals()

    def _refresh_journals(self) -> None:
        self.journal_tree.delete(*self.journal_tree.get_children())
        for path in core.list_journals(cfgmod.journal_dir()):
            summary = core.journal_summary(path)
            self.journal_tree.insert(
                "", "end",
                iid=str(path),
                values=(path.stem.replace("_", " "), summary["moved"], summary["errors"]),
            )

    def _undo_selected(self) -> None:
        selection = self.journal_tree.selection()
        if not selection:
            messagebox.showinfo(APP_TITLE, "Выберите сессию в списке.")
            return
        path = Path(selection[0])
        if not messagebox.askyesno(APP_TITLE, f"Вернуть файлы сессии «{path.stem}» на прежние места?"):
            return
        stats = core.rollback(path)
        messagebox.showinfo(
            APP_TITLE,
            f"Возвращено: {stats.restored}\nПропущено: {stats.skipped}\nОшибок: {stats.errors}",
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
        except Exception as exc:  # noqa: BLE001
            messagebox.showinfo(APP_TITLE, f"Папка журнала: {folder}")

    # --- настройки -----------------------------------------------------------

    def _build_settings(self) -> None:
        frame = tk.Frame(self.container, bg=BG)
        self.screens["settings"] = frame
        top = tk.Frame(frame, bg=BG)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Настройки", style="Head.TLabel").pack(side="left")
        ttk.Button(top, text=STRINGS["btn_back"], command=self._back_home).pack(side="right")

        card = self._card(frame)
        card.pack(fill="both", expand=True)

        body = tk.Frame(card, bg=CARD)
        body.pack(fill="both", expand=True, padx=22, pady=18)

        tk.Label(body, text=STRINGS["template"], bg=CARD, fg=MUTED, font=("Segoe UI", 9)).grid(
            row=0, column=0, sticky="w"
        )
        self.s_template = tk.StringVar(value=self.cfg.get("template", ""))
        ttk.Entry(body, textvariable=self.s_template, width=40).grid(row=1, column=0, sticky="w", pady=(0, 4))
        self.s_template_err = tk.Label(body, text="", bg=CARD, fg="#B3261E", font=("Segoe UI", 8))
        self.s_template_err.grid(row=2, column=0, sticky="w")
        self.s_template.trace_add("write", lambda *_: self._validate_settings())

        tk.Label(body, text=STRINGS["year_folder"], bg=CARD, fg=MUTED, font=("Segoe UI", 9)).grid(
            row=3, column=0, sticky="w", pady=(12, 0)
        )
        self.s_year = tk.StringVar(value=self.cfg.get("year_folder", "{YYYY}"))
        ttk.Entry(body, textvariable=self.s_year, width=20).grid(row=4, column=0, sticky="w", pady=(0, 4))
        self.s_year_err = tk.Label(body, text="", bg=CARD, fg="#B3261E", font=("Segoe UI", 8))
        self.s_year_err.grid(row=5, column=0, sticky="w")
        self.s_year.trace_add("write", lambda *_: self._validate_settings())

        tk.Label(body, text="Режим работы", bg=CARD, fg=MUTED, font=("Segoe UI", 9)).grid(
            row=6, column=0, sticky="w", pady=(12, 0)
        )
        self.s_mode = tk.StringVar(value=self.cfg.get("mode", "inplace"))
        modes_row = tk.Frame(body, bg=CARD)
        modes_row.grid(row=7, column=0, sticky="w")
        for value, label in MODES:
            ttk.Radiobutton(
                modes_row, text=label, value=value, variable=self.s_mode, style="Card.TRadiobutton"
            ).pack(side="left", padx=(0, 14))

        self.s_recursive = tk.BooleanVar(value=bool(self.cfg.get("recursive")))
        ttk.Checkbutton(body, text=STRINGS["recursive"], variable=self.s_recursive, style="TCheckbutton").grid(
            row=8, column=0, sticky="w", pady=(12, 0)
        )
        self.s_fname = tk.BooleanVar(value=bool(self.cfg.get("filename_date_fallback")))
        ttk.Checkbutton(
            body, text=STRINGS["filename_date"], variable=self.s_fname, style="TCheckbutton"
        ).grid(row=9, column=0, sticky="w", pady=(4, 0))
        self.s_skip = tk.BooleanVar(value=bool(self.cfg.get("skip_system_files")))
        ttk.Checkbutton(
            body, text="Пропускать системные и скрытые файлы",
            variable=self.s_skip, style="TCheckbutton",
        ).grid(row=10, column=0, sticky="w", pady=(4, 0))

        info = tk.Label(
            body,
            text=(
                f"Версия: {version.APP_VERSION}\n"
                f"Настройки хранятся в {cfgmod.settings_path()}\n"
                "Сетевых запросов нет, данные никуда не отправляются."
            ),
            bg=CARD, fg=MUTED, font=("Segoe UI", 8), justify="left",
        )
        info.grid(row=11, column=0, sticky="w", pady=(18, 0))

        actions = tk.Frame(card, bg=CARD)
        actions.pack(fill="x", padx=22, pady=(0, 18))
        ttk.Button(actions, text="Сбросить к умолчаниям", command=self._reset_settings).pack(side="left")
        self.s_save = ttk.Button(actions, text="Сохранить", style="Big.TButton", command=self._save_settings)
        self.s_save.pack(side="right")
        self._validate_settings()

    def _validate_settings(self) -> None:
        template_error = core.validate_template(self.s_template.get())
        year_error = core.validate_year_folder(self.s_year.get())
        self.s_template_err.config(text=template_error or "")
        self.s_year_err.config(text=year_error or "")
        self.s_save.state(["disabled"] if (template_error or year_error) else ["!disabled"])

    def _save_settings(self) -> None:
        self.cfg.update(
            template=self.s_template.get(),
            year_folder=self.s_year.get(),
            mode=self.s_mode.get(),
            recursive=bool(self.s_recursive.get()),
            filename_date_fallback=bool(self.s_fname.get()),
            skip_system_files=bool(self.s_skip.get()),
        )
        cfgmod.save_settings(self.cfg)
        self.template_var.set(self.cfg["template"])
        self.year_var.set(self.cfg["year_folder"])
        self.mode_var.set(self.cfg["mode"])
        self.recursive_var.set(self.cfg["recursive"])
        self.fname_date_var.set(self.cfg["filename_date_fallback"])
        self._play_saved_feedback()

    def _play_saved_feedback(self) -> None:
        """Кнопка «Сохранить» меняется на галочку, затем экран возвращается на главный."""
        button = self.s_save
        button.state(["disabled"])
        frames = ["✓", "✓  Сохранено"]
        button.configure(text=frames[0])
        steps = [(0, "✓"), (180, "✓  Сохранено")]

        def set_text(index: int) -> None:
            if self.current != "settings":
                return
            button.configure(text=steps[index][1])

        for index, (delay, _) in enumerate(steps):
            self.after(delay, lambda i=index: set_text(i))
        self.after(1100, self._finish_saved_feedback)

    def _finish_saved_feedback(self) -> None:
        self.s_save.configure(text=STRINGS_SAVE_LABEL)
        self.s_save.state(["!disabled"])
        self._back_home()

    def _reset_settings(self) -> None:
        defaults = cfgmod.DEFAULTS
        self.s_template.set(defaults["template"])
        self.s_year.set(defaults["year_folder"])
        self.s_mode.set(defaults["mode"])
        self.s_recursive.set(defaults["recursive"])
        self.s_fname.set(defaults["filename_date_fallback"])
        self.s_skip.set(defaults["skip_system_files"])

    # --- цикл событий --------------------------------------------------------

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
        elif self.current in ("preview", "journal", "settings"):
            self._back_home()
        elif self.current == "result":
            self._back_home()

    def _on_enter(self) -> None:
        if self.current == "preview":
            if "disabled" not in self.run_button.state():
                self._run_operation()
        elif self.current == "result":
            self._on_thanks()

    def _on_close(self) -> None:
        try:
            self.cfg["geometry"] = self.winfo_geometry()
            cfgmod.save_settings(self.cfg)
        except Exception:  # noqa: BLE001
            pass
        self.destroy()


def run() -> None:
    cfgmod.setup_logging()
    app = App()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        app.destroy()
